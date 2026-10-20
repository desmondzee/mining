"""Front camera view for sim2d: what a camera on the front of the truck sees, drawn in flat colours.

Each image column casts one ray over the nav grid (walls) and the movers (upright cylinders), as
brain/brain/fakesim.py does, and each ground pixel is projected back onto the map to paint the route.
Unlike render.py it has perspective, occlusion and a 90 deg field of view, and it shows no velocity;
it is still noise-free, so it equals a perfect segmentation of a front camera.

The image holds one class id per pixel (uint8, H x W), which keeps datasets small; COLOURS turns it into RGB.
"""

from __future__ import annotations

import math

import numpy as np

from .env import TRUCK_R, DriveEnv

H, W = 64, 128
HFOV = math.radians(90.0)
FOCAL = (W / 2) / math.tan(HFOV / 2)  # px; square pixels, so about 53 deg vertical
HORIZON = H / 2
CAM_HEIGHT = 3.6  # m, as brain/brain/fakesim.py
CAM_FWD = TRUCK_R + 0.2  # m ahead of the truck centre, as the Unity and Isaac front cameras
MAX_RANGE = 100.0
STEP = 0.5  # m between wall samples along a ray; a nav cell is about 2 m
WALL_HEIGHT = 6.0
VEHICLE_HEIGHT = 3.0
PED_HEIGHT = 1.8
PED_DRAW_R = 0.4  # m; a 0.3 m pedestrian is under 1 px wide beyond about 40 m
ROUTE_HALF_W = 1.5  # m of painted route either side of the centreline
ROUTE_AHEAD = 50  # route samples painted, 2 m apart

SKY, GROUND, ROUTE, WALL, VEHICLE, PERSON = range(6)
COLOURS = np.array(
    [[150, 190, 235], [150, 125, 90], [60, 120, 220], [70, 60, 50], [140, 90, 200], [230, 80, 150]], dtype=np.uint8
)

_COLS = np.arange(W) + 0.5
_OFF = np.arctan((_COLS - W / 2) / FOCAL)  # (W,) ray angle from the optical axis
_COS = np.cos(_OFF)
_ROWS = np.arange(H)[:, None] + 0.5
_GROUND_ROWS = np.flatnonzero(_ROWS[:, 0] > HORIZON)
_GROUND_PERP = FOCAL * CAM_HEIGHT / (_ROWS[_GROUND_ROWS, 0] - HORIZON)  # (G,) m along the optical axis
_MARCH = np.arange(STEP, MAX_RANGE + STEP, STEP)


def front(env: DriveEnv) -> np.ndarray:
    """(H, W) uint8 class ids for the truck's front camera."""
    tr = env.truck
    s, c = math.sin(tr.yaw), math.cos(tr.yaw)
    cx, cz = tr.x + s * CAM_FWD, tr.z + c * CAM_FWD
    ang = tr.yaw + _OFF
    dx, dz = np.sin(ang), np.cos(ang)
    img = np.full((H, W), SKY, dtype=np.uint8)
    img[_GROUND_ROWS] = GROUND

    # Route painted on the ground.
    ni = env._nearest_idx()
    route = env.route[ni : ni + ROUTE_AHEAD]
    along = _GROUND_PERP[:, None] / _COS[None, :]  # (G, W) m from the camera to each ground pixel
    gx, gz = cx + dx[None] * along, cz + dz[None] * along
    d2 = (gx[..., None] - route[:, 0]) ** 2 + (gz[..., None] - route[:, 1]) ** 2
    img[_GROUND_ROWS] = np.where(d2.min(axis=-1) < ROUTE_HALF_W**2, ROUTE, GROUND)

    # Walls: the first blocked cell along each ray.
    hit = env.world.blocked_at(cx + dx[:, None] * _MARCH, cz + dz[:, None] * _MARCH)  # (W, M)
    wall = np.where(hit.any(axis=1), _MARCH[hit.argmax(axis=1)], np.inf) * _COS  # perpendicular distance
    _draw(img, wall, WALL_HEIGHT, WALL)

    # Movers as upright cylinders, far to near, each hidden behind a nearer wall.
    rows = []
    for m in env.movers:
        ox, oz = m.x - cx, m.z - cz
        r = max(m.radius, PED_DRAW_R) if m.pedestrian else m.radius
        proj = dx * ox + dz * oz
        perp2 = ox * ox + oz * oz - proj * proj
        dist = np.where((proj > 0) & (perp2 < r * r), proj - np.sqrt(np.maximum(r * r - perp2, 0.0)), np.inf) * _COS
        if np.isfinite(dist).any() and dist.min() < MAX_RANGE:
            rows.append((float(dist[np.isfinite(dist)].min()), dist, m.pedestrian))
    for _, dist, ped in sorted(rows, key=lambda t: -t[0]):
        _draw(img, np.where(dist < wall, dist, np.inf), PED_HEIGHT if ped else VEHICLE_HEIGHT, PERSON if ped else VEHICLE)
    return img


def _draw(img: np.ndarray, perp: np.ndarray, height: float, cls: int) -> None:
    """Paint an upright surface at perpendicular distance perp (W,) per column, from the ground up to height."""
    ok = np.isfinite(perp) & (perp < MAX_RANGE)
    d = np.where(ok, np.maximum(perp, 0.1), 1.0)
    top = HORIZON - FOCAL * (height - CAM_HEIGHT) / d
    bottom = HORIZON + FOCAL * CAM_HEIGHT / d
    img[ok[None, :] & (_ROWS >= top) & (_ROWS <= bottom)] = cls


def to_rgb(ids: np.ndarray) -> np.ndarray:
    """Class ids (..., H, W) -> RGB (..., 3, H, W) uint8."""
    return np.moveaxis(COLOURS[ids], -1, -3)
