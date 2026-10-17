# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub>=0.25", "ultralytics>=8.3", "onnx>=1.16", "onnxslim>=0.1.40"]
# ///
import argparse
import shutil
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

ORG = "pi-hack-mining"
DEST = Path(__file__).resolve().parents[1] / "models"
PLACEHOLDER = "yolo11n.pt"
IMGSZ = (384, 640)
OPSET = 17
WEIGHTS = (".onnx", ".pt")


def export_onnx(pt: Path, out: Path) -> Path:
    from ultralytics import YOLO

    exported = Path(YOLO(str(pt)).export(format="onnx", imgsz=IMGSZ, opset=OPSET, simplify=True, dynamic=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(exported, out)
    return out


def pick_repo(api: HfApi, repo: str | None) -> str:
    if repo:
        return repo if "/" in repo else f"{ORG}/{repo}"
    models = [m.id for m in api.list_models(author=ORG)]
    if not models:
        raise SystemExit(f"no models published under {ORG} yet; use --placeholder")
    return models[0]


def pick_file(api: HfApi, repo: str, name: str | None) -> str:
    if name:
        return name
    files = [f for f in api.list_repo_files(repo) if f.endswith(WEIGHTS)]
    if not files:
        raise SystemExit(f"{repo} has no {'/'.join(WEIGHTS)} weights")
    return sorted(files, key=lambda f: (not f.endswith(".onnx"), "best" not in f, len(f)))[0]


def fetch_team(repo: str | None, name: str | None, dest: Path) -> Path:
    api = HfApi()
    repo = pick_repo(api, repo)
    name = pick_file(api, repo, name)
    path = Path(hf_hub_download(repo, name))
    out = dest / f"{repo.split('/')[-1]}.onnx"
    if path.suffix == ".onnx":
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)
        return out
    return export_onnx(path, out)


def fetch_placeholder(dest: Path) -> Path:
    work = dest / "cache"
    work.mkdir(parents=True, exist_ok=True)
    from ultralytics import YOLO

    pt = work / PLACEHOLDER
    if not pt.exists():
        shutil.move(YOLO(PLACEHOLDER).ckpt_path, pt)
    return export_onnx(pt, dest / PLACEHOLDER.replace(".pt", ".onnx"))


def main() -> None:
    ap = argparse.ArgumentParser(description="download the detector into models/ as ONNX")
    ap.add_argument("--placeholder", action="store_true", help=f"COCO {PLACEHOLDER} instead of the {ORG} model")
    ap.add_argument("--repo", help=f"Hugging Face repo, default: first model under {ORG}")
    ap.add_argument("--file", help="weights file inside the repo")
    ap.add_argument("--dest", type=Path, default=DEST)
    args = ap.parse_args()
    out = fetch_placeholder(args.dest) if args.placeholder else fetch_team(args.repo, args.file, args.dest)
    print(f"{out}  ->  DETECTOR_MODEL=/app/models/{out.name}")


if __name__ == "__main__":
    main()
