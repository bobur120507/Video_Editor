"""Visual analysis: shot cuts, motion energy, flashes, zoom punches, camera shake, slow-motion hints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scenedetect import ContentDetector, detect

from ..media import iter_frames

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


def detect_cuts(path: str | Path, fps: float) -> list[float]:
    """Shot-change timestamps (seconds), excluding 0."""
    scenes = detect(str(path), ContentDetector(threshold=27.0, min_scene_len=max(3, int(fps * 0.12))))
    return [round(s[0].seconds, 3) for s in scenes[1:]]


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
        return {"motion": 0.0, "zoom_in": False, "shake": False, "slowmo": False}
    flow = track.flow[m]
    radial = track.radial[m]
    shift = track.shift[m]
    diff = track.diff[m]

    head = track.window(start, min(end, start + 0.35))
    head_radial = track.radial[head].mean() if head.sum() else 0.0
    zoom_in = bool(head_radial > 0.6 and head_radial > 0.5 * (track.flow[head].mean() if head.sum() else 1))

    jitter = np.abs(np.diff(shift, axis=0)).mean() if len(shift) > 2 else 0.0
    shake = bool(jitter > 1.2 and jitter > 0.6 * flow.mean())

    # Cheap slow-mo shows up as many near-duplicate frames inside a moving shot.
    dup_ratio = float((diff < 0.002).mean())
    slowmo = bool(dup_ratio > 0.3 and flow.mean() > 0.2)

    return {
        "motion": float(min(1.0, flow.mean() / (motion_ref + 1e-6))),
        "zoom_in": zoom_in,
        "shake": shake,
        "slowmo": slowmo,
    }
