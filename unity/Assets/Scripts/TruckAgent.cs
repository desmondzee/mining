using UnityEngine;

[RequireComponent(typeof(Rigidbody))]
public class TruckAgent : MonoBehaviour
{
    public string truckId = "truck-0";
    public float maxSpeed = 8f;
    public float maxSteerDeg = 30f;
    public float wheelbase = 5f;
    public float accel = 1.5f;
    public float commandTimeout = 0.5f;

    Rigidbody body;
    public float Speed { get; private set; }

    void Awake()
    {
        body = GetComponent<Rigidbody>();
        body.isKinematic = true;
        BrainClient.RegisterTruck(truckId);
    }

    void FixedUpdate()
    {
        float throttle = 0, steer = 0;
        var a = BrainClient.Instance ? BrainClient.Instance.LatestAction(truckId, commandTimeout) : null;
        if (a != null) { throttle = Mathf.Clamp01(a.throttle); steer = Mathf.Clamp(a.steer, -1, 1); }

        float dt = Time.fixedDeltaTime;
        Speed += (throttle * maxSpeed - Speed) * Mathf.Min(1f, dt * accel);
        float yawRate = Speed / wheelbase * Mathf.Tan(steer * maxSteerDeg * Mathf.Deg2Rad) * Mathf.Rad2Deg;
        var rot = Quaternion.Euler(0, body.rotation.eulerAngles.y + yawRate * dt, 0);
        body.MoveRotation(rot);
        body.MovePosition(body.position + rot * Vector3.forward * Speed * dt);
    }

    public BrainProtocol.Pose Pose()
    {
        var p = transform.position;
        return new BrainProtocol.Pose { x = p.x, z = p.z, yaw = transform.eulerAngles.y };
    }
}
