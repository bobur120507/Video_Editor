"""AI Director: audits footage, assigns clip windows to blueprint shots, and finds the subject to crop around."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .blueprint import Blueprint, Segment
from .media import Probe, color_stats, iter_frames, probe

log = logging.getLogger(__name__)

_FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


@dataclass
class ClipInfo:
    probe: Probe
    times: np.ndarray
    motion: np.ndarray  # normalized 0..1 motion per sampled frame

    @property
    def path(self) -> Path:
        return self.probe.path

    @property
    def duration(self) -> float:
        return self.probe.duration

    def motion_between(self, start: float, end: float) -> float:
        m = (self.times >= start) & (self.times < end)
        return float(self.motion[m].mean()) if m.any() else 0.0


@dataclass
class Shot:
    segment: Segment
    clip: ClipInfo
    src_start: float
    src_len: float  # seconds of source used
    stretch: float = 1.0  # >1 when the clip was too short and must be slowed further
    center: tuple[float, float] = (0.5, 0.5)  # subject position in the frame, 0..1
    stats: dict = field(default_factory=dict)  # colour statistics of the chosen window


@dataclass
class EditPlan:
    shots: list[Shot]
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # shot list for footage the user should film


def audit_clip(path: str | Path) -> ClipInfo:
    info = probe(path)
    times, energy = [], []
    prev = None
    for t, frame in iter_frames(path, sample_fps=5, width=160):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        energy.append(0.0 if prev is None else float(cv2.absdiff(prev, gray).mean()))
        times.append(t)
        prev = gray
    e = np.array(energy)
    norm = e / (np.percentile(e, 90) + 1e-6) if e.size else e
    return ClipInfo(probe=info, times=np.array(times), motion=np.clip(norm, 0, 1))


def plan_edit(blueprint: Blueprint, clip_paths: list[str | Path]) -> EditPlan:
    clips = [audit_clip(p) for p in clip_paths]
    clips = [c for c in clips if c.duration >= 0.3]
    if not clips:
        raise ValueError("No usable video clips were provided.")

    warnings: list[str] = []
    for c in clips:
        w, h = c.probe.display_size
        if min(w, h) < 720:
            warnings.append(f"{c.path.name}: low resolution {w}x{h}; output will be upscaled.")
        if c.probe.fps < 24:
            warnings.append(f"{c.path.name}: low frame rate ({c.probe.fps:.0f} fps).")

    needed = sum(s.source_seconds_needed() for s in blueprint.segments)
    have = sum(c.duration for c in clips)
    if have < needed:
        warnings.append(f"Footage is {have:.1f}s but the template needs {needed:.1f}s; some footage will be reused.")

    cursor = {id(c): 0.0 for c in clips}
    shots: list[Shot] = []
    prev_clip = None
    for seg in blueprint.segments:
        need = seg.source_seconds_needed()
        best = None
        for c in clips:
            start, reuse = cursor[id(c)], False
            if start + need > c.duration:
                start, reuse = _best_window(c, need, seg.motion), True
            length = min(need, c.duration - start)
            score = -abs(c.motion_between(start, start + length) - seg.motion)
            score -= 0.5 if reuse else 0.0
            score -= 0.3 if c is prev_clip and len(clips) > 1 else 0.0
            score -= 0.4 * max(0.0, 1 - length / need)
            if best is None or score > best[0]:
                best = (score, c, start, length)
        _, clip, start, length = best
        cursor[id(clip)] = max(cursor[id(clip)], start + length)
        prev_clip = clip
        shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length))

    for shot in shots:
        shot.center, shot.stats = _subject_and_color(shot)

    missing = _missing_shots(blueprint, have, needed)
    return EditPlan(shots=shots, warnings=warnings, missing=missing)


def _best_window(clip: ClipInfo, need: float, target_motion: float) -> float:
    if need >= clip.duration:
        return 0.0
    starts = np.arange(0.0, clip.duration - need, 0.25)
    if starts.size == 0:
        return 0.0
    scores = [-abs(clip.motion_between(s, s + need) - target_motion) for s in starts]
    return float(starts[int(np.argmax(scores))])


def _subject_and_color(shot: Shot) -> tuple[tuple[float, float], dict]:
    """Median face position over a few frames of the window (identity preservation), plus colour stats."""
    end = shot.src_start + shot.src_len
    frames = [f for _, f in iter_frames(shot.clip.path, sample_fps=4, width=480, start=shot.src_start, end=end)][:8]
    centers = []
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = _FACE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            centers.append(((x + w / 2) / f.shape[1], (y + h / 2) / f.shape[0]))
    center = tuple(np.median(np.array(centers), axis=0).tolist()) if centers else (0.5, 0.5)
    return center, color_stats(frames)


def _missing_shots(blueprint: Blueprint, have: float, needed: float) -> list[str]:
    if have >= needed * 0.6:
        return []
    out = []
    for i, seg in enumerate(blueprint.segments, 1):
        kind = "fast-moving action" if seg.motion > 0.6 else "steady close-up" if seg.motion < 0.25 else "medium shot with movement"
        extra = " (filmed at 60fps for slow-mo)" if seg.is_slowmo else ""
        out.append(f"Shot {i}: {seg.duration:.1f}s {kind}{extra}")
    return out
