# Unity environment importer (Mine Sim)

This folder builds the mine environment (terrain, sites, roads, junctions, road graph) from `data/worlds/<id>/` inside Unity 6 (6000.x). Navigation, driving and compute live elsewhere. This part only gives you the world and a route API.

## Setup
1. Create or open a Unity 6 project. URP and Built-in both work.
2. Copy or symlink `unity/Assets/MineSim` into the project's `Assets/`. If the project sits at `unity/`, it is already in place.
3. Optional packages (Package Manager). Each one is detected automatically through `versionDefines` in the asmdefs:
   | Package | Define | Adds |
   |---|---|---|
   | `com.unity.splines` (2.x) | `MINESIM_SPLINES` | `SplineContainer` on every `Road_*` |
   | `com.unity.ugui` 2.x (built into Unity 6) / `com.unity.textmeshpro` | `MINESIM_TMP` | TextMeshPro labels. Run *Window > TextMeshPro > Import TMP Essential Resources* once, otherwise the labels fall back to `TextMesh` |
   | `com.unity.cloud.gltfast` | `MINESIM_GLTFAST` | imports `collider.glb` (Marble worlds) as invisible MeshColliders |
   | `com.unity.inputsystem` | `MINESIM_INPUTSYSTEM` | the L/R hotkeys use the Input System; otherwise they use the legacy Input Manager |

   You'll see "missing assembly reference" warnings for packages you haven't installed. They're harmless.

## Import
- **Tools > Mine Sim > Import World...** and pick `data/worlds/<id>/` (for example `data/worlds/default`).
- **Tools > Mine Sim > Reimport Last** rebuilds from the same folder. It replaces any `MineWorld_<id>` in the active scene.
- Generated assets (TerrainData, terrain layers, textures, materials, a copy of `world.json`) go to `Assets/MineSim/Generated/<id>/`. Save the scene afterwards.

Hierarchy:
```
MineWorld_<id>        MineWorldRoot, MineRoadGraph, MineLabelToggle, [MineDesignSpec]
  Terrain             Unity Terrain + TerrainCollider (from heightmap.raw + heightmap_meta.json; navgrid fallback)
  Sites/Site_<id>     MineSiteMarker (siteId, siteType, siteName, tonnes), marker primitive (no collider), Label
  Junctions/Junction_<id>   MineJunction, Label
  Roads/Road_<id>     MineRoad (points, from, to, width_m, grade_pct, one_way), Line (LineRenderer), WP_000.., Label, [SplineContainer]
  Collider            (Marble + glTFast) invisible MeshColliders
  Splat (import manually)   (Marble) MineAssetNote with instructions + the splat_transform already applied
```

## Nav-team API (namespace `MineSim`)
```csharp
var world = FindFirstObjectByType<MineWorldRoot>();
var graph = world.graph;                                   // MineRoadGraph

// Site ids differ per world, so look sites up by type
MineSiteMarker crusher = world.FirstSiteOfType("crusher");
List<MineSiteMarker> ores = MineSiteMarker.FindByType("ore");   // sorted by id

// World-space polyline along roads (Dijkstra by road length; roads are reversed when driven to->from)
List<Vector3> route = graph.GetRoute(ores[0].siteId, crusher.siteId);
List<Vector3> r2 = graph.GetRoute("pit_floor", "crusher", avoidNodes: new[] { "j_ramp_top" });
List<Vector3> r3 = graph.GetRouteFrom(truck.position, "parking");   // starts at the nearest node
List<MineRoadGraph.Leg> legs = graph.GetRouteLegs(a, b);           // which roads, in which direction (null if unreachable)
string nearest = graph.NearestNodeId(pos);

// Raw roads
MineRoad road = world.FindRoad("ramp_main_1");
Vector3[] pts = road.points;        // centreline, ordered from -> to, ~5 m spacing; also child WP_000..
```
- `GetRoute` returns an empty list (and logs a warning) when there's no route. When the start and goal are the same node it returns that node's position.
- A road with `one_way: true` can only be driven from → to.
- The graph builds itself from the children the first time you use it. Call `graph.Rebuild()` after you change roads at runtime.
- Without roads in world.json, routes are empty. Fall back to navgrid A*.

## Runtime hotkeys
`MineLabelToggle` sits on the root: **L** shows/hides labels, **R** shows/hides the road lines. You can change the keys in the inspector. If R collides with your driving controls, set it to `None`.

## Coordinates
- Contract `(x, y, z)` (metres, Y up, X east, Z north) maps to Unity `Vector3(x, y, z)` with no conversion. Ground `[x, z]` maps to `(x, terrainY, z)`.
- Terrain: position = `(origin.x, min_height_m, origin.z)`, size = `(size_m.x, height_range_m, size_m.z)`. Resolution comes from `heightmap_meta.json` and is never hardcoded (513 and 1025 both work). Raw row 0 = south (min z) = Unity terrain row 0, so the importer never flips it.
- Site positions use `pos3` when present. Otherwise the importer samples the terrain.
- Marble collider: the holder gets scale `(-s, s, s)`, rotation `splat_transform.rotation_euler_deg`, and y = `ground_offset`. The `-x` undoes glTFast's handedness flip. If it looks mirrored, make it positive.

## Files
- `Assets/MineSim/Editor/MineWorldImporter.cs`: menu and builder
- `Assets/MineSim/Runtime/`: `MineWorldData` (world.json model) + `MiniJson`, `MineWorldRoot`, `MineSiteMarker`, `MineJunction`, `MineRoad`, `MineRoadGraph`, `MineLabel`, `MineBillboard`, `MineLabelToggle`, `MineDesignSpec`, `MineAssetNote`
