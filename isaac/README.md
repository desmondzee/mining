# isaac/: our add-ons on top of isaac/sim

`isaac/sim/` (Des) is the Isaac Sim 6.2 port: the only stage builder and the only brain bridge (`isaac/sim/main.py`,
TCP `127.0.0.1:9000`, see [sim/README.md](sim/README.md)). This folder adds things on top of it without
duplicating its physics, sensors, protocol or scene code. We do not edit `isaac/sim/` or `isaac/docker/`; changes we
want there are patch files in `patches/` for Des to review. Dust, dirt, gravel and bed load are Des's.

| File | Needs Isaac? | What it does |
|---|---|---|
| `excavators.py` | no (pxr only for the USD rig) | rigged, radio-driven excavator dig cycle for isaac/sim's scene + moving arm boxes for its Warp raycasts |
| `rl_env.py` | no (`render="isaac"` for RTX RGB) | Gymnasium-style env on `isaac.sim.sim.Sim`: reward, routes, KeepInBounds wrapper, multi-truck |
| `tests/test_addons.py` | no | tests for both (Warp / usd-core optional) |
| `patches/main_excavators.patch` | | `isaac/sim/main.py`: `--excavators`, `--radio-url` (not applied) |
| `patches/keep_in_bounds.patch` | | `isaac/sim/kinematics.py` KeepInBounds + `Sim(keep_in_bounds=)` + `--keep-in-bounds` (not applied) |
| `smoke_test.py`, `run_watson.sh`, `CHECKLIST.md` | | first run / demo day on watson (below) |

## Excavators (`excavators.py`)

`isaac/sim/scene.py` places every excavator as `/World/Equipment/<id>/model`, a reference to the non-rigged
`excavator.glb` flattened into meshes, and `Sim.sync_obstacles` gives the caster one static box per machine.
`install(sim, scene, radio_url)` adds at runtime (no change to scene.py):

* **Rig**: one cached USD per node (`tracks`, `house`, `boom`, `stick`, `bucket`) of
  `assets/models/excavator/excavator_rigged.glb` under `/World/Equipment/<id>/rig/<part>`, converted with
  isaac/sim's `usdbuild.write_mesh_usd` and the same `model_box` matrix (the rigged GLB has the same bbox). The static
  `model` prim is deactivated. One transform op per part, written after `scene.update`.
* **Dig cycle**: dig at the face, swing, dump over the truck bed at the preprogrammed spot, swing back. Keys per
  machine from `world.json` `equipment[]` (written by `envgen/exc_aim.py`): `exc_keys_deg` + `exc_period_s` as is;
  else IK to `dig_target` / `dump_target`; else `dig_yaw_deg` / `dump_yaw_deg` with the rig.json arm keys; else
  `rig.json` `dig_cycle`. House angles are relative to `heading_deg` in world-yaw convention (as the browser
  preview); the tests check that the played keys put the bucket pin on `dig_target` / `dump_target` (< 1 cm).
* **Radio** (read-only): `GET {RADIO_URL}/api/radio/excavators` every 0.5 s, `state` `digging` / `holding` per `id`
  (or `face`). Holding eases to rest. No radio or radio down: every machine keeps `default_state` (`digging`).
* **Lidar / depth see the arm**: `sim.caster.set_boxes` is wrapped so each machine's static box becomes five
  oriented boxes (PCA boxes of the part meshes) that follow the rig. The hello obstacle circles stay static.

Integration (`patches/main_excavators.patch`, after the renderer is built in `isaac/sim/main.py`):
```python
    if args.excavators:
        from isaac.excavators import install

        install(sim, None if args.dry_run else renderer, args.radio_url, cache=args.cache)
```
`git apply isaac/patches/main_excavators.patch`, then add `--excavators` to the compose command.
Dry run (Mac): `uv run --with numpy --with pillow --with warp-lang python isaac/sim/main.py --dry-run --excavators ...`
once the patch is in. No truck bed tipping (isaac/sim's truck is one flattened mesh).

## RL environment (`rl_env.py`)

```python
from isaac.rl_env import QuarryEnv, MultiTruckEnv, KeepInBounds
env = KeepInBounds(QuarryEnv("data/worlds/quarry", render="headless"))   # penalty mode; mode="clamp" = hard guard
obs, info = env.reset(seed=0, options={"goal": "crusher", "spawn": "road"})   # goal: node id, (x, z) or None
obs, reward, terminated, truncated, info = env.step({"throttle": 0.6, "steer": 0.1})   # or np.array([0.6, 0.1])
```
* Physics and sensors are isaac/sim's: `Sim` (spawn, `kinematics.Truck`, `GroundFollow`, walkers), `raycast.Caster`
  (Warp) and `sensors.Rig` (Unity ray patterns). 15 Hz control, 1/30 s substeps (main.py `SUBSTEP`).
* `render`: `"headless"` = Warp only, no RTX (fast; `rgb` zeros, `rgb_valid` 0), `"dry"` = main.py `DryRenderer`,
  `"isaac"` = SimulationApp + `IsaacRenderer` (run with Isaac's `python.sh`), or any object with
  `update(sim, dt)` / `render()`. `raycast=False` uses the heightmap and gives no depth/lidar (`depth_valid` 0).
* obs: `rgb` (360, 640, 3) uint8, `depth` (36, 64) float32 m, `lidar` (7200, 3) float32 sensor-local zero padded +
  `lidar_count`, `pose` [x, z, yaw], `speed`, `goal` [x, z], `rgb_valid`, `depth_valid`. Gymnasium spaces when
  `gymnasium` is installed.
* Default reward (`RewardConfig`): +1 per metre along the road-graph route (Dijkstra over `world.json` roads,
  one-way honoured), −0.01 per step, −10 and terminate on collision (footprint vs trucks, excavator boxes,
  pedestrians), −0.1 per step off-road (> 1 m beyond half width), +100 and terminate within 4 m. Or pass
  `reward_fn(env, truck, prev, now) -> (reward, terminated, info)`.
* `KeepInBounds` wrapper: footprint (±2.12 m × ±4.7 m) 5 m inside `world.json` bounds and on `navgrid.json` cells
  with cost ≥ 0. `mode="penalty"` (default): dynamics unchanged, −0.5 per step out. `mode="clamp"`: after every
  substep slide along the wall at half speed or stop, same penalty while it acts. `terminate_after=N` optional.
* `MultiTruckEnv`: every truck as an agent (`{id: action}` → `{id: obs}`), one physics step and one batched cast.
* `excavators=True` animates the excavators, so depth/lidar see the arms.
* Speed: `python isaac/rl_env.py --steps 150` (random policy). About 25 steps/s with Warp on a Mac CPU; the cast is
  most of it, so expect much more on watson's GPU.

## KeepInBounds for the demo (`patches/keep_in_bounds.patch`)

Our separate bridge is gone, so the demo guard is a patch to isaac/sim for Des to review: `KeepInBounds` and
`grid_passable` in `kinematics.py` (same rules as the RL clamp mode), `Truck(guard=)` applied after each step, with
identical numbers when no guard is set (isaac/sim tests still pass with it applied), `Sim(keep_in_bounds=False)`
(loads `navgrid.json`), and `main.py --keep-in-bounds`. The patches apply on their own or together.

## Tests

```bash
uv run --with numpy --with pillow --with pytest --with warp-lang --with usd-core pytest isaac/tests isaac/sim/tests -q
```
`isaac/tests/test_addons.py`: rig maths (rest pose, house yaw sign, boom/stick signs), world.json keys reach
`dig_target`/`dump_target`, dump IK from targets, key fallbacks, digging/holding, RadioLink against a fake HTTP
server (GET only, list / `{"excavators"}` / `{"items"}` bodies, radio down), caster box hook, Warp rays blocked by
the arm only where it is, USD rig on an in-memory stage; env reset/step shapes (heightmap and Warp, Warp obs =
`Sim.depths()`), mock renderer RGB, reward terms, success, road spawn, KeepInBounds penalty (dynamics unchanged)
and clamp (stops at the wall), multi-truck, both patches apply (`git apply --check`).

## Known gaps

* Not run inside Isaac here (no GPU locally): the USD rig is tested with usd-core only.
* Arm boxes are PCA boxes of each part, so the bent boom box is loose. The brain's hello circle is the static one,
  which does not cover the full arm reach.
* No bed tipping: the rigged truck (`assets/models/komatsu_hd465/rig.json`) is not used by isaac/sim's scene.
* RL `render="isaac"` renders every camera of the scene on each step (scene.py has no per-camera switch).

<!-- ================================================================================= first run on watson (begin) -->
## First run on watson

Target: **isaac/sim** (Isaac Sim **6.2** container `nvcr.io/nvidia/isaac-sim:6.2.0`, a multi-arch tag that also runs
on aarch64). Isaac 5.1 is not enough: `isaac/sim/main.py` needs Python 3.12 (6.x ships 3.12, 5.1 ships 3.11).
Livestream is not available on DGX Spark, so the brain web viewer (`ssh -N -L 8080:127.0.0.1:8080 watson`) shows
the cameras.

```bash
isaac/run_watson.sh check     # driver (DGX Spark: 580.x), docker + NVIDIA runtime/CDI, image arch, assets, brain/radio
isaac/run_watson.sh smoke     # isaac/smoke_test.py --excavators in the container -> isaac/logs/smoke/{report.json,*.png}
docker compose up -d brain    # brain first; keep the fakesim profile stopped
isaac/run_watson.sh up        # isaac/sim/main.py via isaac/docker/compose.yml, waits for sim_connected + trucks
isaac/run_watson.sh status    # /api/state summary + lidar point counts per truck
```
`bridge` is now an alias of `up` (isaac/sim/main.py is the only bridge). Logs go to `isaac/logs/` (gitignored).
`ISAAC_MODE=native ISAACSIM_PATH=~/isaacsim` uses a local 6.x install; in native mode `up` passes `--excavators` /
`--keep-in-bounds` when main.py has them (patches applied). The container command is Des's `compose.yml`.
`EXCAVATORS=0` turns the excavator checks off. Without Isaac (Mac):
`uv run --with numpy --with pillow --with warp-lang python isaac/smoke_test.py --dry-run --excavators` checks the
world, Warp depth/lidar, ground follow, the excavator cycle and arm boxes, and the drive test.

Smoke checks: python ≥ 3.12, SimulationApp headless, warp (otherwise no depth or lidar), world + 3 trucks, guard
(INFO: KeepInBounds only with the patch and `--keep-in-bounds`), stage prims, excavators installed (key source per
machine, radio), 300 frames settle, per excavator: cycle advanced, part boxes in the caster, rig prims present and
static model inactive, Warp ground ray under each spawn, USD prim pose = sim pose through `(x, z, y)`, RTX RGB per
truck (PNG, not black), depth stats, lidar count, and a 5 s drive (throttle 0.5, steer 0.2) that must stay on the
ground and inside bounds. RTX lidar, radio polls and the brain socket are INFO only.
For the demo-day go/no-go and the fallbacks, see [CHECKLIST.md](CHECKLIST.md).
<!-- =================================================================================== first run on watson (end) -->
