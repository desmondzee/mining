using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// One haul road (GameObject "Road_&lt;id&gt;"). <see cref="points"/> is the centreline in world space
    /// (Unity metres, ordered from -> to, ~5 m spacing). Child "WP_000.." empties sit on the same points.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineRoad : MonoBehaviour
    {
        public string roadId;
        public string roadName;
        [Tooltip("ramp | haul | spur")]
        public string roadType;
        [Tooltip("Site or junction id at points[0]")]
        public string from;
        [Tooltip("Site or junction id at points[last]")]
        public string to;
        public float widthM = 10f;
        public float gradePct;
        public bool oneWay;
        public Vector3[] points = new Vector3[0];
        public LineRenderer line;

        public float Length
        {
            get
            {
                float len = 0f;
                if (points == null) return 0f;
                for (int i = 1; i < points.Length; i++) len += Vector3.Distance(points[i - 1], points[i]);
                return len;
            }
        }

        public Vector3 Midpoint
        {
            get
            {
                if (points == null || points.Length == 0) return transform.position;
                float half = Length * 0.5f, acc = 0f;
                for (int i = 1; i < points.Length; i++)
                {
                    float d = Vector3.Distance(points[i - 1], points[i]);
                    if (acc + d >= half && d > 1e-5f) return Vector3.Lerp(points[i - 1], points[i], (half - acc) / d);
                    acc += d;
                }
                return points[points.Length - 1];
            }
        }

        /// <summary>Copy of the points, optionally reversed (to -> from).</summary>
        public List<Vector3> GetPoints(bool reversed = false)
        {
            var l = new List<Vector3>(points ?? new Vector3[0]);
            if (reversed) l.Reverse();
            return l;
        }

        public void SetLineVisible(bool visible)
        {
            if (line != null) line.enabled = visible;
        }
    }
}
