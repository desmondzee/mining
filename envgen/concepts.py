"""Batch concept images for Marble, generated in parallel with the OpenAI image API.

    python -m envgen.concepts                 # all variants -> data/concepts/<name>.png
    python -m envgen.concepts --only rim dusk --n 2

Then feed the best one to Marble:
    python -m envgen.worldlabs --image data/concepts/rim_1.png --prompt "open-pit copper mine" --id marble_pit --draft
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import common as C

# Marble works best from an oblique, near-ground viewpoint with a visible ground plane and clear depth.
COMMON = ("Photorealistic photo, strong sense of depth, clear flat ground plane in the foreground, "
          "no people, no text, no watermark.")
VARIANTS = {
    "rim": "Large open-pit copper mine seen from a gravel pad on the pit rim, about 10 m above ground, looking "
           "across the pit. Terraced benches with steep rock faces, a wide haul road spiralling down the walls, "
           "pit floor visible. Ochre and grey rock with green copper-oxide streaks, arid landscape, clear daylight.",
    "ramp": "Eye-level view standing on a wide gravel haul road partway down an open-pit mine, the road curving "
            "down along terraced benches, rock wall on one side and a bench drop on the other, the far pit wall "
            "with layered benches in the distance. Dusty, midday sun.",
    "floor": "View from the floor of a deep open-pit copper mine at about 5 m height, terraced benches rising "
             "on all sides, a loading area with broken ore piles in the foreground, a haul road climbing the wall.",
    "dusk": "Open-pit copper mine from the rim at golden hour, long shadows across terraced benches, dust haze, "
            "haul road lit by low sun, crusher building and conveyor on the near rim.",
    "flooded": "Open-pit mine from the rim after heavy rain, the lowest benches flooded with muddy brown water, "
               "wet dark rock faces, overcast sky, haul road leading down to the water's edge.",
    "cab": "View from the driver's cab height of a huge mining haul truck, about 6 m above a wide gravel haul "
           "road at an open-pit copper mine, road stretching ahead toward the pit rim, terraced benches beyond.",
}


def load_env():
    """Pick up keys without overriding the real environment. Precedence: env vars, then secret files
    (one file per key, e.g. `wlt_api_key`, in /run/secrets or deploy/secrets), then deploy/.env."""
    for d in (Path("/run/secrets"), C.REPO / "deploy" / "secrets"):
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and not f.name.startswith(".") and f.suffix == "" and f.read_text().strip():
                    os.environ.setdefault(f.name.upper(), f.read_text().strip())
    for p in (C.REPO / "deploy" / ".env", C.REPO / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                k, sep, v = line.partition("=")
                if sep and not line.lstrip().startswith("#") and v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


def one(name: str, idx: int, out: Path) -> str:
    import requests
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    r = requests.post(f"{base}/images/generations", timeout=300,
                      headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                      json={"model": model, "prompt": f"{VARIANTS[name]} {COMMON}", "size": "1536x1024", "n": 1})
    if r.status_code >= 400:
        return f"{name}_{idx}: FAILED {r.status_code} {r.text[:200]}"
    d = r.json()["data"][0]
    path = out / f"{name}_{idx}.png"
    if d.get("b64_json"):
        path.write_bytes(base64.b64decode(d["b64_json"]))
    else:
        path.write_bytes(requests.get(d["url"], timeout=120).content)
    return f"{name}_{idx}: {path}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", choices=list(VARIANTS), help="subset of variants")
    ap.add_argument("--n", type=int, default=1, help="images per variant")
    a = ap.parse_args()
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (put it in deploy/.env)")
    out = C.DATA_DIR / "concepts"
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(v, i) for v in (a.only or VARIANTS) for i in range(1, a.n + 1)]
    print(f"generating {len(jobs)} concept images in parallel ...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for msg in ex.map(lambda j: one(*j, out), jobs):
            print(" ", msg)


if __name__ == "__main__":
    main()
