import numpy as np
import pytest

pytest.importorskip("pxr")

from pxr import Usd, UsdGeom  # noqa: E402

from isaac.sim import world as W  # noqa: E402
from isaac.sim.glb import read_glb  # noqa: E402
from isaac.sim.kinematics import yaw_matrix  # noqa: E402
from isaac.sim.usdbuild import convert, isaac_matrix, to_isaac  # noqa: E402


def mesh_points(path):
    stage = Usd.Stage.Open(str(path))
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    pts = [np.asarray(UsdGeom.Mesh(p).GetPointsAttr().Get()) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    return np.concatenate(pts)


def test_truck_usd_is_z_up_forward_y(tmp_path):
    m, size = W.truck_box()
    pts = mesh_points(convert(W.REPO / W.TRUCK_GLB, tmp_path, "truck", m))
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    np.testing.assert_allclose(hi - lo, [size[0], size[2], size[1]], rtol=1e-4)
    assert lo[2] == pytest.approx(0, abs=1e-4)
    assert (lo[1] + hi[1]) / 2 == pytest.approx(0, abs=1e-4)


def test_surround_maps_world_y_to_z(tmp_path):
    world = W.load_world(W.REPO / "data/worlds/quarry")
    src = world.asset("surround")
    pts = mesh_points(convert(src, tmp_path, "surround", np.eye(4)))
    raw, _ = read_glb(src).triangles()
    np.testing.assert_allclose(pts, to_isaac(raw), atol=1e-3)


def test_isaac_matrix_maps_yaw_forward():
    m = np.array(isaac_matrix(yaw_matrix(90), np.array([1.0, 2.0, 3.0])))
    fwd_local = np.array([0, 1, 0, 0])
    np.testing.assert_allclose(fwd_local @ m, [1, 0, 0, 0], atol=1e-12)
    np.testing.assert_allclose(np.array([0, 0, 0, 1]) @ m, [1, 3, 2, 1])


def test_rig_usd_parts_compose_to_glb(tmp_path):
    from isaac.sim.excavators import JOINTS
    from isaac.sim.glb import transform
    from isaac.sim.usdbuild import TO_ISAAC, convert_rig

    src = W.REPO / W.EXCAVATOR_GLB
    m, _ = W.model_box(src, horizontal=W.EXCAVATOR_LENGTH)
    stage = Usd.Stage.Open(str(convert_rig(src, tmp_path, "excavator", m, JOINTS)))
    assert stage.GetPrimAtPath("/excavator/house/boom/stick/bucket")
    cache = UsdGeom.XformCache()
    got = np.concatenate([
        np.asarray(UsdGeom.Mesh(p).GetPointsAttr().Get()) @ np.asarray(cache.GetLocalToWorldTransform(p))[:3, :3] + np.asarray(cache.GetLocalToWorldTransform(p))[3, :3]
        for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) and "bucket" in str(p.GetPath())
    ])
    want = np.concatenate([p.points for p in transform(read_glb(src), TO_ISAAC @ m).primitives if p.node == "bucket"])
    np.testing.assert_allclose(got, want, atol=1e-3)
