"""Convert a sim2d PathScorer checkpoint (.pt) to the numpy .npz the brain loads.

Usage (needs torch): cd brain && uv run --extra model python -m brain.planning.export_scorer ../models/ppo.pt [../models/ppo.npz]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from .driver import CAND_DIM, OBS_DIM
from .learned import CheckpointError, NumpyScorer


def state_dict(ckpt) -> tuple[dict, dict]:
    config = {}
    if isinstance(ckpt, dict):
        config = dict(ckpt.get("config") or {})
        for key in ("state", "state_dict", "actor", "model"):
            if isinstance(ckpt.get(key), dict):
                ckpt = ckpt[key]
                break
    if hasattr(ckpt, "state_dict"):
        ckpt = ckpt.state_dict()
    weights = {}
    for k, v in ckpt.items():
        if hasattr(v, "detach"):
            name = k.removeprefix("module.").removeprefix("_orig_mod.")
            weights[name] = v.detach().cpu().float().numpy()
    return weights, config


def export(src: Path, dst: Path) -> dict:
    import torch

    weights, config = state_dict(torch.load(src, map_location="cpu", weights_only=False))
    NumpyScorer(weights, config)
    width = int(weights["scene.0.weight"].shape[0])
    if config.get("width", width) != width:
        raise CheckpointError(f"config width {config['width']} != scene layer width {width}")
    config.update(
        width=width,
        obs_dim=OBS_DIM,
        cand_dim=CAND_DIM,
        source=src.name,
        sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
    )
    keep = {k: v for k, v in weights.items() if k.split(".")[0] in ("scene", "path", "head")}
    dst.parent.mkdir(parents=True, exist_ok=True)
    np.savez(dst, config=np.array(json.dumps(config)), **keep)
    return config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("checkpoint", type=Path)
    p.add_argument("out", type=Path, nargs="?")
    args = p.parse_args()
    out = args.out or args.checkpoint.with_suffix(".npz")
    try:
        config = export(args.checkpoint, out)
    except CheckpointError as e:
        sys.exit(f"{args.checkpoint}: {e}")
    print(f"wrote {out} {json.dumps(config)}")


if __name__ == "__main__":
    main()
