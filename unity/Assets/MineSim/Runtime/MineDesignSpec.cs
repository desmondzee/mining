using System;
using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Optional world.json "design" block (mine spec: bench height, road width, berm height, design truck ...).
    /// Schema is open, so every leaf is kept as a flattened key/value pair ("design_truck.payload_t" = "220");
    /// a few well-known numeric fields are also parsed best-effort (NaN when absent).
    /// </summary>
    [DisallowMultipleComponent]
    public class MineDesignSpec : MonoBehaviour
    {
        [Serializable]
        public struct Entry
        {
            public string key;
            public string value;
        }

        public float benchHeightM = float.NaN;
        public float roadWidthM = float.NaN;
        public float bermHeightM = float.NaN;
        public string designTruck = "";
        public List<Entry> entries = new List<Entry>();

        public string Get(string key)
        {
            foreach (var e in entries) if (e.key == key) return e.value;
            return null;
        }

        public float GetFloat(string key, float def = float.NaN)
        {
            var s = Get(key);
            return s != null && float.TryParse(s, System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out float v) ? v : def;
        }
    }
}
