import math
from dataclasses import dataclass

import numpy as np

from .kinematics import yaw_matrix
from .pedestrians import PEDESTRIAN_HEIGHT, PEDESTRIAN_RADIUS
from .usdbuild import isaac_matrix

PREFIX = "/World/People"
PEOPLE_ROOT = "/Isaac/People"
CHARACTERS = [
    "Characters/original_male_adult_construction_01/male_adult_construction_01.usd",
    "Characters/original_male_adult_construction_02/male_adult_construction_02.usd",
    "Characters/male_adult_construction_05_new/male_adult_construction_05_new.usd",
    "Characters/original_male_adult_construction_03/male_adult_construction_03.usd",
    "Characters/male_adult_construction_01_new/male_adult_construction_01_new.usd",
    "Characters/original_male_adult_construction_05/male_adult_construction_05.usd",
]
SOURCE_SKELETON = "Characters/biped_demo/biped_demo_meters.usd"
WALK_CLIP = "Animations/stand_walk_loop_in_place.skelanim.usd"
IDLE_CLIP = "Animations/stand_idle_loop.skelanim.usd"
WALK_CLIP_SPEED = 1.083
WALK_RATE = (0.6, 1.6)
IDLE_BLEND = 0.6
Y_TO_Z = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
FACE_YAW = 180.0
GAIT_SPREAD = 0.37
CAPSULE_COLOUR = (1.0, 0.47, 0.08)
JOINT_MAP = {
    "Hip": "Pelvis",
    "Waist": "Spine1",
    "Spine01": "Spine3",
    "Spine02": "Chest",
    "NeckTwist01": "Neck1",
    "NeckTwist02": "Neck2",
    "Head": "Head",
    **{f"{s}_{t}": f"{s}_{b}" for s in "LR" for t, b in [
        ("Thigh", "UpLeg"), ("Calf", "LoLeg"), ("Foot", "Ankle"), ("ToeBase", "Ball"),
        ("Clavicle", "Clavicle"), ("Upperarm", "UpArm"), ("Forearm", "LoArm"), ("Hand", "Wrist"),
    ]},
}
ROOT_JOINT = "Hip"


@dataclass
class Skeleton:
    names: list[str]
    parents: np.ndarray
    rot: np.ndarray
    pos: np.ndarray


@dataclass
class Clip:
    quats: np.ndarray
    trans: np.ndarray
    fps: float

    @property
    def period(self) -> float:
        return (len(self.quats) - 1) / self.fps


def parents_of(paths: list[str]) -> np.ndarray:
    index = {p: i for i, p in enumerate(paths)}
    return np.array([index.get(p.rsplit("/", 1)[0], -1) if "/" in p else -1 for p in paths])


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = np.moveaxis(np.asarray(q, float), -1, 0)
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1),
    ], -2)


def matrix_to_quat(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, float)
    flat = m.reshape(-1, 3, 3)
    out = np.empty((len(flat), 4))
    for k, r in enumerate(flat):
        tr = r[0, 0] + r[1, 1] + r[2, 2]
        if tr > 0:
            s = 2 * math.sqrt(tr + 1)
            out[k] = [s / 4, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
        else:
            i = int(np.argmax(np.diag(r)))
            j, n = (i + 1) % 3, (i + 2) % 3
            s = 2 * math.sqrt(max(1 + r[i, i] - r[j, j] - r[n, n], 1e-12))
            q = np.empty(4)
            q[0] = (r[n, j] - r[j, n]) / s
            q[1 + i], q[1 + j], q[1 + n] = s / 4, (r[j, i] + r[i, j]) / s, (r[n, i] + r[i, n]) / s
            out[k] = q
    out /= np.linalg.norm(out, axis=-1, keepdims=True)
    return out.reshape(*m.shape[:-2], 4)


def globals_of(parents: np.ndarray, rot: np.ndarray, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    g_rot, g_pos = np.empty_like(rot), np.empty_like(pos)
    for j, p in enumerate(parents):
        if p < 0:
            g_rot[..., j, :, :], g_pos[..., j, :] = rot[..., j, :, :], pos[..., j, :]
        else:
            g_rot[..., j, :, :] = g_rot[..., p, :, :] @ rot[..., j, :, :]
            g_pos[..., j, :] = (g_rot[..., p, :, :] @ pos[..., j, :, None])[..., 0] + g_pos[..., p, :]
    return g_rot, g_pos


def retarget(src: Skeleton, src_rot: np.ndarray, src_pos: np.ndarray, dst: Skeleton, fps: float, joint_map: dict[str, str] = JOINT_MAP, axes: np.ndarray = Y_TO_Z) -> Clip:
    s_rest_rot, s_rest_pos = globals_of(src.parents, src.rot, src.pos)
    s_rot, s_pos = globals_of(src.parents, src_rot, src_pos)
    d_rest_rot, d_rest_pos = globals_of(dst.parents, dst.rot, dst.pos)
    frames = len(src_rot)
    g = np.empty((frames, len(dst.names), 3, 3))
    for j, name in enumerate(dst.names):
        p = dst.parents[j]
        m = src.names.index(joint_map[name]) if joint_map.get(name) in src.names else -1
        if m >= 0:
            delta = axes @ (s_rot[:, m] @ s_rest_rot[m].T) @ axes.T
            g[:, j] = delta @ d_rest_rot[j]
        elif p >= 0:
            g[:, j] = g[:, p] @ dst.rot[j]
        else:
            g[:, j] = dst.rot[j]
    local = np.empty_like(g)
    for j, p in enumerate(dst.parents):
        local[:, j] = g[:, j] if p < 0 else np.swapaxes(g[:, p], -1, -2) @ g[:, j]
    trans = np.broadcast_to(dst.pos, (frames, *dst.pos.shape)).copy()
    if ROOT_JOINT in dst.names and joint_map[ROOT_JOINT] in src.names:
        r, m = dst.names.index(ROOT_JOINT), src.names.index(joint_map[ROOT_JOINT])
        scale = d_rest_pos[r, 2] / max(s_rest_pos[m, 1], 1e-6)
        shift = (s_pos[:, m] - s_rest_pos[m]) @ axes.T * scale
        shift[:, :2] -= shift[:, :2].mean(0)
        world = d_rest_pos[r] + shift
        p = dst.parents[r]
        if p >= 0:
            p_rot, p_pos = d_rest_rot[p], d_rest_pos[p]
            world = (world - p_pos) @ p_rot
        trans[:, r] = world
    return Clip(matrix_to_quat(local), trans, fps)


def nlerp(a: np.ndarray, b: np.ndarray, w: float | np.ndarray) -> np.ndarray:
    b = np.where((np.sum(a * b, -1, keepdims=True) < 0), -b, b)
    q = a * (1 - w) + b * w
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def sample(clip: Clip, t: float) -> tuple[np.ndarray, np.ndarray]:
    f = (t % clip.period) * clip.fps
    i = int(f)
    k, w = min(i + 1, len(clip.quats) - 1), f - i
    return nlerp(clip.quats[i], clip.quats[k], w), clip.trans[i] * (1 - w) + clip.trans[k] * w


class Gait:
    def __init__(self, walk: Clip, idle: Clip, phase: float = 0.0) -> None:
        self.walk, self.idle = walk, idle
        self.walk_t, self.idle_t = phase * walk.period, phase * idle.period

    def step(self, dt: float, speed: float) -> tuple[np.ndarray, np.ndarray]:
        w = min(max(speed / IDLE_BLEND, 0.0), 1.0)
        if w > 0:
            self.walk_t += dt * min(max(speed / WALK_CLIP_SPEED, WALK_RATE[0]), WALK_RATE[1])
        self.idle_t += dt
        wq, wt = sample(self.walk, self.walk_t)
        if w >= 1.0:
            return wq, wt
        iq, it = sample(self.idle, self.idle_t)
        return nlerp(iq, wq, w), it * (1 - w) + wt * w


def read_skeleton(stage) -> tuple[object, Skeleton]:
    from pxr import UsdSkel

    prim = next(p for p in stage.Traverse() if p.IsA(UsdSkel.Skeleton))
    skel = UsdSkel.Skeleton(prim)
    paths = [str(j) for j in skel.GetJointsAttr().Get()]
    rest = np.array([np.array(m) for m in skel.GetRestTransformsAttr().Get()], float)
    return prim, Skeleton([p.rsplit("/", 1)[-1] for p in paths], parents_of(paths), np.swapaxes(rest[:, :3, :3], 1, 2), rest[:, 3, :3])


def read_clip(stage, src: Skeleton) -> tuple[np.ndarray, np.ndarray, float]:
    from pxr import UsdSkel

    anim = UsdSkel.Animation(next(p for p in stage.Traverse() if p.IsA(UsdSkel.Animation)))
    names = [str(j).rsplit("/", 1)[-1] for j in anim.GetJointsAttr().Get()]
    times = anim.GetRotationsAttr().GetTimeSamples()
    rot = np.broadcast_to(src.rot, (len(times), *src.rot.shape)).copy()
    pos = np.broadcast_to(src.pos, (len(times), *src.pos.shape)).copy()
    idx = [src.names.index(n) for n in names]
    for f, t in enumerate(times):
        q = np.array([[r.GetReal(), *r.GetImaginary()] for r in anim.GetRotationsAttr().Get(t)], float)
        rot[f, idx] = quat_to_matrix(q)
        pos[f, idx] = np.array(anim.GetTranslationsAttr().Get(t), float)
    return rot, pos, stage.GetTimeCodesPerSecond()


class PeopleRig:
    def __init__(self, stage, walkers, assets_root: str, prefix: str = PREFIX) -> None:
        from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

        self.Gf, self.Vt = Gf, Vt
        root = assets_root.rstrip("/") + PEOPLE_ROOT
        _, src = read_skeleton(Usd.Stage.Open(f"{root}/{SOURCE_SKELETON}"))
        walk_raw = read_clip(Usd.Stage.Open(f"{root}/{WALK_CLIP}"), src)
        idle_raw = read_clip(Usd.Stage.Open(f"{root}/{IDLE_CLIP}"), src)
        clips = {}
        self.people = []
        for i, w in enumerate(walkers):
            asset = f"{root}/{CHARACTERS[i % len(CHARACTERS)]}"
            if asset not in clips:
                _, dst = read_skeleton(Usd.Stage.Open(asset))
                clips[asset] = (retarget(src, *walk_raw[:2], dst, walk_raw[2]), retarget(src, *idle_raw[:2], dst, idle_raw[2]), dst)
            walk, idle, dst = clips[asset]
            path = f"{prefix}/{w.id}"
            op = UsdGeom.Xform.Define(stage, path).AddTransformOp()
            body = stage.DefinePrim(f"{path}/body", "Xform")
            body.GetReferences().AddReference(asset)
            UsdGeom.Xformable(body).AddRotateZOp().Set(FACE_YAW)
            skel_prim = next(p for p in Usd.PrimRange(body) if p.IsA(UsdSkel.Skeleton))
            anim = UsdSkel.Animation.Define(stage, f"{path}/gait")
            anim.CreateJointsAttr().Set(UsdSkel.Skeleton(skel_prim).GetJointsAttr().Get())
            anim.CreateScalesAttr().Set(Vt.Vec3hArray([Gf.Vec3h(1, 1, 1)] * len(dst.names)))
            UsdSkel.BindingAPI.Apply(skel_prim).CreateAnimationSourceRel().SetTargets([anim.GetPath()])
            self.people.append((op, anim.CreateRotationsAttr(), anim.CreateTranslationsAttr(), Gait(walk, idle, (i * GAIT_SPREAD) % 1.0)))
        print(f"people: {len(self.people)} animated from {len(clips)} characters under {root}", flush=True)

    def update(self, dt: float, walkers) -> None:
        Gf, Vt = self.Gf, self.Vt
        for (op, rot_attr, trans_attr, gait), w in zip(self.people, walkers):
            op.Set(isaac_matrix(yaw_matrix(w.yaw), np.array([w.x, w.y, w.z])))
            q, t = gait.step(dt, w.speed)
            rot_attr.Set(Vt.QuatfArray([Gf.Quatf(float(a), float(b), float(c), float(d)) for a, b, c, d in q]))
            trans_attr.Set(Vt.Vec3fArray([Gf.Vec3f(float(x), float(y), float(z)) for x, y, z in t]))


class Capsules:
    def __init__(self, stage, walkers, prefix: str = PREFIX) -> None:
        from pxr import Gf, UsdGeom

        self.ops = []
        for w in walkers:
            path = f"{prefix}/{w.id}"
            self.ops.append(UsdGeom.Xform.Define(stage, path).AddTransformOp())
            body = UsdGeom.Capsule.Define(stage, path + "/body")
            body.CreateAxisAttr(UsdGeom.Tokens.z)
            body.CreateRadiusAttr(PEDESTRIAN_RADIUS)
            body.CreateHeightAttr(PEDESTRIAN_HEIGHT - 2 * PEDESTRIAN_RADIUS)
            body.CreateDisplayColorAttr([Gf.Vec3f(*CAPSULE_COLOUR)])
            body.AddTranslateOp().Set(Gf.Vec3d(0, 0, PEDESTRIAN_HEIGHT / 2))

    def update(self, dt: float, walkers) -> None:
        for op, w in zip(self.ops, walkers):
            op.Set(isaac_matrix(yaw_matrix(w.yaw), np.array([w.x, w.y, w.z])))


def make_people(stage, walkers):
    try:
        from isaacsim.storage.native import get_assets_root_path

        return PeopleRig(stage, walkers, get_assets_root_path())
    except Exception as e:
        print(f"people: animated characters unavailable ({e!r}), capsules instead", flush=True)
        stage.RemovePrim(PREFIX)
        return Capsules(stage, walkers)
