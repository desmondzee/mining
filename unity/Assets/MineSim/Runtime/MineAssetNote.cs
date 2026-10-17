using UnityEngine;

namespace MineSim
{
    /// <summary>Inspector note attached by the importer (e.g. how to bring in the Gaussian splat).</summary>
    public class MineAssetNote : MonoBehaviour
    {
        [TextArea(4, 20)] public string note;
        public string assetFile;
        public string assetPath;
    }
}
