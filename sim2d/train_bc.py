"""Training A: imitation of the baseline's candidate choice.

Usage: python -m sim2d.train_bc --data data/bc.npz --out runs/bc.pt [--epochs 20] [--width 256]
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np
import torch
from torch import nn

from .model import PathScorer, save


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/bc.npz")
    p.add_argument("--out", default="runs/bc.pt")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--width", type=int, default=256)
    p.add_argument("--batch", type=int, default=512)
    p.add_argument("--lr", type=float, default=1e-3)
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    d = np.load(args.data)
    obs = torch.from_numpy(d["obs"]).float()
    cands = torch.from_numpy(d["cands"].astype(np.float32))
    masks = torch.from_numpy(d["masks"])
    labels = torch.from_numpy(d["labels"]).long()
    n = len(labels)
    gen = torch.Generator().manual_seed(0)
    if "trips" in d:
        # Hold out whole trips: ticks of one trip are near-identical, so a tick split leaks.
        trips = torch.from_numpy(d["trips"]).long()
        n_trips = int(trips.max()) + 1
        held = torch.zeros(n_trips, dtype=torch.bool)
        held[torch.randperm(n_trips, generator=gen)[: max(1, n_trips // 10)]] = True
        val, tr = torch.nonzero(held[trips]).squeeze(1), torch.nonzero(~held[trips]).squeeze(1)
    else:
        perm = torch.randperm(n, generator=gen)
        val, tr = perm[: max(1, n // 10)], perm[max(1, n // 10) :]
    n_val = len(val)

    model = PathScorer(width=args.width).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print("samples %d (val %d), params %.2fM, device %s" % (n, n_val, n_params / 1e6, device))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    loss_fn = nn.CrossEntropyLoss()

    def batches(idx, shuffle):
        if shuffle:
            idx = idx[torch.randperm(len(idx))]
        for i in range(0, len(idx), args.batch):
            b = idx[i : i + args.batch]
            yield obs[b].to(device), cands[b].to(device), masks[b].to(device), labels[b].to(device)

    for epoch in range(args.epochs):
        t0 = time.perf_counter()
        model.train()
        total = 0.0
        for o, c, m, y in batches(tr, True):
            loss = loss_fn(model(o, c, m), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * len(y)
        sched.step()
        model.eval()
        correct = 0
        with torch.no_grad():
            for o, c, m, y in batches(val, False):
                correct += (model(o, c, m).argmax(dim=1) == y).sum().item()
        print("epoch %2d  train loss %.3f  val acc %.3f  %.1fs" % (epoch, total / len(tr), correct / n_val, time.perf_counter() - t0))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    save(model.cpu(), args.out)
    print("saved " + args.out)


if __name__ == "__main__":
    main()
