"""TransNetV2 shot-transition detection (Souček & Lokoč, 2020; github.com/soCzech/TransNetV2, MIT licence).

A small network trained to find every kind of shot transition, gradual ones included (whips, slides, masked
reveals, dissolves), which frame-difference detectors miss: on the BMW garage edit they found 14 of 18 shots.

Runs in numpy from the official TensorFlow weights (tools/models/transnetv2/tf, read by tfcheckpoint), following
the official PyTorch port (inference-pytorch/transnetv2_pytorch.py) layer by layer; no TensorFlow or PyTorch.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import tfcheckpoint

WEIGHTS = Path(__file__).resolve().parents[2] / "tools" / "models" / "transnetv2" / "tf" / "variables" / "variables"
H, W = 27, 48
WINDOW, STEP, PAD = 100, 50, 25
BN_EPS = 1e-3


def available() -> bool:
    return WEIGHTS.with_name("variables.index").exists()


@lru_cache(maxsize=1)
def _weights() -> dict:
    t = tfcheckpoint.read(WEIGHTS)
    v = lambda i: t[f"variables/{i}/.ATTRIBUTES/VARIABLE_VALUE"].astype(np.float32)  # noqa: E731
    n = lambda name: t[f"{name}/.ATTRIBUTES/VARIABLE_VALUE"].astype(np.float32)  # noqa: E731
    blocks = []
    for b in range(6):  # SDDCNN_1/DDCNN_1, SDDCNN_1/DDCNN_2, SDDCNN_2/..., in creation order
        base = 10 * b
        convs = [(v(base + 2 * k), v(base + 2 * k + 1)) for k in range(4)]  # Conv3D_1/_2/_4/_8: spatial, temporal
        blocks.append({"convs": convs, "gamma": v(base + 8), "beta": v(base + 9),
                       "mean": v(60 + 2 * b), "var": v(61 + 2 * b)})
    return {
        "blocks": blocks,
        "sim_proj": (v(78), v(79)), "sim_fc": (v(80), v(81)), "hist_fc": (v(82), v(83)),
        "fc1": (n("fc1/kernel"), n("fc1/bias")),
        "cls1": (n("cls_layer1/kernel"), n("cls_layer1/bias")),
        "cls2": (n("cls_layer2/kernel"), n("cls_layer2/bias")),
    }


def _conv_spatial(x: np.ndarray, k: np.ndarray) -> np.ndarray:
    """(1,3,3) convolution, 'same' padding. x [T,H,W,C], k [1,3,3,C,O]."""
    t, h, w, _ = x.shape
    p = np.pad(x, ((0, 0), (1, 1), (1, 1), (0, 0)))
    out = np.zeros((t, h, w, k.shape[-1]), np.float32)
    for dy in range(3):
        for dx in range(3):
            out += p[:, dy:dy + h, dx:dx + w] @ k[0, dy, dx]
    return out


def _conv_temporal(x: np.ndarray, k: np.ndarray, dilation: int) -> np.ndarray:
    """(3,1,1) convolution dilated in time, 'same' padding. k [3,1,1,C,O]."""
    t = x.shape[0]
    p = np.pad(x, ((dilation, dilation), (0, 0), (0, 0), (0, 0)))
    return sum(p[i * dilation:i * dilation + t] @ k[i, 0, 0] for i in range(3))


def _ddcnn(x: np.ndarray, blk: dict, relu: bool) -> np.ndarray:
    outs = [_conv_temporal(_conv_spatial(x, ks), kt, d) for (ks, kt), d in zip(blk["convs"], (1, 2, 4, 8))]
    y = np.concatenate(outs, axis=-1)
    y = (y - blk["mean"]) / np.sqrt(blk["var"] + BN_EPS) * blk["gamma"] + blk["beta"]
    return np.maximum(y, 0) if relu else y


def _avg_pool(x: np.ndarray) -> np.ndarray:
    t, h, w, c = x.shape
    h2, w2 = h // 2, w // 2
    return x[:, :h2 * 2, :w2 * 2].reshape(t, h2, 2, w2, 2, c).mean(axis=(2, 4))


def _window_similarities(feats: np.ndarray, lookup: int = 101) -> np.ndarray:
    """[T, lookup]: each frame's similarity to the frames lookup//2 before and after it (0 beyond the ends)."""
    t = feats.shape[0]
    sims = feats @ feats.T
    half = (lookup - 1) // 2
    padded = np.pad(sims, ((0, 0), (half, half)))
    idx = np.arange(t)[:, None] + np.arange(lookup)[None, :]
    return padded[np.arange(t)[:, None], idx]


def _normalize(x: np.ndarray) -> np.ndarray:
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def _forward(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """frames uint8 [100, 27, 48, 3] RGB -> (single-frame, all-frames) transition probabilities, [100] each."""
    wts = _weights()
    x = frames.astype(np.float32) / 255.0
    block_feats = []
    for stage in range(3):
        b1, b2 = wts["blocks"][2 * stage], wts["blocks"][2 * stage + 1]
        y = _ddcnn(x, b1, relu=True)
        shortcut = y
        y = _ddcnn(y, b2, relu=False)
        x = _avg_pool(np.maximum(y, 0) + shortcut)
        block_feats.append(x)
    conv = x.reshape(x.shape[0], -1)  # [T, 3*6*256] in (h, w, c) order, as the TF model flattens

    pooled = np.concatenate([f.mean(axis=(1, 2)) for f in block_feats], axis=-1)  # [T, 448]
    proj = _normalize(pooled @ wts["sim_proj"][0] + wts["sim_proj"][1])
    frame_sim = np.maximum(_window_similarities(proj) @ wts["sim_fc"][0] + wts["sim_fc"][1], 0)

    q = frames.astype(np.int32) >> 5
    bins = (q[..., 0] << 6) + (q[..., 1] << 3) + q[..., 2]
    hist = np.stack([np.bincount(b.ravel(), minlength=512) for b in bins]).astype(np.float32)
    color = np.maximum(_window_similarities(_normalize(hist)) @ wts["hist_fc"][0] + wts["hist_fc"][1], 0)

    z = np.concatenate([color, frame_sim, conv], axis=-1)
    z = np.maximum(z @ wts["fc1"][0] + wts["fc1"][1], 0)
    sig = lambda a: 1 / (1 + np.exp(-a))  # noqa: E731
    return sig(z @ wts["cls1"][0] + wts["cls1"][1])[:, 0], sig(z @ wts["cls2"][0] + wts["cls2"][1])[:, 0]


def read_frames(path: str | Path) -> np.ndarray:
    """All frames at 48x27 RGB, as the official inference reads them (ffmpeg scale)."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def predict(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame probability that a transition is at that frame (single), and that the frame is part of one
    (all frames of a gradual transition). Windows of 100 frames, 25 of context each side (as the official code)."""
    n = len(frames)
    end_pad = PAD + STEP - (n % STEP if n % STEP else STEP)
    padded = np.concatenate([frames[:1]] * PAD + [frames] + [frames[-1:]] * end_pad)
    single, many = [], []
    for ptr in range(0, len(padded) - WINDOW + 1, STEP):
        s, m = _forward(padded[ptr:ptr + WINDOW])
        single.append(s[PAD:PAD + STEP])
        many.append(m[PAD:PAD + STEP])
    return np.concatenate(single)[:n], np.concatenate(many)[:n]


def transitions(path: str | Path, threshold: float = 0.5) -> list[tuple[int, int]]:
    """(first, last) frame of every transition: runs of frames whose single-frame probability is above threshold."""
    single, _ = predict(read_frames(path))
    on = single > threshold
    out, start = [], None
    for i, flag in enumerate(on):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(on) - 1))
    return out
