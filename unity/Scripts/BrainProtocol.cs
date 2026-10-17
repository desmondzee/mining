// Message framing shared with brain/brain/protocol.py:
//   u32 header_len | u32 payload_len | header (UTF-8 JSON) | payload
// Little-endian.

using System;
using System.Text;

public static class BrainProtocol
{
    [Serializable] public class Size { public int w; public int h; }
    [Serializable] public class RgbMeta { public int w; public int h; public bool flip; public int channels = 4; }
    [Serializable] public class Pose { public float x; public float z; public float yaw; }

    [Serializable]
    public class FrameHeader
    {
        public string type = "frame";
        public string camera;
        public string truck = "";  // empty for non-truck cameras (e.g. overview)
        public RgbMeta rgb;
        public Size depth = new Size();  // w = h = 0 means no depth
        public Pose pose = new Pose();
        public float speed;
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
