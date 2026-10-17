"""Recolour the untextured CAT 785C GLB into CAT livery by part name. Edits only the glTF JSON chunk.

    python3 assets/models/recolor_truck.py assets/models/cat_785c/cat_785c_source.glb assets/models/cat_785c/cat_785c.glb
"""
import json
import struct
import sys

# name -> (baseColor RGBA linear, metallic, roughness)
PALETTE = {
    "cat_yellow":  ([0.80, 0.36, 0.0, 1.0], 0.0, 0.55),
    "chassis":     ([0.05, 0.05, 0.05, 1.0], 0.3, 0.6),
    "steel":       ([0.45, 0.45, 0.45, 1.0], 0.9, 0.35),
    "rubber":      ([0.02, 0.02, 0.02, 1.0], 0.0, 0.9),
    "grille":      ([0.01, 0.01, 0.01, 1.0], 0.2, 0.7),
    "headlight":   ([1.0, 0.97, 0.88, 1.0], 0.0, 0.1),
}
# first matching keyword (lower-case substring of the node's parent name) wins; default is cat_yellow
RULES = [
    (("tire",), "rubber"),
    (("grid", "honeycomb"), "grille"),
    (("faru",), "headlight"),
    (("cylinder", "cilender", "cross joint", "torus", "trubs", "exhaust", "provod"), "steel"),
    (("balks", "support", "fastening", "hoks", "krep", "podon", "nurbs", "bezier", "join"), "chassis"),
]
KEEP = {"Window"}  # materials left untouched


def main(src, dst):
    b = bytearray(open(src, "rb").read())
    jlen = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jlen])
    rest = bytes(b[20 + jlen:])

    base = len(j["materials"])
    ids = {}
    for i, (name, (rgba, metal, rough)) in enumerate(PALETTE.items()):
        j["materials"].append({"name": name, "doubleSided": True, "pbrMetallicRoughness": {
            "baseColorFactor": rgba, "metallicFactor": metal, "roughnessFactor": rough}})
        ids[name] = base + i
    j["materials"][ids["headlight"]]["emissiveFactor"] = [1.0, 0.95, 0.8]

    parent = {c: i for i, nd in enumerate(j["nodes"]) for c in nd.get("children", [])}
    counts = {}
    for i, nd in enumerate(j["nodes"]):
        if "mesh" not in nd:
            continue
        label = (j["nodes"][parent[i]].get("name", "") if i in parent else nd.get("name", "")).lower()
        choice = next((m for keys, m in RULES if any(k in label for k in keys)), "cat_yellow")
        for prim in j["meshes"][nd["mesh"]]["primitives"]:
            old = j["materials"][prim["material"]].get("name") if "material" in prim else None
            if old in KEEP:
                continue
            prim["material"] = ids[choice]
            counts[choice] = counts.get(choice, 0) + 1

    data = json.dumps(j, separators=(",", ":")).encode()
    data += b" " * (-len(data) % 4)
    out = b[:12] + struct.pack("<I", len(data)) + b"JSON" + data + rest
    out[8:12] = struct.pack("<I", len(out))
    open(dst, "wb").write(out)
    print("recoloured primitives:", counts)


if __name__ == "__main__":
    main(*sys.argv[1:3])
