import logging
import os
from functools import cache
from pathlib import Path

from .driver import (
    LIDAR_HEIGHT,
    REPLAN_AFTER,
    Decision,
    Scorer,
    drive,
    plan_route,
    route_blocked,
    static_obstacles,
)
from .follow import follow, reset
from .navmap import NavMap, load

log = logging.getLogger(__name__)

DEFAULT_WORLD_DIR = Path(__file__).resolve().parents[3] / "data" / "worlds" / "quarry"

__all__ = [
    "REPLAN_AFTER",
    "Decision",
    "NavMap",
    "drive",
    "follow",
    "lidar_height",
    "nav_map",
    "plan_route",
    "reset",
    "route_blocked",
    "scorer",
    "static_obstacles",
]


@cache
def nav_map() -> NavMap | None:
    world_dir = Path(os.environ.get("WORLD_DIR", DEFAULT_WORLD_DIR))
    if not (world_dir / "navgrid.json").is_file():
        log.warning("no nav grid in %s; trucks drive straight to goals", world_dir)
        return None
    return load(world_dir)


@cache
def scorer() -> Scorer | None:
    ckpt = os.environ.get("PLANNER_CKPT")
    if not ckpt:
        return None
    from .scorer import load_scorer

    log.info("driving with learned scorer %s", ckpt)
    return load_scorer(ckpt)


def lidar_height() -> float:
    return float(os.environ.get("LIDAR_HEIGHT", LIDAR_HEIGHT))
