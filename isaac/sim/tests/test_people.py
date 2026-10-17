import numpy as np
import pytest

from isaac.sim import people as P


def rz(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])


def rx(deg):
    a = np.radians(deg)
    return np.array([[1.0, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])


def chain(names, rots, offsets):
    return P.Skeleton(names, P.parents_of(["/".join(names[: i + 1]) for i in range(len(names))]), np.array(rots, float), np.array(offsets, float))


@pytest.mark.parametrize("m", [np.eye(3), rz(90), rx(180), rz(180) @ rx(30), rx(-179.9), rz(45) @ rx(120)])
def test_quat_roundtrip(m):
    np.testing.assert_allclose(P.quat_to_matrix(P.matrix_to_quat(m)), m, atol=1e-6)


def test_parents_and_globals():
    sk = chain(["Root", "Hip", "Knee"], [np.eye(3), rz(90), np.eye(3)], [[0, 0, 0], [0, 0, 1], [1, 0, 0]])
    assert sk.parents.tolist() == [-1, 0, 1]
    g_rot, g_pos = P.globals_of(sk.parents, sk.rot, sk.pos)
    np.testing.assert_allclose(g_pos[2], [0, 1, 1], atol=1e-9)
    np.testing.assert_allclose(g_rot[2], rz(90), atol=1e-9)


def test_retarget_maps_world_deltas_across_rest_poses_and_axes():
    src = chain(["Root", "Pelvis", "UpLeg"], [np.eye(3)] * 3, [[0, 0, 0], [0, 1.0, 0], [0, -0.5, 0]])
    dst = chain(["RL_BoneRoot", "Hip", "L_Thigh"], [np.eye(3), rx(90), rz(30)], [[0, 0, 0], [0, 0, 2.0], [0, 0, -1.0]])
    swing = rx(40)
    rot = np.array([[np.eye(3), np.eye(3), swing]])
    pos = np.array([[[0, 0, 0], [0, 1.1, 0], [0, -0.5, 0]]])
    clip = P.retarget(src, rot, pos, dst, 30.0, {"Hip": "Pelvis", "L_Thigh": "UpLeg"})
    g_rot, g_pos = P.globals_of(dst.parents, P.quat_to_matrix(clip.quats[0]), clip.trans[0])
    rest_rot, _ = P.globals_of(dst.parents, dst.rot, dst.pos)
    np.testing.assert_allclose(g_rot[2], P.Y_TO_Z @ swing @ P.Y_TO_Z.T @ rest_rot[2], atol=1e-6)
    assert g_pos[1][2] == pytest.approx(2.0 + 0.1 * 2.0)


def test_identity_retarget_reproduces_source():
    src = chain(["Root", "Pelvis", "UpLeg"], [np.eye(3), rz(10), rx(5)], [[0, 0, 0], [0, 1.0, 0], [0, -0.5, 0]])
    rot = np.stack([np.array([np.eye(3), rz(10 + k), rx(5 * k)]) for k in range(4)])
    pos = np.broadcast_to(src.pos, (4, 3, 3)).copy()
    clip = P.retarget(src, rot, pos, src, 30.0, {n: n for n in src.names}, np.eye(3))
    np.testing.assert_allclose(np.abs(np.sum(clip.quats * P.matrix_to_quat(rot), -1)), 1, atol=1e-9)


def clip_of(values, fps=10.0):
    q = np.array([[[np.cos(np.radians(v) / 2), 0, 0, np.sin(np.radians(v) / 2)]] for v in values])
    return P.Clip(q, np.zeros((len(values), 1, 3)), fps)


def angle(q):
    return np.degrees(2 * np.arctan2(q[0, 3], q[0, 0]))


def test_sample_interpolates_and_loops():
    c = clip_of([0, 10, 20, 0])
    assert c.period == pytest.approx(0.3)
    assert angle(P.sample(c, 0.05)[0]) == pytest.approx(5.0, abs=0.1)
    assert angle(P.sample(c, 0.35)[0]) == pytest.approx(5.0, abs=0.1)


def test_gait_idles_when_still_and_syncs_rate_to_speed():
    g = P.Gait(clip_of([0, 10, 20, 0]), clip_of([90, 90]), 0.0)
    g.step(0.1, 0.0)
    assert g.walk_t == 0.0 and angle(g.step(0.0, 0.0)[0]) == pytest.approx(90.0)
    g.step(0.1, P.WALK_CLIP_SPEED)
    assert g.walk_t == pytest.approx(0.1)
    g.step(0.1, 10.0)
    assert g.walk_t == pytest.approx(0.1 + 0.1 * P.WALK_RATE[1])
    mid = angle(g.step(0.0, P.IDLE_BLEND / 2)[0])
    assert 0.0 < mid < 90.0
