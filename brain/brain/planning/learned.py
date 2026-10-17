import json
from pathlib import Path

import numpy as np

from .driver import CAND_DIM, OBS_DIM

LAYERS = ("scene.0", "scene.2", "path.0", "path.2", "head.0", "head.2")


class CheckpointError(ValueError):
    pass


def check_shapes(weights: dict[str, np.ndarray]) -> None:
    missing = [f"{k}.{p}" for k in LAYERS for p in ("weight", "bias") if f"{k}.{p}" not in weights]
    if missing:
        raise CheckpointError(f"checkpoint is not a PathScorer: missing {', '.join(missing)}")
    obs_dim, cand_dim = weights["scene.0.weight"].shape[1], weights["path.0.weight"].shape[1]
    if (obs_dim, cand_dim) != (OBS_DIM, CAND_DIM):
        raise CheckpointError(
            f"checkpoint expects OBS_DIM={obs_dim}, CAND_DIM={cand_dim}; "
            f"sim2d/brain features are OBS_DIM={OBS_DIM}, CAND_DIM={CAND_DIM}"
        )


def relu(a: np.ndarray) -> np.ndarray:
    return np.maximum(a, 0.0)


class NumpyScorer:
    def __init__(self, weights: dict[str, np.ndarray], config: dict | None = None) -> None:
        check_shapes(weights)
        self.config = config or {}
        self.w = {k: np.ascontiguousarray(weights[f"{k}.weight"], np.float32).T for k in LAYERS}
        self.b = {k: np.asarray(weights[f"{k}.bias"], np.float32) for k in LAYERS}

    def _linear(self, name: str, x: np.ndarray) -> np.ndarray:
        return x @ self.w[name] + self.b[name]

    def logits(self, obs: np.ndarray, cands: np.ndarray) -> np.ndarray:
        """obs (..., OBS_DIM), cands (..., C, CAND_DIM) -> raw logits (..., C)."""
        s = relu(self._linear("scene.2", relu(self._linear("scene.0", np.asarray(obs, np.float32)))))
        p = relu(self._linear("path.2", relu(self._linear("path.0", np.asarray(cands, np.float32)))))
        s = np.broadcast_to(s[..., None, :], p.shape[:-1] + s.shape[-1:])
        h = relu(self._linear("head.0", np.concatenate([s, p], axis=-1)))
        return self._linear("head.2", h)[..., 0]

    def __call__(self, obs: np.ndarray, cands: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return self.logits(obs, cands)


def load_scorer(path: str | Path) -> NumpyScorer:
    with np.load(path, allow_pickle=False) as data:
        config = json.loads(str(data["config"])) if "config" in data.files else {}
        weights = {k: data[k] for k in data.files if k != "config"}
    return NumpyScorer(weights, config)
