using UnityEngine;

namespace MineSim.EditorTools
{
    /// <summary>
    /// Extension points the importer calls instead of referencing optional packages directly.
    /// Optional editor assemblies fill them in from [InitializeOnLoadMethod]:
    /// MineSim.TMP.Editor (TextMeshPro labels) and MineSim.Splines.Editor (SplineContainer per road).
    /// When a hook is null the importer falls back to TextMesh labels or LineRenderer only.
    /// </summary>
    public static class MineImporterHooks
    {
        /// <summary>Adds a text component to <paramref name="go"/>. Return false to fall back to TextMesh.</summary>
        public delegate bool TextLabelFactory(GameObject go, string text, Color color, float height);

        public static TextLabelFactory CreateTextLabel;

        /// <summary>Called for every road with at least 2 world-space points (identity transform on the road object).</summary>
        public static System.Action<GameObject, Vector3[]> AddRoadSpline;
    }
}
