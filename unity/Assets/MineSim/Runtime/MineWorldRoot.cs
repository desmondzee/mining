using UnityEngine;

namespace MineSim
{
    /// <summary>Sits on the "MineWorld_&lt;id&gt;" root created by Tools/Mine Sim/Import World.</summary>
    [DisallowMultipleComponent]
    public class MineWorldRoot : MonoBehaviour
    {
        public string worldId;
        public string worldName;
        [Tooltip("procedural | marble")]
        public string source;
        [Tooltip("Absolute path of data/worlds/<id>/ used at import time")]
        public string worldDir;
        public Vector2 sizeM;
        public Vector2 origin;
        public Terrain terrain;
        public MineRoadGraph graph;

        public MineSiteMarker FindSite(string id)
        {
            foreach (var s in GetComponentsInChildren<MineSiteMarker>(true)) if (s.siteId == id) return s;
            return null;
        }

        public MineSiteMarker FirstSiteOfType(string type)
        {
            MineSiteMarker best = null;
            foreach (var s in GetComponentsInChildren<MineSiteMarker>(true))
                if (s.siteType == type && (best == null || string.CompareOrdinal(s.siteId, best.siteId) < 0)) best = s;
            return best;
        }

        public MineRoad FindRoad(string id)
        {
            foreach (var r in GetComponentsInChildren<MineRoad>(true)) if (r.roadId == id) return r;
            return null;
        }

        /// <summary>Terrain height at world (x, z); falls back to 0 without a terrain.</summary>
        public float GroundHeight(float x, float z)
        {
            if (terrain == null) return 0f;
            return terrain.SampleHeight(new Vector3(x, 0f, z)) + terrain.transform.position.y;
        }
    }
}
