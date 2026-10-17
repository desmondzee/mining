import numpy as np
import pytest

from isaac.sim import excavators as E


def test_cycle_keys():
    assert E.cycle_pose(0.0) == pytest.approx([0.0, 12.0, -42.0, -60.0])
    assert E.cycle_pose(0.6) == pytest.approx([E.TRUCK_SIDE_YAW, -14.0, -15.0, 0.0])
    assert E.cycle_pose(1.0) == pytest.approx(E.cycle_pose(0.0))


def test_rig_tracks_and_rests():
    rig = E.Rig("exc-0", digging=True)
    for _ in range(300):
        rig.step(1 / 30)
    assert rig.pose == pytest.approx(E.cycle_pose(rig.phase), abs=15)
    rig.digging = False
    for _ in range(300):
        rig.step(1 / 30)
    assert max(abs(v) for v in rig.pose) < 0.1
    assert E.Rig("exc-0").phase == E.Rig("exc-0").phase != E.Rig("exc-1").phase


def test_radio_apply():
    rigs = {"exc-0": E.Rig("exc-0"), "exc-1": E.Rig("exc-1")}
    poller = E.RadioPoller("http://x", rigs)
    poller.apply([{"id": "exc-0", "state": "digging", "heading_deg": 10}, {"id": "exc-9", "state": "digging"}])
    assert rigs["exc-0"].digging and not rigs["exc-1"].digging and poller.headings == {"exc-0": 10.0}


def test_rotations():
    np.testing.assert_allclose(E.rot_y(np.pi / 2) @ [0, 0, 1], [1, 0, 0], atol=1e-12)
    np.testing.assert_allclose(E.rot_x(np.pi / 2) @ [0, 0, 1], [0, -1, 0], atol=1e-12)
