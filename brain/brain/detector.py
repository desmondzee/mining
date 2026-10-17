import ast
import logging
import math
import os
import threading
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

INPUT_H = 384
INPUT_W = 640
PAD_VALUE = 114
CONF = 0.35
IOU = 0.5
MAX_DET = 50
THREADS = 4
WORKERS = 2
MAX_AGE_S = 1.0
DEPTH_PERCENTILE = 20
DEPTH_BAND = 0.2
DEPTH_MAX_M = 500
NORM_DIGITS = 4


@dataclass
class Letterbox:
    scale: float
    pad_x: float
    pad_y: float


def letterbox(rgb: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, Letterbox]:
    h, w = rgb.shape[:2]
    th, tw = size
    scale = min(th / h, tw / w)
    nh, nw = round(h * scale), round(w * scale)
    img = rgb if (nh, nw) == (h, w) else np.asarray(Image.fromarray(np.ascontiguousarray(rgb)).resize((nw, nh), Image.BILINEAR))
    px, py = (tw - nw) / 2, (th - nh) / 2
    top, left = round(py - 0.1), round(px - 0.1)
    canvas = np.full((th, tw, 3), PAD_VALUE, np.uint8)
    canvas[top : top + nh, left : left + nw] = img[..., :3]
    blob = np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], np.float32) / 255.0
    return blob, Letterbox(scale, left, top)


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float, limit: int = MAX_DET) -> np.ndarray:
    order = np.argsort(-scores)
    area = np.maximum(boxes[:, 2] - boxes[:, 0], 0) * np.maximum(boxes[:, 3] - boxes[:, 1], 0)
    keep = []
    while order.size and len(keep) < limit:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        x0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(x1 - x0, 0) * np.maximum(y1 - y0, 0)
        order = rest[inter / np.maximum(area[i] + area[rest] - inter, 1e-9) <= iou]
    return np.asarray(keep, np.int64)


def decode(out: np.ndarray, nc: int, conf: float, iou: float, end2end: bool = False) -> np.ndarray:
    out = np.asarray(out, np.float32)
    out = out[0] if out.ndim == 3 else out
    if end2end or (out.shape[-1] == 6 and nc != 2):
        det = out[out[:, 4] >= conf]
        return det[np.argsort(-det[:, 4])][:MAX_DET]
    if out.shape[1] not in (4 + nc, 5 + nc):
        out = out.T
    if out.shape[1] == 5 + nc:
        scores_all = out[:, 5:] * out[:, 4:5]
    else:
        scores_all = out[:, 4 : 4 + nc]
    cls = scores_all.argmax(1)
    scores = scores_all[np.arange(len(cls)), cls]
    m = scores >= conf
    xywh, scores, cls = out[m, :4], scores[m], cls[m]
    if not len(scores):
        return np.zeros((0, 6), np.float32)
    boxes = np.concatenate([xywh[:, :2] - xywh[:, 2:] / 2, xywh[:, :2] + xywh[:, 2:] / 2], 1)
    offset = cls[:, None].astype(np.float32) * 4096
    keep = nms(boxes + offset, scores, iou)
    return np.concatenate([boxes[keep], scores[keep, None], cls[keep, None].astype(np.float32)], 1)


def unletterbox(boxes: np.ndarray, lb: Letterbox, w: int, h: int) -> np.ndarray:
    out = (boxes - [lb.pad_x, lb.pad_y, lb.pad_x, lb.pad_y]) / lb.scale
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, w)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, h)
    return out


def ground_distance(box, depth: np.ndarray | None, w: int, h: int, hfov: float | None) -> float | None:
    if depth is None or not hfov:
        return None
    dh, dw = depth.shape
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    c0 = int(np.clip((x0 + bw * 0.25) / w * dw, 0, dw - 1))
    c1 = int(np.clip(math.ceil((x1 - bw * 0.25) / w * dw), c0 + 1, dw))
    r0 = int(np.clip((y1 - bh * DEPTH_BAND) / h * dh, 0, dh - 1))
    r1 = int(np.clip(math.ceil(y1 / h * dh), r0 + 1, dh))
    patch = depth[r0:r1, c0:c1]
    patch = patch[np.isfinite(patch) & (patch > 0) & (patch < DEPTH_MAX_M)]
    if not patch.size:
        return None
    z = float(np.percentile(patch, DEPTH_PERCENTILE))
    f = (w / 2) / math.tan(math.radians(hfov) / 2)
    return z * math.hypot(1.0, ((x0 + x1) / 2 - w / 2) / f)


def class_names(meta: dict, nc: int) -> list[str]:
    raw = meta.get("names")
    names = {}
    if raw:
        try:
            parsed = ast.literal_eval(raw)
            names = parsed if isinstance(parsed, dict) else dict(enumerate(parsed))
        except (ValueError, SyntaxError):
            log.warning("unreadable class names in model metadata")
    return [str(names.get(i, i)) for i in range(nc)]


class Detector:
    def __init__(self, path: str, conf: float = CONF, iou: float = IOU, threads: int = THREADS, classes: set[str] | None = None,
                 providers: list[str] | None = None) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path, opts, providers=providers or ort.get_available_providers())
        inp = self.session.get_inputs()[0]
        self.input = inp.name
        hh, ww = inp.shape[2:4]
        self.size = (hh if isinstance(hh, int) else INPUT_H, ww if isinstance(ww, int) else INPUT_W)
        meta = self.session.get_modelmeta().custom_metadata_map
        self.end2end = str(meta.get("end2end", "")).lower() == "true"
        shape = self.session.get_outputs()[0].shape
        nc = len(ast.literal_eval(meta["names"])) if meta.get("names") else max(int(min(shape[1:])) - 4, 1)
        self.names = class_names(meta, nc)
        self.classes = classes
        self.conf, self.iou = conf, iou
        self.path = path

    def __call__(self, rgb: np.ndarray) -> list[dict]:
        h, w = rgb.shape[:2]
        blob, lb = letterbox(rgb, self.size)
        out = self.session.run(None, {self.input: blob})[0]
        det = decode(out, len(self.names), self.conf, self.iou, self.end2end)
        boxes = unletterbox(det[:, :4], lb, w, h)
        items = []
        for box, conf, cls in zip(boxes, det[:, 4], det[:, 5].astype(int)):
            name = self.names[cls] if 0 <= cls < len(self.names) else str(cls)
            if self.classes and name not in self.classes:
                continue
            if box[2] - box[0] < 1 or box[3] - box[1] < 1:
                continue
            items.append({"box": [round(float(v), 1) for v in box], "cls": name, "conf": round(float(conf), 3)})
        return items


@dataclass
class Job:
    rgb: np.ndarray
    depth: np.ndarray | None
    t: float
    hfov: float | None


def to_meta(items: list[dict], depth, w: int, h: int, hfov: float | None, t: float) -> dict:
    out = []
    for d in items:
        x0, y0, x1, y1 = d["box"]
        item = {
            "box": [round(x0 / w, NORM_DIGITS), round(y0 / h, NORM_DIGITS), round(x1 / w, NORM_DIGITS), round(y1 / h, NORM_DIGITS)],
            "cls": d["cls"],
            "conf": round(d["conf"], 2),
        }
        dist = ground_distance(d["box"], depth, w, h, hfov)
        if dist is not None:
            item["dist"] = round(dist, 1)
        out.append(item)
    return {"t": round(t, 3), "items": out}


class DetectorWorker:
    def __init__(self, detect, workers: int = WORKERS, max_age: float = MAX_AGE_S) -> None:
        self.detect = detect
        self.max_age = max_age
        self._pending: dict[str, Job] = {}
        self._latest: dict[str, dict] = {}
        self._cond = threading.Condition()
        self._closed = False
        self.runs = 0
        self.dropped = 0
        self.infer_s = 0.0
        self._threads = [threading.Thread(target=self._run, name=f"detector-{i}", daemon=True) for i in range(workers)]
        for th in self._threads:
            th.start()

    def submit(self, camera: str, rgb: np.ndarray, depth: np.ndarray | None, t: float, hfov: float | None = None) -> None:
        with self._cond:
            if camera in self._pending:
                self.dropped += 1
            self._pending[camera] = Job(rgb, depth, t, hfov)
            self._cond.notify()

    def latest(self, camera: str, now: float | None = None) -> dict | None:
        det = self._latest.get(camera)
        if det is None or (now if now is not None else time.time()) - det["t"] > self.max_age:
            return None
        return det

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        for th in self._threads:
            th.join(timeout=2)

    def stats(self) -> dict:
        return {"runs": self.runs, "dropped": self.dropped, "infer_ms": round(1000 * self.infer_s / max(self.runs, 1), 1)}

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._pending or self._closed)
                if self._closed:
                    return
                camera = min(self._pending, key=lambda c: self._pending[c].t)
                job = self._pending.pop(camera)
            try:
                start = time.perf_counter()
                items = self.detect(job.rgb)
                elapsed = time.perf_counter() - start
            except Exception:
                log.exception("detector failed on %s", camera)
                continue
            h, w = job.rgb.shape[:2]
            det = to_meta(items, job.depth, w, h, job.hfov, job.t)
            with self._cond:
                self.runs += 1
                self.infer_s += elapsed
                prev = self._latest.get(camera)
                if prev is None or prev["t"] <= job.t:
                    self._latest[camera] = det


def from_env() -> DetectorWorker | None:
    path = os.environ.get("DETECTOR_MODEL")
    if not path:
        return None
    if not os.path.isfile(path):
        log.warning("DETECTOR_MODEL %s not found; detector off", path)
        return None
    classes = {c.strip() for c in os.environ.get("DETECTOR_CLASSES", "").split(",") if c.strip()} or None
    try:
        det = Detector(
            path,
            conf=float(os.environ.get("DETECTOR_CONF", CONF)),
            threads=int(os.environ.get("DETECTOR_THREADS", THREADS)),
            classes=classes,
            providers=[p.strip() for p in os.environ.get("DETECTOR_PROVIDERS", "").split(",") if p.strip()] or None,
        )
    except ImportError:
        log.warning("onnxruntime not installed (uv sync --extra detector); detector off")
        return None
    log.info("detector %s input %s classes %d providers %s", path, det.size, len(det.names), det.session.get_providers())
    return DetectorWorker(det, workers=int(os.environ.get("DETECTOR_WORKERS", WORKERS)))
