"""Transitions that mix two shots on screen: the wheel portal (detected here), the corner panel and
the car cut-out (marked in the move review; see labels.py).

Wheel portal: the next shot is zoomed hugely into its wheel and zooms out, while the wheel's opening
shows the previous shot. Its signature is one circle that shrinks steadily over several frames
around the cut: on the TikTok car edit 134 -> 27 px (at 216 px width) over 8 frames, while circles
elsewhere (wheels, headlights) only jitter or grow (2026-11-17).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..media import iter_frames

WIDTH = 216
WINDOW = 9  # frames searched on each side of a cut
MIN_RUN = 5  # frames the circle keeps shrinking
MIN_SHRINK = 2.5  # first radius / last radius
NOISE = 1.05  # a frame's radius may exceed the one before by this much and still count as shrinking


def _circle(frame: np.ndarray) -> tuple[float, float, float] | None:
    w = frame.shape[1]
    g = cv2.medianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 5)
    c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.5, minDist=w, param1=90, param2=45,
                         minRadius=int(0.12 * w), maxRadius=int(1.2 * w))
    return None if c is None else tuple(float(v) for v in c[0][0])


def shrinking_circle(circles: list[tuple[float, float, float] | None], width: int) -> bool:
    """True when consecutive frames hold one circle that keeps shrinking (centre steady) by MIN_SHRINK."""
    run: list[tuple[float, float, float]] = []
    for c in circles + [None]:
        ok = c is not None and run and c[2] <= run[-1][2] * NOISE and np.hypot(c[0] - run[-1][0], c[1] - run[-1][1]) < 0.25 * width
        if ok:
            run.append(c)
            continue
        if len(run) >= MIN_RUN and max(r[2] for r in run) / run[-1][2] >= MIN_SHRINK:
            return True
        run = [c] if c is not None else []
    return False


def find_portals(path: str | Path, cuts: list[float], fps: float) -> set[float]:
    """Cuts (seconds, as given) that are wheel portals."""
    frames = [f for _, f in iter_frames(path, sample_fps=fps, width=WIDTH)]
    out = set()
    for cut in cuts:
        k = round(cut * fps)
        window = frames[max(0, k - WINDOW):k + WINDOW]
        if window and shrinking_circle([_circle(f) for f in window], WIDTH):
            out.add(cut)
    return out
