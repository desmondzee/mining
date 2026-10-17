using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>One per site (GameObject "Site_&lt;id&gt;"). Site ids differ per world: look sites up by type.</summary>
    [DisallowMultipleComponent]
    public class MineSiteMarker : MonoBehaviour
    {
        public string siteId;
        [Tooltip("crusher | dump | ore | parking | fuel")]
        public string siteType;
        public string siteName;
        public bool hasTonnes;
        public float tonnes;
        [Tooltip("Ground position [x, z] from world.json")]
        public Vector2 groundPos;

        public Vector3 Position => transform.position;

        public static MineSiteMarker Find(string id)
        {
            foreach (var s in Object.FindObjectsByType<MineSiteMarker>(FindObjectsSortMode.None))
                if (s.siteId == id) return s;
            return null;
        }

        public static List<MineSiteMarker> FindByType(string type)
        {
            var r = new List<MineSiteMarker>();
            foreach (var s in Object.FindObjectsByType<MineSiteMarker>(FindObjectsSortMode.None))
                if (s.siteType == type) r.Add(s);
            r.Sort((a, b) => string.CompareOrdinal(a.siteId, b.siteId));
            return r;
        }
    }
}
