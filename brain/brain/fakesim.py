import argparse
import asyncio
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .protocol import encode, encode_frame, encode_lidar, read_message

FPS = 15
CAM_W, CAM_H = 640, 360
FOV_DEG = 90.0
CAM_HEIGHT = 3.6
MAX_RANGE = 100.0
OVERVIEW_PX = 480
MAX_SPEED = 8.0
MAX_STEER_DEG = 30.0
WHEELBASE = 5.0
TRUCK_RADIUS = 2.5
TRUCK_HEIGHT = 4.0
WALL_HEIGHT = 15.0
LIDAR_HZ = 10
LIDAR_RANGE = 100.0
LIDAR_HFOV = 120.0
LIDAR_VMIN = -20.0
LIDAR_VMAX = 10.0
LIDAR_COLS = 240
LIDAR_CHANNELS = 30
LIDAR_HEIGHT = 3.0
ACTORS_HZ = 10
PEDESTRIANS_PER_PATH = 3
MIN_WALK_SPEED = 1.2
MAX_WALK_SPEED = 1.6
PEDESTRIAN_RADIUS = 0.3
PEDESTRIAN_HEIGHT = 1.8
PEDESTRIAN_PATHS = [
    [(-30, -40), (30, -40)],
    [(-10, -10), (10, -10), (10, 10), (-10, 10)],
    [(-50, 20), (-10, 60)],
    [(40, -20), (40, 40)],
]
CROSSING_ROADS = 4
CROSSING_EXTRA = 12.0
EQUIPMENT_RADIUS = 5.0
EQUIPMENT_HEIGHT = 5.0
ROAD_COLOUR = (175, 160, 135)
ROCK, TRUCK, PERSON = 0, 1, 2
KIND_COLOURS = np.array([[120, 120, 125], [230, 180, 40], [255, 120, 20]], float)
WALL_COLOUR = np.array([170, 140, 110], float)
GROUND_COLOUR = np.array([150, 120, 85], float)
TRUCK_COLOURS = [(240, 180, 40), (60, 160, 240), (230, 80, 80), (120, 220, 120)]


class Truck:
    def __init__(self, truck_id: str, x: float, z: float, yaw: float) -> None:
        self.id, self.x, self.z, self.yaw = truck_id, x, z, yaw
        self.speed = 0.0
        self.throttle = 0.0
        self.steer = 0.0

    def step(self, dt: float) -> None:
        target = self.throttle * MAX_SPEED
        self.speed += (target - self.speed) * min(1.0, dt * 1.5)
        yaw_rate = self.speed / WHEELBASE * math.tan(math.radians(self.steer * MAX_STEER_DEG))
        self.yaw = (self.yaw + math.degrees(yaw_rate * dt)) % 360.0
        self.x += math.sin(math.radians(self.yaw)) * self.speed * dt
        self.z += math.cos(math.radians(self.yaw)) * self.speed * dt


class Pedestrian:
    def __init__(self, pedestrian_id: str, path: list[tuple[float, float]], start: float = 0.0, speed: float = 1.4) -> None:
        self.id, self.path, self.speed = pedestrian_id, path, speed
        self.loop = len(path) > 2
        self.direction = 1
        segments = [(path[i], path[(i + 1) % len(path)]) for i in range(len(path) if self.loop else len(path) - 1)]
        remaining = start * sum(math.dist(a, b) for a, b in segments)
        for i, (a, b) in enumerate(segments):
            length = math.dist(a, b)
            if remaining <= length or i == len(segments) - 1:
                f = min(remaining / length, 1.0) if length else 0.0
                self.x, self.z = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                self.yaw = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0
                self.target = (i + 1) % len(path)
                break
            remaining -= length

    def step(self, dt: float) -> None:
        gx, gz = self.path[self.target]
        dx, dz = gx - self.x, gz - self.z
        dist = math.hypot(dx, dz)
        if dist < 0.2:
            self._advance()
            return
        self.yaw = math.degrees(math.atan2(dx, dz)) % 360.0
        move = min(self.speed * dt, dist)
        self.x += dx / dist * move
        self.z += dz / dist * move

    def _advance(self) -> None:
        if self.loop:
            self.target = (self.target + 1) % len(self.path)
            return
        if not 0 <= self.target + self.direction < len(self.path):
            self.direction = -self.direction
        self.target += self.direction


def load_world(world_dir: str | Path) -> dict:
    world = json.loads((Path(world_dir) / "world.json").read_text())
    nodes = {s["id"]: s["pos"] for s in world["sites"]} | {j["id"]: j["pos"][0::2] for j in world["junctions"]}
    return {
        "bounds": [float(v) for v in world["bounds"]],
        "roads": [
            {
                "id": r["id"],
                "from": r["from"],
                "to": r["to"],
                "width_m": float(r["width_m"]),
                "one_way": bool(r.get("one_way", False)),
                "points": [{"x": p[0], "z": p[2]} for p in r["points"]],
            }
            for r in world["roads"]
            if r["from"] in nodes and r["to"] in nodes
        ],
        "sites": [{"id": s["id"], "type": s["type"], "name": s["name"], "x": s["pos"][0], "z": s["pos"][1]} for s in world["sites"]],
        "junctions": [{"id": j["id"], "x": j["pos"][0], "z": j["pos"][2]} for j in world["junctions"]],
        "trucks": [(t["id"], *t["spawn"], float(t["heading_deg"])) for t in sorted(world["trucks"], key=lambda t: t.get("slot", 0))],
        "equipment": [(e["pos3"][0], e["pos3"][2]) for e in world.get("equipment", [])],
        "terrain": load_terrain(Path(world_dir) / "navgrid.json"),
    }


def load_terrain(path: Path) -> dict | None:
    if not path.is_file():
        return None
    nav = json.loads(path.read_text())
    heights = np.array(nav["heights"], float).reshape(nav["height"], nav["width"])
    return {"heights": heights, "cell": float(nav["cell_m"]), "origin": [float(v) for v in nav["origin"]]}


def ground_at(terrain: dict, x, z) -> np.ndarray:
    h = terrain["heights"]
    c = np.clip(np.floor((np.asarray(x) - terrain["origin"][0]) / terrain["cell"]).astype(int), 0, h.shape[1] - 1)
    r = np.clip(np.floor((np.asarray(z) - terrain["origin"][1]) / terrain["cell"]).astype(int), 0, h.shape[0] - 1)
    return h[r, c]


def polyline_midpoint(pts: list[tuple[float, float]]) -> tuple[int, float, float]:
    lengths = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    remaining = sum(lengths) / 2
    for i, length in enumerate(lengths):
        if remaining <= length or i == len(lengths) - 1:
            f = remaining / length if length else 0.0
            (ax, az), (bx, bz) = pts[i], pts[i + 1]
            return i, ax + (bx - ax) * f, az + (bz - az) * f
        remaining -= length


def crossing_paths(roads: list[dict]) -> list[list[tuple[float, float]]]:
    paths = []
    for road in sorted(roads, key=lambda r: r["id"])[:CROSSING_ROADS]:
        pts = [(p["x"], p["z"]) for p in road["points"]]
        i, mx, mz = polyline_midpoint(pts)
        (ax, az), (bx, bz) = pts[i], pts[i + 1]
        seg = math.hypot(bx - ax, bz - az)
        rx, rz = (bz - az) / seg, -(bx - ax) / seg
        half = (road["width_m"] + CROSSING_EXTRA) / 2
        paths.append([(mx - rx * half, mz - rz * half), (mx + rx * half, mz + rz * half)])
    return paths


def camera_pose(t: Truck, ground: float = 0.0) -> dict:
    return {"x": t.x, "y": ground + CAM_HEIGHT, "z": t.z, "yaw": t.yaw, "pitch": 0.0, "roll": 0.0, "hfov": FOV_DEG}


def path_distance(x: float, z: float, paths: list[list[tuple[float, float]]] = PEDESTRIAN_PATHS) -> float:
    best = math.inf
    for path in paths:
        for i in range(len(path)):
            (ax, az), (bx, bz) = path[i], path[(i + 1) % len(path)]
            abx, abz = bx - ax, bz - az
            t = max(0.0, min(1.0, ((x - ax) * abx + (z - az) * abz) / (abx * abx + abz * abz)))
            best = min(best, math.hypot(x - ax - t * abx, z - az - t * abz))
    return best


class FakeQuarry:
    def __init__(self, n_trucks: int, half_size: float = 100.0, seed: int = 0, world: dict | None = None) -> None:
        rng = np.random.default_rng(seed)
        self.world = world
        if world:
            self.half = max(abs(v) for v in world["bounds"])
            self.bounds = world["bounds"]
            self.paths = crossing_paths(world["roads"])
            self.rocks = np.array(
                [[x, z, EQUIPMENT_RADIUS, EQUIPMENT_HEIGHT, ROCK] for x, z in world["equipment"]], float
            ).reshape(-1, 5)
            self.trucks = [Truck(*spawn) for spawn in world["trucks"][:n_trucks]]
        else:
            self.half = half_size
            self.bounds = [-half_size, -half_size, half_size, half_size]
            self.paths = PEDESTRIAN_PATHS
            n_rocks = 40
            radius = rng.uniform(1.5, 5.0, n_rocks)
            xz = rng.uniform(-half_size * 0.9, half_size * 0.9, (n_rocks, 2))
            rocks = np.column_stack([xz, radius, radius * 1.5, np.full(n_rocks, ROCK)])
            keep = [math.hypot(x, z + 70) > 20 and path_distance(x, z) > r + 4 for x, z, r in rocks[:, :3]]
            self.rocks = rocks[keep]
            self.trucks = [Truck(f"truck-{i}", -15.0 + 15.0 * i, -70.0, 0.0) for i in range(n_trucks)]
        self.pedestrians = [
            Pedestrian(
                f"pedestrian-{i * PEDESTRIANS_PER_PATH + k:02d}",
                path,
                k / PEDESTRIANS_PER_PATH,
                float(rng.uniform(MIN_WALK_SPEED, MAX_WALK_SPEED)),
            )
            for i, path in enumerate(self.paths)
            for k in range(PEDESTRIANS_PER_PATH)
        ]

    def step(self, dt: float) -> None:
        for t in self.trucks:
            t.step(dt)
            t.x = float(np.clip(t.x, -self.half + 1, self.half - 1))
            t.z = float(np.clip(t.z, -self.half + 1, self.half - 1))
        for p in self.pedestrians:
            p.step(dt)

    def _obstacles(self, me: Truck) -> np.ndarray:
        trucks = [[o.x, o.z, TRUCK_RADIUS, TRUCK_HEIGHT, TRUCK] for o in self.trucks if o is not me]
        people = [[p.x, p.z, PEDESTRIAN_RADIUS, PEDESTRIAN_HEIGHT, PERSON] for p in self.pedestrians]
        return np.vstack([self.rocks, np.array(trucks + people).reshape(-1, 5)])

    def _ray_hits(self, t: Truck, angles: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        dx, dz = np.sin(angles), np.cos(angles)
        obs = self._obstacles(t)
        ox, oz, r = obs[:, 0] - t.x, obs[:, 1] - t.z, obs[:, 2]
        proj = dx[:, None] * ox + dz[:, None] * oz
        perp2 = (ox**2 + oz**2) - proj**2
        hit = (proj > 0) & (perp2 < r**2)
        dist = np.where(hit, proj - np.sqrt(np.maximum(r**2 - perp2, 0)), np.inf)
        with np.errstate(divide="ignore"):
            wx = np.where(dx > 0, (self.half - t.x) / dx, np.where(dx < 0, (-self.half - t.x) / dx, np.inf))
            wz = np.where(dz > 0, (self.half - t.z) / dz, np.where(dz < 0, (-self.half - t.z) / dz, np.inf))
        return dist, obs, np.minimum(wx, wz)

    def scan_lidar(self, t: Truck) -> np.ndarray:
        az = np.radians(-LIDAR_HFOV / 2 + (np.arange(LIDAR_COLS) + 0.5) * LIDAR_HFOV / LIDAR_COLS)
        el = np.radians(LIDAR_VMIN + (np.arange(LIDAR_CHANNELS) + 0.5) * (LIDAR_VMAX - LIDAR_VMIN) / LIDAR_CHANNELS)
        dist, obs, wall = self._ray_hits(t, math.radians(t.yaw) + az)
        horizontal = np.concatenate([dist, wall[:, None]], axis=1)[None]
        tops = np.append(obs[:, 3], WALL_HEIGHT)
        cos_e, sin_e = np.cos(el)[:, None], np.sin(el)[:, None]
        rng = horizontal / cos_e[..., None]
        y = LIDAR_HEIGHT + rng * sin_e[..., None]
        rng = np.where((y >= 0) & (y <= tops), rng, np.inf).min(axis=2)
        with np.errstate(divide="ignore"):
            rng = np.minimum(rng, np.where(sin_e < 0, -LIDAR_HEIGHT / sin_e, np.inf))
        pts = np.stack([rng * cos_e * np.sin(az), rng * sin_e, rng * cos_e * np.cos(az)], axis=-1)
        pts = pts[rng <= LIDAR_RANGE]
        terrain = self.world.get("terrain") if self.world else None
        if terrain is not None:
            yaw = math.radians(t.yaw)
            wx = t.x + pts[:, 2] * math.sin(yaw) + pts[:, 0] * math.cos(yaw)
            wz = t.z + pts[:, 2] * math.cos(yaw) - pts[:, 0] * math.sin(yaw)
            pts[:, 1] += ground_at(terrain, wx, wz) - ground_at(terrain, t.x, t.z)
        return pts.astype(np.float32)

    def ground(self, t: Truck) -> float:
        terrain = self.world.get("terrain") if self.world else None
        return float(ground_at(terrain, t.x, t.z)) if terrain is not None else 0.0

    def render_truck(self, t: Truck) -> tuple[np.ndarray, np.ndarray]:
        focal = (CAM_W / 2) / math.tan(math.radians(FOV_DEG / 2))
        offsets = np.arctan((np.arange(CAM_W) + 0.5 - CAM_W / 2) / focal)
        cos_off = np.cos(offsets)
        dist, obs, wall = self._ray_hits(t, math.radians(t.yaw) + offsets)
        horizon = CAM_H / 2
        rows = np.arange(CAM_H)[:, None] + 0.5

        is_ground = rows > horizon
        ground = np.where(is_ground, focal * CAM_HEIGHT / np.maximum(rows - horizon, 1e-6), MAX_RANGE)
        depth = np.broadcast_to(np.minimum(ground, MAX_RANGE), (CAM_H, CAM_W)).astype(np.float32)
        layer = np.full((CAM_H, CAM_W), -1, np.int16)

        visible = np.flatnonzero(np.isfinite(dist).any(axis=0))
        nearest = np.where(np.isfinite(dist[:, visible]), dist[:, visible], np.inf).min(axis=0)
        layers = [(wall, WALL_HEIGHT, WALL_COLOUR)]
        layers += [(dist[:, n], obs[n, 3], KIND_COLOURS[int(obs[n, 4])]) for n in visible[np.argsort(-nearest)]]
        colours = np.empty((len(layers), CAM_W, 3), np.float32)

        for k, (d, height, colour) in enumerate(layers):
            ok = np.isfinite(d)
            d = np.where(ok, d * cos_off, 1.0)
            top = horizon - focal * (height - CAM_HEIGHT) / d
            bottom = horizon + focal * CAM_HEIGHT / d
            mask = ok & (rows >= top) & (rows <= bottom)
            layer[mask] = k
            depth[mask] = np.broadcast_to(np.minimum(d, MAX_RANGE), mask.shape)[mask]
            colours[k] = colour * np.clip(1.0 - d / (2 * MAX_RANGE), 0.3, 1.0)[:, None]

        sky = np.linspace(200, 140, CAM_H)[:, None] * np.array([0.55, 0.75, 1.0])
        background = np.where(is_ground, GROUND_COLOUR, sky)
        rgb = np.broadcast_to(background[:, None, :], (CAM_H, CAM_W, 3)).copy()
        hit = layer >= 0
        rgb[hit] = colours[layer[hit], np.nonzero(hit)[1]]
        return rgb.astype(np.uint8), depth

    def render_overview(self) -> np.ndarray:
        s = OVERVIEW_PX / (2 * self.half)

        def px(x: float, z: float) -> tuple[float, float]:
            return (x + self.half) * s, (self.half - z) * s

        img = Image.new("RGB", (OVERVIEW_PX, OVERVIEW_PX), tuple(int(c) for c in GROUND_COLOUR))
        d = ImageDraw.Draw(img)
        for road in self.world["roads"] if self.world else []:
            width = max(1, round(road["width_m"] * s))
            d.line([px(p["x"], p["z"]) for p in road["points"]], fill=ROAD_COLOUR, width=width, joint="curve")
        for x, z, r, _, _ in self.rocks:
            cx, cy = px(x, z)
            d.ellipse([cx - r * s, cy - r * s, cx + r * s, cy + r * s], fill=(110, 110, 115))
        for p in self.pedestrians:
            cx, cy = px(p.x, p.z)
            d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 120, 20))
        for i, t in enumerate(self.trucks):
            cx, cy = px(t.x, t.z)
            yaw = math.radians(t.yaw)
            fwd = (math.sin(yaw), -math.cos(yaw))
            side = (-fwd[1], fwd[0])
            k = TRUCK_RADIUS * s * 1.6
            tri = [
                (cx + fwd[0] * k, cy + fwd[1] * k),
                (cx - fwd[0] * k + side[0] * k * 0.6, cy - fwd[1] * k + side[1] * k * 0.6),
                (cx - fwd[0] * k - side[0] * k * 0.6, cy - fwd[1] * k - side[1] * k * 0.6),
            ]
            d.polygon(tri, fill=TRUCK_COLOURS[i % len(TRUCK_COLOURS)], outline=(0, 0, 0))
        return np.asarray(img)

    def hello(self) -> dict:
        network = {k: self.world[k] for k in ("roads", "sites", "junctions")} if self.world else {}
        return {
            **network,
            "type": "hello",
            "trucks": [t.id for t in self.trucks],
            "cameras": ["overview", *(t.id for t in self.trucks)],
            "bounds": self.bounds,
            "truck_model": "fakesim",
            "obstacles": [{"x": x, "z": z, "r": r} for x, z, r, _, _ in self.rocks.tolist()],
            "paths": [{"points": [{"x": x, "z": z} for x, z in path]} for path in self.paths],
        }

    def actors(self) -> dict:
        return {
            "type": "actors",
            "pedestrians": [{"id": p.id, "x": p.x, "z": p.z, "yaw": p.yaw} for p in self.pedestrians],
        }


async def run(host: str, port: int, n_trucks: int, world_dir: str | None = None) -> None:
    quarry = FakeQuarry(n_trucks, world=load_world(world_dir) if world_dir else None)
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as e:
            print(f"brain not reachable at {host}:{port} ({e}); retrying in 2s")
            await asyncio.sleep(2)
            continue
        print(f"connected to brain at {host}:{port}")
        trucks = {t.id: t for t in quarry.trucks}
        writer.write(encode(quarry.hello()))

        async def receive_actions() -> None:
            while True:
                header, _ = await read_message(reader)
                t = trucks.get(header.get("truck"))
                if header.get("type") == "action" and t:
                    t.throttle = float(header["throttle"])
                    t.steer = float(header["steer"])

        receiver = asyncio.create_task(receive_actions())
        try:
            next_tick = time.monotonic()
            sim_time = next_lidar = next_actors = 0.0
            while not receiver.done():
                quarry.step(1 / FPS)
                sim_time += 1 / FPS
                if sim_time >= next_actors:
                    next_actors += 1 / ACTORS_HZ
                    writer.write(encode(quarry.actors()))
                if sim_time >= next_lidar:
                    next_lidar += 1 / LIDAR_HZ
                    for t in quarry.trucks:
                        writer.write(encode_lidar(t.id, quarry.scan_lidar(t), {"x": t.x, "z": t.z, "yaw": t.yaw}))
                writer.write(encode_frame("overview", quarry.render_overview()))
                for t in quarry.trucks:
                    rgb, depth = quarry.render_truck(t)
                    ground = quarry.ground(t)
                    pose = {"x": t.x, "y": ground, "z": t.z, "yaw": t.yaw}
                    writer.write(encode_frame(t.id, rgb, depth, truck=t.id, pose=pose, speed=t.speed, cam=camera_pose(t, ground)))
                await writer.drain()
                next_tick += 1 / FPS
                await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
        except ConnectionError:
            pass
        finally:
            receiver.cancel()
            writer.close()
        print("disconnected from brain; reconnecting")
        await asyncio.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--trucks", type=int, default=3)
    p.add_argument("--world", default="", help="world directory with world.json; empty = flat test world")
    args = p.parse_args()
    asyncio.run(run(args.host, args.port, args.trucks, args.world or None))


if __name__ == "__main__":
    main()
