"""PoNR-aware failure warning for haul trucks.

The point of no return (PoNR) is the first tick where the obstacle is closer
than the truck's stopping distance. A warning after PoNR cannot prevent a crash.
"""

from __future__ import annotations

from dataclasses import dataclass

T_REACT_S = 1.5
A_BRAKE_EMPTY = 2.5  # m/s^2
A_BRAKE_LOADED = 1.5  # m/s^2


@dataclass
class TruckState:
    v: float  # m/s
    loaded: bool

    @property
    def a_brake(self) -> float:
        return A_BRAKE_LOADED if self.loaded else A_BRAKE_EMPTY


def stopping_distance(v: float, a_brake: float, t_react: float = T_REACT_S) -> float:
    """Distance covered from the warning until standstill."""
    return v * t_react + v * v / (2.0 * a_brake)


def past_ponr(obstacle_distance: float, truck: TruckState, t_react: float = T_REACT_S) -> bool:
    return obstacle_distance <= stopping_distance(truck.v, truck.a_brake, t_react)


def naive_warn(confidence: float, threshold: float = 0.5) -> bool:
    """Baseline: warn when the CV detector is confident."""
    return confidence > threshold


def ponr_warn(
    obstacle_distance: float,
    truck: TruckState,
    margin_s: float = 2.0,
    t_react: float = T_REACT_S,
) -> bool:
    """Warn when the predicted distance at now + margin is inside the stopping distance."""
    predicted = obstacle_distance - truck.v * margin_s
    return predicted <= stopping_distance(truck.v, truck.a_brake, t_react)
