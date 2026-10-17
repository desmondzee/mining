// Shared world.json data model (JsonUtility-compatible) + site marker / truck placeholder builders.
// Used by both the editor importer and the runtime client.
//
// COORDINATES: contract positions are Y-up, X east, Z north (ground positions are [x, z]).
// We map contract (x, y, z) -> Unity (x, y, z) 1:1, with no axis flip. Seen top-down in Unity (+X right,
// +Z up the screen) that gives the same map as the web viewer: east right, north up. The terrain, sites,
// splat and live trucks all go through this same mapping, so everything lines up with each other.
// Headings: heading_deg is treated as a compass bearing (0 = +Z/north, 90 = +X/east), which matches
// Unity's Quaternion.Euler(0, heading, 0) directly.
using System;
using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    [Serializable]
    public class WorldAssets
    {
        public string splat;
        public string collider;
        public string terrain_mesh;
        public string pano;
        public string concept;
        public string heightmap_raw;
    }

    [Serializable]
    public class SplatTransform
    {
        public float scale = 1f;
        public float ground_offset = 0f;
        public float[] rotation_euler_deg = { 0f, 0f, 180f };
    }

    [Serializable]
    public class WorldSite
    {
        public string id;
        public string type;
        public string name;
        public float[] pos;     // [x, z]
        public float tonnes;
    }

    [Serializable]
    public class WorldTruck
    {
        public string id;
        public string callsign;
        public float[] spawn;   // [x, z]
        public float heading_deg;
        public float capacity_t;
    }

    [Serializable]
    public class WorldJson
    {
        public string id;
        public string name;
        public string source;
        public float[] size_m;  // [x extent, z extent]
        public float[] origin;  // [min x, min z]
        public WorldAssets assets = new WorldAssets();
        public SplatTransform splat_transform = new SplatTransform();
        public WorldSite[] sites;
        public WorldTruck[] trucks;
        public string navgrid;
    }

    [Serializable]
    public class NavGridJson
    {
        public int width;
        public int height;
        public float cell_m = 2f;
        public float[] origin;
        public float[] heights; // row-major, index = row * width + col, row = z index
        public float[] cost;

        /// <summary>Bilinear-free nearest-cell height at world (x, z). Returns 0 if out of range.</summary>
        public float SampleHeight(float x, float z)
        {
            if (heights == null || heights.Length == 0 || width <= 0 || height <= 0) return 0f;
            float ox = origin != null && origin.Length > 0 ? origin[0] : 0f;
            float oz = origin != null && origin.Length > 1 ? origin[1] : 0f;
            int col = Mathf.Clamp(Mathf.FloorToInt((x - ox) / cell_m), 0, width - 1);
            int row = Mathf.Clamp(Mathf.FloorToInt((z - oz) / cell_m), 0, height - 1);
            int idx = row * width + col;
            return idx < heights.Length ? heights[idx] : 0f;
        }
    }

    public static class MineMaterials
    {
        static Material _lit, _line;
        static Font _font;

        /// <summary>Built-in legacy font (Unity 2022.2+/Unity 6 name is LegacyRuntime.ttf; older is Arial.ttf).</summary>
        public static Font Font()
        {
            if (_font != null) return _font;
            try { _font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); } catch { _font = null; }
            if (_font == null)
            {
                try { _font = Resources.GetBuiltinResource<Font>("Arial.ttf"); } catch { _font = null; }
            }
            return _font;
        }

        /// <summary>Adds a camera-facing TextMesh with a valid font + material.</summary>
        public static TextMesh AddTextMesh(GameObject go, string text, Color c, float charSize)
        {
            var tm = go.AddComponent<TextMesh>();
            var f = Font();
            if (f != null)
            {
                tm.font = f;
                var mr = go.GetComponent<MeshRenderer>();
                if (mr != null) mr.sharedMaterial = f.material;
            }
            tm.text = text;
            tm.anchor = TextAnchor.LowerCenter;
            tm.alignment = TextAlignment.Center;
            tm.characterSize = charSize;
            tm.fontSize = 48;
            tm.color = c;
            go.AddComponent<MineBillboard>();
            return tm;
        }

        public static Shader FindFirst(params string[] names)
        {
            foreach (var n in names)
            {
                var s = Shader.Find(n);
                if (s != null) return s;
            }
            return null;
        }

        /// <summary>A lit material for the active pipeline (URP, HDRP or Built-in).</summary>
        public static Material Lit()
        {
            if (_lit == null)
            {
                var sh = FindFirst("Universal Render Pipeline/Lit", "HDRP/Lit", "Standard", "Diffuse");
                _lit = new Material(sh);
            }
            return _lit;
        }

        /// <summary>Vertex-coloured unlit material for LineRenderers.</summary>
        public static Material Line()
        {
            if (_line == null)
            {
                var sh = FindFirst("Sprites/Default", "Universal Render Pipeline/Particles/Unlit", "Unlit/Color");
                _line = new Material(sh);
            }
            return _line;
        }
    }

    public static class MineTruckFactory
    {
        static readonly Color[] Palette =
        {
            new Color(1.00f, 0.80f, 0.10f), new Color(0.30f, 0.80f, 1.00f), new Color(1.00f, 0.40f, 0.30f),
            new Color(0.50f, 1.00f, 0.40f), new Color(0.85f, 0.50f, 1.00f), new Color(1.00f, 0.60f, 0.85f),
        };

        public static Color ColorForIndex(int i) => Palette[((i % Palette.Length) + Palette.Length) % Palette.Length];

        /// <summary>Procedural haul truck (~ CAT 793 proportions, 12 x 7 x 6 m), facing +Z.</summary>
        public static GameObject CreateBoxTruck(string name, Color color)
        {
            var root = new GameObject(name);
            Part(root, PrimitiveType.Cube, "Chassis", new Vector3(0f, 1.6f, 0f), new Vector3(6.5f, 1.2f, 11f), new Color(0.2f, 0.2f, 0.2f));
            Part(root, PrimitiveType.Cube, "Bed", new Vector3(0f, 3.6f, -1.2f), new Vector3(6.8f, 2.8f, 8f), color);
            Part(root, PrimitiveType.Cube, "Cab", new Vector3(-1.8f, 3.4f, 4.2f), new Vector3(2.4f, 2.2f, 2.2f), color * 0.85f);
            Part(root, PrimitiveType.Cube, "Canopy", new Vector3(0f, 5.2f, 3.6f), new Vector3(6.8f, 0.3f, 3f), color);
            float[] zs = { 3.8f, -3.2f };
            foreach (float z in zs)
                foreach (float x in new[] { -3.2f, 3.2f })
                {
                    var w = Part(root, PrimitiveType.Cylinder, "Wheel", new Vector3(x, 1.6f, z), new Vector3(3.2f, 0.6f, 3.2f), new Color(0.08f, 0.08f, 0.08f));
                    w.transform.localRotation = Quaternion.Euler(0f, 0f, 90f);
                }
            return root;
        }

        static GameObject Part(GameObject root, PrimitiveType t, string name, Vector3 pos, Vector3 scale, Color c)
        {
            var g = GameObject.CreatePrimitive(t);
            g.name = name;
            g.transform.SetParent(root.transform, false);
            g.transform.localPosition = pos;
            g.transform.localScale = scale;
            MineSiteMarker.StripCollider(g);
            MineSiteMarker.Tint(g, c);
            return g;
        }

        public static TextMesh AddLabel(GameObject truck, string text, Color c)
        {
            var labelGo = new GameObject("Label");
            labelGo.transform.SetParent(truck.transform, false);
            labelGo.transform.localPosition = new Vector3(0f, 9f, 0f);
            return MineMaterials.AddTextMesh(labelGo, text, c, 0.4f);
        }
    }
}
