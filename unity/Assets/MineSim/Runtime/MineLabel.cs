using UnityEngine;

namespace MineSim
{
    /// <summary>Tags a world-space label object (TextMesh or TextMeshPro) so MineLabelToggle can find it.</summary>
    [DisallowMultipleComponent]
    public class MineLabel : MonoBehaviour
    {
        [Tooltip("site | road | junction")]
        public string kind;
        public string text;
    }
}
