import numpy as np
import warp as wp

BIG = 1.0e30


@wp.func
def hit_box(o: wp.vec3, d: wp.vec3, c: wp.vec3, r: wp.mat33, h: wp.vec3) -> float:
    rt = wp.transpose(r)
    lo = rt * (o - c)
    ld = rt * d
    t0 = float(-BIG)
    t1 = float(BIG)
    for k in range(3):
        if wp.abs(ld[k]) < 1.0e-9:
            if lo[k] < -h[k] or lo[k] > h[k]:
                return BIG
        else:
            a = (-h[k] - lo[k]) / ld[k]
            b = (h[k] - lo[k]) / ld[k]
            t0 = wp.max(t0, wp.min(a, b))
            t1 = wp.min(t1, wp.max(a, b))
    if t0 > t1 or t0 <= 0.0:
        return BIG
    return t0


@wp.func
def hit_cylinder(o: wp.vec3, d: wp.vec3, base: wp.vec3, radius: float, height: float) -> float:
    best = float(BIG)
    px = o[0] - base[0]
    pz = o[2] - base[2]
    a = d[0] * d[0] + d[2] * d[2]
    if a > 1.0e-12:
        b = px * d[0] + pz * d[2]
        c = px * px + pz * pz - radius * radius
        disc = b * b - a * c
        if disc >= 0.0:
            t = (-b - wp.sqrt(disc)) / a
            y = o[1] + t * d[1] - base[1]
            if t > 0.0 and y >= 0.0 and y <= height:
                best = t
    if wp.abs(d[1]) > 1.0e-9:
        for k in range(2):
            t = (base[1] + float(k) * height - o[1]) / d[1]
            qx = px + t * d[0]
            qz = pz + t * d[2]
            if t > 0.0 and t < best and qx * qx + qz * qz <= radius * radius:
                best = t
    return best


@wp.kernel
def cast_kernel(
    ground: wp.uint64,
    surround: wp.uint64,
    has_surround: int,
    box_c: wp.array(dtype=wp.vec3),
    box_r: wp.array(dtype=wp.mat33),
    box_h: wp.array(dtype=wp.vec3),
    nbox: int,
    cyl: wp.array(dtype=wp.vec3),
    ncyl: int,
    cyl_r: float,
    cyl_h: float,
    origins: wp.array(dtype=wp.vec3),
    dirs: wp.array(dtype=wp.vec3),
    ignore: wp.array(dtype=int),
    max_t: float,
    out: wp.array(dtype=float),
):
    i = wp.tid()
    o = origins[i]
    d = dirs[i]
    best = float(BIG)
    q = wp.mesh_query_ray(ground, o, d, max_t)
    if q.result:
        best = q.t
    if has_surround != 0:
        q2 = wp.mesh_query_ray(surround, o, d, wp.min(max_t, best))
        if q2.result and q2.t < best:
            best = q2.t
    for k in range(nbox):
        if k != ignore[i]:
            best = wp.min(best, hit_box(o, d, box_c[k], box_r[k], box_h[k]))
    for k in range(ncyl):
        best = wp.min(best, hit_cylinder(o, d, cyl[k], cyl_r, cyl_h))
    out[i] = best


@wp.kernel
def ground_kernel(
    ground: wp.uint64,
    surround: wp.uint64,
    has_surround: int,
    origins: wp.array(dtype=wp.vec3),
    lengths: wp.array(dtype=float),
    out: wp.array(dtype=float),
):
    i = wp.tid()
    d = wp.vec3(0.0, -1.0, 0.0)
    out[i] = BIG
    q = wp.mesh_query_ray(ground, origins[i], d, lengths[i])
    if q.result:
        out[i] = q.t
    elif has_surround != 0:
        q2 = wp.mesh_query_ray(surround, origins[i], d, lengths[i])
        if q2.result:
            out[i] = q2.t


def make_mesh(points: np.ndarray, tris: np.ndarray, device: str) -> wp.Mesh:
    return wp.Mesh(
        points=wp.array(np.asarray(points, np.float32), dtype=wp.vec3, device=device),
        indices=wp.array(np.asarray(tris, np.int32).reshape(-1), dtype=int, device=device),
    )


class Caster:
    def __init__(self, ground: tuple[np.ndarray, np.ndarray], surround: tuple[np.ndarray, np.ndarray] | None = None, device: str | None = None) -> None:
        wp.init()
        self.device = device or ("cuda:0" if wp.is_cuda_available() else "cpu")
        self.ground = make_mesh(*ground, self.device)
        self.surround = make_mesh(*surround, self.device) if surround is not None else self.ground
        self.has_surround = int(surround is not None)
        self.set_boxes(np.zeros((0, 3)), np.zeros((0, 3, 3)), np.zeros((0, 3)))
        self.set_cylinders(np.zeros((0, 3)), 0.3, 1.8)

    def _array(self, a: np.ndarray, dtype, rows: int) -> wp.array:
        a = np.asarray(a, np.float32)
        if len(a) == 0:
            a = np.zeros((1, *a.shape[1:]), np.float32)
        return wp.array(a[: max(rows, 1)], dtype=dtype, device=self.device)

    def set_boxes(self, centres: np.ndarray, rotations: np.ndarray, half: np.ndarray) -> None:
        self.nbox = len(centres)
        self.box_c = self._array(centres, wp.vec3, self.nbox)
        self.box_r = self._array(rotations, wp.mat33, self.nbox)
        self.box_h = self._array(half, wp.vec3, self.nbox)

    def set_cylinders(self, bases: np.ndarray, radius: float, height: float) -> None:
        self.ncyl = len(bases)
        self.cyl = self._array(bases, wp.vec3, self.ncyl)
        self.cyl_r, self.cyl_h = float(radius), float(height)

    def cast(self, origins: np.ndarray, dirs: np.ndarray, max_t: float, ignore: np.ndarray | int = -1) -> np.ndarray:
        n = len(origins)
        ignore = np.broadcast_to(np.asarray(ignore, np.int32), (n,)).copy()
        out = wp.empty(n, dtype=float, device=self.device)
        wp.launch(
            cast_kernel,
            dim=n,
            inputs=[
                self.ground.id, self.surround.id, self.has_surround,
                self.box_c, self.box_r, self.box_h, self.nbox,
                self.cyl, self.ncyl, self.cyl_r, self.cyl_h,
                wp.array(np.asarray(origins, np.float32), dtype=wp.vec3, device=self.device),
                wp.array(np.asarray(dirs, np.float32), dtype=wp.vec3, device=self.device),
                wp.array(ignore, dtype=int, device=self.device),
                float(max_t),
            ],
            outputs=[out],
            device=self.device,
        )
        d = out.numpy().astype(np.float64)
        d[d > max_t] = np.inf
        return d

    def probe(self, xs: np.ndarray, zs: np.ndarray, tops: np.ndarray, lengths: np.ndarray) -> np.ndarray:
        origins = np.column_stack([xs, tops, zs])
        out = wp.empty(len(xs), dtype=float, device=self.device)
        wp.launch(
            ground_kernel,
            dim=len(xs),
            inputs=[
                self.ground.id, self.surround.id, self.has_surround,
                wp.array(origins.astype(np.float32), dtype=wp.vec3, device=self.device),
                wp.array(np.asarray(lengths, np.float32), dtype=float, device=self.device),
            ],
            outputs=[out],
            device=self.device,
        )
        t = out.numpy().astype(np.float64)
        return np.where(t < BIG / 2, np.asarray(tops) - t, np.nan)

    def height(self, x: float, z: float, ref: float = 10000.0, length: float = 20000.0) -> float:
        return float(self.probe(np.array([x]), np.array([z]), np.array([ref]), np.array([length]))[0])
