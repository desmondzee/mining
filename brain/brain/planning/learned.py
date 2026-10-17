import json
import logging
import os
from pathlib import Path

import numpy as np

from .driver import CAND_DIM, OBS_DIM

log = logging.getLogger(__name__)

LAYERS = ("scene.0", "scene.2", "path.0", "path.2", "head.0", "head.2")
DEVICE = "auto"


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


def onnx_model(scorer: NumpyScorer) -> bytes:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    nodes, inits = [], []
    for k in LAYERS:
        inits += [numpy_helper.from_array(scorer.w[k], f"{k}.w"), numpy_helper.from_array(scorer.b[k], f"{k}.b")]

    def dense(name: str, x: str, act: bool = True) -> str:
        nodes.append(helper.make_node("MatMul", [x, f"{name}.w"], [f"{name}.mm"]))
        nodes.append(helper.make_node("Add", [f"{name}.mm", f"{name}.b"], [f"{name}.y"]))
        if not act:
            return f"{name}.y"
        nodes.append(helper.make_node("Relu", [f"{name}.y"], [f"{name}.r"]))
        return f"{name}.r"

    s = dense("scene.2", dense("scene.0", "obs"))
    p = dense("path.2", dense("path.0", "cands"))
    inits.append(numpy_helper.from_array(np.array([1], np.int64), "axis1"))
    inits.append(numpy_helper.from_array(np.array([-1], np.int64), "axis_last"))
    inits.append(numpy_helper.from_array(np.array([2], np.int64), "two"))
    nodes += [
        helper.make_node("Unsqueeze", [s, "axis1"], ["s1"]),
        helper.make_node("Shape", [p], ["pshape"]),
        helper.make_node("Slice", ["pshape", "axis1", "two"], ["count"]),
        helper.make_node("Concat", ["axis1", "count", "axis1"], ["tile"], axis=0),
        helper.make_node("Expand", ["s1", "tile"], ["sx"]),
        helper.make_node("Concat", ["sx", p], ["sp"], axis=-1),
    ]
    out = dense("head.2", dense("head.0", "sp"), act=False)
    nodes.append(helper.make_node("Squeeze", [out, "axis_last"], ["logits"]))
    graph = helper.make_graph(
        nodes,
        "path_scorer",
        [helper.make_tensor_value_info("obs", TensorProto.FLOAT, ["B", OBS_DIM]),
         helper.make_tensor_value_info("cands", TensorProto.FLOAT, ["B", "C", CAND_DIM])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["B", "C"])],
        inits,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


class OnnxScorer:
    def __init__(self, base: NumpyScorer, device: str = DEVICE) -> None:
        from ..detector import open_session

        self.config = base.config
        self.session = open_session(onnx_model(base), 1, device, {"use_tf32": 0})
        self.device = "cuda" if self.session.get_providers()[0] == "CUDAExecutionProvider" else "cpu"

    def logits(self, obs: np.ndarray, cands: np.ndarray) -> np.ndarray:
        obs, cands = np.asarray(obs, np.float32), np.asarray(cands, np.float32)
        lead = cands.shape[:-2]
        out = self.session.run(None, {"obs": obs.reshape(-1, OBS_DIM), "cands": cands.reshape(-1, *cands.shape[-2:])})[0]
        return out.reshape(*lead, cands.shape[-2])

    def __call__(self, obs: np.ndarray, cands: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return self.logits(obs, cands)


def load_scorer(path: str | Path, device: str | None = None) -> NumpyScorer | OnnxScorer:
    with np.load(path, allow_pickle=False) as data:
        config = json.loads(str(data["config"])) if "config" in data.files else {}
        weights = {k: data[k] for k in data.files if k != "config"}
    base = NumpyScorer(weights, config)
    device = (device or os.environ.get("PLANNER_DEVICE", DEVICE)).strip().lower()
    if device == "cpu":
        return base
    try:
        gpu = OnnxScorer(base, device)
    except ImportError:
        return base
    except Exception:
        log.exception("planner: ONNX scorer failed, using numpy")
        return base
    return gpu if gpu.device == "cuda" else base
