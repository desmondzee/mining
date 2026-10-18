"""Training B: PPO over the 44 candidate paths, optionally warm-started from Training A.

Usage: python -m sim2d.train_ppo --init runs/bc.pt --out runs/ppo.pt [--envs 16] [--total-steps 1000000]
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import time

import numpy as np
import torch
from torch import nn

from .env import HOLD_ACTION, OBS_DIM, check_train_seeds, make_env
from .model import PathScorer, load_model, save


def _fix_mask(ob):
    # No safe candidate: the only legal action is to hold.
    if not ob["mask"].any():
        ob["mask"] = ob["mask"].copy()
        ob["mask"][HOLD_ACTION] = True
    return ob


def worker(conn, seed: int) -> None:
    env = make_env(seed)
    ob = _fix_mask(env.reset())
    ep_ret = 0.0
    while True:
        cmd, arg = conn.recv()
        if cmd == "close":
            break
        if cmd == "obs":
            conn.send(ob)
            continue
        ob, r, done, info = env.step(int(arg))
        ep_ret += r
        stats = None
        if done:
            stats = (ep_ret, info["arrived"], info["collided"], env.min_clear < 2.0, env.stops)
            ob, ep_ret = env.reset(), 0.0
        ob = _fix_mask(ob)
        conn.send((ob, r, done, stats))


class Value(nn.Module):
    def __init__(self, width: int = 256):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(OBS_DIM, width), nn.ReLU(), nn.Linear(width, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.net(obs).squeeze(-1)


def stack(obs_list):
    return (
        np.stack([o["obs"] for o in obs_list]).astype(np.float32),
        np.stack([o["cands"] for o in obs_list]).astype(np.float32),
        np.stack([o["mask"] for o in obs_list]),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--init", default=None, help="Training A checkpoint to warm-start the actor")
    p.add_argument("--out", default="runs/ppo.pt")
    p.add_argument("--envs", type=int, default=16)
    p.add_argument("--steps", type=int, default=128, help="steps per env per update")
    p.add_argument("--total-steps", type=int, default=1_000_000)
    p.add_argument("--width", type=int, default=256)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--epochs", type=int, default=4)
    p.add_argument("--minibatch", type=int, default=1024)
    p.add_argument("--gamma", type=float, default=0.99)
    p.add_argument("--lam", type=float, default=0.95)
    p.add_argument("--clip", type=float, default=0.2)
    p.add_argument("--ent", type=float, default=0.01)
    p.add_argument("--vf", type=float, default=0.5)
    p.add_argument("--bc-coef", type=float, default=0.0, help="weight of KL(init policy || actor); keeps PPO near Training A")
    p.add_argument("--save-every", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = p.parse_args()
    torch.manual_seed(args.seed)

    actor = load_model(args.init).train() if args.init else PathScorer(width=args.width)
    actor.to(args.device)
    anchor = None
    if args.init and args.bc_coef > 0:
        anchor = load_model(args.init).eval().to(args.device)
        for q in anchor.parameters():
            q.requires_grad_(False)
    value = Value(actor.config["width"]).to(args.device)
    opt = torch.optim.Adam(list(actor.parameters()) + list(value.parameters()), lr=args.lr)

    check_train_seeds([args.seed * 1000 + i for i in range(args.envs)])
    ctx = mp.get_context("spawn")
    pipes, procs = [], []
    for i in range(args.envs):
        a, b = ctx.Pipe()
        pr = ctx.Process(target=worker, args=(b, args.seed * 1000 + i), daemon=True)
        pr.start()
        pipes.append(a)
        procs.append(pr)
    for c in pipes:
        c.send(("obs", None))
    obs = [c.recv() for c in pipes]

    E, T = args.envs, args.steps
    n_updates = max(1, args.total_steps // (E * T))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    print("envs %d, steps/update %d, updates %d, device %s, init %s" % (E, E * T, n_updates, args.device, args.init))

    for update in range(1, n_updates + 1):
        t0 = time.perf_counter()
        buf_o, buf_c, buf_m = [], [], []
        buf_a = np.zeros((T, E), dtype=np.int64)
        buf_lp = np.zeros((T, E), dtype=np.float32)
        buf_v = np.zeros((T + 1, E), dtype=np.float32)
        buf_r = np.zeros((T, E), dtype=np.float32)
        buf_d = np.zeros((T, E), dtype=np.float32)
        finished = []
        actor.eval()
        for t in range(T):
            o, c, m = stack(obs)
            with torch.no_grad():
                ot, ct, mt = (torch.from_numpy(x).to(args.device) for x in (o, c, m))
                dist = torch.distributions.Categorical(logits=actor(ot, ct, mt))
                act = dist.sample()
                buf_lp[t] = dist.log_prob(act).cpu().numpy()
                buf_v[t] = value(ot).cpu().numpy()
            buf_a[t] = act.cpu().numpy()
            buf_o.append(o)
            buf_c.append(c)
            buf_m.append(m)
            for c_, a_ in zip(pipes, buf_a[t]):
                c_.send(("step", int(a_)))
            for i, c_ in enumerate(pipes):
                ob, r, done, stats = c_.recv()
                obs[i] = ob
                buf_r[t, i] = r
                buf_d[t, i] = float(done)
                if stats:
                    finished.append(stats)
        with torch.no_grad():
            buf_v[T] = value(torch.from_numpy(stack(obs)[0]).to(args.device)).cpu().numpy()

        adv = np.zeros((T, E), dtype=np.float32)
        last = np.zeros(E, dtype=np.float32)
        for t in reversed(range(T)):
            nonterm = 1.0 - buf_d[t]
            delta = buf_r[t] + args.gamma * buf_v[t + 1] * nonterm - buf_v[t]
            last = delta + args.gamma * args.lam * nonterm * last
            adv[t] = last
        ret = adv + buf_v[:T]

        O = torch.from_numpy(np.concatenate(buf_o)).to(args.device)
        C = torch.from_numpy(np.concatenate(buf_c)).to(args.device)
        M = torch.from_numpy(np.concatenate(buf_m)).to(args.device)
        A = torch.from_numpy(buf_a.reshape(-1)).to(args.device)
        LP = torch.from_numpy(buf_lp.reshape(-1)).to(args.device)
        ADV = torch.from_numpy(adv.reshape(-1)).to(args.device)
        RET = torch.from_numpy(ret.reshape(-1)).to(args.device)
        ADV = (ADV - ADV.mean()) / (ADV.std() + 1e-8)

        actor.train()
        n = len(A)
        for _ in range(args.epochs):
            perm = torch.randperm(n, device=args.device)
            for i in range(0, n, args.minibatch):
                b = perm[i : i + args.minibatch]
                dist = torch.distributions.Categorical(logits=actor(O[b], C[b], M[b]))
                ratio = torch.exp(dist.log_prob(A[b]) - LP[b])
                pg = -torch.min(ratio * ADV[b], torch.clamp(ratio, 1 - args.clip, 1 + args.clip) * ADV[b]).mean()
                vloss = ((value(O[b]) - RET[b]) ** 2).mean()
                loss = pg + args.vf * vloss - args.ent * dist.entropy().mean()
                if anchor is not None:
                    with torch.no_grad():
                        ref = torch.distributions.Categorical(logits=anchor(O[b], C[b], M[b]))
                    loss = loss + args.bc_coef * torch.distributions.kl_divergence(ref, dist).mean()
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(list(actor.parameters()) + list(value.parameters()), 0.5)
                opt.step()

        sps = E * T / (time.perf_counter() - t0)
        if finished:
            f = np.array(finished, dtype=float)
            print(
                "update %4d  steps %8d  episodes %3d  return %7.2f  success %.2f  collision %.2f  timeout %.2f  near-miss %.2f  stops %.2f  %5.0f steps/s"
                % (update, update * E * T, len(f), f[:, 0].mean(), f[:, 1].mean(), f[:, 2].mean(), 1 - f[:, 1].mean() - f[:, 2].mean(), f[:, 3].mean(), f[:, 4].mean(), sps)
            )
        else:
            print("update %4d  steps %8d  no finished episodes  %5.0f steps/s" % (update, update * E * T, sps))
        if update % args.save_every == 0 or update == n_updates:
            save(actor.to("cpu"), args.out)
            actor.to(args.device)

    for c in pipes:
        c.send(("close", None))
    for pr in procs:
        pr.join(timeout=5)
    print("saved " + args.out)


if __name__ == "__main__":
    main()
