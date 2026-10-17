# Unity

Protocol, resolutions and parameters: [../spec.md](../spec.md).

## Scripts

| Script | Attach to |
|---|---|
| `BrainClient.cs` | one empty GameObject |
| `TruckAgent.cs` | each truck (Rigidbody required) |
| `SensorCamera.cs` | each truck front camera, and one overview camera |
| `BrainProtocol.cs` | none |

## Scene setup

1. Copy `Scripts/` into `Assets/`.
2. Add `BrainClient`; set `bounds` to the quarry extent.
3. Per truck: `TruckAgent` with `truckId` = `truck-N`; child forward Camera with `SensorCamera`, `cameraName` = `truck-N`, `truck` = the TruckAgent.
4. Overview Camera with `SensorCamera`, `cameraName` = `overview`, `truck` empty.
5. Marble splat via aras-p/UnityGaussianSplatting; add colliders for ground and obstacles.

## Brain address

| Priority | Setting |
|---|---|
| 1 | `-brain-host H -brain-port P` |
| 2 | `BRAIN_HOST`, `BRAIN_PORT` |
| 3 | inspector |

Editor on Mac against watson: `brev port-forward watson --port 9000:9000`, host `127.0.0.1`.

## Open items

| Item | State |
|---|---|
| Linux ARM64 player renders headless in a container on GB10 | not verified |
| `-batchmode -nographics` | disables rendering, not usable |
