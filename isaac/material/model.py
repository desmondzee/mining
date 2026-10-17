import math
from dataclasses import dataclass, fields

import numpy as np

from . import rig as R
from .geometry import BedShape, BucketShape

GRAVITY = 9.81
UP = np.array([0.0, 0.0, 1.0])
BED_PAD = 0.25
LEVEL_ITERS = 14
CHUNKS_BUCKET = 8
CHUNKS_BED = 14
CHUNK_MIN_DEPTH = 0.12
BED_CHUNK_MIN_DEPTH = 0.2
DUST_LIFE = 2.2
GROUND_BELOW_PIN = 4.0
INHERIT = 0.25
MAX_THROW = 5.0
CARRY_PHASES = np.arange(0.36, 0.61, 0.02)
DUST_RISE = 0.35
DUST_GROW = (0.6, 3.2)


@dataclass
class Config:
    payload_t: float = 55.0
    passes: int = 4
    density: float = 1.8
    repose_deg: float = 37.0
    pour_s: float = 0.9
    cohesion: float = 0.3
    fill_lo: float = 0.92
    fill_hi: float = 1.06
    breakout: float = 0.02
    max_particles: int = 768
    particles_per_t: float = 14.0
    chunk_min: float = 0.07
    chunk_max: float = 0.3
    pile_passes: float = 3.0
    relax_iters: int = 8
    settle_s: float = 1.5
    empty_s: float = 2.5
    empty_accel: float = 3.0
    dump_radius: float = 30.0
    stop_speed: float = 0.3
    stop_s: float = 1.5
    max_dust: int = 0
    dust_every: float = 0.12
    seed: int = 0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Config":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    @property
    def pass_t(self) -> float:
        return self.payload_t / self.passes


def smootherstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6 - 15) + 10)


def as_matrix(m) -> np.ndarray:
    a = np.asarray(m, float)
    if a.shape != (4, 4):
        raise ValueError("expected a 4x4 matrix")
    return a if np.allclose(a[3], [0.0, 0.0, 0.0, 1.0]) else a.T


def quat_from_rotations(r: np.ndarray) -> np.ndarray:
    m = r / np.linalg.norm(r, axis=1, keepdims=True)
    tr = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    w = np.sqrt(np.maximum(0.0, 1 + tr)) / 2
    x = np.copysign(np.sqrt(np.maximum(0.0, 1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2])) / 2, m[..., 2, 1] - m[..., 1, 2])
    y = np.copysign(np.sqrt(np.maximum(0.0, 1 - m[..., 0, 0] + m[..., 1, 1] - m[..., 2, 2])) / 2, m[..., 0, 2] - m[..., 2, 0])
    z = np.copysign(np.sqrt(np.maximum(0.0, 1 - m[..., 0, 0] - m[..., 1, 1] + m[..., 2, 2])) / 2, m[..., 1, 0] - m[..., 0, 1])
    q = np.stack([w, x, y, z], axis=-1)
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def random_quats(rng: np.random.Generator, n: int) -> np.ndarray:
    q = rng.normal(size=(n, 4))
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def relax(height: np.ndarray, floor: np.ndarray, cell: float, slope: float, iters: int) -> np.ndarray:
    h = height
    limit = slope * cell
    for _ in range(iters):
        for axis in (2, 1):
            s = h + floor
            n = h.shape[axis]
            a = [slice(None)] * 3
            b = [slice(None)] * 3
            a[axis], b[axis] = slice(1, n), slice(0, n - 1)
            a, b = tuple(a), tuple(b)
            d = s[a] - s[b]
            q = np.maximum(np.abs(d) - limit, 0.0) * 0.25
            src = np.where(d > 0, h[a], h[b])
            q = np.minimum(q, src * 0.25) * np.sign(d)
            h[a] -= q
            h[b] += q
    return h


def deposit_kernel(h: np.ndarray, iv: np.ndarray, iu: np.ndarray, vol: np.ndarray) -> None:
    nv, nu = h.shape
    w = np.array([0.25, 0.5, 0.25])
    total = np.zeros(len(vol))
    parts = []
    for dv in (-1, 0, 1):
        for du in (-1, 0, 1):
            jv, ju = iv + dv, iu + du
            ok = (jv >= 0) & (jv < nv) & (ju >= 0) & (ju < nu)
            wt = w[dv + 1] * w[du + 1] * ok
            total += wt
            parts.append((np.clip(jv, 0, nv - 1), np.clip(ju, 0, nu - 1), wt))
    for jv, ju, wt in parts:
        np.add.at(h, (jv, ju), vol * wt / total)


class Particles:
    def __init__(self, n: int, rng: np.random.Generator, cfg: Config) -> None:
        self.pos = np.zeros((n, 3))
        self.vel = np.zeros((n, 3))
        self.mass = np.zeros(n)
        self.alive = np.zeros(n, bool)
        self.src = np.zeros(n, int)
        self.kind = np.zeros(n, int)
        self.scale = rng.uniform(cfg.chunk_min, cfg.chunk_max, n) * np.where(rng.random(n) < 0.15, 1.6, 1.0)
        self.quat = random_quats(rng, n)
        self.spin = rng.normal(0, 4.0, (n, 3))
        self.next = 0

    def spawn(self, pos: np.ndarray, vel: np.ndarray, mass: float, src: int, kind: int) -> None:
        k = len(pos)
        if k == 0 or mass <= 0:
            return
        free = np.flatnonzero(~self.alive)
        if len(free) < k:
            same = np.flatnonzero(self.alive & (self.src == src) & (self.kind == kind))
            pool = same if len(same) else np.flatnonzero(self.alive)
            take = len(free)
            if take:
                self.spawn(pos[:take], vel[:take], mass * take / k, src, kind)
            self.mass[pool] += mass * (k - take) / k / len(pool)
            return
        idx = free[:k]
        self.pos[idx], self.vel[idx] = pos, vel
        self.mass[idx] = mass / k
        self.alive[idx] = True
        self.src[idx], self.kind[idx] = src, kind

    def total(self) -> float:
        return float(self.mass[self.alive].sum())


class Model:
    def __init__(self, bucket: BucketShape, bed: BedShape, excavators: list[dict], trucks: list[dict], config: dict | None = None,
                 sites: list[tuple[float, float, float]] | None = None) -> None:
        self.cfg = cfg = Config.from_dict(config)
        self.rng = np.random.default_rng(cfg.seed)
        self.bucket, self.bed = bucket, bed
        self.exc_ids = [e["id"] for e in excavators]
        self.truck_ids = [t["id"] for t in trucks]
        self.sites = list(sites or [])
        e, t = len(self.exc_ids), len(self.truck_ids)
        self.ground = np.array([float(x.get("ground", np.nan)) for x in excavators])
        self.mass = np.zeros(e)
        self.target = np.zeros(e)
        self.phase = ["hold"] * e
        self.matrix = np.tile(np.eye(4), (e, 1, 1))
        self.seen = np.zeros(e, bool)
        self.flowing = np.zeros(e, bool)
        self.surface = np.repeat(bucket.bottom[None], e, axis=0)
        ups = np.array([np.linalg.solve(R.chain(R.pose(u))["bucket"][:3, :3], [0.0, 1.0, 0.0]) for u in CARRY_PHASES])
        ups /= np.linalg.norm(ups, axis=1, keepdims=True)
        self.full = float(self.level(ups, np.full(len(ups), np.inf))[1].min())
        self.pile_pos = np.full((e, 3), np.nan)
        self.pile = np.zeros(e)
        self.truck_matrix = np.tile(np.eye(4), (t, 1, 1))
        self.truck_prev = self.truck_matrix.copy()
        self.truck_speed = np.zeros(t)
        self.truck_seen = np.zeros(t, bool)
        self.under = [None] * t
        self.height = np.zeros((t, *bed.shape))
        self.active = np.zeros(t)
        self.stopped = np.zeros(t)
        self.emptying = np.full(t, -1.0)
        self.source = np.full(t, -1)
        self.dug = 0.0
        self.delivered = np.zeros(t)
        self.parts = Particles(cfg.max_particles, self.rng, cfg)
        cols = np.flatnonzero((np.abs(bucket.x) < np.abs(bucket.x).max() * 0.8) & (bucket.dome > 0.08))
        self.bucket_chunks = self.rng.choice(cols, (e, CHUNKS_BUCKET))
        self.bucket_chunk_scale = self.rng.uniform(0.12, 0.3, (e, CHUNKS_BUCKET))
        self.bucket_chunk_quat = random_quats(self.rng, e * CHUNKS_BUCKET).reshape(e, CHUNKS_BUCKET, 4)
        nv, nu = bed.shape
        self.bed_chunks = np.stack([self.rng.integers(1, nv - 1, (t, CHUNKS_BED)), self.rng.integers(1, nu - 1, (t, CHUNKS_BED))], -1)
        self.bed_chunk_scale = self.rng.uniform(0.15, 0.42, (t, CHUNKS_BED))
        self.bed_chunk_quat = random_quats(self.rng, t * CHUNKS_BED).reshape(t, CHUNKS_BED, 4)
        self.dust_pos = np.zeros((cfg.max_dust, 3))
        self.dust_age = np.full(cfg.max_dust, DUST_LIFE)
        self.dust_size = np.ones(cfg.max_dust)
        self.dust_clock = np.zeros(e + t)
        self.time = 0.0
        self.slope = math.tan(math.radians(cfg.repose_deg))

    def exc_index(self, ident: str) -> int:
        return self.exc_ids.index(ident)

    def truck_index(self, ident: str) -> int:
        return self.truck_ids.index(ident)

    def load(self, ident: str) -> float:
        return self.tonnes(ident) / self.cfg.payload_t

    def tonnes(self, ident: str) -> float:
        k = self.truck_index(ident)
        return float(self.height[k].sum() * self.bed.cell**2 * self.cfg.density)

    def bucket_tonnes(self, ident: str) -> float:
        return float(self.mass[self.exc_index(ident)])

    def audit(self) -> dict:
        beds = float(self.height.sum() * self.bed.cell**2 * self.cfg.density)
        piles = float(self.pile.sum())
        held = float(self.mass.sum()) + self.parts.total() + beds + piles
        return {"dug": self.dug, "delivered": float(self.delivered.sum()), "bucket": float(self.mass.sum()), "air": self.parts.total(),
                "beds": beds, "piles": piles, "balance": self.dug - float(self.delivered.sum()) - held}

    def empty_truck(self, ident: str) -> None:
        k = self.truck_index(ident)
        if self.emptying[k] < 0 and self.height[k].sum() > 0:
            self.emptying[k] = 0.0

    def update(self, dt: float, excavators: list, trucks: list) -> None:
        if dt <= 0:
            return
        self.time += dt
        self.update_trucks(dt, trucks)
        self.update_buckets(dt, excavators)
        self.update_particles(dt)
        self.update_beds(dt)
        self.update_dust(dt)

    def update_trucks(self, dt: float, trucks: list) -> None:
        self.truck_prev = self.truck_matrix.copy()
        for item in trucks:
            ident, m = item[0], as_matrix(item[1])
            k = self.truck_index(ident)
            self.under[k] = item[2] if len(item) > 2 else None
            if not self.truck_seen[k]:
                self.truck_prev[k] = m
            self.truck_speed[k] = np.linalg.norm(m[:3, 3] - self.truck_prev[k][:3, 3]) / dt
            self.truck_matrix[k] = m
            self.truck_seen[k] = True
        for k in range(len(self.truck_ids)):
            near = any(math.hypot(self.truck_matrix[k][0, 3] - x, self.truck_matrix[k][1, 3] - y) < r for x, y, r in self.sites)
            self.stopped[k] = self.stopped[k] + dt if near and self.truck_speed[k] < self.cfg.stop_speed else 0.0
            if self.stopped[k] >= self.cfg.stop_s and self.load(self.truck_ids[k]) > 0.02:
                self.empty_truck(self.truck_ids[k])

    def update_buckets(self, dt: float, excavators: list) -> None:
        cfg = self.cfg
        prev = self.matrix.copy()
        support = np.zeros(len(self.exc_ids))
        for item in excavators:
            ident, m, phase = item[0], as_matrix(item[1]), item[2]
            t = float(item[3]) if len(item) > 3 else 0.0
            if not isinstance(phase, str):
                phase, t = R.phase_name(float(phase))
            e = self.exc_index(ident)
            if not self.seen[e]:
                prev[e] = m
                self.seen[e] = True
            if not np.isfinite(self.ground[e]):
                self.ground[e] = m[2, 3] - GROUND_BELOW_PIN
            last = self.phase[e]
            self.matrix[e] = m
            self.phase[e] = phase
            if phase == "dig":
                support[e] = 1.0 - float(smootherstep((t - 0.85) / 0.15))
                if last != "dig":
                    self.target[e] = cfg.pass_t * self.rng.uniform(cfg.fill_lo, cfg.fill_hi)
                want = self.target[e] * float(smootherstep((t - 0.05) / 0.75))
                add = max(0.0, want - self.mass[e])
                from_pile = min(add, self.pile[e])
                self.pile[e] -= from_pile
                self.dug += add - from_pile
                self.mass[e] += add
            elif last == "dig" and self.mass[e] > 0:
                self.spill(e, self.mass[e] * cfg.breakout, prev[e], dt, chunks_only=True)
        rot = self.matrix[:, :3, :3]
        world_up = np.linalg.solve(rot, np.repeat(UP[None], len(rot), 0)[..., None])[..., 0]
        world_up /= np.linalg.norm(world_up, axis=1, keepdims=True)
        u = support[:, None] * np.array([0.0, 1.0, 0.0]) + (1 - support[:, None]) * world_up
        u /= np.linalg.norm(u, axis=1, keepdims=True)
        vol = self.mass / cfg.pass_t * self.full
        surface, cap = self.level(u, vol)
        excess = np.maximum(vol - cap, 0.0) * (support < 1)
        self.flowing = (excess > cfg.cohesion * self.full) | (self.flowing & (excess > 1e-9))
        excess = excess * self.flowing
        pour = np.minimum(excess, self.full / cfg.pour_s * dt)
        for e in np.flatnonzero(pour > 1e-9):
            self.spill(e, pour[e] / self.full * cfg.pass_t, prev[e], dt)
        vol = self.mass / cfg.pass_t * self.full
        self.surface, _ = self.level(u, vol)

    def level(self, u: np.ndarray, vol: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s = self.bucket
        uy = np.maximum(u[:, 1:2], 1e-3)
        a = u[:, 0:1] * s.x[None] + u[:, 2:3] * s.z[None]
        rim = np.min(u @ s.corners.T, axis=1)
        def fill(level):
            return np.clip((level[:, None] - a) / uy, s.bottom[None], s.lip[None])
        struck = np.sum(fill(rim) - s.bottom[None], axis=1) * s.cell
        upright = u[:, 1] > 0.05
        struck = np.where(upright, struck, 0.0)
        dome_vol = float(np.sum(s.dome) * s.cell)
        heap_k = np.clip((u[:, 1] - 0.6) / 0.25, 0.0, 1.0)
        cap = struck + dome_vol * heap_k
        lo = np.min(a + uy * s.bottom[None], axis=1)
        hi = rim.copy()
        target = np.minimum(vol, struck)
        for _ in range(LEVEL_ITERS):
            mid = (lo + hi) / 2
            v = np.sum(fill(mid) - s.bottom[None], axis=1) * s.cell
            below = v < target
            lo = np.where(below, mid, lo)
            hi = np.where(below, hi, mid)
        surface = fill((lo + hi) / 2)
        over = np.clip((vol - struck) / max(dome_vol, 1e-9), 0.0, 1.0)
        surface = np.where((vol > struck)[:, None], s.lip[None] + s.dome[None] * over[:, None], surface)
        surface = np.where((vol > 1e-6)[:, None] & upright[:, None], surface, s.bottom[None])
        return np.maximum(surface, s.bottom[None]), cap

    def spill(self, e: int, tonnes: float, prev: np.ndarray, dt: float, chunks_only: bool = False) -> None:
        tonnes = min(tonnes, self.mass[e])
        if tonnes <= 0:
            return
        self.mass[e] -= tonnes
        m = self.matrix[e]
        corners = self.bucket.corners
        wc = corners @ m[:3, :3].T + m[:3, 3]
        order = np.argsort(wc[:, 2])
        a, b = corners[order[0]], corners[order[1]]
        far = corners[order[2:]].mean(axis=0) - (a + b) / 2
        normal = np.linalg.solve(m[:3, :3], UP)
        normal /= np.linalg.norm(normal)
        reach = float(np.clip(0.5 - normal[1], 0.1, 1.0))
        n = max(1, int(round(tonnes * self.cfg.particles_per_t * (0.5 if chunks_only else 1.0))))
        n = min(n, 24)
        f = self.rng.random((n, 2))
        local = a + (b - a) * f[:, :1] + far * (f[:, 1:] * reach) + np.array([0.0, 0.1, 0.0]) + self.rng.normal(0, 0.05, (n, 3))
        pos = local @ m[:3, :3].T + m[:3, 3]
        last = local @ prev[:3, :3].T + prev[:3, 3]
        vel = (pos - last) / dt * INHERIT + self.rng.normal(0, 0.3, (n, 3))
        speed = np.linalg.norm(vel, axis=1, keepdims=True)
        vel = np.where(speed > MAX_THROW, vel / speed * MAX_THROW, vel)
        self.parts.spawn(pos, vel, tonnes, e, 0)
        self.puff(e, pos.mean(axis=0), 0.6 if chunks_only else 1.0)

    def update_particles(self, dt: float) -> None:
        p = self.parts
        live = np.flatnonzero(p.alive)
        if not len(live):
            return
        p.vel[live, 2] -= GRAVITY * dt
        p.vel[live] *= 1 - 0.05 * dt
        p.pos[live] += p.vel[live] * dt
        landed = np.zeros(len(live), bool)
        pos = p.pos[live]
        b = self.bed
        nv, nu = b.shape
        for k in np.flatnonzero(self.truck_seen):
            inv = np.linalg.inv(self.truck_matrix[k])
            local = pos @ inv[:3, :3].T + inv[:3, 3]
            iu = np.round((local[:, 0] - b.u[0]) / b.cell).astype(int)
            iv = np.round((local[:, 1] - b.v[0]) / b.cell).astype(int)
            inside = (local[:, 0] > b.u[0] - BED_PAD) & (local[:, 0] < b.u[-1] + BED_PAD) & (local[:, 1] > b.v[0] - BED_PAD) & (local[:, 1] < b.v[-1] + BED_PAD)
            iu, iv = np.clip(iu, 0, nu - 1), np.clip(iv, 0, nv - 1)
            top = b.floor[iv, iu] + self.height[k, iv, iu]
            hit = inside & (local[:, 2] <= top) & ~landed & (local[:, 2] > top - 1.5) & ~((p.kind[live] == 1) & (p.src[live] == k))
            if hit.any():
                idx = live[hit]
                deposit_kernel(self.height[k], iv[hit], iu[hit], p.mass[idx] / self.cfg.density / b.cell**2)
                self.active[k] = self.cfg.settle_s
                if p.kind[idx[0]] == 0:
                    self.source[k] = p.src[idx[0]]
                self.puff(len(self.exc_ids) + k, pos[hit].mean(axis=0), 0.8)
                landed |= hit
        ground = np.where(p.kind[live] == 0, self.ground[np.minimum(p.src[live], len(self.ground) - 1)] if len(self.ground) else -np.inf,
                          self.truck_matrix[np.minimum(p.src[live], max(len(self.truck_ids) - 1, 0)), 2, 3] if len(self.truck_ids) else -np.inf)
        down = ~landed & (pos[:, 2] <= ground)
        for i in np.flatnonzero(down):
            j = live[i]
            if p.kind[j] == 0:
                e = p.src[j]
                if np.isnan(self.pile_pos[e, 0]):
                    self.pile_pos[e] = [pos[i, 0], pos[i, 1], ground[i]]
                self.pile[e] += p.mass[j]
            else:
                self.delivered[p.src[j]] += p.mass[j]
        landed |= down
        idx = live[landed]
        p.alive[idx] = False
        p.mass[idx] = 0.0
        spin = live[~landed]
        if len(spin):
            w = p.spin[spin] * dt / 2
            dq = np.concatenate([np.ones((len(spin), 1)), w], axis=1)
            q = quat_mul(p.quat[spin], dq)
            p.quat[spin] = q / np.linalg.norm(q, axis=1, keepdims=True)

    def update_beds(self, dt: float) -> None:
        b, cfg = self.bed, self.cfg
        for k in np.flatnonzero(self.emptying >= 0):
            self.emptying[k] += dt
            h = self.height[k]
            speed = min(cfg.empty_accel * self.emptying[k], 6.0)
            sc = min(speed * dt / b.cell, 1.0)
            out = h[0] * sc
            h[:-1] = h[:-1] * (1 - sc) + h[1:] * sc
            h[-1] *= 1 - sc
            tonnes = float(out.sum() * b.cell**2 * cfg.density)
            if tonnes > 0:
                n = min(24, max(1, int(round(tonnes * cfg.particles_per_t))))
                cols = self.rng.choice(len(b.u), n, p=out / out.sum()) if out.sum() > 0 else np.zeros(n, int)
                local = np.stack([b.u[cols] + self.rng.normal(0, 0.08, n), np.full(n, b.v[0] - 0.3), b.floor[0, cols] + h[0, cols] + 0.1], 1)
                m = self.truck_matrix[k]
                prev = self.truck_prev[k]
                pos = local @ m[:3, :3].T + m[:3, 3]
                vel = (pos - (local @ prev[:3, :3].T + prev[:3, 3])) / dt + np.array([0.0, -speed, 0.3]) @ m[:3, :3].T + self.rng.normal(0, 0.3, (n, 3))
                self.parts.spawn(pos, vel, tonnes, k, 1)
                self.puff(len(self.exc_ids) + k, pos.mean(axis=0), 1.2)
            if self.emptying[k] > cfg.empty_s or h.sum() * b.cell**2 * cfg.density < 0.05:
                self.delivered[k] += float(h.sum() * b.cell**2 * cfg.density)
                h[:] = 0.0
                self.emptying[k] = -1.0
                self.source[k] = -1
        self.active = np.maximum(self.active - dt, 0.0)
        busy = np.flatnonzero(self.active > 0)
        if len(busy):
            self.height[busy] = relax(self.height[busy], b.floor[None], b.cell, self.slope, cfg.relax_iters)

    def puff(self, slot: int, pos: np.ndarray, strength: float) -> None:
        if self.time - self.dust_clock[slot] < self.cfg.dust_every or self.cfg.max_dust == 0:
            return
        self.dust_clock[slot] = self.time
        i = int(np.argmax(self.dust_age))
        self.dust_pos[i] = pos + self.rng.normal(0, 0.3, 3)
        self.dust_age[i] = 0.0
        self.dust_size[i] = strength * self.rng.uniform(0.8, 1.2)

    def update_dust(self, dt: float) -> None:
        live = self.dust_age < DUST_LIFE
        self.dust_age[live] += dt
        self.dust_pos[live, 2] += DUST_RISE * dt

    def dust(self) -> tuple[np.ndarray, np.ndarray]:
        f = np.clip(self.dust_age / DUST_LIFE, 0, 1)
        size = np.where(self.dust_age < DUST_LIFE, (DUST_GROW[0] + (DUST_GROW[1] - DUST_GROW[0]) * np.sqrt(f)) * self.dust_size * (1 - f**4), 0.0)
        return self.dust_pos, size

    def bucket_points(self) -> np.ndarray:
        s = self.bucket
        return np.stack([np.broadcast_to(s.x, self.surface.shape), self.surface, np.broadcast_to(s.z, self.surface.shape)], -1)

    def bed_points(self) -> np.ndarray:
        b = self.bed
        u = b.u.copy()
        v = b.v.copy()
        u[0] -= BED_PAD
        u[-1] += BED_PAD
        v[0] -= BED_PAD * 0.6
        v[-1] += BED_PAD * 0.6
        gu, gv = np.meshgrid(u, v)
        h = self.height
        z = np.where(h > 0.01, b.floor[None] + h, b.floor[None] - 0.06)
        return np.stack([np.broadcast_to(gu, h.shape), np.broadcast_to(gv, h.shape), z], -1)

    def pile_mesh(self, rings: int, segments: int) -> np.ndarray:
        cap = self.cfg.pile_passes * self.cfg.pass_t
        vol = np.minimum(self.pile, cap) / self.cfg.density
        h = (3 * vol * self.slope**2 / math.pi) ** (1 / 3)
        r = h / self.slope
        k = np.linspace(0, 1, rings)[:, None]
        a = np.linspace(0, 2 * math.pi, segments, endpoint=False)[None]
        prof = (1 - k**2) * 0.5 + (1 - k) * 0.5
        out = np.zeros((len(self.exc_ids), rings, segments, 3))
        out[..., 0] = (r[:, None, None] * k * np.cos(a))
        out[..., 1] = (r[:, None, None] * k * np.sin(a))
        out[..., 2] = h[:, None, None] * prof * 1.0 - 0.05
        return out

    def instances(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        e, t = len(self.exc_ids), len(self.truck_ids)
        pos, quat, scale = [], [], []
        if e:
            s = self.bucket
            cols = self.bucket_chunks
            ys = np.take_along_axis(self.surface, cols, axis=1)
            depth = ys - s.bottom[cols]
            local = np.stack([s.x[cols], ys + 0.04, s.z[cols]], -1)
            world = np.einsum("eij,ekj->eki", self.matrix[:, :3, :3], local) + self.matrix[:, None, :3, 3]
            pq = quat_from_rotations(self.matrix[:, :3, :3])
            pos.append(world.reshape(-1, 3))
            quat.append(quat_mul(pq[:, None], self.bucket_chunk_quat).reshape(-1, 4))
            sc = np.linalg.norm(self.matrix[:, :3, 0], axis=1)[:, None] * self.bucket_chunk_scale * (depth > CHUNK_MIN_DEPTH)
            scale.append(sc.reshape(-1))
        if t:
            b = self.bed
            iv, iu = self.bed_chunks[..., 0], self.bed_chunks[..., 1]
            h = self.height[np.arange(t)[:, None], iv, iu]
            local = np.stack([b.u[iu], b.v[iv], b.floor[iv, iu] + h + 0.02], -1)
            world = np.einsum("tij,tkj->tki", self.truck_matrix[:, :3, :3], local) + self.truck_matrix[:, None, :3, 3]
            pq = quat_from_rotations(self.truck_matrix[:, :3, :3])
            pos.append(world.reshape(-1, 3))
            quat.append(quat_mul(pq[:, None], self.bed_chunk_quat).reshape(-1, 4))
            scale.append((self.bed_chunk_scale * (h > BED_CHUNK_MIN_DEPTH)).reshape(-1))
        p = self.parts
        pos.append(p.pos)
        quat.append(p.quat)
        scale.append(p.scale * p.alive)
        return np.concatenate(pos), np.concatenate(quat), np.concatenate(scale)
