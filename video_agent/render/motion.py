"""Builds a clean camera curve for a shot from its edit events (see analysis/events.py).

Used when the footage is not the reference's own: instead of copying the reference camera's
literal path, the editor's moves are re-created on the new footage at the same moments.
"""

from __future__ import annotations

import math

import numpy as np

from ..analysis.camera import _decompose
from ..blueprint import CameraCurve, EditEvent, Subject
from ..vision import Box

WHIP_FRAMES = 3
WHIP_DISTANCE = 0.45  # frame widths at full strength
PUNCH_DECAY = 4.0  # frames


def synthesize(events: list[EditEvent], n_frames: int, fps: float) -> tuple[CameraCurve, np.ndarray]:
    """Returns the curve and a mask of frames that may show mirrored edges (whip frames)."""
    t = np.arange(n_frames) / fps
    f = np.arange(n_frames, dtype=np.float64)
    log_s = np.zeros(n_frames)
    rot = np.zeros(n_frames)
    x = np.zeros(n_frames)
    y = np.zeros(n_frames)
    whip = np.zeros(n_frames, bool)

    for e in events:
        if e.kind == "push":
            log_s += e.strength * t
        elif e.kind == "punch":
            f0 = e.t * fps
            k = np.where(f >= f0, np.exp(-(f - f0) / PUNCH_DECAY), 0.0)
            log_s += math.copysign(e.strength, e.zoom or 1.0) * k
        elif e.kind == "shake":
            f0, f1 = e.t * fps, (e.t + e.duration) * fps
            env = np.clip(np.minimum(f - f0 + 1, f1 - f) / 2, 0, 1)
            amp = 0.006 + 0.02 * e.strength
            x += amp * env * np.sin(2 * math.pi * 9.3 * t + 0.4)
            y += amp * env * np.sin(2 * math.pi * 12.7 * t + 1.9)
            rot += math.radians(max(0.5, e.rot)) * env * np.sin(2 * math.pi * 11.1 * t)
        elif e.kind in ("whip_in", "whip_out"):
            k = min(WHIP_FRAMES, n_frames)
            if e.kind == "whip_in":
                idx = np.arange(k)
                ramp = ((k - idx) / k) ** 2  # arrives fast, settles
                sign = -1.0  # comes in from where the previous shot left
            else:
                idx = np.arange(n_frames - k, n_frames)
                ramp = ((idx - (n_frames - k - 1)) / k) ** 2  # accelerates out
                sign = 1.0
            dx, dy = (e.dx, e.dy) if (e.dx or e.dy) else (1.0, 0.0)
            d = WHIP_DISTANCE * e.strength
            x[idx] += sign * d * dx * ramp
            y[idx] += sign * d * dy * ramp
            log_s[idx] += 0.12 * e.strength * ramp * (e.zoom or 1.0)
            whip[idx] = True

    curve = CameraCurve(fps=fps, s=np.exp(log_s).tolist(), r=rot.tolist(), x=x.tolist(), y=y.tolist())
    return curve, whip


MAX_REFRAME = 2.0  # never zoom footage in more than this to match framing (it goes soft)


def reframe(curve: CameraCurve, ref: Subject | None, foot: Box | None, width: int, height: int) -> CameraCurve:
    """Put the footage's subject where the reference had it (position and size), then play the
    edit's moves around the subject instead of the frame centre."""
    if ref is None or foot is None:
        return curve
    scale = min(MAX_REFRAME, max(1.0, ref.h / max(foot.h, 1e-3)))
    # where the subject lands after scaling about the centre, and the shift that moves it to the
    # reference position; limited so the frame stays covered
    tx = (ref.cx - 0.5) - scale * (foot.cx - 0.5)
    ty = (ref.cy - 0.5) - scale * (foot.cy - 0.5)
    lim = (scale - 1) / 2
    tx, ty = float(np.clip(tx, -lim, lim)), float(np.clip(ty, -lim, lim))
    frame = _matrix(scale, 0.0, tx, ty, width, height)
    # moves happen around the subject: conjugate each frame's move with a shift to the subject
    px, py = (ref.cx - 0.5) * width, (ref.cy - 0.5) * height
    to_subject = np.array([[1, 0, -px], [0, 1, -py], [0, 0, 1]], float)
    back = np.array([[1, 0, px], [0, 1, py], [0, 0, 1]], float)
    out = CameraCurve(fps=curve.fps)
    for s, r, x, y in zip(curve.s, curve.r, curve.x, curve.y):
        move = back @ _matrix(s, r, x, y, width, height) @ to_subject
        s2, r2, x2, y2 = _decompose(move @ frame, width, height)
        out.s.append(s2)
        out.r.append(r2)
        out.x.append(x2)
        out.y.append(y2)
    return out


def _matrix(s: float, r: float, x: float, y: float, w: int, h: int) -> np.ndarray:
    """Centre-based similarity transform as a 3x3 matrix (same convention as the compositor)."""
    a = s * np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
    c = np.array([w / 2, h / 2])
    m = np.eye(3)
    m[:2, :2] = a
    m[:2, 2] = c + [x * w, y * h] - a @ c
    return m
