"""Qwen3.5-0.8B student: the VLM reads stacked top-down frames, LoRA-tuned, and scores the same 44 options.

Same data, labels and trip split as sim2d/vision.py. The last token's hidden state, the route and the speed feed
the candidate head; the safety mask still comes from SIM2D_MOVERS. Needs transformers>=5.5 and peft (a separate
environment from the sim2d one).

Train: python -m sim2d.qwen_student train --data data/s2_lidar.npz --model models/Qwen3.5-0.8B --out runs/s3
Test:  python -m sim2d.qwen_student eval --model models/Qwen3.5-0.8B --out runs/s3 --episodes 200
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np
import torch
from torch import nn

from .env import CAND_DIM, HOLD_ACTION, N_RAYS, OBS_DIM, make_env
from .model import STACK_GAP, stack_index
from .render import render

VEC_DIM = OBS_DIM - N_RAYS
PROMPT = (
    "Top-down views around a haul truck, now, 0.3 s ago and 0.6 s ago. Green: drivable ground. "
    "Red: vehicles and people. Blue: the route. Choose a safe path."
)
IMAGE_PIXELS = 128 * 128  # 16 tokens per frame after the 2x2 merge


class Head(nn.Module):
    def __init__(self, feat: int, width: int = 512):
        super().__init__()
        self.scene = nn.Sequential(nn.Linear(feat + VEC_DIM, width), nn.ReLU())
        self.path = nn.Sequential(nn.Linear(CAND_DIM, width // 2), nn.ReLU(), nn.Linear(width // 2, width // 2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(width + width // 2, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, feat, vec, cands, mask):
        s = self.scene(torch.cat([feat, vec], dim=1)).unsqueeze(1).expand(-1, cands.shape[1], -1)
        logits = self.head(torch.cat([s, self.path(cands)], dim=-1)).squeeze(-1)
        any_safe = mask.any(dim=1, keepdim=True)
        return torch.where(mask | ~any_safe, logits, torch.full_like(logits, -1e9))


def build(model_dir: str, adapter: str = None, targets=("q_proj", "v_proj")):
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForImageTextToText, AutoProcessor

    proc = AutoProcessor.from_pretrained(model_dir)
    proc.image_processor.size = {"shortest_edge": IMAGE_PIXELS, "longest_edge": IMAGE_PIXELS}
    vlm = AutoModelForImageTextToText.from_pretrained(model_dir, dtype=torch.bfloat16)
    if adapter:
        vlm = PeftModel.from_pretrained(vlm, adapter)
    else:
        vlm = get_peft_model(vlm, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules=list(targets)))
    msgs = [{"role": "user", "content": [{"type": "image"}] * 3 + [{"type": "text", "text": PROMPT}]}]
    text = proc.apply_chat_template(msgs, add_generation_prompt=False, tokenize=False)
    return proc, vlm, text


def features(proc, vlm, text, frames: np.ndarray, device, pool: str = "last") -> torch.Tensor:
    """frames (B, 3 stacked, 3, H, W) uint8 -> (B, hidden) float: the last token's final hidden state, or the mean."""
    images = [[f.transpose(1, 2, 0) for f in sample] for sample in frames]
    batch = proc(text=[text] * len(images), images=images, return_tensors="pt", padding=True).to(device)
    h = vlm(**batch, output_hidden_states=True).hidden_states[-1].float()
    if pool == "mean":
        w = batch["attention_mask"].unsqueeze(-1).float()
        return (h * w).sum(1) / w.sum(1)
    return h[:, -1]


def train(args) -> None:
    device = "cuda"
    d = np.load(args.data)
    trips = d["trips"]
    frames_idx = stack_index(trips, 3)
    rng = np.random.default_rng(0)
    gen = torch.Generator().manual_seed(0)
    n_trips = int(trips.max()) + 1
    held = np.zeros(n_trips, dtype=bool)
    held[torch.randperm(n_trips, generator=gen)[: max(1, n_trips // 10)].numpy()] = True
    lab, msk = d["labels"], d["masks"]
    learnable = msk[np.arange(len(lab)), lab] | ~msk.any(axis=1)
    tr_idx = np.flatnonzero(~held[trips] & learnable)
    val_idx = np.flatnonzero(held[trips] & learnable)
    tr_idx = rng.choice(tr_idx, size=min(args.samples, len(tr_idx)), replace=False)
    val_idx = rng.choice(val_idx, size=min(args.val_samples, len(val_idx)), replace=False)
    imgs, obs, cands, masks, labels = d["imgs"], d["obs"], d["cands"], d["masks"], d["labels"]
    print("train %d, val %d samples" % (len(tr_idx), len(val_idx)), flush=True)

    proc, vlm, text = build(args.model, targets=args.lora_targets.split(","))
    vlm.to(device).train()
    head = Head(vlm.config.get_text_config().hidden_size).to(device)
    opt = torch.optim.AdamW(
        [{"params": [q for q in vlm.parameters() if q.requires_grad], "lr": args.lr_lora},
         {"params": head.parameters(), "lr": args.lr_head}],
        weight_decay=1e-4,
    )
    per_epoch = (len(tr_idx) + args.batch - 1) // args.batch
    steps = per_epoch * args.epochs
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[args.lr_lora, args.lr_head], total_steps=steps, pct_start=0.05)
    loss_fn = nn.CrossEntropyLoss()

    def tensors(b):
        return (
            torch.from_numpy(obs[b, N_RAYS:]).to(device),
            torch.from_numpy(cands[b].astype(np.float32)).to(device),
            torch.from_numpy(masks[b]).to(device),
            torch.from_numpy(labels[b]).long().to(device),
        )

    t0 = time.perf_counter()
    hits = seen = 0
    for step in range(steps):
        if step % per_epoch == 0:
            order = rng.permutation(tr_idx)
        k = step % per_epoch
        b = np.sort(order[k * args.batch : (k + 1) * args.batch])
        v, c, m, y = tensors(b)
        logits = head(features(proc, vlm, text, imgs[frames_idx[b]], device, args.pool), v, c, m)
        loss = loss_fn(logits, y)
        hits += (logits.argmax(1) == y).sum().item()
        seen += len(y)
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if step % 50 == 0:
            print(
                "step %5d/%d  loss %.3f  train acc %.3f  %.0fs"
                % (step, steps, loss.item(), hits / max(seen, 1), time.perf_counter() - t0),
                flush=True,
            )
            hits = seen = 0

    vlm.eval()
    head.eval()
    correct = 0
    with torch.no_grad():
        for i in range(0, len(val_idx), args.batch):
            b = np.sort(val_idx[i : i + args.batch])
            v, c, m, y = tensors(b)
            feat = features(proc, vlm, text, imgs[frames_idx[b]], device, args.pool)
            correct += (head(feat, v, c, m).argmax(1) == y).sum().item()
    print("val acc %.3f" % (correct / len(val_idx)), flush=True)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "pool.txt"), "w") as f:
        f.write(args.pool)
    vlm.save_pretrained(os.path.join(args.out, "lora"))
    torch.save(head.state_dict(), os.path.join(args.out, "head.pt"))
    print("saved " + args.out)


def evaluate(args) -> None:
    """Run args.envs trips in lockstep so each tick is one batched VLM call; same metrics as sim2d.evaluate."""
    device = "cuda"
    proc, vlm, text = build(args.model, os.path.join(args.out, "lora"))
    vlm.to(device).eval()
    head = Head(vlm.config.get_text_config().hidden_size).to(device).eval()
    head.load_state_dict(torch.load(os.path.join(args.out, "head.pt"), map_location=device))
    pool_file = os.path.join(args.out, "pool.txt")
    pool = open(pool_file).read().strip() if os.path.exists(pool_file) else "last"
    rows, next_ep, live = [], 0, []
    t0 = time.perf_counter()

    def start():
        nonlocal next_ep
        env = make_env(args.seed + next_ep, args.worlds, args.movers)
        next_ep += 1
        return {"env": env, "ob": env.reset(), "hist": []}

    while next_ep < min(args.envs, args.episodes):
        live.append(start())
    while live:
        for s in live:
            s["hist"].append(render(s["env"]))
        need = [s for s in live if s["ob"]["mask"].any()]
        acts = {}
        if need:
            frames = np.stack([
                np.stack([s["hist"][max(0, len(s["hist"]) - 1 - k * STACK_GAP)] for k in range(3)]) for s in need
            ])
            with torch.no_grad():
                logits = head(
                    features(proc, vlm, text, frames, device, pool),
                    torch.from_numpy(np.stack([s["ob"]["obs"][N_RAYS:] for s in need])).to(device),
                    torch.from_numpy(np.stack([s["ob"]["cands"] for s in need]).astype(np.float32)).to(device),
                    torch.from_numpy(np.stack([s["ob"]["mask"] for s in need])).to(device),
                )
            for s, a in zip(need, logits.argmax(1).tolist()):
                acts[id(s)] = a
        keep = []
        for s in live:
            env = s["env"]
            s["ob"], _, done, info = env.step(acts.get(id(s), HOLD_ACTION))
            if done:
                rows.append((info["arrived"], info["collided"], env.stops, env.t, env.min_clear, info["off_road"]))
                if next_ep < args.episodes:
                    keep.append(start())
            else:
                keep.append(s)
        live = keep
    a = np.array(rows, dtype=float)
    ok = a[:, 0] == 1
    print("%-16s %s" % ("movers", args.movers))
    for k, v in {
        "episodes": len(rows),
        "success": a[:, 0].mean(),
        "collision": a[:, 1].mean(),
        "off_road": a[:, 5].mean(),
        "timeout": ((a[:, 0] == 0) & (a[:, 1] == 0)).mean(),
        "time_to_goal_s": a[ok, 3].mean() if ok.any() else float("nan"),
        "near_miss_rate": (a[:, 4] < 2.0).mean(),
        "wall_s": time.perf_counter() - t0,
    }.items():
        print("%-16s %s" % (k, "%.3f" % v if isinstance(v, float) else v))


def main() -> None:
    from .env import DEFAULT_MOVERS, DEFAULT_WORLDS
    from .evaluate import EVAL_SEED

    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("train", "eval"))
    p.add_argument("--model", required=True, help="local Qwen3.5-0.8B folder")
    p.add_argument("--out", required=True)
    p.add_argument("--data")
    p.add_argument("--samples", type=int, default=100_000)
    p.add_argument("--val-samples", type=int, default=5_000)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--pool", default="last", choices=("last", "mean"))
    p.add_argument("--lora-targets", default="q_proj,v_proj")
    p.add_argument("--lr-lora", type=float, default=2e-4)
    p.add_argument("--lr-head", type=float, default=1e-3)
    p.add_argument("--episodes", type=int, default=200)
    p.add_argument("--envs", type=int, default=32)
    p.add_argument("--seed", type=int, default=EVAL_SEED)
    p.add_argument("--worlds", default=DEFAULT_WORLDS)
    p.add_argument("--movers", default=DEFAULT_MOVERS)
    args = p.parse_args()
    torch.manual_seed(0)
    train(args) if args.mode == "train" else evaluate(args)


if __name__ == "__main__":
    main()
