# Spec

## Platform

| Item | Value |
|---|---|
| Host | watson (Brev), NVIDIA GB10, aarch64 |
| NVIDIA driver | 580.178.04 |
| Repo on host | `~/Documents/mining` |
| Simulator | Unity 6.3 LTS (6000.3.26f1), URP |
| Unity player target | Linux ARM64, IL2CPP, `com.unity.sdk.linux-arm64` |
| Unity editor | macOS (no ARM64 Linux editor) |
| Brain | Python 3.12, FastAPI, uv |
| Containers | Docker Compose, project `mining` |
| Unity project | `unity/` |
| Packages | URP 17.3.0, glTFast 6.15.0 |
| Scene | `Assets/Scenes/Quarry.unity`, built by Tools > Mining > Build Quarry Scene |
| Environment | flat ground plane and box rocks with colliders |
| Ground colour | RGB (0.55, 0.47, 0.37), URP Lit |
| Rock colour | RGB (0.36, 0.34, 0.33), URP Lit |

## Bandwidth

| Link | Measured | Load |
|---|---|---|
| Unity → brain, same host | localhost | 4 × ~10 KB JPEG × 15 fps + 3 × ~27 KB lidar × 10 Hz ≈ 1.4 MB/s |
| Mac → watson (SSH via Brev) | 0.35–0.45 MB/s | too low for Unity on Mac with brain on watson; run brain locally |
| watson → viewer (SSH via Brev) | 1.4–1.9 MB/s | H.264: ≤0.3 MB/s video + ~0.04 MB/s lidar per viewer |

| Per viewer, 4 cameras 640×360 15 fps + lidar 10 Hz (5 viewers, local) | Before (JPEG, raw lidar) | After (H.264, zlib lidar) |
|---|---|---|
| Photographic frames (quarry pano crops, ~22 KB JPEG) | 1346 KB/s video | 294 KB/s video |
| Unity quarry trucks (35–41 KB JPEG, measured on watson) | ~2300 KB/s video | ≤275 KB/s video (bitrate cap) |
| Fakesim flat world | 459 KB/s video | 60 KB/s video |
| Lidar, selected truck (fakesim scan) | 34.5 KB/scan, 345 KB/s | 3.8 KB/scan, 38 KB/s |
| H.264 keyframe size | | 15–19 KB |
| Brain CPU (Mac M-series, 4 encodes + 4 JPEG decodes + policy) | 39% of a core (JPEG fallback, encoders still running) | 31% of a core |
| x264 encode time per 640×360 frame (M-series) | | 1.8 ms |

## Network

| Item | Value | Source |
|---|---|---|
| Website + API port | 8080 | |
| Sim TCP port | 9000 | `unity/Assets/Scripts/BrainClient.cs:port` |
| Host binding | 127.0.0.1 | |
| Remote access | `ssh -N -L 8080:127.0.0.1:8080 watson` | |
| Public access | Cloudflare Tunnel, compose profile `public`, `cloudflared` 2026.9.3, token `TUNNEL_TOKEN` in gitignored `.env` | |
| Tunnel origin | `http://localhost:8080` (public hostname set in Cloudflare dashboard) | |
| Access control | Cloudflare Access policy: allowed emails (one-time PIN and/or Google login) | |
| Editor → remote brain | `ssh -N -L 9000:127.0.0.1:9000 watson` | |

## World

| Item | Value | Source |
|---|---|---|
| Units | metres, seconds, degrees | |
| Ground plane | x / z, y up | |
| Yaw | `eulerAngles.y`, 0 = +z, clockwise positive | |
| Default bounds | [-100, -100, 100, 100] (xmin, zmin, xmax, zmax) | |
| Trucks | 3 | |
| Truck IDs | `truck-0` … `truck-N` | |
| Overview camera ID | `overview` | |

## Cameras

| Item | Value | Source |
|---|---|---|
| Truck camera width | 640 | `unity/Assets/Scripts/SensorCamera.cs:width` |
| Truck camera height | 360 | `unity/Assets/Scripts/SensorCamera.cs:height` |
| Frame rate | 15 | `unity/Assets/Scripts/SensorCamera.cs:fps` |
| Horizontal FOV | 90 | `unity/Assets/Scripts/SensorCamera.cs:horizontalFov` |
| Readback format | RGBA32, bottom-up (`flip: true`) | |
| Transport encoding | JPEG in Unity, passed through to viewers unchanged | |
| Unity JPEG quality | 80 | `unity/Assets/Scripts/SensorCamera.cs:jpegQuality` |
| Depth width | 64 | `unity/Assets/Scripts/SensorCamera.cs:depthWidth` |
| Depth height | 36 | `unity/Assets/Scripts/SensorCamera.cs:depthHeight` |
| Depth range (m) | 100 | `unity/Assets/Scripts/SensorCamera.cs:depthRange` |
| Depth format | float32 metres, perpendicular distance, row 0 = top, miss = range | |
| Depth method | `RaycastCommand` grid against colliders | |

## Lidar

| Item | Value | Source |
|---|---|---|
| Method | `RaycastCommand` batch against colliders | |
| Range (m) | 100 | `unity/Assets/Scripts/LidarSensor.cs:range` |
| Horizontal FOV (deg) | 120 | `unity/Assets/Scripts/LidarSensor.cs:horizontalFov` |
| Vertical min (deg) | -20 | `unity/Assets/Scripts/LidarSensor.cs:verticalMin` |
| Vertical max (deg) | 10 | `unity/Assets/Scripts/LidarSensor.cs:verticalMax` |
| Columns | 240 | `unity/Assets/Scripts/LidarSensor.cs:columns` |
| Channels | 30 | `unity/Assets/Scripts/LidarSensor.cs:channels` |
| Rate (Hz) | 10 | `unity/Assets/Scripts/LidarSensor.cs:rate` |
| Resolution | 0.5° H × 1° V | |
| Beam angles | cell centres: az = -h_fov/2 + (j + 0.5)·h_fov/columns, el = v_min + (i + 0.5)·(v_max - v_min)/channels | |
| Max points per scan | 7200 | |
| Point format (sim → brain, brain → viewer) | int16 x, y, z in centimetres, little-endian, 6 bytes per point, header `format: int16cm` | |
| Point format (HTTP API) | float32 x, y, z metres, 12 bytes per point | |
| Precision / limit | 1 cm, ±327.67 m | |
| Frame | sensor-local, Unity axes: x right, y up, z forward, metres | |
| Mount | `LidarSensor` transform, unit scale | |
| Misses | dropped | |
| Noise | none | |
| Fake sim mount height (m) | 3 | `brain/brain/fakesim.py:LIDAR_HEIGHT` |
| Fake sim range (m) | 100 | `brain/brain/fakesim.py:LIDAR_RANGE` |
| Fake sim horizontal FOV (deg) | 120 | `brain/brain/fakesim.py:LIDAR_HFOV` |
| Fake sim vertical min (deg) | -20 | `brain/brain/fakesim.py:LIDAR_VMIN` |
| Fake sim vertical max (deg) | 10 | `brain/brain/fakesim.py:LIDAR_VMAX` |
| Fake sim columns | 240 | `brain/brain/fakesim.py:LIDAR_COLS` |
| Fake sim channels | 30 | `brain/brain/fakesim.py:LIDAR_CHANNELS` |
| Fake sim rate (Hz) | 10 | `brain/brain/fakesim.py:LIDAR_HZ` |

## Truck models

| Item | Value | Source |
|---|---|---|
| Folder | `unity/Assets/Models/Trucks/` | |
| Komatsu HD465-7EO | `komatsu_hd465.glb`, 27k triangles, CC-BY-4.0 | |
| Komatsu length (m) | 9.35 | |
| Komatsu import rotation (deg) | (-90, 0, 0) | |
| CAT 785C | `cat_785c.glb`, 396k triangles, licence not documented | |
| CAT length (m) | 11.02 | |
| CAT import rotation (deg) | (-90, -90, 0) | |
| Scaling | uniform, renderer bounds length along z = real length | |
| Origin | bottom centre of renderer bounds | |
| Collider | `BoxCollider` = renderer bounds | |
| Body | kinematic `Rigidbody` | |
| Trucks in scene | 3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:TruckCount` |
| Start positions | (-15, 0, -70), (0, 0, -70), (15, 0, -70), yaw 0 | |
| Sensor offset ahead of collider (m) | 0.2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SensorOffset` |
| Camera height (fraction of truck height) | 0.8 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CameraHeightFraction` |
| Lidar height (fraction of truck height) | 0.6 | `unity/Assets/Editor/QuarrySceneBuilder.cs:LidarHeightFraction` |

## Pedestrians

| Item | Value | Source |
|---|---|---|
| Control | fixed waypoint paths, no policy | |
| Script | `PedestrianWalker` | |
| Count | 12 | |
| Pedestrians per path | 3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestriansPerPath` |
| Start positions | path fractions 0, 1/3, 2/3 | |
| Min walking speed (m/s) | 1.2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:MinWalkSpeed` |
| Max walking speed (m/s) | 1.6 | `unity/Assets/Editor/QuarrySceneBuilder.cs:MaxWalkSpeed` |
| Speed assignment | seeded uniform per pedestrian | |
| Walk clip natural speed (m/s) | 1.4 | `unity/Assets/Scripts/PedestrianWalker.cs:clipSpeed` |
| Animation speed | walking speed / clip speed | |
| Turn rate (deg/s) | 360 | `unity/Assets/Scripts/PedestrianWalker.cs:turnRate` |
| Path modes | loop (3+ waypoints), ping-pong (2 waypoints) | |
| Height (m) | 1.8 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestrianHeight` |
| Collider radius (m) | 0.3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestrianRadius` |
| Body | kinematic `Rigidbody`, `CapsuleCollider` | |
| Models | Microsoft Rocketbox, MIT, fetched by `tools/fetch_rocketbox.py` into `unity/Assets/Models/People/` (gitignored) | |
| Avatars | Construction_Male_01–08, Construction_Female_01, Male_Adult_01, Female_Adult_01, Security_Male_01 | |
| Assignment | avatars sorted by path, round-robin over pedestrians | |
| Texture format | TGA RLE | |
| Texture max size (px) | 1024 | `tools/fetch_rocketbox.py:TEXTURE_SIZE` |
| Rig | Humanoid, avatar per model | |
| Walk clips | `m_walk_neutral_01`, `f_walk_neutral_01` (Rocketbox `motextr_xy`), loop, root motion off | |
| LOD levels | hipoly, midpoly, lowpoly, ultralowpoly | |
| LOD screen heights | 0.25, 0.08, 0.03, 0.005; last present level culls at 0.005 | |
| LOD levels imported | Rocketbox FBX imports one renderer (hipoly) | |
| Materials | URP Lit, base colour white, `opacity` materials alpha-clipped at 0.5, double-sided | |
| Fallback | capsule placeholder when `Models/People` is empty | |
| Paths | 4 | |
| Rock clearance from paths (m) | 4 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PathClearance` |
| Position report rate (Hz) | 10 | `unity/Assets/Scripts/BrainClient.cs:actorsRate` |
| Pedestrian IDs | Unity: `Pedestrian_NN`; fake sim: `pedestrian-NN` | |

| Path | Waypoints (x, z) | Mode |
|---|---|---|
| 0 | (-30, -40), (30, -40) | ping-pong |
| 1 | (-10, -10), (10, -10), (10, 10), (-10, 10) | loop |
| 2 | (-50, 20), (-10, 60) | ping-pong |
| 3 | (40, -20), (40, 40) | ping-pong |

## Quarry scene

| Item | Value | Source |
|---|---|---|
| Build | Tools > Mining > Build Quarry Scene (Quarry World), `QuarrySceneBuilder.BuildQuarry` | |
| World | `data/worlds/quarry/` (scan visual, 300k-triangle `MeshCollider`, surround collider) | |
| Bounds | [-217.5, -217.5, 217.5, 217.5] | |
| Truck model | Komatsu HD465 | |
| Obstacles | 3 excavators from `equipment`, one circle per excavator (collider bounds) | |
| Debug layer | `MineDebug` (8): site markers, junctions, road lines, labels | `unity/ProjectSettings/TagManager.asset` |
| Debug layer seen by | editor only; excluded from truck and overview cameras, depth and lidar masks | |
| Truck spawn | `world.json` slots, heading along the road leaving the spawn site | |
| Heading look-ahead along road (m) | 20 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnLookAhead` |
| Minimum gap between parked trucks (m) | 2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnGap` |
| Slot-line shift search (m) | 10 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnSearch` |
| Slot-line shift step (m) | 0.5 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnSearchStep` |
| Slot-line shift choice | least maximum ground slope under the trucks | |
| Ground following | `MineGroundFollow`, height from the scan collider, model and sensors tilt with the ground | |
| Pedestrian ground probe height (m) | 30 | `unity/Assets/Scripts/PedestrianWalker.cs:probeHeight` |
| Crossing paths | 4 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CrossingCount` |
| Crossing length beyond road width (m) | 12 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CrossingMargin` |
| Crossing roads | first 4 roads by ordinal id | |
| Crossing geometry | 2 points at the x/z arc-length midpoint, ± (width_m + 12) / 2 along the right-hand normal (t.z, -t.x) of the segment containing it; point 0 = left | |
| Crossing mode | ping-pong | |

| Path | Road | Waypoints (x, z) |
|---|---|---|
| 0 | haul_1 | (-27.41, -4.98), (-39.46, 19.18) |
| 1 | haul_2 | (-131.61, 16.83), (-126.85, 43.41) |
| 2 | haul_3 | (31.33, 57.19), (31.33, 84.19) |
| 3 | haul_6 | (-5.30, -13.04), (11.04, -34.53) |

| Hello field | Count |
|---|---|
| roads | 8 |
| sites | 7 |
| junctions | 2 |
| obstacles | 3 |
| paths | 4 |
| header bytes | 15188 |

| Measurement (macOS player, M-series Mac, local brain) | Value |
|---|---|
| Unity frame time (ms) | 8.7 |
| Camera rate per camera, overview and truck-0..2 (fps) | 13.2–14.7 |
| Truck JPEG size (KB) | 35–41 |
| Overview JPEG size (KB) | 29 |
| Player size (MB) | 982 |

## Fake sim

| Item | Value | Source |
|---|---|---|
| Frame rate | 15 | `brain/brain/fakesim.py:FPS` |
| Horizontal FOV | 90 | `brain/brain/fakesim.py:FOV_DEG` |
| Depth range (m) | 100 | `brain/brain/fakesim.py:MAX_RANGE` |
| Truck camera | 640 × 360 RGB + 640 × 360 depth | |
| Overview size (px) | 480 | `brain/brain/fakesim.py:OVERVIEW_PX` |
| Camera height (m) | 3.6 | `brain/brain/fakesim.py:CAM_HEIGHT` |
| Rendering | per-column raycast, far-to-near layers, ground plane depth | |
| Obstacles | rocks (cylinders, height 1.5 r), trucks, pedestrians, 15 m walls | |
| Pedestrians per path | 3 | `brain/brain/fakesim.py:PEDESTRIANS_PER_PATH` |
| Pedestrian min speed (m/s) | 1.2 | `brain/brain/fakesim.py:MIN_WALK_SPEED` |
| Pedestrian max speed (m/s) | 1.6 | `brain/brain/fakesim.py:MAX_WALK_SPEED` |
| Pedestrian radius (m) | 0.3 | `brain/brain/fakesim.py:PEDESTRIAN_RADIUS` |
| Pedestrian height (m) | 1.8 | `brain/brain/fakesim.py:PEDESTRIAN_HEIGHT` |
| Pedestrian colour | RGB (255, 120, 20) | |
| Actors rate (Hz) | 10 | `brain/brain/fakesim.py:ACTORS_HZ` |
| Modes | flat test world (default), world mode `--world <dir>` | |

## Fake sim world

| Item | Value | Source |
|---|---|---|
| Option | `fakesim --world ../data/worlds/quarry`; empty = flat world | |
| Input | `<dir>/world.json` | |
| Bounds | `world.json` `bounds`, walls at bounds | |
| Ground | flat, y = 0 for cameras and collisions | |
| Lidar terrain | point y offset by `navgrid.json` ground at the hit minus ground under the truck (planner ground filter) | |
| Roads | `roads[]` with `points` reduced to x, z | |
| Sites | `sites[]` `{id, type, name}`, x, z from `pos` | |
| Junctions | `junctions[]`, x, z from `pos` | |
| Trucks | `trucks[]` by `slot`, `spawn`, yaw = `heading_deg` | |
| Obstacles | `equipment[]` as cylinders, no rocks | |
| Equipment radius (m) | 5 | `brain/brain/fakesim.py:EQUIPMENT_RADIUS` |
| Equipment height (m) | 5 | `brain/brain/fakesim.py:EQUIPMENT_HEIGHT` |
| Overview | roads drawn at `width_m` | |
| Docker | `docker compose --profile fake up` runs `/app/data/worlds/quarry`; `FAKESIM_WORLD=` for the flat world | |
| Data | `./data` mounted read-only at `/app/data` (brain and fakesim) | |

| Pedestrian crossings | Value | Source |
|---|---|---|
| Roads used | roads sorted by `id`, first N | |
| Crossing count | 4 | `brain/brain/fakesim.py:CROSSING_ROADS` |
| Position | road polyline arc-length midpoint | |
| Direction | perpendicular to the segment containing the midpoint | |
| Waypoint order | left → right of the road's from → to direction | |
| Extra length beyond road width (m) | 12 | `brain/brain/fakesim.py:CROSSING_EXTRA` |
| Length | `width_m` + extra, centred on the road | |
| Walkers per crossing | 3 | `brain/brain/fakesim.py:PEDESTRIANS_PER_PATH` |
| Start fractions | 0, 1/3, 2/3 | |
| Mode | ping-pong | |
| Sent as | hello `paths` | |

## Truck dynamics

| Item | Value | Source |
|---|---|---|
| Model | kinematic bicycle | |
| Max speed (m/s) | 8 | `unity/Assets/Scripts/TruckAgent.cs:maxSpeed` |
| Max steer (deg) | 30 | `unity/Assets/Scripts/TruckAgent.cs:maxSteerDeg` |
| Wheelbase (m) | 5 | `unity/Assets/Scripts/TruckAgent.cs:wheelbase` |
| Speed response (1/s) | 1.5 | `unity/Assets/Scripts/TruckAgent.cs:accel` |
| Command timeout (s) | 0.5 | `unity/Assets/Scripts/TruckAgent.cs:commandTimeout` |
| Fake sim max speed (m/s) | 8 | `brain/brain/fakesim.py:MAX_SPEED` |
| Fake sim max steer (deg) | 30 | `brain/brain/fakesim.py:MAX_STEER_DEG` |
| Fake sim wheelbase (m) | 5 | `brain/brain/fakesim.py:WHEELBASE` |

## Policy

| Item | Value | Source |
|---|---|---|
| Inputs | pose, goal, RGB, depth, latest lidar points | |
| Outputs | throttle 0..1, steer -1..1 (positive = right) | |
| Arrive radius (m) | 4 | `brain/brain/policy.py:ARRIVE_RADIUS` |
| Stop distance (m) | 6 | `brain/brain/policy.py:STOP_DIST` |
| Slow distance (m) | 15 | `brain/brain/policy.py:SLOW_DIST` |
| Full-steer heading error (deg) | 45 | `brain/brain/policy.py:STEER_GAIN_DEG` |
| Depth band | rows 35 %–65 %, split into left / centre / right thirds | |
| Statuses | `idle`, `driving`, `avoiding`, `holding`, `arrived` | |

## Planner

| Item | Value | Source |
|---|---|---|
| World | `data/worlds/default/` (`world.json`, `navgrid.json`), override with `WORLD_DIR` | |
| Route | A* on the nav grid (impassable cells grown by one cell), Chaikin smoothing, 2 m spacing | |
| Route edge cost | step cost + edge cost × (1 − clearance / safe clearance), clearance = cell centre to nearest blocked cell | |
| Edge cost | 2.0 | `brain/brain/planning/navmap.py:EDGE_COST` |
| Safe clearance (m) | 6.0 | `brain/brain/planning/navmap.py:SAFE_CLEARANCE` |
| Used when | the truck has a route; otherwise the straight-to-goal policy above | |
| Candidates | 11 steer × 4 target speeds (8, 5, 2.5, 0 m/s), kinematic bicycle rollout | |
| Candidate horizon (s) | 3.0 | `brain/brain/planning/driver.py:HORIZON` |
| Rollout step (s) | 0.1 | `brain/brain/planning/driver.py:DT` |
| Planner max speed (m/s) | 8.0 | `brain/brain/planning/driver.py:MAX_V` |
| Planner arrive radius (m) | 5.0 | `brain/brain/planning/driver.py:ARRIVE_RADIUS` |
| Output | throttle = target speed / 8, steer = candidate steer | |
| Chooser | hand-tuned score (baseline), or learned scorer from `PLANNER_CKPT` (needs torch) | |
| Lidar mount height (m) | 3.0 | `brain/brain/planning/driver.py:LIDAR_HEIGHT` |
| Lidar mount override | `LIDAR_HEIGHT` env var | |
| Min obstacle height above ground (m) | 0.8 | `brain/brain/planning/driver.py:MIN_OBSTACLE_HEIGHT` |
| Obstacle radius (m) | 4.0 | `brain/brain/planning/driver.py:OBSTACLE_RADIUS` |
| Obstacles | lidar points above the nav-grid ground on free cells; points on walls are left to the grid | |
| Truck half width (m) | 2.12 | `brain/brain/planning/driver.py:TRUCK_HALF_W` |
| Edge margin (m) | 0.5 | `brain/brain/planning/driver.py:EDGE_MARGIN` |
| Unsafe candidate | the centre or either side (half width + edge margin) hits a blocked cell, or comes within 1.5 m of an obstacle while closing in | |
| Holding | chosen target speed is 0, or no candidate is safe | |
| Scorer lidar bins | 60 | `brain/brain/planning/driver.py:N_RAYS` |
| Truck state | `path` (route, `[x, z]`), `path_idx`, `ghost_paths` (safe candidates) | |
| Route in state | `trucks[].route` = `path` | |
| Goal resolution | `{site}` → hello `sites` ∪ `junctions` position (fallback `world.json` sites), then A* route | |
| On arrival | `goal`, `goal_site`, `path`, `ghost_paths` cleared, status `arrived` then `idle` | |

## Sim protocol (TCP)

| Item | Value | Source |
|---|---|---|
| Framing | `u32 header_len \| u32 payload_len \| header JSON \| payload`, little-endian | |
| Max header (bytes) | 65536 | `brain/brain/protocol.py:MAX_HEADER` |
| Max payload (bytes) | 67108864 | `brain/brain/protocol.py:MAX_PAYLOAD` |

| Message | Direction | Header | Payload |
|---|---|---|---|
| `hello` | sim → brain | `trucks`, `cameras`, `bounds`, `truck_model`, `obstacles[{x,z,r}]`, `paths[{points[{x,z}]}]`; optional `roads[{id,from,to,width_m,one_way,points[{x,z}]}]` (points from → to), `sites[{id,type,name,x,z}]`, `junctions[{id,x,z}]` | none |
| `frame` | sim → brain | `camera`, `truck` (`""` if none), `rgb{w,h,flip,channels,format,bytes}`, `depth{w,h}` (0×0 if none), `pose{x,z,yaw}`, `speed` | `format` raw: RGB(A) `w·h·channels` bytes; `format` jpeg: `bytes` JPEG bytes; then depth float32 |
| `actors` | sim → brain | `pedestrians[{id,x,z,yaw}]` | none |
| `lidar` | sim → brain | `truck`, `points`, `format` (`int16cm`; `f32` accepted), `pose{x,z,yaw}` | `points` × 3 int16 cm (or float32 m) |
| `action` | brain → sim | `truck`, `throttle`, `steer` | none |

## Web

| Item | Value | Source |
|---|---|---|
| Video | `GET /ws/video` binary, one H.264 access unit per message: `u8 name_len \| name \| u8 flags (bit0 keyframe) \| u32 LE timestamp_ms \| H.264 Annex B AU` | |
| Video encoder | libx264 via PyAV (bundled in the aarch64/x86_64/macOS wheels, no NVENC), one encoder thread per camera shared by all viewers, `tune=zerolatency`, no B-frames, SPS/PPS in band on every keyframe, VBV cap = bitrate over 1 s | |
| Video profile | x264 `baseline` → constrained baseline, level 2.2 at 640×360 (`avc1.42c016`); browser reads the codec string from the SPS | |
| x264 preset | `veryfast` | |
| Video frame rate (fps) | 15 | `brain/brain/video.py:FPS` |
| Keyframe interval (frames) | 15 | `brain/brain/video.py:GOP_FRAMES` |
| Truck camera bitrate (kbps) | 600 | `brain/brain/video.py:TRUCK_KBPS` |
| Overview bitrate (kbps) | 400 | `brain/brain/video.py:OVERVIEW_KBPS` |
| Video join | new viewer gets the current GOP (latest keyframe + following AUs), then live AUs | |
| Viewer queue (AUs per camera) | 30 | `brain/brain/video.py:VIEWER_QUEUE` |
| Slow viewer | queue full → drop that camera's AUs until the next keyframe | |
| Encoder overload | latest frame wins, frames arriving while encoding are dropped | |
| Browser decode | WebCodecs `VideoDecoder`, Annex B (no `description`), `web/video.js` | |
| Video fallback | `GET /ws/video?codec=jpeg` binary: `u8 name_len \| name \| JPEG`, latest frame per camera; used when `VideoDecoder` lacks `avc1.42e01e` | |
| JPEG quality | 75 | `brain/brain/bridge.py:JPEG_QUALITY` |
| State | `GET /ws/state` JSON | |
| State rate (Hz) | 10 | `brain/brain/app.py:STATE_HZ` |
| Lidar stream | `GET /ws/lidar?truck={id}` binary: `u8 name_len \| name \| 3×f32 pose (x, z, yaw) \| zlib(planar delta int16 cm)` | |
| Lidar payload | int16 cm, planes x[N], y[N], z[N], each delta coded from 0 (wrapping int16), zlib (`protocol.py compress_points`); browser inflates with `DecompressionStream("deflate")`, `web/lidar_stream.js` | |
| Lidar zlib level | 6 | `brain/brain/protocol.py:LIDAR_ZLIB_LEVEL` |
| Lidar compression | encoded once per scan in the bridge, shared by all viewers | |
| Lidar viewer rate (Hz), env `LIDAR_VIEWER_HZ` | 10 | `brain/brain/app.py:LIDAR_VIEWER_HZ` |
| Lidar latest | `GET /api/trucks/{id}/lidar` → N×3 f32 body, `X-Lidar` header JSON `{truck, seq, points, pose}` | |
| Lidar view | three.js 0.169.0, selected truck, coloured by range | |
| Map | preview background, roads (scaled by `width_m`), sites (labelled, coloured by type), junctions, obstacles, pedestrian paths, pedestrians, truck routes, goals, trucks | |
| Map extent | hello `bounds` | |
| Map canvas (px) | 800 × 800, CSS-scaled | |
| Map click | `POST` `{x,z}` for the selected truck (brain snaps to a node when roads exist) | |
| Send to | per-truck site select, `POST` `{site}` | |
| Truck list | status, speed, `goal_site` | |
| Read state | `GET /api/state`: `bounds`, `roads`, `sites`, `junctions`, `world_seq`, `cameras`, `truck_model`, `obstacles`, `paths`, `pedestrians`, `trucks[{id,x,z,yaw,speed,goal,goal_site,route,path_idx,ghost_paths,status,throttle,steer}]` | |
| State stream | `/ws/state` = `/api/state` without `roads`, `sites`, `junctions`; refetch `/api/world` when `world_seq` changes | |
| World | `GET /api/world` → `{bounds, roads, sites, junctions, preview, preview_bounds}` | |
| World preview | `GET /api/world/preview` → `WORLD_DIR/world.json` `assets.preview` (PNG, top-down, north up, aligned to `bounds`), 404 if unset | |
| Preview shown | when `preview_bounds` = hello `bounds` and roads exist | |
| `WORLD_DIR` | brain env (planner nav grid and preview), default `data/worlds/default`, Docker compose `/app/data/worlds/quarry` | |
| Set goal | `POST /api/trucks/{id}/goal` `{"site"}` or `{"x","z"}`, plans a route | |
| Sites | `GET /api/sites` → `[{"id","type","pos"}]` (from `world.json`) | |
| Go to site | `POST /api/trucks/{id}/site/{site_id}` (same as `{"site"}`) | |
| Goal fields | `goal` [x, z], `goal_site` node id or null, `route` [[x, z], …] (planner path) | |
| Clear goal | `DELETE /api/trucks/{id}/goal` (clears `goal`, `goal_site`, `route`) | |

## Radio

| Item | Value | Source |
|---|---|---|
| Port | 8090, bound to 127.0.0.1 unless `HOST` is set | |
| Brain poll rate (Hz) | 5 | `radio/server.py:POLL_HZ` |
| Goals | brain routes (`roads` + per-truck `route`): one `{"site"}` per leg; otherwise chained `{"x","z"}` waypoints | |
| Brain-cleared goal counts as reached within (m) | 6 | `radio/server.py:BRAIN_CLEAR_M` |
| Max command length (chars) | 300 | `radio/server.py:MAX_COMMAND_CHARS` |
| Max WebSocket message (bytes) | 512 | `radio/server.py:WS_MAX_MSG` |
| Max audio upload (KB) | 600 (`RADIO_MAX_AUDIO_KB`) | |
| STT per session / global (per min) | 10 / 30 | |
| Commands per session / global (per min) | 20 / 60 | |
| TTS syntheses global (per min) | 60 | |
| Login attempts per IP / global (per min) | 5 / 20 | |

## Isaac sim

| Item | Value | Source |
|---|---|---|
| Code | `isaac/sim/`, container files `isaac/docker/` | |
| Image | `nvcr.io/nvidia/isaac-sim:6.2.0` (ARM64), build arg `ISAAC_TAG` | |
| Entry | `/isaac-sim/python.sh /mining/isaac/sim/main.py --brain 127.0.0.1:9000 --world /data/worlds/quarry --cache /cache` | |
| Container | `mining-isaac-sim`, compose `isaac/docker/compose.yml`, profile `isaac`, host network, `nvidia.com/gpu=all` | |
| Renderer | RTX `RealTimePathTracing`, headless, Replicator `rgb` annotators | |
| Stage | Z up, metres; Isaac (X, Y, Z) = world (x, z, y) | |
| Yaw | world yaw (0 = +z, clockwise) = rotation about Isaac -Z from +Y | |
| Assets | own GLB reader → USD (`isaac/sim/usdbuild.py`), axis swap and transforms baked into points, cached in `downloads/isaac/cache` | |
| Scan visual | `visual.glb` node transforms, then `scan_transform.matrix`, then axis swap | |
| Collider / surround | world frame, axis swap only | |
| Sky | `sky_pano.jpg` latlong dome light, rotation `--dome-rotation` | |
| Raycasts | NVIDIA Warp `mesh_query_ray` on collider + surround meshes, oriented truck/excavator boxes, pedestrian cylinders | |
| Lidar method | Warp raycasts (RTX lidar not used) | |
| Frame rate (default; `fps` in `isaac/sim/config.json`) | 30 | `isaac/sim/sensors.py:FPS` |
| Truck camera width | 640 | `isaac/sim/sensors.py:CAM_W` |
| Truck camera height | 360 | `isaac/sim/sensors.py:CAM_H` |
| Horizontal FOV | 90 | `isaac/sim/sensors.py:HFOV` |
| Depth width | 64 | `isaac/sim/sensors.py:DEPTH_W` |
| Depth height | 36 | `isaac/sim/sensors.py:DEPTH_H` |
| Depth range (m) | 100 | `isaac/sim/sensors.py:DEPTH_RANGE` |
| Sensor offset ahead of box (m) | 0.2 | `isaac/sim/sensors.py:SENSOR_OFFSET` |
| Camera height (fraction of truck height) | 0.8 | `isaac/sim/sensors.py:CAMERA_HEIGHT_FRACTION` |
| Lidar height (fraction of truck height) | 0.6 | `isaac/sim/sensors.py:LIDAR_HEIGHT_FRACTION` |
| Lidar range (m) | 100 | `isaac/sim/sensors.py:LIDAR_RANGE` |
| Lidar horizontal FOV (deg) | 120 | `isaac/sim/sensors.py:LIDAR_HFOV` |
| Lidar vertical min (deg) | -20 | `isaac/sim/sensors.py:LIDAR_VMIN` |
| Lidar vertical max (deg) | 10 | `isaac/sim/sensors.py:LIDAR_VMAX` |
| Lidar columns | 240 | `isaac/sim/sensors.py:LIDAR_COLS` |
| Lidar channels | 30 | `isaac/sim/sensors.py:LIDAR_CHANNELS` |
| Lidar rate (Hz) | 10 | `isaac/sim/sensors.py:LIDAR_HZ` |
| Actors rate (Hz) | 10 | `isaac/sim/sensors.py:ACTORS_HZ` |
| JPEG quality | 80 | `isaac/sim/protocol.py:JPEG_QUALITY` |
| Overview | orthographic top-down, 640 × 360, vertical extent = bounds z range, image top = +z | |
| Overview camera height (m) | 400 | `isaac/sim/sensors.py:OVERVIEW_HEIGHT` |
| Truck frame `cam` | `{x, y, z, yaw, pitch, roll, hfov}` camera world pose, Unity Euler (pitch > 0 looks down) | |
| Physics substep (s) | 1/60, real-time paced | |
| Max speed (m/s) | 8 | `isaac/sim/kinematics.py:MAX_SPEED` |
| Max steer (deg) | 30 | `isaac/sim/kinematics.py:MAX_STEER_DEG` |
| Wheelbase (m) | 5 | `isaac/sim/kinematics.py:WHEELBASE` |
| Speed response (1/s) | 1.5 | `isaac/sim/kinematics.py:ACCEL` |
| Command timeout (s) | 0.5 | `isaac/sim/kinematics.py:COMMAND_TIMEOUT` |
| Ground follow | 4 wheels + centre, Warp ray down the collider (surround fallback), height smoothing, tilt of body and sensors | |
| Height smoothing (s) | 0.06 | `isaac/sim/kinematics.py:HEIGHT_SMOOTH` |
| Snap distance (m) | 4 | `isaac/sim/kinematics.py:SNAP_DISTANCE` |
| Max tilt (deg) | 25 | `isaac/sim/kinematics.py:MAX_TILT_DEG` |
| Tilt smoothing (s) | 0.15 | `isaac/sim/kinematics.py:TILT_SMOOTH` |
| Truck model | Komatsu HD465, GLB X flipped (Unity frame), length along z | |
| Truck length (m) | 9.35 | `isaac/sim/world.py:TRUCK_LENGTH` |
| Trucks | 3 | `isaac/sim/world.py:TRUCK_COUNT` |
| Spawn | `world.json` spawns, heading along the first road leaving the nearest site, spread along the slot line | |
| Heading look-ahead (m) | 20 | `isaac/sim/world.py:SPAWN_LOOK_AHEAD` |
| Minimum gap between parked trucks (m) | 2 | `isaac/sim/world.py:SPAWN_GAP` |
| Slot-line shift search (m) | 10 | `isaac/sim/world.py:SPAWN_SEARCH` |
| Slot-line shift step (m) | 0.5 | `isaac/sim/world.py:SPAWN_SEARCH_STEP` |
| Excavator length (m) | 10 | `isaac/sim/world.py:EXCAVATOR_LENGTH` |
| Crossing paths | 4 | `isaac/sim/world.py:CROSSING_COUNT` |
| Crossing length beyond road width (m) | 12 | `isaac/sim/world.py:CROSSING_MARGIN` |
| Pedestrians per path | 3 | `isaac/sim/world.py:PEDESTRIANS_PER_PATH` |
| Min walking speed (m/s) | 1.2 | `isaac/sim/world.py:MIN_WALK_SPEED` |
| Max walking speed (m/s) | 1.6 | `isaac/sim/world.py:MAX_WALK_SPEED` |
| Pedestrian turn rate (deg/s) | 360 | `isaac/sim/pedestrians.py:TURN_RATE` |
| Pedestrian height (m) | 1.8 | `isaac/sim/pedestrians.py:PEDESTRIAN_HEIGHT` |
| Pedestrian radius (m) | 0.3 | `isaac/sim/pedestrians.py:PEDESTRIAN_RADIUS` |
| Pedestrian IDs | `Pedestrian_NN` | |
| Pedestrian visual | orange capsule | |
| Dry run | `--dry-run`: no Isaac imports, black JPEG frames, Warp raycasts when `warp` is importable, heightmap otherwise | |

## Bucket material

| Item | Value | Source |
|---|---|---|
| Code | `isaac/material/` (`model.py` numpy core, `stage.py` stage writer, `rig.py` dig cycle, `geometry.py` bucket/bed shapes) | |
| API | `BucketMaterial(stage, excavators_cfg, trucks_cfg, config, sites)`, `update(dt, excavators=[(id, bucket_4x4, phase, phase_t)], trucks=[(id, truck_4x4, under_excavator_id)])`, `empty_truck(id)`, `load(id)`, `tonnes(id)`, `meshes()`, `aim_points()` | |
| Bucket matrix | rigged GLB `bucket` node frame (pin origin, glTF axes) → Isaac world, column vectors (Gf row-major accepted) | |
| Truck matrix | truck prim world matrix (`isaac_matrix(rotation, position)`), local X lateral, Y forward, Z up | |
| Dig cycle | same keys and smootherstep as `envgen/preview/index.html` `EXC_KEYS`, `MineExcavatorRig.RigKeys` | `isaac/material/rig.py:KEYS` |
| Cycle period (s) | 8 | `isaac/material/rig.py:CYCLE_S` |
| Dump yaw (rad, toward cab side) | 1.7 | `isaac/material/rig.py:DUMP_YAW` |
| Joint tracking / rest rate (1/s) | 8 / 2.5 | `isaac/material/rig.py:TRACK_RATE`, `REST_RATE` |
| Phases (cycle fraction) | reach 0, dig 0.12, lift 0.30, swing 0.42, dump 0.60, return 0.78; `hold` when not digging | `isaac/material/rig.py:PHASES` |
| Optional boom raise at the truck side (deg, compensated by bucket curl) | 0 (25 in the demo) | `isaac/material/rig.py:pose` |
| Truck payload (t) | 55 | `isaac/material/model.py:Config.payload_t` |
| Passes to full | 4 | `isaac/material/model.py:Config.passes` |
| Loose density (t/m³) | 1.8 | `isaac/material/model.py:Config.density` |
| Angle of repose (deg) | 37 | `isaac/material/model.py:Config.repose_deg` |
| Bucket fill factor per pass | 0.92–1.06 | `isaac/material/model.py:Config.fill_lo`, `fill_hi` |
| Breakout spill (fraction) | 0.02 | `isaac/material/model.py:Config.breakout` |
| Pour time (s) | 0.9 | `isaac/material/model.py:Config.pour_s` |
| Cohesion before flow (fraction of full) | 0.3 | `isaac/material/model.py:Config.cohesion` |
| Particles per tonne | 14 | `isaac/material/model.py:Config.particles_per_t` |
| Particle pool | 768 | `isaac/material/model.py:Config.max_particles` |
| Face pile cap (passes) | 3 | `isaac/material/model.py:Config.pile_passes` |
| Truck empty slide (s) | 2.5 | `isaac/material/model.py:Config.empty_s` |
| Auto empty: site radius (m) / stop speed (m/s) / stopped for (s) | 30 / 0.3 / 1.5 | `isaac/material/model.py:Config` |
| Bucket heap grid | 9 × 24 | `isaac/material/geometry.py:BUCKET_NX`, `BUCKET_NZ` |
| Bed grid cell (m) | 0.2 | `isaac/material/geometry.py:BED_CELL` |
| Bed heap above rim (m) | 0.9 | `isaac/material/geometry.py:BED_HEAP` |
| Chunks riding per bucket / per bed | 8 / 14 | `isaac/material/model.py:CHUNKS_BUCKET`, `CHUNKS_BED` |
| Bucket capacity | level plane (world up, or bucket up while digging) clipped to the cavity columns, heap dome when upright | |
| Bed load | height field over the HD465 body floor (derived from the GLB dump body), 3 × 3 deposit kernel, repose relaxation, child prim of the truck | |
| No truck under the bucket | material lands at the face pile; the next dig takes from the pile first | |
| Colour | per excavator `colour`, or `face_colour(world_dir, x, z)` from `minimap.png` | |
| Dust puffs (off by default) | 0 | `isaac/material/model.py:Config.max_dust` |
