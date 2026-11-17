"""Velocity edits: speed ramps inside a shot, real smear, and how much the edit should move.

A velocity editor remaps time: footage runs fast between music hits and slows down on them. Here
that is one smooth time map per shot (output frame i shows source time p[i]; speed is its slope),
shaped like the reference's motion wave (Segment.velocity), so speed eases instead of stepping.

Fast output frames average every source frame they sweep across: real motion blur from the
footage itself (what a camera shutter does), not a smeared copy of one frame. Frames on the hit
run slow and show one frame, so the picture lands sharp.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from ..blueprint import CameraCurve, Segment

MIN_SPEED, MAX_SPEED = 0.3, 3.0  # time-remap range relative to the shot's average speed
FLOOR = 0.3  # the motion wave never drops below this share of its mean: the edit never stops
SMOOTH = 5  # frames: speed and drift ease over this many frames (no jumps)
# Share of a frame's source sweep blended into it (180 degrees). 270 smeared 76% of frames against
# the TikTok's 69% (McLaren test edit, 2026-11-15).
TIME_SHUTTER = 0.5
MAX_SAMPLES = 16
SHARP_SPEED = 1.25  # output frames slower than this land sharp; only their source frames need AI upscaling
SAMPLE_W = 160  # motion is measured at this width (like analysis.visual.track_frames)


def _smooth(v: np.ndarray, k: int = SMOOTH) -> np.ndarray:
    if len(v) < 3 or k < 2:
        return v
    k = min(k, len(v) if len(v) % 2 else len(v) - 1)
    pad = k // 2
    return np.convolve(np.pad(v, pad, mode="edge"), np.ones(k) / k, "valid")


def wave(velocity: list[float], n_frames: int) -> np.ndarray:
    """The reference shot's motion wave resampled to n_frames, mean 1, eased, never below FLOOR."""
    if not velocity:
        return np.ones(n_frames)
    v = np.asarray(velocity, dtype=np.float64)
    v = np.interp(np.linspace(0, len(v) - 1, n_frames), np.arange(len(v)), v) if len(v) != n_frames else v
    v = _smooth(np.maximum(v, 0.0))
    mean = v.mean()
    if mean <= 1e-9:
        return np.ones(n_frames)
    return np.maximum(v / mean, FLOOR)


def speed_curve(velocity: list[float], n_frames: int, mean_speed: float) -> np.ndarray:
    """Per output frame playback speed: follows the motion wave, averages `mean_speed` (so the
    shot uses exactly the footage the Director planned) and stays within MIN_SPEED..MAX_SPEED of it."""
    w = wave(velocity, n_frames)
    for _ in range(4):  # clipping moves the mean; a few rounds settle it
        w = np.clip(w / w.mean(), MIN_SPEED, MAX_SPEED)
    w = _smooth(w, 3)  # the wave is eased already; this only rounds the clipped corners
    return w / w.mean() * mean_speed


def time_map(speed: np.ndarray, fps: float) -> np.ndarray:
    """Source seconds shown by each output frame (starts at 0, never runs backwards).

    Frame i moves speed[i] frames on from frame i-1, so the motion seen arriving at frame i is that
    frame's own speed (motion is measured into a frame, as in track_frames); the wave is not late.
    """
    return np.concatenate([[0.0], np.cumsum(speed[1:])]) / fps


def sharp_frames(p: np.ndarray, speed: np.ndarray, fps: float, src_fps: float, n_src: int) -> np.ndarray:
    """Mask of source frames that some slow (sharp) output frame shows."""
    need = np.zeros(n_src, bool)
    for t, s in zip(p, speed):
        if s <= SHARP_SPEED:
            a = int(math.floor(t * src_fps))
            b = int(math.ceil((t + TIME_SHUTTER * s / fps) * src_fps))
            need[max(0, a):min(n_src, b + 1)] = True
    return need


def sample_points(t: float, s: float, fps: float, src_fps: float, shutter: float = TIME_SHUTTER) -> np.ndarray:
    """Source positions (in source frames) blended into one output frame."""
    sweep = shutter * s / fps * src_fps  # source frames crossed while the shutter is open
    n = int(min(MAX_SAMPLES, max(1, math.ceil(sweep * 2))))  # at most half a frame apart
    start = t * src_fps
    return np.array([start]) if n == 1 else start + np.linspace(0, sweep, n)


def retime(load: Callable[[int], np.ndarray], n_src: int, p: np.ndarray, speed: np.ndarray, fps: float, src_fps: float,
           shutter: float = TIME_SHUTTER):
    """Yield the output frames: each the average of the source frames its shutter sweeps across.

    load(k) returns source frame k (uint8). A slow frame (one sample) shows the nearest source frame:
    blending two neighbours would ghost a double image, exactly where the picture must land sharp.
    (With RIFE the source has in-betweens, so slow frames still move smoothly.)
    """
    for t, s in zip(p, speed):
        pts = np.clip(sample_points(t, s, fps, src_fps, shutter), 0, n_src - 1)
        if len(pts) == 1:
            yield load(int(round(pts[0])))
            continue
        acc = None
        for q in pts:
            a = int(math.floor(q))
            b, frac = min(a + 1, n_src - 1), q - a
            fr = load(a).astype(np.float32) if frac < 1e-3 or b == a else cv2.addWeighted(
                load(a).astype(np.float32), 1 - frac, load(b).astype(np.float32), frac, 0)
            acc = fr if acc is None else acc + fr
        yield np.clip(acc / len(pts) + 0.5, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- how much the edit moves


def _grid(width: int, height: int) -> np.ndarray:
    h = SAMPLE_W * height / width
    xs, ys = np.meshgrid(np.linspace(0.08, 0.92, 7) * SAMPLE_W, np.linspace(0.08, 0.92, 12) * h)
    return np.stack([xs.ravel(), ys.ravel(), np.ones(xs.size)], axis=1)


def camera_flow(curve: CameraCurve, n_frames: int, width: int, height: int) -> np.ndarray:
    """Mean on-screen motion the camera curve adds per frame (px/frame at SAMPLE_W width)."""
    from .motion import _matrix

    h = SAMPLE_W * height / width
    pts = _grid(width, height)
    fps = curve.fps
    prev, out = None, np.zeros(n_frames)
    for i in range(n_frames):
        m = _matrix(*curve.at(i / fps), SAMPLE_W, h)
        q = pts @ m.T
        if prev is not None:
            out[i] = float(np.linalg.norm(q[:, :2] - prev[:, :2], axis=1).mean())
        prev = q
    if n_frames > 1:
        out[0] = out[1]
    return out


def motion_targets(seg: Segment, segments: list[Segment], n_frames: int | None = None, mask: np.ndarray | None = None
                   ) -> tuple[float, float]:
    """(typical motion, fast ÷ typical) the shot should have: half the shot's own (on the frames in
    `mask`), half the whole edit's (short shots are noisy), and the edit's fast ÷ typical ratio."""
    every = np.concatenate([np.asarray(s.velocity) for s in segments if s.velocity]) if any(s.velocity for s in segments) else np.ones(1)
    overall = float(np.median(every))
    own = overall
    if seg.velocity:
        v = np.asarray(seg.velocity, dtype=np.float64)
        if n_frames and mask is not None and mask.any():
            v = np.interp(np.linspace(0, len(v) - 1, n_frames), np.arange(len(v)), v)[mask[:n_frames]]
        own = float(np.median(v))
    ratio = float(np.quantile(every, 0.9) / max(overall, 1e-6))
    return 0.5 * own + 0.5 * overall, max(1.2, ratio)


AMPS = np.linspace(0.0, 4.0, 17)
GAINS = (0.25, 0.4, 0.55, 0.7, 0.85, 1.0)
MIN_CALM = 4  # frames outside whips needed to measure a shot; shorter shots are mostly whip
DEFAULT_AMP = 1.0  # drift for shots too short to measure (hidden by their whips anyway)


def calibrate(build: Callable[[float, float], CameraCurve], footage_flow: np.ndarray, target: float, ratio: float,
              n_frames: int, width: int, height: int, mask: np.ndarray | None = None) -> tuple[float, float, float, float]:
    """Drift amplitude and move gain whose result moves like the reference: (amp, gain, median, ratio).

    The output's motion per frame is the footage's own (already speed-ramped, scaled by the
    camera's zoom) combined with the camera's, as independent directions. Only frames in `mask`
    (default: all) are measured.
    """
    keep = np.ones(n_frames, bool) if mask is None or not mask[:n_frames].any() else np.asarray(mask[:n_frames], bool)
    f = np.asarray(footage_flow, dtype=np.float64)[:n_frames]
    if len(f) < n_frames:
        f = np.pad(f, (0, n_frames - len(f)), mode="edge")
    best = (math.inf, 0.0, 1.0, 0.0, 0.0)
    for gain in GAINS:
        for amp in AMPS:
            curve = build(float(amp), gain)
            c = camera_flow(curve, n_frames, width, height)
            o = np.hypot(f * np.asarray(curve.s[:n_frames]), c)[keep]
            med = float(np.median(o)) + 1e-6
            r = float(np.quantile(o, 0.9)) / med
            err = math.log(med / target) ** 2 + 0.5 * math.log(r / ratio) ** 2
            if err < best[0]:
                best = (err, float(amp), gain, med, r)
    return best[1], best[2], best[3], best[4]


WHIP_GAINS = np.geomspace(0.02, 1.0, 25)  # full travel is ~30x the drift per frame: most of the range is low
WHIP_RATIO = 1.9  # whip ÷ in-shot motion when the reference can't be measured (the TikTok's, 2026-11-15)


def whip_ratio(segments: list[Segment], whip_frames: int) -> float:
    """How much faster the reference moves on its whip frames than inside its shots (whole edit).

    In a velocity edit a whip is the flowing motion speeding up into the cut, not a slide out of
    stillness: the TikTok's whip frames move 1.9x its other frames. Flow saturates on heavy smear,
    so this is a floor, which is the safe side (spikes are what looked wrong).
    """
    whip, calm = [], []
    for s in segments:
        v = np.asarray(s.velocity, dtype=np.float64)
        if len(v) <= 2 * whip_frames:
            continue
        m = np.zeros(len(v), bool)
        if any(e.kind == "whip_in" for e in s.events):
            m[:whip_frames] = True
        if any(e.kind == "whip_out" for e in s.events):
            m[-whip_frames:] = True
        whip += list(v[m])
        calm += list(v[~m])
    if len(whip) < whip_frames or len(calm) < whip_frames:
        return WHIP_RATIO
    return max(1.0, float(np.median(whip) / max(np.median(calm), 1e-6)))


def calibrate_whips(build: Callable[[float], CameraCurve], footage_flow: np.ndarray, target: float, whip: np.ndarray,
                    n_frames: int, width: int, height: int) -> tuple[float, float]:
    """Whip gain whose whip frames move `target` px/frame (typical): (gain, median motion got)."""
    whip = np.asarray(whip[:n_frames], bool)
    if not whip.any():
        return 1.0, 0.0
    f = np.asarray(footage_flow, dtype=np.float64)[:n_frames]
    if len(f) < n_frames:
        f = np.pad(f, (0, n_frames - len(f)), mode="edge")
    best = (math.inf, 1.0, 0.0)
    for g in WHIP_GAINS:
        curve = build(float(g))
        o = np.hypot(f * np.asarray(curve.s[:n_frames]), camera_flow(curve, n_frames, width, height))[whip]
        med = float(np.median(o)) + 1e-6
        err = abs(math.log(med / target))
        if err < best[0]:
            best = (err, float(g), med)
    return best[1], best[2]


def load_frames(paths: list[Path], width: int, height: int, cache: int = 24) -> Callable[[int], np.ndarray]:
    """Frame loader with a small cache; frames are resized to the output size (Lanczos) on load."""
    kept: dict[int, np.ndarray] = {}

    def load(k: int) -> np.ndarray:
        if k not in kept:
            img = cv2.imread(str(paths[k]), cv2.IMREAD_COLOR)
            if img is None:
                raise RuntimeError(f"cannot read {paths[k]}")
            if img.shape[1] != width or img.shape[0] != height:
                img = cv2.resize(img, (width, height), interpolation=cv2.INTER_LANCZOS4)
            if len(kept) >= cache:
                kept.pop(min(kept))
            kept[k] = img
        return kept[k]

    return load
