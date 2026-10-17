using System;
using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Goal coordinates for the brain's driving policy, baked from world.json by
    /// <c>Tools/Mine Sim/Integrate World Into Open Scene</c> (so they are available in the player without data/worlds/).
    ///
    /// The brain only needs ground (x, z): <c>POST {brain}/api/trucks/{id}/goal {"x":..,"z":..}</c> (spec.md, Web).
    /// Per site: <see cref="SiteGoal.loadPoint"/> (where a truck stops to be loaded, ore sites) and
    /// <see cref="SiteGoal.dumpPoint"/> (where it tips, crusher / dump). When world.json has no explicit
    /// <c>load_point</c> / <c>dump_point</c>, both fall back to the site position.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineGoals : MonoBehaviour
    {
        [Serializable]
        public class SiteGoal
        {
            public string siteId, siteType, name;
            public Vector3 position;
            public bool hasLoadPoint, hasDumpPoint;
            public Vector3 loadPoint;
            public Vector3 dumpPoint;
            public List<Slot> slots = new List<Slot>();

            /// <summary>Where a truck should drive to for this site (load point for ore, dump point for crusher/dump, else position).</summary>
            public Vector3 Goal
            {
                get
                {
                    if (hasLoadPoint && (siteType == "ore" || !hasDumpPoint)) return loadPoint;
                    if (hasDumpPoint) return dumpPoint;
                    return position;
                }
            }
        }

        [Serializable]
        public class Slot
        {
            public string id;
            public Vector3 position;
            public bool hasHeading;
            public float headingDeg;
        }

        [Serializable]
        public class Equipment
        {
            public string id, type, siteId, model;
            public Vector3 position;
            public bool hasY, hasHeading;
            public float headingDeg;
            public float lengthM;
        }

        public string worldId;
        [Tooltip("world.json bounds [xmin, zmin, xmax, zmax]")]
        public Vector4 bounds;
        public List<SiteGoal> sites = new List<SiteGoal>();
        public List<Equipment> equipment = new List<Equipment>();

        static MineGoals cached;

        public static MineGoals Instance
        {
            get
            {
                if (cached == null) cached = FindFirstObjectByType<MineGoals>();
                return cached;
            }
        }

        public SiteGoal Find(string siteId)
        {
            foreach (var s in sites) if (s.siteId == siteId) return s;
            return null;
        }

        public List<SiteGoal> OfType(string type)
        {
            var r = new List<SiteGoal>();
            foreach (var s in sites) if (s.siteType == type) r.Add(s);
            r.Sort((a, b) => string.CompareOrdinal(a.siteId, b.siteId));
            return r;
        }

        public SiteGoal FirstOfType(string type)
        {
            var l = OfType(type);
            return l.Count > 0 ? l[0] : null;
        }

        /// <summary>JSON body for POST /api/trucks/{id}/goal.</summary>
        public static string GoalJson(Vector3 p)
        {
            return "{\"x\":" + p.x.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture) +
                   ",\"z\":" + p.z.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture) + "}";
        }

        // ------------------------------------------------------------------ world.json parsing (optional fields)

        /// <summary>
        /// Fills this component from world.json text. Optional fields are read leniently:
        /// site <c>load_point</c> / <c>dump_point</c> as [x,z], [x,y,z] or {"pos"|"pos3"|"x","z"}; parking <c>slots</c> as a list of
        /// those or objects with <c>id</c>, <c>heading_deg</c>; top-level <c>equipment</c> entries with
        /// <c>id</c>, <c>type</c>|<c>kind</c>, <c>site</c>|<c>site_id</c>, <c>pos</c>|<c>pos3</c>|<c>position</c>, <c>heading_deg</c>|<c>yaw_deg</c>, <c>length_m</c>, <c>model</c>.
        /// y is NaN when only (x, z) was given; <paramref name="groundY"/> (may be null) fills it.
        /// </summary>
        public void Bake(string worldJson, Func<float, float, float> groundY)
        {
            var root = MiniJson.Parse(worldJson) as Dictionary<string, object>;
            if (root == null) throw new FormatException("world.json root is not an object");
            worldId = MiniJson.Str(root, "id", "world");
            bounds = ReadBounds(root);
            sites.Clear();
            equipment.Clear();

            var sl = MiniJson.Arr(root, "sites");
            if (sl != null)
                foreach (var o in sl)
                {
                    var s = new SiteGoal
                    {
                        siteId = MiniJson.Str(o, "id", "site"),
                        siteType = MiniJson.Str(o, "type", ""),
                    };
                    s.name = MiniJson.Str(o, "name", s.siteId);
                    Vector3 pos;
                    if (!TryPoint(Field(o, "pos3"), out pos) && !TryPoint(Field(o, "pos"), out pos)) pos = Vector3.zero;
                    s.position = FillY(pos, groundY);
                    if (TryPoint(Field(o, "load_point"), out var lp)) { s.hasLoadPoint = true; s.loadPoint = FillY(lp, groundY); }
                    if (TryPoint(Field(o, "dump_point"), out var dp)) { s.hasDumpPoint = true; s.dumpPoint = FillY(dp, groundY); }
                    var slots = MiniJson.Arr(o, "slots");
                    if (slots != null)
                        for (int i = 0; i < slots.Count; i++)
                        {
                            var so = slots[i];
                            Vector3 sp;
                            if (!TryPoint(so, out sp)) continue;
                            var slot = new Slot { id = MiniJson.Str(so, "id", s.siteId + "_" + i), position = FillY(sp, groundY) };
                            if (TryHeading(so, out float h)) { slot.hasHeading = true; slot.headingDeg = h; }
                            s.slots.Add(slot);
                        }
                    sites.Add(s);
                }

            var el = MiniJson.Arr(root, "equipment");
            if (el != null)
                for (int i = 0; i < el.Count; i++)
                {
                    var o = el[i];
                    var e = new Equipment
                    {
                        id = MiniJson.Str(o, "id", "equipment_" + i),
                        type = MiniJson.Str(o, "type", MiniJson.Str(o, "kind", "excavator")),
                        siteId = MiniJson.Str(o, "site", MiniJson.Str(o, "site_id", "")),
                        model = MiniJson.Str(o, "model", ""),
                        lengthM = (float)MiniJson.Num(o, "length_m", 0),
                    };
                    if (!TryPoint(o, out var ep)) continue;
                    e.hasY = !float.IsNaN(ep.y);
                    e.position = FillY(ep, groundY);
                    if (TryHeading(o, out float h)) { e.hasHeading = true; e.headingDeg = h; }
                    equipment.Add(e);
                }
        }

        public static Vector4 ReadBounds(Dictionary<string, object> root)
        {
            var b = MiniJson.Nums(MiniJson.Arr(root, "bounds"));
            if (b != null && b.Length >= 4) return new Vector4((float)b[0], (float)b[1], (float)b[2], (float)b[3]);
            var o = MineWorldData.V2(MiniJson.Arr(root, "origin"), new Vector2(-100, -100));
            var s = MineWorldData.V2(MiniJson.Arr(root, "size_m"), new Vector2(200, 200));
            return new Vector4(o.x, o.y, o.x + s.x, o.y + s.y);
        }

        static object Field(object o, string key)
        {
            var d = o as Dictionary<string, object>;
            return d != null && d.TryGetValue(key, out var v) ? v : null;
        }

        static Vector3 FillY(Vector3 p, Func<float, float, float> groundY)
        {
            if (float.IsNaN(p.y)) p.y = groundY != null ? groundY(p.x, p.z) : 0f;
            return p;
        }

        /// <summary>[x,z] / [x,y,z] / {"pos3"|"pos"|"position"|"point": ...} / {"x","y"?,"z"}. y = NaN when absent.</summary>
        public static bool TryPoint(object o, out Vector3 p)
        {
            p = Vector3.zero;
            if (o == null) return false;
            if (o is List<object> list)
            {
                var d = MiniJson.Nums(list);
                if (d == null || d.Length < 2) return false;
                p = d.Length >= 3 ? new Vector3((float)d[0], (float)d[1], (float)d[2]) : new Vector3((float)d[0], float.NaN, (float)d[1]);
                return true;
            }
            if (o is Dictionary<string, object> dict)
            {
                foreach (var key in new[] { "pos3", "pos", "position", "point", "xz" })
                    if (dict.TryGetValue(key, out var v) && TryPoint(v, out p)) return true;
                if (MiniJson.Has(dict, "x") && MiniJson.Has(dict, "z"))
                {
                    p = new Vector3((float)MiniJson.Num(dict, "x", 0),
                        MiniJson.Has(dict, "y") ? (float)MiniJson.Num(dict, "y", 0) : float.NaN,
                        (float)MiniJson.Num(dict, "z", 0));
                    return true;
                }
            }
            return false;
        }

        public static bool TryHeading(object o, out float deg)
        {
            deg = 0f;
            foreach (var key in new[] { "heading_deg", "yaw_deg", "heading", "yaw" })
                if (MiniJson.Has(o, key)) { deg = (float)MiniJson.Num(o, key, 0); return true; }
            return false;
        }
    }
}
