import bisect
import heapq
import math
import random
from collections.abc import Callable, Sequence

WALK_SEED = 1
AREA_COUNT = 6
PEOPLE = 12
MIN_PACE = 1.2
MAX_PACE = 1.6
HURRY = 1.25
ACCEL = 1.5
TURN_RATE = 240.0
ARRIVE = 0.1
WINDOW = 20.0
END_CLEAR = 20.0
AREA_GAP = 40.0
AREA_SHIFT = 10.0
SHOULDER_IN = 1.5
SHOULDER_OUT = 6.0
EDGE_IN = 1.0
CROSS_SHIFT = 4.0
EDGE_SHIFT = 6.0
PAUSE = (2.0, 6.0)
EDGE_PAUSE = (1.5, 4.0)
START_PAUSE = 3.0
SKIP_PAUSE = 0.3
P_CROSS = 0.3
P_EDGE = 0.15
CORRIDOR_SLACK = 6.0
YIELD_RADIUS = 15.0
YIELD_SPEED = 0.5
ATTENTIVE = 2 / 3
ROUTE_FROM = "parking"
ROUTE_TO = "ore"
POLYGON_STEP = 5.0
PLACE_TRIES = 12
PEDESTRIAN_HEIGHT = 1.8
PEDESTRIAN_RADIUS = 0.3

Point = tuple[float, float]
Ground = Callable[[float, float, float], float]
TruckState = tuple[float, float, float, float]


class Strip:
    def __init__(self, points: Sequence[Point], width: float) -> None:
        self.points = [(float(x), float(z)) for x, z in points]
        self.width = float(width)
        self.cum = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.length = self.cum[-1]

    def segment(self, s: float) -> int:
        return min(max(bisect.bisect_right(self.cum, s) - 1, 0), len(self.points) - 2)

    def frame(self, s: float) -> tuple[Point, Point]:
        i = self.segment(s)
        (ax, az), (bx, bz) = self.points[i], self.points[i + 1]
        seg = self.cum[i + 1] - self.cum[i] or 1.0
        tx, tz = (bx - ax) / seg, (bz - az) / seg
        f = s - self.cum[i]
        return (ax + tx * f, az + tz * f), (tx, tz)

    def at(self, s: float, lateral: float) -> Point:
        (px, pz), (tx, tz) = self.frame(s)
        return px + tz * lateral, pz - tx * lateral

    def locate(self, x: float, z: float, lo: float = 0.0, hi: float = math.inf) -> tuple[float, float]:
        best = (math.inf, 0.0, 0.0)
        for i in range(self.segment(lo), self.segment(hi) + 1):
            (ax, az), (bx, bz) = self.points[i], self.points[i + 1]
            abx, abz = bx - ax, bz - az
            seg2 = abx * abx + abz * abz or 1.0
            t = max(0.0, min(1.0, ((x - ax) * abx + (z - az) * abz) / seg2))
            px, pz = ax + t * abx, az + t * abz
            d = math.hypot(x - px, z - pz)
            if d < best[0]:
                side = (x - ax) * abz - (z - az) * abx
                best = (d, self.cum[i] + t * math.sqrt(seg2), math.copysign(d, side))
        return best[1], best[2]


class Area:
    def __init__(self, road_id: str, strip: Strip, lo: float, hi: float) -> None:
        self.road_id, self.strip, self.lo, self.hi = road_id, strip, lo, hi
        self.half = strip.width / 2

    def band(self, rng: random.Random, side: int) -> float:
        return side * (self.half + rng.uniform(SHOULDER_IN, SHOULDER_OUT))

    def place(self, s: float, lateral: float) -> Point:
        p = self.strip.at(s, lateral)
        if abs(lateral) > self.half:
            for k in range(1, PLACE_TRIES + 1):
                got = self.locate(*p)[1]
                if got * lateral > 0 and abs(got) >= self.half + SHOULDER_IN / 2:
                    break
                p = self.strip.at(s, math.copysign(abs(lateral) + k, lateral))
        return p

    def clamp(self, s: float) -> float:
        return min(max(s, self.lo), self.hi)

    def locate(self, x: float, z: float) -> tuple[float, float]:
        return self.strip.locate(x, z, self.lo - WINDOW, self.hi + WINDOW)

    def straight(self) -> bool:
        n = max(1, math.ceil((self.hi - self.lo) / POLYGON_STEP))
        for k in range(n + 1):
            s = self.lo + (self.hi - self.lo) * k / n
            for lateral in (self.half + SHOULDER_IN, self.half + SHOULDER_OUT, -self.half - SHOULDER_IN, -self.half - SHOULDER_OUT):
                if abs(self.locate(*self.strip.at(s, lateral))[1] - lateral) > 1.0:
                    return False
        return True

    def polygon(self) -> list[Point]:
        n = max(1, math.ceil((self.hi - self.lo) / POLYGON_STEP))
        ss = [self.lo + (self.hi - self.lo) * k / n for k in range(n + 1)]
        out = self.half + SHOULDER_OUT
        return [self.strip.at(s, -out) for s in ss] + [self.strip.at(s, out) for s in reversed(ss)]


def route_usage(roads: Sequence[dict], sites: dict[str, str], start: str = ROUTE_FROM, kind: str = ROUTE_TO) -> dict[str, int]:
    graph: dict[str, list[tuple[str, float, str]]] = {}
    for r in roads:
        length = Strip(r["points"], r["width_m"]).length
        graph.setdefault(r["from"], []).append((r["to"], length, r["id"]))
        if not r.get("one_way", False):
            graph.setdefault(r["to"], []).append((r["from"], length, r["id"]))
    dist, via, heap = {start: 0.0}, {}, [(0.0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, math.inf):
            continue
        for nxt, length, rid in graph.get(node, []):
            if d + length < dist.get(nxt, math.inf):
                dist[nxt], via[nxt] = d + length, (node, rid)
                heapq.heappush(heap, (d + length, nxt))
    usage: dict[str, int] = {}
    for site in sorted(s for s, t in sites.items() if t == kind):
        node = site
        while node in via:
            node, rid = via[node]
            usage[rid] = usage.get(rid, 0) + 1
    return usage


def wander_areas(roads: Sequence[dict], sites: dict[str, str], count: int = AREA_COUNT) -> list[Area]:
    usage = route_usage(roads, sites)
    ranked = sorted(roads, key=lambda r: (-usage.get(r["id"], 0), r["id"]))
    used = [r for r in ranked if usage.get(r["id"], 0)] or ranked
    strips = {r["id"]: Strip(r["points"], r["width_m"]) for r in used}
    clear = {rid: min(END_CLEAR, s.length / 4) for rid, s in strips.items()}
    slots = dict.fromkeys(strips, 0)
    while sum(slots.values()) < count:
        grown = False
        for r in used:
            rid = r["id"]
            if sum(slots.values()) < count and (strips[rid].length - 2 * clear[rid]) >= slots[rid] * AREA_GAP:
                slots[rid] += 1
                grown = True
        if not grown:
            break
    areas = []
    for r in used:
        rid, strip = r["id"], strips[r["id"]]
        lo, hi = clear[rid], strip.length - clear[rid]
        for k in range(slots[rid]):
            centre = lo + (hi - lo) * (k + 0.5) / slots[rid]
            options = [Area(rid, strip, max(lo, c - WINDOW), min(hi, c + WINDOW)) for c in shifts(centre, lo, hi)]
            free = [a for a in options if all(o.road_id != rid or abs(o.lo - a.lo) >= WINDOW for o in areas)] or options
            areas.append(next((a for a in free if a.straight()), free[0]))
    return areas


def shifts(centre: float, lo: float, hi: float) -> list[float]:
    out = [centre]
    for k in range(1, int((hi - lo) / AREA_SHIFT) + 1):
        out += [c for c in (centre + k * AREA_SHIFT, centre - k * AREA_SHIFT) if lo <= c <= hi]
    return out


def paces(n: int, seed: int = WALK_SEED) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(MIN_PACE, MAX_PACE) for _ in range(n)]


class Walker:
    def __init__(self, walker_id: str, area: Area, pace: float = 1.4, seed: int = WALK_SEED) -> None:
        self.id, self.area, self.pace = walker_id, area, pace
        self.rng = random.Random(seed)
        self.attentive = self.rng.random() < ATTENTIVE
        side = self.rng.choice((-1, 1))
        s = self.rng.uniform(area.lo, area.hi)
        self.x, self.z = area.place(s, area.band(self.rng, side))
        _, (tx, tz) = area.strip.frame(s)
        self.yaw = math.degrees(math.atan2(tx, tz)) % 360.0 if self.rng.random() < 0.5 else math.degrees(math.atan2(-tx, -tz)) % 360.0
        self.y = 0.0
        self.speed = 0.0
        self.goal: Point | None = None
        self.mode = "pause"
        self.wait = self.rng.uniform(0.0, START_PAUSE)
        self.corridor_time = 0.0
        self.yielding = False

    @property
    def target_speed(self) -> float:
        if self.wait > 0 or self.goal is None or self.yielding:
            return 0.0
        return self.pace * (HURRY if self.mode in ("cross", "exit") else 1.0)

    def inside(self, lateral: float) -> bool:
        return abs(lateral) < self.area.half

    def threatened(self, trucks: Sequence[TruckState]) -> bool:
        for tx, tz, yaw, speed in trucks:
            dx, dz = self.x - tx, self.z - tz
            if speed > YIELD_SPEED and math.hypot(dx, dz) < YIELD_RADIUS and dx * math.sin(math.radians(yaw)) + dz * math.cos(math.radians(yaw)) > 0:
                return True
        return False

    def choose(self, s: float, lateral: float) -> None:
        a, rng = self.area, self.rng
        side = 1 if lateral >= 0 else -1
        r = rng.random()
        if self.mode in ("edge", "exit") or self.inside(lateral):
            r = 0.0 if r < 0.5 else 1.0
        if r < P_CROSS:
            self.mode = "cross"
            gs, gl = a.clamp(s + rng.uniform(-CROSS_SHIFT, CROSS_SHIFT)), a.band(rng, -side)
        elif r < P_CROSS + P_EDGE:
            self.mode = "edge"
            gs, gl = a.clamp(s + rng.uniform(-EDGE_SHIFT, EDGE_SHIFT)), side * (a.half - EDGE_IN)
        else:
            self.mode = "stroll"
            gs, gl = rng.uniform(a.lo, a.hi), a.band(rng, side)
        self.goal = a.place(gs, gl)

    def exit(self, s: float, lateral: float) -> None:
        side = 1 if lateral >= 0 else -1
        self.mode, self.wait = "exit", 0.0
        self.goal = self.area.place(s, side * (self.area.half + SHOULDER_IN))

    def arrive(self) -> None:
        self.goal = None
        if self.mode == "edge":
            self.wait = self.rng.uniform(*EDGE_PAUSE)
        elif self.mode == "exit" or self.rng.random() >= SKIP_PAUSE:
            self.wait = self.rng.uniform(*PAUSE)
        else:
            self.wait = 0.0

    def step(self, dt: float, ground: Ground | None = None, trucks: Sequence[TruckState] = ()) -> None:
        a = self.area
        s, lateral = a.locate(self.x, self.z)
        inside = self.inside(lateral)
        self.corridor_time = self.corridor_time + dt if inside else 0.0
        threat = self.attentive and self.threatened(trucks)
        left = (a.half - abs(lateral)) / (self.pace * HURRY) + self.pace / ACCEL
        if inside and self.mode != "exit" and (threat or self.corridor_time + left > a.strip.width / self.pace + CORRIDOR_SLACK):
            self.exit(s, lateral)
        if self.wait > 0:
            self.wait -= dt
        if self.wait <= 0 and self.goal is None:
            self.choose(s, lateral)
        self.yielding = False
        if threat and not inside and self.goal is not None:
            _, gl = a.locate(*self.goal)
            self.yielding = abs(gl) < a.half or (gl >= 0) != (lateral >= 0)
        dist = math.dist(self.goal, (self.x, self.z)) if self.goal is not None else 0.0
        want = min(self.target_speed, math.sqrt(2 * ACCEL * dist))
        self.speed += max(-ACCEL * dt, min(ACCEL * dt, want - self.speed))
        if self.goal is not None:
            dx, dz = self.goal[0] - self.x, self.goal[1] - self.z
            if dist < ARRIVE:
                self.arrive()
            else:
                turn = (math.degrees(math.atan2(dx, dz)) - self.yaw + 180.0) % 360.0 - 180.0
                limit = TURN_RATE * dt
                self.yaw = (self.yaw + max(-limit, min(limit, turn))) % 360.0
                move = min(self.speed * dt, dist)
                self.x += math.sin(math.radians(self.yaw)) * move
                self.z += math.cos(math.radians(self.yaw)) * move
        if ground:
            self.y = ground(self.x, self.z, self.y)

    def state(self) -> dict:
        return {"id": self.id, "x": self.x, "z": self.z, "yaw": self.yaw}


def make_walkers(
    roads: Sequence[dict], sites: dict[str, str], prefix: str = "Pedestrian_", seed: int = WALK_SEED, count: int = AREA_COUNT, people: int = PEOPLE
) -> tuple[list[Area], list[Walker]]:
    areas = wander_areas(roads, sites, count)
    speeds = paces(people, seed)
    walkers = [Walker(f"{prefix}{k:02d}", areas[k * len(areas) // people], speeds[k], seed * 1000 + k) for k in range(people)] if areas else []
    return areas, walkers
