# Truck FX

Dust plumes (Omni Flow, rendered volumetrically by RTX) and gravel kick-up (PointInstancer of small rocks) behind the haul trucks. The amount and colour depend on the ground under the rear wheels, which comes from a ground property map baked once from the quarry scan.

## Run

| What | Command |
|---|---|
| Tests (Mac) | `uv run --with numpy --with pytest pytest isaac/fx/tests -q` (add `--with usd-core` to include the `TruckFx` USD test) |
| Bake the ground map (Mac or watson, about 2 s) | `uv run --with numpy --with pillow python -m isaac.fx.ground --world data/worlds/quarry --cache downloads/isaac/cache --preview ground.png` |
| Demo + fps (watson) | `isaac/fx/run.sh /mining/isaac/fx/demo.py [--preset high] [--grounds road,dark] [--measure none,low,high]` → `downloads/isaac/fx/demo/` |

## API (for `isaac/sim`)

| Call | When | Notes |
|---|---|---|
| `from isaac.fx.ground import load_or_bake` | once | `ground = load_or_bake(world_dir, cache_dir)` writes or reuses `cache/ground_<hash>.npz` |
| `from isaac.fx import TruckFx` | once | after the stage exists, before `timeline.play()` |
| `fx = TruckFx(stage, [t.id for t in sim.trucks], preset, ground, **overrides)` | once | enables `omni.flowusd` and the `/rtx/flow/*` settings; prims go under `/World/TruckFx` |
| `fx.update(dt, [(truck_id, pos, yaw_deg, speed, contacts)], height)` | every render step | `pos` = Isaac Z-up `to_isaac(sim.position(t))`; `yaw_deg` = sim `t.yaw` (forward = `(sin, cos, 0)` in Isaac); `speed` m/s signed; `contacts` = `None` (rear wheels derived) or `(2, 3)` rear contact points; `height` = optional `f(x, y) -> z` arrays, e.g. `ground.height_at` |
| `fx.set_wind([wx, wy, wz])` | any time | m/s, Isaac frame |
| `fx.surface_state(truck_id)` | any time | smoothed `dust`, `gravel`, `color`, `tone` per rear wheel and `slip`; reuse for tyre tracks |
| `fx.ground.sample(x, y)` | any time | O(1) `(dust, gravel, rgb)` lookups for any effect |
| `fx.sensor_spheres()` | if wanted | gravel `(centres, radii)` for adding to Warp raycasts |
| `fx.timing()` | stats | mean CPU ms per `update` |

## Presets

| Preset | Dust | Flow cell (m) | Flow steps/s | Emitters per truck | Vorticity | Self-shadow steps | Max stones per truck | Stones/s per m/s above 1 m/s |
|---|---|---|---|---|---|---|---|---|
| `off` | no | | | | | | 0 | |
| `low` | Flow | 0.5 | 30 | 2 (rear wheels) | 0 | 8 | 60 | 6 |
| `high` | Flow | 0.3 | 30 | 3 (wheels + body wake) | 1.0 | 16 | 150 | 15 |

## Overrides (`TruckFx(..., key=value)`)

| Key | Default | Meaning |
|---|---|---|
| `seed` | 0 | gravel RNG (one stream per truck, in registration order) |
| `wind` | [1.5, 0.5, 0] | m/s, Isaac frame; drives a non-allocating Flow box emitter |
| `dust_color` | [0.66, 0.57, 0.45] | colour used when no ground map is given |
| `dust_brightness` | 1.0 | multiplies the colormap scale |
| `dust_density` | 1.0 | multiplies ray-march attenuation (opacity) |
| `sun_intensity`, `sun_elevation`, `sun_azimuth`, `dome_intensity` | 3000, 40, 220, 700 | match `isaac/sim/config.json`; set dust brightness and self-shadow direction |
| `min_speed`, `full_speed` | 1.0, 8.0 | dust starts / saturates (m/s) |
| `rear_axle`, `half_track` | -2.425, 1.5 | rear contact offsets from truck origin (m) |
| `sensor_hits` | false | reserved; see Sensors |

## Ground map

| Field | Meaning |
|---|---|
| `color` | dust colour per 1 m cell: scan albedo, 25 % greyed, ×1.12 |
| `dust` | 0..1: bright, unsaturated, non-green cells high; haul-road corridors at least 0.55; ×(1 − 0.7·rock) for slopes 15–35° |
| `gravel` | 0..1: road corridor 0.85, else albedo texture variance; zero on vegetation |
| `road`, `slope`, `height`, `surface`, `coverage` | road mask, slope (deg, heightmap), heightmap z, scan top z, scan coverage |

Bake: GLB triangles × 4 barycentric samples → texture lookups → per-cell top-surface mean. World `bounds` grid; 24-step neighbour fill outside the scan. About 2 s for 1.5 M triangles.

## Runtime per truck per step

| Step | Cost |
|---|---|
| Rear-wheel cell lookup (2 cells) | O(1) numpy indexing |
| Smoothing of dust, gravel, colour (time constant 0.5 s) | exponential |
| Slip proxy = 0.5·yaw rate/30 °/s + 0.5·|accel|/2 m/s², clipped to 1 | |
| Dust level per wheel = ((speed − 1)/7)^1.5 · dust · (1 + 0.6·slip), clipped to 1.3, × forward-wind suppression | |
| Dust colour = Flow temperature channel indexing an 8-colour palette (map luminance quantiles) | |
| Gravel rate = 15 · (speed − 1) · gravel · (1 + 0.5·slip) stones/s; rock tone from wheel colour (3 tones × 3 shapes) | |

## Sensors and physics

| Consumer | Hit by dust | Hit by gravel |
|---|---|---|
| RTX RGB cameras | yes (volumetric, partial occlusion) | yes |
| Warp depth / lidar (`isaac/sim/raycast.py`) | no | no (add `fx.sensor_spheres()` to the caster to change) |
| PhysX | no (no collision APIs; Flow `physicsCollisionEnabled` off) | no |
| Own front camera | no: emitters sit behind the rear axle and are cut when tail wind along the heading exceeds 0.3–0.6 × speed | no |

## Determinism

| Part | Reproducible with `seed` |
|---|---|
| Gravel spawn, flight, shapes | yes (bit-identical for the same inputs) |
| Emitter positions, rates, colours | yes |
| Flow volume | GPU simulation, close but not bit-identical between runs |

## Files

| File | Isaac needed | Role |
|---|---|---|
| `presets.py` | no | truck geometry constants, presets, `resolve()` |
| `gravel.py` | no | numpy ballistic stones, seeded per truck |
| `dust.py` | no | ground-aware `Surface` smoothing, emitter placement and levels |
| `ground.py` | no (pillow for bake) | GLB reader, ground map bake, `GroundMap` lookups |
| `fx.py` | pxr (+ `omni.flowusd` for dust) | `TruckFx`: Flow prims, rock instancer, per-step USD writes |
| `demo.py` | Isaac | quarry scan + 3 trucks at 8 m/s, chase and follower cameras, fps runs |
| `run.sh` | docker | `mining-isaac-fx` container |
