# Isaac look

Lighting, sky, haze, exposure and material response for the Isaac Sim quarry (`isaac/sim/`). One call after the stage is built:

```python
from isaac.look import apply_look

look = apply_look(stage, "day", world_dir=sim.world.dir, cache=Path(args.cache) / "look")
look.update(dt)  # only needed when sky.drift_deg_per_s != 0
```

Overrides are deep-merged over the preset: `apply_look(stage, "dusty", world_dir=..., fog={"density": 0.15}, exposure={"ev": -1.0})`. Calling it again on the same stage re-applies cleanly (prims are reused).

## API

| Name | Meaning |
|---|---|
| `apply_look(stage, preset="day", world_dir=None, cache=None, **overrides) -> Look` | carb render settings + `/World/Sky` dome + `/World/Sun` + materials under `/World/Scan`, `/World/Surround`, `/World/Trucks`, `/World/Equipment` |
| `world_dir` | world directory; `sky.texture` resolves against it |
| `cache` | writable dir for derived sky textures (sun removed, desaturated, tinted); without it the raw pano is used |
| `Look.update(dt)` | rotates the dome by `sky.drift_deg_per_s` (cloud drift) |
| `Look.elevation`, `Look.azimuth`, `Look.sun_dir` | resolved sun (deg, compass from Isaac +Y toward +X; unit vector toward the sun) |
| `load_preset(name, **overrides) -> dict` | resolved config without touching a stage |

## Presets (`presets.json`)

| Preset | Sun elev / az (deg) | Sun intensity, colour | Sky | Fog density | EV |
|---|---|---|---|---|---|
| `day` | pano sun (56.1 / 220) | 2800, warm white | photoreal pano, sun aligned with the distant light | 0.04 | -1.3 |
| `morning` | 20 / 105 | 2500, warm | pano, pano sun removed, slightly cool | 0.07 | -0.9 |
| `golden_hour` | 9 / 255 | 6000, orange | pano, sun removed, warm tint, sat 0.9 | 0.06 | 0 |
| `overcast` | pano sun, angle 8 | 450, cool | pano, sun removed, sat 0.15, gamma 0.75 | 0.10 | -0.6 |
| `dusty` | pano sun | 2300, warm | pano, sat 0.55, tan tint | 0.25 (tan) | -1.1 |

## Keys

| Key | Default | Meaning |
|---|---|---|
| `sun.azimuth`, `sun.elevation` | `"pano"` | number, or `"pano"`: azimuth = `sky.pano_azimuth`, elevation = brightest region of the pano |
| `sun.intensity`, `sun.angle`, `sun.color` | 2800, 0.53, warm | `DistantLight`; angle 0.53 = real sun disc, soft penumbra |
| `sun.shadows` | true | `UsdLux.ShadowAPI` `inputs:shadow:enable` on the sun |
| `sky.texture` | `sky_pano.jpg` | latlong dome texture; `""` = untextured dome |
| `sky.intensity`, `sky.color`, `sky.visible` | 1100, white, true | dome light; `visible` = `visibleInPrimaryRay` |
| `sky.pano_azimuth` | 220 | world azimuth the pano's own sun is rotated to |
| `sky.latlong_offset`, `sky.latlong_sign` | 93, 1 | measured RTX latlong mapping: azimuth = offset + sign·u·360 − rotateZ |
| `sky.remove_sun_deg` | 0 | radius of the pano sun blended out (presets whose sun is not the pano's) |
| `sky.saturation`, `sky.gamma`, `sky.tint` | 1, 1, white | derived texture (needs `cache`) |
| `sky.drift_deg_per_s` | 0 | dome rotation rate via `Look.update` |
| `fog.*` | on, density 0.04 | `/rtx/fog/*` distance fog, Z-up; works in RealTimePathTracing; also hazes the dome |
| `exposure.ev`, `iso`, `shutter`, `fnumber` | -1.3, 100, 50, 5 | fixed exposure, `filmIso = iso · 2^ev` |
| `exposure.auto`, `min_ev`, `max_ev` | false, 9, 15 | histogram auto-exposure with clamps (off: fixed EV keeps CV frames consistent) |
| `tonemap.op` | `aces` | `clamp linear reinhard reinhard_mod hable_alu hable_uc2 aces iray` |
| `tonemap.saturation`, `contrast`, `gain`, `gamma`, `offset`, `whitepoint` | 1.05, 1, 1, 1, 0, 1 | `/rtx/post/colorcorr/*` |
| `materials.scan` | tint (0.66, 0.58, 0.46), rough 0.92, spec 0.02 | scan `UsdPreviewSurface`: texture scale, specular workflow, no sheen |
| `materials.surround.mode` | `mdl` | `mdl` = `vertex_colour.mdl` (`scene::data_lookup_float3("displayColor")`), `preview`, `unbound` |
| `materials.surround.gain` | 0.3 | vertex colour multiplier |
| `materials.truck`, `materials.excavator` | rough 0.6 / 0.5 | `default` + `by_name` (`m0` body paint rough 0.38 clearcoat 0.3, `m6` glass rough 0.08, `m3`/`m7` frame metallic 0.2) |
| `rtx` | `{}` | extra raw carb settings |

## Findings (watson, 6.2.0 aarch64)

| Element | Result |
|---|---|
| Sky | photoreal pano as latlong dome, rotated so its sun sits on the distant light; separate `DistantLight` for sun lighting and shadows |
| Pano orientation | measured: u = 0.197 (sun) lands at azimuth 93 + 71 − rotateZ; no X/Y rotation needed in the Z-up stage |
| Dynamic sky | `omni.kit.environment.core` 1.4.4 present, but sky assets (`Assets/Skies/Dynamic/*.usd`, 2020, procedural MDL sky sphere, 2D clouds) are only on the public S3 content server; not used |
| Volumetric clouds | no RTX volumetric cloud feature in 6.2 RealTimePathTracing; clouds come from the pano |
| Fog | `/rtx/fog/*` renders in RealTimePathTracing (verified with red fog); `/rtx/raytracing/inscattering/*` is RTX 1.0 only |
| Shadows | `/rtx/shadows/enabled` is ignored by RealTimePathTracing; use the light's ShadowAPI |
| Surround vertex colours | MDL `data_lookup_float3`, `UsdPrimvarReader_float3` (string `varname`) and unbound meshes all render the same pale grey-green at gain 1: the colours do arrive; the washed-out look was exposure plus pale source colours. MDL is kept for its gain/roughness knobs (gain 0.3 gives a forest olive) |
| Exposure | before: road floor near white (mean 185/255); `day`: 143, sky blue, no clipped pixels |

## Cost (4 truck cameras, one tiled 640×360 render product, fresh frames via `rep.orchestrator.step(rt_subframes=1)`)

Measured while the live `mining-isaac-sim` was running on the same GPU (94 % util throughout), so absolute fps is about a third of an idle GPU; compare rows only.

| Variant | Fresh fps |
|---|---|
| before (scene.py lights) | 2.7–4.1 |
| `day` | 4.0–4.2 |
| `day`, fog off | 4.0 |
| `day`, shadows off | 4.1 |
| `day`, untextured dome | 4.3 |
| `golden_hour` / `morning` / `dusty` | 4.1 / 5.0 / 4.1 |

Fog, sun shadows, pano dome and the MDL surround cost nothing measurable (within noise).

## Bench

| Step | Command (watson, from `~/Documents/mining/downloads/isaac/look-work`) |
|---|---|
| Stills + fps for every preset and `before` | `docker run ... /mining/isaac/look/bench.py --cache /cache --world /mining/data/worlds/quarry --out /out` |
| Custom variants | `--plan plan.json` (list of `{"name", "preset", "overrides"}` or `{"name", "before": true}`) |
| Sky calibration views | `--sky-cams` |
| Only stills / only fps | `--no-fps` / `--no-stills` |
