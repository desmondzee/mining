from pathlib import Path

import numpy as np
import pytest

from brain import planning, recovery
from brain.planning import driver
from brain.planning.navmap import load
from brain.world import Truck

QUARRY = Path(__file__).resolve().parents[2] / "data" / "worlds" / "quarry"


@pytest.fixture
def nav():
    if not (QUARRY / "navgrid.json").is_file():
        pytest.skip("no quarry world")
    recovery.reset()
    return load(QUARRY)


def pocket_truck(nav) -> Truck:
    truck = Truck("truck-2", x=-166.34, z=52.84, yaw=84.93)
    truck.set_goal(nav.sites["ore_1"], "ore_1")
    truck.path = planning.plan_route(nav, (truck.x, truck.z), truck.goal).tolist()
    return truck


def decide(nav, truck):
    d, _ = driver.drive(nav, np.asarray(truck.path), truck.path_idx, truck.x, truck.z, truck.yaw, 0.0, 0.0, None)
    return d


def test_reverses_out_of_a_pocket_then_replans(nav):
    truck = pocket_truck(nav)
    d = decide(nav, truck)
    assert not (d.safe & (driver.CAND_SPEED > 0)).any()
    assert recovery.reverse(truck, nav, d, now=0.0) is None
    assert recovery.reverse(truck, nav, d, now=2.0) is None
    assert recovery.reverse(truck, nav, d, now=3.5) == pytest.approx(-recovery.REVERSE_SPEED / driver.MAX_V)
    truck.x -= 6.0
    assert recovery.reverse(truck, nav, d, now=8.0) < 0
    truck.x -= 5.0
    old = truck.path
    assert recovery.reverse(truck, nav, d, now=12.0) is None
    assert truck.path != old and truck.path_idx == 0
    assert (decide(nav, truck).safe & (driver.CAND_SPEED > 0)).any()


def test_no_reverse_while_a_forward_candidate_is_safe(nav):
    truck = Truck("truck-1", x=-94.36, z=-3.55, yaw=98.1)
    truck.set_goal(nav.sites["ore_1"], "ore_1")
    truck.path = planning.plan_route(nav, (truck.x, truck.z), truck.goal).tolist()
    d = decide(nav, truck)
    assert all(recovery.reverse(truck, nav, d, now=t) is None for t in (0.0, 5.0, 10.0))
