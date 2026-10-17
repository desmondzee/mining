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
        [Tooltip("Scan worlds: the invisible ground MeshCollider (used for GroundHeight when there is no Terrain)")]
        public Collider groundCollider;
        [Tooltip("Scan worlds: optional coarse collider of the surround apron/hills (assets.surround), same ground layer")]
        public Collider surroundCollider;
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

        /// <summary>Terrain (or scan ground collider) height at world (x, z); falls back to 0 without either.</summary>
        public float GroundHeight(float x, float z)
        {
            if (terrain == null)
            {
                if (groundCollider != null &&
                    groundCollider.Raycast(new Ray(new Vector3(x, 10000f, z), Vector3.down), out RaycastHit hit, 20000f))
                    return hit.point.y;
                return 0f;
            }
            return terrain.SampleHeight(new Vector3(x, 0f, z)) + terrain.transform.position.y;
        }
    }
}
