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

from .env import check_train_seeds, make_env


def episode(args):
    seed, explore, label_by = args
    env = make_env(seed)
    rng = np.random.default_rng(seed + 1)
    ob = env.reset()
    obs, cands, masks, labels = [], [], [], []
    done = False
    while not done:
        label = env.privileged_action() if label_by == "privileged" else env.expert_action()
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
    p.add_argument("--seed", type=int, default=10000)
    p.add_argument("--out", default="data/bc.npz")
    p.add_argument(
        "--label", default="expert", choices=("expert", "privileged"),
        help="privileged: the expert with true mover state labels, while the truck drives with SIM2D_MOVERS",
    )
    args = p.parse_args()
    jobs = [(args.seed + i, args.explore, args.label) for i in range(args.episodes)]
    check_train_seeds([j[0] for j in jobs])
    with Pool(args.workers) as pool:
        parts = pool.map(episode, jobs)
    obs, cands, masks, labels = (np.concatenate(x) for x in zip(*parts))
    trips = np.concatenate([np.full(len(part[3]), i, np.int32) for i, part in enumerate(parts)])
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    # Uncompressed: zlib on ~1M samples is single-threaded and takes minutes.
    np.savez(args.out, obs=obs, cands=cands.astype(np.float16), masks=masks, labels=labels, trips=trips)
    print("saved %d samples from %d episodes to %s" % (len(labels), args.episodes, args.out))


if __name__ == "__main__":
    main()
