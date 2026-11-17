"""Edit style: velocity edits vs calm edits, and the reference's motion wave.

A velocity edit (TikTok car/phonk style) never stops moving: footage is sped up between music
hits (heavy smear) and slowed on them (sharp), and the camera drifts all the time, so motion
rises and falls like a wave with the beat. A calm edit stands still between moves and stays
sharp; its cuts do the work. The two need different rendering, so the cloner labels each
reference and keeps its per-frame motion (optical flow at 160 px width, see visual.track_frames).

Measured on real references (2026-11-15): the TikTok velocity edit has typical sharpness 0.13 of
its sharpest frames and 12% still frames; the calm reels 0.55 and 9-34%.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..media import iter_frames

SMEARED = 0.3  # typical sharpness (of the sharpest 5%) below which a reference is mostly smeared
RARELY_STILL = 0.2  # share of still frames below which the motion never really stops
STILL = 0.15  # px/frame at 160 px width: a frame moving less than this is standing still


def sharpness(path: str | Path, fps: float) -> np.ndarray:
    """Per frame: Laplacian variance relative to the video's sharpest 5% (1 = as sharp as it gets)."""
    s = np.array([cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var()
                  for _, f in iter_frames(path, sample_fps=fps, width=360)])
    return s / (np.quantile(s, 0.95) + 1e-6) if len(s) else s


def still_share(flow: np.ndarray) -> float:
    flow = np.asarray(flow)[1:]  # the first frame has no motion measure
    return float((flow < STILL).mean()) if len(flow) else 1.0


def edit_style(flow: np.ndarray, sharp: np.ndarray) -> str:
    """"velocity" when the reference is mostly smeared and its motion never stops, else "calm"."""
    if len(flow) < 10 or len(sharp) < 10:
        return "calm"
    return "velocity" if float(np.median(sharp)) < SMEARED and still_share(flow) < RARELY_STILL else "calm"


def segment_velocity(flow: np.ndarray, start: float, end: float, fps: float) -> list[float]:
    """The shot's motion per reference frame (px/frame at 160 px width), lightly smoothed.

    The first frame of a shot measures the cut itself, not motion, so it copies its neighbour.
    """
    a, b = round(start * fps), max(round(start * fps) + 1, round(end * fps))
    v = np.array(flow[a:b], dtype=np.float64)
    if len(v) > 1:
        v[0] = v[1]
    if len(v) >= 3:
        v = np.convolve(np.pad(v, 1, mode="edge"), np.ones(3) / 3, "valid")
    return [round(float(x), 3) for x in v]
