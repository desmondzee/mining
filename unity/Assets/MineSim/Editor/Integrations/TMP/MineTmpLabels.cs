using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace MineSim.EditorTools
{
    /// <summary>TextMeshPro labels for the importer. Only compiled with com.unity.ugui 2.x / com.unity.textmeshpro.</summary>
    internal static class MineTmpLabels
    {
        [InitializeOnLoadMethod]
        private static void Register()
        {
            MineImporterHooks.CreateTextLabel = Create;
        }

        private static bool Create(GameObject go, string text, Color color, float height)
        {
            if (!TmpReady()) return false;
            var tmp = go.AddComponent<TMPro.TextMeshPro>();
            tmp.text = text;
            tmp.fontSize = height * 10f;   // 3D TMP: ~fontSize/10 world units
            tmp.alignment = TMPro.TextAlignmentOptions.Center;
            tmp.color = color;
            tmp.rectTransform.sizeDelta = new Vector2(Mathf.Max(20f, text.Length * height * 1.2f), height * 3f);
            var r = go.GetComponent<MeshRenderer>();
            if (r != null) r.shadowCastingMode = ShadowCastingMode.Off;
            return true;
        }

        private static bool TmpReady()
        {
            try
            {
                var settings = Resources.Load<TMPro.TMP_Settings>("TMP Settings");
                return settings != null && TMPro.TMP_Settings.defaultFontAsset != null;
            }
            catch (System.Exception)
            {
                return false;
            }
        }
    }
}
