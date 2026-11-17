"""Visual analysis: shot cuts, motion energy, flashes, zoom punches, camera shake, slow-motion hints."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scenedetect import ContentDetector, SceneManager, StatsManager, open_video

from ..media import frame_at, iter_frames, thumb

log = logging.getLogger(__name__)


@dataclass
class FrameTrack:
    """Per-frame measurements over the whole video (all source frames at low resolution)."""

    times: np.ndarray
    luma: np.ndarray  # mean brightness 0..1
    flow: np.ndarray  # mean optical-flow magnitude (px/frame at analysis width)
    radial: np.ndarray  # mean outward flow component; >0 means zooming in
    shift: np.ndarray  # (N, 2) global translation per frame
    diff: np.ndarray  # mean abs pixel difference to previous frame 0..1

    def window(self, start: float, end: float) -> np.ndarray:
        return (self.times >= start) & (self.times < end)


CUT_THRESHOLD = 27.0  # PySceneDetect content score
# Steady fast motion can hover just over the threshold (McLaren velocity edits: 27-34 for several
# frames). A real cut jumps above its neighbours or changes the picture; a detection that does
# neither is motion. Real cuts rose 1.9x+ or changed the picture (TikTok, reel_1, reel_2; 2026-11-17).
CUT_RISE = 1.5  # score / median of the 3 frames each side
CONTINUOUS = 0.9  # thumbnail similarity of the frames either side: the same picture, still moving
# While fast motion keeps the score above the threshold, PySceneDetect registers no new cut, even a
# clear one (McLaren v11: 68.5 and 64.3 among frames at 30-35, cuts at 1.87 s and 2.17 s missed).
# Peaks this far above their neighbours are added back.
MISSED_RISE = 1.8


def detect_cuts(path: str | Path, fps: float) -> list[float]:
    """Shot-change timestamps (seconds), excluding 0."""
    min_len = max(3, int(fps * 0.12))
    video = open_video(str(path))
    stats = StatsManager()
    manager = SceneManager(stats)
    manager.add_detector(ContentDetector(threshold=CUT_THRESHOLD, min_scene_len=min_len))
    manager.detect_scenes(video)
    score = np.array([float(stats.get_metrics(k, ["content_val"])[0] or 0.0) for k in range(video.frame_number)])

    def rise(i: int) -> float:
        around = np.concatenate([score[max(1, i - 3):i], score[i + 1:i + 4]])
        return float(score[i] / max(np.median(around), 1e-6)) if len(around) else math.inf

    cuts = []
    for scene in manager.get_scene_list()[1:]:
        i = scene[0].frame_num
        if _same_picture(path, i / fps, fps):
            continue
        if rise(i) < CUT_RISE and _continuous(path, i, fps):
            # Motion, not a cut, unless the real jump sits next to it (the detector fired on the
            # motion just before a cut, or a frame late): then the cut is there.
            near = [j for j in range(max(1, i - 1), min(len(score), i + min_len + 1))
                    if j != i and score[j] >= CUT_THRESHOLD and rise(j) >= CUT_RISE]
            if not near:
                continue
            i = max(near, key=lambda j: score[j])
        cuts.append(round(i / fps, 3))
    taken = [round(c * fps) for c in cuts]
    for i in range(min_len + 1, len(score) - 1):  # like the library: no cut in the opening frames (fade-ins)
        if score[i] < CUT_THRESHOLD or score[i] < score[i - 1] or score[i] < score[i + 1] or rise(i) < MISSED_RISE:
            continue
        if any(abs(i - j) < min_len for j in taken) or _same_picture(path, i / fps, fps) or _continuous(path, i, fps):
            continue
        cuts.append(round(i / fps, 3))
        taken.append(i)
    return sorted(set(cuts))


def flash_aware_cuts(path: str | Path, cuts: list[float], flashes: list[float], fps: float) -> list[float]:
    """A white flash hides the cut under it and its fade-out looks like a second cut: fix both."""
    out = sorted(cuts)
    for f in flashes:
        near = [c for c in out if abs(c - f) <= 0.15]
        if near:
            # keep the cut at the flash, drop "cuts" detected while the flash fades
            out = [c for c in out if not (f + 0.05 < c <= f + 0.25)]
        elif not _same_picture(path, f + 1 / fps, fps, before_frames=3, after_frames=8):
            out.append(round(f, 3))
    return sorted(set(out))


def flash_length(track: FrameTrack, flash: float, fps: float) -> int:
    """Frames until a flash has faded back to the shot's normal brightness."""
    m = track.window(flash, flash + 0.6)
    lum = track.luma[m]
    if len(lum) < 3:
        return 4
    base = float(np.median(lum[-max(3, len(lum) // 3) :]))
    peak = float(lum[0])
    for i, v in enumerate(lum):
        if v - base <= 0.1 * max(peak - base, 1e-3):
            return int(min(max(i, 2), 0.5 * fps))
    return int(0.5 * fps)


def _continuous(path: str | Path, i: int, fps: float) -> bool:
    """True when frame i shows the same picture as the frame before it (moving, not cut)."""
    before, at = frame_at(path, max(0, i - 1) / fps, width=240), frame_at(path, i / fps, width=240)
    return before is not None and at is not None and float(thumb(before) @ thumb(at)) > CONTINUOUS


def _same_picture(path: str | Path, cut: float, fps: float, before_frames: int = 2, after_frames: int = 4) -> bool:
    """True when the frames around a detected cut show the same content (fast shake, whip, flash)."""
    before = frame_at(path, max(0.0, cut - before_frames / fps), width=240)
    after = frame_at(path, cut + after_frames / fps, width=240)
    if before is None or after is None:
        return False
    return float(thumb(before) @ thumb(after)) > 0.85


def track_frames(path: str | Path, fps: float, width: int = 160) -> FrameTrack:
    times, luma, flow, radial, shift, diff = [], [], [], [], [], []
    prev = None
    grid = None
    for t, frame in iter_frames(path, sample_fps=fps, width=width):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        times.append(t)
        luma.append(gray.mean() / 255)
        if prev is None:
            flow.append(0.0)
            radial.append(0.0)
            shift.append((0.0, 0.0))
            diff.append(0.0)
        else:
            f = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 2, 9, 2, 5, 1.1, 0)
            if grid is None:
                h, w = gray.shape
                ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
                dx, dy = xs - w / 2, ys - h / 2
                r = np.sqrt(dx**2 + dy**2) + 1e-3
                grid = (dx / r, dy / r)
            mag = np.sqrt(f[..., 0] ** 2 + f[..., 1] ** 2)
            flow.append(float(mag.mean()))
            radial.append(float((f[..., 0] * grid[0] + f[..., 1] * grid[1]).mean()))
            shift.append((float(f[..., 0].mean()), float(f[..., 1].mean())))
            diff.append(float(cv2.absdiff(prev, gray).mean() / 255))
        prev = gray
    return FrameTrack(
        times=np.array(times),
        luma=np.array(luma),
        flow=np.array(flow),
        radial=np.array(radial),
        shift=np.array(shift).reshape(-1, 2),
        diff=np.array(diff),
    )


def find_flashes(track: FrameTrack, jump: float = 0.18) -> list[float]:
    """Frames whose brightness spikes well above both neighbours' baseline (white flashes)."""
    out: list[float] = []
    lum = track.luma
    for i in range(1, len(lum) - 1):
        base = min(lum[max(0, i - 3) : i].min(), lum[i + 1 : i + 4].min())
        if lum[i] - base >= jump and lum[i] > 0.6:
            t = float(track.times[i])
            if not out or t - out[-1] > 0.2:
                out.append(round(t, 3))
    return out


def segment_features(track: FrameTrack, start: float, end: float, motion_ref: float) -> dict:
    m = track.window(start, end)
    if m.sum() < 2:
        return {"motion": 0.0, "zoom_in": False, "shake": False, "dup_ratio": 0.0, "moving": False}
    flow = track.flow[m]
    radial = track.radial[m]
    shift = track.shift[m]
    diff = track.diff[m]

    head = track.window(start, min(end, start + 0.35))
    head_radial = track.radial[head].mean() if head.sum() else 0.0
    zoom_in = bool(head_radial > 0.6 and head_radial > 0.5 * (track.flow[head].mean() if head.sum() else 1))

    jitter = np.abs(np.diff(shift, axis=0)).mean() if len(shift) > 2 else 0.0
    shake = bool(jitter > 1.2 and jitter > 0.6 * flow.mean())

    # Cheap slow-mo shows up as many near-duplicate frames inside a moving shot. The caller compares
    # this against the whole video's rate, because animation "on twos" repeats frames everywhere.
    dup_ratio = float((diff < 0.002).mean())

    return {
        "motion": float(min(1.0, flow.mean() / (motion_ref + 1e-6))),
        "zoom_in": zoom_in,
        "shake": shake,
        "dup_ratio": dup_ratio,
        "moving": bool(flow.mean() > 0.2),
    }
