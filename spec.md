# Spec

## Platform

| Item | Value |
|---|---|
| Host | watson (Brev), NVIDIA GB10, aarch64 |
| NVIDIA driver | 580.178.04 |
| Repo on host | `~/Documents/mining` |
| Simulator | Unity 6.3 LTS, URP |
| Unity player target | Linux ARM64, IL2CPP, `com.unity.sdk.linux-arm64` |
| Unity editor | macOS (no ARM64 Linux editor) |
| Brain | Python 3.12, FastAPI, uv |
| Containers | Docker Compose, project `mining` |
| Environment source | World Labs Marble, Gaussian splat `.ply` / `.spz` |
| Splat renderer | aras-p/UnityGaussianSplatting |
| Collision | separate colliders (splats have none) |

## Network

| Item | Value | Source |
|---|---|---|
| Website + API port | 8080 | |
| Sim TCP port | 9000 | `unity/Scripts/BrainClient.cs:port` |
| Host binding | 127.0.0.1 | |
| Remote access | `ssh -N -L 8080:127.0.0.1:8080 watson` | |
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
| Truck camera width | 640 | `unity/Scripts/SensorCamera.cs:width` |
| Truck camera height | 360 | `unity/Scripts/SensorCamera.cs:height` |
| Frame rate | 15 | `unity/Scripts/SensorCamera.cs:fps` |
| Horizontal FOV | 90 | `unity/Scripts/SensorCamera.cs:horizontalFov` |
| Readback format | RGBA32, bottom-up (`flip: true`) | |
| Depth width | 64 | `unity/Scripts/SensorCamera.cs:depthWidth` |
| Depth height | 36 | `unity/Scripts/SensorCamera.cs:depthHeight` |
| Depth range (m) | 100 | `unity/Scripts/SensorCamera.cs:depthRange` |
| Depth format | float32 metres, perpendicular distance, row 0 = top, miss = range | |
| Depth method | `RaycastCommand` grid against colliders | |

## Lidar

| Item | Value | Source |
|---|---|---|
| Method | `RaycastCommand` batch against colliders | |
| Range (m) | 100 | `unity/Scripts/LidarSensor.cs:range` |
| Horizontal FOV (deg) | 120 | `unity/Scripts/LidarSensor.cs:horizontalFov` |
| Vertical min (deg) | -20 | `unity/Scripts/LidarSensor.cs:verticalMin` |
| Vertical max (deg) | 10 | `unity/Scripts/LidarSensor.cs:verticalMax` |
| Columns | 240 | `unity/Scripts/LidarSensor.cs:columns` |
| Channels | 30 | `unity/Scripts/LidarSensor.cs:channels` |
| Rate (Hz) | 10 | `unity/Scripts/LidarSensor.cs:rate` |
| Resolution | 0.5° H × 1° V | |
| Beam angles | cell centres: az = -h_fov/2 + (j + 0.5)·h_fov/columns, el = v_min + (i + 0.5)·(v_max - v_min)/channels | |
| Max points per scan | 7200 | |
| Point format | float32 x, y, z, little-endian, 12 bytes per point | |
| Frame | sensor-local, Unity axes: x right, y up, z forward, metres | |
| Mount | `LidarSensor` transform, unit scale; mask excludes own truck | |
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

## Fake sim

| Item | Value | Source |
|---|---|---|
| Frame rate | 15 | `brain/brain/fakesim.py:FPS` |
| Horizontal FOV | 90 | `brain/brain/fakesim.py:FOV_DEG` |
| Depth range (m) | 100 | `brain/brain/fakesim.py:MAX_RANGE` |
| Truck camera | 640 × 360 RGB + 640 × 360 depth | |
| Overview size (px) | 480 | `brain/brain/fakesim.py:OVERVIEW_PX` |

## Truck dynamics

| Item | Value | Source |
|---|---|---|
| Model | kinematic bicycle | |
| Max speed (m/s) | 8 | `unity/Scripts/TruckAgent.cs:maxSpeed` |
| Max steer (deg) | 30 | `unity/Scripts/TruckAgent.cs:maxSteerDeg` |
| Wheelbase (m) | 5 | `unity/Scripts/TruckAgent.cs:wheelbase` |
| Speed response (1/s) | 1.5 | `unity/Scripts/TruckAgent.cs:accel` |
| Command timeout (s) | 0.5 | `unity/Scripts/TruckAgent.cs:commandTimeout` |
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

## Sim protocol (TCP)

| Item | Value | Source |
|---|---|---|
| Framing | `u32 header_len \| u32 payload_len \| header JSON \| payload`, little-endian | |
| Max header (bytes) | 65536 | `brain/brain/protocol.py:MAX_HEADER` |
| Max payload (bytes) | 67108864 | `brain/brain/protocol.py:MAX_PAYLOAD` |

| Message | Direction | Header | Payload |
|---|---|---|---|
| `hello` | sim → brain | `trucks`, `cameras`, `bounds` | none |
| `frame` | sim → brain | `camera`, `truck` (`""` if none), `rgb{w,h,flip,channels}`, `depth{w,h}` (0×0 if none), `pose{x,z,yaw}`, `speed` | RGB(A) bytes, then depth float32 |
| `lidar` | sim → brain | `truck`, `points`, `pose{x,z,yaw}` | `points` × 3 float32 |
| `action` | brain → sim | `truck`, `throttle`, `steer` | none |

## Web

| Item | Value | Source |
|---|---|---|
| Video | `GET /ws/video` binary: `u8 name_len \| name \| JPEG` | |
| Video delivery | latest frame per camera, slow clients skip frames | |
| JPEG quality | 75 | `brain/brain/bridge.py:JPEG_QUALITY` |
| State | `GET /ws/state` JSON | |
| State rate (Hz) | 10 | `brain/brain/app.py:STATE_HZ` |
| Lidar stream | `GET /ws/lidar?truck={id}` binary: `u8 name_len \| name \| 3×f32 pose (x, z, yaw) \| N×3 f32 points` | |
| Lidar latest | `GET /api/trucks/{id}/lidar` → N×3 f32 body, `X-Lidar` header JSON `{truck, seq, points, pose}` | |
| Lidar view | three.js 0.169.0, selected truck, coloured by range | |
| Read state | `GET /api/state` | |
| Set goal | `POST /api/trucks/{id}/goal` `{"x","z"}`, plans a route | |
| Sites | `GET /api/sites` → `[{"id","type","pos"}]` | |
| Go to site | `POST /api/trucks/{id}/site/{site_id}` | |
| Clear goal | `DELETE /api/trucks/{id}/goal` | |
