// Streams a Camera to the brain at a fixed rate: RGBA via AsyncGPUReadback, and (for truck
// cameras) a low-res depth image from a grid of physics raycasts.
//
// Depth comes from colliders, not from what is rendered. Gaussian splats have no
// colliders, so the quarry needs collision geometry (ground, walls) for depth to see it.

using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

[RequireComponent(typeof(Camera))]
public class SensorCamera : MonoBehaviour
{
    [Tooltip("Stream name shown on the website, e.g. truck-0 or overview")]
    public string cameraName = "overview";
    [Tooltip("Set on truck front cameras; leave empty for the overview camera")]
    public TruckAgent truck;
    public int width = 640, height = 360;
    public float fps = 15f;
    [Tooltip("Depth image size (raycasts per frame = depthWidth * depthHeight)")]
    public int depthWidth = 64, depthHeight = 36;
    public float depthRange = 100f;
    public LayerMask depthMask = ~0;
    [Tooltip("GPU readback rows are usually bottom-up; untick if images arrive upside down")]
    public bool flipRows = true;

    Camera cam;
    float nextCapture;
    bool pending;

    void Awake()
    {
        cam = GetComponent<Camera>();
        cam.targetTexture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
        BrainClient.RegisterCamera(cameraName);
    }

    void LateUpdate()
    {
        var client = BrainClient.Instance;
        if (client == null || !client.Connected || pending || Time.time < nextCapture) return;
        nextCapture = Time.time + 1f / fps;

        // Sample pose and depth now so they match the frame being read back.
        var header = new BrainProtocol.FrameHeader
        {
            camera = cameraName,
            rgb = new BrainProtocol.RgbMeta { w = width, h = height, flip = flipRows },
        };
        float[] depth = null;
        if (truck != null)
        {
            header.truck = truck.truckId;
            header.pose = truck.Pose();
            header.speed = truck.Speed;
            depth = CaptureDepth();
            header.depth = new BrainProtocol.Size { w = depthWidth, h = depthHeight };
        }

        pending = true;
        AsyncGPUReadback.Request(cam.targetTexture, 0, TextureFormat.RGBA32, req =>
        {
            pending = false;
            if (req.hasError) return;
            var rgb = req.GetData<byte>();
            int depthBytes = depth != null ? depth.Length * 4 : 0;
            var payload = new byte[rgb.Length + depthBytes];
            NativeArray<byte>.Copy(rgb, payload, rgb.Length);
            if (depth != null) System.Buffer.BlockCopy(depth, 0, payload, rgb.Length, depthBytes);
            client.Send(BrainProtocol.Encode(JsonUtility.ToJson(header), payload));
        });
    }

    /// Perpendicular distance (metres) per pixel, row 0 = top. Misses read as depthRange.
    float[] CaptureDepth()
    {
        int n = depthWidth * depthHeight;
        var commands = new NativeArray<RaycastCommand>(n, Allocator.TempJob);
        var hits = new NativeArray<RaycastHit>(n, Allocator.TempJob);
        var query = new QueryParameters(depthMask, false, QueryTriggerInteraction.Ignore, false);
        var fwd = cam.transform.forward;
        for (int v = 0; v < depthHeight; v++)
        for (int u = 0; u < depthWidth; u++)
        {
            var ray = cam.ViewportPointToRay(new Vector3((u + 0.5f) / depthWidth, 1f - (v + 0.5f) / depthHeight, 0));
            commands[v * depthWidth + u] = new RaycastCommand(ray.origin, ray.direction, query, depthRange);
        }
        RaycastCommand.ScheduleBatch(commands, hits, 64, 1).Complete();

        var depth = new float[n];
        for (int i = 0; i < n; i++)
        {
            var hit = hits[i];
            depth[i] = hit.colliderInstanceID != 0 ? hit.distance * Vector3.Dot(commands[i].direction, fwd) : depthRange;
        }
        commands.Dispose();
        hits.Dispose();
        return depth;
    }
}
