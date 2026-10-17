import numpy as np
import torch
from torch import nn

from .driver import CAND_DIM, HOLD_ACTION, OBS_DIM


class PathScorer(nn.Module):
    def __init__(self, width: int = 256):
        super().__init__()
        self.config = {"width": width}
        self.scene = nn.Sequential(nn.Linear(OBS_DIM, width), nn.ReLU(), nn.Linear(width, width), nn.ReLU())
        self.path = nn.Sequential(nn.Linear(CAND_DIM, width // 2), nn.ReLU(), nn.Linear(width // 2, width // 2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(width + width // 2, width), nn.ReLU(), nn.Linear(width, 1))

    def forward(self, obs: torch.Tensor, cands: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        s = self.scene(obs).unsqueeze(1).expand(-1, cands.shape[1], -1)
        logits = self.head(torch.cat([s, self.path(cands)], dim=-1)).squeeze(-1)
        any_safe = mask.any(dim=1, keepdim=True)
        return torch.where(mask | ~any_safe, logits, torch.full_like(logits, -1e9))


def load_scorer(path: str):
    ckpt = torch.load(path, map_location="cpu")
    model = PathScorer(**ckpt["config"])
    model.load_state_dict(ckpt["state"])
    model.eval()

    @torch.no_grad()
    def score(obs: np.ndarray, cands: np.ndarray, mask: np.ndarray) -> int:
        if not mask.any():
            return HOLD_ACTION
        logits = model(torch.from_numpy(obs)[None], torch.from_numpy(cands)[None], torch.from_numpy(mask)[None])
        return int(logits.argmax(dim=1))

    return score
