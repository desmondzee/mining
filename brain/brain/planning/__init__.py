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
    "learned_scorer",
    "lidar_height",
    "nav_map",
    "plan_route",
    "reset",
    "route_blocked",
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
def learned_scorer() -> Scorer | None:
    ckpt = os.environ.get("PLANNER_CHECKPOINT") or os.environ.get("PLANNER_CKPT")
    if not ckpt:
        return None
    path = Path(ckpt)
    if path.suffix == ".pt" and path.with_suffix(".npz").is_file():
        path = path.with_suffix(".npz")
    if not path.is_file():
        log.warning("planner checkpoint %s not found; driving with the rule-based scorer", ckpt)
        return None
    from .scorer import load_scorer

    try:
        if path.suffix == ".pt":
            from .export_scorer import export

            export(path, path.with_suffix(".npz"))
            path = path.with_suffix(".npz")
        model = load_scorer(path)
    except Exception as e:
        log.warning("cannot load planner checkpoint %s (%s); driving with the rule-based scorer", path, e)
        return None
    log.info("driving with learned scorer %s %s", path, model.config)
    return model


def lidar_height() -> float:
    return float(os.environ.get("LIDAR_HEIGHT", LIDAR_HEIGHT))
