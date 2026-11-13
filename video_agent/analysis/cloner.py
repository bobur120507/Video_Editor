"""/clone: deconstruct a reference video into an Edit Blueprint."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np

from ..blueprint import AudioInfo, Blueprint, ColorProfile, HookInfo, OutputSpec, Segment, SourceInfo, SpeedPoint
from ..config import get_settings
from ..media import color_stats, constant_fps_copy, extract_audio, ffmpeg, iter_frames, probe, sha256_file, thumb
from .audio import analyze_audio
from .camera import estimate_curves_and_blur
from .visual import detect_cuts, find_flashes, flash_aware_cuts, flash_length, segment_features, track_frames

log = logging.getLogger(__name__)

MIN_SHOT = 0.15


def clone_video(path: str | Path, name: str = "", origin: str = "") -> Blueprint:
    settings = get_settings()
    path = constant_fps_copy(path, settings.dir("references"))
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
    track = track_frames(path, info.fps)
    flashes = find_flashes(track)
    cuts = flash_aware_cuts(path, detect_cuts(path, info.fps), flashes, info.fps)

    bounds = _shot_bounds(cuts, info.duration)
    motion_ref = float(np.percentile(track.flow, 90)) if track.flow.size else 1.0
    curves, shutter = estimate_curves_and_blur(path, bounds, info.fps)

    feats = [segment_features(track, start, end, motion_ref) for start, end in bounds]
    held = float(np.median([f["dup_ratio"] for f in feats])) if feats else 0.0
    if held > 0.2:
        notes.append(f"Footage repeats frames throughout ({held:.0%}, e.g. animation on twos); slow-mo judged against that.")

    segments: list[Segment] = []
    for (start, end), curve, feat in zip(bounds, curves, feats):
        slowmo = feat["moving"] and feat["dup_ratio"] > max(0.3, held + 0.25)
        # Zoom/shake are carried by the camera curve itself; these flags are only a fallback.
        effects = [] if len(curve) > 1 else [e for e, on in (("zoom_punch", feat["zoom_in"]), ("shake", feat["shake"])) if on]
        if any(start + 0.15 < f < end - 0.05 for f in flashes):
            effects.append("flash")
        at_start = [f for f in flashes if abs(f - start) <= 0.15]
        transition = "flash" if at_start else "cut"
        speed = [SpeedPoint(at=0.0, speed=0.5)] if slowmo else [SpeedPoint(at=0.0, speed=1.0)]
        segments.append(
            Segment(
                start=round(start, 3),
                end=round(end, 3),
                speed=speed,
                transition_in=transition,
                effects=effects,
                motion=round(feat["motion"], 3),
                camera=curve if len(curve) > 1 else None,
                flash_frames=flash_length(track, at_start[0], info.fps) if at_start else 0,
            )
        )

    frames = list(iter_frames(path, sample_fps=4, width=320))
    color = ColorProfile(**color_stats([f for _, f in frames]))
    for seg in segments:
        shot = [(t, f) for t, f in frames if seg.start <= t < seg.end]
        if shot:
            seg.color = ColorProfile(**color_stats([f for _, f in shot]))
            t_mid, f_mid = shot[len(shot) // 2]
            seg.thumb = [round(float(v), 4) for v in thumb(f_mid)]
            seg.thumb_t = round(t_mid - seg.start, 4)
            pick = np.unique(np.linspace(0, len(shot) - 1, min(8, len(shot))).round().astype(int))
            seg.thumbs = [[round(float(v), 3) for v in thumb(shot[i][1])] for i in pick]
            seg.thumbs_t = [round(shot[i][0] - seg.start, 4) for i in pick]

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
            path=str(path.resolve()),
        ),
        audio=audio,
        segments=segments,
        color=color,
        hook=hook,
        output=output_for(*info.display_size),
        tier=tier,
        motion_blur=shutter,
        notes=notes,
    )


def output_for(width: int, height: int) -> OutputSpec:
    """Same shape as the reference: 9:16 Reel, 4:5 or 1:1 feed post, or 16:9."""
    ar = width / height
    if ar > 1.3:
        return OutputSpec(width=1920, height=1080)
    if ar > 0.9:
        return OutputSpec(width=1080, height=1080)
    if ar > 0.7:
        return OutputSpec(width=1080, height=1350)
    return OutputSpec()


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
