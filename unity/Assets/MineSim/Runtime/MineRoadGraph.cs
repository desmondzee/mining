using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Road graph handoff API for navigation. Nodes = sites + junctions (+ any road endpoint id),
    /// edges = MineRoad children (cost = polyline length; one_way roads only traversable from -> to).
    /// Built lazily from children of this GameObject; call <see cref="Rebuild"/> after editing roads.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineRoadGraph : MonoBehaviour
    {
        public struct Leg
        {
            public MineRoad road;
            public bool reversed;   // true = traversed to -> from
            public string fromNode, toNode;
            public float length;
        }

        private struct Edge
        {
            public string to;
            public MineRoad road;
            public bool reversed;
            public float cost;
        }

        private Dictionary<string, List<Edge>> adj;
        private Dictionary<string, Vector3> nodePos;

        public IEnumerable<string> NodeIds { get { EnsureBuilt(); return nodePos.Keys; } }

        private void Awake() { Rebuild(); }

        public void Rebuild()
        {
            adj = new Dictionary<string, List<Edge>>();
            nodePos = new Dictionary<string, Vector3>();
            foreach (var s in GetComponentsInChildren<MineSiteMarker>(true))
                if (!string.IsNullOrEmpty(s.siteId)) nodePos[s.siteId] = s.transform.position;
            foreach (var j in GetComponentsInChildren<MineJunction>(true))
                if (!string.IsNullOrEmpty(j.junctionId)) nodePos[j.junctionId] = j.transform.position;

            foreach (var r in GetComponentsInChildren<MineRoad>(true))
            {
                if (r.points == null || r.points.Length < 2 || string.IsNullOrEmpty(r.from) || string.IsNullOrEmpty(r.to))
                    continue;
                if (!nodePos.ContainsKey(r.from)) nodePos[r.from] = r.points[0];
                if (!nodePos.ContainsKey(r.to)) nodePos[r.to] = r.points[r.points.Length - 1];
                float len = Mathf.Max(0.01f, r.Length);
                AddEdge(r.from, new Edge { to = r.to, road = r, reversed = false, cost = len });
                if (!r.oneWay) AddEdge(r.to, new Edge { to = r.from, road = r, reversed = true, cost = len });
            }
        }

        private void AddEdge(string from, Edge e)
        {
            if (!adj.TryGetValue(from, out var l)) { l = new List<Edge>(); adj[from] = l; }
            l.Add(e);
        }

        private void EnsureBuilt() { if (adj == null || nodePos == null) Rebuild(); }

        public bool HasNode(string id) { EnsureBuilt(); return id != null && nodePos.ContainsKey(id); }

        public bool TryGetNodePosition(string id, out Vector3 pos)
        {
            EnsureBuilt();
            pos = default;
            return id != null && nodePos.TryGetValue(id, out pos);
        }

        /// <summary>Closest node (site/junction) to a world position, or null if the graph is empty.</summary>
        public string NearestNodeId(Vector3 worldPos, bool ignoreY = true)
        {
            EnsureBuilt();
            string best = null;
            float bd = float.MaxValue;
            foreach (var kv in nodePos)
            {
                Vector3 d = kv.Value - worldPos;
                if (ignoreY) d.y = 0f;
                float m = d.sqrMagnitude;
                if (m < bd) { bd = m; best = kv.Key; }
            }
            return best;
        }

        /// <summary>
        /// Dijkstra over the road graph. Returns the legs (roads + direction) or null if unreachable.
        /// Nodes in <paramref name="avoidNodes"/> are never entered (except as the start / goal).
        /// </summary>
        public List<Leg> GetRouteLegs(string fromId, string toId, ICollection<string> avoidNodes = null)
        {
            EnsureBuilt();
            if (fromId == null || toId == null || !nodePos.ContainsKey(fromId) || !nodePos.ContainsKey(toId)) return null;
            var legs = new List<Leg>();
            if (fromId == toId) return legs;

            var dist = new Dictionary<string, float> { [fromId] = 0f };
            var prev = new Dictionary<string, KeyValuePair<string, Edge>>();
            var done = new HashSet<string>();
            var open = new List<string> { fromId };

            while (open.Count > 0)
            {
                int bi = 0;
                for (int i = 1; i < open.Count; i++) if (dist[open[i]] < dist[open[bi]]) bi = i;
                string u = open[bi];
                open.RemoveAt(bi);
                if (!done.Add(u)) continue;
                if (u == toId) break;
                if (!adj.TryGetValue(u, out var edges)) continue;
                foreach (var e in edges)
                {
                    if (done.Contains(e.to)) continue;
                    if (avoidNodes != null && e.to != toId && avoidNodes.Contains(e.to)) continue;
                    float nd = dist[u] + e.cost;
                    if (!dist.TryGetValue(e.to, out float old) || nd < old)
                    {
                        dist[e.to] = nd;
                        prev[e.to] = new KeyValuePair<string, Edge>(u, e);
                        if (!open.Contains(e.to)) open.Add(e.to);
                    }
                }
            }

            if (!prev.ContainsKey(toId)) return null;
            for (string n = toId; n != fromId;)
            {
                var p = prev[n];
                legs.Add(new Leg { road = p.Value.road, reversed = p.Value.reversed, fromNode = p.Key, toNode = n, length = p.Value.cost });
                n = p.Key;
            }
            legs.Reverse();
            return legs;
        }

        /// <summary>
        /// Route between two node ids (site or junction ids) as a world-space polyline, built by
        /// concatenating road centrelines (reversed where driven backwards). Empty list if unreachable.
        /// </summary>
        public List<Vector3> GetRoute(string fromId, string toId)
        {
            return GetRoute(fromId, toId, null);
        }

        public List<Vector3> GetRoute(string fromId, string toId, ICollection<string> avoidNodes)
        {
            var result = new List<Vector3>();
            var legs = GetRouteLegs(fromId, toId, avoidNodes);
            if (legs == null)
            {
                Debug.LogWarning($"[MineRoadGraph] No route {fromId} -> {toId}");
                return result;
            }
            if (legs.Count == 0)
            {
                if (TryGetNodePosition(fromId, out var p)) result.Add(p);
                return result;
            }
            foreach (var leg in legs)
            {
                var pts = leg.road.GetPoints(leg.reversed);
                for (int i = 0; i < pts.Count; i++)
                {
                    if (i == 0 && result.Count > 0 && (result[result.Count - 1] - pts[0]).sqrMagnitude < 0.25f) continue;
                    result.Add(pts[i]);
                }
            }
            return result;
        }

        /// <summary>Route from an arbitrary world position: straight to the nearest node, then along roads.</summary>
        public List<Vector3> GetRouteFrom(Vector3 start, string toId)
        {
            string n = NearestNodeId(start);
            var r = GetRoute(n, toId);
            if (r.Count > 0) r.Insert(0, start);
            return r;
        }

        public static float PolylineLength(List<Vector3> pts)
        {
            float l = 0f;
            for (int i = 1; i < pts.Count; i++) l += Vector3.Distance(pts[i - 1], pts[i]);
            return l;
        }

        private void OnDrawGizmosSelected()
        {
            Gizmos.color = Color.yellow;
            foreach (var r in GetComponentsInChildren<MineRoad>())
            {
                if (r.points == null) continue;
                for (int i = 1; i < r.points.Length; i++) Gizmos.DrawLine(r.points[i - 1], r.points[i]);
            }
        }
    }
}
