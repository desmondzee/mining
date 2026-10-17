// Site marker component (file name must match class name for scene serialization).
using UnityEngine;

namespace MineSim
{
    /// <summary>Attached to each site marker so the runtime client can update tonnage labels.</summary>
    public class MineSiteMarker : MonoBehaviour
    {
        public string siteId;
        public string siteType;
        public string displayName;
        public float tonnes = -1f;
        public TextMesh label;

        public void SetTonnes(float t)
        {
            tonnes = t;
            RefreshLabel();
        }

        public void RefreshLabel()
        {
            if (label == null) return;
            label.text = tonnes >= 0f && siteType == "ore"
                ? displayName + "\n" + Mathf.RoundToInt(tonnes) + " t"
                : displayName;
        }

        public static Color ColorFor(string type)
        {
            switch (type)
            {
                case "crusher": return new Color(1.00f, 0.55f, 0.10f);
                case "dump":    return new Color(0.55f, 0.45f, 0.35f);
                case "ore":     return new Color(0.20f, 0.75f, 0.55f);
                case "parking": return new Color(0.25f, 0.55f, 1.00f);
                case "fuel":    return new Color(0.95f, 0.20f, 0.20f);
                default:        return Color.white;
            }
        }

        /// <summary>Creates a coloured pillar + ground disc + floating label at (x, groundY, z).</summary>
        public static MineSiteMarker Create(Transform parent, string id, string type, string name,
                                            float x, float groundY, float z, float tonnes = -1f)
        {
            var root = new GameObject("Site_" + id);
            if (parent != null) root.transform.SetParent(parent, false);
            root.transform.position = new Vector3(x, groundY, z);
            Color c = ColorFor(type);

            var disc = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            disc.name = "Disc";
            disc.transform.SetParent(root.transform, false);
            disc.transform.localPosition = new Vector3(0f, 0.1f, 0f);
            disc.transform.localScale = new Vector3(16f, 0.1f, 16f);
            StripCollider(disc);
            Tint(disc, new Color(c.r, c.g, c.b, 1f) * 0.8f);

            var pillar = GameObject.CreatePrimitive(type == "crusher" ? PrimitiveType.Cube : PrimitiveType.Cylinder);
            pillar.name = "Pillar";
            pillar.transform.SetParent(root.transform, false);
            pillar.transform.localPosition = new Vector3(0f, 6f, 0f);
            pillar.transform.localScale = type == "crusher" ? new Vector3(6f, 12f, 6f) : new Vector3(1.5f, 6f, 1.5f);
            StripCollider(pillar);
            Tint(pillar, c);

            var labelGo = new GameObject("Label");
            labelGo.transform.SetParent(root.transform, false);
            labelGo.transform.localPosition = new Vector3(0f, 16f, 0f);
            var tm = MineMaterials.AddTextMesh(labelGo, "", c, 0.5f);

            var m = root.AddComponent<MineSiteMarker>();
            m.siteId = id;
            m.siteType = type;
            m.displayName = string.IsNullOrEmpty(name) ? id : name;
            m.tonnes = tonnes;
            m.label = tm;
            m.RefreshLabel();
            return m;
        }

        public static void StripCollider(GameObject go)
        {
            var col = go.GetComponent<Collider>();
            if (col == null) return;
            if (Application.isPlaying) Destroy(col); else DestroyImmediate(col);
        }

        /// <summary>Tints a primitive. Uses a new material so it works in edit mode and play mode.</summary>
        public static void Tint(GameObject go, Color c)
        {
            var r = go.GetComponent<Renderer>();
            if (r == null) return;
            var mat = new Material(r.sharedMaterial != null ? r.sharedMaterial : MineMaterials.Lit());
            mat.color = c;
            r.sharedMaterial = mat;
        }
    }
}
