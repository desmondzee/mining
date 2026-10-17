TRUCK_LENGTH = 9.35
REAR_AXLE_FROM_REAR = 2.25
REAR_HALF_TRACK = 1.5
DUAL_HALF_WIDTH = 0.55
TYRE_RADIUS = 1.1
GRAVITY = 9.81

DEFAULTS = {
    "seed": 0,
    "wind": [1.5, 0.5, 0.0],
    "dust_color": [0.66, 0.57, 0.45],
    "sun_intensity": 3000.0,
    "sun_elevation": 40.0,
    "sun_azimuth": 220.0,
    "dome_intensity": 700.0,
    "sensor_hits": False,
    "min_speed": 1.0,
    "full_speed": 8.0,
    "rear_axle": -TRUCK_LENGTH / 2 + REAR_AXLE_FROM_REAR,
    "half_track": REAR_HALF_TRACK,
}

PRESETS = {
    "off": {"dust": False, "gravel": 0},
    "low": {
        "dust": True,
        "gravel": 60,
        "cell": 0.5,
        "emitters": 2,
        "steps_per_second": 30.0,
        "vorticity": 0.0,
        "shadow_steps": 8,
        "gravel_rate": 6.0,
    },
    "high": {
        "dust": True,
        "gravel": 150,
        "cell": 0.3,
        "emitters": 3,
        "steps_per_second": 30.0,
        "vorticity": 1.0,
        "shadow_steps": 16,
        "gravel_rate": 15.0,
    },
}


def resolve(preset: str = "high", **overrides) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}, expected one of {sorted(PRESETS)}")
    cfg = {**DEFAULTS, **PRESETS[preset], "preset": preset}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg
