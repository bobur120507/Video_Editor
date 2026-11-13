"""/clone: deconstruct a reference video into an Edit Blueprint."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np

from ..blueprint import AudioInfo, Blueprint, ColorProfile, HookInfo, Segment, SourceInfo, SpeedPoint
from ..config import get_settings
from ..media import color_stats, extract_audio, ffmpeg, iter_frames, probe, sha256_file
from .audio import analyze_audio
from .visual import detect_cuts, find_flashes, segment_features, track_frames

log = logging.getLogger(__name__)

MIN_SHOT = 0.15


def clone_video(path: str | Path, name: str = "", origin: str = "") -> Blueprint:
    path = Path(path)
    settings = get_settings()
    info = probe(path)
    digest = sha256_file(path)
    notes: list[str] = []

    audio = AudioInfo()
    if info.has_audio:
        with tempfile.TemporaryDirectory() as tmp:
            wav = extract_audio(path, Path(tmp) / "a.wav")
            res = analyze_audio(wav)
        audio = AudioInfo(bpm=res.bpm, beats=res.beats, drops=res.drops, has_music=res.has_music)
        if res.has_music:
            track = settings.dir("tracks") / f"{digest[:16]}.m4a"
            if not track.exists():
                ffmpeg(["-i", path, "-vn", "-ac", 2, "-ar", 48000, "-c:a", "aac", "-b:a", "192k", track])
            audio.track_path = str(track)
    else:
        notes.append("Reference has no audio track; upload music at /apply time.")

    # Reference cuts are kept frame-exact: they are already in sync with the reference music.
    cuts = detect_cuts(path, info.fps)
    track = track_frames(path, info.fps)
    flashes = find_flashes(track)

    bounds = _shot_bounds(cuts, info.duration)
    motion_ref = float(np.percentile(track.flow, 90)) if track.flow.size else 1.0

    segments: list[Segment] = []
    for start, end in bounds:
        feat = segment_features(track, start, end, motion_ref)
        effects = []
        if feat["zoom_in"]:
            effects.append("zoom_punch")
        if feat["shake"]:
            effects.append("shake")
        if any(start + 0.15 < f < end - 0.05 for f in flashes):
            effects.append("flash")
        transition = "flash" if any(abs(f - start) <= 0.15 for f in flashes) else "cut"
        speed = [SpeedPoint(at=0.0, speed=0.5)] if feat["slowmo"] else [SpeedPoint(at=0.0, speed=1.0)]
        segments.append(
            Segment(
                start=round(start, 3),
                end=round(end, 3),
                speed=speed,
                transition_in=transition,
                effects=effects,
                motion=round(feat["motion"], 3),
            )
        )

    frames = [f for _, f in iter_frames(path, sample_fps=2, width=320)]
    color = ColorProfile(**color_stats(frames))

    hook = _hook(segments, track, flashes)
    tier = "pro" if any(s.is_slowmo for s in segments) else "standard"
    notes.append("Speed changes and effects are detected heuristically; tweak them after the first render.")

    return Blueprint(
        name=name,
        source=SourceInfo(
            duration=round(info.duration, 3),
            fps=round(info.fps, 3),
            width=info.display_size[0],
            height=info.display_size[1],
            sha256=digest,
            origin=origin or path.name,
        ),
        audio=audio,
        segments=segments,
        color=color,
        hook=hook,
        tier=tier,
        notes=notes,
    )


def _shot_bounds(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    points = [0.0]
    for c in sorted(cuts):
        if MIN_SHOT <= c - points[-1] and duration - c >= MIN_SHOT:
            points.append(c)
    points.append(duration)
    return list(zip(points[:-1], points[1:]))


def _hook(segments: list[Segment], track, flashes: list[float]) -> HookInfo:
    cuts = sum(1 for s in segments if 0 < s.start < 3.0)
    m = track.window(0, 3.0)
    motion = float(track.flow[m].mean()) if m.sum() else 0.0
    n_flash = sum(1 for f in flashes if f < 3.0)
    score = 45 * min(cuts / 4, 1) + 40 * min(motion / 3.0, 1) + 15 * min(n_flash / 2, 1)
    return HookInfo(
        score=round(score, 1),
        cuts_in_first_3s=cuts,
        motion_first_3s=round(motion, 3),
        flashes_in_first_3s=n_flash,
    )
