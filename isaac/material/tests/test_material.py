import math

import numpy as np
import pytest

from isaac.material import geometry as G
from isaac.material import rig as R
from isaac.material.model import Model, relax

DT = 1 / 30


def scene(seed=0, truck_at=(-5.4, -0.5), sites=None, config=None):
    base = R.base_matrix(0, 0, 0, 0)
    rig = R.ExcavatorRig("exc-0", base, phase=0.0, lift_deg=25)
    truck = np.eye(4)
    truck[:3, 3] = [*truck_at, 0.0]
    cfg = {"seed": seed, **(config or {})}
    model = Model(G.bucket_shape(), G.bed_shape(), [{"id": "exc-0", "ground": 0.0}], [{"id": "truck-0"}], cfg, sites or [])
    return rig, truck, model


def run(rig, truck, model, seconds):
    for _ in range(int(seconds / DT)):
        rig.step(DT, True)
        name, t = rig.state()
        model.update(DT, [("exc-0", rig.bucket(), name, t)], [("truck-0", truck, "exc-0")])


def test_rig_offsets_match_glb():
    for part, offset in R.rest_offsets().items():
        np.testing.assert_allclose(offset, R.OFFSET[part], atol=1e-9)


def test_pose_matches_keyframes():
    np.testing.assert_allclose(R.pose(0.30), [0, 14, -12, -10])
    np.testing.assert_allclose(R.pose(0.70), [R.DUMP_YAW, -12, -35, -110])
    assert R.phase_name(0.2)[0] == "dig" and R.phase_name(0.65)[0] == "dump"


def test_bed_bounds_inside_hd465():
    b = G.bed_shape()
    assert 3.0 < b.u[-1] - b.u[0] < 4.2
    assert 5.0 < b.v[-1] - b.v[0] < 7.0
    assert 2.0 < b.floor.min() < b.floor.max() < b.rim < 4.0


def test_bucket_cavity_volume():
    s = G.bucket_shape()
    assert 2.0 < s.struck < s.heaped < 4.5


def test_mass_conserved_through_cycles():
    rig, truck, model = scene()
    run(rig, truck, model, 3 * R.CYCLE_S)
    a = model.audit()
    assert a["dug"] > 2.5 * model.cfg.pass_t
    assert abs(a["balance"]) < 1e-9
    assert model.load("truck-0") > 0.4


def test_fill_then_dump():
    rig, truck, model = scene()
    peak = 0.0
    for _ in range(int(R.CYCLE_S / DT)):
        rig.step(DT, True)
        name, t = rig.state()
        model.update(DT, [("exc-0", rig.bucket(), name, t)], [("truck-0", truck, "exc-0")])
        peak = max(peak, model.bucket_tonnes("exc-0"))
    assert peak > 0.85 * model.cfg.pass_t
    assert model.bucket_tonnes("exc-0") < 0.01
    assert model.tonnes("truck-0") > 0.7 * model.cfg.pass_t


def test_no_truck_goes_to_pile():
    rig, truck, model = scene(truck_at=(60.0, 60.0))
    run(rig, truck, model, R.CYCLE_S + 1)
    assert model.tonnes("truck-0") == 0
    assert model.pile[0] > 0.9 * model.audit()["dug"]


def test_empty_truck_delivers_load():
    rig, truck, model = scene()
    run(rig, truck, model, 2 * R.CYCLE_S)
    loaded = model.tonnes("truck-0")
    model.empty_truck("truck-0")
    for _ in range(int(4 / DT)):
        model.update(DT, [], [("truck-0", truck, None)])
    assert model.tonnes("truck-0") == 0
    assert model.delivered[0] == pytest.approx(loaded, rel=1e-9)
    assert abs(model.audit()["balance"]) < 1e-9


def test_auto_empty_at_dump_site():
    rig, truck, model = scene(sites=[(-5.4, -0.5, 30.0)], config={"stop_s": 0.5})
    model.height[0, 8:14, 4:12] = 0.8
    for _ in range(int(4 / DT)):
        model.update(DT, [], [("truck-0", truck, None)])
    assert model.tonnes("truck-0") == 0


def test_repose_relaxation_conserves_and_limits_slope():
    h = np.zeros((1, 20, 20))
    h[0, 10, 10] = 5.0
    floor = np.zeros((20, 20))
    out = relax(h.copy(), floor[None], 0.2, math.tan(math.radians(37)), 400)
    assert out.sum() == pytest.approx(5.0)
    assert out.min() >= 0
    s = out[0]
    slope = max(np.abs(np.diff(s, axis=0)).max(), np.abs(np.diff(s, axis=1)).max()) / 0.2
    assert slope < math.tan(math.radians(37)) * 1.05


def test_deterministic():
    runs = []
    for _ in range(2):
        rig, truck, model = scene(seed=5)
        run(rig, truck, model, R.CYCLE_S)
        runs.append((model.height.copy(), model.parts.pos.copy(), model.pile.copy()))
    for a, b in zip(*runs):
        np.testing.assert_array_equal(a, b)


def test_step_budget():
    import time

    rigs, models = [], []
    base = [R.base_matrix(30.0 * i, 0, 0, 0) for i in range(3)]
    exc = [{"id": f"exc-{i}", "ground": 0.0} for i in range(3)]
    trk = [{"id": f"truck-{i}"} for i in range(3)]
    model = Model(G.bucket_shape(), G.bed_shape(), exc, trk, {})
    rigs = [R.ExcavatorRig(f"exc-{i}", base[i], lift_deg=25) for i in range(3)]
    trucks = []
    for i in range(3):
        m = np.eye(4)
        m[:3, 3] = [30.0 * i - 5.4, -0.5, 0.0]
        trucks.append(m)
    spent, steps = 0.0, int(2 * R.CYCLE_S / DT)
    for _ in range(steps):
        items = []
        for r in rigs:
            r.step(DT, True)
            items.append((r.id, r.bucket(), *r.state()))
        t0 = time.perf_counter()
        model.update(DT, items, [(f"truck-{i}", trucks[i], f"exc-{i}") for i in range(3)])
        model.instances(), model.bucket_points(), model.bed_points()
        spent += time.perf_counter() - t0
    assert spent / steps < 0.0015
