// Tools/Mine Sim/Import World... : builds MineWorld_<id> from data/worlds/<id>/ (see CONTRACT.md).
// Optional packages are detected via versionDefines in MineSim.Editor.asmdef:
//   TextMeshPro and Splines come through MineImporterHooks (optional assemblies under Editor/Integrations).
//   MINESIM_GLTFAST (com.unity.cloud.gltfast / com.atteneder.gltfast).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MineSim.EditorTools
{
    public static class MineWorldImporter
    {
        private const string PrefLastDir = "MineSim.LastWorldDir";
        private const string GeneratedRoot = "Assets/MineSim/Generated";

        // Tweakables
        public static float RoadLineWidthScale = 0.35f;   // LineRenderer width = width_m * this
        public static float RoadLineLift = 0.4f;          // metres above the centreline
        public static float SiteLabelHeight = 5f;         // world-space text height (m) at reference distance
        public static float RoadLabelHeight = 3f;
        public static float JunctionLabelHeight = 2.5f;
        /// <summary>Scan worlds (source "scan" / assets.visual): the scan mesh is the ground, so no Terrain by default.</summary>
        public static bool BuildTerrainForScanWorlds = false;
        /// <summary>Layer for the scan ground collider if it exists in Tags and Layers; otherwise Default.</summary>
        public static string GroundLayerName = "MineGround";

        // ------------------------------------------------------------------ menu
        [MenuItem("Tools/Mine Sim/Import World...")]
        private static void ImportMenu()
        {
            string start = EditorPrefs.GetString(PrefLastDir, "");
            if (string.IsNullOrEmpty(start) || !Directory.Exists(start))
            {
                string guess = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "data", "worlds"));
                start = Directory.Exists(guess) ? guess : Application.dataPath;
            }
            string dir = EditorUtility.OpenFolderPanel("Select a world folder (data/worlds/<id>/)", start, "");
            if (string.IsNullOrEmpty(dir)) return;
            Import(dir);
        }

        [MenuItem("Tools/Mine Sim/Reimport Last")]
        private static void ReimportLast()
        {
            string dir = EditorPrefs.GetString(PrefLastDir, "");
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir))
            {
                EditorUtility.DisplayDialog("Mine Sim", "No previous world folder. Use Tools/Mine Sim/Import World... first.", "OK");
                return;
            }
            Import(dir);
        }

        [MenuItem("Tools/Mine Sim/Reimport Last", true)]
        private static bool ReimportLastValidate()
        {
            return !string.IsNullOrEmpty(EditorPrefs.GetString(PrefLastDir, ""));
        }

        // ------------------------------------------------------------------ import
        private class Ctx
        {
            public MineWorldData w;
            public string dir;
            public string gen;          // Assets/MineSim/Generated/<id>
            public Terrain terrain;
            public TerrainData td;
            public Material lineMat;
            public Collider ground;     // scan worlds: ground MeshCollider (built before sites so GroundY can raycast it)
            public bool isScan;
        }

        /// <summary>Import a world folder into the active scene. Returns the root GameObject (or null on failure).</summary>
        public static GameObject Import(string worldDir)
        {
            worldDir = Path.GetFullPath(worldDir);
            string worldJson = Path.Combine(worldDir, "world.json");
            if (!File.Exists(worldJson))
            {
                EditorUtility.DisplayDialog("Mine Sim", "world.json not found in\n" + worldDir, "OK");
                return null;
            }

            var c = new Ctx { dir = worldDir };
            try
            {
                c.w = MineWorldData.Load(worldDir);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorUtility.DisplayDialog("Mine Sim", "Failed to parse world.json:\n" + e.Message, "OK");
                return null;
            }
            EditorPrefs.SetString(PrefLastDir, worldDir);

            string safeId = SafeName(c.w.id);
            c.gen = EnsureFolder(GeneratedRoot + "/" + safeId);
            EnsureFolder(c.gen + "/Materials");

            GameObject root = null;
            try
            {
                EditorUtility.DisplayProgressBar("Mine Sim", "Removing previous import", 0.05f);
                string rootName = "MineWorld_" + c.w.id;
                Scene scene = SceneManager.GetActiveScene();
                foreach (var go in scene.GetRootGameObjects())
                    if (go.name == rootName) Undo.DestroyObjectImmediate(go);

                root = new GameObject(rootName);
                var wr = root.AddComponent<MineWorldRoot>();
                wr.worldId = c.w.id;
                wr.worldName = c.w.name;
                wr.source = c.w.source;
                wr.worldDir = worldDir;
                wr.sizeM = c.w.sizeM;
                wr.origin = c.w.origin;

                CopyWorldJson(c, worldJson);

                c.isScan = c.w.source == "scan" || c.w.Asset("visual") != null;
                if (!c.isScan || BuildTerrainForScanWorlds)
                {
                    EditorUtility.DisplayProgressBar("Mine Sim", "Building terrain", 0.15f);
                    c.terrain = BuildTerrain(c, root.transform);
                }
                c.td = c.terrain != null ? c.terrain.terrainData : null;
                wr.terrain = c.terrain;

                if (c.isScan)
                {
                    EditorUtility.DisplayProgressBar("Mine Sim", "Scan collider + visual mesh (can take a minute)", 0.3f);
                    BuildScan(c, root.transform);
                    wr.groundCollider = c.ground;
                }

                c.lineMat = LineMaterial(c);

                EditorUtility.DisplayProgressBar("Mine Sim", "Sites", 0.6f);
                BuildSites(c, root.transform);
                EditorUtility.DisplayProgressBar("Mine Sim", "Junctions", 0.65f);
                BuildJunctions(c, root.transform);
                EditorUtility.DisplayProgressBar("Mine Sim", "Roads", 0.7f);
                BuildRoads(c, root.transform);

                var graph = root.AddComponent<MineRoadGraph>();
                graph.Rebuild();
                wr.graph = graph;
                root.AddComponent<MineLabelToggle>();
                BuildDesignSpec(c, root);

                EditorUtility.DisplayProgressBar("Mine Sim", "Optional assets", 0.85f);
                if (!c.isScan) BuildCollider(c, root.transform);
                BuildSplatNote(c, root.transform);

                Undo.RegisterCreatedObjectUndo(root, "Import Mine World");
                AssetDatabase.SaveAssets();
                EditorSceneManager.MarkSceneDirty(scene);
                Selection.activeGameObject = root;

                Debug.Log($"[MineSim] Imported world '{c.w.id}' ({c.w.name}) from {worldDir}: " +
                          $"{c.w.sites.Count} sites, {c.w.junctions.Count} junctions, {c.w.roads.Count} roads, " +
                          $"terrain {(c.terrain != null ? "yes" : "no")}, scan ground {(c.ground != null ? "yes" : "no")}. Generated assets in {c.gen}");
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorUtility.DisplayDialog("Mine Sim", "Import failed:\n" + e.Message + "\n(see Console)", "OK");
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
            return root;
        }

        // ------------------------------------------------------------------ terrain
        private static Terrain BuildTerrain(Ctx c, Transform parent)
        {
            var w = c.w;
            string hmPath = Path.Combine(c.dir, w.Asset("heightmap_raw") ?? "heightmap.raw");
            string metaPath = Path.Combine(c.dir, w.Asset("heightmap_meta") ?? "heightmap_meta.json");

            // Source sampler: normalised (u = x, v = z) in [0,1] -> height 0..1 within [minH, minH + range]
            Func<float, float, float> sample = null;
            int srcRes = 0;
            Vector2 size = w.sizeM, origin = w.origin;
            float minH = 0f, range = 1f;

            if (File.Exists(hmPath))
            {
                byte[] bytes = File.ReadAllBytes(hmPath);
                MineHeightmapMeta meta = null;
                if (File.Exists(metaPath))
                {
                    try { meta = MineHeightmapMeta.Parse(File.ReadAllText(metaPath)); }
                    catch (Exception e) { Debug.LogWarning("[MineSim] heightmap_meta.json unreadable: " + e.Message); }
                }
                int n = bytes.Length / 2;
                int res = meta != null && meta.resolution > 0 ? meta.resolution : Mathf.RoundToInt(Mathf.Sqrt(n));
                if (res * res > n)
                {
                    Debug.LogError($"[MineSim] heightmap.raw has {n} samples, expected {res}x{res}");
                    return null;
                }
                if (meta != null)
                {
                    if (meta.sizeM.x > 0 && meta.sizeM.y > 0) size = meta.sizeM;
                    origin = meta.origin;
                    minH = meta.minHeightM;
                    range = meta.heightRangeM;
                }
                else
                {
                    Debug.LogWarning("[MineSim] heightmap_meta.json missing: assuming height range 0..100 m and world.json size/origin.");
                    minH = 0f; range = 100f;
                }
                var h = new float[res * res];   // row 0 = south (min z), col 0 = west (min x)
                for (int i = 0; i < res * res; i++)
                    h[i] = (bytes[2 * i] | (bytes[2 * i + 1] << 8)) / 65535f;
                srcRes = res;
                sample = (u, v) => Bilinear(h, res, res, u * (res - 1), v * (res - 1));
            }
            else
            {
                // Fallback: navgrid heights (cell centres)
                string navPath = Path.Combine(c.dir, string.IsNullOrEmpty(w.navgrid) ? "navgrid.json" : w.navgrid);
                if (!File.Exists(navPath))
                {
                    Debug.LogWarning("[MineSim] No heightmap.raw or navgrid.json: skipping terrain.");
                    return null;
                }
                try
                {
                    var nav = MiniJson.Parse(File.ReadAllText(navPath));
                    int gw = (int)MiniJson.Num(nav, "width", 0), gh = (int)MiniJson.Num(nav, "height", 0);
                    float cell = (float)MiniJson.Num(nav, "cell_m", 2.0);
                    double[] hs = MiniJson.Nums(MiniJson.Arr(nav, "heights"));
                    if (gw < 2 || gh < 2 || hs == null || hs.Length < gw * gh) throw new FormatException("bad navgrid dims");
                    origin = MineWorldData.V2(MiniJson.Arr(nav, "origin"), w.origin);
                    size = new Vector2(gw * cell, gh * cell);
                    double lo = double.MaxValue, hi = double.MinValue;
                    for (int i = 0; i < gw * gh; i++) { lo = Math.Min(lo, hs[i]); hi = Math.Max(hi, hs[i]); }
                    minH = (float)lo; range = Mathf.Max(0.01f, (float)(hi - lo));
                    var h = new float[gw * gh];
                    for (int i = 0; i < gw * gh; i++) h[i] = (float)((hs[i] - lo) / range);
                    srcRes = Mathf.Max(gw, gh);
                    // u in [0,1] spans the full extent; samples sit at cell centres
                    sample = (u, v) => Bilinear(h, gw, gh, u * gw - 0.5f, v * gh - 0.5f);
                    Debug.Log("[MineSim] heightmap.raw missing: terrain built from navgrid heights.");
                }
                catch (Exception e)
                {
                    Debug.LogWarning("[MineSim] navgrid.json unusable for terrain: " + e.Message);
                    return null;
                }
            }

            range = Mathf.Max(range, 0.01f);
            int tRes = ValidHeightmapRes(srcRes);
            var heights = new float[tRes, tRes];   // [z, x]
            bool direct = tRes == srcRes && File.Exists(hmPath);
            for (int z = 0; z < tRes; z++)
                for (int x = 0; x < tRes; x++)
                    heights[z, x] = Mathf.Clamp01(sample(x / (float)(tRes - 1), z / (float)(tRes - 1)));
            if (!direct) Debug.Log($"[MineSim] Heightmap resampled {srcRes} -> {tRes}");

            var td = new TerrainData();
            td.heightmapResolution = tRes;
            td.size = new Vector3(size.x, range, size.y);   // set after resolution
            td.SetHeights(0, 0, heights);
            int alphaRes = Mathf.Max(size.x, size.y) > 500f ? 1024 : 512;
            td.alphamapResolution = alphaRes;
            td.baseMapResolution = 1024;

            ReplaceAsset(td, c.gen + "/TerrainData.asset");

            // layers
            var layers = new[]
            {
                MakeLayer(c, "Dirt",   new Color(0.55f, 0.43f, 0.31f), 0.10f, 6f),
                MakeLayer(c, "Rock",   new Color(0.42f, 0.38f, 0.35f), 0.18f, 10f),
                MakeLayer(c, "Gravel", new Color(0.66f, 0.62f, 0.55f), 0.14f, 3f),
                MakeLayer(c, "Bench",  new Color(0.36f, 0.29f, 0.23f), 0.12f, 6f),
            };
            td.terrainLayers = layers;
            PaintSplat(c, td, origin, size);
            EditorUtility.SetDirty(td);

            GameObject tgo = Terrain.CreateTerrainGameObject(td);
            tgo.name = "Terrain";
            tgo.transform.SetParent(parent, false);
            tgo.transform.position = new Vector3(origin.x, minH, origin.y);
            var terrain = tgo.GetComponent<Terrain>();
            terrain.heightmapPixelError = 4f;
            terrain.basemapDistance = Mathf.Max(size.x, size.y) * 2f;
            terrain.drawInstanced = true;
            var tmat = TerrainMaterial(c);
            if (tmat != null) terrain.materialTemplate = tmat;
            return terrain;
        }

        private static int ValidHeightmapRes(int src)
        {
            int r = 33;
            while (r < src && r < 4097) r = (r - 1) * 2 + 1;
            return r;
        }

        private static float Bilinear(float[] h, int w, int hgt, float fx, float fz)
        {
            fx = Mathf.Clamp(fx, 0f, w - 1);
            fz = Mathf.Clamp(fz, 0f, hgt - 1);
            int x0 = Mathf.Min((int)fx, w - 2 < 0 ? 0 : w - 2), z0 = Mathf.Min((int)fz, hgt - 2 < 0 ? 0 : hgt - 2);
            int x1 = Mathf.Min(x0 + 1, w - 1), z1 = Mathf.Min(z0 + 1, hgt - 1);
            float tx = fx - x0, tz = fz - z0;
            float a = Mathf.Lerp(h[z0 * w + x0], h[z0 * w + x1], tx);
            float b = Mathf.Lerp(h[z1 * w + x0], h[z1 * w + x1], tx);
            return Mathf.Lerp(a, b, tz);
        }

        private static TerrainLayer MakeLayer(Ctx c, string name, Color col, float noise, float tile)
        {
            const int N = 64;
            var tex = new Texture2D(N, N, TextureFormat.RGBA32, true);
            tex.name = "Tex_" + name;
            tex.wrapMode = TextureWrapMode.Repeat;
            var rng = new System.Random(name.GetHashCode());
            var px = new Color32[N * N];
            for (int i = 0; i < px.Length; i++)
            {
                float k = 1f + ((float)rng.NextDouble() * 2f - 1f) * noise;
                px[i] = new Color(col.r * k, col.g * k, col.b * k, 1f);
            }
            tex.SetPixels32(px);
            tex.Apply(true);
            ReplaceAsset(tex, c.gen + "/Tex_" + name + ".asset");

            var tl = new TerrainLayer
            {
                name = "Layer_" + name,
                diffuseTexture = tex,
                tileSize = new Vector2(tile, tile),
                tileOffset = Vector2.zero,
                smoothness = 0.05f,
                metallic = 0f,
            };
            ReplaceAsset(tl, c.gen + "/Layer_" + name + ".terrainlayer");
            return tl;
        }

        private static void PaintSplat(Ctx c, TerrainData td, Vector2 origin, Vector2 size)
        {
            int ar = td.alphamapResolution;
            int layers = td.terrainLayers.Length;
            var road = new float[ar, ar];

            // roads: distance from each texel to centreline segments
            foreach (var r in c.w.roads)
            {
                if (r.points == null || r.points.Length < 2) continue;
                float half = Mathf.Max(2f, r.widthM * 0.5f);
                for (int i = 1; i < r.points.Length; i++)
                    StampSegment(road, ar, origin, size, new Vector2(r.points[i - 1].x, r.points[i - 1].z),
                        new Vector2(r.points[i].x, r.points[i].z), half);
            }
            // gravel pads around sites
            foreach (var s in c.w.sites)
                StampSegment(road, ar, origin, size, s.pos, s.pos, 12f);

            var a = new float[ar, ar, layers];
            for (int z = 0; z < ar; z++)
            {
                float nz = z / (float)(ar - 1);
                for (int x = 0; x < ar; x++)
                {
                    float nx = x / (float)(ar - 1);
                    float steep = td.GetSteepness(nx, nz);
                    float hn = td.size.y > 0 ? td.GetInterpolatedHeight(nx, nz) / td.size.y : 0f;
                    float rock = Smooth(22f, 38f, steep);
                    float bench = (1f - rock) * (1f - Smooth(0.35f, 0.6f, hn));
                    float dirt = Mathf.Max(0f, 1f - rock - bench);
                    float rd = road[z, x];
                    float keep = 1f - rd;
                    a[z, x, 0] = dirt * keep;
                    a[z, x, 1] = rock * keep;
                    a[z, x, 2] = rd;
                    a[z, x, 3] = bench * keep;
                }
            }
            td.SetAlphamaps(0, 0, a);
        }

        private static void StampSegment(float[,] m, int ar, Vector2 origin, Vector2 size, Vector2 p0, Vector2 p1, float half)
        {
            float sx = (ar - 1) / size.x, sz = (ar - 1) / size.y;
            float minX = Mathf.Min(p0.x, p1.x) - half, maxX = Mathf.Max(p0.x, p1.x) + half;
            float minZ = Mathf.Min(p0.y, p1.y) - half, maxZ = Mathf.Max(p0.y, p1.y) + half;
            int x0 = Mathf.Clamp(Mathf.FloorToInt((minX - origin.x) * sx), 0, ar - 1);
            int x1 = Mathf.Clamp(Mathf.CeilToInt((maxX - origin.x) * sx), 0, ar - 1);
            int z0 = Mathf.Clamp(Mathf.FloorToInt((minZ - origin.y) * sz), 0, ar - 1);
            int z1 = Mathf.Clamp(Mathf.CeilToInt((maxZ - origin.y) * sz), 0, ar - 1);
            Vector2 d = p1 - p0;
            float dd = d.sqrMagnitude;
            for (int z = z0; z <= z1; z++)
                for (int x = x0; x <= x1; x++)
                {
                    var p = new Vector2(origin.x + x / sx, origin.y + z / sz);
                    float t = dd > 1e-6f ? Mathf.Clamp01(Vector2.Dot(p - p0, d) / dd) : 0f;
                    float dist = Vector2.Distance(p, p0 + d * t);
                    float wgt = 1f - Smooth(half * 0.75f, half, dist);
                    if (wgt > m[z, x]) m[z, x] = wgt;
                }
        }

        private static float Smooth(float e0, float e1, float v)
        {
            float t = Mathf.Clamp01((v - e0) / (e1 - e0));
            return t * t * (3f - 2f * t);
        }

        private static float GroundY(Ctx c, float x, float z)
        {
            if (c.terrain == null || c.td == null)
            {
                if (c.ground != null &&
                    c.ground.Raycast(new Ray(new Vector3(x, 10000f, z), Vector3.down), out RaycastHit hit, 20000f))
                    return hit.point.y;
                return 0f;
            }
            Vector3 tp = c.terrain.transform.position;
            Vector3 s = c.td.size;
            float nx = Mathf.Clamp01((x - tp.x) / s.x), nz = Mathf.Clamp01((z - tp.z) / s.z);
            return c.td.GetInterpolatedHeight(nx, nz) + tp.y;
        }

        // ------------------------------------------------------------------ sites / junctions / roads
        private static void TypeStyle(string type, out PrimitiveType prim, out Color col, out Vector3 scale)
        {
            switch (type)
            {
                case "crusher": prim = PrimitiveType.Cube; col = new Color(1f, 0.45f, 0.05f); scale = new Vector3(10, 8, 10); break;
                case "dump": prim = PrimitiveType.Cylinder; col = new Color(0.55f, 0.5f, 0.45f); scale = new Vector3(14, 2, 14); break;
                case "ore": prim = PrimitiveType.Sphere; col = new Color(0.95f, 0.75f, 0.1f); scale = new Vector3(8, 8, 8); break;
                case "parking": prim = PrimitiveType.Cube; col = new Color(0.15f, 0.45f, 1f); scale = new Vector3(16, 0.6f, 10); break;
                case "fuel": prim = PrimitiveType.Capsule; col = new Color(0.9f, 0.1f, 0.1f); scale = new Vector3(4, 4, 4); break;
                default: prim = PrimitiveType.Cube; col = Color.magenta; scale = new Vector3(6, 6, 6); break;
            }
        }

        private static void BuildSites(Ctx c, Transform root)
        {
            var parent = NewChild(root, "Sites");
            foreach (var s in c.w.sites)
            {
                Vector3 p = s.hasPos3 ? s.pos3 : new Vector3(s.pos.x, GroundY(c, s.pos.x, s.pos.y), s.pos.y);
                var go = NewChild(parent, "Site_" + s.id);
                go.position = p;
                var m = go.gameObject.AddComponent<MineSiteMarker>();
                m.siteId = s.id;
                m.siteType = s.type;
                m.siteName = s.name;
                m.hasTonnes = s.hasTonnes;
                m.tonnes = s.tonnes;
                m.groundPos = s.pos;

                TypeStyle(s.type, out var prim, out var col, out var scale);
                var mat = ColorMaterial(c, "Site_" + (string.IsNullOrEmpty(s.type) ? "unknown" : SafeName(s.type)), col);

                var marker = Primitive(prim, "Marker_" + (string.IsNullOrEmpty(s.type) ? "site" : s.type), go, mat);
                marker.localScale = scale;
                marker.localPosition = new Vector3(0, scale.y * (prim == PrimitiveType.Cylinder || prim == PrimitiveType.Capsule ? 1f : 0.5f), 0);

                var pole = Primitive(PrimitiveType.Cylinder, "Pole", go, mat);
                pole.localScale = new Vector3(0.6f, 12f, 0.6f);   // cylinder is 2 units tall -> 24 m
                pole.localPosition = new Vector3(0, 12f, 0);

                CreateLabel(go, s.name, new Vector3(0, 27f, 0), Color.white, SiteLabelHeight, "site");
            }
        }

        private static void BuildJunctions(Ctx c, Transform root)
        {
            var parent = NewChild(root, "Junctions");
            foreach (var j in c.w.junctions)
            {
                Vector3 p = j.posIs3D ? j.pos : new Vector3(j.pos.x, GroundY(c, j.pos.x, j.pos.z), j.pos.z);
                var go = NewChild(parent, "Junction_" + j.id);
                go.position = p;
                var mj = go.gameObject.AddComponent<MineJunction>();
                mj.junctionId = j.id;
                mj.junctionName = j.name;
                CreateLabel(go, j.name, new Vector3(0, 6f, 0), new Color(0.75f, 0.9f, 1f), JunctionLabelHeight, "junction");
            }
        }

        private static void BuildRoads(Ctx c, Transform root)
        {
            var parent = NewChild(root, "Roads");
            foreach (var r in c.w.roads)
            {
                var go = NewChild(parent, "Road_" + r.id);   // identity transform: local == world
                var pts = new Vector3[r.points.Length];
                for (int i = 0; i < pts.Length; i++)
                {
                    var q = r.points[i];
                    if (float.IsNaN(q.y)) q.y = GroundY(c, q.x, q.z);
                    pts[i] = q;
                }

                var mr = go.gameObject.AddComponent<MineRoad>();
                mr.roadId = r.id;
                mr.roadName = r.name;
                mr.roadType = r.type;
                mr.from = r.from;
                mr.to = r.to;
                mr.widthM = r.widthM;
                mr.gradePct = r.gradePct;
                mr.oneWay = r.oneWay;
                mr.points = pts;

                // ribbon line, flat on the ground
                var lineGo = NewChild(go, "Line");
                lineGo.localRotation = Quaternion.Euler(90f, 0f, 0f);
                var lr = lineGo.gameObject.AddComponent<LineRenderer>();
                lr.useWorldSpace = true;
                lr.alignment = LineAlignment.TransformZ;
                var lifted = new Vector3[pts.Length];
                for (int i = 0; i < pts.Length; i++) lifted[i] = pts[i] + Vector3.up * RoadLineLift;
                lr.positionCount = lifted.Length;
                lr.SetPositions(lifted);
                lr.widthMultiplier = Mathf.Max(1f, r.widthM * RoadLineWidthScale);
                Color rc = RoadColor(r.type);
                lr.startColor = rc;
                lr.endColor = rc;
                lr.numCornerVertices = 2;
                lr.numCapVertices = 2;
                lr.shadowCastingMode = ShadowCastingMode.Off;
                lr.receiveShadows = false;
                if (c.lineMat != null) lr.sharedMaterial = c.lineMat;
                mr.line = lr;

                for (int i = 0; i < pts.Length; i++)
                {
                    var wp = NewChild(go, "WP_" + i.ToString("D3", CultureInfo.InvariantCulture));
                    wp.position = pts[i];
                }

                if (pts.Length > 0)
                    CreateLabel(go, r.name, mr.Midpoint - go.position + new Vector3(0, 5f, 0), new Color(1f, 0.92f, 0.5f), RoadLabelHeight, "road");

                if (pts.Length >= 2 && MineImporterHooks.AddRoadSpline != null)
                {
                    try { MineImporterHooks.AddRoadSpline(go.gameObject, pts); }
                    catch (Exception e) { Debug.LogWarning("[MineSim] spline hook failed for road " + r.id + ": " + e.Message); }
                }
            }
        }

        private static Color RoadColor(string type)
        {
            switch (type)
            {
                case "ramp": return new Color(1f, 0.55f, 0.1f, 0.9f);
                case "spur": return new Color(0.6f, 0.85f, 1f, 0.9f);
                default: return new Color(1f, 0.9f, 0.2f, 0.9f);
            }
        }

        // ------------------------------------------------------------------ design spec
        private static void BuildDesignSpec(Ctx c, GameObject root)
        {
            if (c.w.design == null) return;
            var spec = root.AddComponent<MineDesignSpec>();
            Flatten(c.w.design, "", spec.entries);
            spec.benchHeightM = FirstFloat(spec, "bench_height_m", "bench_height");
            spec.roadWidthM = FirstFloat(spec, "road_width_m", "road_width");
            spec.bermHeightM = FirstFloat(spec, "berm_height_m", "berm_height");
            spec.designTruck = spec.Get("design_truck") ?? spec.Get("design_truck.name") ?? spec.Get("design_truck.model") ?? "";
        }

        private static float FirstFloat(MineDesignSpec s, params string[] keys)
        {
            foreach (var k in keys)
            {
                float v = s.GetFloat(k);
                if (!float.IsNaN(v)) return v;
            }
            return float.NaN;
        }

        private static void Flatten(object o, string prefix, List<MineDesignSpec.Entry> outList)
        {
            if (o is Dictionary<string, object> d)
            {
                foreach (var kv in d) Flatten(kv.Value, prefix.Length == 0 ? kv.Key : prefix + "." + kv.Key, outList);
            }
            else if (o is List<object> l)
            {
                bool allScalar = true;
                foreach (var e in l) if (e is Dictionary<string, object> || e is List<object>) { allScalar = false; break; }
                if (allScalar)
                {
                    var parts = new List<string>();
                    foreach (var e in l) parts.Add(Scalar(e));
                    outList.Add(new MineDesignSpec.Entry { key = prefix, value = "[" + string.Join(", ", parts) + "]" });
                }
                else
                    for (int i = 0; i < l.Count; i++) Flatten(l[i], prefix + "[" + i + "]", outList);
            }
            else outList.Add(new MineDesignSpec.Entry { key = prefix, value = Scalar(o) });
        }

        private static string Scalar(object o)
        {
            if (o == null) return "null";
            if (o is double dv) return dv.ToString(CultureInfo.InvariantCulture);
            if (o is bool b) return b ? "true" : "false";
            return o.ToString();
        }

        // ------------------------------------------------------------------ optional: collider + splat
        private static void BuildCollider(Ctx c, Transform root)
        {
            string file = c.w.Asset("collider");
            if (file == null) return;
            string src = Path.Combine(c.dir, file);
            if (!File.Exists(src))
            {
                Debug.LogWarning("[MineSim] assets.collider set but file missing: " + src);
                return;
            }
#if MINESIM_GLTFAST
            string dst = c.gen + "/" + Path.GetFileName(file);
            File.Copy(src, Path.GetFullPath(dst), true);
            AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceUpdate);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(dst);
            if (prefab == null)
            {
                Debug.LogWarning("[MineSim] glTFast did not produce a GameObject for " + dst);
                return;
            }
            var holder = NewChild(root, "Collider");
            ApplyMarbleTransform(c, holder);
            var inst = Object.Instantiate(prefab, holder, false);
            inst.name = "collider_mesh";
            int count = 0;
            foreach (var mf in inst.GetComponentsInChildren<MeshFilter>(true))
            {
                if (mf.sharedMesh == null) continue;
                var mc = mf.gameObject.AddComponent<MeshCollider>();
                mc.sharedMesh = mf.sharedMesh;
                var rend = mf.GetComponent<MeshRenderer>();
                if (rend != null) rend.enabled = false;
                count++;
            }
            var note = holder.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = dst;
            note.note = "Invisible MeshCollider(s) from the Marble collider.glb (" + count + " meshes).\n" +
                        "Holder transform = splat_transform: localScale (-scale, scale, scale), rotation rotation_euler_deg, " +
                        "position y = ground_offset. The -X scale undoes glTFast's right->left-handed X flip so the mesh lands in " +
                        "contract coordinates. If it looks mirrored against the terrain, set localScale.x positive.";
#else
            var holder = NewChild(root, "Collider (needs glTFast)");
            var note = holder.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = src;
            note.note = "world.json has assets.collider but glTFast (com.unity.cloud.gltfast) is not installed.\n" +
                        "Install it via Package Manager and Reimport, or import collider.glb manually and apply the transform from splat_transform.";
            Debug.Log("[MineSim] collider.glb present; install glTFast (com.unity.cloud.gltfast) to import it.");
#endif
        }

        // ------------------------------------------------------------------ scan worlds (source "scan")
        // Transform composition (see README "Scanned worlds"):
        //   contract:  p_world = M * p_gltf, M = scan_transform.matrix = [s*R | t]  (glTF axes == contract axes == Unity axes)
        //   glTFast:   u = F * p_gltf with F = diag(-1, 1, 1) (it negates X on import and re-winds triangles)
        //   holder H:  p_world = H * u  =>  H = M * F^-1 = M * F = [s*R*F | t]
        //   => localPosition = t, rotation/scale decomposed from s*R*F; for R = I that is rotation identity, scale (-s, s, s).
        //   t is NOT mirrored: it is applied after F has been undone. The negative-determinant holder makes Unity invert
        //   culling, which cancels glTFast's re-winding, so faces stay front-facing.
        // collider.glb is already in world frame (contract), so it does NOT get scan_transform. It is read verbatim by
        // MineGlbMeshReader (no glTFast, no flip) into a Mesh asset on an identity transform.
        private static void BuildScan(Ctx c, Transform root)
        {
            var holder = NewChild(root, "Scan");

            string colFile = c.w.Asset("collider");
            if (colFile != null) c.ground = BuildScanCollider(c, holder, colFile);
            else Debug.LogWarning("[MineSim] scan world without assets.collider: depth camera/lidar raycasts will hit nothing");

            string visFile = c.w.Asset("visual");
            if (visFile != null) BuildScanVisual(c, holder, visFile);
        }

        /// <summary>Copies a world file into Generated/&lt;id&gt;/ (skips when unchanged). Returns the asset path or null.</summary>
        private static string CopyIntoProject(Ctx c, string file)
        {
            string src = Path.Combine(c.dir, file);
            if (!File.Exists(src))
            {
                Debug.LogWarning("[MineSim] asset listed in world.json but missing: " + src);
                return null;
            }
            string dst = c.gen + "/" + Path.GetFileName(file);
            string full = Path.GetFullPath(dst);
            var si = new FileInfo(src);
            var di = new FileInfo(full);
            bool changed = !di.Exists || di.Length != si.Length || di.LastWriteTimeUtc < si.LastWriteTimeUtc;
            if (changed)
            {
                File.Copy(src, full, true);
                AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceUpdate);
            }
            else AssetDatabase.ImportAsset(dst);
            return dst;
        }

        private static Collider BuildScanCollider(Ctx c, Transform holder, string file)
        {
            string src = Path.Combine(c.dir, file);
            if (!File.Exists(src))
            {
                Debug.LogError("[MineSim] scan collider missing: " + src);
                return null;
            }
            CopyIntoProject(c, file);   // keep the .glb with the project, as requested; the Mesh asset below is what is used

            if (!MineGlbMeshReader.TryRead(src, out var verts, out var tris, out string err))
            {
                Debug.LogError("[MineSim] could not read " + file + ": " + err);
                return null;
            }
            var mesh = new Mesh { name = "ScanGround" };
            mesh.indexFormat = verts.Count > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.SetVertices(verts);
            mesh.SetTriangles(tris, 0, true);
            mesh.RecalculateNormals();
            string meshPath = c.gen + "/ScanGround_collider.asset";
            ReplaceAsset(mesh, meshPath);

            var go = NewChild(holder, "Collider");   // identity: collider.glb is already in world frame
            int layer = LayerMask.NameToLayer(GroundLayerName);
            go.gameObject.layer = layer >= 0 ? layer : 0;
            var mc = go.gameObject.AddComponent<MeshCollider>();
            mc.sharedMesh = mesh;
            Physics.SyncTransforms();
            var note = go.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = meshPath;
            note.note = "Invisible ground MeshCollider from " + file + " (" + verts.Count + " verts, " + (tris.Count / 3) + " tris), " +
                        "read verbatim (already in world frame), identity transform, layer " +
                        (layer >= 0 ? GroundLayerName : "Default (add a '" + GroundLayerName + "' layer and reimport to use it)") + ".";
            Debug.Log("[MineSim] scan ground collider: " + verts.Count + " verts, " + (tris.Count / 3) + " tris, bounds " + mesh.bounds);
            return mc;
        }

        private static void BuildScanVisual(Ctx c, Transform holder, string file)
        {
            var st = c.w.scanTransform;
            if (!st.present) Debug.LogWarning("[MineSim] scan world has no scan_transform: visual placed with identity");
#if MINESIM_GLTFAST
            EditorUtility.DisplayProgressBar("Mine Sim", "Importing " + file + " with glTFast (large file)", 0.4f);
            string dst = CopyIntoProject(c, file);
            if (dst == null) return;
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(dst);
            if (prefab == null)
            {
                Debug.LogError("[MineSim] glTFast did not produce a GameObject for " + dst);
                return;
            }
            var vis = NewChild(holder, "Visual");
            ApplyScanTransform(st, vis);
            var inst = PrefabUtility.InstantiatePrefab(prefab, vis) as GameObject;
            if (inst == null) inst = Object.Instantiate(prefab, vis, false);
            inst.name = "visual_mesh";
            inst.transform.localPosition = Vector3.zero;
            inst.transform.localRotation = Quaternion.identity;
            inst.transform.localScale = Vector3.one;
            foreach (var col in inst.GetComponentsInChildren<Collider>(true)) col.enabled = false;   // the ground collider is the physics
            var note = vis.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = dst;
            note.note = "Scan visual (glTFast). Holder = scan_transform composed with glTFast's X flip: " +
                        "localPosition = t, localRotation/localScale from s*R*diag(-1,1,1). See README 'Scanned worlds'.";
            if (c.ground != null)
            {
                var vb = new Bounds();
                bool any = false;
                foreach (var r in inst.GetComponentsInChildren<Renderer>(true))
                {
                    if (!any) { vb = r.bounds; any = true; }
                    else vb.Encapsulate(r.bounds);
                }
                if (any)
                    Debug.Log("[MineSim] scan visual bounds " + vb + " vs collider bounds " + c.ground.bounds +
                              " (they should overlap; if X is mirrored, negate Visual localScale.x)");
            }
#else
            var holderNote = NewChild(holder, "Visual (needs glTFast)");
            ApplyScanTransform(st, holderNote);
            var note = holderNote.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = Path.Combine(c.dir, file);
            note.note = "Scan world visual.glb needs glTFast (com.unity.cloud.gltfast). Install it and reimport. " +
                        "This object already carries the scan_transform (glTFast X flip included).";
            Debug.LogError("[MineSim] scan world '" + c.w.id + "' needs glTFast (com.unity.cloud.gltfast) to import " + file +
                           ". The ground collider was still built.");
#endif
        }

        /// <summary>Sets t so that a glTFast-imported child (X negated) lands at p_world = M * p_gltf.</summary>
        private static void ApplyScanTransform(MineScanTransform st, Transform t)
        {
            Matrix4x4 m = st.present ? st.matrix : Matrix4x4.identity;
            Matrix4x4 h = m * Matrix4x4.Scale(new Vector3(-1f, 1f, 1f));   // H = M * F
            Vector3 c0 = h.GetColumn(0), c1 = h.GetColumn(1), c2 = h.GetColumn(2);
            float sx = c0.magnitude, sy = c1.magnitude, sz = c2.magnitude;
            if (h.determinant < 0f) sx = -sx;
            if (Mathf.Abs(sx) < 1e-8f || sy < 1e-8f || sz < 1e-8f)
            {
                Debug.LogWarning("[MineSim] degenerate scan_transform matrix");
                return;
            }
            Vector3 fwd = c2 / sz, up = c1 / sy;
            t.localPosition = h.GetColumn(3);
            t.localRotation = Quaternion.LookRotation(fwd, up);
            t.localScale = new Vector3(sx, sy, sz);
        }

        private static void ApplyMarbleTransform(Ctx c, Transform t)
        {
            var st = c.w.splatTransform;
            float s = st.scale <= 0f ? 1f : st.scale;
            t.localPosition = new Vector3(0f, st.groundOffset, 0f);
            t.localRotation = Quaternion.Euler(st.rotationEulerDeg);
            t.localScale = new Vector3(-s, s, s);
        }

        private static void BuildSplatNote(Ctx c, Transform root)
        {
            string file = c.w.Asset("splat");
            if (file == null) return;
            string lowres = c.w.Asset("splat_lowres");
            var go = NewChild(root, "Splat (import manually)");
            var st = c.w.splatTransform;
            float s = st.scale <= 0f ? 1f : st.scale;
            go.localPosition = new Vector3(0f, st.groundOffset, 0f);
            go.localRotation = Quaternion.Euler(st.rotationEulerDeg);
            go.localScale = new Vector3(s, s, s);
            var note = go.gameObject.AddComponent<MineAssetNote>();
            note.assetFile = file;
            note.assetPath = Path.Combine(c.dir, file);
            note.note =
                "Gaussian splat: " + file + (lowres != null ? " (low-res: " + lowres + ")" : "") + "\n" +
                "Render it with aras-p/UnityGaussianSplatting (convert .spz/.ply into a GaussianSplatAsset) or the " +
                "DakkuaDev World Labs Unity tool, then parent the renderer under THIS object.\n" +
                "This object already carries splat_transform: position y = ground_offset (" + st.groundOffset.ToString(CultureInfo.InvariantCulture) +
                "), rotation " + st.rotationEulerDeg + " (frame '" + st.frame + "': Marble assets are OpenCV, +y down; 180 deg about X negates y and z), " +
                "uniform scale " + s.ToString(CultureInfo.InvariantCulture) + ".\n" +
                "Contract order: p = raw*scale, p.y -= ground_offset (raw frame), then rotate. Check alignment against the terrain/collider; " +
                "if the splat importer already converts handedness it may appear mirrored -> flip localScale.x.";
        }

        // ------------------------------------------------------------------ labels
        private static Font builtinFont;

        private static void CreateLabel(Transform parent, string text, Vector3 localPos, Color color, float height, string kind)
        {
            var go = new GameObject("Label");
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPos;
            var ml = go.AddComponent<MineLabel>();
            ml.kind = kind;
            ml.text = text;

            bool made = false;
            if (MineImporterHooks.CreateTextLabel != null)
            {
                try { made = MineImporterHooks.CreateTextLabel(go, text, color, height); }
                catch (Exception e) { Debug.LogWarning("[MineSim] text label hook failed: " + e.Message); made = false; }
            }
            if (!made)
            {
                if (builtinFont == null) builtinFont = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                var tm = go.AddComponent<TextMesh>();
                tm.text = text;
                tm.fontSize = 64;
                tm.characterSize = height * 10f / 64f;   // ~characterSize*fontSize/10 world units
                tm.anchor = TextAnchor.MiddleCenter;
                tm.alignment = TextAlignment.Center;
                tm.color = color;
                var r = go.GetComponent<MeshRenderer>();
                if (r == null) r = go.AddComponent<MeshRenderer>();
                if (builtinFont != null)
                {
                    tm.font = builtinFont;
                    r.sharedMaterial = builtinFont.material;
                }
                r.shadowCastingMode = ShadowCastingMode.Off;
                r.receiveShadows = false;
            }
            go.AddComponent<MineBillboard>();
        }


        // ------------------------------------------------------------------ materials
        private static bool IsSrp => GraphicsSettings.currentRenderPipeline != null;

        private static Shader LitShader()
        {
            Shader s = null;
            if (IsSrp)
            {
                s = Shader.Find("Universal Render Pipeline/Lit");
                if (s == null) s = Shader.Find("HDRP/Lit");
            }
            if (s == null) s = Shader.Find("Standard");
            return s;
        }

        private static Material ColorMaterial(Ctx c, string name, Color col)
        {
            string path = c.gen + "/Materials/" + name + ".mat";
            var sh = LitShader();
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                if (sh == null) return null;
                m = new Material(sh);
                AssetDatabase.CreateAsset(m, path);
            }
            else if (sh != null && m.shader != sh) m.shader = sh;
            if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", col);
            if (m.HasProperty("_Color")) m.SetColor("_Color", col);
            EditorUtility.SetDirty(m);
            return m;
        }

        private static Material LineMaterial(Ctx c)
        {
            string path = c.gen + "/Materials/RoadLine.mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m != null) return m;
            // Sprites/Default uses vertex colour (LineRenderer start/end colour), is double-sided, and draws in built-in and URP.
            var sh = Shader.Find("Sprites/Default");
            if (sh == null && IsSrp) sh = Shader.Find("Universal Render Pipeline/Unlit");
            if (sh == null) return null;
            m = new Material(sh);
            AssetDatabase.CreateAsset(m, path);
            return m;
        }

        private static Material TerrainMaterial(Ctx c)
        {
            if (!IsSrp) return null;   // built-in uses its default terrain shader
            var sh = Shader.Find("Universal Render Pipeline/Terrain/Lit");
            if (sh == null) sh = Shader.Find("HDRP/TerrainLit");
            if (sh == null) return null;
            string path = c.gen + "/Materials/Terrain.mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(sh);
                AssetDatabase.CreateAsset(m, path);
            }
            return m;
        }

        // ------------------------------------------------------------------ helpers
        private static Transform NewChild(Transform parent, string name)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            return go.transform;
        }

        private static Transform Primitive(PrimitiveType type, string name, Transform parent, Material mat)
        {
            var go = GameObject.CreatePrimitive(type);
            go.name = name;
            var col = go.GetComponent<Collider>();
            if (col != null) Object.DestroyImmediate(col);   // visual only; don't block vehicles
            go.transform.SetParent(parent, false);
            var r = go.GetComponent<MeshRenderer>();
            if (r != null && mat != null) r.sharedMaterial = mat;
            return go.transform;
        }

        private static void ReplaceAsset(Object obj, string path)
        {
            if (AssetDatabase.LoadAssetAtPath<Object>(path) != null) AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(obj, path);
        }

        private static void CopyWorldJson(Ctx c, string worldJson)
        {
            try
            {
                string dst = c.gen + "/world.json";
                File.Copy(worldJson, Path.GetFullPath(dst), true);
                AssetDatabase.ImportAsset(dst);
            }
            catch (Exception e)
            {
                Debug.LogWarning("[MineSim] could not copy world.json: " + e.Message);
            }
        }

        private static string EnsureFolder(string path)
        {
            var parts = path.Split('/');
            string cur = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
            return path;
        }

        private static string SafeName(string s)
        {
            if (string.IsNullOrEmpty(s)) return "world";
            foreach (char ch in Path.GetInvalidFileNameChars()) s = s.Replace(ch, '_');
            return s.Replace('/', '_').Replace('\\', '_');
        }
    }
}
