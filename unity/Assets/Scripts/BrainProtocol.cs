using System;
using System.Text;

public static class BrainProtocol
{
    [Serializable] public class Size { public int w; public int h; }
    [Serializable] public class RgbMeta { public int w; public int h; public bool flip; public int channels = 4; public string format = "raw"; public int bytes; }
    [Serializable] public class Pose { public float x; public float y; public float z; public float yaw; }
    [Serializable] public class CamPose { public float x; public float y; public float z; public float yaw; public float pitch; public float roll; public float hfov; }

    [Serializable]
    public class FrameHeader
    {
        public string type = "frame";
        public string camera;
        public string truck = "";
        public RgbMeta rgb;
        public Size depth = new Size();
        public Pose pose = new Pose();
        public float speed;
        public CamPose cam = new CamPose();
    }

    [Serializable] public class MapPoint { public float x; public float z; }
    [Serializable] public class MapPath { public MapPoint[] points; }
    [Serializable] public class Obstacle { public float x; public float z; public float r; }
    [Serializable] public class Road { public string id; public string from; public string to; public float width_m; public bool one_way; public MapPoint[] points; }
    [Serializable] public class Site { public string id; public string type; public string name; public float x; public float z; }
    [Serializable] public class Junction { public string id; public float x; public float z; }
    [Serializable] public class PedestrianState { public string id; public float x; public float z; public float yaw; }

    [Serializable]
    public class ActorsMsg
    {
        public string type = "actors";
        public PedestrianState[] pedestrians;
    }

    [Serializable]
    public class LidarHeader
    {
        public string type = "lidar";
        public string truck;
        public int points;
        public string format = "int16cm";
        public Pose pose;
    }

    public static byte[] Encode(string headerJson, byte[] payload)
    {
        var header = Encoding.UTF8.GetBytes(headerJson);
        int plen = payload?.Length ?? 0;
        var msg = new byte[8 + header.Length + plen];
        WriteU32(msg, 0, header.Length);
        WriteU32(msg, 4, plen);
        Buffer.BlockCopy(header, 0, msg, 8, header.Length);
        if (plen > 0) Buffer.BlockCopy(payload, 0, msg, 8 + header.Length, plen);
        return msg;
    }

    static void WriteU32(byte[] b, int off, int v)
    {
        b[off] = (byte)v; b[off + 1] = (byte)(v >> 8); b[off + 2] = (byte)(v >> 16); b[off + 3] = (byte)(v >> 24);
    }
}
