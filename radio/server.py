"""Push-to-talk radio dispatcher for the autonomous haul trucks.

Phone (radio/web/index.html) -> /api/radio/transcribe (ElevenLabs STT) -> /api/radio/command
-> parser (OpenAI tool calling, regex fallback) -> road-graph routes fed to the brain as
waypoints (POST /api/trucks/{id}/goal) -> radio replies (ElevenLabs TTS) over /ws/radio.

Nothing moves until a human speaks (or types) a command. POST /api/radio/release clears everything.

Run:  uv run --with fastapi --with uvicorn --with httpx --with python-multipart radio/server.py
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import hmac
import json
import logging
import math
import os
import re
import secrets
import socket
import subprocess
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

log = logging.getLogger("radio")

RADIO_DIR = Path(__file__).resolve().parent
REPO = RADIO_DIR.parent
WEB_INDEX = RADIO_DIR / "web" / "index.html"
CACHE_DIR = RADIO_DIR / "cache" / "audio"

BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:8080").rstrip("/")
WORLD_PATH = Path(os.environ.get("RADIO_WORLD", REPO / "data" / "worlds" / "quarry" / "world.json"))
MOCK_ENV = os.environ.get("RADIO_MOCK", "auto").lower()  # auto | 1 | 0
MOCK_SPEED = float(os.environ.get("RADIO_MOCK_SPEED", "1.0"))
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
EL_BASE = "https://api.elevenlabs.io"
EL_TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
EL_STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")

POLL_HZ = 5.0
ADVANCE_M = 9.0  # straight-road waypoint: switch to next when this close
BEND_ADVANCE_M = 5.5  # bend waypoint
ARRIVE_M = 4.5  # final waypoint (brain arrives at 4.0)
BEND_DEG = 12.0
MAX_SPACING_M = 20.0
LOAD_DWELL_S = float(os.environ.get("RADIO_LOAD_DWELL", "5"))
DUMP_DWELL_S = float(os.environ.get("RADIO_DUMP_DWELL", "4"))
OVERRIDE_GRACE_S = 1.5
BRAIN_CLEAR_M = 6.0
ORBIT_M = 12.0  # the straight-line policy can circle a goal inside its turning circle (R ~ 8.7 m)
ORBIT_S = 5.0  # ... so if we hover within ORBIT_M this long, call it reached

BRAIN_ROUTING_ENV = os.environ.get("RADIO_BRAIN_ROUTING", "auto").lower()

RADIO_PIN = os.environ.get("RADIO_PIN", "").strip()
SESSION_COOKIE = "radio_session"
SESSION_TTL_S = float(os.environ.get("RADIO_SESSION_HOURS", "12")) * 3600
MAX_AUDIO_BYTES = int(os.environ.get("RADIO_MAX_AUDIO_KB", "600")) * 1024
MAX_COMMAND_CHARS = 300


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


NUM_NAMES = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]


# --------------------------------------------------------------------------- secrets


def dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and not k.startswith("#"):
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def secret(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
        f = os.environ.get(f"{n}_FILE")
        try:
            if f and (v := Path(f).read_text().strip()):
                return v
        except OSError:
            log.warning("%s_FILE is set but not readable", n)
    for n in names:
        for d in (RADIO_DIR / "secrets", REPO / "envgen" / "secrets"):
            p = d / n.lower()
            try:
                if p.is_file() and (v := p.read_text().strip()):
                    return v
            except OSError:
                pass
    env = dotenv(REPO / ".env")
    return next((env[n] for n in names if env.get(n)), None)


OPENAI_KEY = secret("OPENAI_API_KEY")
EL_KEY = secret("ELEVENLABS_API_KEY", "ELEVEN_LABS_API_KEY")


# --------------------------------------------------------------------------- world + road graph


def callsign(truck_id: str) -> str:
    m = re.search(r"(\d+)$", truck_id)
    if not m:
        return truck_id
    n = int(m.group(1)) + 1  # truck-0 is "Truck One"
    return f"Truck {NUM_NAMES[n] if n < len(NUM_NAMES) else n}"


@dataclass
class Site:
    id: str
    type: str
    name: str
    x: float
    z: float
    spoken: str
    aliases: list[str]
    point: tuple[float, float]  # where trucks stop
    slots: list[tuple[float, float]] = field(default_factory=list)


class World:
    def __init__(self, path: Path):
        self.raw = json.loads(path.read_text())
        self.bounds = self.raw.get("bounds")
        self.sites: dict[str, Site] = {}
        ore_rank = 0
        for s in self.raw["sites"]:
            x, z = s["pos"][0], s["pos"][1]
            point = (x, z)
            for k in ("load_point", "dump_point"):
                if k in s:
                    p = s[k]["pos3"]
                    point = (p[0], p[2])
            slots = [(sl["pos3"][0], sl["pos3"][2]) for sl in s.get("slots", [])]
            name = s.get("name", s["id"])
            t = s["type"]
            low = name.lower()
            aliases = {low, s["id"].lower().replace("_", " ")}
            spoken = name
            if t == "ore":
                ore_rank += 1
                first = low.split()[0]
                aliases |= {first, f"{first} bench", f"{first} pit", f"{first} phase", f"{first} place",
                            f"{first} base", f"{first} faith", f"face {NUM_NAMES[ore_rank].lower()}",
                            f"ore {NUM_NAMES[ore_rank].lower()}", f"pit {NUM_NAMES[ore_rank].lower()}"}
                if first == "upper":
                    aliases |= {"top face", "top bench", "top"}
                if first == "lower":
                    aliases |= {"bottom face", "bottom bench", "bottom"}
                if first == "middle":
                    aliases |= {"mid face", "mid bench", "midface"}
            elif t == "crusher":
                aliases |= {"crusher", "crushers", "the crusher", "primary", "plant", "crush", "rusher",
                            "unload", "unloading", "unload point", "unloading point", "unloading area", "unload area",
                            "offload", "off load", "drop off", "dropoff", "dump point", "tipping point", "unloader"}
                spoken = "crusher"
            elif t == "dump":
                aliases |= {"dump", "waste dump", "waste", "tip", "spoil", "dump site"}
                spoken = "waste dump"
            elif t == "parking":
                aliases |= {"ready line", "readyline", "ready-line", "red line", "ready lane", "parking",
                            "park", "home", "line up", "lineup", "parking lot"}
                spoken = "the ready line"
            elif t == "fuel":
                aliases |= {"fuel bay", "fuel", "refuel", "diesel", "fuel station", "fueling"}
                spoken = "fuel bay"
            self.sites[s["id"]] = Site(s["id"], t, name, x, z, spoken, sorted(aliases, key=len, reverse=True),
                                       point, slots)
        self.trucks = {t["id"]: t for t in self.raw.get("trucks", [])}
        self._build_graph()

    def by_type(self, t: str) -> Site | None:
        return next((s for s in self.sites.values() if s.type == t), None)

    def _build_graph(self) -> None:
        self.nodes: list[tuple[float, float]] = []
        self.adj: dict[int, list[tuple[int, float]]] = {}

        def node(x: float, z: float) -> int:
            for i, (nx, nz) in enumerate(self.nodes):
                if abs(nx - x) < 0.75 and abs(nz - z) < 0.75:
                    return i
            self.nodes.append((x, z))
            self.adj[len(self.nodes) - 1] = []
            return len(self.nodes) - 1

        self.road_lines = []
        for r in self.raw.get("roads", []):
            pts = [(p[0], p[2]) for p in r["points"]]
            self.road_lines.append({"id": r["id"], "name": r.get("name"), "points": [[round(a, 2), round(b, 2)] for a, b in pts]})
            ids = [node(*p) for p in pts]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                d = math.dist(self.nodes[a], self.nodes[b])
                self.adj[a].append((b, d))
                self.adj[b].append((a, d))

    def nearest(self, x: float, z: float) -> int:
        return min(range(len(self.nodes)), key=lambda i: (self.nodes[i][0] - x) ** 2 + (self.nodes[i][1] - z) ** 2)

    def road_path(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
        s, g = self.nearest(*start), self.nearest(*goal)
        dist = {s: 0.0}
        prev: dict[int, int] = {}
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == g:
                break
            if d > dist.get(u, math.inf):
                continue
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
        path = [g]
        while path[-1] != s and path[-1] in prev:
            path.append(prev[path[-1]])
        path = [self.nodes[i] for i in reversed(path)]
        if math.dist(path[-1], goal) > 1.0:
            path.append(goal)
        # drop leading nodes the truck is already at / past
        while len(path) >= 2:
            p0, p1 = path[0], path[1]
            if math.dist(start, p0) < 6.0:
                path.pop(0)
                continue
            sx, sz = p1[0] - p0[0], p1[1] - p0[1]
            L2 = sx * sx + sz * sz or 1e-9
            t = ((start[0] - p0[0]) * sx + (start[1] - p0[1]) * sz) / L2
            perp = abs((start[0] - p0[0]) * sz - (start[1] - p0[1]) * sx) / math.sqrt(L2)
            if t > 0 and perp < 15.0:
                path.pop(0)
                continue
            break
        return path


@dataclass
class Waypoint:
    x: float
    z: float
    bend: bool = False
    final: bool = False


def heading(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


def angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


RDP_TOL_M = 1.5  # road centrelines come off a grid and zig-zag by ~1-2 m; ignore that


def rdp(pts: list[tuple[float, float]], tol: float) -> list[int]:
    """Ramer-Douglas-Peucker; returns kept indices."""
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, az), (bx, bz) = pts[a], pts[b]
        L = math.hypot(bx - ax, bz - az) or 1e-9
        best, bi = 0.0, -1
        for i in range(a + 1, b):
            d = abs((pts[i][0] - ax) * (bz - az) - (pts[i][1] - az) * (bx - ax)) / L
            if d > best:
                best, bi = d, i
        if best > tol:
            keep.add(bi)
            stack += [(a, bi), (bi, b)]
    return sorted(keep)


def simplify(start: tuple[float, float], path: list[tuple[float, float]]) -> list[Waypoint]:
    """Keep a waypoint wherever the road turns > BEND_DEG (after removing grid jitter), else every ~20 m."""
    if not path:
        return []
    pts = [start] + list(path)
    idx = rdp(pts, RDP_TOL_M)
    corners = [pts[i] for i in idx]  # corners[0] == start
    wps: list[Waypoint] = []
    for k in range(1, len(corners)):
        a, b = corners[k - 1], corners[k]
        seg = math.dist(a, b)
        n = max(1, math.ceil(seg / MAX_SPACING_M))
        for j in range(1, n):  # straight filler points every <= 20 m
            f = j / n
            wps.append(Waypoint(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        final = k == len(corners) - 1
        bend = final or angdiff(heading(a, b), heading(b, corners[k + 1])) > BEND_DEG
        wps.append(Waypoint(b[0], b[1], bend=bend, final=final))
    # first waypoint right next to the truck is pointless
    while len(wps) > 1 and math.dist(start, (wps[0].x, wps[0].z)) < 3.0:
        wps.pop(0)
    return wps


def path_len(start: tuple[float, float], wps: list[Waypoint]) -> float:
    total, cur = 0.0, start
    for w in wps:
        total += math.dist(cur, (w.x, w.z))
        cur = (w.x, w.z)
    return total


# --------------------------------------------------------------------------- brain backends


class RealBrain:
    mode = "brain"

    def __init__(self, url: str):
        self.url = url
        self.client = httpx.AsyncClient(base_url=url, timeout=2.0)

    async def state(self) -> dict:
        r = await self.client.get("/api/state")
        r.raise_for_status()
        return r.json()

    async def set_goal(self, tid: str, x: float, z: float) -> None:
        r = await self.client.post(f"/api/trucks/{tid}/goal", json={"x": x, "z": z})
        r.raise_for_status()

    async def set_site(self, tid: str, site: str) -> None:
        r = await self.client.post(f"/api/trucks/{tid}/goal", json={"site": site})
        r.raise_for_status()

    async def clear_goal(self, tid: str) -> None:
        r = await self.client.delete(f"/api/trucks/{tid}/goal")
        r.raise_for_status()


def route_points(route) -> list[tuple[float, float]]:
    out = []
    for p in route or []:
        if isinstance(p, dict):
            out.append((float(p["x"]), float(p["z"])))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append((float(p[0]), float(p[-1])))
    return out


def brain_routes(state: dict) -> bool:
    trucks = state.get("trucks") or []
    return bool(state.get("roads")) and any("route" in t or "goal_site" in t for t in trucks)


def brain_site_ids(state: dict) -> set[str]:
    s = state.get("sites") or []
    if isinstance(s, dict):
        return set(s)
    return {str(x.get("id")) if isinstance(x, dict) else str(x) for x in s}


class MockBrain:
    """In-process stand-in: bicycle model + the brain's straight-line policy (no obstacles)."""

    mode = "mock"
    MAX_SPEED, MAX_STEER, WHEELBASE = 8.0, 30.0, 5.0

    def __init__(self, world: World):
        self.trucks: dict[str, dict] = {}
        for i, (tid, t) in enumerate(sorted(world.trucks.items()) or [(f"truck-{i}", None) for i in range(3)]):
            if t:
                x, z, yaw = t["spawn"][0], t["spawn"][1], t.get("heading_deg", 0.0)
            else:
                x, z, yaw = -10.0 + 10 * i, 0.0, 0.0
            self.trucks[tid] = dict(id=tid, x=x, z=z, yaw=yaw, speed=0.0, goal=None, status="idle", throttle=0.0, steer=0.0)
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if not self.task:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def _run(self) -> None:
        dt = 1 / 20
        while True:
            for t in self.trucks.values():
                self._step(t, dt * MOCK_SPEED)
            await asyncio.sleep(dt)

    def _step(self, t: dict, dt: float) -> None:
        if t["goal"] is None:
            throttle, steer, t["status"] = 0.0, 0.0, "idle"
        else:
            gx, gz = t["goal"]
            if math.hypot(gx - t["x"], gz - t["z"]) < 4.0:
                throttle, steer, t["status"] = 0.0, 0.0, "arrived"
            else:
                err = (math.degrees(math.atan2(gx - t["x"], gz - t["z"])) - t["yaw"] + 180) % 360 - 180
                steer = max(-1.0, min(1.0, err / 45.0))
                throttle, t["status"] = 1.0 - 0.6 * abs(steer), "driving"
        t["throttle"], t["steer"] = throttle, steer
        t["speed"] += (throttle * self.MAX_SPEED - t["speed"]) * min(1.0, dt * 1.5)
        yaw_rate = t["speed"] / self.WHEELBASE * math.tan(math.radians(steer * self.MAX_STEER))
        t["yaw"] = (t["yaw"] + math.degrees(yaw_rate * dt)) % 360
        t["x"] += math.sin(math.radians(t["yaw"])) * t["speed"] * dt
        t["z"] += math.cos(math.radians(t["yaw"])) * t["speed"] * dt

    async def state(self) -> dict:
        return {"sim_connected": True, "mock": True, "trucks": [dict(t, goal=list(t["goal"]) if t["goal"] else None) for t in self.trucks.values()]}

    async def set_goal(self, tid: str, x: float, z: float) -> None:
        if tid not in self.trucks:
            raise HTTPException(404, tid)
        self.trucks[tid]["goal"] = (x, z)
        self.trucks[tid]["status"] = "driving"

    async def clear_goal(self, tid: str) -> None:
        if tid in self.trucks:
            self.trucks[tid]["goal"] = None
            self.trucks[tid]["status"] = "idle"


# --------------------------------------------------------------------------- parsing

NUMWORD = {"zero": 0, "oh": 0, "nought": 0, "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3,
           "tree": 3, "free": 3, "four": 4, "for": 4, "five": 5, "six": 6}
NUM_RE = r"(?:zero|oh|one|won|two|too|to|three|tree|free|four|for|five|six|\d+)"
TRUCK_RE = re.compile(rf"\b(?:trucks?|units?|haulers?)\s*(?:number\s*)?#?-?\s*({NUM_RE}(?:\s*(?:,|and|&|\+)\s*(?:trucks?\s*)?{NUM_RE})*)\b")
LITERAL_ID_RE = re.compile(r"\btruck-(\d+)\b")
ALL_RE = re.compile(r"\b(all trucks|all units|all stations|all|everyone|everybody|every truck|whole fleet|fleet)\b")
STOP_RE = re.compile(r"\b(stop|stopped|halt|hold|freeze|abort|cancel|e-?stop|stand down|emergency)\b")
STATUS_RE = re.compile(r"\b(status|report|sitrep|where are|where is|what are you doing|position|check in)\b")
HAUL_RE = re.compile(r"\b(haul|hauling|haulage|cycle|cycling|loads?|ferry|shuttle|production|start mining)\b")
RETURN_RE = re.compile(r"\b(return|returning|go home|head home|back to base|come home|park up|rtb)\b")
LOADS_RE = re.compile(rf"\b({NUM_RE}|seven|eight|nine|ten)\s+(?:loads?|runs?|trips?|cycles?)\b")


def norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9#&+,\- ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def num_value(w: str) -> int | None:
    w = w.strip()
    if w.isdigit():
        return int(w)
    return NUMWORD.get(w, {"seven": 7, "eight": 8, "nine": 9, "ten": 10}.get(w))


class Parser:
    def __init__(self, world: World, truck_ids):
        self.world = world
        self.truck_ids = truck_ids  # callable -> list[str]
        self.alias_list = sorted(((a, s.id) for s in world.sites.values() for a in s.aliases), key=lambda x: -len(x[0]))

    # ---- helpers shared by LLM + regex
    def truck_from_number(self, n: int) -> str | None:
        ids = self.truck_ids()
        idx = 0 if n == 0 else n - 1  # "Truck One" = truck-0; "truck zero" also truck-0
        tid = f"truck-{idx}"
        return tid if tid in ids else None

    def norm_trucks(self, items) -> list[str]:
        ids = self.truck_ids()
        out: list[str] = []
        for it in items or []:
            s = str(it).strip().lower()
            if s in ("all", "everyone", "all trucks", "*"):
                return list(ids)
            if s in ids:
                out.append(s)
                continue
            m = re.search(rf"({NUM_RE})\s*$", s)
            if m and (v := num_value(m.group(1))) is not None and (tid := self.truck_from_number(v)):
                out.append(tid)
        return list(dict.fromkeys(out))

    def site_id(self, name) -> str | None:
        if not name:
            return None
        s = norm(str(name))
        if str(name) in self.world.sites:
            return str(name)
        for sid in self.world.sites:
            if s == sid.lower() or s == sid.lower().replace("_", " "):
                return sid
        hits = self.find_sites(s)
        return hits[0] if hits else None

    def find_sites(self, text: str) -> list[str]:
        taken = [False] * len(text)
        found: list[tuple[int, str]] = []
        for alias, sid in self.alias_list:
            for m in re.finditer(rf"\b{re.escape(alias)}\b", text):
                if any(taken[m.start():m.end()]):
                    continue
                for i in range(m.start(), m.end()):
                    taken[i] = True
                found.append((m.start(), sid))
        return [sid for _, sid in sorted(found)]

    # ---- regex fallback
    def parse_regex(self, text: str) -> list[dict]:
        t = norm(text)
        segments = re.split(r"[.;!?]|\b(?:and|then|also)\s+(?=(?:trucks?\b|units?\b|all\b|everyone|everybody))", t)
        calls: list[dict] = []
        carry: list[str] = []
        for seg in segments:
            seg = (seg or "").strip(" ,")
            if not seg:
                continue
            call = self._parse_segment(seg, carry)
            if call:
                calls.append(call)
                carry = call["args"].get("trucks", carry)
        return calls

    def _segment_trucks(self, seg: str) -> tuple[list[str], bool]:
        ids = self.truck_ids()
        found: list[str] = []
        for m in LITERAL_ID_RE.finditer(seg):
            tid = f"truck-{m.group(1)}"
            if tid in ids:
                found.append(tid)
        seg2 = LITERAL_ID_RE.sub(" ", seg)
        for m in TRUCK_RE.finditer(seg2):
            for w in re.split(r"\s*(?:,|and|&|\+)\s*(?:trucks?\s*)?", m.group(1)):
                v = num_value(w)
                if v is not None and (tid := self.truck_from_number(v)):
                    found.append(tid)
        is_all = bool(ALL_RE.search(seg)) and not found
        return list(dict.fromkeys(found)), is_all

    def _parse_segment(self, seg: str, carry: list[str]) -> dict | None:
        trucks, is_all = self._segment_trucks(seg)
        ids = self.truck_ids()
        if is_all:
            trucks = list(ids)
        # strip truck references before finding sites/loads ("truck two" must not read as "two loads")
        body = TRUCK_RE.sub(" ", LITERAL_ID_RE.sub(" ", seg))
        sites = self.find_sites(body)
        types = [self.world.sites[s].type for s in sites]
        explicit = bool(trucks)
        if not trucks:
            trucks = carry

        if STOP_RE.search(body) and not HAUL_RE.search(body):
            return {"name": "stop", "args": {"trucks": trucks or list(ids)}}
        if STATUS_RE.search(body):
            return {"name": "status", "args": {"trucks": trucks or list(ids)}}
        ores = [s for s, ty in zip(sites, types) if ty == "ore"]
        dests = [s for s, ty in zip(sites, types) if ty in ("crusher", "dump")]
        if HAUL_RE.search(body) or (ores and dests and re.search(r"\bfrom\b", body)):
            loads = None
            if m := LOADS_RE.search(body):
                loads = num_value(m.group(1))
            src = ores[0] if ores else None
            dst = dests[0] if dests else None
            return {"name": "haul", "args": {"trucks": trucks, "from_site": src, "to_site": dst, "loads": loads}}
        if RETURN_RE.search(body) or (types and types[0] == "parking"):
            return {"name": "return_to_parking", "args": {"trucks": trucks or (list(ids) if not explicit else [])}}
        if sites:
            return {"name": "go_to", "args": {"trucks": trucks, "site": sites[0]}}
        return None

    # ---- LLM
    def tools_schema(self) -> list[dict]:
        ids = self.truck_ids()
        trucks = {"type": "array", "items": {"type": "string", "enum": ids + ["all"]},
                  "description": "truck ids, or [\"all\"]. Empty array = let dispatch pick an idle truck."}
        site_enum = list(self.world.sites)
        ore_enum = [s.id for s in self.world.sites.values() if s.type == "ore"]
        dest_enum = [s.id for s in self.world.sites.values() if s.type in ("crusher", "dump")]

        def fn(name, desc, props, req):
            return {"type": "function", "function": {"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": req}}}

        return [
            fn("go_to", "Send trucks to a site and stop there.", {"trucks": trucks, "site": {"type": "string", "enum": site_enum}}, ["trucks", "site"]),
            fn("haul", "Haul cycle: load at an ore face, dump at crusher or waste dump, repeat.",
               {"trucks": trucks, "from_site": {"type": "string", "enum": ore_enum},
                "to_site": {"type": "string", "enum": dest_enum},
                "loads": {"type": "integer", "description": "number of loads; omit for continuous"}},
               ["trucks", "from_site", "to_site"]),
            fn("return_to_parking", "Send trucks back to the ready line (parking).", {"trucks": trucks}, ["trucks"]),
            fn("stop", "Stop trucks immediately and cancel their tasks.", {"trucks": trucks}, ["trucks"]),
            fn("status", "Trucks report their status over the radio.", {"trucks": trucks}, ["trucks"]),
        ]

    def system_prompt(self) -> str:
        ids = self.truck_ids()
        tl = "\n".join(f"- {tid}: callsign \"{callsign(tid)}\"" for tid in ids)
        sl = "\n".join(f"- {s.id} ({s.type}): \"{s.name}\"; also called {', '.join(a for a in s.aliases[:8])}" for s in self.world.sites.values())
        return (
            "You are the dispatch parser for an autonomous open-pit mine radio. The operator speaks short radio "
            "commands; the text comes from speech-to-text and may contain mishearings (e.g. 'to'/'too' for 'two', "
            "'won' for 'one', 'red line' for 'ready line', 'phase' for 'face'). Convert each instruction into tool "
            "calls. Several instructions in one message -> several tool calls.\n\n"
            f"Trucks (callsign numbers start at One):\n{tl}\n'truck zero' also means truck-0. "
            "'all trucks', 'everyone', 'all units', 'fleet' -> [\"all\"].\n\n"
            f"Sites:\n{sl}\n\n"
            "Rules: 'haul X to Y' / 'cycle' / 'run loads' -> haul (default to_site = crusher). 'return', 'ready line', "
            "'park' -> return_to_parking. 'stop', 'hold', 'halt', 'all stop' -> stop. 'status', 'report', 'where are you' "
            "-> status (no trucks named = all). If nothing is a truck command, call no tool."
        )


# --------------------------------------------------------------------------- controller


@dataclass
class TruckCtl:
    id: str
    route: list[Waypoint] = field(default_factory=list)
    job: dict | None = None
    phase: str = "idle"  # idle | to_site | to_load | loading | to_dump | dumping | returning
    target: str | None = None  # site id of current leg
    dwell_until: float = 0.0
    goal: tuple[float, float] | None = None
    goal_set_at: float = 0.0
    near_since: float | None = None  # orbit detection: time we first came within ORBIT_M of the current waypoint
    dest: tuple[float, float] | None = None
    site_goal: str | None = None

    def task_text(self, world: World) -> str:
        if not self.job:
            return "standing by"
        tgt = world.sites[self.target].name if self.target else ""
        j = self.job
        if j["kind"] == "haul":
            n = f" ({j['done']}/{j['loads']})" if j.get("loads") else f" ({j['done']} done)"
            src, dst = world.sites[j["from"]].name, world.sites[j["to"]].name
            return {"to_load": f"en route to {src}", "loading": f"loading at {src}", "to_dump": f"hauling to {dst}",
                    "dumping": f"dumping at {dst}"}.get(self.phase, self.phase) + n
        return {"to_site": f"en route to {tgt}", "returning": "returning to ready line"}.get(self.phase, self.phase)


class Radio:
    def __init__(self) -> None:
        self.world = World(WORLD_PATH)
        self.world_mtime = WORLD_PATH.stat().st_mtime
        self.brain: RealBrain | MockBrain | None = None
        self.state: dict = {"trucks": []}
        self.brain_ok = False
        self.ctl: dict[str, TruckCtl] = {}
        self.events: deque = deque(maxlen=60)
        self.sockets: set[WebSocket] = set()
        self.parser = Parser(self.world, lambda: self.truck_ids())
        self.voices: dict[str, str] = {}
        self.http = httpx.AsyncClient(timeout=20.0)
        self.last_error: str | None = None
        self.tasks: list[asyncio.Task] = []
        self.lock = asyncio.Lock()
        self.brain_routing = False

    def update_routing(self) -> None:
        if BRAIN_ROUTING_ENV in ("1", "true", "yes"):
            self.brain_routing = isinstance(self.brain, RealBrain)
        elif BRAIN_ROUTING_ENV in ("0", "false", "no"):
            self.brain_routing = False
        else:
            routed = isinstance(self.brain, RealBrain) and brain_routes(self.state)
            if routed != self.brain_routing:
                log.info("routing: %s", "brain plans routes ({site} goals)" if routed else "radio chains {x,z} waypoints")
            self.brain_routing = routed

    def truck_ids(self) -> list[str]:
        ids = [t["id"] for t in self.state.get("trucks", [])]
        return sorted(ids) if ids else sorted(self.world.trucks) or ["truck-0", "truck-1", "truck-2"]

    def truck(self, tid: str) -> dict | None:
        return next((t for t in self.state.get("trucks", []) if t["id"] == tid), None)

    def c(self, tid: str) -> TruckCtl:
        if tid not in self.ctl:
            self.ctl[tid] = TruckCtl(tid)
        return self.ctl[tid]

    # ---- lifecycle
    async def start(self) -> None:
        real = RealBrain(BRAIN_URL)
        use_mock = MOCK_ENV in ("1", "true", "yes")
        if not use_mock:
            try:
                self.state = await real.state()
                self.brain, self.brain_ok = real, True
                log.info("brain reachable at %s (%d trucks)", BRAIN_URL, len(self.state.get("trucks", [])))
            except Exception as e:
                if MOCK_ENV in ("0", "false", "no"):
                    self.brain = real
                    log.warning("brain not reachable at %s (%s); RADIO_MOCK=0 so no mock", BRAIN_URL, e)
                else:
                    use_mock = True
                    log.warning("brain not reachable at %s (%s) -> MOCK MODE", BRAIN_URL, e)
        if use_mock:
            mb = MockBrain(self.world)
            mb.start()
            self.brain = mb
            self.state = await mb.state()
            self.brain_ok = True
        self.update_routing()
        self.tasks.append(asyncio.create_task(self.loop()))
        if MOCK_ENV == "auto" and self.brain.mode == "mock":
            self.tasks.append(asyncio.create_task(self.watch_for_brain(real)))
        if EL_KEY:
            self.tasks.append(asyncio.create_task(self.pick_voices()))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        if isinstance(self.brain, MockBrain):
            await self.brain.stop()

    async def watch_for_brain(self, real: RealBrain) -> None:
        while self.brain and self.brain.mode == "mock":
            await asyncio.sleep(5)
            try:
                st = await real.state()
            except Exception:
                continue
            log.info("brain came up at %s -> switching from mock to brain", BRAIN_URL)
            async with self.lock:
                old = self.brain
                self.ctl.clear()
                self.brain, self.state = real, st
                if isinstance(old, MockBrain):
                    await old.stop()
            await self.radio_event("Control", None, "Brain link up. Live trucks on the net.", kind="control")

    # ---- main loop: poll brain, advance waypoints, phases
    def maybe_reload_world(self) -> None:
        try:
            m = WORLD_PATH.stat().st_mtime
        except OSError:
            return
        if m != self.world_mtime:
            try:
                w = World(WORLD_PATH)
            except Exception as e:  # half-written file: try again next time
                log.warning("world reload failed: %s", e)
                return
            self.world, self.world_mtime = w, m
            self.parser = Parser(w, lambda: self.truck_ids())
            log.info("world.json changed -> reloaded (%d sites, %d road nodes)", len(w.sites), len(w.nodes))

    async def loop(self) -> None:
        fails = 0
        last_push = last_world = 0.0
        while True:
            t0 = time.monotonic()
            if t0 - last_world > 3.0:
                last_world = t0
                self.maybe_reload_world()
            try:
                self.state = await self.brain.state()
                self.brain_ok, fails = True, 0
                self.update_routing()
            except Exception as e:
                fails += 1
                if fails == 3:
                    log.warning("brain poll failing: %s", e)
                self.brain_ok = fails < 3
            if self.brain_ok:
                async with self.lock:
                    for t in list(self.state.get("trucks", [])):
                        try:
                            await self.tick_truck(t)
                        except Exception as e:  # never let one truck kill the loop
                            log.exception("tick %s: %s", t.get("id"), e)
            if t0 - last_push > 0.19:
                last_push = t0
                await self.broadcast(self.state_msg())
            await asyncio.sleep(max(0.0, 1 / POLL_HZ - (time.monotonic() - t0)))

    async def post_goal(self, ctl: TruckCtl, x: float, z: float) -> None:
        ctl.goal, ctl.goal_set_at = (x, z), time.monotonic()
        try:
            await self.brain.set_goal(ctl.id, round(x, 2), round(z, 2))
        except Exception as e:
            self.last_error = f"set_goal {ctl.id}: {e}"
            log.warning(self.last_error)

    async def post_leg(self, ctl: TruckCtl, site_id: str, goal: tuple[float, float]) -> None:
        ctl.goal, ctl.dest, ctl.site_goal, ctl.goal_set_at = None, goal, None, time.monotonic()
        known = brain_site_ids(self.state)
        try:
            if not known or site_id in known:
                try:
                    await self.brain.set_site(ctl.id, site_id)
                    ctl.site_goal = site_id
                    return
                except httpx.HTTPStatusError as e:
                    if not 400 <= e.response.status_code < 500:
                        raise
                    log.info("brain refused site goal %s for %s (%s); sending x,z", site_id, ctl.id, e.response.status_code)
            await self.brain.set_goal(ctl.id, round(goal[0], 2), round(goal[1], 2))
        except Exception as e:
            self.last_error = f"set_goal {ctl.id}: {e}"
            log.warning(self.last_error)

    async def yield_override(self, ctl: TruckCtl) -> None:
        ctl.route, ctl.job, ctl.phase, ctl.target, ctl.goal, ctl.dest, ctl.site_goal = [], None, "idle", None, None, None, None
        cs = callsign(ctl.id)
        await self.radio_event(cs, ctl.id, f"{cs}, manual override, releasing radio task.")

    async def tick_routed(self, ctl: TruckCtl, t: dict) -> None:
        pos = (t["x"], t["z"])
        if route := route_points(t.get("route")):
            ctl.dest = route[-1]
        settled = time.monotonic() - ctl.goal_set_at > OVERRIDE_GRACE_S
        gs = t.get("goal_site")
        if settled and ctl.site_goal and gs is not None and gs != ctl.site_goal:
            await self.yield_override(ctl)
            return
        d = math.dist(pos, ctl.dest)
        cleared = t.get("goal") is None and gs is None
        if d < ARRIVE_M or (settled and (t.get("status") == "arrived" or (cleared and d < 15.0))):
            ctl.dest = None
            await self.on_arrive(ctl, t)
        elif settled and cleared:
            await self.yield_override(ctl)

    def remaining_m(self, ctl: TruckCtl, t: dict) -> float | None:
        pos = (t["x"], t["z"])
        if ctl.dest:
            pts = route_points(t.get("route")) or [ctl.dest]
            return path_len(pos, [Waypoint(x, z) for x, z in pts])
        return path_len(pos, ctl.route) if ctl.route else None

    async def clear(self, ctl: TruckCtl) -> None:
        ctl.route, ctl.job, ctl.phase, ctl.target, ctl.dwell_until, ctl.goal = [], None, "idle", None, 0.0, None
        ctl.dest = ctl.site_goal = None
        ctl.goal_set_at = time.monotonic()
        try:
            await self.brain.clear_goal(ctl.id)
        except Exception as e:
            self.last_error = f"clear_goal {ctl.id}: {e}"
            log.warning(self.last_error)

    async def tick_truck(self, t: dict) -> None:
        ctl = self.c(t["id"])
        if ctl.dest:
            await self.tick_routed(ctl, t)
            return
        pos = (t["x"], t["z"])
        now = time.monotonic()
        # someone else (web UI click, team script) changed the goal -> yield
        if ctl.job and ctl.goal and now - ctl.goal_set_at > OVERRIDE_GRACE_S:
            g = t.get("goal")
            brain_arrived = g is None and math.dist(pos, ctl.goal) < BRAIN_CLEAR_M
            if not brain_arrived and (g is None or math.dist(g, ctl.goal) > 0.05):
                await self.yield_override(ctl)
                return
        if ctl.route:
            wp = ctl.route[0]
            d = math.dist(pos, (wp.x, wp.z))
            if d < ORBIT_M:
                ctl.near_since = ctl.near_since or now
            else:
                ctl.near_since = None
            reached = t.get("goal") is None and d < BRAIN_CLEAR_M
            orbiting = ctl.near_since is not None and now - ctl.near_since > ORBIT_S / max(MOCK_SPEED if self.brain.mode == "mock" else 1.0, 1.0)
            if wp.final:
                if d < ARRIVE_M or (t.get("status") == "arrived" and d < 6) or reached or orbiting:
                    if orbiting and d >= ARRIVE_M:
                        log.info("%s circling final waypoint at %.1f m -> holding here", ctl.id, d)
                        await self.post_goal(ctl, pos[0], pos[1])  # brain arrives instantly -> truck stops
                    ctl.route, ctl.near_since = [], None
                    await self.on_arrive(ctl, t)
                return
            passed = False
            if len(ctl.route) > 1:
                nx = ctl.route[1]
                vx, vz = nx.x - wp.x, nx.z - wp.z
                passed = d < 15 and ((pos[0] - wp.x) * vx + (pos[1] - wp.z) * vz) > 0
            if d < (BEND_ADVANCE_M if wp.bend else ADVANCE_M) or passed or reached or orbiting:
                ctl.route.pop(0)
                ctl.near_since = None
                nxt = ctl.route[0]
                await self.post_goal(ctl, nxt.x, nxt.z)
        elif ctl.dwell_until and now >= ctl.dwell_until:
            ctl.dwell_until = 0.0
            await self.on_dwell_done(ctl, t)

    async def drive_to(self, ctl: TruckCtl, site_id: str, point: tuple[float, float] | None = None) -> float:
        t = self.truck(ctl.id)
        site = self.world.sites[site_id]
        goal = point or site.point
        start = (t["x"], t["z"]) if t else goal
        wps = simplify(start, self.world.road_path(start, goal)) or [Waypoint(goal[0], goal[1], True, True)]
        ctl.target = site_id
        if self.brain_routing:
            ctl.route = []
            await self.post_leg(ctl, site_id, goal)
        else:
            ctl.route, ctl.dest, ctl.site_goal = wps, None, None
            await self.post_goal(ctl, wps[0].x, wps[0].z)
        return path_len(start, wps)

    def parking_point(self, tid: str) -> tuple[float, float]:
        park = self.world.by_type("parking")
        wt = self.world.trucks.get(tid)
        if park and park.slots:
            i = wt.get("slot", 0) if wt else (int(re.sub(r"\D", "", tid) or 0))
            return park.slots[i % len(park.slots)]
        return park.point if park else (0.0, 0.0)

    async def on_arrive(self, ctl: TruckCtl, t: dict) -> None:
        cs = callsign(ctl.id)
        j = ctl.job or {}
        kind = j.get("kind")
        if kind == "haul":
            if ctl.phase == "to_load":
                ctl.phase, ctl.dwell_until = "loading", time.monotonic() + LOAD_DWELL_S
                await self.radio_event(cs, ctl.id, f"{cs}, at {self.world.sites[j['from']].name}, loading.", spoken=False)
            elif ctl.phase == "to_dump":
                ctl.phase, ctl.dwell_until = "dumping", time.monotonic() + DUMP_DWELL_S
                await self.radio_event(cs, ctl.id, f"{cs}, tipping at {self.world.sites[j['to']].spoken}.", spoken=False)
            return
        site = self.world.sites.get(ctl.target or "")
        ctl.phase, ctl.job = "idle", None
        if kind == "park":
            await self.radio_event(cs, ctl.id, f"{cs}, parked on the ready line.")
        elif site:
            await self.radio_event(cs, ctl.id, f"{cs}, arrived {site.spoken}, holding.")

    async def on_dwell_done(self, ctl: TruckCtl, t: dict) -> None:
        cs = callsign(ctl.id)
        j = ctl.job
        if not j or j["kind"] != "haul":
            return
        src, dst = self.world.sites[j["from"]], self.world.sites[j["to"]]
        if ctl.phase == "loading":
            ctl.phase = "to_dump"
            await self.drive_to(ctl, dst.id)
            await self.radio_event(cs, ctl.id, f"{cs}, loaded, heading to {dst.spoken}.")
        elif ctl.phase == "dumping":
            j["done"] += 1
            if j.get("loads") and j["done"] >= j["loads"]:
                ctl.job = {"kind": "park"}
                ctl.phase = "returning"
                await self.drive_to(ctl, self.world.by_type("parking").id, self.parking_point(ctl.id))
                await self.radio_event(cs, ctl.id, f"{cs}, haul complete, {j['done']} {'load' if j['done'] == 1 else 'loads'} delivered, returning to the ready line.")
            else:
                ctl.phase = "to_load"
                await self.drive_to(ctl, src.id)
                await self.radio_event(cs, ctl.id, f"{cs}, load {j['done']} dumped, back to {src.name}.")

    # ---- commands
    async def parse(self, text: str) -> tuple[list[dict], str]:
        if OPENAI_KEY:
            try:
                calls = await self.parse_llm(text)
                return calls, "llm"
            except Exception as e:
                self.last_error = f"llm: {type(e).__name__}: {e}"
                log.warning("LLM parse failed, regex fallback: %s", self.last_error)
        return self.parser.parse_regex(text), "regex"

    async def parse_llm(self, text: str) -> list[dict]:
        body = {
            "model": OPENAI_MODEL,
            "temperature": 0,
            "messages": [{"role": "system", "content": self.parser.system_prompt()}, {"role": "user", "content": text}],
            "tools": self.parser.tools_schema(),
            "tool_choice": "auto",
        }
        r = await self.http.post(f"{OPENAI_BASE_URL}/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {OPENAI_KEY}"}, timeout=10.0)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            args = json.loads(tc["function"].get("arguments") or "{}")
            calls.append({"name": tc["function"]["name"], "args": args})
        return calls

    def pick_idle(self, n: int = 1) -> list[str]:
        idle = [tid for tid in self.truck_ids() if not self.c(tid).job]
        return (idle or self.truck_ids())[:n]

    async def execute(self, calls: list[dict]) -> tuple[list[dict], list[tuple[str, str | None, str]]]:
        actions, replies = [], []
        async with self.lock:
            for call in calls:
                name, a = call["name"], call.get("args") or {}
                trucks = self.parser.norm_trucks(a.get("trucks"))
                if not trucks and name in ("stop", "status"):
                    trucks = self.truck_ids()
                if not trucks and name == "return_to_parking":
                    trucks = self.truck_ids()
                if not trucks:
                    trucks = self.pick_idle()
                act = {"tool": name, "trucks": trucks, "args": {k: v for k, v in a.items() if k != "trucks"}}
                actions.append(act)
                for tid in trucks:
                    ctl, cs = self.c(tid), callsign(tid)
                    if name == "stop":
                        await self.clear(ctl)
                        replies.append((cs, tid, f"{cs}, stopped."))
                    elif name == "status":
                        replies.append((cs, tid, self.status_text(tid)))
                    elif name == "return_to_parking":
                        await self.clear_job(ctl)
                        ctl.job, ctl.phase = {"kind": "park"}, "returning"
                        d = await self.drive_to(ctl, self.world.by_type("parking").id, self.parking_point(tid))
                        replies.append((cs, tid, f"Copy, {cs} returning to the ready line."))
                        act["route_m"] = round(d)
                    elif name == "go_to":
                        sid = self.parser.site_id(a.get("site"))
                        if not sid:
                            replies.append((cs, tid, f"{cs}, say again destination."))
                            act["error"] = "unknown site"
                            continue
                        await self.clear_job(ctl)
                        site = self.world.sites[sid]
                        if site.type == "parking":
                            ctl.job, ctl.phase = {"kind": "park"}, "returning"
                            d = await self.drive_to(ctl, sid, self.parking_point(tid))
                            replies.append((cs, tid, f"Copy, {cs} returning to the ready line."))
                        else:
                            ctl.job, ctl.phase = {"kind": "goto", "site": sid}, "to_site"
                            d = await self.drive_to(ctl, sid)
                            replies.append((cs, tid, f"Copy, {cs} heading to {site.spoken}."))
                        act["route_m"] = round(d)
                    elif name == "haul":
                        src = self.parser.site_id(a.get("from_site"))
                        dst = self.parser.site_id(a.get("to_site")) or (self.world.by_type("crusher").id)
                        if not src or self.world.sites[src].type != "ore":
                            last = (ctl.job or {}).get("from")
                            src = last or next(s.id for s in self.world.sites.values() if s.type == "ore")
                        loads = a.get("loads")
                        try:
                            loads = int(loads) if loads else None
                        except (TypeError, ValueError):
                            loads = None
                        await self.clear_job(ctl)
                        ctl.job = {"kind": "haul", "from": src, "to": dst, "loads": loads, "done": 0}
                        ctl.phase = "to_load"
                        d = await self.drive_to(ctl, src)
                        sname, dname = self.world.sites[src].name, self.world.sites[dst].spoken
                        n = f", {loads} {'load' if loads == 1 else 'loads'}" if loads else ""
                        replies.append((cs, tid, f"Copy, {cs} hauling {sname} to {dname}{n}."))
                        act["args"].update(from_site=src, to_site=dst, loads=loads)
                        act["route_m"] = round(d)
                    else:
                        act["error"] = "unknown tool"
        return actions, replies

    async def clear_job(self, ctl: TruckCtl) -> None:
        ctl.route, ctl.job, ctl.phase, ctl.target, ctl.dwell_until = [], None, "idle", None, 0.0
        ctl.dest = ctl.site_goal = None

    def status_text(self, tid: str) -> str:
        cs, t, ctl = callsign(tid), self.truck(tid), self.c(tid)
        if not t:
            return f"{cs}, no position."
        pos = (t["x"], t["z"])
        if ctl.job:
            rem = self.remaining_m(ctl, t)
            txt = ctl.task_text(self.world)
            if rem is not None:
                return f"{cs}, {txt}, {rem:.0f} meters out."
            return f"{cs}, {txt}."
        near = min(self.world.sites.values(), key=lambda s: math.dist(pos, s.point))
        if math.dist(pos, near.point) < 20:
            return f"{cs}, standing by at {near.spoken}."
        return f"{cs}, standing by."

    async def release(self) -> None:
        async with self.lock:
            for tid in self.truck_ids():
                await self.clear(self.c(tid))

    # ---- radio events + TTS
    async def radio_event(self, frm: str, truck_id: str | None, text: str, kind: str = "truck", spoken: bool = True,
                          audio: bool = True) -> dict:
        ev = {"type": "radio", "id": uuid.uuid4().hex[:12], "from": frm, "truck_id": truck_id, "text": text,
              "kind": kind, "audio_url": None, "spoken": spoken, "t": time.time()}
        if audio and spoken and kind != "operator":
            ev["audio_url"] = await self.tts(text, truck_id or "dispatch")
        self.events.append(ev)
        await self.broadcast(ev)
        return ev

    async def pick_voices(self) -> None:
        override = [v.strip() for v in os.environ.get("ELEVENLABS_VOICE_IDS", "").split(",") if v.strip()]
        keys = ["dispatch"] + self.truck_ids()
        if override:
            self.voices = {k: override[i % len(override)] for i, k in enumerate(keys)}
            return
        fallback = {"Rachel": "21m00Tcm4TlvDq8ikWAM", "Adam": "pNInz6obpgDQGcFmaJgB", "Antoni": "ErXwobaYiN019PkySvjV",
                    "Arnold": "VR6AewLTigWG4xSOukaG", "Josh": "TxGEqnHWrfWFTfGW9XTX"}
        avail: dict[str, str] = {}
        try:
            r = await self.http.get(f"{EL_BASE}/v1/voices", headers={"xi-api-key": EL_KEY})
            r.raise_for_status()
            for v in r.json().get("voices", []):
                if v.get("category") in ("premade", None, "default"):
                    avail.setdefault(v["name"].split()[0].strip(" -"), v["voice_id"])
        except Exception as e:
            log.warning("could not list ElevenLabs voices (%s); using known premade ids", e)
        if not avail:
            avail = fallback
        female = ["Rachel", "Sarah", "Matilda", "Alice", "Laura", "Jessica", "Charlotte", "Lily", "Aria"]
        male = ["Adam", "Arnold", "Antoni", "Josh", "Callum", "George", "Brian", "Daniel", "Bill", "Eric", "Chris",
                "Liam", "Roger", "Will", "Charlie", "Sam"]
        disp = next((avail[n] for n in female if n in avail), next(iter(avail.values())))
        truck_voices = [avail[n] for n in male if n in avail and avail[n] != disp]
        truck_voices += [v for v in avail.values() if v != disp and v not in truck_voices]
        self.voices = {"dispatch": disp}
        for i, tid in enumerate(sorted(set(self.truck_ids()) | {f"truck-{k}" for k in range(3)})):
            self.voices[tid] = truck_voices[i % len(truck_voices)] if truck_voices else disp
        names = {v: n for n, v in avail.items()}
        log.info("voices: %s", {k: names.get(v, v[:6]) for k, v in self.voices.items()})

    async def tts(self, text: str, speaker: str) -> str | None:
        if not EL_KEY:
            return None
        if not self.voices:
            await self.pick_voices()
        voice = self.voices.get(speaker) or self.voices.get("dispatch")
        if not voice:
            return None
        key = hashlib.sha1(f"{voice}|{EL_TTS_MODEL}|{text}".encode()).hexdigest()[:20]
        path = CACHE_DIR / f"{key}.mp3"
        if path.exists():
            return f"/api/radio/audio/{key}.mp3"
        if not LIMITS["tts"].allow("*"):
            log.warning("tts rate limit reached; reply goes out without audio")
            return None
        try:
            r = await self.http.post(
                f"{EL_BASE}/v1/text-to-speech/{voice}",
                params={"output_format": "mp3_44100_64"},
                headers={"xi-api-key": EL_KEY, "accept": "audio/mpeg"},
                json={"text": text, "model_id": EL_TTS_MODEL,
                      "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "speed": 1.08}},
                timeout=15.0,
            )
            r.raise_for_status()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return f"/api/radio/audio/{key}.mp3"
        except Exception as e:
            detail = getattr(getattr(e, "response", None), "text", "")[:200]
            self.last_error = f"tts: {e} {detail}"
            log.warning(self.last_error)
            return None

    async def transcribe(self, data: bytes, filename: str, ctype: str) -> str:
        if EL_KEY:
            last = None
            for model in dict.fromkeys([EL_STT_MODEL, "scribe_v1"]):
                r = await self.http.post(
                    f"{EL_BASE}/v1/speech-to-text", headers={"xi-api-key": EL_KEY},
                    data={"model_id": model, "language_code": "en", "tag_audio_events": "false"},
                    files={"file": (filename, data, ctype)}, timeout=30.0)
                if r.status_code == 200:
                    return (r.json().get("text") or "").strip()
                last = f"ElevenLabs STT {model}: {r.status_code} {r.text[:200]}"
                log.warning(last)
            raise HTTPException(502, last)
        if OPENAI_KEY:
            r = await self.http.post(
                f"{OPENAI_BASE_URL}/audio/transcriptions", headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                data={"model": os.environ.get("OPENAI_STT_MODEL", "whisper-1"), "language": "en"},
                files={"file": (filename, data, ctype)}, timeout=30.0)
            if r.status_code == 200:
                return (r.json().get("text") or "").strip()
            raise HTTPException(502, f"OpenAI STT: {r.status_code} {r.text[:200]}")
        raise HTTPException(503, "no ELEVENLABS_API_KEY (or OPENAI_API_KEY) for speech-to-text; type the command instead")

    # ---- websocket
    def truck_view(self, t: dict) -> dict:
        ctl = self.c(t["id"])
        return {
            "id": t["id"], "callsign": callsign(t["id"]), "x": round(t["x"], 2), "z": round(t["z"], 2),
            "yaw": round(t.get("yaw", 0), 1), "speed": round(t.get("speed", 0), 2), "status": t.get("status"),
            "goal": t.get("goal"), "phase": ctl.phase, "task": ctl.task_text(self.world),
            "target": self.world.sites[ctl.target].name if ctl.target else None,
            "remaining_m": None if (rem := self.remaining_m(ctl, t)) is None else round(rem),
            "route": [[round(x, 1), round(z, 1)] for x, z in
                      (route_points(t.get("route")) if ctl.dest else [(w.x, w.z) for w in ctl.route])],
        }

    def state_msg(self) -> dict:
        return {"type": "state", "t": time.time(), "mode": self.brain.mode if self.brain else None,
                "brain_ok": self.brain_ok, "sim_connected": self.state.get("sim_connected"),
                "trucks": [self.truck_view(t) for t in self.state.get("trucks", [])]}

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self.sockets):
            try:
                await asyncio.wait_for(ws.send_json(msg), 1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    def health(self) -> dict:
        return {
            "ok": True, "mode": self.brain.mode if self.brain else None, "mock": bool(self.brain and self.brain.mode == "mock"),
            "brain_url": BRAIN_URL, "brain_ok": self.brain_ok, "sim_connected": self.state.get("sim_connected"),
            "world": self.world.raw.get("id"), "trucks": self.truck_ids(),
            "stt": bool(EL_KEY or OPENAI_KEY), "stt_provider": "elevenlabs" if EL_KEY else ("openai" if OPENAI_KEY else None),
            "tts": bool(EL_KEY), "tts_model": EL_TTS_MODEL if EL_KEY else None,
            "llm": bool(OPENAI_KEY), "llm_model": OPENAI_MODEL if OPENAI_KEY else None,
            "voices_ready": bool(self.voices), "last_error": self.last_error,
            "routing": "brain" if self.brain_routing else "radio", "auth_required": bool(RADIO_PIN),
        }

    def public_health(self) -> dict:
        h = self.health()
        return {k: h[k] for k in ("ok", "mode", "brain_ok", "sim_connected", "stt", "tts", "llm", "routing", "auth_required")}


# --------------------------------------------------------------------------- app


class Limiter:
    def __init__(self, per_min: int):
        self.per_min = per_min
        self.hits: dict[str, deque] = {}

    def room(self, key: str, now: float) -> bool:
        if self.per_min <= 0:
            return True
        q = self.hits.get(key)
        while q and now - q[0] >= 60.0:
            q.popleft()
        if q is not None and not q:
            del self.hits[key]
            q = None
        return q is None or len(q) < self.per_min

    def hit(self, key: str, now: float) -> None:
        if self.per_min > 0:
            self.hits.setdefault(key, deque()).append(now)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if not self.room(key, now):
            return False
        self.hit(key, now)
        return True


class RateGate:
    def __init__(self, per_client: int, per_all: int):
        self.client, self.all = Limiter(per_client), Limiter(per_all)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if not (self.client.room(key, now) and self.all.room("*", now)):
            return False
        self.client.hit(key, now)
        self.all.hit("*", now)
        return True


LIMITS = {
    "stt": RateGate(env_int("RADIO_STT_PER_MIN", 10), env_int("RADIO_STT_GLOBAL_PER_MIN", 30)),
    "command": RateGate(env_int("RADIO_COMMAND_PER_MIN", 20), env_int("RADIO_COMMAND_GLOBAL_PER_MIN", 60)),
    "login": RateGate(env_int("RADIO_LOGIN_PER_MIN", 5), env_int("RADIO_LOGIN_GLOBAL_PER_MIN", 20)),
    "tts": Limiter(env_int("RADIO_TTS_GLOBAL_PER_MIN", 60)),
}
SESSIONS: dict[str, float] = {}


def client_ip(request: Request | WebSocket) -> str:
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "?")


def session_of(cookies) -> str | None:
    tok = cookies.get(SESSION_COOKIE) or ""
    exp = SESSIONS.get(tok)
    return tok if exp and exp > time.time() else None


def requester(request: Request) -> str:
    if not RADIO_PIN:
        return "ip:" + client_ip(request)
    tok = session_of(request.cookies)
    if not tok:
        raise HTTPException(401, "access code required")
    return "s:" + tok


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def limit(kind: str, who: str) -> None:
    if not LIMITS[kind].allow(who):
        raise HTTPException(429, f"{kind} rate limit, try again in a minute")


radio = Radio()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await radio.start()
    yield
    await radio.stop()


app = FastAPI(title="Haul truck radio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# Aerial 3D view for the laptop/projector: /view -> envgen/preview (live mode follows this radio's truck state)
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

for _url, _dir in (
    ("/envgen/preview", REPO / "envgen" / "preview"),
    ("/data", REPO / "data"),
    ("/assets", REPO / "assets"),
    ("/unity/Assets/Models/Trucks", REPO / "unity" / "Assets" / "Models" / "Trucks"),
):
    if _dir.is_dir():
        app.mount(_url, StaticFiles(directory=_dir), name=_url.strip("/").replace("/", "_"))


@app.get("/view")
def view():
    return RedirectResponse("/envgen/preview/index.html?live=1")


class Command(BaseModel):
    text: str


class Login(BaseModel):
    pin: str


@app.get("/")
def index():
    return FileResponse(WEB_INDEX, headers={"Cache-Control": "no-store"})


@app.get("/api/radio/health")
def health(request: Request):
    if RADIO_PIN and not session_of(request.cookies):
        return radio.public_health()
    return radio.health()


@app.post("/api/radio/login")
def login(body: Login, request: Request, response: Response):
    if not RADIO_PIN:
        return {"ok": True, "auth_required": False}
    limit("login", client_ip(request))
    if not hmac.compare_digest(body.pin.strip().encode(), RADIO_PIN.encode()):
        raise HTTPException(401, "wrong access code")
    now = time.time()
    for tok in [t for t, exp in SESSIONS.items() if exp <= now]:
        del SESSIONS[tok]
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = now + SESSION_TTL_S
    response.set_cookie(SESSION_COOKIE, tok, max_age=int(SESSION_TTL_S), httponly=True, secure=is_https(request),
                        samesite="strict", path="/")
    return {"ok": True}


@app.get("/api/radio/state")
def state(who: str = Depends(requester)):
    return dict(radio.state_msg(), log=list(radio.events)[-15:])


@app.get("/api/radio/world")
def world(who: str = Depends(requester)):
    w = radio.world
    return {"id": w.raw.get("id"), "bounds": w.bounds, "roads": w.road_lines,
            "sites": [{"id": s.id, "type": s.type, "name": s.name, "x": s.point[0], "z": s.point[1]} for s in w.sites.values()]}


AUDIO_TYPES = {"audio/webm": "webm", "video/webm": "webm", "audio/mp4": "m4a", "video/mp4": "m4a", "audio/x-m4a": "m4a",
               "audio/aac": "aac", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3"}


@app.post("/api/radio/transcribe")
async def transcribe(audio: UploadFile = File(...), who: str = Depends(requester)):
    ctype = (audio.content_type or "").split(";")[0].strip().lower()
    if ctype not in AUDIO_TYPES:
        raise HTTPException(415, "unsupported audio type")
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, f"recording too long (max {MAX_AUDIO_BYTES // 1024} KB)")
    if len(data) < 800:
        return {"text": "", "note": "recording too short"}
    limit("stt", who)
    t0 = time.monotonic()
    text = await radio.transcribe(data, f"ptt.{AUDIO_TYPES[ctype]}", ctype)
    return {"text": text, "ms": round((time.monotonic() - t0) * 1000)}


@app.post("/api/radio/command")
async def command(cmd: Command, who: str = Depends(requester)):
    text = cmd.text.strip()
    if not text:
        raise HTTPException(400, "empty command")
    if len(text) > MAX_COMMAND_CHARS:
        raise HTTPException(413, f"command too long (max {MAX_COMMAND_CHARS} characters)")
    limit("command", who)
    t0 = time.monotonic()
    await radio.radio_event("Dispatch", None, text, kind="operator", audio=False)
    calls, parser = await radio.parse(text)
    actions, replies = await radio.execute(calls)
    if not replies:
        replies = [("Control", None, "Control, say again, over.")]
    evs = await asyncio.gather(*[radio.radio_event(frm, tid, txt, kind="control" if tid is None else "truck")
                                 for frm, tid, txt in replies])
    return {
        "heard": text, "parser": parser, "actions": actions,
        "replies": [{"id": e["id"], "truck_id": e["truck_id"], "callsign": e["from"], "text": e["text"],
                     "audio_url": e["audio_url"]} for e in evs],
        "ms": round((time.monotonic() - t0) * 1000),
    }


@app.post("/api/radio/release")
async def release(who: str = Depends(requester)):
    await radio.release()
    await radio.radio_event("Control", None, "All radio tasks released. Trucks holding.", kind="control")
    return {"ok": True}


@app.get("/api/radio/audio/{name}")
def audio(name: str, who: str = Depends(requester)):
    if not re.fullmatch(r"[0-9a-f]{20}\.mp3", name):
        raise HTTPException(404)
    p = CACHE_DIR / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


WS_MAX_MSG = 512


@app.websocket("/ws/radio")
async def ws_radio(ws: WebSocket):
    await ws.accept()
    if RADIO_PIN and not session_of(ws.cookies):
        await ws.close(code=4401)
        return
    radio.sockets.add(ws)
    try:
        await ws.send_json({"type": "hello", "health": radio.health(), "log": list(radio.events)})
        await ws.send_json(radio.state_msg())
        while True:
            msg = await ws.receive_text()
            if len(msg) > WS_MAX_MSG:
                await ws.close(code=1009)
                break
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("type") == "ping":
                t = m.get("t")
                await ws.send_json({"type": "pong", "t": t if isinstance(t, (int, float)) else None})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        radio.sockets.discard(ws)


@app.exception_handler(HTTPException)
async def http_exc(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def any_exc(_, exc: Exception):
    log.exception("unhandled: %s", exc)
    return JSONResponse({"error": "internal error"}, status_code=500)


def lan_ip() -> str:
    if ip := os.environ.get("RADIO_LAN_IP"):
        return ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def self_signed_cert() -> tuple[str, str]:
    d = RADIO_DIR / "secrets" / "tls"
    cert, key, stamp = d / "cert.pem", d / "key.pem", d / "san.txt"
    ip = lan_ip()
    if not (cert.is_file() and key.is_file() and stamp.is_file() and stamp.read_text() == ip):
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
                        "-subj", "/CN=haul-radio", "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        key.chmod(0o600)
        stamp.write_text(ip)
    return str(cert), str(key)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        import websockets  # noqa: F401
    except ImportError:
        try:
            import wsproto  # noqa: F401
        except ImportError:
            log.warning("no websocket library: /ws/radio disabled, phone falls back to HTTP polling. "
                        "Use --with 'uvicorn[standard]' (see radio/run.sh) for websockets.")
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", 8090))
    tls: dict = {}
    cert, key = os.environ.get("SSL_CERTFILE"), os.environ.get("SSL_KEYFILE")
    if not cert and os.environ.get("RADIO_TLS", "").lower() in ("1", "true", "yes"):
        cert, key = self_signed_cert()
    if cert:
        tls = {"ssl_certfile": cert, "ssl_keyfile": key}
        log.info("HTTPS on https://%s:%d (self-signed: accept the browser warning once)",
                 lan_ip() if host == "0.0.0.0" else host, port)
    if RADIO_PIN:
        log.info("access code required (RADIO_PIN)")
    uvicorn.run(app, host=host, port=port, log_level="info", ws_max_size=64 * 1024, server_header=False, **tls)
