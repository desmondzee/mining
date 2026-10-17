import json
import math

DIGITS = 2
CANDIDATE_DIGITS = 1
CANDIDATE_STRIDE = 4
MAX_CANDIDATES = 8
STATIC_KEYS = ("bounds", "cameras", "truck_model", "obstacles", "paths", "views")
ROUTE_KEYS = ("route", "route_y")


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


def _top_candidates(t: dict) -> dict:
    cands, scores = t.get("candidates") or [], t.get("candidate_scores") or []
    choice = t.get("choice", -1)
    scored = [i for i in range(len(cands)) if i < len(scores) and scores[i] is not None]
    keep = sorted(scored, key=lambda i: -scores[i])[:MAX_CANDIDATES]
    if choice in scored and choice not in keep:
        keep = keep[:-1] + [choice]
    keep.sort()
    t["candidates"] = [_thin(list(cands[i])) for i in keep]
    t["candidate_scores"] = [scores[i] for i in keep]
    t["choice"] = keep.index(choice) if choice in keep else -1
    return t


def _compact_truck(t: dict) -> dict:
    t = dict(t)
    if t.get("candidates"):
        t = _top_candidates(t)
        t["ghost_paths"] = []
    elif t.get("ghost_paths"):
        t["ghost_paths"] = [_thin(list(p)) for p in t["ghost_paths"][:MAX_CANDIDATES]]
    for k in ("candidates", "ghost_paths"):
        if t.get(k):
            t[k] = _round(t[k], CANDIDATE_DIGITS)
    return t


def pack_state(state: dict) -> str:
    trucks = [_compact_truck(t) for t in state.get("trucks", [])]
    return json.dumps(_round({**state, "trucks": trucks}, DIGITS), separators=(",", ":"))


class StateDelta:
    def __init__(self) -> None:
        self.static: dict[str, str] = {}
        self.routes: dict[str, str] = {}

    def pack(self, state: dict) -> str:
        out = dict(state)
        for k in STATIC_KEYS:
            if k not in out:
                continue
            sig = json.dumps(out[k], separators=(",", ":"), default=str)
            if self.static.get(k) == sig:
                del out[k]
            else:
                self.static[k] = sig
        trucks = []
        for t in state.get("trucks", []):
            t = dict(t)
            sig = json.dumps([t.get("route"), t.get("goal")], separators=(",", ":"))
            if t.get("route") and self.routes.get(t["id"]) == sig:
                for k in ROUTE_KEYS:
                    t.pop(k, None)
            else:
                self.routes[t["id"]] = sig
            trucks.append(t)
        out["trucks"] = trucks
        return pack_state(out)
