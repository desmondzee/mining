import math

import numpy as np

ZOOM_SECONDS = 0.4
UP = np.array([0.0, 0.0, 1.0])


def aim(eye: np.ndarray, forward: np.ndarray) -> np.ndarray:
    f = forward / np.linalg.norm(forward)
    right = np.cross(f, UP)
    right /= np.linalg.norm(right)
    m = np.eye(4)
    m[:3, :3] = np.column_stack([right, np.cross(right, f), -f])
    m[:3, 3] = eye
    return m


def box_rays(base: np.ndarray, tan_x: float, aspect: float, box: list[float]) -> np.ndarray:
    x0, y0, x1, y1 = box
    u = np.array([x0, x1, x1, x0, (x0 + x1) / 2])
    v = np.array([y0, y0, y1, y1, (y0 + y1) / 2])
    cam = np.stack([(2 * u - 1) * tan_x, (1 - 2 * v) * tan_x / aspect, -np.ones_like(u)], axis=1)
    rays = cam @ base[:3, :3].T
    return rays / np.linalg.norm(rays, axis=1, keepdims=True)


def box_target(base: np.ndarray, hfov: float, aspect: float, box: list[float]) -> tuple[np.ndarray, float]:
    rays = box_rays(base, math.tan(math.radians(hfov) / 2), aspect, box)
    m = aim(base[:3, 3], rays[4])
    local = rays[:4] @ m[:3, :3]
    depth = -local[:, 2]
    tan_x = max(np.abs(local[:, 0] / depth).max(), np.abs(local[:, 1] / depth).max() * aspect)
    return rays[4], math.degrees(2 * math.atan(tan_x))


class OverviewZoom:
    def __init__(self, base: np.ndarray, hfov: float, aspect: float, duration: float = ZOOM_SECONDS) -> None:
        self.base = np.asarray(base, float)
        self.hfov0, self.aspect, self.duration = hfov, aspect, duration
        self.home = (-self.base[:3, 2], math.tan(math.radians(hfov) / 2))
        self.start = self.end = self.now = self.home
        self.box: list[float] | None = None
        self.pending: tuple | None = None
        self.elapsed = duration

    def set(self, box: list[float] | None) -> None:
        self.pending = (None if box is None else [float(v) for v in box],)

    def step(self, dt: float) -> bool:
        pending, self.pending = self.pending, None
        if pending is not None:
            self.box = pending[0]
            if self.box is None:
                self.end = self.home
            else:
                f, hfov = box_target(self.base, self.hfov0, self.aspect, self.box)
                self.end = (f, math.tan(math.radians(hfov) / 2))
            self.start, self.elapsed = self.now, 0.0
        if self.elapsed >= self.duration:
            return pending is not None
        self.elapsed = min(self.elapsed + dt, self.duration)
        p = self.elapsed / self.duration if self.duration > 0 else 1.0
        if p >= 1:
            self.now = self.end
            return True
        s = p * p * (3 - 2 * p)
        (f0, t0), (f1, t1) = self.start, self.end
        f = f0 * (1 - s) + f1 * s
        self.now = (f / np.linalg.norm(f), math.exp(math.log(t0) * (1 - s) + math.log(t1) * s))
        return True

    @property
    def settled(self) -> bool:
        return self.pending is None and self.elapsed >= self.duration

    @property
    def matrix(self) -> np.ndarray:
        return self.base.copy() if self.now is self.home else aim(self.base[:3, 3], self.now[0])

    @property
    def hfov(self) -> float:
        return math.degrees(2 * math.atan(self.now[1]))

    def meta(self) -> dict:
        return {"box": self.box, "settled": self.settled}
