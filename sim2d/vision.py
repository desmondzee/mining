"""Vision student: a ResNet-18 over stacked top-down frames (sim2d/render.py) scores the same 44 options.

The student sees images, the route and its own speed, but no lidar rays. The safety mask still comes from the
deploy-time sensor (SIM2D_MOVERS). Train it on datagen --images data, usually with --label privileged.

Usage: python -m sim2d.vision --data data/s2.npz --out runs/s2/vision.pt [--stack 3] [--epochs 10]
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np
import torch
import torchvision
from torch import nn

from .env import CAND_DIM, HOLD_ACTION, N_RAYS, OBS_DIM
from .model import STACK_GAP, stack_index
from .render import render

VEC_DIM = OBS_DIM - N_RAYS  # route points + state


class VisionScorer(nn.Module):
    def __init__(self, width: int = 512, stack: int = 3):
        super().__init__()
        self.config = {"kind": "vision", "width": width, "stack": stack}
        self.stack = stack
        net = torchvision.models.resnet18(num_classes=width)
        # 64 px inputs: a 3x3 stride-1 stem keeps enough resolution for 1 m movers.
        net.conv1 = nn.Conv2d(3 * stack, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.image = net
        self.scene = nn.Sequential(nn.Linear(width + VEC_DIM, width), nn.ReLU())
        self.path = nn.Sequential(nn.Linear(CAND_DIM, width // 2), nn.ReLU(), nn.Linear(width // 2, width // 2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(width + width // 2, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, imgs: torch.Tensor, vec: torch.Tensor, cands: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """imgs (B, 3*stack, H, W) uint8, newest frame first; vec (B, VEC_DIM); cands (B, C, CAND_DIM) -> (B, C)."""
        s = self.scene(torch.cat([self.image(imgs.float() / 255.0), vec], dim=1))
        s = s.unsqueeze(1).expand(-1, cands.shape[1], -1)
        logits = self.head(torch.cat([s, self.path(cands)], dim=-1)).squeeze(-1)
        any_safe = mask.any(dim=1, keepdim=True)
        return torch.where(mask | ~any_safe, logits, torch.full_like(logits, -1e9))


def load_vision_driver(path: str, device: str = "cpu"):
    ckpt = torch.load(path, map_location="cpu")
    cfg = {k: v for k, v in ckpt["config"].items() if k != "kind"}
    model = VisionScorer(**cfg)
    model.load_state_dict(ckpt["state"])
    model = model.to(device).eval()
    hist = []

    @torch.no_grad()
    def drive(env, ob) -> int:
        if env.ticks == 0:
            hist.clear()
        hist.append(render(env))
        if not ob["mask"].any():
            return HOLD_ACTION
        imgs = np.concatenate([hist[max(0, len(hist) - 1 - k * STACK_GAP)] for k in range(model.stack)])
        logits = model(
            torch.from_numpy(imgs)[None].to(device),
            torch.from_numpy(ob["obs"][N_RAYS:])[None].to(device),
            torch.from_numpy(np.asarray(ob["cands"], dtype=np.float32))[None].to(device),
            torch.from_numpy(ob["mask"])[None].to(device),
        )
        return int(logits.argmax(dim=1))

    return drive


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--stack", type=int, default=3)
    p.add_argument("--width", type=int, default=512)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    d = np.load(args.data)
    imgs = torch.from_numpy(d["imgs"])  # (N, 3, H, W) uint8, kept on the CPU
    vec = torch.from_numpy(d["obs"][:, N_RAYS:]).float()
    cands = torch.from_numpy(d["cands"].astype(np.float32))
    masks = torch.from_numpy(d["masks"])
    labels = torch.from_numpy(d["labels"]).long()
    trips = torch.from_numpy(d["trips"]).long()
    frames = torch.from_numpy(stack_index(d["trips"], args.stack))
    gen = torch.Generator().manual_seed(0)
    n_trips = int(trips.max()) + 1
    held = torch.zeros(n_trips, dtype=torch.bool)
    held[torch.randperm(n_trips, generator=gen)[: max(1, n_trips // 10)]] = True
    val, tr = torch.nonzero(held[trips]).squeeze(1), torch.nonzero(~held[trips]).squeeze(1)

    model = VisionScorer(width=args.width, stack=args.stack).to(device)
    n_params = sum(q.numel() for q in model.parameters())
    print("samples %d (val %d), params %.2fM, device %s" % (len(labels), len(val), n_params / 1e6, device), flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    loss_fn = nn.CrossEntropyLoss()

    def batches(idx, shuffle):
        if shuffle:
            idx = idx[torch.randperm(len(idx))]
        for i in range(0, len(idx), args.batch):
            b = idx[i : i + args.batch]
            x = imgs[frames[b]].flatten(1, 2)  # (B, 3*stack, H, W)
            yield (
                x.to(device, non_blocking=True), vec[b].to(device), cands[b].to(device), masks[b].to(device),
                labels[b].to(device),
            )

    for epoch in range(args.epochs):
        t0 = time.perf_counter()
        model.train()
        total = 0.0
        for x, v, c, m, y in batches(tr, True):
            loss = loss_fn(model(x, v, c, m), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * len(y)
        sched.step()
        model.eval()
        correct = 0
        with torch.no_grad():
            for x, v, c, m, y in batches(val, False):
                correct += (model(x, v, c, m).argmax(dim=1) == y).sum().item()
        print(
            "epoch %2d  train loss %.3f  val acc %.3f  %.1fs"
            % (epoch, total / len(tr), correct / len(val), time.perf_counter() - t0),
            flush=True,
        )

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    torch.save({"config": model.config, "state": model.cpu().state_dict()}, args.out)
    print("saved " + args.out)


if __name__ == "__main__":
    main()
