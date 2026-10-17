"""Benchmark grid A* on procedural mine maps.

Usage: python -m planner.bench_astar [--maps 10] [--queries 5] [--plot out.png]
"""

from __future__ import annotations

import argparse
import math
import time

import numpy as np
from scipy.ndimage import label

from .astar import astar
from .maps import generate

MIN_TURN_RADIUS = 15.0  # m, large haul truck
SEGMENT = 5.0  # m, resample step for the turn check


def path_length(path) -> float:
    p = np.asarray(path, dtype=float)
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def max_turn_rate(path) -> float:
    """Largest heading change per metre after resampling every SEGMENT metres."""
    p = np.asarray(path, dtype=float)
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    if s[-1] < 3 * SEGMENT:
        return 0.0
    q = np.arange(0.0, s[-1], SEGMENT)
    pts = np.stack([np.interp(q, s, p[:, 0]), np.interp(q, s, p[:, 1])], axis=1)
    d = np.diff(pts, axis=0)
    heading = np.arctan2(d[:, 0], d[:, 1])
    dh = np.abs((np.diff(heading) + math.pi) % (2 * math.pi) - math.pi)
    return float(dh.max() / SEGMENT)


def sample_query(m, rng, min_dist):
    """Pick start and goal in the largest connected free region."""
    labels, _ = label(np.isfinite(m.cost), structure=np.ones((3, 3)))
    biggest = np.bincount(labels.ravel())[1:].argmax() + 1
    free = np.argwhere(labels == biggest)
    while True:
        a, b = free[rng.integers(len(free), size=2)]
        if np.hypot(*(a - b)) >= min_dist:
            return tuple(int(v) for v in a), tuple(int(v) for v in b)


def bench(size: int, n_maps: int, n_queries: int):
    rng = np.random.default_rng(size)
    times, ratios, feasible, found, total = [], [], 0, 0, 0
    for seed in range(n_maps):
        m = generate(size=size, seed=seed)
        for _ in range(n_queries):
            start, goal = sample_query(m, rng, size / 2)
            t0 = time.perf_counter()
            path = astar(m.cost, start, goal)
            times.append((time.perf_counter() - t0) * 1000)
            total += 1
            if path is None:
                continue
            found += 1
            ratios.append(path_length(path) / math.dist(start, goal))
            if max_turn_rate(path) <= 1.0 / MIN_TURN_RADIUS:
                feasible += 1
    return {
        "size": size,
        "queries": total,
        "found": found / total,
        "ms_median": float(np.median(times)),
        "ms_p95": float(np.percentile(times, 95)),
        "len_ratio": float(np.median(ratios)) if ratios else float("nan"),
        "turn_ok": feasible / max(found, 1),
    }


def plot(out: str, size: int = 256) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    m = generate(size=size, seed=0)
    rng = np.random.default_rng(1)
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(m.height, cmap="terrain", origin="lower")
    blocked = np.where(np.isfinite(m.cost), np.nan, 1.0)
    ax.imshow(blocked, cmap="Greys", vmin=0, vmax=1.5, origin="lower", alpha=0.8)
    ax.imshow(np.where(m.road, 1.0, np.nan), cmap="autumn", origin="lower", alpha=0.35)
    for color in ("red", "blue", "magenta"):
        start, goal = sample_query(m, rng, size / 2)
        path = astar(m.cost, start, goal)
        if path:
            p = np.asarray(path)
            ax.plot(p[:, 1], p[:, 0], color=color, linewidth=2)
            ax.plot([start[1]], [start[0]], "o", color=color)
            ax.plot([goal[1]], [goal[0]], "*", color=color, markersize=14)
    ax.set_title("A* on a %dx%d m map (grey = blocked, orange = road)" % (size, size))
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    print("plot: " + out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--maps", type=int, default=10)
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--sizes", type=int, nargs="+", default=[128, 256, 512])
    parser.add_argument("--plot")
    args = parser.parse_args()
    print("%5s %7s %6s %9s %7s %9s %8s" % ("size", "queries", "found", "ms_median", "ms_p95", "len/line", "turn_ok"))
    for size in args.sizes:
        r = bench(size, args.maps, args.queries)
        print(
            "%5d %7d %5.0f%% %9.1f %7.1f %9.2f %7.0f%%"
            % (r["size"], r["queries"], 100 * r["found"], r["ms_median"], r["ms_p95"], r["len_ratio"], 100 * r["turn_ok"])
        )
    if args.plot:
        plot(args.plot)


if __name__ == "__main__":
    main()
