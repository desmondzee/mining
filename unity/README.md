# Unity

Unity 6.3 LTS project. Protocol and parameters: [../spec.md](../spec.md).

## Open

1. Unity Hub > Add > select this `unity/` folder; open with 6000.3.x.
2. Wait for packages (URP, glTFast) to resolve and the GLBs to import.
3. Tools > Mining > Build Quarry Scene (Komatsu HD465) or (CAT 785C).
4. Commit the generated `.meta` files, `Assets/Settings`, `Assets/Prefabs`, `Assets/Scenes`.

## Layout

| Path | Contents |
|---|---|
| `Assets/Scripts/` | runtime scripts |
| `Assets/Editor/QuarrySceneBuilder.cs` | URP setup, truck prefabs, scene |
| `Assets/Models/Trucks/` | truck GLBs, `CREDITS.md` |
| `Packages/manifest.json` | URP, glTFast |

## Scripts

| Script | Attach to |
|---|---|
| `BrainClient.cs` | one empty GameObject |
| `TruckAgent.cs` | truck root (kinematic Rigidbody) |
| `SensorCamera.cs` | truck front camera, overview camera |
| `LidarSensor.cs` | truck child transform at the sensor mount |
| `PedestrianWalker.cs` | pedestrian root (CapsuleCollider, kinematic Rigidbody); `path` = parent of waypoint transforms |
| `BrainProtocol.cs` | none |

## Scene

| Object | Contents |
|---|---|
| `Ground` | 200 × 200 m plane |
| `Rocks` | box obstacles, seeded |
| `Pedestrians` | `Path_N` waypoints, `Pedestrian_N` walkers |
| `truck-0..2` | truck prefab instances |
| `Overview` | orthographic top-down `SensorCamera` |
| `BrainClient` | brain connection |

## Brain address

| Priority | Setting |
|---|---|
| 1 | `-brain-host H -brain-port P` |
| 2 | `BRAIN_HOST`, `BRAIN_PORT` |
| 3 | inspector |

Editor on Mac against watson: `ssh -N -L 9000:127.0.0.1:9000 watson`, host `127.0.0.1`.

## Open items

| Item | State |
|---|---|
| Project opened in Unity | not yet |
| Truck yaw offsets | from model inspection, check visually |
| CAT 785C licence | not documented |
| Pedestrian models | capsule placeholders |
| Linux ARM64 player on GB10 | not verified |
| `-batchmode -nographics` | disables rendering, not usable |
