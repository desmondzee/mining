import math
from collections.abc import Callable

from .world import PEDESTRIANS_PER_PATH, walk_speeds

TURN_RATE = 360.0
ARRIVE = 0.2
PEDESTRIAN_HEIGHT = 1.8
PEDESTRIAN_RADIUS = 0.3

Ground = Callable[[float, float, float], float]


class Walker:
    def __init__(self, walker_id: str, path: list[tuple[float, float]], start: float = 0.0, speed: float = 1.4) -> None:
        self.id, self.path, self.speed = walker_id, path, speed
        self.loop = len(path) > 2
        self.step_dir = 1
        self.y = 0.0
        n = len(path)
        segments = [(path[i], path[(i + 1) % n]) for i in range(n if self.loop else n - 1)]
        remaining = start * sum(math.dist(a, b) for a, b in segments)
        for i, (a, b) in enumerate(segments):
            length = math.dist(a, b)
            if remaining <= length or i == len(segments) - 1:
                f = min(max(remaining / length, 0.0), 1.0) if length else 0.0
                self.x, self.z = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                self.yaw = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0
                self.target = (i + 1) % n
                break
            remaining -= length

    def step(self, dt: float, ground: Ground | None = None) -> None:
        gx, gz = self.path[self.target]
        dx, dz = gx - self.x, gz - self.z
        dist = math.hypot(dx, dz)
        if dist < ARRIVE:
            self.advance()
            return
        want = math.degrees(math.atan2(dx, dz))
        turn = (want - self.yaw + 180.0) % 360.0 - 180.0
        limit = TURN_RATE * dt
        self.yaw = (self.yaw + max(-limit, min(limit, turn))) % 360.0
        move = min(self.speed * dt, dist)
        self.x += math.sin(math.radians(self.yaw)) * move
        self.z += math.cos(math.radians(self.yaw)) * move
        if ground:
            self.y = ground(self.x, self.z, self.y)

    def advance(self) -> None:
        if self.loop:
            self.target = (self.target + 1) % len(self.path)
            return
        if not 0 <= self.target + self.step_dir < len(self.path):
            self.step_dir = -self.step_dir
        self.target += self.step_dir

    def state(self) -> dict:
        return {"id": self.id, "x": self.x, "z": self.z, "yaw": self.yaw}


def make_walkers(paths: list[list[tuple[float, float]]], per_path: int = PEDESTRIANS_PER_PATH) -> list[Walker]:
    speeds = walk_speeds(len(paths) * per_path)
    return [
        Walker(f"Pedestrian_{i * per_path + k:02d}", path, k / per_path, speeds[i * per_path + k])
        for i, path in enumerate(paths)
        for k in range(per_path)
    ]
