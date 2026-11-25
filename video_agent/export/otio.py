"""OpenTimelineIO (.otio, plain JSON): the open interchange format of the Academy Software Foundation.
DaVinci Resolve, Kdenlive, Premiere Pro and many pipeline tools read it; written by hand here (the format is
documented JSON), so no extra package is needed.

A video track of clips (speed as a LinearTimeWarp effect), a track of flash markers and text titles as
markers with their words, and the music on an audio track.
"""

from __future__ import annotations

import json
from typing import Callable

from .timeline import Flat, Media
from .xmeml import pathurl


def _rt(value: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(value)}


def _range(start: float, dur: float, rate: float) -> dict:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _rt(start, rate), "duration": _rt(dur, rate)}


def _ref(m: Media, locate: Callable[[Media], str]) -> dict:
    return {"OTIO_SCHEMA": "ExternalReference.1", "name": m.name, "target_url": pathurl(locate(m)).replace("file://localhost", "file://"),
            "available_range": _range(0, round(m.duration * m.fps), m.fps), "metadata": {}}


def _marker(name: str, frame: int, frames: int, fps: int, color: str, note: str = "") -> dict:
    return {"OTIO_SCHEMA": "Marker.2", "name": name, "color": color, "marked_range": _range(frame, frames, fps),
            "comment": note, "metadata": {}}


def write(flat: Flat, locate: Callable[[Media], str] = lambda m: m.path) -> str:
    fps = flat.fps
    clips = []
    for p in flat.pieces:
        m = p.media
        src_fps = m.fps
        clip = {"OTIO_SCHEMA": "Clip.2", "name": m.name, "metadata": {"video_agent": {"shot": p.shot, "center": list(p.center)}},
                "source_range": _range(round(p.src_in * src_fps), max(1, round(p.frames / fps * src_fps)), src_fps),
                "effects": [], "markers": [], "enabled": True,
                "media_references": {"DEFAULT_MEDIA": _ref(m, locate)}, "active_media_reference_key": "DEFAULT_MEDIA"}
        if abs(p.speed - 1.0) > 1e-3:
            clip["effects"].append({"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "speed", "effect_name": "LinearTimeWarp",
                                    "time_scalar": p.speed, "metadata": {}})
        clips.append(clip)

    markers = [_marker("FLASH" if kind == "flash" else kind.upper(), at, frames, fps, "WHITE" if kind == "flash" else "CYAN")
               for at, kind, frames in flat.transitions]
    markers += [_marker(f"TEXT: {t.text}", round(t.start * fps), max(1, round((t.end - t.start) * fps)), fps, "YELLOW",
                        f"{t.position}, {t.size}px, {t.color}, {t.style}") for t in flat.texts]
    markers += [_marker(mv.kind.replace("_", " ").upper(), mv.at, mv.frames, fps, "ORANGE") for mv in flat.moves]
    video = {"OTIO_SCHEMA": "Track.1", "name": "Video 1", "kind": "Video", "children": clips, "effects": [],
             "markers": markers, "enabled": True, "metadata": {}, "source_range": None}
    tracks = [video]
    if flat.music:
        m = flat.music
        tracks.append({"OTIO_SCHEMA": "Track.1", "name": "Music", "kind": "Audio", "effects": [], "markers": [],
                       "enabled": True, "metadata": {}, "source_range": None,
                       "children": [{"OTIO_SCHEMA": "Clip.2", "name": m.name, "metadata": {}, "effects": [], "markers": [],
                                     "enabled": True, "source_range": _range(0, flat.frames, fps),
                                     "media_references": {"DEFAULT_MEDIA": _ref(m, locate)},
                                     "active_media_reference_key": "DEFAULT_MEDIA"}]})
    timeline = {"OTIO_SCHEMA": "Timeline.1", "name": flat.name, "global_start_time": _rt(0, fps),
                "metadata": {"video_agent": {"width": flat.width, "height": flat.height, "fps": fps}},
                "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "effects": [], "markers": [],
                           "enabled": True, "metadata": {}, "source_range": None}}
    return json.dumps(timeline, indent=1)
