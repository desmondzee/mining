# %% [markdown]
# # Welcome to Modal notebooks!
# 
# Write Python code and collaborate in real time. Your code runs in Modal's
# **serverless cloud**, and anyone in the same workspace can join.
# 
# This notebook comes with some common Python libraries installed. Run
# cells with `Shift+Enter`.

# %%
%uv pip install -q -U ultralytics pandas matplotlib pyyaml pillow

# %%
%uv pip install --force-reinstall --no-deps torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu129

# %%
%uv pip install --no-cache-dir --force-reinstall --no-deps "torchvision==0.23.0+cu129" --index-url https://download.pytorch.org/whl/cu129

# %%
import torch
import torchvision
from torchvision.ops import nms

print("PyTorch:", torch.__version__)
print("torchvision:", torchvision.__version__)

assert torch.cuda.is_available(), "CUDA is unavailable."
print("GPU:", torch.cuda.get_device_name(0))

for device in ["cpu", "cuda"]:
    boxes = torch.tensor(
        [
            [0, 0, 100, 100],
            [5, 5, 95, 95],
            [200, 200, 300, 300],
        ],
        dtype=torch.float32,
        device=device,
    )

    scores = torch.tensor(
        [0.9, 0.8, 0.7],
        dtype=torch.float32,
        device=device,
    )

    result = nms(boxes, scores, iou_threshold=0.5)

    assert result.cpu().tolist() == [0, 2]
    print(f"✅ NMS works on {device}")

# %%
import sys
import torch
from importlib.metadata import version

print("Python:", sys.executable)
print("Loaded torch:", torch.__version__)
print("Installed torch:", version("torch"))
print("Installed torchvision:", version("torchvision"))
print("CUDA build:", torch.version.cuda)

# %%
ROOT = Path("/root/mining_cv")
DATASET_ROOT = ROOT / "dataset"
RUNS_ROOT = ROOT / "runs"

RUNS_ROOT.mkdir(parents=True, exist_ok=True)

# EXAMPLE ORDER ONLY.
# Replace with the exact class-ID order supplied with your annotations.
from pathlib import Path
from collections import Counter
import subprocess
import json
import uuid

REPO_URL = "https://github.com/desmondzee/mining.git"
ROOT = Path("/root/mining_cv")
ROOT.mkdir(parents=True, exist_ok=True)

CHECKOUT = ROOT / f"unity_repo_audit_{uuid.uuid4().hex[:8]}"

def git(*args):
    return subprocess.check_output(
        ["git", *args],
        text=True,
        stderr=subprocess.STDOUT,
    )

# Resolve the branch/path ambiguity in the GitHub URL.
remote_heads = git("ls-remote", "--heads", REPO_URL)

heads = {
    line.split("\t", 1)[1].removeprefix("refs/heads/")
    for line in remote_heads.splitlines()
    if "\t" in line
}

if "vato/environment" in heads:
    branch = "vato/environment"
    relative_scope = Path(".")
elif "vato" in heads:
    branch = "vato"
    relative_scope = Path("environment")
else:
    raise RuntimeError(
        "Neither 'vato/environment' nor 'vato' exists as a branch.\n"
        f"Available branches: {sorted(heads)}"
    )

print("Cloning branch:", branch)

subprocess.run(
    [
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        "--branch", branch,
        REPO_URL,
        str(CHECKOUT),
    ],
    check=True,
)

SCOPE = CHECKOUT / relative_scope

if not SCOPE.is_dir():
    raise FileNotFoundError(
        f"The URL resolved to a directory that does not exist: {SCOPE}"
    )

print("Repository:", CHECKOUT)
print("Selected directory:", SCOPE)
print("Commit:", git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip())

# Inspect the whole checkout: data-recording code may be outside the
# directory selected in the GitHub URL.
files = sorted(
    path for path in CHECKOUT.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(CHECKOUT).parts
)

def relative(path):
    return str(path.relative_to(CHECKOUT))

image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
video_extensions = {".mp4", ".avi", ".mov", ".mkv"}

images = [
    path for path in files
    if path.suffix.lower() in image_extensions
]
videos = [
    path for path in files
    if path.suffix.lower() in video_extensions
]
json_files = [
    path for path in files
    if path.suffix.lower() == ".json"
]
yaml_files = [
    path for path in files
    if path.suffix.lower() in {".yaml", ".yml"}
]

print("\nFile extension counts:")
print(Counter(path.suffix.lower() for path in files).most_common(20))

print("\nImages:", len(images))
print("Videos:", len(videos))
print("JSON files:", len(json_files))
print("YAML files:", len(yaml_files))

print("\nExample image paths — these could be assets, not camera frames:")
for path in images[:20]:
    print(relative(path))

print("\nPossible dataset/configuration files:")
for path in (json_files + yaml_files)[:40]:
    print(relative(path))

keywords = (
    "capture", "record", "dataset", "annotation", "bounding",
    "camera", "sensor", "protocol", "fakesim", "spec",
)

relevant_code = [
    path for path in files
    if path.suffix.lower() in {".py", ".cs", ".md"}
    and any(word in path.name.lower() for word in keywords)
]

print("\nRelevant source/documentation files:")
for path in relevant_code[:60]:
    print(relative(path))

# Look for already-exported YOLO labels.
def looks_like_yolo_label(path):
    if path.suffix.lower() != ".txt" or path.stat().st_size > 1_000_000:
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return False

        for line in lines:
            parts = line.split()
            if len(parts) != 5:
                return False

            class_id = int(parts[0])
            coordinates = [float(value) for value in parts[1:]]

            if class_id < 0 or not all(0 <= value <= 1 for value in coordinates):
                return False

        return True

    except (ValueError, UnicodeError):
        return False

yolo_candidates = [
    path for path in files if looks_like_yolo_label(path)
]

print("\nPossible YOLO label files:", len(yolo_candidates))
for path in yolo_candidates[:10]:
    print(relative(path))
    print(path.read_text()[:500])

# Display the project specification if present.
spec_files = [path for path in files if path.name == "spec.md"]

for path in spec_files[:2]:
    print(f"\n--- {relative(path)} ---")
    print(path.read_text(errors="replace")[:12000])

report = {
    "checkout": str(CHECKOUT),
    "branch": branch,
    "commit": git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip(),
    "images": [relative(path) for path in images],
    "videos": [relative(path) for path in videos],
    "json_files": [relative(path) for path in json_files],
    "yaml_files": [relative(path) for path in yaml_files],
    "relevant_code": [relative(path) for path in relevant_code],
    "possible_yolo_labels": [relative(path) for path in yolo_candidates],
}

REPORT_PATH = ROOT / "unity_repo_audit.json"
REPORT_PATH.write_text(json.dumps(report, indent=2))

print("\n✅ Audit saved:", REPORT_PATH)

MODEL_WEIGHTS = "yolo26n.pt"

IMAGE_SIZE = 640
BATCH_SIZE = 8
EPOCHS = 60
SEED = 42

DATA_YAML = ROOT / "mining.yaml"

config = {
    "path": str(DATASET_ROOT),
    "train": "images/train",
    "val": "images/val",
    "test": "images/test",
    "names": dict(enumerate(CLASS_NAMES)),
}

DATA_YAML.write_text(yaml.safe_dump(config, sort_keys=False))

print(DATA_YAML.read_text())

# %%
import modal

volume = modal.Volume.from_name(
    "omod",
    environment_name="main",
)

entries = list(volume.listdir("/"))

print("Remote volume contents:")
for entry in entries:
    print(f"{entry.path} — {entry.size:,} bytes")

# %%
from pathlib import Path
import shutil
import zipfile

ZIP_PATH = Path("/root/mining_cv/dataset.zip")
ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)

entry = next(
    (
        item for item in entries
        if item.path.lstrip("/") == "dataset.zip"
    ),
    None,
)

if entry is None:
    raise RuntimeError(
        "dataset.zip was not found through the API. "
        "Check the notebook workspace and the 'main' environment."
    )

expected_bytes = entry.size
temporary_path = ZIP_PATH.with_suffix(".zip.part")

if (
    ZIP_PATH.is_file()
    and ZIP_PATH.stat().st_size == expected_bytes
    and zipfile.is_zipfile(ZIP_PATH)
):
    print("Existing local ZIP found; skipping download.")
else:
    # Remove any incomplete copy from an earlier attempt.
    temporary_path.unlink(missing_ok=True)

    free_bytes = shutil.disk_usage(ZIP_PATH.parent).free
    if free_bytes < expected_bytes:
        raise RuntimeError("Insufficient local disk space for the ZIP.")

    downloaded = 0
    next_report = 100 * 1024**2

    with temporary_path.open("wb") as destination:
        for chunk in volume.read_file(entry.path):
            destination.write(chunk)
            downloaded += len(chunk)

            if downloaded >= next_report:
                print(
                    f"{downloaded / 1024**2:.0f} / "
                    f"{expected_bytes / 1024**2:.0f} MiB"
                )
                next_report = downloaded + 100 * 1024**2

    if downloaded != expected_bytes:
        raise RuntimeError(
            f"Incomplete copy: {downloaded} of {expected_bytes} bytes."
        )

    if not zipfile.is_zipfile(temporary_path):
        raise RuntimeError("Downloaded file is not a recognized ZIP.")

    temporary_path.replace(ZIP_PATH)

print("✅ Dataset available:", ZIP_PATH)
print(f"Size: {ZIP_PATH.stat().st_size / 1024**3:.2f} GiB")

# %%
with zipfile.ZipFile(ZIP_PATH) as archive:
    files = [
        item for item in archive.infolist()
        if not item.is_dir()
        and not item.filename.startswith("__MACOSX/")
    ]

    print("Files:", len(files))
    print(
        "Uncompressed size:",
        f"{sum(item.file_size for item in files) / 1024**3:.2f} GiB",
    )

    for item in files[:30]:
        print(item.filename)

# %%
from pathlib import Path, PurePosixPath
from collections import Counter, defaultdict
import json
import zipfile
import pandas as pd

ZIP_PATH = Path("/root/mining_cv/dataset.zip")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

label_counts = Counter()
shape_counts = Counter()
dimension_counts = Counter()

matched_pairs = []
pairing_problems = []
annotation_errors = []
other_json_files = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    members = [
        item.filename
        for item in archive.infolist()
        if not item.is_dir()
        and "__MACOSX" not in PurePosixPath(item.filename).parts
        and not PurePosixPath(item.filename).name.startswith("._")
    ]

    image_members = [
        name for name in members
        if PurePosixPath(name).suffix.lower() in IMAGE_EXTENSIONS
    ]

    json_members = [
        name for name in members
        if PurePosixPath(name).suffix.lower() == ".json"
    ]

    # Pair by filename stem only when the match is unique.
    images_by_stem = defaultdict(list)
    for name in image_members:
        images_by_stem[PurePosixPath(name).stem].append(name)

    empty_annotations = 0
    labelme_count = 0

    for annotation_name in json_members:
        try:
            annotation = json.loads(archive.read(annotation_name))

            if (
                not isinstance(annotation, dict)
                or not isinstance(annotation.get("shapes"), list)
            ):
                other_json_files.append(annotation_name)
                continue

            labelme_count += 1
            shapes = annotation["shapes"]

            if not shapes:
                empty_annotations += 1

            dimension_counts[(
                annotation.get("imageWidth"),
                annotation.get("imageHeight"),
            )] += 1

            for shape in shapes:
                label_counts[str(shape.get("label"))] += 1
                shape_counts[str(shape.get("shape_type"))] += 1

            candidates = images_by_stem[
                PurePosixPath(annotation_name).stem
            ]

            if len(candidates) == 1:
                matched_pairs.append({
                    "image": candidates[0],
                    "annotation": annotation_name,
                })
            else:
                pairing_problems.append({
                    "annotation": annotation_name,
                    "candidate_images": candidates,
                })

        except Exception as exc:
            annotation_errors.append({
                "annotation": annotation_name,
                "error": str(exc),
            })

    paired_images = {pair["image"] for pair in matched_pairs}
    unpaired_images = sorted(set(image_members) - paired_images)

    metadata_members = [
        name for name in members
        if PurePosixPath(name).suffix.lower()
        in {".txt", ".md", ".yaml", ".yml", ".csv"}
    ]

print("Images:", len(image_members))
print("JSON files:", len(json_members))
print("LabelMe annotations:", labelme_count)
print("Matched image/annotation pairs:", len(matched_pairs))
print("Empty annotations:", empty_annotations)
print("Unpaired images:", len(unpaired_images))
print("Pairing problems:", len(pairing_problems))
print("Annotation errors:", len(annotation_errors))

print("\nShape types:", dict(shape_counts))
print("Annotation dimensions:", dict(dimension_counts))

display(pd.DataFrame(
    sorted(label_counts.items()),
    columns=["source_label", "instances"],
))

print("\nMetadata / possible split files:")
for name in metadata_members[:40]:
    print(name)

print("\nFirst matched pairs:")
for pair in matched_pairs[:3]:
    print(pair)

if pairing_problems:
    print("\nExample pairing problems:")
    print(pairing_problems[:3])

if annotation_errors:
    print("\nExample annotation errors:")
    print(annotation_errors[:3])

if other_json_files:
    print("\nOther JSON files:", other_json_files[:10])

# %%
import io
import random
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

assert matched_pairs, "No unique image/annotation pairs found."

selected_pairs = random.Random(42).sample(
    matched_pairs,
    min(6, len(matched_pairs)),
)

fig, axes = plt.subplots(3, 2, figsize=(18, 24))
axes = axes.ravel()

with zipfile.ZipFile(ZIP_PATH) as archive:
    for ax, pair in zip(axes, selected_pairs):
        image = Image.open(
            io.BytesIO(archive.read(pair["image"]))
        ).convert("RGB")

        annotation = json.loads(
            archive.read(pair["annotation"])
        )

        actual_size = image.size
        declared_size = (
            annotation.get("imageWidth"),
            annotation.get("imageHeight"),
        )

        # Avoid silently overlaying coordinates on a differently sized image.
        if actual_size != declared_size:
            raise ValueError(
                f"Size mismatch for {pair['image']}: "
                f"image={actual_size}, annotation={declared_size}"
            )

        ax.imshow(image)

        for shape in annotation["shapes"]:
            if shape.get("shape_type") != "rectangle":
                continue

            points = np.asarray(shape["points"], dtype=float)

            if points.shape != (2, 2) or not np.isfinite(points).all():
                raise ValueError(
                    f"Invalid rectangle in {pair['annotation']}"
                )

            x1, y1 = points.min(axis=0)
            x2, y2 = points.max(axis=0)

            ax.add_patch(Rectangle(
                (x1, y1),
                x2 - x1,
                y2 - y1,
                fill=False,
                edgecolor="red",
                linewidth=2,
            ))

            ax.text(
                x1,
                max(0, y1 - 5),
                f"label={shape['label']}",
                color="white",
                fontsize=10,
                bbox={"facecolor": "red", "alpha": 0.75, "pad": 2},
            )

        ax.set_title(PurePosixPath(pair["image"]).name)
        ax.axis("off")

for ax in axes[len(selected_pairs):]:
    ax.axis("off")

plt.tight_layout()
plt.show()

# %%
from collections import Counter
import json
import zipfile

label_counts = Counter()
label_examples = {}

with zipfile.ZipFile(ZIP_PATH) as archive:
    for pair in matched_pairs:
        annotation = json.loads(
            archive.read(pair["annotation"])
        )

        labels_in_image = set()

        for shape in annotation["shapes"]:
            # Keep source labels unchanged during the audit.
            label = str(shape["label"])

            label_counts[label] += 1
            labels_in_image.add(label)

        for label in labels_in_image:
            label_examples.setdefault(label, []).append(pair)

print("Source labels and instance counts:")
for label, count in sorted(label_counts.items()):
    print(f"{label!r}: {count}")

print("\nExample annotations containing 'n':")
for pair in label_examples.get("n", [])[:5]:
    print(pair["annotation"])

# %%
import io
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

examples = label_examples.get("n", [])[:4]

if not examples:
    print("No exact 'n' labels found.")
else:
    fig, axes = plt.subplots(
        len(examples), 1,
        figsize=(12, 10 * len(examples)),
        squeeze=False,
    )

    with zipfile.ZipFile(ZIP_PATH) as archive:
        for ax, pair in zip(axes[:, 0], examples):
            image = Image.open(
                io.BytesIO(archive.read(pair["image"]))
            ).convert("RGB")

            annotation = json.loads(
                archive.read(pair["annotation"])
            )

            assert image.size == (
                annotation["imageWidth"],
                annotation["imageHeight"],
            ), f"Image/annotation size mismatch: {pair['image']}"

            ax.imshow(image)

            for shape in annotation["shapes"]:
                if shape.get("shape_type") != "rectangle":
                    continue

                label = str(shape["label"])
                points = np.asarray(shape["points"], dtype=float)

                if points.shape != (2, 2):
                    raise ValueError(f"Invalid rectangle: {pair['annotation']}")

                x1, y1 = points.min(axis=0)
                x2, y2 = points.max(axis=0)

                color = "yellow" if label == "n" else "red"

                ax.add_patch(Rectangle(
                    (x1, y1),
                    x2 - x1,
                    y2 - y1,
                    fill=False,
                    edgecolor=color,
                    linewidth=3,
                ))

                ax.text(
                    x1, max(0, y1 - 5),
                    repr(label),
                    color="black",
                    bbox={"facecolor": color, "alpha": 0.85},
                )

            ax.set_title(pair["image"])
            ax.axis("off")

    plt.tight_layout()
    plt.show()

# %%
source_labels = sorted(label_counts)

SOURCE_LABEL_TO_ID = {
    label: index
    for index, label in enumerate(source_labels)
}

CLASS_NAMES = {
    index: f"source_label_{label}"
    for label, index in SOURCE_LABEL_TO_ID.items()
}

print("Mapping:", SOURCE_LABEL_TO_ID)
print("Class names:", CLASS_NAMES)

# %%
import json
import zipfile
import pandas as pd

clean_pairs = []
quarantined = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    for pair in matched_pairs:
        annotation = json.loads(
            archive.read(pair["annotation"])
        )

        labels = {
            str(shape["label"])
            for shape in annotation["shapes"]
        }

        if labels == {"1"}:
            clean_pairs.append(pair)
        else:
            quarantined.append({
                "image": pair["image"],
                "annotation": pair["annotation"],
                "source_labels": json.dumps(
                    sorted(labels), ensure_ascii=False
                ),
            })

print("Images containing only label '1':", len(clean_pairs))
print("Images requiring review:", len(quarantined))

print("\nInstance counts by source label:")
for label, count in sorted(label_counts.items()):
    print(f"{label!r}: {count}")

review_path = Path("/root/mining_cv/annotation_review.csv")
review_path.parent.mkdir(parents=True, exist_ok=True)

pd.DataFrame(
    quarantined,
    columns=["image", "annotation", "source_labels"],
).to_csv(review_path, index=False)

print("\nReview list saved:", review_path)

# %%
SOURCE_LABEL_TO_ID = {"1": 0}

# Temporary name: official semantic definition still needs confirmation.
CLASS_NAMES = {0: "source_label_1"}

assert len(clean_pairs) >= 500, (
    f"Only {len(clean_pairs)} clean images available. "
    "Reduce the smoke-test sample size."
)

selected = random.Random(42).sample(clean_pairs, 500)

splits = {
    "train": selected[:400],
    "val": selected[400:],
}

# %%
from pathlib import Path
from PIL import Image
import io
import json
import random
import uuid
import zipfile

import numpy as np
import pandas as pd
import yaml

ZIP_PATH = Path("/root/mining_cv/dataset.zip")
ROOT = Path("/root/mining_cv")

# Unique directory prevents mixing new files with earlier experiments.
EXPERIMENT_ID = uuid.uuid4().hex[:8]
DATASET_ROOT = ROOT / f"aerial_smoke_{EXPERIMENT_ID}"
DATASET_ROOT.mkdir(parents=True)

SOURCE_LABEL_TO_ID = {"1": 0}
CLASS_NAMES = {0: "source_label_1"}

# Keep only images whose annotations all use an approved source label.
# Images containing "n", "'", "‘", or other unknown labels are excluded
# entirely and recorded for review.

clean_pairs = []
excluded_images = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    for pair in matched_pairs:
        annotation = json.loads(
            archive.read(pair["annotation"])
        )

        shapes = annotation.get("shapes", [])
        labels = {
            str(shape.get("label", ""))
            for shape in shapes
        }

        unknown_labels = labels - set(SOURCE_LABEL_TO_ID)

        if not shapes:
            excluded_images.append({
                "image": pair["image"],
                "annotation": pair["annotation"],
                "reason": "empty_annotation_requires_review",
                "unknown_labels": "[]",
            })
            continue

        if unknown_labels:
            excluded_images.append({
                "image": pair["image"],
                "annotation": pair["annotation"],
                "reason": "unknown_labels",
                "unknown_labels": json.dumps(
                    sorted(unknown_labels),
                    ensure_ascii=False,
                ),
            })
            continue

        clean_pairs.append(pair)

# Save review information without modifying the original archive.
pd.DataFrame(
    excluded_images,
    columns=["image", "annotation", "reason", "unknown_labels"],
).to_csv(
    DATASET_ROOT / "excluded_images.csv",
    index=False,
)

print("Total matched images:", len(matched_pairs))
print("Eligible images:", len(clean_pairs))
print("Excluded images:", len(excluded_images))

TRAIN_COUNT = 400
VAL_COUNT = 100
REQUIRED_COUNT = TRAIN_COUNT + VAL_COUNT

if len(clean_pairs) < REQUIRED_COUNT:
    raise ValueError(
        f"Only {len(clean_pairs)} eligible images remain. "
        f"Need {REQUIRED_COUNT}. Reduce TRAIN_COUNT and VAL_COUNT."
    )

# IMPORTANT: sample clean_pairs, not matched_pairs.
selected = random.Random(42).sample(
    clean_pairs,
    REQUIRED_COUNT,
)

splits = {
    "train": selected[:TRAIN_COUNT],
    "val": selected[TRAIN_COUNT:],
}

# Defensive check before conversion starts.
with zipfile.ZipFile(ZIP_PATH) as archive:
    for pair in selected:
        annotation = json.loads(archive.read(pair["annotation"]))
        assert all(
            str(shape["label"]) in SOURCE_LABEL_TO_ID
            for shape in annotation["shapes"]
        ), f"Unexpected label remained in {pair['annotation']}"

print("Selected images:", {
    split: len(pairs) for split, pairs in splits.items()
})

manifest = []
instance_counts = {"train": 0, "val": 0}

with zipfile.ZipFile(ZIP_PATH) as archive:
    for split, pairs in splits.items():
        image_dir = DATASET_ROOT / "images" / split
        label_dir = DATASET_ROOT / "labels" / split

        image_dir.mkdir(parents=True)
        label_dir.mkdir(parents=True)

        for index, pair in enumerate(pairs):
            image_bytes = archive.read(pair["image"])
            annotation = json.loads(
                archive.read(pair["annotation"])
            )

            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size

            if (width, height) != (
                annotation["imageWidth"],
                annotation["imageHeight"],
            ):
                raise ValueError(
                    f"Image/annotation size mismatch: {pair['image']}"
                )

            rows = []

            for shape in annotation["shapes"]:
                if shape.get("shape_type") != "rectangle":
                    raise ValueError(
                        f"Unexpected shape type: {pair['annotation']}"
                    )

                source_label = str(shape["label"])

                if source_label not in SOURCE_LABEL_TO_ID:
                    raise ValueError(
                        f"Unknown source label {source_label!r}. "
                        "Update the mapping before continuing."
                    )

                points = np.asarray(shape["points"], dtype=float)

                if points.shape != (2, 2) or not np.isfinite(points).all():
                    raise ValueError(
                        f"Invalid rectangle: {pair['annotation']}"
                    )

                x1, y1 = points.min(axis=0)
                x2, y2 = points.max(axis=0)

                # Permit tiny rounding errors, but reject invalid boxes.
                tolerance = 1.0
                if (
                    x1 < -tolerance
                    or y1 < -tolerance
                    or x2 > width + tolerance
                    or y2 > height + tolerance
                ):
                    raise ValueError(
                        f"Out-of-bounds box: {pair['annotation']}"
                    )

                x1, x2 = np.clip([x1, x2], 0, width)
                y1, y2 = np.clip([y1, y2], 0, height)

                if x2 <= x1 or y2 <= y1:
                    raise ValueError(
                        f"Zero-area box: {pair['annotation']}"
                    )

                class_id = SOURCE_LABEL_TO_ID[source_label]

                xc = (x1 + x2) / (2 * width)
                yc = (y1 + y2) / (2 * height)
                bw = (x2 - x1) / width
                bh = (y2 - y1) / height

                rows.append(
                    f"{class_id} {xc:.8f} {yc:.8f} "
                    f"{bw:.8f} {bh:.8f}"
                )

            suffix = Path(pair["image"]).suffix.lower()
            stem = f"{split}_{index:05d}"

            (image_dir / f"{stem}{suffix}").write_bytes(image_bytes)
            (label_dir / f"{stem}.txt").write_text(
                "\n".join(rows) + ("\n" if rows else "")
            )

            instance_counts[split] += len(rows)

            manifest.append({
                "split": split,
                "output_image": f"{stem}{suffix}",
                "source_image": pair["image"],
                "source_annotation": pair["annotation"],
                "instances": len(rows),
            })

DATA_YAML = DATASET_ROOT / "data.yaml"

DATA_YAML.write_text(yaml.safe_dump({
    "path": str(DATASET_ROOT),
    "train": "images/train",
    "val": "images/val",
    "names": CLASS_NAMES,
}, sort_keys=False))

pd.DataFrame(manifest).to_csv(
    DATASET_ROOT / "manifest.csv",
    index=False,
)

print("Dataset:", DATASET_ROOT)
print("Images:", {split: len(pairs) for split, pairs in splits.items()})
print("Instances:", instance_counts)
print(DATA_YAML.read_text())

# %%
import torch
from ultralytics import YOLO

assert torch.cuda.is_available(), "GPU unavailable."

model = YOLO("yolo26n.pt")

model.train(
    data=str(DATA_YAML),
    epochs=5,
    imgsz=640,
    batch=8,
    device=0,
    workers=2,
    amp=True,
    cache=False,
    seed=42,

    # Keep the first experiment straightforward.
    mosaic=0.0,
    mixup=0.0,
    flipud=0.0,

    project=str(ROOT / "runs"),
    name=f"aerial_pipeline_check_{EXPERIMENT_ID}",
    plots=True,
    save=True,
    val=True,
)

RUN_DIR = Path(model.trainer.save_dir)
BEST_WEIGHTS = Path(model.trainer.best)

print("Results:", RUN_DIR)
print("Best weights:", BEST_WEIGHTS)

# %%
import matplotlib.pyplot as plt

history = pd.read_csv(RUN_DIR / "results.csv")
history.columns = history.columns.str.strip()

display(history)

loss_types = sorted({
    column.split("/", 1)[1]
    for column in history.columns
    if column.startswith(("train/", "val/"))
    and "loss" in column
})

for loss_type in loss_types:
    plt.figure(figsize=(7, 4))

    for split in ["train", "val"]:
        column = f"{split}/{loss_type}"

        if column in history.columns:
            plt.plot(
                history["epoch"],
                history[column],
                marker="o",
                label=split,
            )

    plt.title(loss_type)
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()

# %%
best_model = YOLO(str(BEST_WEIGHTS))

metrics = best_model.val(
    data=str(DATA_YAML),
    split="val",
    imgsz=640,
    batch=8,
    device=0,
    conf=0.001,
    plots=True,
    project=str(ROOT / "runs"),
    name=f"aerial_eval_{EXPERIMENT_ID}",
)

print({
    "mAP50": float(metrics.box.map50),
    "mAP50_95": float(metrics.box.map),
    "mAP75": float(metrics.box.map75),
})

print("Evaluation plots:", metrics.save_dir)

# %%
import sys
import subprocess

def pip(*args):
    subprocess.check_call([sys.executable, "-m", "pip", *args])
%uv pip("uninstall", "-y", "torch", "torchvision", "torchaudio")

pip(
    "install",
    "--no-cache-dir",
    "--index-url", "https://download.pytorch.org/whl/cu124",
    "torch==2.6.0",
    "torchvision==0.21.0",
    "torchaudio==2.6.0",
)

pip(
    "install",
    "transformers==4.51.3",
    "scipy>=1.13,<2",
    "numpy>=1.26,<3",
    "pillow>=10,<12",
    "pandas>=2,<3",
    "matplotlib>=3.8,<4",
    "pycocotools==2.0.8",
    "modal",
)

print("Restart the notebook kernel now.")

# %%


# %%
from pathlib import Path, PurePosixPath
from collections import defaultdict
import json
import zipfile

ZIP_PATH = Path("/root/mining_cv/dataset.zip")

if not ZIP_PATH.is_file():
    raise FileNotFoundError(
        f"{ZIP_PATH} does not exist. Run your Modal download cell first."
    )

matched_pairs = []
pairing_problems = []
annotation_errors = []
other_json_files = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    members = [
        item.filename
        for item in archive.infolist()
        if not item.is_dir()
        and "__MACOSX" not in PurePosixPath(item.filename).parts
        and not PurePosixPath(item.filename).name.startswith("._")
    ]

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

    images_by_stem = defaultdict(list)

    for name in members:
        path = PurePosixPath(name)
        if path.suffix.lower() in image_extensions:
            images_by_stem[path.stem].append(name)

    json_members = sorted(
        name for name in members
        if PurePosixPath(name).suffix.lower() == ".json"
    )

    for annotation_name in json_members:
        try:
            annotation = json.loads(archive.read(annotation_name))
        except Exception as exc:
            annotation_errors.append({
                "annotation": annotation_name,
                "error": str(exc),
            })
            continue

        # Only accept LabelMe-style annotation files.
        if (
            not isinstance(annotation, dict)
            or not isinstance(annotation.get("shapes"), list)
        ):
            other_json_files.append(annotation_name)
            continue

        candidates = images_by_stem.get(
            PurePosixPath(annotation_name).stem, []
        )

        # Preserve your original pairing rule: unique filename stem only.
        if len(candidates) == 1:
            matched_pairs.append({
                "image": candidates[0],
                "annotation": annotation_name,
            })
        else:
            pairing_problems.append({
                "annotation": annotation_name,
                "candidate_images": candidates,
            })

print("Matched image/annotation pairs:", len(matched_pairs))
print("Missing or ambiguous image matches:", len(pairing_problems))
print("JSON read errors:", len(annotation_errors))
print("Other JSON files skipped:", len(other_json_files))

if pairing_problems:
    print("\nExample pairing problems:")
    for problem in pairing_problems[:5]:
        print(problem)

if annotation_errors:
    print("\nExample JSON errors:")
    for error in annotation_errors[:5]:
        print(error)

if not matched_pairs:
    raise RuntimeError(
        "No unique image/LabelMe pairs found. "
        "Inspect the ZIP filenames and annotation format."
    )

print("\nExample matched pair:")
print(matched_pairs[0])

# %%
from pathlib import Path
import io
import json
import random
import uuid
import zipfile

import numpy as np
import pandas as pd
import torch
from PIL import Image
from transformers import (
    RTDetrForObjectDetection,
    RTDetrImageProcessor,
    set_seed,
)

ROOT = Path("/root/mining_cv")
ZIP_PATH = ROOT / "dataset.zip"

EXPERIMENT_ID = uuid.uuid4().hex[:8]
DATASET_ROOT = ROOT / f"hf_aerial_smoke_{EXPERIMENT_ID}"
RUN_DIR = ROOT / "runs" / f"rtdetr_pipeline_check_{EXPERIMENT_ID}"

DATASET_ROOT.mkdir(parents=True, exist_ok=False)
RUN_DIR.mkdir(parents=True, exist_ok=False)

MODEL_ID = "PekingU/rtdetr_r18vd"

SOURCE_LABEL_TO_ID = {"1": 0}
ID2LABEL = {0: "source_label_1"}
LABEL2ID = {name: index for index, name in ID2LABEL.items()}

SEED = 42
IMAGE_SIZE = 640
TRAIN_COUNT = 400
VAL_COUNT = 100

# Start with the same five-epoch smoke test as your final YOLO cell.
EPOCHS = 5

BATCH_SIZE = 2
GRAD_ACCUM_STEPS = 4  # Effective batch size: 8 on one GPU.
NUM_WORKERS = 2
LEARNING_RATE = 5e-5

set_seed(SEED)

assert ZIP_PATH.is_file(), f"Missing ZIP: {ZIP_PATH}"
assert matched_pairs, "Run your ZIP pairing/audit cell first."
assert torch.cuda.is_available(), "Enable a GPU before training."

DEVICE = torch.device("cuda")

print("GPU:", torch.cuda.get_device_name(0))
print("Model:", MODEL_ID)
print("Dataset:", DATASET_ROOT)
print("Run:", RUN_DIR)

# %%
clean_pairs = []
excluded_images = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    for pair in matched_pairs:
        annotation = json.loads(archive.read(pair["annotation"]))
        shapes = annotation.get("shapes", [])

        labels = {str(shape.get("label", "")) for shape in shapes}
        unknown = labels - set(SOURCE_LABEL_TO_ID)

        if not shapes:
            reason = "empty_annotation_requires_review"
        elif unknown:
            reason = "unknown_labels"
        else:
            clean_pairs.append(pair)
            continue

        excluded_images.append({
            "image": pair["image"],
            "annotation": pair["annotation"],
            "reason": reason,
            "unknown_labels": json.dumps(sorted(unknown), ensure_ascii=False),
        })

pd.DataFrame(
    excluded_images,
    columns=["image", "annotation", "reason", "unknown_labels"],
).to_csv(DATASET_ROOT / "excluded_images.csv", index=False)

required = TRAIN_COUNT + VAL_COUNT

if len(clean_pairs) < required:
    raise ValueError(
        f"Only {len(clean_pairs)} eligible images; need {required}. "
        "Reduce TRAIN_COUNT and VAL_COUNT."
    )

# Prevent the same ZIP image from entering multiple splits through
# multiple annotation files.
source_images = [pair["image"] for pair in clean_pairs]
if len(source_images) != len(set(source_images)):
    raise ValueError("Multiple annotation files reference the same image.")

selected = random.Random(SEED).sample(clean_pairs, required)

splits = {
    "train": selected[:TRAIN_COUNT],
    "val": selected[TRAIN_COUNT:],
}

categories = [
    {"id": index, "name": name}
    for index, name in ID2LABEL.items()
]

manifest = []

with zipfile.ZipFile(ZIP_PATH) as archive:
    for split, pairs in splits.items():
        image_dir = DATASET_ROOT / "images" / split
        image_dir.mkdir(parents=True)

        coco = {
            "info": {"description": "Mining LabelMe smoke-test dataset"},
            "licenses": [],
            "images": [],
            "annotations": [],
            "categories": categories,
        }

        annotation_id = 1

        for image_id, pair in enumerate(pairs):
            image_bytes = archive.read(pair["image"])
            annotation = json.loads(archive.read(pair["annotation"]))

            with Image.open(io.BytesIO(image_bytes)) as source:
                image = source.convert("RGB")

            width, height = image.size

            if (width, height) != (
                annotation.get("imageWidth"),
                annotation.get("imageHeight"),
            ):
                raise ValueError(
                    f"Image/annotation dimensions differ: {pair['image']}"
                )

            image_annotations = []

            for shape in annotation["shapes"]:
                if shape.get("shape_type") != "rectangle":
                    raise ValueError(
                        f"Non-rectangle shape: {pair['annotation']}"
                    )

                source_label = str(shape["label"])
                if source_label not in SOURCE_LABEL_TO_ID:
                    raise ValueError(f"Unknown label: {source_label!r}")

                points = np.asarray(shape["points"], dtype=np.float64)

                if points.shape != (2, 2) or not np.isfinite(points).all():
                    raise ValueError(
                        f"Invalid rectangle: {pair['annotation']}"
                    )

                x1, y1 = points.min(axis=0)
                x2, y2 = points.max(axis=0)

                tolerance = 1.0
                if (
                    x1 < -tolerance
                    or y1 < -tolerance
                    or x2 > width + tolerance
                    or y2 > height + tolerance
                ):
                    raise ValueError(
                        f"Out-of-bounds rectangle: {pair['annotation']}"
                    )

                x1, x2 = np.clip([x1, x2], 0, width)
                y1, y2 = np.clip([y1, y2], 0, height)

                box_width = float(x2 - x1)
                box_height = float(y2 - y1)

                if box_width <= 0 or box_height <= 0:
                    raise ValueError(
                        f"Zero-area rectangle: {pair['annotation']}"
                    )

                image_annotations.append({
                    "id": annotation_id,
                    "image_id": image_id,
                    "category_id": SOURCE_LABEL_TO_ID[source_label],
                    # COCO uses absolute pixel coordinates: x, y, width, height.
                    "bbox": [
                        float(x1), float(y1), box_width, box_height
                    ],
                    "area": box_width * box_height,
                    "iscrowd": 0,
                })
                annotation_id += 1

            # Write decoded RGB pixels without JPEG recompression.
            output_name = f"{split}_{image_id:05d}.png"
            image.save(image_dir / output_name)

            coco["images"].append({
                "id": image_id,
                "file_name": output_name,
                "width": width,
                "height": height,
            })
            coco["annotations"].extend(image_annotations)

            manifest.append({
                "split": split,
                "output_image": output_name,
                "source_image": pair["image"],
                "source_annotation": pair["annotation"],
                "instances": len(image_annotations),
            })

        annotation_path = DATASET_ROOT / f"{split}.json"
        annotation_path.write_text(json.dumps(coco), encoding="utf-8")

        print(
            f"{split}: {len(coco['images'])} images, "
            f"{len(coco['annotations'])} objects"
        )

pd.DataFrame(manifest).to_csv(
    DATASET_ROOT / "manifest.csv", index=False
)

print("Excluded images:", len(excluded_images))
print("Dataset ready:", DATASET_ROOT)

# %%
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval

coco_ground_truth = COCO(str(DATASET_ROOT / "val.json"))

@torch.inference_mode()
def evaluate_map(model):
    model.eval()
    predictions = []

    for batch in val_loader:
        batch = to_device(batch)

        labels = batch["labels"]
        inputs = {
            key: value
            for key, value in batch.items()
            if key != "labels"
        }

        outputs = model(**inputs)

        # orig_size is [height, width], before preprocessing.
        target_sizes = torch.stack([
            target["orig_size"] for target in labels
        ])

        results = processor.post_process_object_detection(
            outputs,
            target_sizes=target_sizes,
            threshold=0.0,  # Do not discard low-score detections for AP.
        )

        for target, result in zip(labels, results):
            image_id = int(target["image_id"].item())

            boxes = result["boxes"].detach().cpu().tolist()
            scores = result["scores"].detach().cpu().tolist()
            classes = result["labels"].detach().cpu().tolist()

            for box, score, class_id in zip(boxes, scores, classes):
                x1, y1, x2, y2 = box

                predictions.append({
                    "image_id": image_id,
                    "category_id": int(class_id),
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                    "score": float(score),
                })

    if not predictions:
        return {"map": 0.0, "map_50": 0.0, "map_75": 0.0}

    coco_predictions = coco_ground_truth.loadRes(predictions)
    evaluator = COCOeval(
        coco_ground_truth,
        coco_predictions,
        iouType="bbox",
    )

    evaluator.params.imgIds = [
        image["id"] for image in val_dataset.images
    ]

    evaluator.evaluate()
    evaluator.accumulate()
    evaluator.summarize()

    return {
        "map": float(evaluator.stats[0]),
        "map_50": float(evaluator.stats[1]),
        "map_75": float(evaluator.stats[2]),
    }

# %%
import math

optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=LEARNING_RATE,
    weight_decay=1e-4,
)

updates_per_epoch = math.ceil(len(train_loader) / GRAD_ACCUM_STEPS)

scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
    optimizer,
    T_max=max(1, EPOCHS * updates_per_epoch),
)

# BF16 is preferable where supported; otherwise use FP16 + scaling.
AMP_DTYPE = (
    torch.bfloat16
    if torch.cuda.is_bf16_supported()
    else torch.float16
)

scaler = torch.amp.GradScaler(
    "cuda",
    enabled=(AMP_DTYPE == torch.float16),
)

BEST_MODEL_DIR = RUN_DIR / "best"
LAST_MODEL_DIR = RUN_DIR / "last"

best_map = -1.0
history = []

run_config = {
    "model_id": MODEL_ID,
    "dataset_root": str(DATASET_ROOT),
    "source_label_to_id": SOURCE_LABEL_TO_ID,
    "id2label": ID2LABEL,
    "image_size": IMAGE_SIZE,
    "batch_size": BATCH_SIZE,
    "gradient_accumulation_steps": GRAD_ACCUM_STEPS,
    "epochs": EPOCHS,
    "learning_rate": LEARNING_RATE,
    "seed": SEED,
}

(RUN_DIR / "run_config.json").write_text(
    json.dumps(run_config, indent=2)
)

for epoch in range(EPOCHS):
    model.train()
    optimizer.zero_grad(set_to_none=True)

    total_loss = 0.0

    for step, batch in enumerate(train_loader):
        batch = to_device(batch)

        # Correctly scale even an incomplete final accumulation group.
        group_start = (step // GRAD_ACCUM_STEPS) * GRAD_ACCUM_STEPS
        group_size = min(
            GRAD_ACCUM_STEPS,
            len(train_loader) - group_start,
        )

        with torch.autocast("cuda", dtype=AMP_DTYPE):
            outputs = model(**batch)
            raw_loss = outputs.loss
            loss = raw_loss / group_size

        if not torch.isfinite(raw_loss):
            raise RuntimeError(
                f"Non-finite loss: epoch={epoch + 1}, step={step + 1}"
            )

        total_loss += raw_loss.detach().item()
        scaler.scale(loss).backward()

        should_update = (
            (step + 1) % GRAD_ACCUM_STEPS == 0
            or step + 1 == len(train_loader)
        )

        if should_update:
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            previous_scale = scaler.get_scale()
            scaler.step(optimizer)
            scaler.update()

            # Do not advance the scheduler after a skipped FP16 update.
            if scaler.get_scale() >= previous_scale:
                scheduler.step()

            optimizer.zero_grad(set_to_none=True)

        if (step + 1) % 25 == 0:
            print(
                f"Epoch {epoch + 1}/{EPOCHS} | "
                f"batch {step + 1}/{len(train_loader)} | "
                f"loss {raw_loss.item():.4f}"
            )

    metrics = evaluate_map(model)

    record = {
        "epoch": epoch + 1,
        "train_loss": total_loss / len(train_loader),
        **metrics,
    }
    history.append(record)

    pd.DataFrame(history).to_csv(
        RUN_DIR / "history.csv", index=False
    )

    # Hugging Face-format model and preprocessing configuration.
    model.save_pretrained(LAST_MODEL_DIR)
    processor.save_pretrained(LAST_MODEL_DIR)

    if metrics["map"] > best_map:
        best_map = metrics["map"]

        model.save_pretrained(BEST_MODEL_DIR)
        processor.save_pretrained(BEST_MODEL_DIR)

        (RUN_DIR / "best_metrics.json").write_text(
            json.dumps(record, indent=2)
        )

        print("Saved new best model:", BEST_MODEL_DIR)

    print(
        f"Epoch {epoch + 1}: "
        f"loss={record['train_loss']:.4f}, "
        f"mAP={metrics['map']:.4f}, "
        f"AP50={metrics['map_50']:.4f}"
    )

print("\nTraining complete.")
print("Best model:", BEST_MODEL_DIR)
print("Best validation mAP:", best_map)
print("History:", RUN_DIR / "history.csv")

# %%
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# Release the training model and optimizer before loading another model.
del optimizer, scheduler, model
torch.cuda.empty_cache()

inference_processor = RTDetrImageProcessor.from_pretrained(
    BEST_MODEL_DIR
)

inference_model = RTDetrForObjectDetection.from_pretrained(
    BEST_MODEL_DIR
).to(DEVICE).eval()

validation_image = val_dataset.images[0]
IMAGE_PATH = (
    DATASET_ROOT
    / "images"
    / "val"
    / validation_image["file_name"]
)

with Image.open(IMAGE_PATH) as source:
    image = source.convert("RGB")

inputs = inference_processor(
    images=image,
    return_tensors="pt",
).to(DEVICE)

with torch.inference_mode():
    outputs = inference_model(**inputs)

result = inference_processor.post_process_object_detection(
    outputs,
    target_sizes=torch.tensor(
        [[image.height, image.width]],
        device=DEVICE,
    ),
    threshold=0.30,
)[0]

fig, ax = plt.subplots(figsize=(14, 10))
ax.imshow(image)

prediction_rows = []

for box, score, class_id in zip(
    result["boxes"].cpu().tolist(),
    result["scores"].cpu().tolist(),
    result["labels"].cpu().tolist(),
):
    x1, y1, x2, y2 = box
    label = inference_model.config.id2label[class_id]

    prediction_rows.append({
        "label": label,
        "score": score,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
    })

    ax.add_patch(Rectangle(
        (x1, y1),
        x2 - x1,
        y2 - y1,
        fill=False,
        edgecolor="lime",
        linewidth=2,
    ))

    ax.text(
        x1,
        max(0, y1 - 5),
        f"{label}: {score:.2f}",
        color="black",
        bbox={"facecolor": "lime", "alpha": 0.8},
    )

ax.set_title(IMAGE_PATH.name)
ax.axis("off")
plt.tight_layout()

plt.savefig(
    RUN_DIR / "prediction_example.png",
    bbox_inches="tight",
    dpi=150,
)
plt.show()

display(pd.DataFrame(prediction_rows))

print("✅ Hugging Face model:", BEST_MODEL_DIR)
print("✅ Original dataset:", ZIP_PATH)
print("✅ Converted dataset:", DATASET_ROOT)
print("✅ Results:", RUN_DIR)

# %%
from pathlib import Path

ROOT = Path("/root/mining_cv")

configs = sorted(ROOT.glob("aerial_smoke_*/data.yaml"))

print("Available YOLO datasets:")
for config in configs:
    print(config)

if not configs:
    raise FileNotFoundError(
        "No original YOLO dataset found. "
        "Run your original LabelMe-to-YOLO conversion cell first. "
        "The Hugging Face COCO JSON dataset cannot be passed directly "
        "to Ultralytics as data.yaml."
    )

# %%
from pathlib import Path
import uuid

import torch
from ultralytics import YOLO

ROOT = Path("/root/mining_cv")

# Replace this with an actual path printed by the previous cell.
ROOT = Path("/root/mining_cv")

candidates = [
    path
    for path in ROOT.glob("aerial_smoke_*/data.yaml")
    if (path.parent / "images" / "train").is_dir()
    and (path.parent / "images" / "val").is_dir()
    and (path.parent / "labels" / "train").is_dir()
    and (path.parent / "labels" / "val").is_dir()
]

if not candidates:
    hf_datasets = sorted(ROOT.glob("hf_aerial_smoke_*"))

    raise FileNotFoundError(
        "No converted YOLO dataset found under /root/mining_cv.\n"
        f"Found {len(hf_datasets)} Hugging Face dataset folder(s).\n"
        "If you only created the Hugging Face dataset, it must be "
        "converted from COCO JSON to YOLO labels before YOLO training. "
        "Otherwise, rerun your original LabelMe-to-YOLO conversion cell."
    )

# Automatically choose the most recently modified dataset configuration.
DATA_YAML = max(candidates, key=lambda path: path.stat().st_mtime)

print("Using YOLO dataset:", DATA_YAML)
print(DATA_YAML.read_text())

if not DATA_YAML.is_file():
    raise FileNotFoundError(
        f"Dataset configuration not found: {DATA_YAML}"
    )

DEVICE = 0 if torch.cuda.is_available() else "cpu"
RUN_NAME = f"yolo26n_mining_{uuid.uuid4().hex[:8]}"

print("Device:", DEVICE)
print("Dataset:", DATA_YAML)

# 1. Load pretrained YOLO26n.
model = YOLO("yolo26n.pt")

# 2. Train.
train_results = model.train(
    data=str(DATA_YAML),
    epochs=5,              # Smoke test first; change to 100 afterward.
    imgsz=640,
    batch=8 if DEVICE != "cpu" else 2,
    device=DEVICE,
    workers=0,             # Notebook-friendly.
    amp=DEVICE != "cpu",
    cache=False,
    seed=42,
    mosaic=0.0,
    mixup=0.0,
    flipud=0.0,
    project=str(ROOT / "runs"),
    name=RUN_NAME,
    plots=True,
    save=True,
    val=True,
)

RUN_DIR = Path(model.trainer.save_dir)
BEST_WEIGHTS = Path(model.trainer.best)

if not BEST_WEIGHTS.is_file():
    raise FileNotFoundError(f"Best checkpoint missing: {BEST_WEIGHTS}")

# Explicitly reload the best checkpoint for evaluation and export.
model = YOLO(str(BEST_WEIGHTS))

# 3. Evaluate on the validation split.
metrics = model.val(
    data=str(DATA_YAML),
    split="val",
    imgsz=640,
    device=DEVICE,
)

print("Validation mAP50:", metrics.box.map50)
print("Validation mAP50–95:", metrics.box.map)

# 4. Predict on an image from your original converted dataset.
validation_dir = DATA_YAML.parent / "images" / "val"
image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

validation_images = sorted(
    path for path in validation_dir.iterdir()
    if path.suffix.lower() in image_extensions
)

if not validation_images:
    raise FileNotFoundError(f"No validation images in {validation_dir}")

IMAGE_PATH = validation_images[0]

results = model.predict(
    source=str(IMAGE_PATH),
    imgsz=640,
    conf=0.25,
    device=DEVICE,
    save=True,
    project=str(RUN_DIR),
    name="predictions",
)

# Display inline in the notebook.
from PIL import Image
from IPython.display import display

# Ultralytics plot() returns a BGR image.
display(Image.fromarray(results[0].plot()[..., ::-1]))

# 5. Export the best model to ONNX.
onnx_path = model.export(
    format="onnx",
    imgsz=640,
    device="cpu",
    dynamic=False,
    simplify=False,
)

print("✅ Best weights:", BEST_WEIGHTS)
print("✅ ONNX model:", onnx_path)
print("✅ Run directory:", RUN_DIR)

# %%
from pathlib import Path

checkpoint = Path(BEST_WEIGHTS).resolve()
assert checkpoint.is_file(), f"Checkpoint missing: {checkpoint}"

pointer = Path("/root/mining_cv/export_checkpoint.txt")
pointer.write_text(str(checkpoint))

print("Saved checkpoint location:", checkpoint)

# %%
%uv pip install --no-cache-dir "protobuf==6.31.1"
%uv pip check

# %%
from pathlib import Path

import onnx
import google.protobuf
from ultralytics import YOLO

print("ONNX:", onnx.__version__)
print("Protobuf:", google.protobuf.__version__)

pointer = Path("/root/mining_cv/export_checkpoint.txt")
assert pointer.is_file(), "Checkpoint-location file is missing."

BEST_WEIGHTS = Path(pointer.read_text().strip())
assert BEST_WEIGHTS.is_file(), f"Checkpoint missing: {BEST_WEIGHTS}"

model = YOLO(str(BEST_WEIGHTS))

onnx_path = model.export(
    format="onnx",
    imgsz=640,
    device="cpu",
    dynamic=False,
    simplify=False,
)

# Check that the exported ONNX graph is structurally valid.
onnx.checker.check_model(str(onnx_path))

print("✅ Trained weights:", BEST_WEIGHTS)
print("✅ ONNX exported and graph validated:", onnx_path)

# %%
%uv pip install onnxruntime

# %%
from pathlib import Path
from ultralytics import YOLO

WEIGHTS_DIR = Path(
    "/root/mining_cv/runs/yolo26n_mining_63c504fa/weights"
)
DATA_YAML = Path(
    "/root/mining_cv/aerial_smoke_0acf3f95/data.yaml"
)

pt_model = YOLO(str(WEIGHTS_DIR / "best.pt"))
onnx_model = YOLO(
    str(WEIGHTS_DIR / "best.onnx"),
    task="detect",
)

validation_args = {
    "data": str(DATA_YAML),
    "split": "val",
    "imgsz": 640,
    "batch": 1,         # Matches your fixed ONNX export batch size.
    "device": "cpu",
    "workers": 0,
    "plots": True,
}

pt_metrics = pt_model.val(**validation_args)
onnx_metrics = onnx_model.val(**validation_args)

print("\n                 PyTorch      ONNX")
print(
    f"mAP50:           {pt_metrics.box.map50:.4f}       "
    f"{onnx_metrics.box.map50:.4f}"
)
print(
    f"mAP50–95:        {pt_metrics.box.map:.4f}       "
    f"{onnx_metrics.box.map:.4f}"
)

# %%
from PIL import Image
from IPython.display import display

VAL_DIR = DATA_YAML.parent / "images" / "val"
extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

images = sorted(
    path for path in VAL_DIR.iterdir()
    if path.suffix.lower() in extensions
)

assert images, f"No images found in {VAL_DIR}"

for image_path in images[:5]:
    results = onnx_model.predict(
        source=str(image_path),
        imgsz=640,
        conf=0.25,
        device="cpu",
        save=True,
        project="/root/mining_cv/runs",
        name="onnx_prediction_check",
        exist_ok=True,
    )

    print(image_path.name)
    display(Image.fromarray(results[0].plot()[..., ::-1]))

# %%
from PIL import Image
from IPython.display import display

VAL_DIR = DATA_YAML.parent / "images" / "val"
extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

images = sorted(
    path for path in VAL_DIR.iterdir()
    if path.suffix.lower() in extensions
)

assert images, f"No images found in {VAL_DIR}"

for image_path in images[:5]:
    results = onnx_model.predict(
        source=str(image_path),
        imgsz=640,
        conf=0.25,
        device="cpu",
        save=True,
        project="/root/mining_cv/runs",
        name="onnx_prediction_check",
        exist_ok=True,
    )

    print(image_path.name)
    display(Image.fromarray(results[0].plot()[..., ::-1]))

# %%
%uv pip install -q huggingface_hub

# %%
from huggingface_hub import HfApi
import pandas as pd

api = HfApi()

queries = [
    "bdd100k",
    "dashcam",
    "kitti",
    "road object detection",
    "driving",
    "india driving",
]

found = {}

for query in queries:
    for dataset in api.list_datasets(
        search=query,
        sort="downloads",
        direction=-1,
        limit=15,
    ):
        tags = dataset.tags or []

        found[dataset.id] = {
            "dataset": dataset.id,
            "downloads": dataset.downloads or 0,
            "gated": dataset.gated,
            "detection_tag": "task_categories:object-detection" in tags,
            "segmentation_tag": (
                "task_categories:image-segmentation" in tags
            ),
            "url": f"https://huggingface.co/datasets/{dataset.id}",
        }

results = pd.DataFrame(found.values())

if results.empty:
    print("No results returned. Check your network connection.")
else:
    results = results.sort_values(
        ["detection_tag", "downloads"],
        ascending=[False, False],
    ).reset_index(drop=True)

    pd.set_option("display.max_colwidth", 110)
    display(results.head(30))

    print("\nTop repository links:")
    for row in results.head(10).itertuples():
        print(row.url)

# %%
for repo_id in results.head(8)["dataset"]:
    try:
        info = api.dataset_info(repo_id)
        files = [file.rfilename for file in info.siblings]

        data_files = [
            name for name in files
            if name.endswith((
                ".parquet", ".json", ".jsonl",
                ".csv", ".zip", ".tar", ".tar.gz"
            ))
        ]

        print("\n" + "=" * 80)
        print("REPOSITORY:", repo_id)
        print("URL:", f"https://huggingface.co/datasets/{repo_id}")
        print("GATED:", info.gated)

        if info.card_data is not None:
            card = info.card_data.to_dict()
            print("LICENSE:", card.get("license", "Not specified"))
            print("DATASET INFO:", str(card.get("dataset_info", ""))[:1200])

        print("DATA FILES:")
        for name in data_files[:12]:
            print("  ", name)

    except Exception as exc:
        print(f"Could not inspect {repo_id}: {exc}")

# %%
%uv pip install -q huggingface_hub requests ijson

# %%
from collections import Counter
from pathlib import PurePosixPath
import json

import ijson
import requests
from huggingface_hub import HfApi, hf_hub_url

REPO_ID = "ShantyCam/bdd100k-det"

api = HfApi()

# Listing repository files does not download the image contents.
info = api.dataset_info(
    REPO_ID,
    files_metadata=True,
)

REVISION = info.sha
files = info.siblings

image_extensions = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp"
}

image_files = [
    file
    for file in files
    if PurePosixPath(file.rfilename).suffix.lower() in image_extensions
]

print("Repository:", REPO_ID)
print("Revision:", REVISION)
print("Individual image files:", len(image_files))

print("\nExample image paths:")
for file in image_files[:8]:
    size = (
        f"{file.size / 1024:.1f} KiB"
        if file.size is not None
        else "unknown size"
    )
    print(f"  {file.rfilename} — {size}")

print("\nMost common file extensions:")
extension_counts = Counter(
    PurePosixPath(file.rfilename).suffix.lower()
    for file in files
)
print(extension_counts.most_common(12))

sample_files = [
    file for file in files
    if PurePosixPath(file.rfilename).name == "samples.json"
]

if len(sample_files) != 1:
    raise RuntimeError(
        "Expected one samples.json file. Found: "
        + str([file.rfilename for file in sample_files])
    )

sample_file = sample_files[0]

if sample_file.size is not None:
    print(
        "\nFull annotation file size:",
        f"{sample_file.size / 1024**2:.2f} MiB",
    )

# Stream the annotation file instead of downloading the whole file.
url = hf_hub_url(
    repo_id=REPO_ID,
    filename=sample_file.rfilename,
    repo_type="dataset",
    revision=REVISION,
)

preview_samples = []

with requests.get(url, stream=True, timeout=(15, 90)) as response:
    response.raise_for_status()
    response.raw.decode_content = True

    # Typical FiftyOne export: {"samples": [{...}, {...}]}
    for sample in ijson.items(response.raw, "samples.item"):
        preview_samples.append(sample)

        if len(preview_samples) == 3:
            break

if not preview_samples:
    raise RuntimeError(
        "No records found under 'samples'. "
        "This repository may use a different JSON structure."
    )


# Show field structure while keeping long detection lists readable.
def compact(value):
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()}

    if isinstance(value, list):
        if len(value) > 6:
            return {
                "_list_length": len(value),
                "_first_two": [compact(item) for item in value[:2]],
            }
        return [compact(item) for item in value]

    return value


print("\nFirst sample records:")
for index, sample in enumerate(preview_samples):
    print(f"\n--- Sample {index + 1} ---")
    print(json.dumps(compact(sample), indent=2, default=str))

# %%
from pathlib import Path
from collections import defaultdict, Counter
import json
import random
import shutil
import uuid

import numpy as np
import torch
import yaml
from PIL import Image
from huggingface_hub import hf_hub_download
from ultralytics import YOLO

ROOT = Path("/root/mining_cv")
ROOT.mkdir(parents=True, exist_ok=True)

REPO_ID = "ShantyCam/bdd100k-det"
REVISION = "d775466059d07da240a4137c8cec93deea11ceed"

SEED = 42
TRAIN_COUNT = 300
VAL_COUNT = 100

EXPERIMENT_ID = uuid.uuid4().hex[:8]
DATASET_ROOT = ROOT / f"bdd_small_{EXPERIMENT_ID}"
DATASET_ROOT.mkdir(parents=True, exist_ok=False)

DEVICE = 0 if torch.cuda.is_available() else "cpu"

print("Dataset:", DATASET_ROOT)
print("Device:", DEVICE)

# %%
annotation_file = hf_hub_download(
    repo_id=REPO_ID,
    repo_type="dataset",
    revision=REVISION,
    filename="samples.json",
)

with open(annotation_file) as file:
    document = json.load(file)

samples = document["samples"]

if not isinstance(samples, list):
    raise ValueError("Expected samples.json to contain a samples list.")

# Use JPEG camera images only, not PNG segmentation masks.
eligible = [
    sample
    for sample in samples
    if Path(sample.get("filepath", "")).suffix.lower() in {".jpg", ".jpeg"}
    and isinstance(sample.get("detections"), dict)
    and isinstance(
        sample["detections"].get("detections"), list
    )
]

paths = [sample["filepath"] for sample in eligible]

if len(paths) != len(set(paths)):
    raise ValueError("Duplicate image paths found in the annotations.")

if len(eligible) < TRAIN_COUNT + VAL_COUNT:
    raise ValueError(f"Only {len(eligible)} eligible images found.")

# Preserve all detection categories found in the source annotations.
CLASS_NAMES = sorted({
    detection["label"]
    for sample in eligible
    for detection in sample["detections"]["detections"]
})

CLASS_TO_ID = {
    name: index for index, name in enumerate(CLASS_NAMES)
}

def group_key(sample):
    return Path(sample["filepath"]).stem.split("-")[0]

groups = defaultdict(list)

for sample in eligible:
    groups[group_key(sample)].append(sample)

group_names = sorted(groups)
rng = random.Random(SEED)
rng.shuffle(group_names)

# Select validation groups first. Any unused images from those groups
# are excluded from training.
validation_groups = set()
validation_pool = []

for group in group_names:
    validation_groups.add(group)
    validation_pool.extend(groups[group])

    if len(validation_pool) >= VAL_COUNT:
        break

training_pool = [
    sample
    for group in group_names
    if group not in validation_groups
    for sample in groups[group]
]

if len(training_pool) < TRAIN_COUNT:
    raise ValueError(
        "Not enough training images after separating filename groups."
    )

splits = {
    "train": rng.sample(training_pool, TRAIN_COUNT),
    "val": rng.sample(validation_pool, VAL_COUNT),
}

assert (
    {group_key(sample) for sample in splits["train"]}
    .isdisjoint({group_key(sample) for sample in splits["val"]})
)

estimated_image_bytes = sum(
    sample.get("metadata", {}).get("size_bytes", 0) or 0
    for subset in splits.values()
    for sample in subset
)

print("Eligible annotated images:", len(eligible))
print("Classes:", CLASS_NAMES)
print("Selected:", {name: len(items) for name, items in splits.items()})
print(
    "Estimated selected image download:",
    f"{estimated_image_bytes / 1024**2:.1f} MiB",
)

# %%
manifest = []
split_counts = {}
clipped_boxes = 0

for split, selected in splits.items():
    image_dir = DATASET_ROOT / "images" / split
    label_dir = DATASET_ROOT / "labels" / split

    image_dir.mkdir(parents=True)
    label_dir.mkdir(parents=True)

    counts = Counter()

    for index, sample in enumerate(selected):
        source_path = sample["filepath"]

        cached_image = hf_hub_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            revision=REVISION,
            filename=source_path,
        )

        output_stem = f"{split}_{index:05d}"
        output_name = f"{output_stem}.jpg"
        output_image = image_dir / output_name

        # Copy the selected image so this dataset is independent
        # of the Hugging Face cache.
        shutil.copy2(cached_image, output_image)

        with Image.open(output_image) as image:
            width, height = image.size

        metadata = sample.get("metadata", {})

        if (
            metadata.get("width") is not None
            and metadata.get("height") is not None
            and (width, height) != (
                metadata["width"], metadata["height"]
            )
        ):
            raise ValueError(f"Image dimensions mismatch: {source_path}")

        rows = []

        for detection in sample["detections"]["detections"]:
            class_name = detection["label"]
            class_id = CLASS_TO_ID[class_name]

            box = np.asarray(
                detection["bounding_box"], dtype=float
            )

            if box.shape != (4,) or not np.isfinite(box).all():
                raise ValueError(f"Invalid box in {source_path}: {box}")

            x, y, box_width, box_height = box

            if box_width <= 0 or box_height <= 0:
                raise ValueError(f"Non-positive box in {source_path}")

            original = np.array([
                x, y, x + box_width, y + box_height
            ])

            # Permit only small boundary overshoots.
            tolerance = 0.01
            if (
                original[0] < -tolerance
                or original[1] < -tolerance
                or original[2] > 1 + tolerance
                or original[3] > 1 + tolerance
            ):
                raise ValueError(
                    f"Box significantly outside image: "
                    f"{source_path}, {original}"
                )

            clipped = np.clip(original, 0, 1)
            if not np.array_equal(original, clipped):
                clipped_boxes += 1

            x1, y1, x2, y2 = clipped

            if x2 <= x1 or y2 <= y1:
                raise ValueError(f"Zero-area clipped box: {source_path}")

            xc = (x1 + x2) / 2
            yc = (y1 + y2) / 2
            bw = x2 - x1
            bh = y2 - y1

            rows.append(
                f"{class_id} {xc:.8f} {yc:.8f} {bw:.8f} {bh:.8f}"
            )

            counts[class_name] += 1

        # Empty annotations remain valid negative images.
        (label_dir / f"{output_stem}.txt").write_text(
            "\n".join(rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )

        manifest.append({
            "split": split,
            "output_image": output_name,
            "source_image": source_path,
            "instances": len(rows),
            "group": group_key(sample),
        })

        if (index + 1) % 50 == 0:
            print(f"{split}: {index + 1}/{len(selected)} images ready")

    split_counts[split] = dict(counts)

DATA_YAML = DATASET_ROOT / "data.yaml"

DATA_YAML.write_text(
    yaml.safe_dump(
        {
            "path": str(DATASET_ROOT.resolve()),
            "train": "images/train",
            "val": "images/val",
            "names": dict(enumerate(CLASS_NAMES)),
        },
        sort_keys=False,
    ),
    encoding="utf-8",
)

(DATASET_ROOT / "manifest.json").write_text(
    json.dumps({
        "repository": REPO_ID,
        "revision": REVISION,
        "seed": SEED,
        "split_method": "custom_filename_prefix_group_split",
        "images": manifest,
        "instance_counts": split_counts,
    }, indent=2),
    encoding="utf-8",
)

# Preserve polygons and mask references, but do not download masks.
(DATASET_ROOT / "selected_source_annotations.json").write_text(
    json.dumps(splits),
    encoding="utf-8",
)

# Persist the dataset path for later cells or a kernel restart.
(ROOT / "bdd_small_dataset_path.txt").write_text(
    str(DATA_YAML.resolve())
)

print("\n✅ Dataset ready:", DATA_YAML)
print("Clipped boxes:", clipped_boxes)
print("Instance counts:", json.dumps(split_counts, indent=2))

for split, counts in split_counts.items():
    missing = [name for name in CLASS_NAMES if counts.get(name, 0) == 0]
    if missing:
        print(f"⚠️ No examples of these classes in {split}: {missing}")

# %%
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

image_path = next(
    (DATASET_ROOT / "images" / "train").glob("*.jpg")
)
label_path = (
    DATASET_ROOT / "labels" / "train" / f"{image_path.stem}.txt"
)

with Image.open(image_path) as source:
    image = source.convert("RGB")

width, height = image.size

fig, ax = plt.subplots(figsize=(16, 9))
ax.imshow(image)

for line in label_path.read_text().splitlines():
    class_id, xc, yc, bw, bh = map(float, line.split())

    x1 = (xc - bw / 2) * width
    y1 = (yc - bh / 2) * height

    ax.add_patch(Rectangle(
        (x1, y1),
        bw * width,
        bh * height,
        fill=False,
        edgecolor="lime",
        linewidth=1.5,
    ))

    ax.text(
        x1,
        y1,
        CLASS_NAMES[int(class_id)],
        fontsize=8,
        color="black",
        bbox={"facecolor": "lime", "alpha": 0.7},
    )

ax.axis("off")
plt.tight_layout()
plt.show()

# %%
from pathlib import Path
import uuid
import torch
from ultralytics import YOLO

ROOT = Path("/root/mining_cv")

DATA_YAML = Path(
    (ROOT / "bdd_small_dataset_path.txt").read_text().strip()
)
assert DATA_YAML.is_file(), DATA_YAML

DEVICE = 0 if torch.cuda.is_available() else "cpu"

# New road-object training run, not the old source_label_1 model.
model = YOLO("yolo26n.pt")

model.train(
    data=str(DATA_YAML),
    epochs=5,              # Smoke test first.
    imgsz=640,
    batch=4 if DEVICE != "cpu" else 2,
    device=DEVICE,
    workers=0,
    amp=DEVICE != "cpu",
    cache=False,
    seed=42,
    mosaic=0.0,
    mixup=0.0,
    flipud=0.0,
    project=str(ROOT / "runs"),
    name=f"yolo26n_bdd_small_{uuid.uuid4().hex[:8]}",
    plots=True,
    save=True,
    val=True,
)

BEST_WEIGHTS = Path(model.trainer.best).resolve()

(ROOT / "bdd_small_best_checkpoint.txt").write_text(
    str(BEST_WEIGHTS)
)

print("✅ Best model:", BEST_WEIGHTS)

# %%
from PIL import Image
from IPython.display import display

model = YOLO(str(BEST_WEIGHTS))

metrics = model.val(
    data=str(DATA_YAML),
    split="val",
    imgsz=640,
    batch=4 if DEVICE != "cpu" else 2,
    device=DEVICE,
    workers=0,
)

print("mAP50:", metrics.box.map50)
print("mAP50–95:", metrics.box.map)

validation_images = sorted(
    (DATA_YAML.parent / "images" / "val").glob("*.jpg")
)

for image_path in validation_images[:3]:
    results = model.predict(
        source=str(image_path),
        imgsz=640,
        conf=0.25,
        device=DEVICE,
    )

    display(Image.fromarray(results[0].plot()[..., ::-1]))

# %%
from pathlib import Path
import json

import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from huggingface_hub import hf_hub_download

ROOT = Path("/root/mining_cv")

pointer = ROOT / "bdd_small_dataset_path.txt"
assert pointer.is_file(), "Missing saved BDD dataset path."

DATA_YAML = Path(pointer.read_text().strip())
DATASET_ROOT = DATA_YAML.parent

with (DATASET_ROOT / "manifest.json").open() as file:
    manifest = json.load(file)

with (DATASET_ROOT / "selected_source_annotations.json").open() as file:
    source_annotations = json.load(file)

REPO_ID = manifest["repository"]
REVISION = manifest["revision"]

# Match source annotations to the exact local image filenames.
local_validation_images = {
    record["source_image"]: (
        DATASET_ROOT / "images" / "val" / record["output_image"]
    )
    for record in manifest["images"]
    if record["split"] == "val"
}

MASK_DIR = DATASET_ROOT / "mask_preview"
MASK_DIR.mkdir(exist_ok=True)

preview_samples = source_annotations["val"][:3]

fig, axes = plt.subplots(
    len(preview_samples),
    2,
    figsize=(16, 5 * len(preview_samples)),
    squeeze=False,
)

for index, sample in enumerate(preview_samples):
    image_path = local_validation_images[sample["filepath"]]

    mask_reference = sample.get("drivable", {}).get("mask_path")
    if not mask_reference:
        raise ValueError(
            f"No drivable mask reference for {sample['filepath']}"
        )

    cached_mask = hf_hub_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=REVISION,
        filename=mask_reference,
    )

    with Image.open(image_path) as source:
        image = source.convert("RGB")

    with Image.open(cached_mask) as source:
        mask = source.copy()

    if image.size != mask.size:
        raise ValueError(
            f"Image/mask size mismatch: {image.size} vs {mask.size}"
        )

    # Keep original mask encoding; do not convert labels to grayscale.
    mask.save(MASK_DIR / f"{image_path.stem}.png")
    values = np.asarray(mask)

    print(f"\nImage: {image_path.name}")
    print("Mask mode:", mask.mode)
    print("Mask shape:", values.shape)

    if values.ndim == 2:
        unique, counts = np.unique(values, return_counts=True)
        print("Pixel values/counts:", dict(zip(
            unique.tolist(), counts.tolist()
        )))
    else:
        unique, counts = np.unique(
            values.reshape(-1, values.shape[-1]),
            axis=0,
            return_counts=True,
        )
        print("Number of unique colors:", len(unique))
        print("First colors/counts:", list(zip(
            unique[:10].tolist(), counts[:10].tolist()
        )))

    axes[index, 0].imshow(image)
    axes[index, 0].set_title("Camera image")

    if mask.mode == "P":
        axes[index, 1].imshow(mask.convert("RGB"))
    elif values.ndim == 2:
        axes[index, 1].imshow(
            values,
            cmap="tab10",
            interpolation="nearest",
        )
    else:
        axes[index, 1].imshow(mask)

    axes[index, 1].set_title(
        "Ground-truth mask — colors are not yet a verified legend"
    )

    for ax in axes[index]:
        ax.axis("off")

plt.tight_layout()
plt.show()

# %%


# %%
from pathlib import Path
from collections import Counter
import subprocess
import json
import uuid

REPO_URL = "https://github.com/desmondzee/mining.git"
ROOT = Path("/root/mining_cv")
ROOT.mkdir(parents=True, exist_ok=True)

CHECKOUT = ROOT / f"unity_repo_audit_{uuid.uuid4().hex[:8]}"

def git(*args):
    return subprocess.check_output(
        ["git", *args],
        text=True,
        stderr=subprocess.STDOUT,
    )

# Resolve the branch/path ambiguity in the GitHub URL.
remote_heads = git("ls-remote", "--heads", REPO_URL)

heads = {
    line.split("\t", 1)[1].removeprefix("refs/heads/")
    for line in remote_heads.splitlines()
    if "\t" in line
}

if "vato/environment" in heads:
    branch = "vato/environment"
    relative_scope = Path(".")
elif "vato" in heads:
    branch = "vato"
    relative_scope = Path("environment")
else:
    raise RuntimeError(
        "Neither 'vato/environment' nor 'vato' exists as a branch.\n"
        f"Available branches: {sorted(heads)}"
    )

print("Cloning branch:", branch)

subprocess.run(
    [
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        "--branch", branch,
        REPO_URL,
        str(CHECKOUT),
    ],
    check=True,
)

SCOPE = CHECKOUT / relative_scope

if not SCOPE.is_dir():
    raise FileNotFoundError(
        f"The URL resolved to a directory that does not exist: {SCOPE}"
    )

print("Repository:", CHECKOUT)
print("Selected directory:", SCOPE)
print("Commit:", git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip())

# Inspect the whole checkout: data-recording code may be outside the
# directory selected in the GitHub URL.
files = sorted(
    path for path in CHECKOUT.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(CHECKOUT).parts
)

def relative(path):
    return str(path.relative_to(CHECKOUT))

image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
video_extensions = {".mp4", ".avi", ".mov", ".mkv"}

images = [
    path for path in files
    if path.suffix.lower() in image_extensions
]
videos = [
    path for path in files
    if path.suffix.lower() in video_extensions
]
json_files = [
    path for path in files
    if path.suffix.lower() == ".json"
]
yaml_files = [
    path for path in files
    if path.suffix.lower() in {".yaml", ".yml"}
]

print("\nFile extension counts:")
print(Counter(path.suffix.lower() for path in files).most_common(20))

print("\nImages:", len(images))
print("Videos:", len(videos))
print("JSON files:", len(json_files))
print("YAML files:", len(yaml_files))

print("\nExample image paths — these could be assets, not camera frames:")
for path in images[:20]:
    print(relative(path))

print("\nPossible dataset/configuration files:")
for path in (json_files + yaml_files)[:40]:
    print(relative(path))

keywords = (
    "capture", "record", "dataset", "annotation", "bounding",
    "camera", "sensor", "protocol", "fakesim", "spec",
)

relevant_code = [
    path for path in files
    if path.suffix.lower() in {".py", ".cs", ".md"}
    and any(word in path.name.lower() for word in keywords)
]

print("\nRelevant source/documentation files:")
for path in relevant_code[:60]:
    print(relative(path))

# Look for already-exported YOLO labels.
def looks_like_yolo_label(path):
    if path.suffix.lower() != ".txt" or path.stat().st_size > 1_000_000:
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return False

        for line in lines:
            parts = line.split()
            if len(parts) != 5:
                return False

            class_id = int(parts[0])
            coordinates = [float(value) for value in parts[1:]]

            if class_id < 0 or not all(0 <= value <= 1 for value in coordinates):
                return False

        return True

    except (ValueError, UnicodeError):
        return False

yolo_candidates = [
    path for path in files if looks_like_yolo_label(path)
]

print("\nPossible YOLO label files:", len(yolo_candidates))
for path in yolo_candidates[:10]:
    print(relative(path))
    print(path.read_text()[:500])

# Display the project specification if present.
spec_files = [path for path in files if path.name == "spec.md"]

for path in spec_files[:2]:
    print(f"\n--- {relative(path)} ---")
    print(path.read_text(errors="replace")[:12000])

report = {
    "checkout": str(CHECKOUT),
    "branch": branch,
    "commit": git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip(),
    "images": [relative(path) for path in images],
    "videos": [relative(path) for path in videos],
    "json_files": [relative(path) for path in json_files],
    "yaml_files": [relative(path) for path in yaml_files],
    "relevant_code": [relative(path) for path in relevant_code],
    "possible_yolo_labels": [relative(path) for path in yolo_candidates],
}

REPORT_PATH = ROOT / "unity_repo_audit.json"
REPORT_PATH.write_text(json.dumps(report, indent=2))

print("\n✅ Audit saved:", REPORT_PATH)

# %%
from pathlib import Path
from collections import Counter
import subprocess
import json
import uuid

REPO_URL = "https://github.com/desmondzee/mining.git"
ROOT = Path("/root/mining_cv")
ROOT.mkdir(parents=True, exist_ok=True)

CHECKOUT = ROOT / f"unity_repo_audit_{uuid.uuid4().hex[:8]}"

def git(*args):
    return subprocess.check_output(
        ["git", *args],
        text=True,
        stderr=subprocess.STDOUT,
    )

# Resolve the branch/path ambiguity in the GitHub URL.
remote_heads = git("ls-remote", "--heads", REPO_URL)

heads = {
    line.split("\t", 1)[1].removeprefix("refs/heads/")
    for line in remote_heads.splitlines()
    if "\t" in line
}

if "vato/environment" in heads:
    branch = "vato/environment"
    relative_scope = Path(".")
elif "vato" in heads:
    branch = "vato"
    relative_scope = Path("environment")
else:
    raise RuntimeError(
        "Neither 'vato/environment' nor 'vato' exists as a branch.\n"
        f"Available branches: {sorted(heads)}"
    )

print("Cloning branch:", branch)

subprocess.run(
    [
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        "--branch", branch,
        REPO_URL,
        str(CHECKOUT),
    ],
    check=True,
)

SCOPE = CHECKOUT / relative_scope

if not SCOPE.is_dir():
    raise FileNotFoundError(
        f"The URL resolved to a directory that does not exist: {SCOPE}"
    )

print("Repository:", CHECKOUT)
print("Selected directory:", SCOPE)
print("Commit:", git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip())

# Inspect the whole checkout: data-recording code may be outside the
# directory selected in the GitHub URL.
files = sorted(
    path for path in CHECKOUT.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(CHECKOUT).parts
)

def relative(path):
    return str(path.relative_to(CHECKOUT))

image_extensions = {".jpg", ".jpeg", ".png", ".bmp"}
video_extensions = {".mp4", ".avi", ".mov", ".mkv"}

images = [
    path for path in files
    if path.suffix.lower() in image_extensions
]
videos = [
    path for path in files
    if path.suffix.lower() in video_extensions
]
json_files = [
    path for path in files
    if path.suffix.lower() == ".json"
]
yaml_files = [
    path for path in files
    if path.suffix.lower() in {".yaml", ".yml"}
]

print("\nFile extension counts:")
print(Counter(path.suffix.lower() for path in files).most_common(20))

print("\nImages:", len(images))
print("Videos:", len(videos))
print("JSON files:", len(json_files))
print("YAML files:", len(yaml_files))

print("\nExample image paths — these could be assets, not camera frames:")
for path in images[:20]:
    print(relative(path))

print("\nPossible dataset/configuration files:")
for path in (json_files + yaml_files)[:40]:
    print(relative(path))

keywords = (
    "capture", "record", "dataset", "annotation", "bounding",
    "camera", "sensor", "protocol", "fakesim", "spec",
)

relevant_code = [
    path for path in files
    if path.suffix.lower() in {".py", ".cs", ".md"}
    and any(word in path.name.lower() for word in keywords)
]

print("\nRelevant source/documentation files:")
for path in relevant_code[:60]:
    print(relative(path))

# Look for already-exported YOLO labels.
def looks_like_yolo_label(path):
    if path.suffix.lower() != ".txt" or path.stat().st_size > 1_000_000:
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in lines if line.strip()]

        if not lines:
            return False

        for line in lines:
            parts = line.split()
            if len(parts) != 5:
                return False

            class_id = int(parts[0])
            coordinates = [float(value) for value in parts[1:]]

            if class_id < 0 or not all(0 <= value <= 1 for value in coordinates):
                return False

        return True

    except (ValueError, UnicodeError):
        return False

yolo_candidates = [
    path for path in files if looks_like_yolo_label(path)
]

print("\nPossible YOLO label files:", len(yolo_candidates))
for path in yolo_candidates[:10]:
    print(relative(path))
    print(path.read_text()[:500])

# Display the project specification if present.
spec_files = [path for path in files if path.name == "spec.md"]

for path in spec_files[:2]:
    print(f"\n--- {relative(path)} ---")
    print(path.read_text(errors="replace")[:12000])

report = {
    "checkout": str(CHECKOUT),
    "branch": branch,
    "commit": git("-C", str(CHECKOUT), "rev-parse", "HEAD").strip(),
    "images": [relative(path) for path in images],
    "videos": [relative(path) for path in videos],
    "json_files": [relative(path) for path in json_files],
    "yaml_files": [relative(path) for path in yaml_files],
    "relevant_code": [relative(path) for path in relevant_code],
    "possible_yolo_labels": [relative(path) for path in yolo_candidates],
}

REPORT_PATH = ROOT / "unity_repo_audit.json"
REPORT_PATH.write_text(json.dumps(report, indent=2))

print("\n✅ Audit saved:", REPORT_PATH)

# %%
from pathlib import Path
import json
import zipfile

ROOT = Path("/root/mining_cv")

audit = json.loads(
    (ROOT / "unity_repo_audit.json").read_text()
)
repo = Path(audit["checkout"])

requested = [
    "unity/Scripts/SensorCamera.cs",
    "unity/Scripts/BrainProtocol.cs",
    "unity/Scripts/BrainClient.cs",
    "unity/Scripts/LidarSensor.cs",
    "brain/brain/protocol.py",
    "brain/brain/bridge.py",
    "brain/brain/policy.py",
    "schema/brain_sim.schema.json",
    "spec.md",
]

output = ROOT / "unity_perception_integration_sources.zip"

with zipfile.ZipFile(
    output, "w", compression=zipfile.ZIP_DEFLATED
) as archive:
    for relative_path in requested:
        path = repo / relative_path

        if path.is_file():
            archive.write(path, arcname=relative_path)
            print("Added:", relative_path)
        else:
            print("Missing:", relative_path)

    # Check whether the candidate-planner environment is in this checkout.
    for path in repo.rglob("env.py"):
        if "sim2d" in path.parts:
            relative_path = str(path.relative_to(repo))
            archive.write(path, arcname=relative_path)
            print("Added planner environment:", relative_path)

print("\nSource bundle:", output)

# %%
from pathlib import Path
import json
import zipfile

ROOT = Path("/root/mining_cv")

audit_path = ROOT / "unity_repo_audit.json"
assert audit_path.is_file(), audit_path

audit = json.loads(audit_path.read_text())
repo = Path(audit["checkout"])

assert repo.is_dir(), repo

requested = [
    # Full specification, including sections truncated in the audit.
    "spec.md",

    # Existing capture and scene construction.
    "unity/Assets/Editor/SceneCapture.cs",
    "unity/Assets/Editor/QuarrySceneBuilder.cs",

    # Current camera, transport, actors, and LiDAR implementations.
    "unity/Assets/Scripts/SensorCamera.cs",
    "unity/Assets/Scripts/BrainClient.cs",
    "unity/Assets/Scripts/BrainProtocol.cs",
    "unity/Assets/Scripts/LidarSensor.cs",
    "unity/Assets/Scripts/PedestrianWalker.cs",

    # Brain-side decoding and routing.
    "brain/brain/protocol.py",
    "brain/brain/bridge.py",
    "brain/brain/app.py",
    "brain/brain/policy.py",

    # Message schemas and scene metadata.
    "schema/brain_sim.schema.json",
    "schema/common.schema.json",
    "data/worlds/quarry/world.json",

    # Unity dependencies and layers.
    "unity/Packages/manifest.json",
    "unity/ProjectSettings/TagManager.asset",
]

output = ROOT / "unity_capture_sources.zip"
added = set()

with zipfile.ZipFile(
    output,
    "w",
    compression=zipfile.ZIP_DEFLATED,
) as archive:
    for relative_path in requested:
        path = repo / relative_path

        if path.is_file():
            archive.write(path, arcname=relative_path)
            added.add(relative_path)
            print("Added:", relative_path)
        else:
            print("Not found:", relative_path)

    # Include camera mounting/terrain-following code wherever it lives.
    extra_names = {
        "MineGroundFollow.cs",
        "MineCabCamera.cs",
        "MineOverviewCamera.cs",
    }

    for path in repo.rglob("*.cs"):
        if path.name not in extra_names:
            continue

        relative_path = str(path.relative_to(repo))

        if relative_path not in added:
            archive.write(path, arcname=relative_path)
            added.add(relative_path)
            print("Added:", relative_path)

print("\n✅ Source bundle:", output)
print("Files:", len(added))
print(f"Size: {output.stat().st_size / 1024:.1f} KiB")

# %%
from pathlib import Path
import json
import zipfile

ROOT = Path("/root/mining_cv")

audit_path = ROOT / "unity_repo_audit.json"
assert audit_path.is_file(), audit_path

audit = json.loads(audit_path.read_text())
repo = Path(audit["checkout"])

assert repo.is_dir(), repo

requested = [
    # Full specification, including sections truncated in the audit.
    "spec.md",

    # Existing capture and scene construction.
    "unity/Assets/Editor/SceneCapture.cs",
    "unity/Assets/Editor/QuarrySceneBuilder.cs",

    # Current camera, transport, actors, and LiDAR implementations.
    "unity/Assets/Scripts/SensorCamera.cs",
    "unity/Assets/Scripts/BrainClient.cs",
    "unity/Assets/Scripts/BrainProtocol.cs",
    "unity/Assets/Scripts/LidarSensor.cs",
    "unity/Assets/Scripts/PedestrianWalker.cs",

    # Brain-side decoding and routing.
    "brain/brain/protocol.py",
    "brain/brain/bridge.py",
    "brain/brain/app.py",
    "brain/brain/policy.py",

    # Message schemas and scene metadata.
    "schema/brain_sim.schema.json",
    "schema/common.schema.json",
    "data/worlds/quarry/world.json",

    # Unity dependencies and layers.
    "unity/Packages/manifest.json",
    "unity/ProjectSettings/TagManager.asset",
]

output = ROOT / "unity_capture_sources.zip"
added = set()

with zipfile.ZipFile(
    output,
    "w",
    compression=zipfile.ZIP_DEFLATED,
) as archive:
    for relative_path in requested:
        path = repo / relative_path

        if path.is_file():
            archive.write(path, arcname=relative_path)
            added.add(relative_path)
            print("Added:", relative_path)
        else:
            print("Not found:", relative_path)

    # Include camera mounting/terrain-following code wherever it lives.
    extra_names = {
        "MineGroundFollow.cs",
        "MineCabCamera.cs",
        "MineOverviewCamera.cs",
    }

    for path in repo.rglob("*.cs"):
        if path.name not in extra_names:
            continue

        relative_path = str(path.relative_to(repo))

        if relative_path not in added:
            archive.write(path, arcname=relative_path)
            added.add(relative_path)
            print("Added:", relative_path)

print("\n✅ Source bundle:", output)
print("Files:", len(added))
print(f"Size: {output.stat().st_size / 1024:.1f} KiB")

# %%
%uv pip install -q huggingface_hub


# %%
import os
from huggingface_hub import HfApi

token = os.environ.get("HF_TOKEN")

if not token:
    raise RuntimeError(
        "Add HF_TOKEN in your notebook platform's Secrets settings first."
    )

api = HfApi(token=token)
print("Authenticated as:", api.whoami()["name"])

# %%
from pathlib import Path
import json

from huggingface_hub import HfApi
from ultralytics import YOLO

REPO_ID = "pi-hack-mining/bdd-yolo26n-road-detector"

CHECKPOINT = Path(
    "/root/mining_cv/runs/"
    "yolo26n_bdd_small_377d685c/weights/best.pt"
)

assert CHECKPOINT.is_file(), f"Missing checkpoint: {CHECKPOINT}"

model = YOLO(str(CHECKPOINT))
class_names = model.names

print("Uploading checkpoint:", CHECKPOINT)
print("Actual classes:", class_names)

api = HfApi()

api.create_repo(
    repo_id=REPO_ID,
    repo_type="model",
    private=True,
    exist_ok=True,
)

api.upload_file(
    path_or_fileobj=str(CHECKPOINT),
    path_in_repo="best.pt",
    repo_id=REPO_ID,
    repo_type="model",
    commit_message="Upload trained BDD YOLO26n checkpoint",
)

api.upload_file(
    path_or_fileobj=json.dumps(
        class_names, indent=2
    ).encode("utf-8"),
    path_in_repo="classes.json",
    repo_id=REPO_ID,
    repo_type="model",
)

readme = """---
library_name: ultralytics
pipeline_tag: object-detection
tags:
- object-detection
- bdd100k
- prototype
---

# BDD YOLO26n Road Detector

YOLO26n fine-tuned for 5 epochs on 300 training images,
with 100 validation images from a BDD100K subset.

## Validation results

- mAP50: 0.212
- mAP50-95: 0.133
- Reported recall: 0.221
- Pedestrian recall: 0.164

These are prototype results, not evidence of safe autonomous operation.

## Classes

The actual checkpoint class names are recorded in classes.json.

This is NOT a six-class mining detector. The desired future classes are:
vehicle, digger, car, loader, person, stone.

Some road-object predictions can be mapped to vehicle, car, or person.
Digger, loader, and stone require appropriate training data and evaluation.
Do not rename checkpoint classes to claim these capabilities.

## PPO integration

Convert detections into tracked world-coordinate obstacles using calibrated
depth or LiDAR and camera pose. Preserve existing PPO feature semantics.
Start in shadow mode and retain independent physical obstacle sensing.

## Licensing

Review applicable Ultralytics and original BDD100K terms before
redistribution or commercial deployment.
"""

api.upload_file(
    path_or_fileobj=readme.encode("utf-8"),
    path_in_repo="README.md",
    repo_id=REPO_ID,
    repo_type="model",
)

print("✅ Upload complete:")
print(f"https://huggingface.co/{REPO_ID}")

# %%
from pathlib import Path
import shutil

SOURCE = Path(
    "/root/mining_cv/runs/"
    "yolo26n_bdd_small_377d685c/weights/best.pt"
)

assert SOURCE.is_file(), f"Checkpoint not found: {SOURCE}"

DOWNLOAD_FILE = Path.cwd() / "bdd_yolo26n_best.pt"

if SOURCE.resolve() != DOWNLOAD_FILE.resolve():
    shutil.copy2(SOURCE, DOWNLOAD_FILE)

print("✅ Model ready to download")
print("Location:", DOWNLOAD_FILE)
print(f"Size: {DOWNLOAD_FILE.stat().st_size / 1024**2:.2f} MiB")

# %%
from IPython.display import FileLink, display

display(FileLink(
    "bdd_yolo26n_best.pt",
    result_html_prefix="Download model: ",
))

# %%


# %%
from ultralytics import YOLO

model = YOLO("bdd_yolo26n_best.pt")
print(model.names)

# %%
from getpass import getpass
from huggingface_hub import HfApi

hf_token = getpass("Hugging Face write token: ").strip()

# Explicit authentication overrides stale cached credentials.
api = HfApi(token=)

identity = api.whoami()

print("✅ Authenticated as:", identity["name"])
print(
    "Organizations:",
    [org["name"] for org in identity.get("orgs", [])],
)

# %%



