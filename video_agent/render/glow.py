"""Beat glows (analysis/glow.py), laid over the finished edit: for a few frames on a beat the top of the
picture turns warm and golden and its highlights bloom, like light catching the lens, then it returns.

Colour-exact like the transitions: streamed frame by frame, re-encoded losslessly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import Glow
from .transitions import _reader, _writer

TOP, BOTTOM = 1.0, 0.35  # share of the warmth at the top and bottom of the frame (the TikTok's: strongest in the sky)
RED_SHARE = 0.35  # a (red) added per unit of b (yellow): golden, not lemon
BLOOM = 0.35  # how strongly the highlights bloom at full glow
BLOOM_SIZE = 0.02  # bloom radius, share of the frame width
MIDTONE = 0.35  # share of the warmth that reaches darker areas (houses, trees, the car)
MAX_DARKEN = 6.0  # the golden sky may darken by at most this (Lab L); more read as dim, not golden


def envelope(k: int, frames: int) -> float:
    """Strength on frame k of a glow: on at once (the beat), held, easing off over its last 2 frames."""
    if k < 0 or k >= frames:
        return 0.0
    left = frames - k
    return 1.0 if left > 2 else left / 3


def glow_frame(f: np.ndarray, g: Glow, strength: float) -> np.ndarray:
    if strength <= 0:
        return f
    h, w = f.shape[:2]
    lab = cv2.cvtColor(f.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    ramp = np.linspace(TOP, BOTTOM, h, dtype=np.float32)[:, None] * strength
    # The reference's glow turns its *sky* golden; spread evenly over a frame of houses and trees it looked
    # muddy brown (McLaren stills). Bright areas take the full change, the rest a light warmth.
    sky = np.clip((lab[..., 0] - 55) / 30, 0, 1)
    tint = ramp * (MIDTONE + (1 - MIDTONE) * sky)
    lab[..., 2] += g.warm * tint
    lab[..., 1] += g.warm * RED_SHARE * tint
    lab[..., 0] = np.clip(lab[..., 0] + max(g.bright, -MAX_DARKEN) * ramp * sky, 0, 100)
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    hi = np.clip((out - 0.75) / 0.25, 0, 1)  # what is already bright glows
    bloom = cv2.GaussianBlur(hi, (0, 0), BLOOM_SIZE * w) * np.array([0.75, 0.9, 1.0], np.float32)  # warm bloom
    out = 1 - (1 - out) * (1 - BLOOM * strength * bloom)  # screen blend
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def apply(src: Path, dst: Path, glows: list[Glow], w: int, h: int, fps: int, encode: list) -> None:
    starts = [(round(g.t * fps), g) for g in glows]
    writer = _writer(dst, w, h, fps, encode)
    for i, f in enumerate(_reader(src, w, h)):
        for s, g in starts:
            f = glow_frame(f, g, envelope(i - s, g.frames))
        writer.stdin.write(f.tobytes())
    writer.stdin.close()
    if writer.wait() != 0:
        raise RuntimeError("ffmpeg failed while writing the glows")
