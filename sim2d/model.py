"""Candidate-path scorer (design 3).

The scene encoder reads lidar + route + state. The candidate encoder reads one
3 s path. The head scores each (scene, path) pair. Unsafe candidates are
masked out, and the truck drives the highest-scoring safe path.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from .env import CAND_DIM, HOLD_ACTION, N_RAYS, OBS_DIM

STACK_GAP = 3  # ticks between stacked scene frames (0.3 s)


def stack_index(trips: np.ndarray, stack: int) -> np.ndarray:
    """(N, stack) sample indices of the frames 0, STACK_GAP, 2*STACK_GAP ticks back, clipped to the trip start."""
    i = np.arange(len(trips))
    start = np.r_[0, np.flatnonzero(np.diff(trips)) + 1]
    first = start[np.searchsorted(start, i, side="right") - 1]
    return np.stack([np.maximum(i - k * STACK_GAP, first) for k in range(stack)], axis=1)


def scene_dim(inputs: str) -> int:
    """obs: lidar rays + route + state. objects: route + state + a detector object list (no lidar rays)."""
    from .env import N_OBJECTS

    return OBS_DIM if inputs == "obs" else OBS_DIM - N_RAYS + 4 * N_OBJECTS


def scene_input(inputs: str, obs: np.ndarray, objs: np.ndarray = None) -> np.ndarray:
    return obs if inputs == "obs" else np.concatenate([obs[..., N_RAYS:], objs], axis=-1)


class PathScorer(nn.Module):
    def __init__(self, width: int = 256, stack: int = 1, inputs: str = "obs"):
        super().__init__()
        self.config = {"width": width} if stack == 1 else {"width": width, "stack": stack}
        if inputs != "obs":
            self.config["inputs"] = inputs
        self.stack, self.inputs = stack, inputs
        self.scene = nn.Sequential(nn.Linear(scene_dim(inputs) * stack, width), nn.ReLU(), nn.Linear(width, width), nn.ReLU())
        self.path = nn.Sequential(nn.Linear(CAND_DIM, width // 2), nn.ReLU(), nn.Linear(width // 2, width // 2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(width + width // 2, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, obs: torch.Tensor, cands: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """obs (B, OBS_DIM * stack), newest frame first; cands (B, C, CAND_DIM), mask (B, C) bool -> logits (B, C)."""
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
    hist = []

    @torch.no_grad()
    def drive(env, ob) -> int:
        if env.ticks == 0:
            hist.clear()
        hist.append(scene_input(model.inputs, ob["obs"], env.object_list() if model.inputs == "objects" else None))
        if not ob["mask"].any():
            return HOLD_ACTION
        obs = np.concatenate([hist[max(0, len(hist) - 1 - k * STACK_GAP)] for k in range(model.stack)])
        logits = model(
            torch.from_numpy(obs)[None],
            torch.from_numpy(np.asarray(ob["cands"], dtype=np.float32))[None],
            torch.from_numpy(ob["mask"])[None],
        )
        return int(logits.argmax(dim=1))

    return drive
