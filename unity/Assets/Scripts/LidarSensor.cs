using Unity.Collections;
using UnityEngine;

public class LidarSensor : MonoBehaviour
{
    public TruckAgent truck;
    public float range = 100f;
    public float horizontalFov = 120f;
    public float verticalMin = -20f;
    public float verticalMax = 10f;
    public int columns = 240;
    public int channels = 30;
    public float rate = 10f;
    public LayerMask mask = ~0;

    Vector3[] directions;
    float nextScan;

    void Awake()
    {
        directions = new Vector3[columns * channels];
        for (int c = 0; c < channels; c++)
        for (int u = 0; u < columns; u++)
        {
            float el = verticalMin + (c + 0.5f) * (verticalMax - verticalMin) / channels;
            float az = -horizontalFov / 2 + (u + 0.5f) * horizontalFov / columns;
            directions[c * columns + u] = Quaternion.Euler(-el, az, 0) * Vector3.forward;
        }
    }

    void Update()
    {
        var client = BrainClient.Instance;
        if (client == null || !client.Connected || Time.time < nextScan) return;
        nextScan = Time.time + 1f / rate;

        int n = directions.Length;
        var commands = new NativeArray<RaycastCommand>(n, Allocator.TempJob);
        var hits = new NativeArray<RaycastHit>(n, Allocator.TempJob);
        var query = new QueryParameters(mask, false, QueryTriggerInteraction.Ignore, false);
        var origin = transform.position;
        for (int i = 0; i < n; i++)
            commands[i] = new RaycastCommand(origin, transform.TransformDirection(directions[i]), query, range);
        RaycastCommand.ScheduleBatch(commands, hits, 64, 1).Complete();

        var points = new short[n * 3];
        int count = 0;
        for (int i = 0; i < n; i++)
        {
            if (hits[i].collider == null) continue;
            var p = transform.InverseTransformPoint(hits[i].point);
            points[count++] = Centimetres(p.x);
            points[count++] = Centimetres(p.y);
            points[count++] = Centimetres(p.z);
        }
        commands.Dispose();
        hits.Dispose();

        var header = new BrainProtocol.LidarHeader { truck = truck.truckId, points = count / 3, pose = truck.Pose() };
        var payload = new byte[count * 2];
        System.Buffer.BlockCopy(points, 0, payload, 0, payload.Length);
        client.Send(BrainProtocol.Encode(JsonUtility.ToJson(header), payload));
    }

    static short Centimetres(float metres) => (short)Mathf.Clamp(Mathf.Round(metres * 100f), short.MinValue, short.MaxValue);
}
