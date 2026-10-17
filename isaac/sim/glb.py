import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Material:
    name: str = ""
    color: tuple = (1.0, 1.0, 1.0, 1.0)
    image: int | None = None
    unlit: bool = False
    double_sided: bool = False


@dataclass
class Primitive:
    name: str
    points: np.ndarray
    triangles: np.ndarray
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    colors: np.ndarray | None = None
    material: int | None = None


@dataclass
class Glb:
    primitives: list[Primitive] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    images: list[tuple[bytes, str]] = field(default_factory=list)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        pts = np.concatenate([p.points for p in self.primitives])
        return pts.min(axis=0), pts.max(axis=0)

    def triangles(self) -> tuple[np.ndarray, np.ndarray]:
        points, tris, base = [], [], 0
        for p in self.primitives:
            points.append(p.points)
            tris.append(p.triangles + base)
            base += len(p.points)
        return np.concatenate(points), np.concatenate(tris)


def node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T
    m = np.eye(4)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    m[:3, :3] = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    m[:3, :3] *= np.array(node.get("scale", [1, 1, 1]))[None, :]
    m[:3, 3] = node.get("translation", [0, 0, 0])
    return m


def read_glb(path: str | Path) -> Glb:
    data = Path(path).read_bytes()
    magic, _, _ = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError(f"{path} is not a GLB file")
    offset, gltf, binary = 12, None, b""
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8 : offset + 8 + length]
        if kind == 0x4E4F534A:
            gltf = json.loads(chunk)
        elif kind == 0x004E4942:
            binary = chunk
        offset += 8 + length

    views = gltf.get("bufferViews", [])

    def view_bytes(index: int) -> bytes:
        v = views[index]
        start = v.get("byteOffset", 0)
        return binary[start : start + v["byteLength"]]

    def accessor(index: int) -> np.ndarray:
        a = gltf["accessors"][index]
        dtype = np.dtype(COMPONENTS[a["componentType"]]).newbyteorder("<")
        width, count = WIDTH[a["type"]], a["count"]
        v = views[a["bufferView"]]
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride") or dtype.itemsize * width
        raw = np.frombuffer(binary, np.uint8, (count - 1) * stride + dtype.itemsize * width, start)
        out = np.lib.stride_tricks.as_strided(raw, (count, dtype.itemsize * width), (stride, 1)).copy()
        out = out.view(dtype).reshape(count, width)
        if a.get("normalized") and dtype.kind in "iu":
            out = out.astype(np.float32) / np.iinfo(dtype).max
        return out

    result = Glb()
    for m in gltf.get("materials", []):
        pbr = m.get("pbrMetallicRoughness", {})
        tex = pbr.get("baseColorTexture")
        image = gltf["textures"][tex["index"]].get("source") if tex else None
        result.materials.append(
            Material(
                m.get("name", ""),
                tuple(pbr.get("baseColorFactor", [1, 1, 1, 1])),
                image,
                "KHR_materials_unlit" in m.get("extensions", {}),
                bool(m.get("doubleSided")),
            )
        )
    for img in gltf.get("images", []):
        result.images.append((view_bytes(img["bufferView"]) if "bufferView" in img else b"", img.get("mimeType", "image/png")))

    def visit(index: int, parent: np.ndarray) -> None:
        node = gltf["nodes"][index]
        m = parent @ node_matrix(node)
        if "mesh" in node:
            normal_m = np.linalg.inv(m[:3, :3]).T
            for k, prim in enumerate(gltf["meshes"][node["mesh"]]["primitives"]):
                if prim.get("mode", 4) != 4:
                    continue
                attrs = prim["attributes"]
                pts = accessor(attrs["POSITION"]).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
                idx = accessor(prim["indices"]).reshape(-1) if "indices" in prim else np.arange(len(pts))
                normals = None
                if "NORMAL" in attrs:
                    normals = accessor(attrs["NORMAL"]).astype(np.float64) @ normal_m.T
                    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
                colors = accessor(attrs["COLOR_0"]).astype(np.float32) if "COLOR_0" in attrs else None
                uvs = accessor(attrs["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in attrs else None
                result.primitives.append(
                    Primitive(
                        f"{node.get('name', 'node')}_{k}",
                        pts,
                        idx.astype(np.int64).reshape(-1, 3),
                        normals,
                        uvs,
                        colors,
                        prim.get("material"),
                    )
                )
        for child in node.get("children", []):
            visit(child, m)

    scene = gltf.get("scenes", [{}])[gltf.get("scene", 0)]
    for root in scene.get("nodes", []):
        visit(root, np.eye(4))
    return result


def transform(glb: Glb, matrix: np.ndarray) -> Glb:
    normal_m = np.linalg.inv(matrix[:3, :3]).T
    flip = np.linalg.det(matrix[:3, :3]) < 0
    prims = []
    for p in glb.primitives:
        normals = None
        if p.normals is not None:
            normals = p.normals @ normal_m.T
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        prims.append(
            Primitive(
                p.name,
                p.points @ matrix[:3, :3].T + matrix[:3, 3],
                p.triangles[:, ::-1].copy() if flip else p.triangles,
                normals,
                p.uvs,
                p.colors,
                p.material,
            )
        )
    return Glb(prims, glb.materials, glb.images)
