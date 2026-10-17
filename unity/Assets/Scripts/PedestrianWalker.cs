using UnityEngine;

[RequireComponent(typeof(CapsuleCollider), typeof(Rigidbody))]
public class PedestrianWalker : MonoBehaviour
{
    public Transform path;
    public float speed = 1.4f;
    public float turnRate = 360f;
    public bool loop = true;
    public LayerMask groundMask = ~0;

    readonly RaycastHit[] hits = new RaycastHit[8];
    int target;
    int step = 1;

    void Awake()
    {
        GetComponent<Rigidbody>().isKinematic = true;
        if (path != null && path.childCount > 0) transform.position = path.GetChild(0).position;
    }

    void Update()
    {
        if (path == null || path.childCount < 2) return;

        var goal = path.GetChild(target).position;
        var flat = new Vector3(goal.x - transform.position.x, 0, goal.z - transform.position.z);
        if (flat.magnitude < 0.2f)
        {
            Advance();
            return;
        }

        transform.rotation = Quaternion.RotateTowards(transform.rotation, Quaternion.LookRotation(flat), turnRate * Time.deltaTime);
        var next = transform.position + transform.forward * Mathf.Min(speed * Time.deltaTime, flat.magnitude);
        next.y = GroundHeight(next, transform.position.y);
        transform.position = next;
    }

    float GroundHeight(Vector3 at, float fallback)
    {
        int n = Physics.RaycastNonAlloc(at + Vector3.up * 3f, Vector3.down, hits, 6f, groundMask, QueryTriggerInteraction.Ignore);
        float best = float.NegativeInfinity;
        for (int i = 0; i < n; i++)
            if (hits[i].rigidbody == null && hits[i].point.y > best) best = hits[i].point.y;
        return float.IsNegativeInfinity(best) ? fallback : best;
    }

    void Advance()
    {
        if (loop)
        {
            target = (target + 1) % path.childCount;
            return;
        }
        if (target + step >= path.childCount || target + step < 0) step = -step;
        target += step;
    }
}
