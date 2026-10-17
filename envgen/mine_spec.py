"""Open-pit + haul-road design spec tied to a real design truck.

Every geometric number the procedural generator uses (bench height, face angle,
catch-berm width, ramp grade/width, safety-berm height, cross-fall, curve radius)
comes from a MineSpec. Rules of thumb and their sources are listed in
envgen/DESIGN_SPEC.md; `MineSpec.validate()` flags anything outside the
industry-standard range.

    python -m envgen.mine_spec --truck cat785d     # print spec + validation
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------- design trucks
@dataclass(frozen=True)
class Truck:
    key: str
    name: str
    payload_t: float              # nominal payload, metric tonnes
    width_m: float                # widest point (canopy / body / operating width)
    length_m: float
    height_m: float
    wheelbase_m: float
    tire: str
    tire_diameter_m: float        # overall tire diameter (OD)
    top_speed_kmh: float
    turning_diameter_m: float     # front-wheel turning diameter (SAE/ISO)
    clearance_diameter_m: float   # machine clearance turning diameter
    source: str

    @property
    def mid_axle_height_m(self):  # ≈ loaded static radius ≈ 0.47 × OD; we use OD/2 (conservative)
        return self.tire_diameter_m / 2


TRUCKS = {
    "komatsu_hd465": Truck(
        "komatsu_hd465", "Komatsu HD465-7 (HD465-7E0)", payload_t=55, width_m=4.24, length_m=9.35, height_m=4.40,
        wheelbase_m=4.29, tire="24.00R35", tire_diameter_m=2.20, top_speed_kmh=70.0,
        turning_diameter_m=17.0, clearance_diameter_m=19.0,
        source="Komatsu HD465-7E0 product page (55 t nominal payload, komatsu.com); RitchieSpecs HD465-7 "
               "(overall width 13.92 ft, length 30.67 ft, height 14.42 ft, wheelbase 14.08 ft, 43.5 mph); "
               "distributor sheets: 24.00R35 tires, min turning radius 8.5 m, 70 km/h. Clearance diameter "
               "and tire OD (~2.2 m for 24.00R35) are estimates - official PDF not retrievable."),
    "cat785d": Truck(
        "cat785d", "Caterpillar 785D", payload_t=136, width_m=6.64, length_m=12.31, height_m=5.12,
        wheelbase_m=5.18, tire="33.00R51", tire_diameter_m=3.07, top_speed_kmh=54.8,
        turning_diameter_m=29.8, clearance_diameter_m=33.2,
        source="Cat 785D product page / spec sheet (cat.com 18089285); RitchieSpecs 785D"),
    "cat797f": Truck(
        "cat797f", "Caterpillar 797F", payload_t=363, width_m=9.76, length_m=15.08, height_m=7.70,
        wheelbase_m=7.21, tire="59/80R63", tire_diameter_m=4.03, top_speed_kmh=67.6,
        turning_diameter_m=39.6, clearance_diameter_m=42.0,
        source="Cat 797F product page / spec sheet (cat.com 18093014): outside body width 9755 mm"),
    "komatsu930e": Truck(
        "komatsu930e", "Komatsu 930E", payload_t=290, width_m=9.19, length_m=15.6, height_m=7.37,
        wheelbase_m=6.35, tire="53/80R63", tire_diameter_m=3.89, top_speed_kmh=64.5,
        turning_diameter_m=29.7, clearance_diameter_m=32.0,
        source="Komatsu 930E-5 spec sheet; Wikipedia 'Komatsu 930E' (width 9.19 m)"),
}

# per-truck pit presets: bench height matched to the loading tool sized for the truck
BENCH_PRESET = {"komatsu_hd465": 10.0, "cat785d": 12.0, "cat797f": 15.0, "komatsu930e": 15.0}
# in-pit design speed: small quarry with 55 t trucks (sim truck max 8 m/s = 28.8 km/h; loaded on 9 % ~ 15 km/h)
SPEED_PRESET = {"komatsu_hd465": 20.0, "cat785d": 30.0, "cat797f": 30.0, "komatsu930e": 30.0}
# teammates' kinematic sim truck (desmondzee/mining spec.md): bicycle model
SIM_WHEELBASE_M, SIM_MAX_STEER_DEG = 5.0, 30.0
N_BENCH_PRESET = {"komatsu_hd465": 7, "cat785d": 7, "cat797f": 6, "komatsu930e": 6}

REFERENCES = {
    "IC8758": "Kaufman & Ault (1977), Design of Surface Mine Haulage Roads - A Manual, USBM IC 8758. "
              "https://stacks.cdc.gov/view/cdc/8915",
    "MSHA": "30 CFR 56.9300 - berms/guardrails at least mid-axle height of the largest self-propelled "
            "equipment that usually travels the roadway. https://www.law.cornell.edu/cfr/text/30/56.9300",
    "Thompson": "Thompson, R.J. (2011) Mine haul road design and management: a review of current practice "
                "(SME); Thompson, Mining Haul Roads: Theory and Practice (CRC 2019). "
                "https://espace.curtin.edu.au/handle/20.500.11937/22673",
    "CatHaulRoad": "Caterpillar haul road design guidance (2% constant cross-fall, curve widening tables). "
                   "https://es.scribd.com/doc/52194520/CAT-Haul-Road-Design",
    "Ritchie": "Modified Ritchie criterion, catch bench width W = 0.2*H + 4.5 m (Ryan & Pryor 2000, "
               "SME). https://cnitucson.com/publications/2000_ch3__Catch%20Bench_sme_tr_pp.pdf",
    "WV": "W. Va. Code R. 38-3-5 haulageways: switchback grade < approach grade and <= 10%. "
          "https://www.law.cornell.edu/regulations/west-virginia/W-Va-C-S-R-SS-38-3-5",
    "sim": "Team simulator spec (desmondzee/mining spec.md): kinematic truck wheelbase 5 m, max steer 30 deg, "
           "max speed 8 m/s.",
    "Read&Stacey": "Read & Stacey (2009) Guidelines for Open Pit Slope Design (CSIRO): bench face 60-75 deg "
                   "in competent rock, inter-ramp / overall 35-50 deg typical.",
}


def mrc_berm(bench_h):
    """Modified Ritchie criterion minimum catch-berm width (m)."""
    return 0.2 * bench_h + 4.5


# ---------------------------------------------------------------- spec
@dataclass
class MineSpec:
    truck: Truck
    bench_height_m: float = 12.0
    n_benches: int = 7
    face_angle_deg: float = 70.0
    berm_width_m: float | None = None        # default: MRC rounded up to 0.5 m
    ramp_grade_pct: float = 9.0
    lanes: int = 2
    lane_factor: float | None = None         # road width / truck width (IC 8758: 2.0 one lane, 3.5 two lanes)
    cross_fall_pct: float = 2.0
    berm_repose_deg: float = 37.0            # safety windrow side slope (loose rock angle of repose)
    berm_top_m: float = 0.5
    ramp_design_speed_kmh: float = 30.0      # loaded uphill/downhill speed on the in-pit ramp
    side_friction: float = 0.20              # lateral friction for curve radius (V^2 / 127(e+f))
    floor_radius_m: float | None = None      # default: >= machine clearance turning diameter
    rock: str = "competent porphyry (copper)"
    notes: list = field(default_factory=list)

    def __post_init__(self):
        if self.lane_factor is None:
            self.lane_factor = {1: 2.0, 2: 3.5, 3: 5.0}[self.lanes]
        if self.berm_width_m is None:
            self.berm_width_m = math.ceil(mrc_berm(self.bench_height_m) * 2) / 2
        if self.floor_radius_m is None:  # full truck turn on the floor, and ramp foot radius >= curve minimum
            self.floor_radius_m = max(math.ceil(self.truck.clearance_diameter_m * 1.05),
                                      math.ceil(self.min_curve_radius_m - self.road_width_m / 2))

    # ---- derived
    @property
    def face_horizontal_m(self):
        return self.bench_height_m / math.tan(math.radians(self.face_angle_deg))

    @property
    def bench_step_m(self):  # horizontal toe-to-toe distance of one bench
        return self.face_horizontal_m + self.berm_width_m

    @property
    def inter_ramp_angle_deg(self):
        return math.degrees(math.atan(self.bench_height_m / self.bench_step_m))

    @property
    def road_width_m(self):  # running surface, straights; rounded up to 0.5 m
        return math.ceil(self.truck.width_m * self.lane_factor * 2) / 2

    @property
    def berm_height_m(self):  # safety windrow: >= MSHA mid-axle height; industry 1/2 tire OD
        return round(math.ceil(self.truck.tire_diameter_m / 2 * 20) / 20, 2)

    @property
    def berm_base_m(self):
        return round(2 * self.berm_height_m / math.tan(math.radians(self.berm_repose_deg)) + self.berm_top_m, 2)

    @property
    def min_curve_radius_m(self):
        e = -self.cross_fall_pct / 100  # ramp cross-fall drains to the highwall = adverse on the pit-side curve
        v = self.ramp_design_speed_kmh
        r_speed = v * v / (127 * (e + self.side_friction))
        return round(max(self.truck.clearance_diameter_m / 2, r_speed), 1)

    def curve_widening_m(self, radius):
        """Off-tracking allowance per IC 8758 / AASHTO: n * (R - sqrt(R^2 - L^2)) + speed term."""
        L = self.truck.wheelbase_m
        off = self.lanes * (radius - math.sqrt(max(radius * radius - L * L, 0)))
        speed = 0.1 * self.ramp_design_speed_kmh / math.sqrt(radius)
        return off + speed

    @property
    def ramp_width_m(self):  # running width on the (always-curving) spiral ramp at its tightest radius
        r = self.floor_radius_m + self.road_width_m / 2
        return math.ceil((self.road_width_m + self.curve_widening_m(r)) * 2) / 2

    @property
    def depth_m(self):
        return self.bench_height_m * self.n_benches

    @property
    def mrc_berm_m(self):
        return round(mrc_berm(self.bench_height_m), 2)

    def overall_slope_deg(self, ramp_crossings=1):
        """Overall slope crest-to-toe for a wall section crossed by `ramp_crossings` ramp legs."""
        horiz = self.n_benches * self.bench_step_m - self.berm_width_m \
            + ramp_crossings * (self.ramp_width_m + self.berm_base_m)
        return math.degrees(math.atan(self.depth_m / horiz))

    # ---- checks
    def checks(self):
        t = self.truck
        c = []

        def chk(name, value, lo, hi, unit, ref, note="", severity="error"):
            ok = (lo is None or value >= lo - 1e-9) and (hi is None or value <= hi + 1e-9)
            c.append({"param": name, "value": round(value, 2), "min": None if lo is None else round(lo, 2), "max": None if hi is None else round(hi, 2), "unit": unit,
                      "ok": bool(ok), "severity": severity, "ref": ref, "note": note})

        chk("bench_height_m", self.bench_height_m, 10, 15, "m", "Read&Stacey",
            "matched to loader reach (HD465 + ~6-7 m3 excavator: 10 m; 785D-class: 10-12 m; 797F-class shovels: 15 m)")
        chk("face_angle_deg", self.face_angle_deg, 60, 75, "deg", "Read&Stacey")
        chk("berm_width_m", self.berm_width_m, self.mrc_berm_m, 10, "m", "Ritchie",
            f"MRC minimum 0.2*H+4.5 = {self.mrc_berm_m} m")
        chk("inter_ramp_angle_deg", self.inter_ramp_angle_deg, 35, 50, "deg", "Read&Stacey")
        chk("overall_slope_deg", self.overall_slope_deg(1), 35, 50, "deg", "Read&Stacey",
            "flatter than 35 deg is conservative (a shallow pit where one ramp leg is a large share of the wall)",
            severity="warning")
        chk("ramp_grade_pct", self.ramp_grade_pct, 8, 10, "%", "IC8758",
            "max sustained grade ~8-10%; >10% only for short pitches")
        chk("road_width_factor", self.road_width_m / t.width_m, {1: 2.0, 2: 3.5, 3: 5.0}[self.lanes],
            {1: 2.5, 2: 4.0, 3: 5.5}[self.lanes], "x truck width", "IC8758",
            f"{self.lanes}-lane running width on straights")
        chk("ramp_width_m", self.ramp_width_m, self.road_width_m, None, "m", "IC8758",
            "straight width + off-tracking widening on curves")
        chk("berm_height_m", self.berm_height_m, t.mid_axle_height_m * 0.94, None, "m", "MSHA",
            f"MSHA mid-axle height ~{t.mid_axle_height_m * 0.94:.2f} m (loaded radius); practice 1/2-3/4 tire OD")
        chk("cross_fall_pct", self.cross_fall_pct, 2, 3, "%", "CatHaulRoad")
        chk("ramp_min_radius_m", self.floor_radius_m + self.road_width_m / 2, self.min_curve_radius_m, None, "m",
            "IC8758", f"V={self.ramp_design_speed_kmh} km/h, f={self.side_friction}; >= truck clearance radius")
        sim_r = SIM_WHEELBASE_M / math.tan(math.radians(SIM_MAX_STEER_DEG))
        chk("ramp_radius_vs_sim_truck_m", self.floor_radius_m + self.road_width_m / 2, 1.5 * sim_r, None, "m", "sim",
            f"sim kinematic truck min turning radius {sim_r:.1f} m (wheelbase 5 m, 30 deg steer) x1.5 margin")
        chk("floor_diameter_m", 2 * self.floor_radius_m, t.clearance_diameter_m, None, "m", "Thompson",
            "pit floor must allow a full truck turn")
        return c

    def validate(self, severity="error"):
        """List of human-readable problems (empty = all parameters within the standard range)."""
        out = []
        for c in self.checks():
            if not c["ok"] and c["severity"] == severity:
                rng = f"[{c['min'] if c['min'] is not None else '-inf'}, {c['max'] if c['max'] is not None else 'inf'}]"
                out.append(f"{c['param']}={c['value']} {c['unit']} outside standard range {rng} ({c['ref']})")
        return out

    def to_dict(self):
        t = self.truck
        return {
            "design_truck": {**asdict(t), "mid_axle_height_m": round(t.mid_axle_height_m, 2)},
            "rock": self.rock,
            "bench_height_m": self.bench_height_m, "n_benches": self.n_benches, "pit_depth_m": self.depth_m,
            "face_angle_deg": self.face_angle_deg, "face_horizontal_m": round(self.face_horizontal_m, 2),
            "berm_width_m": self.berm_width_m, "mrc_min_berm_m": self.mrc_berm_m,
            "inter_ramp_angle_deg": round(self.inter_ramp_angle_deg, 1),
            "overall_slope_deg": round(self.overall_slope_deg(1), 1),
            "overall_slope_deg_two_ramp_legs": round(self.overall_slope_deg(2), 1),
            "ramp_grade_pct": self.ramp_grade_pct, "lanes": self.lanes, "lane_factor": self.lane_factor,
            "road_width_m": self.road_width_m, "ramp_width_m": self.ramp_width_m,
            "safety_berm_height_m": self.berm_height_m, "safety_berm_base_m": self.berm_base_m,
            "safety_berm_side_deg": self.berm_repose_deg,
            "cross_fall_pct": self.cross_fall_pct,
            "ramp_design_speed_kmh": self.ramp_design_speed_kmh,
            "min_curve_radius_m": self.min_curve_radius_m,
            "floor_radius_m": self.floor_radius_m,
            "ramp_type": "spiral (no switchbacks); ramp cross-falls toward the highwall ditch",
            "checks": self.checks(), "violations": self.validate(), "warnings": self.validate("warning"),
            "references": REFERENCES, "notes": self.notes,
        }


DEFAULT_TRUCK = "komatsu_hd465"


def preset(key=DEFAULT_TRUCK, **kw):
    key = key.lower()
    if key not in TRUCKS:
        raise SystemExit(f"unknown truck {key!r}; choose from {sorted(TRUCKS)}")
    kw.setdefault("bench_height_m", BENCH_PRESET[key])
    kw.setdefault("n_benches", N_BENCH_PRESET[key])
    kw.setdefault("ramp_design_speed_kmh", SPEED_PRESET[key])
    return MineSpec(TRUCKS[key], **kw)


def table(spec):
    rows = [f"{'param':24s} {'value':>8s}  range            ok  ref"]
    for c in spec.checks():
        rng = f"[{c['min'] if c['min'] is not None else '':}, {c['max'] if c['max'] is not None else ''}]"
        rows.append(f"{c['param']:24s} {c['value']:8.2f}  {rng:16s} {'ok' if c['ok'] else 'NO':3s} {c['ref']}")
    return "\n".join(rows)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--truck", default=DEFAULT_TRUCK, choices=sorted(TRUCKS))
    s = preset(ap.parse_args().truck)
    print(s.truck.name, f"road {s.road_width_m} m, ramp {s.ramp_width_m} m, berm h {s.berm_height_m} m")
    print(table(s))
    print("violations:", s.validate() or "none", "| warnings:", s.validate("warning") or "none")
