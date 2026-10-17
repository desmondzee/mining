import numpy as np


def icosphere(subdiv: int) -> tuple[np.ndarray, np.ndarray]:
    t = (1 + 5 ** 0.5) / 2
    v = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t],
                  [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    f = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
                  [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5],
                  [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]])
    verts = list(v / np.linalg.norm(v, axis=1, keepdims=True))
    for _ in range(subdiv):
        cache, out = {}, []

        def mid(a, b):
            k = (a, b) if a < b else (b, a)
            if k not in cache:
                p = verts[a] + verts[b]
                verts.append(p / np.linalg.norm(p))
                cache[k] = len(verts) - 1
            return cache[k]

        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            out += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]
        f = np.array(out)
    return np.array(verts), f


def wave_noise(p: np.ndarray, rng: np.random.Generator, octaves: int = 5, base: float = 1.6, waves: int = 6) -> np.ndarray:
    out = np.zeros(len(p))
    amp, freq = 1.0, base
    for _ in range(octaves):
        d = rng.normal(size=(waves, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        ph = rng.uniform(0, 2 * np.pi, waves)
        out += amp * np.sin(p @ d.T * freq + ph).sum(axis=1) / np.sqrt(waves)
        amp *= 0.5
        freq *= 2.1
    return out


def vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.zeros_like(v)
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def rock(seed: int, subdiv: int = 4, cuts: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    v, f = icosphere(subdiv)
    v = v * np.array([1.0, rng.uniform(0.7, 1.0), rng.uniform(0.5, 0.85)])
    for _ in range(cuts):
        n = rng.normal(size=3)
        n[2] = abs(n[2]) * 0.6 if rng.random() < 0.7 else n[2]
        n /= np.linalg.norm(n)
        d = rng.uniform(0.55, 0.85)
        h = v @ n
        over = h > d
        v[over] -= np.outer(h[over] - d, n) * rng.uniform(0.85, 1.0)
    noise_rng = np.random.default_rng(seed + 7919)
    v += vertex_normals(v, f) * (0.045 * wave_noise(v, noise_rng))[:, None]
    lo = v[:, 2].min()
    floor = lo + 0.12 * (v[:, 2].max() - lo)
    v[:, 2] = np.where(v[:, 2] < floor, floor + (v[:, 2] - floor) * 0.25, v[:, 2])
    v[:, :2] -= (v[:, :2].max(0) + v[:, :2].min(0)) / 2
    v[:, 2] -= v[:, 2].min()
    return v / np.ptp(v[:, :2], axis=0).max(), f


def box_uv(v: np.ndarray, f: np.ndarray, metres_per_uv: float = 1.0) -> np.ndarray:
    fn = np.abs(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]))
    axis = fn.argmax(axis=1)
    drop = {0: [1, 2], 1: [0, 2], 2: [0, 1]}
    uv = np.zeros((len(f), 3, 2))
    for a, keep in drop.items():
        m = axis == a
        uv[m] = v[f[m]][:, :, keep] / metres_per_uv
    return uv.reshape(-1, 2)


def lathe(profile: np.ndarray, segments: int) -> tuple[np.ndarray, np.ndarray]:
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    r, z = profile[:, 0], profile[:, 1]
    v = np.stack([np.outer(r, np.cos(a)), np.outer(r, np.sin(a)), np.repeat(z[:, None], segments, 1)], -1).reshape(-1, 3)
    f = []
    for i in range(len(profile) - 1):
        for j in range(segments):
            a0, a1 = i * segments + j, i * segments + (j + 1) % segments
            b0, b1 = a0 + segments, a1 + segments
            f += [[a0, a1, b1], [a0, b1, b0]]
    return v, np.array(f)


def box(lo, hi) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    c = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], float)
    v = lo + c * (hi - lo)
    f = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
                  [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])
    return v, f


def merge(parts: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    vs, fs, n = [], [], 0
    for v, f in parts:
        vs.append(v)
        fs.append(f + n)
        n += len(v)
    return np.concatenate(vs), np.concatenate(fs)


def cone() -> dict:
    prof = np.array([[0.20, 0.0], [0.20, 0.035], [0.165, 0.04], [0.15, 0.06], [0.125, 0.22], [0.112, 0.3],
                     [0.10, 0.38], [0.085, 0.46], [0.07, 0.54], [0.055, 0.62], [0.035, 0.7], [0.025, 0.72], [0.0, 0.72]])
    v, f = lathe(prof, 32)
    zc = v[f].mean(axis=1)[:, 2]
    band = ((zc > 0.30) & (zc < 0.38)) | ((zc > 0.46) & (zc < 0.54))
    base = zc < 0.05
    v2, f2 = box([-0.2, -0.2, 0.0], [0.2, 0.2, 0.035])
    return {"parts": [("body", v, f[~band & ~base], "cone_orange"), ("bands", v, f[band], "reflective_white"),
                      ("base", *merge([(v, f[base]), (v2, f2)]), "rubber_black")]}


def sign(width: float = 0.9, height: float = 2.4) -> dict:
    post = box([-0.04, -0.04, 0.0], [0.04, 0.04, height])
    back = box([-width / 2, 0.04, height - width - 0.05], [width / 2, 0.06, height - 0.05])
    z0, z1 = height - width - 0.05, height - 0.05
    face = (np.array([[-width / 2, 0.035, z0], [width / 2, 0.035, z0], [width / 2, 0.035, z1], [-width / 2, 0.035, z1]]),
            np.array([[0, 1, 2], [0, 2, 3]]))
    return {"parts": [("post", *merge([post, back]), "galvanised"), ("face", *face, "sign_face")],
            "face_uv": np.array([[0, 0], [1, 0], [1, 1], [0, 0], [1, 1], [0, 1]], float)}


def fuel_tank(length: float = 8.0, diameter: float = 2.6) -> dict:
    r = diameter / 2
    prof = np.array([[0.0, -length / 2], [r * 0.6, -length / 2 - 0.05], [r * 0.95, -length / 2 + 0.05],
                     [r, -length / 2 + 0.25], [r, length / 2 - 0.25], [r * 0.95, length / 2 - 0.05],
                     [r * 0.6, length / 2 + 0.05], [0.0, length / 2]])
    v, f = lathe(prof, 48)
    v = v[:, [0, 2, 1]] * np.array([1.0, -1.0, 1.0]) + np.array([0.0, 0.0, r + 0.45])
    saddles = [box([-r * 0.8, y - 0.15, 0.0], [r * 0.8, y + 0.15, r * 0.9]) for y in (-length / 3, 0.0, length / 3)]
    skid = [box([x - 0.12, -length / 2 - 0.3, 0.0], [x + 0.12, length / 2 + 0.3, 0.2]) for x in (-r * 0.7, r * 0.7)]
    ladder = [box([r * 0.95, -0.25, 0.0], [r * 1.05, -0.2, diameter + 0.6]), box([r * 0.95, 0.2, 0.0], [r * 1.05, 0.25, diameter + 0.6])]
    rungs = [box([r * 0.95, -0.2, z - 0.02], [r * 1.03, 0.2, z + 0.02]) for z in np.arange(0.3, diameter + 0.5, 0.3)]
    manway = lathe(np.array([[0.32, 0.0], [0.32, 0.25], [0.36, 0.25], [0.36, 0.3], [0.0, 0.3]]), 24)
    manway = (manway[0] + np.array([0.0, length / 4, diameter + 0.4]), manway[1])
    return {"parts": [("shell", v, f, "tank_white"), ("frame", *merge(saddles + skid + ladder + rungs), "steel_dark"),
                      ("manway", *manway, "tank_white")]}


def windrow(stations: np.ndarray, normals: np.ndarray, ground, height: float, base: float, top: float,
            rng: np.random.Generator, taper: float = 4.0, floor: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs = np.array([-0.55, -0.5, -0.32, -0.5 * top / base, 0.5 * top / base, 0.32, 0.5, 0.55]) * base
    hs = np.array([-0.35, 0.0, 0.62, 1.0, 1.0, 0.62, 0.0, -0.35])
    n = len(stations)
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(stations, axis=0), axis=1))])
    ramp = np.clip(np.minimum(s, s[-1] - s) / taper, 0.0, 1.0)
    ramp = ramp * ramp * (3 - 2 * ramp)
    lump = 1.0 + 0.12 * np.interp(s, np.arange(0, s[-1] + 6, 6.0), rng.uniform(-1, 1, int(s[-1] // 6) + 2))
    wobble = 0.25 * np.interp(s, np.arange(0, s[-1] + 9, 9.0), rng.uniform(-1, 1, int(s[-1] // 9) + 2))
    jit = rng.uniform(-0.06, 0.06, (n, len(xs)))
    off = xs[None, :] + wobble[:, None]
    xz = stations[:, None, :] + normals[:, None, :] * off[..., None]
    g = ground(xz.reshape(-1, 2)).reshape(n, len(xs))
    if floor is not None:
        g[:, :-2] = np.maximum(g[:, :-2], floor[:, None])
    h = height * lump[:, None] * (ramp[:, None] * hs[None, :] + jit)
    h[:, [0, -1]] = height * hs[[0, -1]]
    h[:, [1, -2]] = np.minimum(h[:, [1, -2]], 0.0)
    y = g + h
    v = np.stack([xz[..., 0], y, xz[..., 1]], -1).reshape(-1, 3)
    m = len(xs)
    f = []
    for i in range(n - 1):
        for j in range(m - 1):
            a, b, c, d = i * m + j, i * m + j + 1, (i + 1) * m + j, (i + 1) * m + j + 1
            f += [[a, c, b], [b, c, d]]
    for i, cap in ((0, False), (n - 1, True)):
        ring = [i * m + j for j in range(m)]
        for j in range(1, m - 2):
            tri = [ring[0], ring[j], ring[j + 1]]
            f.append(tri[::-1] if cap else tri)
    uv = np.stack([np.repeat(s[:, None], m, 1), off], -1).reshape(-1, 2) / 3.0
    return v, np.array(f), uv


def mound(seed: int, radius: float, height: float, rings: int = 18, segments: int = 48) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    r = np.linspace(0.0, 1.12, rings + 1)[1:]
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    R, A = np.meshgrid(r, a, indexing="ij")
    p = np.stack([R * np.cos(A), R * np.sin(A), np.zeros_like(R)], -1).reshape(-1, 3)
    edge = 1.0 + 0.18 * wave_noise(np.c_[np.cos(a), np.sin(a), np.zeros(segments)], rng, octaves=3, base=1.5)
    rr = (R / edge[None, :]).reshape(-1)
    z = np.where(rr < 1.0, np.clip(1 - rr, 0, 1) ** 0.9, -0.15 * (rr - 1.0) / 0.12)
    z += 0.06 * wave_noise(p * 3.0, rng, octaves=4, base=1.0) * (rr < 1.0)
    v = np.concatenate([[[0.0, 0.0, 1.0]], np.c_[p[:, :2], z]]) * np.array([radius, radius, height])
    f = [[0, 1 + j, 1 + (j + 1) % segments] for j in range(segments)]
    for i in range(rings - 1):
        for j in range(segments):
            a0, a1 = 1 + i * segments + j, 1 + i * segments + (j + 1) % segments
            f += [[a0, a0 + segments, a1 + segments], [a0, a1 + segments, a1]]
    return v, np.array(f)
