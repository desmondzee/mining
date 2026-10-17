"""Brain-bridge tests. Plain python, no Isaac, no Warp:   python3 isaac/test_bridge.py   (or pytest isaac/test_bridge.py)

Unit: framing, spec field compliance (parsed from spec.md), byte parity with brain/brain/protocol.py, bicycle model vs
TruckAgent.cs, lidar beam table vs the spec formula and LidarSensor.cs, depth/lidar semantics, KeepInBounds, config
parity with BrainClient.cs, BrainLink against a socket, RL env shapes.
Integration (skip with SKIP_INTEGRATION=1): the real brain from a temporary `git worktree` of origin/main (`uv run brain`
on spare ports), a BrainLink driving the fake core, a goal posted over HTTP, actions back, truck moves. Worktree removed.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "brain"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from isaac import brain_bridge as B  # noqa: E402
from isaac import truck as T  # noqa: E402
from isaac.sensors_bridge import FakeCore, SensorSuite, lidar_angles, make_core  # noqa: E402
from isaac.sim import kinematics as K
from isaac.sim import world as W  # noqa: E402
from isaac.sim.sensors import depth_directions, lidar_directions  # noqa: E402

QUARRY = ROOT / "data/worlds/quarry"


def git_show(path: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "show", f"origin/main:{path}"], capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        local = ROOT / path
        return local.read_text() if local.exists() else None


def cs_field(src: str, name: str) -> float:
    m = re.search(rf"\b{name}\s*=\s*(-?[\d.]+)f?", src)
    return float(m.group(1))


def spec_messages(spec: str) -> dict[str, dict]:
    """spec.md 'Sim protocol' message table -> {type: {field: [subfields]}}."""
    out = {}
    for line in spec.splitlines():
        m = re.match(r"\| `(\w+)` \| (sim|brain) → (sim|brain) \| (.*?) \| (.*?) \|$", line)
        if not m:
            continue
        fields = {}
        for tok in re.findall(r"`([a-z_]+(?:[\[{][^`]*)?)`", m.group(4)):
            name = re.match(r"[a-z_]+", tok).group(0)
            subs = re.match(r"[a-z_]+\[?\{([a-z_,]+)", tok)
            fields[name] = subs.group(1).split(",") if subs else []
        out[m.group(1)] = fields
    return out


# --------------------------------------------------------------------------------------------------------- protocol
class TestFraming(unittest.TestCase):
    def test_round_trip_and_chunking(self):
        msgs = [B.action_message("truck-0", 0.5, -0.25), B.lidar_message("truck-1", np.array([[1.0, 2.0, 3.0]]), {"x": 1, "z": 2, "yaw": 3})]
        stream = b"".join(msgs)
        r = B.MessageReader()
        got = []
        for i in range(0, len(stream), 7):
            got += r.feed(stream[i : i + 7])[0]
        self.assertEqual([h["type"] for h, _ in got], ["action", "lidar"])
        self.assertEqual(got[0][0], {"type": "action", "truck": "truck-0", "throttle": 0.5, "steer": -0.25})
        self.assertEqual(np.frombuffer(got[1][1], "<i2").tolist(), [100, 200, 300])
        hlen, plen = B.PREFIX.unpack(msgs[1][:8])
        self.assertEqual((hlen + plen + 8, plen), (len(msgs[1]), 6))

    def test_limits(self):
        with self.assertRaises(B.ProtocolError):
            B.MessageReader().feed(B.PREFIX.pack(B.MAX_HEADER + 1, 0))

    def test_byte_parity_with_brain_protocol(self):
        try:
            from brain import protocol as P
        except ImportError as e:
            self.skipTest(f"brain package not importable: {e}")
        hdr = {"type": "hello", "trucks": ["a"], "bounds": [1, 2, 3, 4]}
        self.assertEqual(B.encode(hdr, b"xy"), P.encode(hdr, b"xy"))
        pts = np.random.default_rng(0).uniform(-50, 50, (500, 3)).astype(np.float32)
        self.assertEqual(B.lidar_message("t", pts, {"x": 0, "z": 0, "yaw": 0}), P.encode_lidar("t", pts, {"x": 0.0, "z": 0.0, "yaw": 0.0}))
        rgb = np.random.default_rng(1).integers(0, 255, (360, 640, 3), np.uint8)
        depth = np.random.default_rng(2).uniform(0, 100, (36, 64)).astype(np.float32)
        for enc in ("jpeg", "raw"):
            h, p = B.decode(B.frame_message("truck-0", rgb, depth, truck="truck-0", pose={"x": 1, "z": 2, "yaw": 3}, speed=4, encoding=enc))
            out_rgb, out_depth, jpeg = P.decode_frame(h, p)
            self.assertEqual(out_rgb.shape, (360, 640, 3))
            np.testing.assert_array_equal(out_depth, depth)
            if enc == "raw":
                np.testing.assert_array_equal(out_rgb, rgb)  # flip=true bottom-up round trip
            else:
                self.assertIsNotNone(jpeg)
        h, p = B.decode(B.lidar_message("t", pts, {"x": 0, "z": 0, "yaw": 0}))
        np.testing.assert_allclose(P.decode_lidar(h, p), pts, atol=0.006)


class TestSpecCompliance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = git_show("spec.md")
        if spec is None:
            raise unittest.SkipTest("spec.md not found")
        cls.spec = spec_messages(spec)

    def test_every_spec_message_and_field_is_covered(self):
        self.assertEqual(set(self.spec), set(B.SPEC), "message types differ from spec.md")
        for kind, fields in self.spec.items():
            ours = B.SPEC[kind]
            self.assertEqual(set(fields) - {"cam", "overview", "hello"}, set(ours["required"]) | set(ours["optional"]) - {"cam"}, kind)
            for name, subs in fields.items():
                if subs and name in ours.get("items", {}):
                    self.assertEqual(tuple(subs), ours["items"][name], f"{kind}.{name}")
                elif subs and name in ours:
                    self.assertEqual(tuple(subs), ours[name], f"{kind}.{name}")

    def test_directions(self):
        for kind in self.spec:
            self.assertEqual(B.SPEC[kind]["direction"], "brain->sim" if kind in ("action", "view") else "sim->brain")

    def test_messages_from_fake_core_validate(self):
        core, _, _ = make_core(QUARRY)
        core.sync_obstacles()
        s = SensorSuite(core)
        msgs = [B.hello_message(core.hello()), B.actors_message(core.actors()), *s.lidar_messages(), *s.frame_messages(),
                *s.frame_messages(encoding="raw"), B.action_message("truck-0", 1, 0), B.view_message("overview", None)]
        kinds = set()
        for m in msgs:
            h, p = B.decode(m)
            self.assertEqual(B.validate(h, p), [], h["type"])
            kinds.add(h["type"])
        self.assertEqual(kinds, set(B.SPEC))
        h, _ = B.decode(msgs[0])
        self.assertEqual(h["truck_model"], "komatsu_hd465")
        self.assertEqual(h["cameras"], ["overview", "truck-0", "truck-1", "truck-2"])
        self.assertEqual((len(h["roads"]), len(h["paths"]), len(h["obstacles"])), (len(core.world.roads), len(core.areas), len(core.excavators) + len(W.prop_obstacles(core.world))))
        self.assertTrue(all(o["r"] >= 5 for o in h["obstacles"][: len(core.excavators)]))
        frames = [B.decode(m)[0] for m in s.frame_messages()]
        self.assertEqual(frames[0]["truck"], "")
        self.assertEqual(frames[0]["depth"], {"w": 0, "h": 0})
        self.assertEqual(frames[1]["depth"], {"w": 64, "h": 36})
        self.assertEqual((frames[1]["rgb"]["w"], frames[1]["rgb"]["h"]), (640, 360))

    def test_validate_catches_mistakes(self):
        self.assertTrue(B.validate({"type": "lidar", "truck": "a", "points": 2, "format": "int16cm", "pose": {"x": 0, "z": 0, "yaw": 0}}, b"\0" * 6))
        self.assertTrue(B.validate({"type": "action", "truck": "a", "throttle": 1}))
        self.assertTrue(B.validate({"type": "nope"}))


# ----------------------------------------------------------------------------------------------------------- truck
class TestBicycle(unittest.TestCase):
    def test_constants_match_truck_agent(self):
        src = git_show("unity/Assets/Scripts/TruckAgent.cs")
        if src is None:
            self.skipTest("TruckAgent.cs not found")
        for name, ours in (("maxSpeed", K.MAX_SPEED), ("maxSteerDeg", K.MAX_STEER_DEG), ("wheelbase", K.WHEELBASE),
                           ("accel", K.ACCEL), ("commandTimeout", K.COMMAND_TIMEOUT)):
            self.assertEqual(cs_field(src, name), ours, name)

    def test_controller_matches_reference_step(self):
        c = T.TruckController("a", 3.0, -4.0, 30.0)
        x, z, yaw, v = 3.0, -4.0, 30.0, 0.0
        rng = np.random.default_rng(0)
        now, dt = 0.0, 0.02  # Unity fixedDeltaTime
        for _ in range(2000):
            th, st = float(rng.uniform(0, 1)), float(rng.uniform(-1, 1))
            c.command(th, st, now)
            c.step(dt, now)
            x, z, yaw, v = T.bicycle_step(x, z, yaw, v, th, st, dt)
            now += dt
        self.assertAlmostEqual(c.x, x, places=9)
        self.assertAlmostEqual(c.z, z, places=9)
        self.assertAlmostEqual(c.yaw, yaw, places=9)
        self.assertAlmostEqual(c.speed, v, places=9)

    def test_straight_line(self):
        x, z, yaw, v = 0.0, 0.0, 0.0, 0.0
        for _ in range(500):
            x, z, yaw, v = T.bicycle_step(x, z, yaw, v, 1.0, 0.0, 0.02)
        self.assertEqual((x, yaw), (0.0, 0.0))
        # closed form of v' = (8 - v) * min(1, dt * 1.5) per step
        self.assertAlmostEqual(v, 8 * (1 - (1 - 0.03) ** 500), places=9)
        x, z, yaw, v = T.bicycle_step(0, 0, 90, 8, 1, 0, 1.0)  # yaw 90 = +x
        self.assertAlmostEqual(x, 8.0)
        self.assertAlmostEqual(z, 0.0)

    def test_constant_steer_circle(self):
        x, z, yaw, v = 0.0, 0.0, 0.0, 4.0
        xs, zs = [], []
        for _ in range(3000):
            x, z, yaw, v = T.bicycle_step(x, z, yaw, v, 0.5, 1.0, 0.01)
            xs.append(x)
            zs.append(z)
        xs, zs = np.array(xs[500:]), np.array(zs[500:])
        A = np.column_stack([xs, zs, np.ones_like(xs)])
        cx, cz, c = np.linalg.lstsq(A, -(xs**2 + zs**2), rcond=None)[0]
        radius = math.sqrt(cx**2 / 4 + cz**2 / 4 - c)
        self.assertAlmostEqual(radius, 5.0 / math.tan(math.radians(30)), delta=0.05)  # 8.66 m
        self.assertGreater(-cx / 2, 0)  # positive steer = right turn = centre on +x when heading +z

    def test_timeout_coasts_to_stop(self):
        c = T.TruckController("a", 0, 0, 0)
        c.command(1, 0, 0.0)
        now = 0.0
        while now < 0.45:
            c.step(0.02, now)
            now += 0.02
        self.assertGreater(c.speed, 0.1)
        for _ in range(500):
            c.step(0.02, now)
            now += 0.02
        self.assertLess(c.speed, 1e-3)


class TestKeepInBounds(unittest.TestCase):
    def make(self, wall_x=None, bounds=(-100, -100, 100, 100)):
        passable = np.ones((80, 80), bool)
        if wall_x is not None:
            passable[:, int((wall_x + 100) / 2.5) :] = False  # cells with x >= wall_x impassable
        nav = T.NavGrid.from_mask(passable, 2.5, -100.0, -100.0)
        return T.KeepInBounds(list(bounds), nav)

    def drive(self, c, throttle, steer, seconds, dt=1 / 60):
        now = 0.0
        while now < seconds:
            c.command(throttle, steer, now)
            c.step(dt, now)
            now += dt
            yield now

    def test_turn_into_wall_stops_or_slides(self):
        g = self.make(wall_x=20.0)
        c = T.TruckController("a", 0.0, 0.0, 0.0, guard=g)
        statuses = set()
        for _ in self.drive(c, 1.0, 0.6, 30.0):
            statuses.add(c.guard_status)
            self.assertEqual(g.blocked_corners(c.x, c.z, c.yaw), 0)
            self.assertTrue(np.all(g.footprint(c.x, c.z, c.yaw)[:, 0] < 20.0))
        self.assertTrue(statuses & {"slide", "blocked"})

    def test_straight_into_wall_stops(self):
        g = self.make(wall_x=20.0)
        c = T.TruckController("a", 0.0, 0.0, 90.0, guard=g)  # heading +x
        for _ in self.drive(c, 1.0, 0.0, 20.0):
            pass
        self.assertEqual(c.guard_status, "blocked")
        self.assertEqual(c.speed, 0.0)
        self.assertLess(c.x + 4.7, 20.0)

    def test_bounds_cannot_be_crossed(self):
        g = self.make(bounds=(-50, -50, 50, 50))
        for yaw in (0, 90, 180, 270, 45):
            c = T.TruckController("a", 0.0, 0.0, yaw, guard=g)
            for _ in self.drive(c, 1.0, 0.0, 30.0):
                self.assertTrue(np.all(np.abs(g.footprint(c.x, c.z, c.yaw)) <= 45.0 + 1e-9), yaw)
            self.assertEqual(c.speed, 0.0)

    def test_lateral_acceleration_cap(self):
        g = T.KeepInBounds([-1e4, -1e4, 1e4, 1e4])
        c = T.TruckController("a", 0.0, 0.0, 0.0, guard=g)
        for _ in self.drive(c, 1.0, 0.0, 15.0):
            pass
        self.assertGreater(c.speed, 7.5)
        prev_yaw, worst = c.yaw, 0.0
        for _ in self.drive(c, 1.0, 1.0, 20.0, dt=1 / 60):
            yaw_rate = math.radians(((c.yaw - prev_yaw + 180) % 360) - 180) * 60
            worst = max(worst, abs(c.speed * yaw_rate))  # a_lat = v * omega
            prev_yaw = c.yaw
        self.assertLessEqual(worst, T.LATERAL_ACCEL + 0.02)
        self.assertAlmostEqual(c.speed, T.curvature_speed_cap(1.0), delta=0.05)  # sqrt(1.5 * 8.66) = 3.6 m/s

    def test_quarry_spawns_are_clear_and_unity_mode_unguarded(self):
        g = T.KeepInBounds.for_world(QUARRY)
        core = FakeCore(QUARRY)
        for t in core.trucks:
            self.assertEqual(g.blocked_corners(t.x, t.z, t.yaw), 0, t.id)
        self.assertIsNone(T.TruckController.from_truck(core.trucks[0]).guard)


# ---------------------------------------------------------------------------------------------------------- sensors
class TestSensors(unittest.TestCase):
    def test_lidar_beam_table_matches_spec_and_unity(self):
        src = git_show("unity/Assets/Scripts/LidarSensor.cs")
        if src is not None:
            for name, val in (("horizontalFov", 120), ("verticalMin", -20), ("verticalMax", 10), ("columns", 240), ("channels", 30), ("range", 100), ("rate", 10)):
                self.assertEqual(cs_field(src, name), val, name)
        az, el = lidar_angles()
        j, i = np.arange(240), np.arange(30)
        np.testing.assert_allclose(az, -120 / 2 + (j + 0.5) * 120 / 240)
        np.testing.assert_allclose(el, -20 + (i + 0.5) * (10 - -20) / 30)
        self.assertAlmostEqual(az[0], -59.75)
        self.assertAlmostEqual(el[-1], 9.5)
        dirs = lidar_directions().reshape(30, 240, 3)  # channel-major like LidarSensor.directions[c * columns + u]

        def unity_dir(e, a):  # Quaternion.Euler(-el, az, 0) * Vector3.forward, Unity rotation order Z, X, Y
            e, a = math.radians(-e), math.radians(a)
            fwd = np.array([0.0, -math.sin(e), math.cos(e)])  # rotate about X by e (left-handed)
            return np.array([fwd[2] * math.sin(a), fwd[1], fwd[2] * math.cos(a)])  # then about Y by a

        for ci, ui in ((0, 0), (29, 239), (10, 120), (17, 3)):
            np.testing.assert_allclose(dirs[ci, ui], unity_dir(el[ci], az[ui]), atol=1e-12)

    def test_depth_rays_match_viewport_point_to_ray(self):
        d = depth_directions().reshape(36, 64, 3)
        self.assertGreater(d[0, 32, 1], 0)  # row 0 = top
        self.assertLess(d[18, 0, 0], 0)  # column 0 = left
        tan_h = d[18, 63, 0] / d[18, 63, 2]
        self.assertAlmostEqual(tan_h, (2 * 63.5 / 64 - 1) * math.tan(math.radians(45)))

    def test_depth_and_lidar_against_a_wall(self):
        core = FakeCore(None)
        t = core.trucks[1]
        cam_z = core.rig.camera[2]
        # wall: 40 m wide, 20 m tall box 30 m ahead of the truck origin
        core.sync_obstacles()
        c, r, h = core.caster.box_c, core.caster.box_r, core.caster.box_h
        core.caster.set_boxes(np.vstack([c, [t.x, 10, t.z + 30 + 0.5]]), np.vstack([r, np.eye(3)[None]]), np.vstack([h, [20, 10, 0.5]]))
        core.sync_obstacles = lambda: None
        depth = core.depths()[1]
        self.assertEqual(depth.shape, (36, 64))
        self.assertAlmostEqual(float(depth[18, 32]), 30 - cam_z, places=3)  # perpendicular metres
        self.assertAlmostEqual(float(depth[18, 12]), 30 - cam_z, places=3)   # not the slant range
        pts = core.lidars()[1]
        self.assertLess(len(pts), 7200)  # misses (sky) dropped
        level = pts[np.abs(pts[:, 1]) < 0.3]
        self.assertTrue(np.allclose(level[:, 2][np.abs(level[:, 0]) < 5], 30 - core.rig.lidar[2], atol=1e-3))  # sensor-local z forward
        right = pts[(pts[:, 0] > 3) & (np.abs(pts[:, 1]) < 0.3)]
        self.assertTrue(len(right) > 0)  # x right present


# ----------------------------------------------------------------------------------------------------- config/link
class TestConfigParity(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(B.brain_endpoint([], {}), ("127.0.0.1", 9000))
        self.assertEqual(B.brain_endpoint([], {"BRAIN_HOST": "h", "BRAIN_PORT": "9100"}), ("h", 9100))
        self.assertEqual(B.brain_endpoint(["-brain-host", "a", "-brain-port", "1"], {"BRAIN_HOST": "h", "BRAIN_PORT": "9"}), ("a", 1))
        self.assertEqual(B.brain_endpoint(["--brain", "b:2"], {}), ("b", 2))
        self.assertEqual(B.brain_endpoint(["--brain-port=3"], {"BRAIN_HOST": "h"}), ("h", 3))


class TestBrainLink(unittest.TestCase):
    def test_hello_first_then_actions(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        got = []

        def serve():
            conn, _ = srv.accept()
            got.append(B.recv_message(conn))
            conn.sendall(B.action_message("truck-0", 0.7, -0.2) + B.encode({"type": "other"}))
            got.append(B.recv_message(conn))
            time.sleep(0.3)
            conn.close()

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        link = B.BrainLink("127.0.0.1", port, lambda: {"trucks": ["truck-0"], "cameras": ["overview", "truck-0"]}).start()
        deadline = time.time() + 5
        while "truck-0" not in link.actions and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(link.latest_action("truck-0"), (0.7, -0.2))
        self.assertIsNone(link.latest_action("truck-0", max_age=-1))
        link.send(B.actors_message([]))
        th.join(5)
        link.stop()
        srv.close()
        self.assertEqual(got[0][0]["type"], "hello")
        self.assertEqual(B.validate(*got[0]), [])
        self.assertEqual(got[1][0]["type"], "actors")


class TestExternalAdapter(unittest.TestCase):
    """isaac/load_world.py path: its own trucks + read_sensors() dicts."""

    def test_publish_and_actions(self):
        class FakeLink:
            def __init__(self):
                self.connected = threading.Event()
                self.connected.set()
                self.sent, self.actions, self.clock = [], {"truck-0": (0.5, 0.1, 100.0)}, lambda: 100.2

            def send(self, m):
                self.sent.append(B.decode(m))

        class FakeTruck:
            id = "truck-0"

            def set_action(self, th, st, now):
                self.cmd = (th, st, now)

        link, truck = FakeLink(), FakeTruck()
        ad = B.ExternalTrucksBridge(link, [truck])
        ad.apply_actions(now=50.0)
        self.assertEqual(truck.cmd[:2], (0.5, 0.1))
        self.assertAlmostEqual(truck.cmd[2], 49.8)
        rgb = np.zeros((360, 640, 3), np.uint8)
        rgb[:, :10] = 255  # bright left edge in Isaac -> right edge after the Unity mirror
        depth = np.tile(np.arange(64, dtype=np.float32), (36, 1))
        data = {"truck-0": {"rgb": rgb, "depth": depth, "lidar": np.array([[1.0, 2.0, 3.0]]), "pose": {"x": 1, "z": 2, "yaw": 3}, "speed": 1.5}}
        ad.publish(0.0, lambda: data, overview=lambda: rgb, pedestrians=lambda: [{"id": "p", "x": 0, "z": 0, "yaw": 0}])
        kinds = [h["type"] for h, _ in link.sent]
        self.assertEqual(kinds, ["actors", "lidar", "frame", "frame"])
        for h, p in link.sent:
            self.assertEqual(B.validate(h, p), [])
        h, p = link.sent[1]
        self.assertEqual(np.frombuffer(p, "<i2").tolist(), [-100, 200, 300])
        h, p = link.sent[3]
        d = np.frombuffer(p[h["rgb"]["bytes"] :], "<f4").reshape(36, 64)
        self.assertEqual(d[0, 0], 63.0)
        hello = B.hello_from_world(QUARRY, ["truck-0", "truck-1"])
        self.assertEqual(B.validate(hello), [])
        self.assertEqual(hello["cameras"], ["overview", "truck-0", "truck-1"])


# ------------------------------------------------------------------------------------------------------------ RL env
class TestRLEnv(unittest.TestCase):
    def test_reset_step_shapes(self):
        from isaac.rl_env import IsaacQuarryEnv, MultiTruckQuarryEnv

        env = IsaacQuarryEnv(QUARRY)
        obs, info = env.reset(seed=0, truck_id="truck-1", goal=env.core.world.sites[0].id)
        shapes = {"rgb": (360, 640, 3), "depth": (36, 64), "lidar": (7200, 3), "pose": (3,), "speed": (1,), "goal": (2,)}
        for k, s in shapes.items():
            self.assertEqual(obs[k].shape, s, k)
        self.assertEqual(obs["rgb"].dtype, np.uint8)
        self.assertGreater(obs["lidar_count"], 0)
        self.assertGreater(len(info["route"]), 2)  # routed along roads
        x0 = obs["pose"].copy()
        for _ in range(15):
            obs, r, term, trunc, inf = env.step({"throttle": 1.0, "steer": 0.0})
        self.assertIsInstance(r, float)
        self.assertGreater(float(np.hypot(*(obs["pose"][:2] - x0[:2]))), 1.0)
        self.assertIn("progress_m", inf)
        self.assertIsNone(env.truck().guard)  # training default: Unity dynamics
        obs2, _ = env.reset(seed=0, truck_id="truck-1", goal=(0.0, 0.0))
        np.testing.assert_allclose(obs2["pose"], x0)
        np.testing.assert_allclose(obs2["goal"], [0, 0])

        m = MultiTruckQuarryEnv(QUARRY, keep_in_bounds=True, rgb=False)
        o, _ = m.reset(seed=1)
        o, r, te, tr, inf = m.step({k: [1.0, 0.2] for k in o})
        self.assertEqual(set(o), {"truck-0", "truck-1", "truck-2"})
        self.assertIsNotNone(m.truck("truck-0").guard)


# ------------------------------------------------------------------------------------------------------ integration
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http(method: str, url: str, body: dict | None = None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"null")


@unittest.skipIf(os.environ.get("SKIP_INTEGRATION"), "SKIP_INTEGRATION set")
@unittest.skipUnless(shutil.which("uv") and shutil.which("git"), "needs uv and git")
class TestIntegrationWithRealBrain(unittest.TestCase):
    def test_brain_drives_fake_core(self):
        tmp = Path(tempfile.mkdtemp(prefix="brain-wt-"))
        wt = tmp / "main"
        subprocess.run(["git", "-C", str(ROOT), "fetch", "-q"], check=False)
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", "-q", str(wt), "origin/main"], check=True)
        http_port, sim_port = free_port(), free_port()
        env = {**os.environ, "PORT": str(http_port), "SIM_PORT": str(sim_port), "HOST": "127.0.0.1",
               "WORLD_DIR": str(wt / "data/worlds/quarry"), "WEB_DIR": str(wt / "web")}
        log = open(tmp / "brain.log", "w")
        brain = subprocess.Popen(["uv", "run", "--quiet", "brain"], cwd=wt / "brain", env=env, stdout=log, stderr=subprocess.STDOUT)
        link = None
        stop = threading.Event()
        try:
            base = f"http://127.0.0.1:{http_port}"
            deadline = time.time() + 240
            while time.time() < deadline:
                try:
                    http("GET", base + "/api/state")
                    break
                except Exception:
                    if brain.poll() is not None:
                        self.fail("brain exited:\n" + (tmp / "brain.log").read_text()[-3000:])
                    time.sleep(0.5)
            else:
                self.fail("brain did not start")

            core, _, _ = make_core(wt / "data/worlds/quarry", keep_in_bounds=True)
            link = B.BrainLink("127.0.0.1", sim_port, core.hello).start()
            start = {t.id: (t.x, t.z) for t in core.trucks}

            loop = threading.Thread(target=B.run, args=(core, link), kwargs={"duration": 120.0, "stop": stop}, daemon=True)
            loop.start()
            deadline = time.time() + 20
            while not http("GET", base + "/api/state").get("cameras") and time.time() < deadline:
                time.sleep(0.2)
            state = http("GET", base + "/api/state")
            self.assertEqual(sorted(state["cameras"]), ["overview", "truck-0", "truck-1", "truck-2"])
            self.assertEqual(state["truck_model"], "komatsu_hd465")
            site = sorted(s["id"] for s in http("GET", base + "/api/world")["sites"])[0]
            http("POST", base + "/api/trucks/truck-0/goal", {"site": site})
            deadline = time.time() + 30
            moved = 0.0
            while time.time() < deadline:
                t0 = core.trucks[0]
                moved = math.hypot(t0.x - start["truck-0"][0], t0.z - start["truck-0"][1])
                if moved > 3.0:
                    break
                time.sleep(0.25)
            state = http("GET", base + "/api/state")
            tr = {t["id"]: t for t in state["trucks"]}
            lidar = urllib.request.urlopen(base + "/api/trucks/truck-0/lidar", timeout=5)
            n_points = json.loads(lidar.headers["X-Lidar"])["points"]
            print(f"\n[integration] goal {site}: truck-0 moved {moved:.1f} m, status {tr['truck-0']['status']}, "
                  f"route {len(tr['truck-0'].get('route') or [])} pts, link sent {link.sent} recv {link.received} actions, "
                  f"lidar {n_points} pts, pedestrians {len(state['pedestrians'])}", flush=True)
            self.assertGreater(link.received, 10)
            self.assertGreater(moved, 3.0)
            self.assertTrue(tr["truck-0"].get("route"))
            self.assertGreater(n_points, 100)
            self.assertEqual(len(state["pedestrians"]), 12)
        finally:
            stop.set()
            if link:
                link.stop()
            brain.terminate()
            try:
                brain.wait(10)
            except subprocess.TimeoutExpired:
                brain.kill()
            log.close()
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], check=False)
            subprocess.run(["git", "-C", str(ROOT), "worktree", "prune"], check=False)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
