"""Run a driver on fixed scenarios and report the comparison metrics.

Usage: python -m sim2d.evaluate [--episodes 20] [--policy expert|model.pt]
"""

from __future__ import annotations

import argparse
import time
from typing import Callable, Dict

import numpy as np

from .env import DriveEnv
from .world import load

Driver = Callable[[DriveEnv, Dict[str, np.ndarray]], int]


def expert_driver(env: DriveEnv, ob) -> int:
    return env.expert_action()


def run(driver: Driver, episodes: int = 20, seed: int = 1000, world_id: str = "default") -> Dict[str, float]:
    world = load(world_id)
    rows = []
    t0 = time.perf_counter()
    ticks = 0
    for ep in range(episodes):
        env = DriveEnv(world, seed=seed + ep)
        ob = env.reset()
        done, info = False, {}
        while not done:
            ob, _, done, info = env.step(driver(env, ob))
        ticks += env.ticks
        rows.append((info["arrived"], info["collided"], env.stops, env.t, env.min_clear))
    a = np.array(rows, dtype=float)
    ok = a[:, 0] == 1
    return {
        "episodes": episodes,
        "success": a[:, 0].mean(),
        "collision": a[:, 1].mean(),
        "stops_per_ep": a[:, 2].mean(),
        "time_to_goal_s": a[ok, 3].mean() if ok.any() else float("nan"),
        "near_miss_rate": (a[:, 4] < 2.0).mean(),
        "ticks_per_s": ticks / (time.perf_counter() - t0),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=20)
    p.add_argument("--policy", default="expert")
    args = p.parse_args()
    if args.policy == "expert":
        driver = expert_driver
    else:
        from .model import load_driver

        driver = load_driver(args.policy)
    for k, v in run(driver, args.episodes).items():
        print("%-16s %s" % (k, "%.3f" % v if isinstance(v, float) else v))


if __name__ == "__main__":
    main()
