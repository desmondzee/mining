"""Stand-in for the Unity player: 3 trucks on a flat quarry floor with rock obstacles.

Speaks exactly the same protocol as Unity, so the brain, streaming and website can be
developed and tested without a Unity build. Rendering is a crude per-column raycast.
"""

import argparse
import asyncio
import math
import time

import numpy as np
from PIL import Image, ImageDraw

from .protocol import encode, encode_frame, read_message

FPS = 15
CAM_W, CAM_H = 640, 360
FOV_DEG = 90.0
MAX_RANGE = 100.0
OVERVIEW_PX = 480
MAX_SPEED = 8.0  # m/s
MAX_STEER_DEG = 30.0
WHEELBASE = 5.0
TRUCK_RADIUS = 2.5
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


class FakeQuarry:
    def __init__(self, n_trucks: int, half_size: float = 100.0, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)
        self.half = half_size
        self.bounds = [-half_size, -half_size, half_size, half_size]
        n_rocks = 40
        self.rocks = np.column_stack(
            [rng.uniform(-half_size * 0.9, half_size * 0.9, (n_rocks, 2)), rng.uniform(1.5, 5.0, n_rocks)]
        )
        # Keep the start area clear.
        self.rocks = self.rocks[np.hypot(self.rocks[:, 0], self.rocks[:, 1] + 70) > 20]
        self.trucks = [Truck(f"truck-{i}", -15.0 + 15.0 * i, -70.0, 0.0) for i in range(n_trucks)]

    def step(self, dt: float) -> None:
        for t in self.trucks:
            t.step(dt)
            t.x = float(np.clip(t.x, -self.half + 1, self.half - 1))
            t.z = float(np.clip(t.z, -self.half + 1, self.half - 1))

    def _obstacles_for(self, me: Truck) -> np.ndarray:
        others = [[t.x, t.z, TRUCK_RADIUS] for t in self.trucks if t is not me]
        return np.vstack([self.rocks, np.array(others).reshape(-1, 3)])

    def render_truck(self, t: Truck) -> tuple[np.ndarray, np.ndarray]:
        """Front camera: (rgb HxWx3 uint8, depth HxW float32 metres)."""
        offsets = np.radians(np.linspace(-FOV_DEG / 2, FOV_DEG / 2, CAM_W))
        angles = math.radians(t.yaw) + offsets
        dx, dz = np.sin(angles), np.cos(angles)

        # Ray vs circles (vectorised over columns x obstacles).
        obs = self._obstacles_for(t)
        ox, oz, r = obs[:, 0] - t.x, obs[:, 1] - t.z, obs[:, 2]
        proj = dx[:, None] * ox + dz[:, None] * oz
        perp2 = (ox**2 + oz**2) - proj**2
        hit = (proj > 0) & (perp2 < r**2)
        dist = np.where(hit, proj - np.sqrt(np.maximum(r**2 - perp2, 0)), np.inf).min(axis=1)

        # Ray vs the quarry walls.
        with np.errstate(divide="ignore"):
            wx = np.where(dx > 0, (self.half - t.x) / dx, np.where(dx < 0, (-self.half - t.x) / dx, np.inf))
            wz = np.where(dz > 0, (self.half - t.z) / dz, np.where(dz < 0, (-self.half - t.z) / dz, np.inf))
        wall = np.minimum(wx, wz)
        is_wall = wall < dist
        dist = np.minimum(dist, wall)
        # Perpendicular distance avoids fish-eye distortion.
        dist = np.minimum(dist * np.cos(offsets), MAX_RANGE)

        rows = np.arange(CAM_H)[:, None]
        horizon = CAM_H / 2
        half_h = np.minimum(CAM_H / 2, 900.0 / np.maximum(dist, 0.1))[None, :]
        in_obj = np.abs(rows - horizon) < half_h

        rgb = np.empty((CAM_H, CAM_W, 3), np.uint8)
        sky = np.linspace(200, 140, CAM_H)[:, None]
        rgb[..., 0] = np.where(rows < horizon, sky * 0.55, 150)
        rgb[..., 1] = np.where(rows < horizon, sky * 0.75, 120)
        rgb[..., 2] = np.where(rows < horizon, sky, 85)
        shade = np.clip(1.0 - dist / MAX_RANGE, 0.25, 1.0)[None, :]
        obj_colour = np.where(is_wall[None, :, None], [[[170, 140, 110]]], [[[120, 120, 125]]])
        rgb = np.where(in_obj[..., None], (obj_colour * shade[..., None]).astype(np.uint8), rgb)

        depth = np.where(in_obj, dist[None, :], MAX_RANGE).astype(np.float32)
        return rgb, depth

    def render_overview(self) -> np.ndarray:
        s = OVERVIEW_PX / (2 * self.half)

        def px(x: float, z: float) -> tuple[float, float]:
            return (x + self.half) * s, (self.half - z) * s

        img = Image.new("RGB", (OVERVIEW_PX, OVERVIEW_PX), (150, 120, 85))
        d = ImageDraw.Draw(img)
        for x, z, r in self.rocks:
            cx, cy = px(x, z)
            d.ellipse([cx - r * s, cy - r * s, cx + r * s, cy + r * s], fill=(110, 110, 115))
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
        writer.write(
            encode(
                {
                    "type": "hello",
                    "trucks": list(trucks),
                    "cameras": ["overview", *trucks],
                    "bounds": quarry.bounds,
                }
            )
        )

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
            while not receiver.done():
                quarry.step(1 / FPS)
                writer.write(encode_frame("overview", quarry.render_overview()))
                for t in quarry.trucks:
                    rgb, depth = quarry.render_truck(t)
                    writer.write(
                        encode_frame(
                            t.id,
                            rgb,
                            depth,
                            truck=t.id,
                            pose={"x": t.x, "z": t.z, "yaw": t.yaw},
                            speed=t.speed,
                        )
                    )
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--trucks", type=int, default=3)
    args = p.parse_args()
    asyncio.run(run(args.host, args.port, args.trucks))


if __name__ == "__main__":
    main()
