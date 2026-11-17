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

WHIP_FRAMES = 4
# Frame widths at full strength. 0.45 made whips measure 1.9x the TikTok's motion at its cuts
# (6.1 vs 3.2 px/frame, both McLaren edits, 2026-11-15): part of "too much shaking".
WHIP_DISTANCE = 0.25
# Velocity edits ease whips gentler (power 2, not 3): the cubic put 58% of a whip's travel on one
# frame, the biggest spike in the McLaren test edit (2026-11-15); squared spreads it 44/31/19/6%.
WHIP_EASE, VELOCITY_WHIP_EASE = 3.0, 2.0
PUNCH_ATTACK = (0.65, 1.0)  # share of the punch reached on the hit frame and the next (fast ease-out)
# Velocity edits: the punch flows out of the drift over 3 frames. 65% on one frame read as a cut
# once calibration raised punches to x0.55 (McLaren v4, 3.93s and 4.27s, 2026-11-16).
VELOCITY_PUNCH_ATTACK = (0.35, 0.75, 1.0)
PUNCH_RELEASE = 8  # frames to ease back
PUNCH_UNDERSHOOT = 0.08  # settles slightly past rest, like a spring
JOLT_HZ = 8.0
JOLT_AMP = 0.022  # frame widths at full strength
# Motion budget: whatever piles up on one frame stays within what reads as an edit, not chaos.
MAX_SHIFT, MAX_LOG_ZOOM, MAX_ROT = 0.09, math.log(1.6), math.radians(4.0)
# Drift per frame at amplitude 1 and average wave: pan (frame widths), push (log zoom), roll (radians).
DRIFT_PAN, DRIFT_ZOOM, DRIFT_ROLL = 0.004, 0.004, math.radians(0.03)
# Total drift a shot may travel: beyond this a pan needs so much cover zoom the picture goes soft,
# so a long shot pans slower and the push carries more.
DRIFT_TRAVEL, DRIFT_ZOOM_TRAVEL, DRIFT_ROLL_TRAVEL = 0.12, math.log(1.2), math.radians(3.0)  # picture quality first (Q12)


SWAY_ROUND = 0.95  # 1 = sharp turnarounds; lower rounds them (the zoom and roll ease back like keyframes)


def _fold(v: np.ndarray, travel: float) -> np.ndarray:
    """Keep moving at the same speed but sway back and forth within `travel` (rounded turns), centred
    on 0. A path that fits already is only centred."""
    span = float(v.max() - v.min()) if len(v) > 1 else 0.0
    if span <= travel:
        return v - (v.max() + v.min()) / 2
    phase = (v - v[len(v) // 2]) * math.pi / travel  # one back-and-forth (2 travels) per 2 pi
    return travel / 2 * np.arcsin(SWAY_ROUND * np.sin(phase)) / math.asin(SWAY_ROUND)


def _arc(step: np.ndarray, direction: float, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """Path moving `step` per frame, starting along `direction` and turning 1/radius per unit moved."""
    heading = direction + np.concatenate([[0.0], np.cumsum(step[:-1])]) / radius
    x = np.concatenate([[0.0], np.cumsum(step[1:] * np.cos(heading[1:]))])
    y = np.concatenate([[0.0], np.cumsum(step[1:] * np.sin(heading[1:]))])
    return x, y


def _pan(step: np.ndarray, direction: float, travel: float) -> tuple[np.ndarray, np.ndarray]:
    """A pan that never stops: straight while it fits in `travel`, else it curves (the tightest
    turn that still fits; at most a circle `travel` wide). Centred in the frame."""
    def fit(x, y):
        k = min(1.0, travel / max(x.max() - x.min(), y.max() - y.min(), 1e-12))  # frames are steps, not a true circle
        return k * (x - (x.max() + x.min()) / 2), k * (y - (y.max() + y.min()) / 2)

    def extent(r):
        x, y = _arc(step, direction, r)
        return max(x.max() - x.min(), y.max() - y.min())

    x, y = _arc(step, direction, math.inf)
    if extent(math.inf) <= travel:
        return fit(x, y)
    lo, hi = travel / 2, max(travel / 2, float(step.sum()))  # a circle fits at lo; far too gentle at hi
    if extent(hi) <= travel:
        return fit(*_arc(step, direction, hi))
    for _ in range(30):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if extent(mid) <= travel else (lo, mid)
    return fit(*_arc(step, direction, lo))


def drift_path(wave: np.ndarray, direction: float, amp: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Never-stopping camera: x, y, log zoom and roll per frame, each moving at a speed that follows
    the motion wave.

    The quality caps (DRIFT_*_TRAVEL) limit where the camera may go, not how fast it moves: a long
    shot's pan curves instead of stopping at the edge, and its push and roll ease back and forth.
    (Scaling the path down to fit slowed long shots to 0.6 px/frame against the TikTok's 2, McLaren
    v3, 2026-11-15.) Pans pass near the centre; the push stays at or above 1x.

    The drift does not follow the reference camera's own path: shrunk into the caps that path scores
    no better on camera than standing still (16% vs 15%), and the sway is what makes the edit smooth
    and flowing (McLaren, modelled 2026-11-17; PROBLEMS.md).
    """
    step = np.concatenate([[0.0], wave[:-1]]) * amp
    walk = np.cumsum(step)
    px, py = _pan(step * DRIFT_PAN, direction, DRIFT_TRAVEL)
    zoom = _fold(walk * DRIFT_ZOOM, DRIFT_ZOOM_TRAVEL)
    zoom = zoom - zoom.min()
    roll = _fold(walk * DRIFT_ROLL, DRIFT_ROLL_TRAVEL) * (1.0 if math.cos(direction) >= 0 else -1.0)
    return px, py * (9 / 16), zoom, roll


def drift_directions(segments: list) -> dict[float, float]:
    """Pan direction per shot start (radians): it carries on across cuts, so shots feel connected,
    and turns only where a whip sends the picture somewhere else."""
    out, angle = {}, 0.0
    for seg in segments:
        inn = next((e for e in seg.events if e.kind == "whip_in" and (e.dx or e.dy)), None)
        if inn is not None:
            angle = math.atan2(inn.dy, inn.dx)
        out[round(seg.start, 3)] = angle
    return out


def ease_out(u: np.ndarray) -> np.ndarray:
    return 1 - (1 - u) ** 3


def ease_in_out(u: np.ndarray) -> np.ndarray:
    return np.where(u < 0.5, 4 * u**3, 1 - (-2 * u + 2) ** 3 / 2)


def synthesize(events: list[EditEvent], n_frames: int, fps: float, seg_start: float = 0.0, offset_frames: int = 0,
               drift: np.ndarray | None = None, drift_dir: float = 0.0, drift_amp: float = 0.0, gain: float = 1.0,
               whip_gain: float = 1.0, whip_ease: float = WHIP_EASE, punch_attack: tuple[float, ...] = PUNCH_ATTACK,
               ) -> tuple[CameraCurve, np.ndarray, np.ndarray]:
    """The curve, a mask of frames that may show mirrored edges (whips) and a mask of fast frames
    (whips, punch attacks: where motion blur belongs when the reference has none to copy).

    Moves on hits land on the hit's own frame of the output (seg_start keeps the frame grid the
    same as the final timeline); offset_frames shifts them (-1 = a frame early).
    Every move is eased like hand-set keyframes, so moves flow instead of jumping.

    Velocity edits: `drift` (per-frame motion wave, see render/velocity.wave) adds a camera that
    never stops (push in plus a pan along drift_dir), and `gain` scales punches and shakes so the
    moves ride on the drift instead of bursting out of stillness. Whips are scaled by `whip_gain`
    instead (so they speed up out of the drift, not jump out of stillness) and eased with power
    `whip_ease` (lower = travel spread more evenly over the whip's frames). `punch_attack` is the
    share of a punch reached per frame; its biggest step lands on the hit frame.
    """
    f = np.arange(n_frames, dtype=np.float64)
    log_s = np.zeros(n_frames)
    rot = np.zeros(n_frames)
    x = np.zeros(n_frames)
    y = np.zeros(n_frames)
    whip_parts = [np.zeros(n_frames) for _ in range(3)]  # x, y, log_s of whips (not scaled by gain)
    whip = np.zeros(n_frames, bool)
    fast = np.zeros(n_frames, bool)
    first = round(seg_start * fps)

    def hit_frame(t: float) -> int:
        return round((seg_start + t) * fps) - first + offset_frames

    for k, e in enumerate(events):
        if e.kind == "push":
            log_s += e.strength * f / fps
        elif e.kind == "punch":
            f0 = hit_frame(e.t)
            f0 -= int(np.argmax(np.diff(punch_attack, prepend=0.0)))  # the biggest step lands on the hit
            amp = math.copysign(e.strength, e.zoom or 1.0)
            env = np.zeros(n_frames)
            for j, v in enumerate(punch_attack):
                if 0 <= f0 + j < n_frames:
                    env[f0 + j] = v
                    fast[f0 + j] = True
            rel = f - (f0 + len(punch_attack) - 1)
            down = (rel > 0) & (rel <= PUNCH_RELEASE)
            env[down] = 1 - (1 + PUNCH_UNDERSHOOT) * ease_in_out(rel[down] / PUNCH_RELEASE)
            settle = (rel > PUNCH_RELEASE) & (rel <= PUNCH_RELEASE + 4)
            env[settle] = -PUNCH_UNDERSHOOT * (1 - ease_out((rel[settle] - PUNCH_RELEASE) / 4))
            log_s += amp * env
        elif e.kind == "shake":
            # A jolt: biggest offset on the hit frame, then a fast damped ring that dies out.
            f0 = hit_frame(e.t)
            n = f - f0
            on = n >= 0
            decay = max(e.duration, 0.1) * fps / 3.0  # ~95% gone after the event's duration
            ring = np.where(on, np.exp(-n / decay) * np.cos(2 * math.pi * JOLT_HZ * n / fps), 0.0)
            angle = (k * 2.399) % (2 * math.pi)  # golden-angle steps: each jolt a different direction
            amp = JOLT_AMP * max(0.25, e.strength)
            x += amp * math.cos(angle) * ring
            y += amp * math.sin(angle) * ring * (9 / 16)
            rot += math.radians(e.rot) * math.copysign(1.0, math.sin(angle)) * ring
        elif e.kind in ("whip_in", "whip_out"):
            k_ = min(WHIP_FRAMES, n_frames)
            if e.kind == "whip_in":
                idx = np.arange(k_)
                ramp = (1 - idx / k_) ** whip_ease  # arrives fast, decelerates into place
                sign = -1.0  # starts offset against its direction and travels along it
            else:
                idx = np.arange(n_frames - k_, n_frames)
                ramp = ((idx - (n_frames - k_ - 1)) / k_) ** whip_ease  # accelerates out of the shot
                sign = 1.0
            dx, dy = (e.dx, e.dy) if (e.dx or e.dy) else (1.0, 0.0)
            d = WHIP_DISTANCE * e.strength * whip_gain
            whip_parts[0][idx] += sign * d * dx * ramp
            whip_parts[1][idx] += sign * d * dy * ramp
            whip_parts[2][idx] += 0.12 * e.strength * whip_gain * ramp * (e.zoom or 1.0)
            whip[idx] = fast[idx] = True

    x, y, log_s, rot = gain * x + whip_parts[0], gain * y + whip_parts[1], gain * log_s + whip_parts[2], gain * rot
    if drift is not None and drift_amp > 0:
        dx_, dy_, dz, dr = drift_path(drift[:n_frames], drift_dir, drift_amp)
        x, y, log_s, rot = x + dx_, y + dy_, log_s + dz, rot + dr

    calm = ~whip  # whips may travel far (hidden by blur and the cut); everything else stays in budget
    x[calm] = np.clip(x[calm], -MAX_SHIFT, MAX_SHIFT)
    y[calm] = np.clip(y[calm], -MAX_SHIFT, MAX_SHIFT)
    log_s = np.clip(log_s, -MAX_LOG_ZOOM, MAX_LOG_ZOOM)
    rot = np.clip(rot, -MAX_ROT, MAX_ROT)
    curve = CameraCurve(fps=fps, s=np.exp(log_s).tolist(), r=rot.tolist(), x=x.tolist(), y=y.tolist())
    return curve, whip, fast


MAX_REFRAME = 1.5  # never zoom footage in more than this to match framing (beyond it the picture goes soft)


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
