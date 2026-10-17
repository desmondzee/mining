import math

import numpy as np

from .gravel import intensity, truck_frame
from .presets import TYRE_RADIUS

WHEEL_RADIUS = 1.3
WAKE_RADIUS = 2.2
WHEEL_LIFT = 1.8
WHEEL_SPREAD = 2.0
WAKE_DRAG = 0.3
WAKE_HEIGHT = 1.6
WAKE_SHARE = 0.6
SMOKE = 1.0
COUPLE = 1.0
AHEAD_LIMIT = 0.6
SMOOTH_TIME = 0.5
SLIP_YAW_RATE = 30.0
SLIP_ACCEL = 2.0
SLIP_GAIN = 0.6
LEVEL_MAX = 1.3


def forward_suppression(speed: float, wind, forward) -> float:
    ahead = float(np.dot(wind, forward))
    reach = AHEAD_LIMIT * abs(speed)
    if ahead <= reach * 0.5:
        return 1.0
    return float(np.clip((reach - ahead) / max(reach * 0.5, 1e-6), 0.0, 1.0))


class Surface:
    def __init__(self, ground, palette: np.ndarray) -> None:
        self.ground, self.palette = ground, palette
        self.state: dict[str, dict] = {}

    def update(self, truck_id: str, dt: float, contacts: np.ndarray, yaw: float, speed: float) -> dict:
        dust, gravel, color = self.ground.sample(contacts[:, 0], contacts[:, 1])
        tone = self.ground.tone(color, self.palette)
        s = self.state.get(truck_id)
        if s is None:
            s = self.state[truck_id] = {"dust": dust.astype(float), "gravel": gravel.astype(float), "tone": tone.astype(float), "color": color.astype(float),
                                        "yaw": yaw, "speed": speed, "slip": 0.0}
            return s
        a = 1.0 - math.exp(-max(dt, 0.0) / SMOOTH_TIME)
        for key, value in (("dust", dust), ("gravel", gravel), ("tone", tone), ("color", color)):
            s[key] += (value - s[key]) * a
        if dt > 0:
            yaw_rate = abs((yaw - s["yaw"] + 180.0) % 360.0 - 180.0) / dt
            accel = abs(speed - s["speed"]) / dt
            slip = min(1.0, 0.5 * yaw_rate / SLIP_YAW_RATE + 0.5 * accel / SLIP_ACCEL)
            s["slip"] += (slip - s["slip"]) * a
        s["yaw"], s["speed"] = yaw, speed
        return s


def levels(speed: float, surface: dict, wind, forward, cfg: dict) -> np.ndarray:
    base = intensity(speed, cfg["min_speed"], cfg["full_speed"]) ** 1.5 * forward_suppression(speed, wind, forward)
    return np.clip(base * surface["dust"] * (1.0 + SLIP_GAIN * surface["slip"]), 0.0, LEVEL_MAX)


def emitters(pos, yaw_deg: float, speed: float, contacts, cfg: dict, surface: dict | None = None) -> list[tuple[np.ndarray, float, np.ndarray, float, float]]:
    forward, right = truck_frame(yaw_deg)
    wind = np.asarray(cfg["wind"], float)
    contacts = np.asarray(contacts, float).reshape(-1, 3)
    if surface is None:
        surface = {"dust": np.ones(len(contacts)), "tone": np.full(len(contacts), 0.5), "slip": 0.0}
    lv = levels(speed, surface, wind, forward, cfg)
    back = -1.0 if speed >= 0 else 1.0
    out = []
    for c, level, tone in zip(contacts, lv, surface["tone"]):
        side = 1.0 if np.dot(c - np.asarray(pos, float), right) >= 0 else -1.0
        p = c + forward * back * TYRE_RADIUS * 0.8 + np.array([0.0, 0.0, WHEEL_RADIUS * 0.7])
        v = wind + np.array([0.0, 0.0, WHEEL_LIFT]) * (0.3 + min(level, 1.0)) + right * side * WHEEL_SPREAD * min(level, 1.0)
        out.append((p, WHEEL_RADIUS, v, float(level), float(tone)))
    if cfg.get("emitters", 2) >= 3:
        p = contacts.mean(0) + forward * back * (TYRE_RADIUS + WAKE_RADIUS) + np.array([0.0, 0.0, WAKE_HEIGHT])
        v = wind + forward * speed * WAKE_DRAG
        out.append((p, WAKE_RADIUS, v, float(lv.mean() * WAKE_SHARE), float(np.mean(surface["tone"]))))
    return out
