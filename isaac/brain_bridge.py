"""Brain TCP link for the Isaac sim, with BrainClient.cs parity (spec.md "Sim protocol").

Pure Python + numpy (+ Pillow for JPEG). Nothing here imports Isaac, so it is unit-testable on a Mac.

Wire format (little-endian): u32 header_len | u32 payload_len | header JSON (UTF-8) | payload.
    sim -> brain: hello, frame, lidar, actors          brain -> sim: action {truck, throttle, steer}

Config parity with BrainClient.Awake: `-brain-host` / `-brain-port` command-line args, then BRAIN_HOST / BRAIN_PORT env,
then 127.0.0.1:9000. `--brain host:port` (isaac/sim/main.py style) and `--brain-host/--brain-port` are accepted too.

Run without Isaac (fake world, flat ground, Unity rates) against a brain:
    python isaac/brain_bridge.py --world data/worlds/quarry -brain-port 9000
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREFIX = struct.Struct("<II")
MAX_HEADER = 64 * 1024            # brain/brain/protocol.py:MAX_HEADER
MAX_PAYLOAD = 64 * 1024 * 1024    # brain/brain/protocol.py:MAX_PAYLOAD
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 9000  # BrainClient.cs host / port
JPEG_QUALITY = 80                 # SensorCamera.cs:jpegQuality
FRAME_FPS = 15.0                  # SensorCamera.cs:fps (isaac/sim/main.py defaults to 30 via config.json)
LIDAR_HZ = 10.0                   # LidarSensor.cs:rate
ACTORS_HZ = 10.0                  # BrainClient.cs:actorsRate
RETRY_S = 2.0                     # BrainClient reconnect sleep
OUTBOX = 64                       # BrainClient BlockingCollection capacity (TryAdd drops when full)


# ------------------------------------------------------------------------------------------------------------ framing
class ProtocolError(Exception):
    pass


def encode(header: dict, payload: bytes = b"") -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    if len(h) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"message too large: header={len(h)} payload={len(payload)}")
    return PREFIX.pack(len(h), len(payload)) + h + payload


def decode(data: bytes) -> tuple[dict, bytes]:
    msgs, rest = MessageReader().feed(data)
    if len(msgs) != 1 or rest:
        raise ProtocolError(f"expected exactly one message, got {len(msgs)} (+{rest} bytes)")
    return msgs[0]


class MessageReader:
    """Incremental decoder: feed() any chunking of the byte stream, get complete (header, payload) messages back."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> tuple[list[tuple[dict, bytes]], int]:
        self.buf += data
        out = []
        while len(self.buf) >= PREFIX.size:
            hlen, plen = PREFIX.unpack_from(self.buf)
            if hlen > MAX_HEADER or plen > MAX_PAYLOAD:
                raise ProtocolError(f"message too large: header={hlen} payload={plen}")
            end = PREFIX.size + hlen + plen
            if len(self.buf) < end:
                break
            header = json.loads(bytes(self.buf[PREFIX.size : PREFIX.size + hlen]))
            out.append((header, bytes(self.buf[PREFIX.size + hlen : end])))
            del self.buf[:end]
        return out, len(self.buf)


def recv_message(sock) -> tuple[dict, bytes]:
    def exactly(n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed")
            buf += chunk
        return bytes(buf)

    hlen, plen = PREFIX.unpack(exactly(PREFIX.size))
    if hlen > MAX_HEADER or plen > MAX_PAYLOAD:
        raise ProtocolError(f"message too large: header={hlen} payload={plen}")
    header = json.loads(exactly(hlen))
    return header, exactly(plen) if plen else b""


# --------------------------------------------------------------------------------------------- spec compliance table
# spec.md "Sim protocol" (origin/main 00da484) + BrainProtocol.cs field sets. Asserted by test_bridge.py.
NUM, STR, BOOL, LIST, DICT = (int, float), str, bool, list, dict
SPEC = {
    "hello": {
        "direction": "sim->brain",
        "required": {"trucks": LIST, "cameras": LIST, "bounds": LIST, "truck_model": STR, "obstacles": LIST, "paths": LIST},
        "optional": {"roads": LIST, "sites": LIST, "junctions": LIST},
        "items": {
            "obstacles": ("x", "z", "r"),
            "paths": ("points",),
            "roads": ("id", "from", "to", "width_m", "one_way", "points"),
            "sites": ("id", "type", "name", "x", "z"),
            "junctions": ("id", "x", "z"),
        },
    },
    "frame": {
        "direction": "sim->brain",
        "required": {"camera": STR, "truck": STR, "rgb": DICT, "depth": DICT, "pose": DICT, "speed": NUM},
        "optional": {"cam": DICT},  # Isaac sim extension (spec "Isaac sim": camera world pose)
        "rgb": ("w", "h", "flip", "channels", "format", "bytes"),
        "pose": ("x", "z", "yaw"),
    },
    "lidar": {
        "direction": "sim->brain",
        "required": {"truck": STR, "points": int, "format": STR, "pose": DICT},
        "optional": {},
        "pose": ("x", "z", "yaw"),
    },
    "actors": {"direction": "sim->brain", "required": {"pedestrians": LIST}, "optional": {}, "items": {"pedestrians": ("id", "x", "z", "yaw")}},
    "action": {"direction": "brain->sim", "required": {"truck": STR, "throttle": NUM, "steer": NUM}, "optional": {}},
}


def validate(header: dict, payload: bytes = b"") -> list[str]:
    """Spec check of one message; returns a list of problems (empty = compliant)."""
    kind = header.get("type")
    spec = SPEC.get(kind)
    if spec is None:
        return [f"unknown type {kind!r}"]
    errs = []
    for name, typ in spec["required"].items():
        if name not in header:
            errs.append(f"{kind}: missing {name}")
        elif not isinstance(header[name], typ) or (typ is NUM and isinstance(header[name], bool)):
            errs.append(f"{kind}.{name}: {type(header[name]).__name__}")
    for name, keys in spec.get("items", {}).items():
        for i, item in enumerate(header.get(name, [])):
            missing = [k for k in keys if k not in item]
            if missing:
                errs.append(f"{kind}.{name}[{i}] missing {missing}")
    for sub in ("rgb", "pose"):
        if sub in spec and isinstance(header.get(sub), dict):
            missing = [k for k in spec[sub] if k not in header[sub]]
            if missing:
                errs.append(f"{kind}.{sub} missing {missing}")
    if kind == "hello" and len(header.get("bounds", [])) != 4:
        errs.append("hello.bounds must be [xmin, zmin, xmax, zmax]")
    if kind == "frame" and not errs:
        rgb, depth = header["rgb"], header["depth"]
        rgb_len = rgb["bytes"] if rgb["format"] == "jpeg" else rgb["w"] * rgb["h"] * rgb["channels"]
        depth_len = depth.get("w", 0) * depth.get("h", 0) * 4
        if len(payload) != rgb_len + depth_len:
            errs.append(f"frame payload {len(payload)} != rgb {rgb_len} + depth {depth_len}")
    if kind == "lidar" and not errs:
        per = {"int16cm": 6, "f32": 12}.get(header["format"])
        if per is None:
            errs.append(f"lidar.format {header['format']!r}")
        elif len(payload) != header["points"] * per:
            errs.append(f"lidar payload {len(payload)} != {header['points']} x {per}")
    if kind in ("hello", "actors", "action") and payload:
        errs.append(f"{kind} must have no payload")
    return errs


# ------------------------------------------------------------------------------------------------------- encoders
def _pose(pose: dict | None) -> dict:
    pose = pose or {}
    return {"x": float(pose.get("x", 0.0)), "z": float(pose.get("z", 0.0)), "yaw": float(pose.get("yaw", 0.0))}


def encode_jpeg(rgb: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def hello_message(hello: dict) -> bytes:
    """hello from isaac.sim.world.hello / the fake core; normalised so every required spec field is present."""
    h = {"type": "hello", "trucks": [], "cameras": [], "bounds": [-100.0, -100.0, 100.0, 100.0], "truck_model": "",
         "obstacles": [], "paths": [], **hello}
    h["type"] = "hello"
    return encode(h)


def frame_message(camera: str, rgb: np.ndarray | None = None, depth: np.ndarray | None = None, *, truck: str = "",
                  pose: dict | None = None, speed: float = 0.0, cam: dict | None = None, jpeg: bytes | None = None,
                  size: tuple[int, int] | None = None, encoding: str = "jpeg", quality: int = JPEG_QUALITY) -> bytes:
    """SensorCamera.cs header: camera, truck ("" for overview), rgb{w,h,flip,channels,format,bytes}, depth{w,h}
    (0x0 if none), pose{x,z,yaw}, speed; payload = RGB (JPEG or raw) then depth float32, row 0 = top.
    `rgb` is (H, W, 3|4) uint8, row 0 = top. encoding 'raw' sends RGBA bottom-up with flip=true like Unity's raw path."""
    if jpeg is None and rgb is None:
        raise ValueError("need rgb or jpeg")
    if jpeg is not None or encoding == "jpeg":
        data = jpeg if jpeg is not None else encode_jpeg(rgb, quality)
        w, h = size if size else (rgb.shape[1], rgb.shape[0])
        meta = {"w": int(w), "h": int(h), "flip": False, "channels": 4, "format": "jpeg", "bytes": len(data)}
    else:
        h, w = rgb.shape[:2]
        rgba = np.concatenate([rgb[..., :3], np.full((h, w, 1), 255, np.uint8)], axis=2) if rgb.shape[2] == 3 else rgb
        data = np.ascontiguousarray(rgba[::-1], dtype=np.uint8).tobytes()
        meta = {"w": w, "h": h, "flip": True, "channels": 4, "format": "raw", "bytes": 0}
    header = {"type": "frame", "camera": camera, "truck": truck, "rgb": meta, "depth": {"w": 0, "h": 0},
              "pose": _pose(pose), "speed": float(speed)}
    if cam is not None:
        header["cam"] = cam
    if depth is not None:
        dh, dw = depth.shape
        header["depth"] = {"w": int(dw), "h": int(dh)}
        data += np.ascontiguousarray(depth, dtype="<f4").tobytes()
    return encode(header, data)


def pack_points(points: np.ndarray) -> bytes:
    """LidarSensor.Centimetres: round(m * 100) clamped to int16, x/y/z interleaved, little-endian."""
    return np.clip(np.round(np.asarray(points, np.float64) * 100), -32768, 32767).astype("<i2").tobytes()


def lidar_message(truck: str, points: np.ndarray, pose: dict) -> bytes:
    points = np.asarray(points, np.float32).reshape(-1, 3)
    return encode({"type": "lidar", "truck": truck, "points": len(points), "format": "int16cm", "pose": _pose(pose)}, pack_points(points))


def actors_message(pedestrians: list[dict]) -> bytes:
    return encode({"type": "actors", "pedestrians": [{"id": str(p["id"]), "x": float(p["x"]), "z": float(p["z"]), "yaw": float(p["yaw"])} for p in pedestrians]})


def action_message(truck: str, throttle: float, steer: float) -> bytes:
    return encode({"type": "action", "truck": truck, "throttle": float(throttle), "steer": float(steer)})


# ------------------------------------------------------------------------------------------------------------ config
def brain_endpoint(argv: list[str] | None = None, env: dict | None = None) -> tuple[str, int]:
    """BrainClient.Awake order: -brain-host/-brain-port arg > BRAIN_HOST/BRAIN_PORT env > 127.0.0.1:9000."""
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env

    def arg(*names: str) -> str | None:
        for i, a in enumerate(argv):
            for n in names:
                if a == n and i + 1 < len(argv):
                    return argv[i + 1]
                if a.startswith(n + "="):
                    return a.split("=", 1)[1]
        return None

    host, port = env.get("BRAIN_HOST") or DEFAULT_HOST, int(env.get("BRAIN_PORT") or DEFAULT_PORT)
    combined = arg("--brain")
    if combined:
        h, _, p = combined.rpartition(":")
        host, port = (h or host), int(p)
    host = arg("-brain-host", "--brain-host") or host
    port = int(arg("-brain-port", "--brain-port") or port)
    return host, port


# --------------------------------------------------------------------------------------------------------------- link
class BrainLink:
    """BrainClient: background TCP thread, hello on every (re)connect, bounded outbox (drop when full or disconnected),
    2 s retry, receive thread storing the latest action per truck with its receive time (LatestAction(truck, maxAge))."""

    def __init__(self, host: str, port: int, hello: Callable[[], dict],
                 on_action: Callable[[str, float, float, float], None] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        import queue

        self.host, self.port, self.hello, self.on_action, self.clock = host, port, hello, on_action, clock
        self.outbox: "queue.Queue[bytes]" = queue.Queue(OUTBOX)
        self.actions: dict[str, tuple[float, float, float]] = {}
        self.connected = threading.Event()
        self.running = False
        self.sent = self.dropped = self.received = 0
        self.thread = threading.Thread(target=self._run, name="BrainLink", daemon=True)

    def start(self) -> "BrainLink":
        self.running = True
        self.thread.start()
        return self

    def stop(self, timeout: float = 1.0) -> None:
        self.running = False
        self.thread.join(timeout)

    def send(self, message: bytes) -> bool:
        if not self.connected.is_set():
            return False
        try:
            self.outbox.put_nowait(message)
            return True
        except Exception:
            self.dropped += 1
            return False

    def latest_action(self, truck: str, max_age: float = 0.5) -> tuple[float, float] | None:
        a = self.actions.get(truck)
        if a is None or self.clock() - a[2] > max_age:
            return None
        return a[0], a[1]

    def _run(self) -> None:
        import queue
        import socket

        while self.running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
            except OSError:
                time.sleep(RETRY_S)
                continue
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while not self.outbox.empty():
                self.outbox.get_nowait()
            try:
                sock.sendall(hello_message(self.hello()))
                self.connected.set()
                rx = threading.Thread(target=self._receive, args=(sock,), name="BrainLink.rx", daemon=True)
                rx.start()
                while self.running and rx.is_alive():
                    try:
                        msg = self.outbox.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    sock.sendall(msg)
                    self.sent += 1
            except OSError:
                pass
            finally:
                self.connected.clear()
                sock.close()
            if self.running:
                time.sleep(RETRY_S)

    def _receive(self, sock) -> None:
        try:
            while True:
                header, _ = recv_message(sock)
                if header.get("type") != "action":
                    continue
                truck = str(header.get("truck"))
                throttle, steer, now = float(header.get("throttle", 0.0)), float(header.get("steer", 0.0)), self.clock()
                self.actions[truck] = (throttle, steer, now)
                self.received += 1
                if self.on_action:
                    self.on_action(truck, throttle, steer, now)
        except (OSError, ValueError, ConnectionError, ProtocolError):
            pass


# ------------------------------------------------------------------------------------------------------------ runtime
class RateGate:
    """`if Time.time < next: return; next = Time.time + 1 / rate` (Unity sensors gate on sim time)."""

    def __init__(self, rate: float) -> None:
        self.period, self.next = 1.0 / rate, 0.0

    def due(self, t: float) -> bool:
        if t < self.next:
            return False
        self.next = t + self.period
        return True


class BridgeRuntime:
    """Emits Unity-rate sensor traffic for a core (isaac.sim.sim.Sim or sensors_bridge.FakeCore) over a BrainLink.

    core API used: trucks, hello(), actors(), depths(), lidars(), camera_pose(i), sync_obstacles(), time.
    render: callable -> {camera_name: (H, W, 3) uint8}; None = depth-shaded stand-in (fake core)."""

    def __init__(self, core, link: BrainLink, render: Callable[[], dict] | None = None, fps: float = FRAME_FPS,
                 lidar_hz: float = LIDAR_HZ, actors_hz: float = ACTORS_HZ, encoding: str = "jpeg", send_cam: bool = True) -> None:
        from isaac.sensors_bridge import SensorSuite

        self.core, self.link, self.render = core, link, render
        self.sensors = SensorSuite(core)
        self.frames, self.lidar, self.actors = RateGate(fps), RateGate(lidar_hz), RateGate(actors_hz)
        self.encoding, self.send_cam = encoding, send_cam
        self.counts = {"frame": 0, "lidar": 0, "actors": 0}

    def tick(self, t: float | None = None) -> None:
        """Call once per rendered frame after core.step(); t = sim time (defaults to core.time)."""
        t = self.core.time if t is None else t
        if not self.link.connected.is_set():
            return
        self.core.sync_obstacles()
        if self.actors.due(t):
            self.link.send(actors_message(self.core.actors()))
            self.counts["actors"] += 1
        if self.lidar.due(t):
            for msg in self.sensors.lidar_messages():
                self.link.send(msg)
                self.counts["lidar"] += 1
        if self.frames.due(t):
            images = self.render() if self.render else None
            for msg in self.sensors.frame_messages(images, encoding=self.encoding, send_cam=self.send_cam):
                self.link.send(msg)
                self.counts["frame"] += 1


def apply_link_actions(core, link: BrainLink, now: float) -> None:
    """Pull mode (BrainClient.LatestAction): copy the latest action per truck into the controllers' command slot,
    stamped with its receive time mapped onto the sim clock so the 0.5 s command timeout still applies."""
    wall = link.clock()
    for t in core.trucks:
        a = link.actions.get(t.id)
        if a is not None:
            t.command(a[0], a[1], now - (wall - a[2]))


def run(core, link: BrainLink, render: Callable[[], dict] | None = None, fps: float = FRAME_FPS, substep: float = 1 / 60,
        duration: float = 0.0, realtime: bool = True, on_frame: Callable[[float], None] | None = None,
        stop: threading.Event | None = None) -> BridgeRuntime:
    """Fixed-rate loop: physics substeps (default 1/60 s, spec "Physics substep") then sensors at Unity rates."""
    runtime = BridgeRuntime(core, link, render, fps=fps)
    period = 1.0 / fps
    steps = max(1, round(period / substep))
    next_tick = time.monotonic()
    while (duration <= 0 or core.time < duration) and not (stop and stop.is_set()):
        for _ in range(steps):
            apply_link_actions(core, link, core.time)
            core.step(period / steps, core.time)
        if on_frame:
            on_frame(core.time)
        runtime.tick()
        if realtime:
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
    return runtime


def attach_bridge(core, render: Callable[[], dict] | None = None, argv: list[str] | None = None, fps: float = FRAME_FPS) -> BridgeRuntime:
    """Entry point for an external Isaac loop (e.g. isaac/load_world.py --bridge): start the link and return a runtime.
    Per frame the caller does: apply_link_actions(core, rt.link, core.time); core.step(dt, core.time); rt.tick()."""
    host, port = brain_endpoint(argv)
    link = BrainLink(host, port, core.hello).start()
    return BridgeRuntime(core, link, render, fps=fps)


def hello_from_world(world_dir: str | Path, truck_ids: list[str], truck_model: str = "komatsu_hd465",
                     excavator_radius: float | None = None) -> dict:
    """hello without a Sim (for stages built by isaac/build_stage.py): world.json roads/sites/junctions, 4 crossing
    paths, one obstacle circle per excavator (swing envelope = half diagonal of a 10 m x 4.5 m footprint by default)."""
    from isaac.sim import world as W

    world = W.load_world(world_dir)
    r = excavator_radius if excavator_radius is not None else math.hypot(10.0, 4.5) / 2
    obstacles = [{"x": float(e["pos3"][0]), "z": float(e["pos3"][2]), "r": float(r)}
                 for e in world.equipment if "excavat" in str(e.get("type", "excavator")).lower()] + W.prop_obstacles(world)
    return W.hello(world, list(truck_ids), W.crossing_paths(world), obstacles, truck_model)


class ExternalTrucksBridge:
    """Adapter for a loop that owns its own trucks and sensors (isaac/load_world.py: TruckController.set_action(),
    read_sensors() -> {truck_id: {rgb, depth, lidar, pose, speed}}). Same rates and formats as BridgeRuntime.

    Handedness: build_stage.py / mine_frames.py use a PROPER rotation (Isaac = (x, -z, y)), so the Isaac scene is the
    true glTF geometry, which is the mirror image of what Unity rendered from the same contract numbers. For Unity
    parity the camera picture and depth columns must be flipped left-right, and the brain's sensor-local x (right) is
    Isaac vehicle +Y (left), not -Y as mine_frames.isaac_sensor_points_to_unity assumes. `flip_image` and
    `negate_lidar_x` (default on) apply exactly that to read_sensors() output; turn negate_lidar_x off once
    load_world.py converts lidar with x = +Y itself. (isaac/sim uses an improper axis swap and needs neither.)"""

    def __init__(self, link: BrainLink, trucks: list, fps: float = FRAME_FPS, flip_image: bool = True, negate_lidar_x: bool = True) -> None:
        self.link, self.trucks = link, trucks
        self.frames, self.lidar, self.actors = RateGate(fps), RateGate(LIDAR_HZ), RateGate(ACTORS_HZ)
        self.flip_image, self.negate_lidar_x = flip_image, negate_lidar_x

    def apply_actions(self, now: float) -> None:
        wall = self.link.clock()
        for t in self.trucks:
            a = self.link.actions.get(t.id)
            if a is not None:
                t.set_action(a[0], a[1], now - (wall - a[2]))

    def publish(self, t: float, read: Callable[[], dict], overview: Callable[[], np.ndarray | None] | None = None,
                pedestrians: Callable[[], list[dict]] | None = None) -> None:
        if not self.link.connected.is_set():
            return
        if pedestrians is not None and self.actors.due(t):
            self.link.send(actors_message(pedestrians()))
        want_lidar, want_frames = self.lidar.due(t), self.frames.due(t)
        if not (want_lidar or want_frames):
            return
        data = read()
        if want_lidar:
            for tid, d in data.items():
                pts = np.asarray(d["lidar"], np.float32).reshape(-1, 3).copy()
                if self.negate_lidar_x:
                    pts[:, 0] *= -1
                self.link.send(lidar_message(tid, pts, d["pose"]))
        if want_frames:
            over = overview() if overview else None
            if over is not None:
                self.link.send(frame_message("overview", over[:, ::-1] if self.flip_image else over))
            for tid, d in data.items():
                rgb, depth = d.get("rgb"), d.get("depth")
                if rgb is None:
                    continue
                if self.flip_image:
                    rgb = rgb[:, ::-1]
                    depth = None if depth is None else depth[:, ::-1]
                self.link.send(frame_message(tid, rgb, depth, truck=tid, pose=d["pose"], speed=d.get("speed", 0.0)))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--world", default=str(ROOT / "data/worlds/quarry"))
    p.add_argument("--backend", choices=["fake", "isaac"], default="fake")
    p.add_argument("--fps", type=float, default=FRAME_FPS)
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--no-keep-in-bounds", action="store_true")
    p.add_argument("--cache", default=str(ROOT / "downloads/isaac/cache"))
    args, _ = p.parse_known_args(argv)
    host, port = brain_endpoint(argv)

    from isaac.sensors_bridge import make_core

    core, render, close = make_core(args.world, backend=args.backend, keep_in_bounds=not args.no_keep_in_bounds, cache=args.cache)
    link = BrainLink(host, port, core.hello).start()
    print(f"bridge: {args.backend} core, trucks {[t.id for t in core.trucks]}, brain {host}:{port}, {args.fps} fps", flush=True)
    try:
        run(core, link, render, fps=args.fps, duration=args.duration)
    finally:
        link.stop()
        close()


if __name__ == "__main__":
    main()
