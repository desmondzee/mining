"""Procedural mine maps for planner tests.

A stand-in until the terrain workstream exports real maps. Grid cells are
1 m. A map holds a height field, an obstacle mask, a road mask and a cost grid.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter

MAX_SLOPE = 0.15  # rise over run; haul roads are usually under 10%
SLOPE_WEIGHT = 20.0
ROAD_DISCOUNT = 0.5
RELIEF_PER_M = 0.12  # total relief / map width, so slopes do not depend on size


@dataclass
class MineMap:
    height: np.ndarray  # (H, W) metres
    obstacles: np.ndarray  # (H, W) bool
    road: np.ndarray  # (H, W) bool
    cost: np.ndarray  # (H, W) float, np.inf = impassable


def generate(size: int = 256, n_rocks: int = 40, seed: int = 0) -> MineMap:
    rng = np.random.default_rng(seed)
    height = gaussian_filter(rng.normal(size=(size, size)), sigma=size / 12)
    height *= RELIEF_PER_M * size / (np.ptp(height) + 1e-9)

    yy, xx = np.mgrid[0:size, 0:size]
    obstacles = np.zeros((size, size), dtype=bool)
    for _ in range(n_rocks):
        cy, cx = rng.integers(0, size, 2)
        r = rng.uniform(2, 8)
        obstacles |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r

    # One winding haul road across the map.
    road = np.zeros((size, size), dtype=bool)
    xs = np.arange(size)
    ys = size / 2 + 0.25 * size * np.sin(xs / size * 2 * np.pi * rng.uniform(0.5, 1.5) + rng.uniform(0, 6))
    for x, y in zip(xs, ys.astype(int)):
        road[max(0, y - 4) : y + 5, x] = True
    obstacles &= ~road
    height = np.where(road, gaussian_filter(height, sigma=8), height)

    gy, gx = np.gradient(height)
    slope = np.hypot(gx, gy)
    cost = 1.0 + SLOPE_WEIGHT * slope
    cost = np.where(road, cost * ROAD_DISCOUNT, cost)
    cost = np.where(obstacles | (slope > MAX_SLOPE), np.inf, cost)
    return MineMap(height=height, obstacles=obstacles, road=road, cost=cost)
