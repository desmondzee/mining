"""Candidate-path scorer (design 3).

The scene encoder reads lidar + route + state. The candidate encoder reads one
3 s path. The head scores each (scene, path) pair. Unsafe candidates are
masked out, and the truck drives the highest-scoring safe path.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from .env import CAND_DIM, HOLD_ACTION, OBS_DIM


class PathScorer(nn.Module):
    def __init__(self, width: int = 256):
        super().__init__()
        self.config = {"width": width}
        self.scene = nn.Sequential(nn.Linear(OBS_DIM, width), nn.ReLU(), nn.Linear(width, width), nn.ReLU())
        self.path = nn.Sequential(nn.Linear(CAND_DIM, width // 2), nn.ReLU(), nn.Linear(width // 2, width // 2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(width + width // 2, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, obs: torch.Tensor, cands: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """obs (B, OBS_DIM), cands (B, C, CAND_DIM), mask (B, C) bool -> logits (B, C)."""
        s = self.scene(obs).unsqueeze(1).expand(-1, cands.shape[1], -1)
        logits = self.head(torch.cat([s, self.path(cands)], dim=-1)).squeeze(-1)
        # A row with no safe candidate keeps its raw logits; the caller then holds.
        any_safe = mask.any(dim=1, keepdim=True)
        return torch.where(mask | ~any_safe, logits, torch.full_like(logits, -1e9))


def save(model: PathScorer, path: str) -> None:
    torch.save({"config": model.config, "state": model.state_dict()}, path)


def load_model(path: str) -> PathScorer:
    ckpt = torch.load(path, map_location="cpu")
    model = PathScorer(**ckpt["config"])
    model.load_state_dict(ckpt["state"])
    return model.eval()


def load_driver(path: str):
    model = load_model(path)

    @torch.no_grad()
    def drive(env, ob) -> int:
        if not ob["mask"].any():
            return HOLD_ACTION
        logits = model(
            torch.from_numpy(ob["obs"])[None],
            torch.from_numpy(np.asarray(ob["cands"], dtype=np.float32))[None],
            torch.from_numpy(ob["mask"])[None],
        )
        return int(logits.argmax(dim=1))

    return drive
