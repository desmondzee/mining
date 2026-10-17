using Unity.Collections;
using UnityEngine;
using UnityEngine.Experimental.Rendering;
using UnityEngine.Rendering;

[RequireComponent(typeof(Camera))]
public class SensorCamera : MonoBehaviour
{
    public string cameraName = "overview";
    public TruckAgent truck;
    public int width = 640, height = 360;
    public float fps = 15f;
    public float horizontalFov = 90f;
    public int depthWidth = 64, depthHeight = 36;
    public float depthRange = 100f;
    public LayerMask depthMask = ~0;
    public bool flipRows = true;
    public int jpegQuality = 80;

    Camera cam;
    float nextCapture;
    bool pending;

    void Awake()
    {
        cam = GetComponent<Camera>();
        cam.fieldOfView = Camera.HorizontalToVerticalFieldOfView(horizontalFov, (float)width / height);
        cam.targetTexture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32);
        BrainClient.RegisterCamera(cameraName);
    }

    void LateUpdate()
    {
        var client = BrainClient.Instance;
        if (client == null || !client.Connected || pending || Time.time < nextCapture) return;
        nextCapture = Time.time + 1f / fps;

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
            byte[] image;
            if (jpegQuality > 0)
            {
                using var jpeg = ImageConversion.EncodeNativeArrayToJPG(rgb, GraphicsFormat.R8G8B8A8_UNorm, (uint)width, (uint)height, 0, jpegQuality);
                image = jpeg.ToArray();
                header.rgb.format = "jpeg";
                header.rgb.flip = false;
                header.rgb.bytes = image.Length;
            }
            else
            {
                image = rgb.ToArray();
            }
            int depthBytes = depth != null ? depth.Length * 4 : 0;
            var payload = new byte[image.Length + depthBytes];
            System.Buffer.BlockCopy(image, 0, payload, 0, image.Length);
            if (depth != null) System.Buffer.BlockCopy(depth, 0, payload, image.Length, depthBytes);
            client.Send(BrainProtocol.Encode(JsonUtility.ToJson(header), payload));
        });
    }

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
            depth[i] = hit.collider != null ? hit.distance * Vector3.Dot(commands[i].direction, fwd) : depthRange;
        }
        commands.Dispose();
        hits.Dispose();
        return depth;
    }
}
