import math
from collections.abc import Callable

import numpy as np

MAX_SPEED = 8.0
MAX_STEER_DEG = 30.0
WHEELBASE = 5.0
ACCEL = 1.5
COMMAND_TIMEOUT = 0.5
PROBE_UP = 8.0
PROBE_DOWN = 150.0
HIGH_PROBE = 1000.0
HEIGHT_SMOOTH = 0.06
SNAP_DISTANCE = 4.0
MAX_TILT_DEG = 25.0
TILT_SMOOTH = 0.15

Probe = Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], np.ndarray]


def yaw_matrix(yaw: float) -> np.ndarray:
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def quat_from_matrix(m: np.ndarray) -> np.ndarray:
    w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
    x = math.copysign(math.sqrt(max(0.0, 1 + m[0, 0] - m[1, 1] - m[2, 2])) / 2, m[2, 1] - m[1, 2])
    y = math.copysign(math.sqrt(max(0.0, 1 - m[0, 0] + m[1, 1] - m[2, 2])) / 2, m[0, 2] - m[2, 0])
    z = math.copysign(math.sqrt(max(0.0, 1 - m[0, 0] - m[1, 1] + m[2, 2])) / 2, m[1, 0] - m[0, 1])
    q = np.array([w, x, y, z])
    return q / np.linalg.norm(q)


def quat_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    d = float(np.dot(a, b))
    if d < 0:
        b, d = -b, -d
    if d > 0.9995:
        q = a + (b - a) * t
        return q / np.linalg.norm(q)
    theta = math.acos(d)
    return (math.sin((1 - t) * theta) * a + math.sin(t * theta) * b) / math.sin(theta)


def quat_angle(q: np.ndarray) -> float:
    return math.degrees(2 * math.acos(min(1.0, abs(float(q[0])))))


def look_rotation(forward: np.ndarray, up: np.ndarray) -> np.ndarray:
    z = forward / np.linalg.norm(forward)
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    return np.column_stack([x, np.cross(z, x), z])


class Truck:
    def __init__(self, truck_id: str, x: float, z: float, yaw: float, y: float = 0.0) -> None:
        self.id, self.x, self.y, self.z, self.yaw = truck_id, x, y, z, yaw % 360.0
        self.speed = 0.0
        self.throttle = 0.0
        self.steer = 0.0
        self.received = -math.inf

    def command(self, throttle: float, steer: float, now: float) -> None:
        self.throttle = min(1.0, max(0.0, float(throttle)))
        self.steer = min(1.0, max(-1.0, float(steer)))
        self.received = now

    def step(self, dt: float, now: float) -> None:
        live = now - self.received <= COMMAND_TIMEOUT
        throttle, steer = (self.throttle, self.steer) if live else (0.0, 0.0)
        self.speed += (throttle * MAX_SPEED - self.speed) * min(1.0, dt * ACCEL)
        yaw_rate = self.speed / WHEELBASE * math.tan(math.radians(steer * MAX_STEER_DEG))
        self.yaw = (self.yaw + math.degrees(yaw_rate) * dt) % 360.0
        self.x += math.sin(math.radians(self.yaw)) * self.speed * dt
        self.z += math.cos(math.radians(self.yaw)) * self.speed * dt

    def pose(self) -> dict:
        return {"x": self.x, "z": self.z, "yaw": self.yaw}


class GroundFollow:
    def __init__(self, box_size: tuple[float, float, float], box_centre_z: float = 0.0) -> None:
        self.front = box_centre_z + box_size[2] * 0.30
        self.rear = box_centre_z - box_size[2] * 0.28
        self.half_track = box_size[0] * 0.38
        self.tilt = np.array([1.0, 0.0, 0.0, 0.0])
        self.smooth_y: float | None = None
        self.tilt_init = False

    def sample(self, probe: Probe, xs: np.ndarray, zs: np.ndarray, ref: float | np.ndarray) -> np.ndarray:
        ref = np.broadcast_to(np.asarray(ref, float), xs.shape)
        h = probe(xs, zs, ref + PROBE_UP, np.full(len(xs), PROBE_UP + PROBE_DOWN))
        miss = ~np.isfinite(h)
        if miss.any():
            h[miss] = probe(xs[miss], zs[miss], ref[miss] + HIGH_PROBE, np.full(miss.sum(), HIGH_PROBE + PROBE_DOWN))
        return h

    def points(self, truck: Truck) -> np.ndarray:
        local = np.array(
            [
                [0, 0, 0],
                [-self.half_track, 0, self.front],
                [self.half_track, 0, self.front],
                [-self.half_track, 0, self.rear],
                [self.half_track, 0, self.rear],
            ],
            float,
        )
        return local @ yaw_matrix(truck.yaw).T + [truck.x, 0, truck.z]

    def ref(self, truck: Truck) -> float:
        return self.smooth_y if self.smooth_y is not None else truck.y

    def update(self, truck: Truck, probe: Probe, dt: float) -> None:
        pts = self.points(truck)
        self.apply(truck, self.sample(probe, pts[:, 0], pts[:, 2], self.ref(truck)), dt)

    def apply(self, truck: Truck, h: np.ndarray, dt: float) -> None:
        ok = np.isfinite(h)
        if not ok.any():
            return
        centre = h[0] if ok[0] else float(h[1:][ok[1:]].mean())
        wheels = np.where(ok[1:], h[1:], centre)
        target = float(wheels.mean()) if ok[1:].sum() >= 2 else float(centre)
        if self.smooth_y is None or abs(target - self.smooth_y) > SNAP_DISTANCE or HEIGHT_SMOOTH <= 0:
            self.smooth_y = target
        else:
            k = 1 - math.exp(-dt / HEIGHT_SMOOTH)
            nxt = self.smooth_y + (target - self.smooth_y) * k
            if target > self.smooth_y:
                nxt = max(nxt, target - 0.25)
            self.smooth_y = nxt
        truck.y = self.smooth_y
        self.update_tilt(*wheels, dt)

    def update_tilt(self, fl: float, fr: float, rl: float, rr: float, dt: float) -> None:
        wheelbase = max(0.5, self.front - self.rear)
        track = max(0.5, self.half_track * 2)
        f = np.array([0.0, (fl + fr - rl - rr) * 0.5, wheelbase])
        r = np.array([track, (fr + rr - fl - rl) * 0.5, 0.0])
        up = np.cross(f, r)
        target = quat_from_matrix(look_rotation(f, up)) if np.dot(up, up) > 1e-8 else np.array([1.0, 0, 0, 0])
        angle = quat_angle(target)
        identity = np.array([1.0, 0, 0, 0])
        if angle > MAX_TILT_DEG:
            target = slerp(identity, target, MAX_TILT_DEG / angle)
        if not self.tilt_init or TILT_SMOOTH <= 0:
            self.tilt = target
        else:
            self.tilt = slerp(self.tilt, target, 1 - math.exp(-dt / TILT_SMOOTH))
        self.tilt_init = True

    def rotation(self, yaw: float) -> np.ndarray:
        return yaw_matrix(yaw) @ quat_matrix(self.tilt)
