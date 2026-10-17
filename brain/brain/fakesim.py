import argparse
import asyncio
import math
import time

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
PEDESTRIAN_SPEED = 1.4
PEDESTRIAN_RADIUS = 0.3
PEDESTRIAN_HEIGHT = 1.8
PEDESTRIAN_PATHS = [
    [(-30, -40), (30, -40)],
    [(-10, -10), (10, -10), (10, 10), (-10, 10)],
    [(-50, 20), (-10, 60)],
    [(40, -20), (40, 40)],
]
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
    def __init__(self, pedestrian_id: str, path: list[tuple[float, float]]) -> None:
        self.id, self.path = pedestrian_id, path
        self.x, self.z = path[0]
        self.yaw = 0.0
        self.loop = len(path) > 2
        self.target, self.direction = 1, 1

    def step(self, dt: float) -> None:
        gx, gz = self.path[self.target]
        dx, dz = gx - self.x, gz - self.z
        dist = math.hypot(dx, dz)
        if dist < 0.2:
            self._advance()
            return
        self.yaw = math.degrees(math.atan2(dx, dz)) % 360.0
        move = min(PEDESTRIAN_SPEED * dt, dist)
        self.x += dx / dist * move
        self.z += dz / dist * move

    def _advance(self) -> None:
        if self.loop:
            self.target = (self.target + 1) % len(self.path)
            return
        if not 0 <= self.target + self.direction < len(self.path):
            self.direction = -self.direction
        self.target += self.direction


def path_distance(x: float, z: float) -> float:
    best = math.inf
    for path in PEDESTRIAN_PATHS:
        for i in range(len(path)):
            (ax, az), (bx, bz) = path[i], path[(i + 1) % len(path)]
            abx, abz = bx - ax, bz - az
            t = max(0.0, min(1.0, ((x - ax) * abx + (z - az) * abz) / (abx * abx + abz * abz)))
            best = min(best, math.hypot(x - ax - t * abx, z - az - t * abz))
    return best


class FakeQuarry:
    def __init__(self, n_trucks: int, half_size: float = 100.0, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        self.half = half_size
        self.bounds = [-half_size, -half_size, half_size, half_size]
        n_rocks = 40
        radius = rng.uniform(1.5, 5.0, n_rocks)
        xz = rng.uniform(-half_size * 0.9, half_size * 0.9, (n_rocks, 2))
        rocks = np.column_stack([xz, radius, radius * 1.5, np.full(n_rocks, ROCK)])
        keep = [math.hypot(x, z + 70) > 20 and path_distance(x, z) > r + 4 for x, z, r in rocks[:, :3]]
        self.rocks = rocks[keep]
        self.trucks = [Truck(f"truck-{i}", -15.0 + 15.0 * i, -70.0, 0.0) for i in range(n_trucks)]
        self.pedestrians = [Pedestrian(f"pedestrian-{i}", p) for i, p in enumerate(PEDESTRIAN_PATHS)]

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
        return pts[rng <= LIDAR_RANGE].astype(np.float32)

    def render_truck(self, t: Truck) -> tuple[np.ndarray, np.ndarray]:
        offsets = np.radians(np.linspace(-FOV_DEG / 2, FOV_DEG / 2, CAM_W))
        cos_off = np.cos(offsets)
        dist, obs, wall = self._ray_hits(t, math.radians(t.yaw) + offsets)
        focal = (CAM_W / 2) / math.tan(math.radians(FOV_DEG / 2))
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
        return {
            "type": "hello",
            "trucks": [t.id for t in self.trucks],
            "cameras": ["overview", *(t.id for t in self.trucks)],
            "bounds": self.bounds,
            "truck_model": "fakesim",
            "obstacles": [{"x": x, "z": z, "r": r} for x, z, r, _, _ in self.rocks.tolist()],
            "paths": [{"points": [{"x": x, "z": z} for x, z in p.path]} for p in self.pedestrians],
        }

    def actors(self) -> dict:
        return {
            "type": "actors",
            "pedestrians": [{"id": p.id, "x": p.x, "z": p.z, "yaw": p.yaw} for p in self.pedestrians],
        }


async def run(host: str, port: int, n_trucks: int) -> None:
    quarry = FakeQuarry(n_trucks)
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
                    pose = {"x": t.x, "z": t.z, "yaw": t.yaw}
                    writer.write(encode_frame(t.id, rgb, depth, truck=t.id, pose=pose, speed=t.speed))
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
    args = p.parse_args()
    asyncio.run(run(args.host, args.port, args.trucks))


if __name__ == "__main__":
    main()
