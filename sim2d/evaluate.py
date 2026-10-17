"""Run a driver on fixed scenarios and report the comparison metrics.

Usage: python -m sim2d.evaluate [--episodes 20] [--policy expert|model.pt|model.npz] [--seed 900000]

Evaluation seeds start at EVAL_SEED, far from the training seeds: datagen uses
--seed + 0..episodes-1 (bc_v1/bc_v2: 0-1999, bc_v3: 50000-55999) and PPO
workers use --seed * 1000 + 0..envs-1.
"""

from __future__ import annotations

import argparse
import time
from typing import Callable, Dict

import numpy as np

from .env import DEFAULT_WORLDS, HOLD_ACTION, DriveEnv, make_env

Driver = Callable[[DriveEnv, Dict[str, np.ndarray]], int]

EVAL_SEED = 900000


def expert_driver(env: DriveEnv, ob) -> int:
    return env.expert_action()


def npz_driver(path: str) -> Driver:
    """The brain's numpy scorer (brain/brain/planning/learned.py) on an exported checkpoint."""
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
    from brain.planning.learned import load_scorer

    scorer = load_scorer(path)

    def drive(env: DriveEnv, ob) -> int:
        if not ob["mask"].any():
            return HOLD_ACTION
        return int(np.argmax(np.where(ob["mask"], scorer(ob["obs"], ob["cands"], ob["mask"]), -np.inf)))

    return drive


def run(driver: Driver, episodes: int = 20, seed: int = EVAL_SEED, worlds: str = DEFAULT_WORLDS) -> Dict[str, float]:
    rows = []
    t0 = time.perf_counter()
    ticks = 0
    for ep in range(episodes):
        env = make_env(seed + ep, worlds)
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
    p.add_argument("--worlds", default=DEFAULT_WORLDS)
    p.add_argument("--seed", type=int, default=EVAL_SEED, help="first episode seed; use 1000 to reproduce the 2026-10-17 rows")
    args = p.parse_args()
    if args.policy == "expert":
        driver = expert_driver
    elif args.policy.endswith(".npz"):
        driver = npz_driver(args.policy)
    else:
        from .model import load_driver

        driver = load_driver(args.policy)
    print("%-16s %d-%d" % ("seeds", args.seed, args.seed + args.episodes - 1))
    for k, v in run(driver, args.episodes, args.seed, args.worlds).items():
        print("%-16s %s" % (k, "%.3f" % v if isinstance(v, float) else v))


if __name__ == "__main__":
    main()
