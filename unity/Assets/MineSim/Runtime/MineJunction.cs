using UnityEngine;

namespace MineSim
{
    /// <summary>Road junction (GameObject "Junction_&lt;id&gt;").</summary>
    [DisallowMultipleComponent]
    public class MineJunction : MonoBehaviour
    {
        public string junctionId;
        public string junctionName;
        public Vector3 Position => transform.position;
    }
}
