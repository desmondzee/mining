// Plain C# model of data/worlds/<id>/world.json (+ heightmap_meta.json), see CONTRACT.md.
// Contract (x, y, z) -> Unity Vector3(x, y, z) directly. Ground [x, z] -> Vector2(x, z).
using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace MineSim
{
    [Serializable]
    public class MineSiteInfo
    {
        public string id, type, name;
        public Vector2 pos;          // [x, z]
        public bool hasPos3;
        public Vector3 pos3;         // [x, y, z] if provided
        public bool hasTonnes;
        public float tonnes;
    }

    [Serializable]
    public class MineJunctionInfo
    {
        public string id, name;
        public Vector3 pos;          // [x, y, z]
        public bool posIs3D;         // false if only [x, z] was given (y must be sampled)
    }

    [Serializable]
    public class MineRoadInfo
    {
        public string id, name, type, from, to;
        public float widthM = 10f, gradePct;
        public bool oneWay;
        public Vector3[] points = new Vector3[0];
    }

    [Serializable]
    public class MineTruckInfo
    {
        public string id, callsign;
        public Vector2 spawn;
        public float headingDeg, capacityT;
    }

    [Serializable]
    public class MineSplatTransform
    {
        public float scale = 1f, groundOffset;
        public Vector3 rotationEulerDeg;
        public string frame = "";
    }

    [Serializable]
    public class MineHeightmapMeta
    {
        public int resolution;
        public Vector2 sizeM;
        public Vector2 origin;
        public float minHeightM, maxHeightM, heightRangeM;

        public static MineHeightmapMeta Parse(string json)
        {
            var root = MiniJson.Parse(json);
            var m = new MineHeightmapMeta
            {
                resolution = (int)MiniJson.Num(root, "resolution", 0),
                minHeightM = (float)MiniJson.Num(root, "min_height_m", 0),
                maxHeightM = (float)MiniJson.Num(root, "max_height_m", 0),
            };
            m.heightRangeM = (float)MiniJson.Num(root, "height_range_m", m.maxHeightM - m.minHeightM);
            m.sizeM = MineWorldData.V2(MiniJson.Arr(root, "size_m"), Vector2.zero);
            m.origin = MineWorldData.V2(MiniJson.Arr(root, "origin"), Vector2.zero);
            return m;
        }
    }

    [Serializable]
    public class MineWorldData
    {
        public string id = "default", name = "", source = "procedural";
        public Vector2 sizeM = new Vector2(400, 400);
        public Vector2 origin = new Vector2(-200, -200);
        public Dictionary<string, string> assets = new Dictionary<string, string>();
        public MineSplatTransform splatTransform = new MineSplatTransform();
        public List<MineSiteInfo> sites = new List<MineSiteInfo>();
        public List<MineJunctionInfo> junctions = new List<MineJunctionInfo>();
        public List<MineRoadInfo> roads = new List<MineRoadInfo>();
        public List<MineTruckInfo> trucks = new List<MineTruckInfo>();
        public string navgrid = "navgrid.json";
        /// <summary>Raw optional "design" block (MiniJson object) or null.</summary>
        public Dictionary<string, object> design;

        /// <summary>Asset file name from world.json "assets" (null if missing/null).</summary>
        public string Asset(string key)
        {
            return assets.TryGetValue(key, out var v) && !string.IsNullOrEmpty(v) ? v : null;
        }

        public static MineWorldData Load(string worldDir)
        {
            string path = Path.Combine(worldDir, "world.json");
            return Parse(File.ReadAllText(path));
        }

        public static MineWorldData Parse(string json)
        {
            var root = MiniJson.Parse(json) as Dictionary<string, object>;
            if (root == null) throw new FormatException("world.json root is not an object");
            var w = new MineWorldData
            {
                id = MiniJson.Str(root, "id", "default"),
                name = MiniJson.Str(root, "name", ""),
                source = MiniJson.Str(root, "source", "procedural"),
                navgrid = MiniJson.Str(root, "navgrid", "navgrid.json"),
            };
            w.sizeM = V2(MiniJson.Arr(root, "size_m"), w.sizeM);
            w.origin = V2(MiniJson.Arr(root, "origin"), w.origin);

            w.design = MiniJson.Obj(root, "design");

            var assets = MiniJson.Obj(root, "assets");
            if (assets != null)
                foreach (var kv in assets)
                    if (kv.Value is string s) w.assets[kv.Key] = s;

            var st = MiniJson.Obj(root, "splat_transform");
            if (st != null)
            {
                w.splatTransform.scale = (float)MiniJson.Num(st, "scale", 1);
                w.splatTransform.groundOffset = (float)MiniJson.Num(st, "ground_offset", 0);
                w.splatTransform.rotationEulerDeg = V3(MiniJson.Arr(st, "rotation_euler_deg"), Vector3.zero);
                w.splatTransform.frame = MiniJson.Str(st, "frame", "");
            }

            var sites = MiniJson.Arr(root, "sites");
            if (sites != null)
                foreach (var o in sites)
                {
                    var s = new MineSiteInfo
                    {
                        id = MiniJson.Str(o, "id", "site"),
                        type = MiniJson.Str(o, "type", ""),
                        pos = V2(MiniJson.Arr(o, "pos"), Vector2.zero),
                    };
                    s.name = MiniJson.Str(o, "name", s.id);
                    var p3 = MiniJson.Arr(o, "pos3");
                    if (p3 != null && p3.Count >= 3) { s.hasPos3 = true; s.pos3 = V3(p3, Vector3.zero); }
                    if (MiniJson.Has(o, "tonnes")) { s.hasTonnes = true; s.tonnes = (float)MiniJson.Num(o, "tonnes", 0); }
                    w.sites.Add(s);
                }

            var juncs = MiniJson.Arr(root, "junctions");
            if (juncs != null)
                foreach (var o in juncs)
                {
                    var j = new MineJunctionInfo { id = MiniJson.Str(o, "id", "junction") };
                    j.name = MiniJson.Str(o, "name", j.id);
                    var p = MiniJson.Arr(o, "pos");
                    if (p != null && p.Count >= 3) { j.pos = V3(p, Vector3.zero); j.posIs3D = true; }
                    else { var g = V2(p, Vector2.zero); j.pos = new Vector3(g.x, 0, g.y); j.posIs3D = false; }
                    w.junctions.Add(j);
                }

            var roads = MiniJson.Arr(root, "roads");
            if (roads != null)
                foreach (var o in roads)
                {
                    var r = new MineRoadInfo
                    {
                        id = MiniJson.Str(o, "id", "road"),
                        type = MiniJson.Str(o, "type", "haul"),
                        from = MiniJson.Str(o, "from", ""),
                        to = MiniJson.Str(o, "to", ""),
                        widthM = (float)MiniJson.Num(o, "width_m", 10),
                        gradePct = (float)MiniJson.Num(o, "grade_pct", 0),
                        oneWay = MiniJson.Bool(o, "one_way", false),
                    };
                    r.name = MiniJson.Str(o, "name", r.id);
                    var pts = MiniJson.Arr(o, "points");
                    var list = new List<Vector3>();
                    if (pts != null)
                        foreach (var po in pts)
                        {
                            var a = po as List<object>;
                            if (a == null) continue;
                            if (a.Count >= 3) list.Add(V3(a, Vector3.zero));
                            else if (a.Count == 2) { var g = V2(a, Vector2.zero); list.Add(new Vector3(g.x, float.NaN, g.y)); }
                        }
                    r.points = list.ToArray();
                    w.roads.Add(r);
                }

            var trucks = MiniJson.Arr(root, "trucks");
            if (trucks != null)
                foreach (var o in trucks)
                {
                    var t = new MineTruckInfo
                    {
                        id = MiniJson.Str(o, "id", "T"),
                        spawn = V2(MiniJson.Arr(o, "spawn"), Vector2.zero),
                        headingDeg = (float)MiniJson.Num(o, "heading_deg", 0),
                        capacityT = (float)MiniJson.Num(o, "capacity_t", 0),
                    };
                    t.callsign = MiniJson.Str(o, "callsign", t.id);
                    w.trucks.Add(t);
                }
            return w;
        }

        public static Vector2 V2(List<object> a, Vector2 def)
        {
            var d = MiniJson.Nums(a);
            if (d == null || d.Length < 2) return def;
            return new Vector2((float)d[0], (float)d[1]);
        }

        public static Vector3 V3(List<object> a, Vector3 def)
        {
            var d = MiniJson.Nums(a);
            if (d == null || d.Length < 3) return def;
            return new Vector3((float)d[0], (float)d[1], (float)d[2]);
        }
    }
}
