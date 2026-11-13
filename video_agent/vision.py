"""Seeing what is in a frame: the main subject (YOLOX detector) and what kind of shot it is (CLIP).

Models live in tools/models/ (not in git; see README):
  yolox_s.onnx                       Megvii YOLOX-s, Apache-2.0
  clip/vision_model_quantized.onnx   OpenAI CLIP ViT-B/32 (Xenova ONNX export), MIT
  clip/text_model_quantized.onnx
  clip/tokenizer.json
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODELS = Path(__file__).resolve().parents[1] / "tools" / "models"
YOLOX = MODELS / "yolox_s.onnx"
CLIP_DIR = MODELS / "clip"

# COCO classes worth building an edit around, in order of preference when several are present.
SUBJECT_CLASSES = {2: "car", 3: "motorcycle", 7: "truck", 5: "bus", 0: "person", 1: "bicycle", 8: "boat", 4: "airplane"}


@dataclass
class Box:
    cls: str
    score: float
    x0: float  # normalized 0..1
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def area(self) -> float:
        return self.w * self.h


def available() -> bool:
    return YOLOX.exists() and (CLIP_DIR / "vision_model_quantized.onnx").exists()


@lru_cache
def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


# ---------------------------------------------------------------- subject detection (YOLOX)


@lru_cache
def _grids(size: int = 640) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        n = size // stride
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), stride))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def detect(frame: np.ndarray, min_score: float = 0.35) -> list[Box]:
    """Subjects in a BGR frame, largest first."""
    size = 640
    h, w = frame.shape[:2]
    ratio = min(size / h, size / w)
    padded = np.full((size, size, 3), 114, np.uint8)
    padded[: int(h * ratio), : int(w * ratio)] = cv2.resize(frame, (int(w * ratio), int(h * ratio)), interpolation=cv2.INTER_LINEAR)
    blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
    sess = _session(YOLOX)
    out = sess.run(None, {sess.get_inputs()[0].name: blob})[0][0]
    grids, strides = _grids(size)
    out[:, :2] = (out[:, :2] + grids) * strides
    out[:, 2:4] = np.exp(out[:, 2:4]) * strides
    scores = out[:, 4:5] * out[:, 5:]
    cls = scores.argmax(1)
    best = scores[np.arange(len(cls)), cls]
    keep = (best >= min_score) & np.isin(cls, list(SUBJECT_CLASSES))
    if not keep.any():
        return []
    b, s, c = out[keep, :4] / ratio, best[keep], cls[keep]
    xywh = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 2], b[:, 3]], 1)
    idx = cv2.dnn.NMSBoxes(xywh.tolist(), s.tolist(), min_score, 0.45)
    boxes = []
    for i in np.array(idx).reshape(-1):
        x, y, bw, bh = xywh[i]
        boxes.append(
            Box(SUBJECT_CLASSES[int(c[i])], float(s[i]), max(0.0, x / w), max(0.0, y / h), min(1.0, (x + bw) / w), min(1.0, (y + bh) / h))
        )
    return sorted(boxes, key=lambda bx: bx.area, reverse=True)


def main_subject(frames: list[np.ndarray]) -> Box | None:
    """The subject of a shot: the class with the most total area, its median box over the frames."""
    per_frame = [detect(f) for f in frames]
    totals: dict[str, float] = {}
    for boxes in per_frame:
        for b in boxes:
            totals[b.cls] = totals.get(b.cls, 0.0) + b.area * b.score
    if not totals:
        return None
    cls = max(totals, key=totals.get)
    picks = [next(b for b in boxes if b.cls == cls) for boxes in per_frame if any(b.cls == cls for b in boxes)]
    med = np.median([[b.x0, b.y0, b.x1, b.y1, b.score] for b in picks], axis=0)
    return Box(cls, float(med[4]), *map(float, med[:4]))


# ---------------------------------------------------------------- shot understanding (CLIP)

_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


def embed_images(frames: list[np.ndarray]) -> np.ndarray:
    """(N, 512) unit-length CLIP embeddings of BGR frames."""
    if not frames:
        return np.zeros((0, 512), np.float32)
    batch = []
    for f in frames:
        h, w = f.shape[:2]
        k = 224 / min(h, w)
        f = cv2.resize(f, (max(224, round(w * k)), max(224, round(h * k))), interpolation=cv2.INTER_CUBIC)
        h, w = f.shape[:2]
        f = f[(h - 224) // 2 : (h - 224) // 2 + 224, (w - 224) // 2 : (w - 224) // 2 + 224]
        rgb = cv2.cvtColor(f, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
        batch.append(((rgb - _MEAN) / _STD).transpose(2, 0, 1))
    sess = _session(CLIP_DIR / "vision_model_quantized.onnx")
    names = [o.name for o in sess.get_outputs()]
    out = sess.run(None, {sess.get_inputs()[0].name: np.stack(batch)})
    emb = out[names.index("image_embeds")] if "image_embeds" in names else out[0]
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)


@lru_cache
def _tokenizer():
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(CLIP_DIR / "tokenizer.json"))
    tok.enable_padding(length=77, pad_id=49407)
    tok.enable_truncation(max_length=77)
    return tok


def embed_texts(texts: list[str]) -> np.ndarray:
    """(N, 512) unit-length CLIP embeddings of text prompts."""
    enc = _tokenizer().encode_batch(texts)
    ids = np.array([e.ids for e in enc], np.int64)
    sess = _session(CLIP_DIR / "text_model_quantized.onnx")
    feeds = {}
    for inp in sess.get_inputs():
        feeds[inp.name] = ids if inp.name == "input_ids" else np.array([e.attention_mask for e in enc], np.int64)
    names = [o.name for o in sess.get_outputs()]
    out = sess.run(None, feeds)
    emb = out[names.index("text_embeds")] if "text_embeds" in names else out[0]
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)
