# isaac/: mine simulator on NVIDIA Isaac Sim

This folder moves the Unity scene to Isaac Sim 5.x. It targets **watson**, an NVIDIA GB10 / DGX Spark with aarch64.
The code is written against **Isaac Sim 5.0/5.1** (module names `isaacsim.*`) and **OpenUSD 24–26**. It was tested
locally with `usd-core 26.8`, `trimesh 5.1.1` and `numpy 2.5`. Dust and dirt are not built here (a teammate owns them).

| File | Needs Isaac? | What it does |
|---|---|---|
| `build_stage.py` | no (pure `pxr` + trimesh) | converts `data/worlds/<id>/` into `data/worlds/<id>/isaac/world.usda`, plus meshes, models and textures |
| `glb2usd.py` | no | GLB → `UsdGeom.Mesh` / `UsdPreviewSurface` converter used by build_stage |
| `mine_frames.py` | no | coordinate conventions (contract ↔ Isaac, heading ↔ yaw, sensor axes) |
| `animate.py` | no (pxr only) | excavator dig cycle, truck bed tipping, optional read-only `RadioLink` |
| `load_world.py` | yes | opens the stage, spawns 3 kinematic Komatsu trucks, adds RTX camera + RTX lidar per truck, physics loop |

## Quick start

Local (Mac or any machine, no Isaac):
```bash
python3.12 -m venv isaac/.venv.nosync && isaac/.venv.nosync/bin/pip install usd-core trimesh numpy pillow scipy
isaac/.venv.nosync/bin/python isaac/build_stage.py quarry default      # ~12 s for quarry incl. the 1.5 M-tri scan
isaac/.venv.nosync/bin/python isaac/animate.py data/worlds/quarry/isaac/world.usda   # rig + radio self-test
```
The output folder `data/worlds/<id>/isaac/` is self-contained (relative paths; the dome light points at
`../sky_pano.jpg`). Copy it to watson with rsync, or run build_stage there. The Isaac python already ships `pxr`;
install `trimesh` with `./python.sh -m pip install trimesh`. The quarry output is about 70 MB, so do not commit it.

On watson (Isaac Sim 5.1, DGX Spark build):
```bash
cd ~/isaacsim            # or wherever Isaac Sim is installed / the container's /isaac-sim
./python.sh ~/Physical_mining/isaac/load_world.py --world quarry --headless --seconds 60 \
    --demo-drive --dig --save-frames /tmp/isaac_frames          # PNG + lidar .npy per truck, once a second
./python.sh ~/Physical_mining/isaac/load_world.py --world quarry --headless --radio http://127.0.0.1:8090
```
Script Editor (GUI on a machine with a display): `exec(open('<repo>/isaac/load_world.py').read());
main_in_editor('quarry')`, then press Play.

**Viewing on watson:** livestreaming (WebRTC) is **not supported on aarch64 / DGX Spark** in Isaac Sim 5.1, according
to the 5.1 requirements page. Use one of these instead:
1. Run headless and send the truck cameras through the brain bridge (TODO below). The existing brain → H.264 →
   web viewer path then shows them, which is the intended route anyway.
2. Use `--save-frames DIR` and look at the PNGs.
3. Open `world.usda` in Isaac Sim (or usdview) on an x86 workstation with a GPU.
On x86_64 hosts, livestream works with `./isaac-sim.streaming.sh` or
`standalone_examples/api/isaacsim.simulation_app/livestream.py` and the "Isaac Sim WebRTC Streaming Client"
(TCP 49100, UDP 47998).

## Stage layout (`world.usda`)

```
/physicsScene                      UsdPhysics.Scene, gravity -Z 9.81
/World                             defaultPrim; customData mine:worldId, mine:frame
  Terrain/Collider                 collider.glb as a static triangle mesh (MeshCollisionAPI approximation "none");
                                   purpose=guide when a visual exists (PhysX collides, RTX camera/lidar ignore it)
  Terrain/Visual                   visual.glb, scan_transform baked in, UsdPreviewSurface + JPEG texture, no collider
  Terrain/Terrain                  procedural worlds (no collider.glb): terrain.glb, rendered + collider
  Terrain/Surround                 surround.glb apron, vertex colours (displayColor), rendered + collider
                                   (fallback ground for ray probes, like MineGroundFollow.surroundFallback)
  Lights/Sky                       UsdLux.DomeLight, texture:file = ../sky_pano.jpg (latlong)
  Lights/Sun                       UsdLux.DistantLight, 50 deg elevation
  Sites/<id>                       Xform at pos3; mine:id/type/name/tonnes/contractPos3
    load_point | dump_point | slot_N   child Xforms, local +X = arrival heading
  Junctions/<id>                   Xform
  Roads/<id>                       UsdGeom.BasisCurves (linear, constant width = width_m), purpose=guide;
                                   mine:from/to/type/width_m/grade_pct/one_way
  Equipment/exc_N                  reference models/excavator.usdc; translate + rotateZ (heading); mine:id = "exc-N"
  Spawns/truck_N                   Xform (translate + orient), mine:id = "truck-N", mine:heading_deg
  Trucks/                          empty; load_world.spawn_trucks fills it
```
USD prim names cannot contain `-`, so `truck-0` becomes `truck_0` and `exc-0` becomes `exc_0`. The original id is
kept in `mine:id`, and `animate.py` accepts either form.

Models (`models/*.usdc`, written by build_stage with pxr, so they don't need the asset converter):
* `komatsu_hd465.usdc` is scaled to 9.35 m long, with its origin at the bottom centre, +X forward, +Y left and +Z up.
  The `Chassis/*` meshes are static. `BedHinge` sits at the hinge pins from `assets/models/komatsu_hd465/rig.json`
  `hinge_m` = [0.0838, 1.8742, -2.82] (glTF), which maps to (-2.28, -0.08, 1.88) m in the model. The tip is driven by
  `xformOp:rotateY:tip = -deg` (0..48 deg per rig.json `tip_deg`) and the front of the bed rises. Checked locally:
  at 48 deg the bed front reaches 8.3 m and the tail stays 0.85 m above the ground.
* `excavator.usdc` is the rigged GLB scaled to 10 m (same as the preview). Its nested joint Xforms are
  `house` (rotateZ:joint) / `boom` / `stick` / `bucket` (rotateY:joint). The pins come from
  `assets/models/excavator/rig.json` `joints.*.pivot_m`. The signs match the browser: + house = swing to the cab/truck
  side, + boom = down, + stick = in, + bucket = curl.
* If the pxr models are missing, `load_world` falls back to `omni.kit.asset_converter`. That fallback has no bed hinge
  and no joints.

## Coordinate conventions

| | Contract / Unity / brain | Isaac stage |
|---|---|---|
| up | +Y | +Z (`upAxis = Z`, `metersPerUnit = 1`, `kilogramsPerUnit = 1`) |
| position | (x, y, z) | (X, Y, Z) = (x, **-z**, y) |
| heading | `heading_deg`: 0 = +z, clockwise from above | yaw about +Z = `heading_deg - 90` (body +X forward) |
| truck / sensor local | x right, y up, z forward | +X forward, +Y left, +Z up |

The mapping is a proper rotation (+90 deg about X). Nothing is mirrored: winding, the cab side and every relative pose
are kept. Contract "north" (+z) points along Isaac -Y. All helpers live in `mine_frames.py`:
`isaac_pose_to_brain()` gives brain `pose{x,z,yaw}`, and `isaac_sensor_points_to_unity()` puts lidar points into
the brain's sensor axes. The build checks this: the spawn forward vectors match the road directions, and the
load/dump-point headings have a dot product of 0.96–1.0 with their road's end segment.

Why the conversion is baked into the points, and not done by a Y-up stage or a rotated `/World`: Isaac's physics
defaults, `World`/`SimulationContext`, Camera "world" axes, RTX lidar frames and core pose APIs all assume Z-up. A
rotated parent puts every rigid body and sensor pose in a rotated frame, which breeds bugs in the vehicle loop and the
bridge.

## Unity → Isaac mapping

| Unity | Isaac (this folder) |
|---|---|
| `MineWorldImporter` / `QuarrySceneBuilder` | `build_stage.py` → `world.usda` |
| `TruckAgent` (kinematic Rigidbody, bicycle model, maxSpeed 8, steer 30°, wheelbase 5, accel 1.5, 0.5 s timeout) | `load_world.TruckController.step()`, same equations. Root prim = kinematic `RigidBodyAPI` + guide box `CollisionAPI`. `set_action(throttle, steer)` takes the brain `action` |
| `MineGroundFollow` (4 wheel rays, surround fallback, smoothing, visual tilt) | `TruckController.ground()`: PhysX scene query `raycast_all`, filtered to `/World/Terrain/*` so the truck's own box is skipped. Height is smoothed (60 ms, rises never lag, snaps above 4 m). Tilt goes on `Model` only; `Sensors` stay level (`tilt_sensors=False`, same as the Unity default) |
| `SensorCamera` 640×360, 90° hFOV, 15 fps, depth 64×36 | `isaacsim.sensors.camera.Camera` at `Sensors/camera`; aperture 20.955 and focal 10.4775 give 90° hFOV. Depth comes from `distance_to_image_plane`, sampled down to 64×36 (miss = 100 m) |
| `LidarSensor` 120°×30° (-20..+10), 240×30, 10 Hz, 100 m | RTX `OmniLidar` via `IsaacSensorCreateRtxLidar` (config=None) at `Sensors/lidar`. It has 30 emitters at the spec cell-centre elevations and fires at 7200 Hz (0.5° steps at 10 Hz), farRange 100. The 360° scan is cropped to ±60° in `read_lidar()` and converted to brain axes, ≤ 7200 points |
| Sensor mount (0.2 m ahead of collider, camera 0.8 H, lidar 0.6 H) | `(L/2 + 0.2, 0, 0.8 H)` and `(L/2 + 0.2, 0, 0.6 H)` under `/World/Trucks/<id>/Sensors` |
| `MineExcavatorRig` + `MineRadioLink` | `animate.py` (`Animator`, `RadioLink`, same keyframes, period, rates) |
| bed tip (preview `[bed-tip]`) | `animate.tip_bed(truck_id, deg)` / `dump_cycle(truck_id, 6 s)`, raise 3 s / lower 2.5 s from rig.json |
| `BrainClient` TCP :9000 | **TODO** (see below) |

`animate.py` API: `init(stage)`, `set_excavator_state('exc-0', 'digging'|'holding')`, `tip_bed('truck-0', deg)`,
`update(dt)` (call on the main thread; `load_world` calls it from the physics callback). `RadioLink(anim, RADIO_URL)`
polls `GET {RADIO_URL}/api/radio/state` every 0.5 s. It is read-only and never sends goals or actions.
`excavators[].state` drives the dig cycle. A truck `phase` containing `dump`/`tip` (but not the travel leg `to_dump`)
runs one ~6 s bed cycle on the rising edge. In `load_world.py` it is turned on with `--radio URL`.

## TODO for the team

1. **Brain TCP bridge in Isaac** (`isaac/brain_bridge.py`). Connect to brain `127.0.0.1:9000` with the spec.md framing
   (`u32 header_len | u32 payload_len | JSON | payload`; see `brain/brain/protocol.py`).
   * `hello`: trucks, cameras, bounds, truck_model, roads/sites/junctions from `world.json` (`/World` customData
     `mine:worldJson`).
   * `frame` per truck camera at 15 fps from `read_sensors()`: JPEG-encode the rgb, add depth float32 64×36, and
     `pose = TruckController.brain_pose()`, `speed`. Also send the `overview` camera (add one Camera prim).
   * `lidar` at 10 Hz: `format: int16cm`, points already in brain sensor axes.
   * `action` from the brain → `TruckController.set_action(throttle, steer, sim_time)`.
   * Run the socket on an asyncio task or thread. Only touch USD and sensors from the main/physics thread, using
     queues (the same pattern as `RadioLink`).
2. **Policy loop timing.** Step physics at 60 Hz, camera at 15 Hz and lidar at 10 Hz. Check that RTX rendering of
   3 cameras + 3 lidars holds real time on GB10. If not, cut the camera rate or use `world.step(render=False)` between
   sensor frames.
3. **Verify on watson on the first run** (APIs this code could not exercise locally):
   * Check the lidar point frame: `MINE_LIDAR_FRAME=auto` guesses world or sensor; set `sensor` or `world` once known.
     Also check that `IsaacCreateRTXLidarScanBuffer` returns full scans, and the emitter-state attribute names
     (`omni:sensor:Core:emitterState:s001:*`).
   * Check the dome-light orientation in a Z-up stage. If the sky looks sideways, add `rotateX 90` on
     `/World/Lights/Sky` (Kit versions differ; newer UsdLux has `poleAxis`).
   * Check the 22 MB scan texture (`textures/Material_0.jpg`) for RTX texture memory. Downscale it if needed.
4. Pedestrians (`PedestrianWalker`, `actors` message) and the overview camera have not been ported yet.
5. Optional: a PhysX articulation for the excavator (revolute joints house/boom/stick/bucket), if contact with the
   bucket is ever needed. The Xform rig is visual only.
6. Optional: crop the lidar in hardware (`validStart/EndAzimuthDeg`) once the azimuth convention is confirmed. That
   saves 2/3 of the rays.

## Risks

* **aarch64 / GB10:** Isaac Sim 5.1's aarch64 builds are supported only on DGX Spark (DGX OS 7.2.3, driver
  580.95.05). Livestream, Hub cache, OBJ import, cuRobo/cuMotion and the app selector are not supported there.
  watson is GB10, which is the DGX Spark chip, so it should work, but make sure the OS and driver match. Pip wheels
  and containers may differ on aarch64; use the official DGX Spark build or container.
* The RTX lidar config moved from JSON (`Camera` prims, deprecated in 5.0) to `OmniLidar` USD attributes, and the
  Python class moved again in 6.x (`isaacsim.sensors.experimental.rtx.Lidar`). This code uses the command plus raw
  attributes to limit API churn.
* Scan textures are large (8k-class JPEG) and the scan has holes (`hole_fraction` 0.48). The apron sits 4 m below them,
  and RTX lidar sees the visual mesh, not the collider, so holes in the scan are lidar misses.

<!-- ===================================================================================== brain bridge (begin) -->
## Brain bridge

Files (all pure Python + numpy/Pillow at import time; Isaac modules are imported lazily):

| File | Needs Isaac? | What it does |
|---|---|---|
| `brain_bridge.py` | no | spec framing, `SPEC` compliance table + `validate()`, encoders, `BrainLink` (BrainClient twin), `BridgeRuntime` (Unity rates), `ExternalTrucksBridge` (adapter for `load_world.py`), CLI |
| `truck.py` | no (pose writer / PhysX probe: yes) | `TruckController` = TruckAgent + optional `KeepInBounds`, `NavGrid`, axis helpers, `PrimPoseWriter`, `physx_probe` |
| `sensors_bridge.py` | no (`make_core(backend="isaac")`: yes) | frame / lidar message assembly, `NumpyCaster`, `FakeCore` (no Isaac/Warp/GLBs), excavator swing envelope, `make_core` |
| `rl_env.py` | no (`backend="isaac"`: yes) | `IsaacQuarryEnv` / `MultiTruckQuarryEnv` (Gymnasium API) |
| `test_bridge.py` | no | 25 unit tests + 1 integration test against the real brain |

**Important: `isaac/sim/` (desmondzee, merged on origin/main as "Isaac Sim simulator") already implements a full
Isaac replacement for the Unity player** (`isaac/sim/main.py`, container `isaac/docker/`, spec.md section "Isaac
sim"). The bridge files above reuse its physics and sensor code instead of duplicating it:
`isaac/sim/kinematics.py` (bicycle + MineGroundFollow), `sensors.py` (ray patterns, mounts), `raycast.py` (Warp),
`world.py` (hello, spawns, crossings, excavators), `pedestrians.py`, `scene.py` (RTX renderer). Note the two stage
conventions in this folder: `isaac/sim` uses Isaac (X, Y, Z) = (x, z, y) (improper swap: picture identical to
Unity); `build_stage.py`/`mine_frames.py` use (x, -z, y) (proper rotation: picture is Unity's mirror image, see
"Handedness" below).

### Run order on watson

```bash
# 1. brain (compose project "mining"; sim TCP 127.0.0.1:9000, web 127.0.0.1:8080)
docker compose up -d brain                       # do NOT start the "fake" profile: the brain keeps one sim connection
# 2a. Isaac via isaac/sim scene + this bridge (Unity rates 15/10/10 Hz, KeepInBounds on):
cd /isaac-sim && ./python.sh ~/Documents/mining/isaac/brain_bridge.py --backend isaac --world ~/Documents/mining/data/worlds/quarry \
    -brain-host 127.0.0.1 -brain-port 9000       # or BRAIN_HOST / BRAIN_PORT; --no-keep-in-bounds = exact Unity dynamics
# 2b. or isaac/sim's own loop (30 fps, no KeepInBounds):  docker compose -f isaac/docker/compose.yml --profile isaac up -d --build
# 2c. or load_world.py (build_stage stage) once it calls ExternalTrucksBridge (snippet below); `--bridge` is not wired yet
# 3. view: ssh -N -L 8080:127.0.0.1:8080 watson  ->  http://localhost:8080  (cameras come back as H.264 via the brain;
#    Isaac WebRTC livestream is not supported on aarch64/DGX Spark, so the brain web viewer is the livestream)
```
No Isaac at all (Mac, CI): `python3 isaac/brain_bridge.py --world data/worlds/quarry -brain-port 9000` runs the
`FakeCore` (flat ground, depth-shaded RGB, real roads/spawns/pedestrians/excavators) against a brain.

Wiring into `load_world.py` (its own `TruckController.set_action` and `read_sensors()`):
```python
import brain_bridge as BB                                        # isaac/ is on sys.path there
link = BB.BrainLink(*BB.brain_endpoint(), lambda: BB.hello_from_world(world_dir, [t.id for t in trucks])).start()
bridge = BB.ExternalTrucksBridge(link, trucks)                   # flip_image / negate_lidar_x: see Handedness
# physics callback, before t.step():  bridge.apply_actions(clock["t"])
# after world.step(render=True):      bridge.publish(clock["t"], lambda: read_sensors(sensors), overview=None)
```

### Mapping from Unity

| Unity | Bridge |
|---|---|
| `BrainClient.Awake` `-brain-host`/`-brain-port`, `BRAIN_HOST`/`BRAIN_PORT`, 127.0.0.1:9000 | `brain_endpoint()` (same precedence; also `--brain host:port`) |
| `BrainClient.Run`: thread, `NoDelay`, hello first on each connect, outbox 64 (TryAdd drops), 2 s retry | `BrainLink` |
| `BrainClient.LatestAction(truck, commandTimeout)` | `BrainLink.latest_action()`, `apply_link_actions()` (receive time mapped onto sim time, so the 0.5 s timeout holds) |
| `BrainClient.Update` actors at `actorsRate` 10 Hz (`Pedestrian_NN`) | `BridgeRuntime` `actors` gate, `isaac/sim/pedestrians.py` walkers |
| `SensorCamera` 15 fps gate on `Time.time`, JPEG q80, `flip` false after JPEG, `channels` 4 | `RateGate(15)` on sim time, `frame_message()`; `encoding="raw"` = Unity raw path (RGBA, bottom-up, `flip: true`) |
| `SensorCamera.CaptureDepth` raycast grid, `distance * dot(dir, forward)`, miss = 100 | `isaac/sim` Warp grid (`NumpyCaster` in the fake core), same cell-centre rays |
| `LidarSensor` 240×30 cell centres, `InverseTransformPoint`, misses dropped, int16 cm | `isaac/sim/sensors.lidar_directions()` + `lidar_message()` |
| `TruckAgent.FixedUpdate` | `isaac/sim/kinematics.Truck.step` (identical, pinned by `bicycle_step` test); `TruckController` adds the guard |
| `MineGroundFollow` (4 wheels + centre, 8 m up / 150 m down / 1000 m retry, 0.06 s smoothing, snap 4 m, tilt) | `isaac/sim/kinematics.GroundFollow` (Warp `probe`); `truck.physx_probe()` = PhysX scene-query version |
| hello obstacles = excavator collider bounds | excavator circles widened to the swing envelope (half box diagonal) |

### Spec compliance (spec.md "Sim protocol", origin/main 00da484; asserted by `TestSpecCompliance`)

| Message | Spec field | Implementation |
|---|---|---|
| framing | `u32 header_len \| u32 payload_len \| JSON \| payload`, LE, header ≤ 64 KiB, payload ≤ 64 MiB | `encode`, `MessageReader`, `recv_message`; byte-identical to `brain.protocol.encode` (test) |
| `hello` | `trucks` | core truck ids (`truck-0..2`) |
| | `cameras` | `["overview", truck ids…]` (+ isaac/sim views if configured) |
| | `bounds` | `world.json` `bounds` (`[-100,-100,100,100]` flat) |
| | `truck_model` | `komatsu_hd465` |
| | `obstacles[{x,z,r}]` | one circle per excavator (`world.equipment`), r = swing envelope; + `props.json` obstacles |
| | `paths[{points[{x,z}]}]` | 4 road crossings (`isaac/sim/world.crossing_paths`, same rule as Unity/fakesim) |
| | `roads[{id,from,to,width_m,one_way,points}]` | `world.json` roads, points from → to |
| | `sites[{id,type,name,x,z}]`, `junctions[{id,x,z}]` | `world.json` |
| `frame` | `camera`, `truck` (`""` overview) | camera name, truck id |
| | `rgb{w,h,flip,channels,format,bytes}` | 640×360, `jpeg` q80 (`flip` false) or `raw` RGBA (`flip` true), `channels` 4 |
| | `depth{w,h}` (0×0 if none) | 64×36 float32 perpendicular m, row 0 top, miss 100; overview 0×0 |
| | `pose{x,z,yaw}`, `speed` | truck pose (yaw 0 = +z, clockwise), m/s; overview zeros like Unity |
| | (extension) `cam` | camera world pose `{x,y,z,yaw,pitch,roll,hfov}` (spec "Isaac sim"); ignored by the brain |
| `lidar` | `truck`, `points`, `format`, `pose{x,z,yaw}` | `int16cm`, ≤ 7200 points, 6 bytes each, sensor-local x right / y up / z forward |
| `actors` | `pedestrians[{id,x,z,yaw}]` | 12 walkers `Pedestrian_00..11`, 10 Hz |
| `action` (brain → sim) | `truck`, `throttle`, `steer` | `BrainLink._receive` → `TruckController.command` (throttle clamped 0..1, steer -1..1, 0.5 s timeout → coast) |
| rates | frames 15 fps per camera, lidar 10 Hz, actors 10 Hz | `RateGate` on sim time (isaac/sim/main.py itself defaults to 30 fps) |

The policy controls only throttle and steer; the radio link (`animate.RadioLink`) stays read-only.

### Deliberate differences from Unity

* **KeepInBounds** (`truck.KeepInBounds`, on by default in the bridge, `--no-keep-in-bounds` to turn off; off by
  default in `rl_env`, `keep_in_bounds=True` to enable, wall contact is a reward penalty either way):
  (a) the 4-corner footprint (±2.12 m lateral, ±4.7 m longitudinal) must stay 5 m inside `world.json` bounds;
  (b) it must stay on `navgrid.json` cells with cost ≥ 0 (off-grid = wall). A step that would touch a wall is not
  taken: the guard tries the move with the old heading, then x-only, then z-only (slide, speed × 0.5), else stops
  (speed 0). A truck already touching a wall may make moves that don't add blocked corners, so it can get out.
  (c) curvature limit: speed ≤ √(1.5 m/s² · R), R = 5 / tan(|steer|·30°) (3.6 m/s at full steer). Above the cap
  the truck brakes at 3 m/s² and opens the turn meanwhile, so v²/R never exceeds 1.5 m/s².
* Depth origin is the camera position, not Unity's near plane (Unity depth = true depth − 0.3 m near clip).
* Lidar/depth hit trucks/excavators as oriented boxes and pedestrians as cylinders (Unity: box/capsule colliders,
  excavator mesh colliders).

### Handedness (read this before trusting the cameras)

Contract numbers are glTF numbers used verbatim by Unity (left-handed), so Unity shows the **mirror image** of the
true glTF geometry. `isaac/sim` uses an improper axis swap and reproduces Unity's picture exactly.
`build_stage.py` uses a proper rotation, so its scene is the true geometry: an object the brain computes at
sensor-local x > 0 (right, from the pose numbers) appears on the truck's **left** in that stage. Consequences for
`load_world.py`: the camera image and depth columns must be flipped left-right to match Unity, and lidar x must be
`+Y_vehicle`, not `-Y` as in `mine_frames.isaac_sensor_points_to_unity` (that sign mirrors obstacles across the
truck's heading for the planner). `ExternalTrucksBridge(flip_image=True, negate_lidar_x=True)` corrects both;
set `negate_lidar_x=False` once `load_world.py` is fixed. Positive steer turns the truck clockwise in contract
numbers, which looks like a left turn in the build_stage scene.

### RL environment

```python
from isaac.rl_env import IsaacQuarryEnv, MultiTruckQuarryEnv
env = IsaacQuarryEnv(backend="fake")            # "isaac" under ./python.sh: RTX RGB + Warp depth/lidar
obs, info = env.reset(seed=0, truck_id="truck-0", goal="crusher")   # goal: node id, (x, z) or None (random site)
obs, reward, terminated, truncated, info = env.step({"throttle": 0.6, "steer": 0.1})   # or np.array([0.6, 0.1])
```
* obs: `rgb` (360, 640, 3) uint8, `depth` (36, 64) float32, `lidar` (7200, 3) float32 zero-padded + `lidar_count`,
  `pose` [x, z, yaw], `speed` [m/s], `goal` [x, z]. Gymnasium spaces are defined when `gymnasium` is installed.
* 15 Hz control, 1/60 s physics substeps, same `TruckController`/`GroundFollow`/sensor code as the bridge.
* Default reward (`RewardConfig`): +1 per metre of progress along the road-graph route (Dijkstra over `world.json`
  roads, one-way honoured), −0.01 per step, −10 + terminate on collision (truck footprint vs other trucks,
  excavator boxes, pedestrians), −0.1 per step off-road (> 1 m beyond the road half width), −0.5 per step of wall
  contact (guard slide/stop), +100 + terminate within 4 m of the goal. Pass `reward_fn(env, truck, prev, now)`.
* `MultiTruckQuarryEnv`: all trucks of one world as agents (dict actions/observations), one physics + sensor pass.
  `rgb=False` skips images. Fake core step ≈ 0.1 s on an M-series Mac (3 trucks, depth + lidar + JPEG-free).

### Tests

`python3 isaac/test_bridge.py` (or `pytest isaac/test_bridge.py`); `SKIP_INTEGRATION=1` skips the brain run.
The integration test makes a `git worktree` of origin/main in a temp dir, starts `uv run brain` on spare ports with
`WORLD_DIR` = the quarry, connects `BrainLink` + `FakeCore`, posts `{"site"}` for truck-0, waits for actions and
motion, then removes the worktree.

### Known gaps

* Not run inside Isaac here (no GPU locally): `make_core(backend="isaac")` relies on `isaac/sim/scene.py`
  (Isaac 6.2 container); `PrimPoseWriter` and `physx_probe` are untested in Isaac.
* `load_world.py --bridge` is not wired (adapter + snippet above); its RTX depth (`distance_to_image_plane`) is
  Isaac's perpendicular depth, sampled not averaged.
* Excavator swing is not in the raycast boxes unless `animate.py` calls `sensors_bridge.set_excavator_yaw()`;
  the hello circle covers the full swing.
* The fake core is flat (y = 0) with the fakesim terrain offset on lidar y; it does not test ground follow on slopes.
* Brain `LIDAR_HEIGHT` defaults to 3.0 m; the Komatsu lidar mount is 0.6 × 4.4 ≈ 2.6 m (same mismatch as Unity).
<!-- ======================================================================================= brain bridge (end) -->
