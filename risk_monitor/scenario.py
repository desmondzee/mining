"""1D approach scenario: one truck drives toward one static obstacle."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .core import T_REACT_S, TruckState, naive_warn, ponr_warn, stopping_distance

# Stand-in for the CV detector until the CV workstream sends real confidences.
CV_RANGE_50 = 25.0  # m, distance where confidence crosses 0.5
CV_SLOPE = 3.0  # m


def cv_confidence(distance: float) -> float:
    return 1.0 / (1.0 + math.exp((distance - CV_RANGE_50) / CV_SLOPE))


@dataclass
class Trace:
    policy: str
    loaded: bool
    t: List[float] = field(default_factory=list)
    distance: List[float] = field(default_factory=list)
    v: List[float] = field(default_factory=list)
    confidence: List[float] = field(default_factory=list)
    t_warn: Optional[float] = None
    t_ponr: Optional[float] = None
    collided: bool = False

    @property
    def warned_before_ponr(self) -> bool:
        return self.t_warn is not None and (self.t_ponr is None or self.t_warn < self.t_ponr)

    @property
    def lead_time(self) -> Optional[float]:
        """t_PoNR - t_warn. Negative means the warning came too late."""
        if self.t_warn is None or self.t_ponr is None:
            return None
        return self.t_ponr - self.t_warn


def run(
    policy: str,
    loaded: bool,
    v0: float = 10.0,
    d0: float = 120.0,
    dt: float = 0.05,
    t_max: float = 30.0,
    margin_s: float = 2.0,
    t_react: float = T_REACT_S,
) -> Trace:
    """Simulate until the truck stops or hits the obstacle.

    Before the warning the truck cruises at v0. After the warning it keeps v0
    for t_react, then brakes at its load-dependent deceleration.
    """
    truck = TruckState(v=v0, loaded=loaded)
    trace = Trace(policy=policy, loaded=loaded)
    # The truck cruises at v0 until it warns, so PoNR is the same for every policy.
    trace.t_ponr = max(0.0, (d0 - stopping_distance(v0, truck.a_brake, t_react)) / v0)
    d, t = d0, 0.0
    while t <= t_max:
        conf = cv_confidence(d)
        trace.t.append(t)
        trace.distance.append(d)
        trace.v.append(truck.v)
        trace.confidence.append(conf)

        if trace.t_warn is None:
            if policy == "naive":
                warn = naive_warn(conf)
            elif policy == "ponr":
                warn = ponr_warn(d, truck, margin_s, t_react)
            else:
                raise ValueError("unknown policy: " + policy)
            if warn:
                trace.t_warn = t

        if d <= 0.0:
            trace.collided = True
            break
        if truck.v <= 0.0:
            break

        braking = trace.t_warn is not None and t - trace.t_warn >= t_react
        if braking:
            truck.v = max(0.0, truck.v - truck.a_brake * dt)
        d -= truck.v * dt
        t += dt

    return trace
