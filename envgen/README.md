# envgen: world generation

Run from the repo root. Output goes to `$DATA_DIR/worlds/<id>/` (default `./data/worlds/<id>/`).

```bash
python3.12 -m venv envgen/.venv.nosync   # ".nosync" keeps iCloud from evicting venv files in ~/Documents
envgen/.venv.nosync/bin/pip install -r envgen/requirements.txt
PY=envgen/.venv.nosync/bin/python
```

## Procedural fallback (no keys, ~2 s)
```bash
$PY -m envgen.procedural --seed 42 --id default                 # design truck komatsu_hd465 (default), 260 m world
$PY -m envgen.procedural --id big --truck cat785d               # or cat797f | komatsu930e (world auto-sized)
$PY -m envgen.mine_spec --truck komatsu_hd465                   # print the spec and its standards check
$PY -m envgen.measure_geometry --id default                     # measure the as-built geometry from heightmap.raw
```
The geometry follows an explicit design spec tied to a real design truck. See `DESIGN_SPEC.md`; the same numbers are in `world.json["design"]`.
- The default design truck is the Komatsu HD465-7 (55 t). Roads are 15 m (3.5 × truck width), the ramp is 16.5 m, and the ramp grade is 9 % with 2 % cross-fall.
- A 1.1 m safety windrow (≥ MSHA mid-axle height) runs along the ramp crest edge.
- Benches are 10 m high with 70° faces and 6.5 m catch berms (modified Ritchie).
- The pit is a 3-bench, 30 m deep spiral pit in a 260 m lease, with a level 40 m run-out at the ramp foot.
- Three ore sites: the pit floor plus two bench loading bays cut into the highwall beside the ramp.
- Surface haul roads join the crusher, fuel bay, ready line and dump pads in the lease corners.
- Three trucks (`truck-0..2`) start on the ready line.

The script prints navgrid reachability, any spec violations, and the measured-vs-spec table.

## World Labs Marble
```bash
export WLT_API_KEY=...            # OPENAI_API_KEY (+ OPENAI_BASE_URL, OPENAI_IMAGE_MODEL) only for --concept
$PY -m envgen.worldlabs --id marble_pit --concept --draft        # cheap iteration (marble-1.0-draft)
$PY -m envgen.worldlabs --id marble_pit --concept                # marble-1.1-plus (default)
$PY -m envgen.worldlabs --id marble_pit --image my.png --prompt "open-pit copper mine ..."
$PY -m envgen.worldlabs --id marble_pit --world-id <marble world id>   # fetch an existing world
$PY -m envgen.worldlabs --id marble_pit --from-dir data/worlds/marble_pit   # rebuild only, no credits
```
How a run works:
1. `--concept` asks gpt-image-1 for an oblique, eye-level-to-15 m view across a pit and saves it as `concept.png`.
2. That image goes to `POST /marble/v1/worlds:generate` as `data_base64`.
3. The script polls `/operations/{id}` every 10 s. A world takes about 5 minutes; a full 1.1-plus world costs about **1,580 credits (≈ $1.26)**, and a draft about 150 credits.
4. It downloads `splat_500k.spz`, `splat_100k.spz`, `collider.glb`, `pano.jpg` and `thumbnail.jpg`, and saves the raw API response as `marble_world.json`.
5. It builds the navgrid from the collider (details below) and auto-places the sites and trucks.

Building the navgrid from the collider:
- Scale the collider by `metric_scale_factor`.
- Subtract `ground_plane_offset` from y.
- Convert the OpenCV frame to Y-up (negate y and z), checked against the mesh normals; `--up` overrides this.
- Cast rays straight down on a grid of about extent/220 m (at least 0.5 m) to get the heightmap.
- Turn slope into cost.

Site placement uses the largest connected passable region: ore sites go low in the terrain, and the crusher, dump, parking and fuel pads go on the flattest, most open areas.

## Outputs (per world)
| file | notes |
|---|---|
| `world.json`, `navgrid.json` | per CONTRACT.md |
| `terrain.glb` | heightmap mesh, Y up, RGBA vertex colors (dirt / rock face / gravel road / pad / ore tint) |
| `heightmap.raw` + `heightmap_meta.json` | Unity RAW, 513x513 (1025x1025 when size_m > 500) uint16 LE, row 0 = south; meta has min/max height for scaling |
| `preview.png` | top-down hillshade with sites and trucks, north up |
| Marble only | `splat_500k.spz`, `splat_100k.spz`, `collider.glb`, `pano.jpg`, `concept.png`, `marble_world.json` |

Navgrid cost: road/pad 1.0; off-road 1.3 + 0.15 per degree of slope; mean slope > 20° or any cliff > 35° gives -1.
