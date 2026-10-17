# Pit and haul-road design spec (procedural `default` world)

Code: `envgen/mine_spec.py` (`MineSpec`, presets, `validate()`). Generator: `envgen/procedural.py --truck <key>`.
As-built check: `envgen/measure_geometry.py` reads the measured geometry back out of `heightmap.raw`.
Every value below is also written to `world.json["design"]`, along with the checks, references and as-built numbers.

**Design truck: Komatsu HD465-7 (HD465-7E0)** (default). It is a 55 t rigid quarry truck: 4.24 m wide, 9.35 m long, 4.40 m high, 4.29 m wheelbase, 24.00R35 tires (OD about 2.2 m), 70 km/h top speed and an 8.5 m minimum turning radius.
Other presets: `cat785d` (136 t, 6.64 m wide, 33.00R51), `cat797f` (363 t, 9.76 m, 59/80R63), `komatsu930e` (290 t, 9.19 m, 53/80R63).

| Parameter | Default (HD465) | Standard / rule | Source |
|---|---|---|---|
| Bench height | 10 m | 10–15 m, matched to the loader (here a hydraulic excavator) | Read & Stacey 2009 |
| Bench face angle | 70° (face run 3.64 m) | 60–75° in competent rock | Read & Stacey 2009 |
| Catch berm width | 6.5 m | ≥ modified Ritchie 0.2·H + 4.5 = 6.5 m | [Ryan & Pryor 2000](https://cnitucson.com/publications/2000_ch3__Catch%20Bench_sme_tr_pp.pdf) |
| Inter-ramp angle | 44.6° (derived) | 35–50° | Read & Stacey 2009 |
| Overall slope | 39.7° design for a deep wall with one ramp leg; 34.4° as built (3 benches) | 35–50° | Read & Stacey 2009 |
| Ramp grade | 9 % | max sustained 8–10 % | [USBM IC 8758](https://stacks.cdc.gov/view/cdc/8915) |
| Road width (two-lane, straights) | 15.0 m = 3.54 × 4.24 m | ≥ 3.5 × widest truck (1 lane: 2×) | IC 8758 |
| Ramp width (curved) | 16.5 m | straight width + off-tracking n·(R−√(R²−L²)) + speed term | IC 8758 / AASHTO |
| Safety berm (windrow) height | 1.10 m, 37° sides, 3.4 m base | ≥ mid-axle height of largest truck; practice is ½ tire OD | [30 CFR 56.9300](https://www.law.cornell.edu/cfr/text/30/56.9300) |
| Cross-fall | 2 % (ramp drains to the highwall; surface roads crowned) | 2–3 % | [Cat haul road guide](https://es.scribd.com/doc/52194520/CAT-Haul-Road-Design) |
| Min curve radius | 17.5 m (V = 20 km/h, f = 0.20, e = −2 %); ramp tightest centreline 33 m as built | V²/127(e+f), and ≥ truck clearance radius | IC 8758 |
| Sim truck turning radius | 8.7 m (5 m wheelbase, 30° steer), ×1.5 margin = 13 m; ramp is ≥ 27.5 m | radius ≥ sim turning radius plus margin | team sim spec |
| Pit floor | Ø 40 m | ≥ truck turning clearance diameter (19 m) | Thompson 2011 |
| Ramp layout | 1 spiral turn, 333 m long, 40 m level run-out at the foot, no switchbacks | switchback grade < approach grade and ≤ 10 % (not used) | WV 38-3-5 |

**As built (measured from heightmap.raw, 0.51 m/px), spec vs measured:**
- Face angle: 70° spec, 69.8° measured.
- Catch berm: 6.5 m spec, 6.45 m measured.
- Ramp grade: 9.0 % spec, 9.0 % measured (worst 20 m window 9.01 %).
- Ramp cross-fall: 2.0 % spec, 2.03 % measured.
- Windrow height: 1.10 m spec, 1.03 m measured. The measurement loses about 1 px at the peak.
- Ramp running width: 16.5 m spec, 15.9 m measured. Each edge loses about 1 px.
- Surface road width: 15.0 m spec, 15.15 m measured.

**Compromises (small sim quarry: the world must be 200–260 m; default is 260 m, bounds [-130, -130, 130, 130]):**
- The pit is 30 m deep with 3 benches. The generator drops benches until the crest fits the lease; `design.notes` lists the steps from 7 to 3. Bench height, face angle, berm width, grade, widths and berm height all stay at the standard values.
- In a pit this shallow, one 19.9 m ramp band (running width + windrow) takes up a large share of each wall. The overall slope therefore comes out at 34.4°, below the typical 35–50°. That is flatter, so more conservative, and is reported as a `warning`, not a violation.
- Berms directly above a ramp leg are partial by design, because the ramp is the catch surface there. Full catch berms measure 6.45 m.
- Ramp design speed is 20 km/h. The sim truck's top speed is 28.8 km/h, and a loaded 55 t truck on a 9 % grade does about 15 km/h.
- Bench loading bays are 12 m deep × 28.5 m long. The truck turns using the bay plus the ramp width, as a 3-point turn.
- Komatsu's official HD465-7 PDF could not be retrieved. Width, length, height and wheelbase come from RitchieSpecs. Tire size, the 8.5 m turning radius and the 70 km/h top speed come from distributor sheets. Tire OD (~2.2 m) and the 19 m clearance diameter are estimates. Width is the input that drives the road design, so check it against the official sheet before relying on it.
- Big-truck presets (785D, 797F, 930E) auto-size the world to 500–600 m and keep 6–7 benches.

**References:**
- Kaufman & Ault 1977, *Design of Surface Mine Haulage Roads*, USBM IC 8758.
- R.J. Thompson 2011, "Mine haul road design and management" (SME), and *Mining Haul Roads: Theory and Practice* (CRC 2019).
- MSHA 30 CFR 56.9300.
- Read & Stacey 2009, *Guidelines for Open Pit Slope Design* (CSIRO).
- Ryan & Pryor 2000 (modified Ritchie criterion).
- Caterpillar spec sheets: 785D (cat.com 18089285) and 797F (18093014).
- Komatsu HD465-7E0 product page.
- RitchieSpecs HD465-7 and 785D pages.
