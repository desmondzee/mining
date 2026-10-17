import json
import math

DIGITS = 2
CANDIDATE_DIGITS = 1
CANDIDATE_STRIDE = 3


def _round(value, digits):
    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _round(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v, digits) for v in value]
    return value


def _thin(path):
    if len(path) <= 2:
        return path
    return path[::CANDIDATE_STRIDE] + ([path[-1]] if (len(path) - 1) % CANDIDATE_STRIDE else [])


def pack_state(state: dict) -> str:
    trucks = []
    for t in state.get("trucks", []):
        t = dict(t)
        if t.get("candidates"):
            t["candidates"] = _round([_thin(list(p)) for p in t["candidates"]], CANDIDATE_DIGITS)
        if t.get("ghost_paths"):
            t["ghost_paths"] = _round([_thin(list(p)) for p in t["ghost_paths"]], CANDIDATE_DIGITS)
        trucks.append(t)
    return json.dumps(_round({**state, "trucks": trucks}, DIGITS), separators=(",", ":"))
