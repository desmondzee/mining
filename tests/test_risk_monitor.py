import pytest

from risk_monitor.core import TruckState, ponr_warn, stopping_distance
from risk_monitor.scenario import run


def test_stopping_distance_is_longer_when_loaded():
    assert stopping_distance(10.0, 2.5) == pytest.approx(15.0 + 20.0)
    assert stopping_distance(10.0, 1.5) == pytest.approx(15.0 + 100.0 / 3.0)


def test_ponr_warn_fires_margin_seconds_before_ponr():
    truck = TruckState(v=10.0, loaded=True)
    d_stop = stopping_distance(10.0, truck.a_brake)
    assert not ponr_warn(d_stop + 20.1, truck, margin_s=2.0)
    assert ponr_warn(d_stop + 19.9, truck, margin_s=2.0)


def test_naive_policy_crashes_loaded_truck():
    tr = run("naive", loaded=True, v0=10.0)
    assert tr.collided
    assert tr.lead_time < 0


def test_ponr_policy_stops_loaded_truck_before_obstacle():
    tr = run("ponr", loaded=True, v0=10.0)
    assert not tr.collided
    assert tr.warned_before_ponr
    assert tr.lead_time == pytest.approx(2.0, abs=0.1)
