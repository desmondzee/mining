import math

import numpy as np

CAM_W = 640
CAM_H = 360
FPS = 30
HFOV = 90.0
DEPTH_W = 64
DEPTH_H = 36
DEPTH_RANGE = 100.0
SENSOR_OFFSET = 0.2
CAMERA_HEIGHT_FRACTION = 0.8
LIDAR_HEIGHT_FRACTION = 0.6
LIDAR_RANGE = 100.0
LIDAR_HFOV = 120.0
LIDAR_VMIN = -20.0
LIDAR_VMAX = 10.0
LIDAR_COLS = 240
LIDAR_CHANNELS = 30
LIDAR_HZ = 10
ACTORS_HZ = 10
OVERVIEW_PX = 480
OVERVIEW_HEIGHT = 400.0


def vfov(hfov: float = HFOV, w: int = CAM_W, h: int = CAM_H) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(hfov) / 2) * h / w))


def depth_directions(w: int = DEPTH_W, h: int = DEPTH_H, hfov: float = HFOV, aspect: float = CAM_W / CAM_H) -> np.ndarray:
    tx = math.tan(math.radians(hfov) / 2)
    ty = tx / aspect
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    x = (2 * u - 1) * tx
    y = (1 - 2 * v) * ty
    d = np.stack(np.broadcast_arrays(x[None, :], y[:, None], np.ones((h, w))), axis=-1).reshape(-1, 3)
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def lidar_directions() -> np.ndarray:
    el = np.radians(LIDAR_VMIN + (np.arange(LIDAR_CHANNELS) + 0.5) * (LIDAR_VMAX - LIDAR_VMIN) / LIDAR_CHANNELS)
    az = np.radians(-LIDAR_HFOV / 2 + (np.arange(LIDAR_COLS) + 0.5) * LIDAR_HFOV / LIDAR_COLS)
    el, az = np.meshgrid(el, az, indexing="ij")
    return np.stack([np.cos(el) * np.sin(az), np.sin(el), np.cos(el) * np.cos(az)], axis=-1).reshape(-1, 3)


def mounts(box_size: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    front = box_size[2] / 2 + SENSOR_OFFSET
    return (
        np.array([0.0, box_size[1] * CAMERA_HEIGHT_FRACTION, front]),
        np.array([0.0, box_size[1] * LIDAR_HEIGHT_FRACTION, front]),
    )


def depth_image(dist: np.ndarray, dirs: np.ndarray, w: int = DEPTH_W, h: int = DEPTH_H, max_range: float = DEPTH_RANGE) -> np.ndarray:
    depth = np.where(np.isfinite(dist), dist * dirs[:, 2], max_range)
    return depth.reshape(h, w).astype(np.float32)


def lidar_points(dist: np.ndarray, dirs: np.ndarray) -> np.ndarray:
    hit = np.isfinite(dist)
    return (dirs[hit] * dist[hit, None]).astype(np.float32)


def euler(r: np.ndarray) -> tuple[float, float, float]:
    yaw = math.degrees(math.atan2(r[0, 2], r[2, 2]))
    pitch = math.degrees(math.atan2(-r[1, 2], math.hypot(r[1, 0], r[1, 1])))
    roll = math.degrees(math.atan2(r[1, 0], r[1, 1]))
    return yaw % 360.0, pitch, roll


def cam_field(position: np.ndarray, rotation: np.ndarray, hfov: float = HFOV) -> dict:
    yaw, pitch, roll = euler(rotation)
    x, y, z = (float(v) for v in position)
    return {"x": x, "y": y, "z": z, "yaw": yaw, "pitch": pitch, "roll": roll, "hfov": hfov}


class Rig:
    def __init__(self, box_size: tuple[float, float, float]) -> None:
        self.camera, self.lidar = mounts(box_size)
        self.depth_dirs = depth_directions()
        self.lidar_dirs = lidar_directions()

    def depth_rays(self, position: np.ndarray, rotation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        origin = position + rotation @ self.camera
        return np.broadcast_to(origin, self.depth_dirs.shape), self.depth_dirs @ rotation.T

    def lidar_rays(self, position: np.ndarray, rotation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        origin = position + rotation @ self.lidar
        return np.broadcast_to(origin, self.lidar_dirs.shape), self.lidar_dirs @ rotation.T
