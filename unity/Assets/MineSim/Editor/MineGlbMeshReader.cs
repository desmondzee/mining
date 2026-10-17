using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace MineSim.EditorTools
{
    /// <summary>
    /// Minimal GLB geometry reader (positions + triangle indices only) so the scan collider does not need glTFast.
    /// Coordinates are returned verbatim in glTF/contract axes, node transforms applied. Contract (x, y, z) == Unity
    /// (x, y, z), so the result is used as-is. glTF counter-clockwise front faces read verbatim in Unity's left-handed
    /// space appear clockwise, which is Unity's front face, so indices are kept as-is (reversed only for negative
    /// determinant nodes, per the glTF spec). As a safety net, if most of the area faces down the winding is flipped
    /// (raycasts from depth cameras/lidar only hit front faces).
    /// Unsupported: Draco/meshopt compression, sparse accessors, non-float positions. Those return false.
    /// </summary>
    internal static class MineGlbMeshReader
    {
        private class Doc
        {
            public List<object> nodes, meshes, accessors, bufferViews;
            public byte[] bin;
            public int binOffset;
            public int binLength;
        }

        public static bool TryRead(string path, out List<Vector3> verts, out List<int> tris, out string error)
        {
            verts = new List<Vector3>();
            tris = new List<int>();
            error = null;
            try
            {
                byte[] b = File.ReadAllBytes(path);
                if (b.Length < 20 || BitConverter.ToUInt32(b, 0) != 0x46546C67u) { error = "not a GLB file"; return false; }
                int off = 12;
                string json = null;
                var d = new Doc { bin = b, binOffset = -1 };
                while (off + 8 <= b.Length)
                {
                    int len = BitConverter.ToInt32(b, off);
                    uint type = BitConverter.ToUInt32(b, off + 4);
                    int dataOff = off + 8;
                    if (len < 0 || dataOff + len > b.Length) break;
                    if (type == 0x4E4F534Au) json = Encoding.UTF8.GetString(b, dataOff, len);
                    else if (type == 0x004E4942u && d.binOffset < 0) { d.binOffset = dataOff; d.binLength = len; }
                    off = dataOff + len;
                }
                if (json == null || d.binOffset < 0) { error = "missing JSON or BIN chunk"; return false; }

                var root = MiniJson.Parse(json) as Dictionary<string, object>;
                if (root == null) { error = "bad JSON chunk"; return false; }
                var req = MiniJson.Arr(root, "extensionsRequired");
                if (req != null && req.Count > 0)
                {
                    var names = new List<string>();
                    foreach (var e in req) names.Add(e as string);
                    error = "requires extensions " + string.Join(", ", names);
                    return false;
                }
                d.nodes = MiniJson.Arr(root, "nodes") ?? new List<object>();
                d.meshes = MiniJson.Arr(root, "meshes") ?? new List<object>();
                d.accessors = MiniJson.Arr(root, "accessors") ?? new List<object>();
                d.bufferViews = MiniJson.Arr(root, "bufferViews") ?? new List<object>();

                // root nodes: default scene, else every node that is nobody's child
                var roots = new List<int>();
                var scenes = MiniJson.Arr(root, "scenes");
                int sceneIdx = (int)MiniJson.Num(root, "scene", 0);
                if (scenes != null && sceneIdx >= 0 && sceneIdx < scenes.Count)
                {
                    var sn = MiniJson.Arr(scenes[sceneIdx], "nodes");
                    if (sn != null) foreach (var n in sn) roots.Add(ToInt(n));
                }
                if (roots.Count == 0)
                {
                    var isChild = new bool[d.nodes.Count];
                    foreach (var n in d.nodes)
                    {
                        var ch = MiniJson.Arr(n, "children");
                        if (ch != null) foreach (var c in ch) { int ci = ToInt(c); if (ci >= 0 && ci < isChild.Length) isChild[ci] = true; }
                    }
                    for (int i = 0; i < isChild.Length; i++) if (!isChild[i]) roots.Add(i);
                }

                foreach (int r in roots) Visit(d, r, Matrix4x4.identity, verts, tris, 0);
                if (tris.Count == 0) { error = "no triangle primitives"; return false; }

                // safety net: ground must face up
                double upArea = 0;
                for (int i = 0; i + 2 < tris.Count; i += 3)
                {
                    Vector3 a = verts[tris[i]], bb = verts[tris[i + 1]], c = verts[tris[i + 2]];
                    upArea += Vector3.Cross(bb - a, c - a).y;
                }
                if (upArea < 0)
                {
                    for (int i = 0; i + 2 < tris.Count; i += 3) { int t = tris[i + 1]; tris[i + 1] = tris[i + 2]; tris[i + 2] = t; }
                    Debug.Log("[MineSim] " + Path.GetFileName(path) + ": most triangles faced down; winding flipped so raycasts from above hit.");
                }
                return true;
            }
            catch (Exception e)
            {
                error = e.Message;
                return false;
            }
        }

        private static void Visit(Doc d, int nodeIdx, Matrix4x4 parent, List<Vector3> verts, List<int> tris, int depth)
        {
            if (nodeIdx < 0 || nodeIdx >= d.nodes.Count || depth > 64) return;
            var node = d.nodes[nodeIdx];
            Matrix4x4 m = parent * LocalMatrix(node);
            if (MiniJson.Has(node, "mesh"))
            {
                int mi = (int)MiniJson.Num(node, "mesh", -1);
                if (mi >= 0 && mi < d.meshes.Count) AddMesh(d, d.meshes[mi], m, verts, tris);
            }
            var ch = MiniJson.Arr(node, "children");
            if (ch != null) foreach (var c in ch) Visit(d, ToInt(c), m, verts, tris, depth + 1);
        }

        private static Matrix4x4 LocalMatrix(object node)
        {
            var mat = MiniJson.Arr(node, "matrix");
            if (mat != null && mat.Count == 16)
            {
                var a = MiniJson.Nums(mat);
                var m = new Matrix4x4();
                for (int c = 0; c < 4; c++)
                    for (int r = 0; r < 4; r++)
                        m[r, c] = (float)a[c * 4 + r];   // glTF matrices are column-major
                return m;
            }
            var t = MiniJson.Nums(MiniJson.Arr(node, "translation"));
            var q = MiniJson.Nums(MiniJson.Arr(node, "rotation"));
            var s = MiniJson.Nums(MiniJson.Arr(node, "scale"));
            Vector3 tv = t != null && t.Length >= 3 ? new Vector3((float)t[0], (float)t[1], (float)t[2]) : Vector3.zero;
            Quaternion qv = q != null && q.Length >= 4 ? new Quaternion((float)q[0], (float)q[1], (float)q[2], (float)q[3]) : Quaternion.identity;
            Vector3 sv = s != null && s.Length >= 3 ? new Vector3((float)s[0], (float)s[1], (float)s[2]) : Vector3.one;
            // Numerically identical to glTF's T*R*S (the quaternion->matrix formula does not depend on handedness)
            return Matrix4x4.TRS(tv, Quaternion.Normalize(qv), sv);
        }

        private static void AddMesh(Doc d, object mesh, Matrix4x4 m, List<Vector3> verts, List<int> tris)
        {
            var prims = MiniJson.Arr(mesh, "primitives");
            if (prims == null) return;
            bool flip = m.determinant < 0f;
            foreach (var p in prims)
            {
                int mode = (int)MiniJson.Num(p, "mode", 4);
                if (mode != 4) continue;
                var attrs = MiniJson.Obj(p, "attributes");
                if (attrs == null || !attrs.ContainsKey("POSITION")) continue;
                if (MiniJson.Has(p, "extensions")) throw new Exception("compressed primitives are not supported");
                Vector3[] pos = ReadVec3(d, ToInt(attrs["POSITION"]));
                int baseIdx = verts.Count;
                foreach (var v in pos) verts.Add(m.MultiplyPoint3x4(v));
                int[] idx = MiniJson.Has(p, "indices") ? ReadIndices(d, (int)MiniJson.Num(p, "indices", -1)) : null;
                int n = idx != null ? idx.Length : pos.Length;
                for (int i = 0; i + 2 < n; i += 3)
                {
                    int a = idx != null ? idx[i] : i, b = idx != null ? idx[i + 1] : i + 1, c = idx != null ? idx[i + 2] : i + 2;
                    tris.Add(baseIdx + a);
                    tris.Add(baseIdx + (flip ? c : b));
                    tris.Add(baseIdx + (flip ? b : c));
                }
            }
        }

        private static void Locate(Doc d, object acc, int elemSize, out int start, out int stride, out int count)
        {
            if (MiniJson.Has(acc, "sparse")) throw new Exception("sparse accessors are not supported");
            int bvi = (int)MiniJson.Num(acc, "bufferView", -1);
            if (bvi < 0 || bvi >= d.bufferViews.Count) throw new Exception("accessor without bufferView");
            var bv = d.bufferViews[bvi];
            if ((int)MiniJson.Num(bv, "buffer", 0) != 0) throw new Exception("external buffers are not supported");
            start = d.binOffset + (int)MiniJson.Num(bv, "byteOffset", 0) + (int)MiniJson.Num(acc, "byteOffset", 0);
            stride = (int)MiniJson.Num(bv, "byteStride", 0);
            if (stride <= 0) stride = elemSize;
            count = (int)MiniJson.Num(acc, "count", 0);
            if (count < 0 || start + (long)(count - 1) * stride + elemSize > d.binOffset + d.binLength)
                throw new Exception("accessor out of range");
        }

        private static Vector3[] ReadVec3(Doc d, int ai)
        {
            var acc = d.accessors[ai];
            if ((int)MiniJson.Num(acc, "componentType", 0) != 5126 || MiniJson.Str(acc, "type") != "VEC3")
                throw new Exception("POSITION must be float VEC3");
            Locate(d, acc, 12, out int start, out int stride, out int count);
            var r = new Vector3[count];
            for (int i = 0; i < count; i++)
            {
                int o = start + i * stride;
                r[i] = new Vector3(BitConverter.ToSingle(d.bin, o), BitConverter.ToSingle(d.bin, o + 4), BitConverter.ToSingle(d.bin, o + 8));
            }
            return r;
        }

        private static int[] ReadIndices(Doc d, int ai)
        {
            var acc = d.accessors[ai];
            int ct = (int)MiniJson.Num(acc, "componentType", 0);
            int size = ct == 5125 ? 4 : ct == 5123 ? 2 : ct == 5121 ? 1 : 0;
            if (size == 0) throw new Exception("bad index componentType " + ct);
            Locate(d, acc, size, out int start, out int _, out int count);
            var r = new int[count];
            for (int i = 0; i < count; i++)
            {
                int o = start + i * size;   // index accessors are tightly packed
                r[i] = size == 4 ? (int)BitConverter.ToUInt32(d.bin, o) : size == 2 ? BitConverter.ToUInt16(d.bin, o) : d.bin[o];
            }
            return r;
        }

        private static int ToInt(object o)
        {
            return o is double v ? (int)v : -1;
        }
    }
}
