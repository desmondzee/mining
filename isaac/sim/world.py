import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
TRUCK_MODEL = "komatsu_hd465"
TRUCK_GLB = "unity/Assets/Models/Trucks/komatsu_hd465.glb"
TRUCK_LENGTH = 9.35
EXCAVATOR_GLB = "assets/models/excavator/excavator.glb"
EXCAVATOR_LENGTH = 10.0
EXCAVATOR_SINK = 0.15
TRUCK_COUNT = 3
CROSSING_COUNT = 4
CROSSING_MARGIN = 12.0
SPAWN_LOOK_AHEAD = 20.0
SPAWN_GAP = 2.0
SPAWN_SEARCH = 10.0
SPAWN_SEARCH_STEP = 0.5
WHEEL_FRACTION = 0.8
PEDESTRIANS_PER_PATH = 3
MIN_WALK_SPEED = 1.2
MAX_WALK_SPEED = 1.6
WALK_SEED = 1
UNITY_FLIP = np.diag([-1.0, 1.0, 1.0, 1.0])

Height = Callable[[float, float], float]


@dataclass
class Road:
    id: str
    start: str
    end: str
    width_m: float
    one_way: bool
    points: np.ndarray


@dataclass
class Site:
    id: str
    type: str
    name: str
    pos: np.ndarray


@dataclass
class Junction:
    id: str
    pos: np.ndarray


@dataclass
class TruckSpec:
    id: str
    x: float
    z: float
    yaw: float


@dataclass
class Box:
    id: str
    x: float
    y: float
    z: float
    yaw: float
    size: tuple[float, float, float]
    centre_y: float

    def circle(self) -> dict:
        c, s = abs(math.cos(math.radians(self.yaw))), abs(math.sin(math.radians(self.yaw)))
        ex = c * self.size[0] / 2 + s * self.size[2] / 2
        ez = s * self.size[0] / 2 + c * self.size[2] / 2
        return {"x": self.x, "z": self.z, "r": max(ex, ez)}


@dataclass
class World:
    dir: Path
    id: str
    bounds: list[float]
    roads: list[Road]
    sites: list[Site]
    junctions: list[Junction]
    trucks: list[TruckSpec]
    equipment: list[dict]
    scan_matrix: np.ndarray
    assets: dict = field(default_factory=dict)

    def asset(self, key: str) -> Path | None:
        name = self.assets.get(key)
        return self.dir / name if name else None


def load_world(world_dir: str | Path) -> World:
    world_dir = Path(world_dir)
    raw = json.loads((world_dir / "world.json").read_text())
    roads = sorted(
        (
            Road(r["id"], r["from"], r["to"], float(r["width_m"]), bool(r.get("one_way", False)), np.array(r["points"], float))
            for r in raw.get("roads", [])
        ),
        key=lambda r: r.id,
    )
    sites = sorted(
        (Site(s["id"], s["type"], s.get("name", s["id"]), np.array(s.get("pos3") or [s["pos"][0], 0, s["pos"][1]], float)) for s in raw.get("sites", [])),
        key=lambda s: s.id,
    )
    junctions = sorted((Junction(j["id"], np.array(j["pos"], float)) for j in raw.get("junctions", [])), key=lambda j: j.id)
    trucks = sorted(
        (TruckSpec(t["id"], float(t["spawn"][0]), float(t["spawn"][1]), float(t.get("heading_deg", 0.0))) for t in raw.get("trucks", [])),
        key=lambda t: t.id,
    )
    st = raw.get("scan_transform") or {}
    matrix = np.array(st["matrix"], float) if "matrix" in st else np.eye(4)
    return World(world_dir, raw["id"], [float(v) for v in raw["bounds"]], roads, sites, junctions, trucks, raw.get("equipment", []), matrix, raw.get("assets", {}))


def flat(p: np.ndarray) -> np.ndarray:
    return np.array([p[0], p[-1]], float)


def crossing_path(road: Road, margin: float = CROSSING_MARGIN) -> list[tuple[float, float]]:
    pts = road.points[:, [0, 2]] if road.points.shape[1] == 3 else road.points
    if len(pts) < 2:
        raise ValueError(f"road {road.id} has fewer than 2 points")
    seg = np.diff(pts, axis=0)
    lengths = np.hypot(seg[:, 0], seg[:, 1])
    remaining = lengths.sum() / 2
    for i, (s, length) in enumerate(zip(seg, lengths)):
        if remaining > length and i + 2 < len(pts):
            remaining -= length
            continue
        t = s / length
        mid = pts[i] + t * remaining
        half = np.array([t[1], -t[0]]) * (road.width_m + margin) / 2
        return [tuple(map(float, mid - half)), tuple(map(float, mid + half))]
    raise AssertionError


def crossing_paths(world: World, count: int = CROSSING_COUNT) -> list[list[tuple[float, float]]]:
    return [crossing_path(r) for r in world.roads[:count]]


def aim_heading(world: World, x: float, z: float) -> float:
    site = min(world.sites, key=lambda s: (s.pos[0] - x) ** 2 + (s.pos[2] - z) ** 2)
    road = next((r for r in world.roads if r.start == site.id), None)
    pts = road.points if road else next(r for r in world.roads if r.end == site.id).points[::-1]
    pts = pts[:, [0, -1]]
    ahead, travelled = pts[-1], 0.0
    for i in range(len(pts) - 1):
        travelled += float(np.hypot(*(pts[i + 1] - pts[i])))
        if travelled >= SPAWN_LOOK_AHEAD:
            ahead = pts[i + 1]
            break
    d = ahead - pts[0]
    return math.degrees(math.atan2(d[0], d[1]))


def yaw_rotate(yaw: float, x: float, z: float) -> tuple[float, float]:
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return c * x + s * z, -s * x + c * z


def slope(height: Height, x: float, z: float, yaw: float, box: tuple[float, float, float]) -> float:
    def h(lx: float, lz: float) -> float:
        dx, dz = yaw_rotate(yaw, lx, lz)
        return height(x + dx, z + dz)

    w, l = box[0] * WHEEL_FRACTION / 2, box[2] * WHEEL_FRACTION / 2
    fl, fr, rl, rr = h(-w, l), h(w, l), h(-w, -l), h(w, -l)
    return max(abs(fl + fr - rl - rr) / (4 * l), abs(fl + rl - fr - rr) / (4 * w))


def spawn_trucks(world: World, box: tuple[float, float, float], height: Height, count: int = TRUCK_COUNT) -> list[TruckSpec]:
    specs = world.trucks[:count] or [TruckSpec(f"truck-{i}", -15.0 + 15.0 * i, -70.0, 0.0) for i in range(count)]
    specs = [TruckSpec(t.id, t.x, t.z, aim_heading(world, t.x, t.z) if world.roads and world.sites else t.yaw) for t in specs]
    if len(specs) < 2:
        return specs
    first, last = np.array([specs[0].x, specs[0].z]), np.array([specs[-1].x, specs[-1].z])
    line = last - first
    length = float(np.hypot(*line))
    direction = line / length if length > 1e-9 else np.array([1.0, 0.0])
    spacing = max(length / (len(specs) - 1), box[0] + SPAWN_GAP)
    centre = np.mean([[t.x, t.z] for t in specs], axis=0)
    n = len(specs)

    def at(shift: float, i: int) -> np.ndarray:
        return centre + direction * (spacing * (i - (n - 1) / 2) + shift)

    steps = int(SPAWN_SEARCH / SPAWN_SEARCH_STEP)
    shifts = [k * SPAWN_SEARCH_STEP for k in range(-steps, steps + 1)]
    best = min(shifts, key=lambda s: (max(slope(height, *at(s, i), specs[i].yaw, box) for i in range(n)), abs(s)))
    return [TruckSpec(t.id, *map(float, at(best, i)), t.yaw) for i, t in enumerate(specs)]


def model_box(glb_path: Path, length: float | None = None, horizontal: float | None = None) -> tuple[np.ndarray, tuple[float, float, float]]:
    from .glb import read_glb

    lo, hi = read_glb(glb_path).bounds()
    lo, hi = UNITY_FLIP[:3, :3] @ lo, UNITY_FLIP[:3, :3] @ hi
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
    size = hi - lo
    scale = length / size[2] if length else horizontal / max(size[0], size[2])
    m = np.diag([scale, scale, scale, 1.0]) @ UNITY_FLIP
    centre = (lo + hi) / 2 * scale
    m[:3, 3] = -np.array([centre[0], lo[1] * scale, centre[2]])
    return m, tuple(float(v) for v in size * scale)


def truck_box(repo: Path = REPO) -> tuple[np.ndarray, tuple[float, float, float]]:
    return model_box(repo / TRUCK_GLB, length=TRUCK_LENGTH)


def excavators(world: World, height: Height, repo: Path = REPO) -> list[Box]:
    entries = [e for e in world.equipment if not e.get("type") or "excavat" in e["type"].lower() or "shovel" in e["type"].lower()]
    if not entries:
        return []
    _, size = model_box(repo / EXCAVATOR_GLB, horizontal=EXCAVATOR_LENGTH)
    out = []
    for e in entries:
        x, z = float(e["pos3"][0]), float(e["pos3"][2])
        y = height(x, z) - EXCAVATOR_SINK
        out.append(Box(e["id"], x, y, z, float(e.get("heading_deg", 0.0)), size, size[1] / 2))
    return out


def walk_speeds(n: int, seed: int = WALK_SEED) -> list[float]:
    rng = np.random.default_rng(seed)
    return [float(v) for v in rng.uniform(MIN_WALK_SPEED, MAX_WALK_SPEED, n)]


def hello(
    world: World,
    trucks: list[str],
    paths: list[list[tuple[float, float]]],
    obstacles: list[dict],
    truck_model: str = TRUCK_MODEL,
) -> dict:
    return {
        "type": "hello",
        "trucks": trucks,
        "cameras": ["overview", *trucks],
        "bounds": world.bounds,
        "truck_model": truck_model,
        "obstacles": obstacles,
        "paths": [{"points": [{"x": x, "z": z} for x, z in path]} for path in paths],
        "roads": [
            {
                "id": r.id,
                "from": r.start,
                "to": r.end,
                "width_m": r.width_m,
                "one_way": r.one_way,
                "points": [{"x": float(p[0]), "z": float(p[-1])} for p in r.points],
            }
            for r in world.roads
        ],
        "sites": [{"id": s.id, "type": s.type, "name": s.name, "x": float(s.pos[0]), "z": float(s.pos[2])} for s in world.sites],
        "junctions": [{"id": j.id, "x": float(j.pos[0]), "z": float(j.pos[2])} for j in world.junctions],
    }


def heightmap(world: World) -> Height:
    meta = json.loads((world.dir / world.assets.get("heightmap_meta", "heightmap_meta.json")).read_text())
    res = int(meta["resolution"])
    raw = np.fromfile(world.dir / meta["file"], "<u2").reshape(res, res).astype(np.float64)
    grid = meta["min_height_m"] + raw / 65535 * meta["height_range_m"]
    (ox, oz), (sx, sz) = meta["origin"], meta["size_m"]

    def height(x: float, z: float) -> float:
        u = np.clip((x - ox) / sx * (res - 1), 0, res - 1)
        v = np.clip((z - oz) / sz * (res - 1), 0, res - 1)
        i, j = min(int(v), res - 2), min(int(u), res - 2)
        fv, fu = v - i, u - j
        top = grid[i, j] * (1 - fu) + grid[i, j + 1] * fu
        bottom = grid[i + 1, j] * (1 - fu) + grid[i + 1, j + 1] * fu
        return float(top * (1 - fv) + bottom * fv)

    return height
