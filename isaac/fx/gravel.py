import math
from typing import Callable

import numpy as np

from .presets import DUAL_HALF_WIDTH, GRAVITY, TYRE_RADIUS

DRAG = 0.15
RESTITUTION = 0.3
BOUNCE_MIN = 1.5
REST_TIME = 0.4
SHRINK_TIME = 0.25
MAX_LIFE = 4.0
ELEVATION = (15.0, 60.0)
SPEED_FRACTION = (0.35, 1.0)
LATERAL = 0.25
SIZE = (0.04, 0.16)
SPIN = (4.0, 20.0)
FIELDS = [
    ("pos", (3,), float), ("vel", (3,), float), ("axis", (3,), float),
    ("ground", (), float), ("age", (), float), ("rest", (), float), ("size", (), float), ("angle", (), float), ("spin", (), float),
    ("proto", (), np.int32), ("bounced", (), bool), ("alive", (), bool),
]


def truck_frame(yaw_deg: float) -> tuple[np.ndarray, np.ndarray]:
    y = math.radians(yaw_deg)
    forward = np.array([math.sin(y), math.cos(y), 0.0])
    right = np.array([math.cos(y), -math.sin(y), 0.0])
    return forward, right


def rear_contacts(pos, yaw_deg: float, rear_axle: float, half_track: float) -> np.ndarray:
    forward, right = truck_frame(yaw_deg)
    base = np.asarray(pos, float) + forward * rear_axle
    return np.array([base - right * half_track, base + right * half_track])


def intensity(speed: float, min_speed: float, full_speed: float) -> float:
    return float(np.clip((abs(speed) - min_speed) / max(full_speed - min_speed, 1e-6), 0.0, 1.0))


class Gravel:
    def __init__(self, cap: int, rate: float, seed: int = 0, min_speed: float = 1.0, protos: int = 3, tones: int = 1) -> None:
        self.cap, self.rate, self.seed, self.min_speed, self.protos, self.tones = cap, rate, seed, min_speed, protos, tones
        self.slots: dict[str, int] = {}
        self.rngs: list[np.random.Generator] = []
        self.acc = np.zeros(0)
        for name, shape, dtype in FIELDS:
            setattr(self, name, np.zeros((0, *shape), dtype))

    def slot(self, truck_id: str) -> int:
        if truck_id not in self.slots:
            k = len(self.slots)
            self.slots[truck_id] = k
            self.rngs.append(np.random.default_rng([self.seed, k]))
            self.acc = np.append(self.acc, 0.0)
            for name, shape, dtype in FIELDS:
                setattr(self, name, np.concatenate([getattr(self, name), np.zeros((self.cap, *shape), dtype)]))
        return self.slots[truck_id]

    def spawn_rate(self, speed: float, scale: float = 1.0) -> float:
        return self.rate * max(0.0, abs(speed) - self.min_speed) * max(scale, 0.0)

    def emit(self, truck_id: str, dt: float, speed: float, contacts: np.ndarray, forward: np.ndarray, right: np.ndarray, scale: float = 1.0, tone: float = 0.5) -> int:
        k = self.slot(truck_id)
        if self.cap == 0:
            return 0
        self.acc[k] += self.spawn_rate(speed, scale) * dt
        want = int(self.acc[k])
        self.acc[k] -= want
        lo = k * self.cap
        free = np.flatnonzero(~self.alive[lo : lo + self.cap]) + lo
        n = min(want, len(free))
        if n == 0:
            return 0
        rng = self.rngs[k]
        idx = free[:n]
        contacts = np.asarray(contacts, float).reshape(-1, 3)
        wheel = rng.integers(0, len(contacts), n)
        side = np.where(np.einsum("ij,j->i", contacts[wheel] - contacts.mean(0), right) >= 0, 1.0, -1.0)
        back = -np.sign(speed) if speed else -1.0
        across = rng.uniform(-DUAL_HALF_WIDTH, DUAL_HALF_WIDTH, n)
        behind = rng.uniform(0.2, 0.9, n) * TYRE_RADIUS
        up = rng.uniform(0.05, 0.5, n)
        self.pos[idx] = contacts[wheel] + np.outer(across, right) + np.outer(back * behind, forward) + np.outer(up, [0.0, 0.0, 1.0])
        self.ground[idx] = contacts[wheel, 2]
        elev = np.radians(rng.uniform(*ELEVATION, n))
        s = abs(speed) * rng.uniform(*SPEED_FRACTION, n)
        lateral = s * LATERAL * (rng.uniform(-0.5, 1.0, n) * side)
        rel = np.outer(back * s * np.cos(elev), forward) + np.outer(lateral, right) + np.outer(s * np.sin(elev), [0.0, 0.0, 1.0])
        self.vel[idx] = rel + forward * speed
        self.age[idx] = 0.0
        self.rest[idx] = 0.0
        self.bounced[idx] = False
        self.size[idx] = SIZE[0] * (SIZE[1] / SIZE[0]) ** rng.random(n)
        ax = rng.normal(size=(n, 3))
        self.axis[idx] = ax / np.linalg.norm(ax, axis=1, keepdims=True)
        self.angle[idx] = rng.uniform(0, 2 * np.pi, n)
        self.spin[idx] = rng.uniform(*SPIN, n)
        bin_ = min(int(np.clip(tone, 0.0, 1.0) * self.tones), self.tones - 1)
        self.proto[idx] = rng.integers(0, self.protos, n) + self.protos * bin_
        self.alive[idx] = True
        return n

    def step(self, dt: float, height: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None) -> None:
        a = np.flatnonzero(self.alive)
        if not len(a) or dt <= 0:
            return
        flying = a[self.rest[a] == 0.0]
        v = self.vel[flying]
        v[:, 2] -= GRAVITY * dt
        v *= math.exp(-DRAG * dt)
        p = self.pos[flying] + v * dt
        self.angle[flying] += self.spin[flying] * dt
        g = self.ground[flying] if height is None else np.asarray(height(p[:, 0], p[:, 1]), float)
        self.ground[flying] = g
        hit = p[:, 2] <= g + self.size[flying] * 0.5
        if hit.any():
            p[hit, 2] = g[hit] + self.size[flying][hit] * 0.5
            bounce = hit & ~self.bounced[flying] & (-v[:, 2] > BOUNCE_MIN)
            v[bounce, 2] *= -RESTITUTION
            v[bounce, :2] *= 0.5
            self.bounced[flying[bounce]] = True
            settle = hit & ~bounce
            v[settle] = 0.0
            self.rest[flying[settle]] = 1e-9
        self.vel[flying], self.pos[flying] = v, p
        resting = a[self.rest[a] > 0.0]
        self.rest[resting] += dt
        self.age[a] += dt
        dead = a[(self.rest[a] > REST_TIME + SHRINK_TIME) | (self.age[a] > MAX_LIFE)]
        self.alive[dead] = False

    def update(self, dt: float, trucks, height=None) -> None:
        for truck_id, contacts, forward, right, speed, *extra in trucks:
            self.emit(truck_id, dt, speed, contacts, forward, right, *extra)
        self.step(dt, height)

    def count(self, truck_id: str | None = None) -> int:
        if truck_id is None:
            return int(self.alive.sum())
        k = self.slots[truck_id]
        return int(self.alive[k * self.cap : (k + 1) * self.cap].sum())

    def render_state(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        a = np.flatnonzero(self.alive)
        shrink = np.clip(1.0 - np.maximum(self.rest[a] - REST_TIME, 0.0) / SHRINK_TIME, 0.0, 1.0)
        half = self.angle[a] / 2
        quat = np.column_stack([np.cos(half), self.axis[a] * np.sin(half)[:, None]])
        return self.pos[a], quat, self.size[a] * shrink, self.proto[a]
