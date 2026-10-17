"""Generate Training A data: (observation, candidates, safe mask, expert choice).

The truck sometimes drives a random safe candidate instead of the expert's
choice, but the label is always the expert's choice. This shows the model how
to recover from its own mistakes (the ChauffeurNet / DAgger idea).

Usage: python -m sim2d.datagen --episodes 200 --out data/bc.npz [--workers 8]
"""

from __future__ import annotations

import argparse
import os
from multiprocessing import Pool

import numpy as np

from .env import DriveEnv
from .world import load


def episode(args):
    seed, explore = args
    env = DriveEnv(load("default"), seed=seed)
    rng = np.random.default_rng(seed + 1)
    ob = env.reset()
    obs, cands, masks, labels = [], [], [], []
    done = False
    while not done:
        label = env.expert_action()
        obs.append(ob["obs"])
        cands.append(ob["cands"])
        masks.append(ob["mask"])
        labels.append(label)
        act = label
        if rng.random() < explore and ob["mask"].any():
            act = int(rng.choice(np.flatnonzero(ob["mask"])))
        ob, _, done, _ = env.step(act)
    return np.array(obs), np.array(cands), np.array(masks), np.array(labels)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=200)
    p.add_argument("--explore", type=float, default=0.2)
    p.add_argument("--workers", type=int, default=os.cpu_count())
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="data/bc.npz")
    args = p.parse_args()
    jobs = [(args.seed + i, args.explore) for i in range(args.episodes)]
    with Pool(args.workers) as pool:
        parts = pool.map(episode, jobs)
    obs, cands, masks, labels = (np.concatenate(x) for x in zip(*parts))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.savez_compressed(args.out, obs=obs, cands=cands.astype(np.float16), masks=masks, labels=labels)
    print("saved %d samples from %d episodes to %s" % (len(labels), args.episodes, args.out))


if __name__ == "__main__":
    main()
