"""Segment and rig the CC-BY "Excavator digger 3d model" (Dioramass design).

Reads  assets/models/excavator/excavator.glb   (left untouched)
Writes assets/models/excavator/excavator_rigged.glb

Node hierarchy (glTF Y-up, metres, boom reaching toward +Z, cab on +X, same world
placement as the original so existing scale/orientation code keeps working):

    excavator_rigged
      tracks                      static undercarriage (chassis + track chains)
      house   @ slew centre       upper structure: frame, counterweight, cab, seat, glass, hoses
        boom   @ boom foot pin    boom + boom/stick cylinders + hoses along the boom
          stick  @ boom-stick pin stick (dipper) + bucket cylinder + guide links + hose loop
            bucket @ stick-bucket pin  bucket + dog-bone link

Each node's origin is its pin; meshes are re-centred onto it. Rotate boom/stick/bucket
about their local X axis (+ = boom down / stick in / bucket curl), house about local Y.

Segmentation: the source splits meshes by material only. Track chains + chassis are the
undercarriage; glass + seat are cab. The "body" and "hydraulic hose" materials are split into
connected components (vertices welded by position) and each component is classified by its
bounding box along the arm axis (+Z). The two long hoses that run down the boom onto the stick
are cut per triangle at the boom tip. Pins are least-squares circle fits to the rounded ends of
the boom / stick in the side (Z,Y) plane.

Usage: envgen/.venv.nosync/bin/python assets/models/rig_excavator.py [--plot OUT_DIR]
"""
import argparse
import os
import struct

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from pygltflib import GLTF2

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "excavator", "excavator.glb")
DST = os.path.join(HERE, "excavator", "excavator_rigged.glb")
PARTS = ["tracks", "house", "boom", "stick", "bucket"]
COMP_DTYPE = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_accessor(g, blob, idx):
    a = g.accessors[idx]
    bv = g.bufferViews[a.bufferView]
    dt = np.dtype(COMP_DTYPE[a.componentType])
    n = NCOMP[a.type]
    start = (bv.byteOffset or 0) + (a.byteOffset or 0)
    stride = bv.byteStride or dt.itemsize * n
    if stride == dt.itemsize * n:
        arr = np.frombuffer(blob, dt, a.count * n, start).reshape(a.count, n)
    else:
        raw = np.frombuffer(blob, np.uint8, stride * (a.count - 1) + dt.itemsize * n, start)
        arr = np.lib.stride_tricks.as_strided(raw, (a.count, dt.itemsize * n), (stride, 1))
        arr = arr.copy().view(dt).reshape(a.count, n)
    return arr.copy()


def node_matrix(n):
    if n.matrix:
        return np.array(n.matrix, float).reshape(4, 4).T
    M = np.eye(4)
    if n.scale:
        M = np.diag(list(n.scale) + [1]) @ M
    if n.rotation:
        x, y, z, w = n.rotation
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        T = np.eye(4); T[:3, :3] = R; M = T @ M
    if n.translation:
        T = np.eye(4); T[:3, 3] = n.translation; M = T @ M
    return M


def components(pos, tri):
    _, inv = np.unique(np.round(pos, 5), axis=0, return_inverse=True)
    inv = inv.ravel(); ff = inv[tri]; n = inv.max() + 1
    A = sp.coo_matrix((np.ones(ff.size), (ff.ravel(), ff[:, [1, 2, 0]].ravel())), shape=(n, n))
    _, lab = connected_components(A, directed=False)
    return lab[ff[:, 0]]


def fit_circle(p):
    """Kasa least-squares circle through 2D points -> (centre, radius)."""
    A = np.c_[2 * p, np.ones(len(p))]
    b = (p ** 2).sum(1)
    c = np.linalg.lstsq(A, b, rcond=None)[0]
    return c[:2], np.sqrt(c[2] + (c[:2] ** 2).sum())


def classify(matname, pos, tri):
    """Return the part index (into PARTS) for each triangle."""
    if matname in ("chassist_3k", "trackchain"):
        return np.zeros(len(tri), int)
    if matname in ("Material.001", "seat_texture_4k.001"):
        return np.full(len(tri), 1)
    lab = components(pos, tri)
    out = np.empty(len(tri), int)
    cen = pos[tri].mean(1)
    for c in np.unique(lab):
        sel = lab == c
        p = pos[np.unique(tri[sel])]
        zmin, zmax = p[:, 2].min(), p[:, 2].max()
        ymin, ymax = p[:, 1].min(), p[:, 1].max()
        if matname == "body_texture_3k":
            if zmax < 1.6:
                part = "house"           # frame, deck, counterweight
            elif zmin < 0.5:
                part = "boom"
            elif ymin < 1.3 or ymax < 2.2:
                part = "bucket"          # bucket shell, dog-bone link
            else:
                part = "stick"           # stick, guide links
            out[sel] = PARTS.index(part)
        else:  # hose_hidraulic
            if zmax < 1.6:
                out[sel] = PARTS.index("house")
            elif zmin > 4.5:
                out[sel] = PARTS.index("stick")   # bucket cylinder on the stick
            elif zmax > 5.8:
                # long hoses down the boom and looping onto the stick: cut at the boom tip
                out[sel] = np.where(cen[sel, 2] > 5.25, PARTS.index("stick"), PARTS.index("boom"))
            else:
                out[sel] = PARTS.index("boom")    # boom + stick cylinders, boom hoses
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", help="directory for segmentation check PNGs")
    args = ap.parse_args()

    g = GLTF2().load(SRC)
    blob = g.binary_blob()

    # world matrices
    world = {}
    def walk(i, M):
        world[i] = M @ node_matrix(g.nodes[i])
        for c in g.nodes[i].children or []:
            walk(c, world[i])
    for r in g.scenes[g.scene or 0].nodes:
        walk(r, np.eye(4))

    # gather triangles per (part, material) in world space
    buckets = {}   # (part, mat) -> list of dict(attr->array, tri)
    allpos = {p: [] for p in PARTS}
    bodypos = {p: [] for p in PARTS}   # body-material only (pin fitting ignores hoses)
    for ni, M in world.items():
        n = g.nodes[ni]
        if n.mesh is None:
            continue
        R = M[:3, :3]
        for prim in g.meshes[n.mesh].primitives:
            attrs = {k: read_accessor(g, blob, v) for k, v in prim.attributes.__dict__.items() if v is not None}
            attrs["POSITION"] = attrs["POSITION"].astype(float) @ R.T + M[:3, 3]
            if "NORMAL" in attrs:
                nn = attrs["NORMAL"].astype(float) @ R.T
                attrs["NORMAL"] = nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)
            if "TANGENT" in attrs:
                attrs["TANGENT"][:, :3] = attrs["TANGENT"][:, :3] @ R.T
            tri = read_accessor(g, blob, prim.indices).reshape(-1, 3).astype(np.int64)
            matname = g.materials[prim.material].name
            part = classify(matname, attrs["POSITION"], tri)
            for pi, pname in enumerate(PARTS):
                t = tri[part == pi]
                if len(t) == 0:
                    continue
                used, remap = np.unique(t, return_inverse=True)
                sub = {k: v[used] for k, v in attrs.items()}
                buckets.setdefault((pname, prim.material), []).append((sub, remap.reshape(-1, 3)))
                allpos[pname].append(sub["POSITION"])
                if matname == "body_texture_3k":
                    bodypos[pname].append(sub["POSITION"])
    allpos = {k: np.concatenate(v) for k, v in allpos.items()}
    bodypos = {k: np.concatenate(v) for k, v in bodypos.items() if v}

    # ---- pivots (world, glTF Y-up) ----
    tr = allpos["tracks"]
    slew = np.array([0.0, allpos["house"][:, 1].min(), 0.5 * (tr[:, 2].min() + tr[:, 2].max())])
    bp, stick = bodypos["boom"], bodypos["stick"]
    # boom foot: rounded rear end of the boom plate
    foot_pts = bp[bp[:, 2] < bp[:, 2].min() + 0.12][:, [2, 1]]
    tip_pts = bp[(bp[:, 2] > 5.0) & (bp[:, 2] < 5.34) & (np.abs(bp[:, 1] - 5.0) < 0.2)][:, [2, 1]]
    sp_ = stick[np.abs(stick[:, 0]) < 0.3]
    bot_pts = sp_[(sp_[:, 1] < sp_[:, 1].min() + 0.12) & (sp_[:, 2] < 5.6)][:, [2, 1]]
    (fz, fy), fr = fit_circle(foot_pts)
    (tz, ty), trr = fit_circle(tip_pts)
    (kz, ky), kr = fit_circle(bot_pts)
    foot = np.array([0.0, fy, fz]); tip = np.array([0.0, ty, tz]); bk = np.array([0.0, ky, kz])
    print(f"slew centre      {slew.round(3)}")
    print(f"boom foot pin    {foot.round(3)}  r={fr:.2f}")
    print(f"boom-stick pin   {tip.round(3)}  r={trr:.2f}")
    print(f"stick-bucket pin {bk.round(3)}  r={kr:.2f}")
    origin = {"tracks": np.zeros(3), "house": slew, "boom": foot, "stick": tip, "bucket": bk}
    parent = {"tracks": None, "house": None, "boom": "house", "stick": "boom", "bucket": "stick"}

    # ---- build output glTF ----
    out = bytearray()
    bufferViews, accessors = [], []

    def add_view(data, target=None):
        while len(out) % 4:
            out.append(0)
        off = len(out); out.extend(data)
        bv = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            bv["target"] = target
        bufferViews.append(bv)
        return len(bufferViews) - 1

    def add_acc(arr, typ, target):
        arr = np.ascontiguousarray(arr)
        ct = 5125 if arr.dtype == np.uint32 else 5126
        acc = {"bufferView": add_view(arr.tobytes(), target), "componentType": ct,
               "count": int(arr.shape[0]), "type": typ}
        if typ == "VEC3" and ct == 5126:
            acc["min"] = arr.min(0).tolist(); acc["max"] = arr.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    images = []
    for im in g.images:
        bv = g.bufferViews[im.bufferView]
        data = blob[(bv.byteOffset or 0):(bv.byteOffset or 0) + bv.byteLength]
        images.append({"bufferView": add_view(data), "mimeType": im.mimeType})

    typ = {"POSITION": "VEC3", "NORMAL": "VEC3", "TANGENT": "VEC4", "TEXCOORD_0": "VEC2", "TEXCOORD_1": "VEC2"}
    meshes, nodes = [], []
    node_index = {}
    for pname in PARTS:
        prims = []
        for (pp, mat), chunks in sorted(buckets.items(), key=lambda kv: kv[0][1]):
            if pp != pname:
                continue
            offs, attrs, tris = 0, {}, []
            for sub, t in chunks:
                for k, v in sub.items():
                    attrs.setdefault(k, []).append(v)
                tris.append(t + offs); offs += len(sub["POSITION"])
            attrs = {k: np.concatenate(v) for k, v in attrs.items()}
            attrs["POSITION"] = attrs["POSITION"] - origin[pname]
            pa = {k: add_acc(v.astype(np.float32), typ[k], 34962) for k, v in attrs.items()}
            ind = add_acc(np.concatenate(tris).astype(np.uint32).ravel(), "SCALAR", 34963)
            prims.append({"attributes": pa, "indices": ind, "material": mat, "mode": 4})
        meshes.append({"name": pname, "primitives": prims})
        par = parent[pname]
        local = origin[pname] - (origin[par] if par else 0)
        node_index[pname] = len(nodes)
        nodes.append({"name": pname, "mesh": len(meshes) - 1, "translation": [float(x) for x in local]})
    for pname in PARTS:
        kids = [node_index[c] for c, p in parent.items() if p == pname]
        if kids:
            nodes[node_index[pname]]["children"] = kids
    root = len(nodes)
    nodes.append({"name": "excavator_rigged",
                  "children": [node_index[p] for p in PARTS if parent[p] is None]})

    gj = {
        "asset": {"version": "2.0", "generator": "rig_excavator.py",
                  "extras": dict(g.asset.extras or {}, modified="segmented and rigged (house/boom/stick/bucket/tracks)")},
        "scene": 0, "scenes": [{"nodes": [root]}], "nodes": nodes, "meshes": meshes,
        "materials": [m.to_dict() for m in g.materials],
        "textures": [t.to_dict() for t in g.textures],
        "samplers": [s.to_dict() for s in g.samplers],
        "images": images, "accessors": accessors, "bufferViews": bufferViews,
        "buffers": [{"byteLength": len(out)}],
    }
    import json
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if v is not None and v != {} and v != []}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o
    js = json.dumps(clean(gj), separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    while len(out) % 4:
        out.append(0)
    glb = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(out))
    glb += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    with open(DST, "wb") as f:
        f.write(glb)
    print(f"wrote {DST} ({len(glb) / 1e6:.1f} MB)")

    if args.plot:
        plot(args.plot, allpos, origin)


def rotx(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def plot(d, allpos, origin):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    os.makedirs(d, exist_ok=True)
    col = dict(tracks="#777777", house="#e0a020", boom="#d03030", stick="#3060d0", bucket="#20a050")

    def posed(boomA, stickA, bucketA):
        res = {}
        for p in PARTS:
            P = allpos[p][::4]
            chain = {"boom": ["boom"], "stick": ["boom", "stick"], "bucket": ["boom", "stick", "bucket"]}.get(p, [])
            ang = {"boom": boomA, "stick": stickA, "bucket": bucketA}
            for j in reversed(chain):
                P = (P - origin[j]) @ rotx(np.radians(ang[j])).T + origin[j]
            res[p] = P
        return res

    fig, ax = plt.subplots(2, 1, figsize=(12, 11))
    for p, P in allpos.items():
        P = P[::3]
        ax[0].scatter(P[:, 2], P[:, 1], s=0.5, c=col[p], label=p)
        ax[1].scatter(P[:, 2], P[:, 0], s=0.5, c=col[p])
    for k, o in origin.items():
        ax[0].plot(o[2], o[1], "k+", ms=14, mew=2); ax[0].annotate(k, (o[2], o[1]), xytext=(5, 5), textcoords="offset points")
    ax[0].set_title("side (Z fwd, Y up) - segmentation + pins"); ax[1].set_title("top (Z fwd, X)")
    for a in ax:
        a.set_aspect("equal"); a.grid(alpha=0.3)
    ax[0].legend(markerscale=12)
    fig.savefig(os.path.join(d, "excavator_segmentation.png"), dpi=70)

    poses = [("rest", 0, 0, 0), ("reach", 20, -50, -85),
             ("dig (stick in, curl)", 14, -12, -10), ("lifted", -14, -5, 0), ("dump", -12, -35, -110)]
    fig, axs = plt.subplots(1, len(poses), figsize=(5 * len(poses), 5))
    for a, (name, b, s_, k) in zip(axs, poses):
        for p, P in posed(b, s_, k).items():
            a.scatter(P[:, 2], P[:, 1], s=0.3, c=col[p])
        a.axhline(0, c="k", lw=0.5); a.set_aspect("equal"); a.set_xlim(-4, 11); a.set_ylim(-3, 9)
        a.set_title(f"{name}\nboom {b} stick {s_} bucket {k}", fontsize=9)
    fig.savefig(os.path.join(d, "excavator_poses.png"), dpi=60)


if __name__ == "__main__":
    main()
