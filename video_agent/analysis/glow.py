"""Beat glows: the editor switches the picture to a warm, golden look for a few frames on a beat, then back
(the TikTok car edit: sky cool -6.7 -> golden +6.6 at 4.00 s on the 3.98 s beat, held 7 frames; again at
6.77 s). Told apart from new footage by the picture staying the same while its colour jumps.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..blueprint import Glow
from ..media import iter_frames, thumb

STEP = 6.0  # Lab b (yellow) rise of the upper half, median of 3 frames after vs before
SAME = 0.8  # picture similarity across the step: above it the footage is the same, so the colour is an effect
CUT_GAP = 3  # frames: steps this close to a cut are the cut itself
HIT_GAP = 0.1  # seconds: a glow starts this close to a music hit
MIN_FRAMES, MAX_FRAMES = 3, 15


SAME_SHOT = 0.13  # CLIP change (analysis/transitions.clip_change) below which the footage is still the same shot.
# The thumbnail test alone was fooled by motion blur: the TikTok's two "glows" (4.00 s, 6.77 s) were whips into new,
# warmer shots (CLIP change 0.18 and 0.149; the BMW edit's real light flares 0.12).


def find_glows(path: str | Path, fps: float, cuts: list[float], hits: list[float], change: np.ndarray | None = None
               ) -> list[Glow]:
    frames = [f for _, f in iter_frames(path, sample_fps=fps, width=180)]
    if len(frames) < 8:
        return []
    labs = [cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32) for f in frames]
    warm = np.array([(l[: l.shape[0] // 2, :, 2] - 128).mean() for l in labs])
    bright = np.array([(l[: l.shape[0] // 2, :, 0]).mean() * 100 / 255 for l in labs])
    cut_frames = [round(c * fps) for c in cuts]
    out, i = [], 3
    while i < len(frames) - 3:
        step = np.median(warm[i:i + 3]) - np.median(warm[i - 3:i])
        near_cut = any(abs(i - c) <= CUT_GAP for c in cut_frames)
        near_hit = any(abs(i / fps - h) <= HIT_GAP for h in hits)
        same_shot = change is None or float(change[max(0, i - 3):i + 4].max()) < SAME_SHOT
        if step > STEP and not near_cut and near_hit and same_shot and float(thumb(frames[i - 2]) @ thumb(frames[i + 2])) > SAME:
            i += int(np.argmax(np.diff(warm[i - 1:i + 3])))  # the frame where the colour actually jumps
            before, level = np.median(warm[i - 3:i]), np.median(warm[i:i + 3])
            j = i + 1
            while j < len(frames) and warm[j] > before + 0.5 * (level - before) and not any(abs(j - c) <= 1 for c in cut_frames):
                j += 1
            n = j - i
            if MIN_FRAMES <= n <= MAX_FRAMES:
                out.append(Glow(t=round(i / fps, 3), frames=n, warm=round(float(step), 2),
                                bright=round(float(np.median(bright[i:i + 3]) - np.median(bright[i - 3:i])), 2)))
                i = j
                continue
        i += 1
    return out
