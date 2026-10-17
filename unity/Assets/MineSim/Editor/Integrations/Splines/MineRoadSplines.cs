using UnityEditor;
using UnityEngine;
using UnityEngine.Splines;

namespace MineSim.EditorTools
{
    /// <summary>Adds a SplineContainer to every imported road. Only compiled with com.unity.splines 2.x.</summary>
    internal static class MineRoadSplines
    {
        [InitializeOnLoadMethod]
        private static void Register()
        {
            MineImporterHooks.AddRoadSpline = Add;
        }

        private static void Add(GameObject go, Vector3[] pts)
        {
            var sc = go.GetComponent<SplineContainer>();
            if (sc == null) sc = go.AddComponent<SplineContainer>();
            var spline = sc.Spline;
            if (spline == null)
            {
                Debug.LogWarning("[MineSim] SplineContainer had no default spline for " + go.name);
                return;
            }
            spline.Clear();
            foreach (var p in pts)
                spline.Add(new BezierKnot(new Unity.Mathematics.float3(p.x, p.y, p.z)), TangentMode.AutoSmooth);
        }
    }
}
