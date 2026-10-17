import argparse
import io
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

REPO = "microsoft/Microsoft-Rocketbox"
RAW = f"https://raw.githubusercontent.com/{REPO}/master/"
TREE = f"https://api.github.com/repos/{REPO}/git/trees/master?recursive=1"
DEST = Path(__file__).resolve().parents[1] / "unity/Assets/Models/People"
AVATARS = [
    "Construction_Male_01",
    "Construction_Male_02",
    "Construction_Male_03",
    "Construction_Male_04",
    "Construction_Male_05",
    "Construction_Male_06",
    "Construction_Male_07",
    "Construction_Male_08",
    "Construction_Female_01",
    "Male_Adult_01",
    "Female_Adult_01",
    "Security_Male_01",
]
ANIMATIONS = [
    "all_animations_max_motextr_xy/m_walk_neutral_01.max.fbx",
    "all_animations_max_motextr_xy/f_walk_neutral_01.max.fbx",
    "all_animations_max_motextr_static/m_idle_neutral_01.max.fbx",
    "all_animations_max_motextr_static/f_idle_neutral_01.max.fbx",
]
TEXTURE_SIZE = 1024


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(RAW + urllib.request.quote(path), timeout=300) as r:
        return r.read()


def plan(tree: list[str]) -> list[tuple[str, Path]]:
    jobs = [("LICENSE.md", DEST / "LICENSE.md")]
    for name in AVATARS:
        prefix = next(p for p in tree if p.startswith("Assets/Avatars/") and p.split("/")[3] == name)
        root = "/".join(prefix.split("/")[:4])
        jobs.append((f"{root}/Export/{name}.fbx", DEST / name / "Export" / f"{name}.fbx"))
        jobs += [(p, DEST / name / "Textures" / Path(p).name) for p in tree if p.startswith(f"{root}/Textures/")]
    jobs += [(f"Assets/Animations/{a}", DEST / "Animations" / Path(a).name) for a in ANIMATIONS]
    return jobs


def download(job: tuple[str, Path], size: int) -> int:
    src, dest = job
    if dest.exists():
        return dest.stat().st_size
    data = fetch(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix == ".tga":
        img = Image.open(io.BytesIO(data))
        scale = size / max(img.size)
        if scale < 1:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        img.save(dest, compression="tga_rle")
    else:
        dest.write_bytes(data)
    return dest.stat().st_size


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--size", type=int, default=TEXTURE_SIZE)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    with urllib.request.urlopen(TREE, timeout=60) as r:
        tree = [e["path"] for e in json.load(r)["tree"] if e["type"] == "blob"]
    jobs = plan(tree)
    with ThreadPoolExecutor(args.workers) as pool:
        sizes = list(pool.map(lambda j: download(j, args.size), jobs))
    print(f"{len(jobs)} files, {sum(sizes) / 1e6:.0f} MB in {DEST}")


if __name__ == "__main__":
    main()
