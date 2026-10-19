"""Run a driver on fixed scenarios and report the comparison metrics.

Usage: python -m sim2d.evaluate [--episodes 20] [--policy expert|privileged|random|straight|greedy|model.pt|model.npz] [--seed 900000] [--movers oracle|lidar]

Evaluation seeds start at EVAL_SEED, far from the training seeds: datagen uses
--seed + 0..episodes-1 (bc_v1/bc_v2: 0-1999, bc_v3: 50000-55999) and PPO
workers use --seed * 1000 + 0..envs-1. bc_v1/bc_v2 data contains the 1000+ trips
of the 2026-10-17 rows, so those rows are not clean for bc_v2 and the PPO runs
started from it. env.check_train_seeds now refuses such overlaps.
"""

from __future__ import annotations

import argparse
import time
from typing import Callable, Dict

import numpy as np

from .env import (
    CAND_SPEED, CAND_STEER, DEFAULT_MOVERS, DEFAULT_WORLDS, HOLD_ACTION, HORIZON, MAX_V, MOVER_MODES, ROUTE_STEP, DriveEnv,
    make_env,
)

Driver = Callable[[DriveEnv, Dict[str, np.ndarray]], int]

EVAL_SEED = 900000


def expert_driver(env: DriveEnv, ob) -> int:
    return env.expert_action()


def picker_driver(kind: str) -> Driver:
    """Learning-free pickers over the same safe set, to measure what the route and the mask do alone.

    random:   a random safe option
    straight: the fastest safe option, then the one with the least steering
    greedy:   the safe option whose 3 s end point is nearest the route point MAX_V * HORIZON ahead
    """
    rng = np.random.default_rng(0)
    straight_key = -CAND_SPEED * 10.0 + np.abs(CAND_STEER)
    ahead = int(round(MAX_V * HORIZON / ROUTE_STEP))

    def drive(env: DriveEnv, ob) -> int:
        safe = np.flatnonzero(ob["mask"])
        if not len(safe):
            return HOLD_ACTION
        if kind == "random":
            return int(rng.choice(safe))
        if kind == "straight":
            return int(safe[np.argmin(straight_key[safe])])
        target = env.route[min(env.route_idx + ahead, len(env.route) - 1)]
        ends = env.cand_pts[safe, -1]
        return int(safe[np.argmin(np.hypot(ends[:, 0] - target[0], ends[:, 1] - target[1]))])

    return drive


PICKERS = ("random", "straight", "greedy")


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


def run(
    driver: Driver, episodes: int = 20, seed: int = EVAL_SEED, worlds: str = DEFAULT_WORLDS, movers: str = DEFAULT_MOVERS
) -> Dict[str, float]:
    rows = []
    t0 = time.perf_counter()
    ticks = 0
    for ep in range(episodes):
        env = make_env(seed + ep, worlds, movers)
        ob = env.reset()
        done, info = False, {}
        while not done:
            ob, _, done, info = env.step(driver(env, ob))
        ticks += env.ticks
        rows.append((info["arrived"], info["collided"], env.stops, env.t, env.min_clear, info["off_road"]))
    a = np.array(rows, dtype=float)
    ok = a[:, 0] == 1
    return {
        "episodes": episodes,
        "success": a[:, 0].mean(),
        "collision": a[:, 1].mean(),
        "off_road": a[:, 5].mean(),
        "timeout": ((a[:, 0] == 0) & (a[:, 1] == 0)).mean(),
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
    p.add_argument("--movers", default=DEFAULT_MOVERS, choices=MOVER_MODES, help="what the safety mask knows about movers")
    p.add_argument("--seed", type=int, default=EVAL_SEED, help="first episode seed; use 1000 to reproduce the 2026-10-17 rows")
    args = p.parse_args()
    if args.policy == "expert":
        driver = expert_driver
    elif args.policy == "privileged":
        driver = lambda env, ob: env.privileged_action()  # noqa: E731
    elif args.policy in PICKERS:
        driver = picker_driver(args.policy)
    elif args.policy.endswith(".npz"):
        driver = npz_driver(args.policy)
    else:
        from .model import load_driver

        driver = load_driver(args.policy)
    print("%-16s %d-%d" % ("seeds", args.seed, args.seed + args.episodes - 1))
    print("%-16s %s" % ("movers", args.movers))
    for k, v in run(driver, args.episodes, args.seed, args.worlds, args.movers).items():
        print("%-16s %s" % (k, "%.3f" % v if isinstance(v, float) else v))


if __name__ == "__main__":
    main()
