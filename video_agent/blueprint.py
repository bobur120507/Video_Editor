"""Edit Blueprint: the reusable "editing DNA" extracted from a reference video.

All times are in seconds on the output timeline (which matches the reference video's timeline,
so cuts stay locked to the music beats).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

BLUEPRINT_VERSION = 1

Effect = Literal["zoom_punch", "shake", "flash"]
Transition = Literal["cut", "flash", "zoom"]


class SourceInfo(BaseModel):
    duration: float
    fps: float
    width: int
    height: int
    sha256: str = ""
    origin: str = ""  # file name or URL the blueprint was cloned from
    path: str = ""  # local copy of the reference, used for match scoring


class AudioInfo(BaseModel):
    bpm: float = 0.0
    beats: list[float] = Field(default_factory=list)
    drops: list[float] = Field(default_factory=list)  # big energy jumps (beat drops)
    hits: list[float] = Field(default_factory=list)  # strong onsets (kicks, snares, 808s) moves are synced to
    track_path: str | None = None  # extracted music track, reused at apply time
    has_music: bool = False


class SpeedPoint(BaseModel):
    """Speed for a slice of a segment: from `at` (0..1 fraction of the segment) until the next point."""

    at: float = Field(ge=0.0, le=1.0)
    speed: float = Field(gt=0.0)


class CameraCurve(BaseModel):
    """Per-frame transform of a shot relative to its sharpest frame (centre-based).

    s = scale (>1 means content grows: zoom in), r = rotation in radians,
    x/y = translation as a fraction of frame width/height. One entry per reference frame.
    """

    fps: float = 30.0
    s: list[float] = Field(default_factory=list)
    r: list[float] = Field(default_factory=list)
    x: list[float] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.s)

    def at(self, t: float) -> tuple[float, float, float, float]:
        if not self.s:
            return 1.0, 0.0, 0.0, 0.0
        f = min(max(t * self.fps, 0.0), len(self.s) - 1)
        i = int(f)
        j = min(i + 1, len(self.s) - 1)
        k = f - i
        lerp = lambda a: a[i] + (a[j] - a[i]) * k  # noqa: E731
        return lerp(self.s), lerp(self.r), lerp(self.x), lerp(self.y)

    def describe(self) -> dict:
        if len(self) < 2:
            return {"zoom": 1.0, "rotation_deg": 0.0, "pan": 0.0, "shake": 0.0}
        x, y = np.array(self.x), np.array(self.y)
        jitter = float(np.abs(np.diff(x, 2)).mean() + np.abs(np.diff(y, 2)).mean()) if len(self) > 3 else 0.0
        return {
            "zoom": round(max(self.s) / max(min(self.s), 1e-3), 3),
            "rotation_deg": round((max(self.r) - min(self.r)) * 57.2958, 2),
            "pan": round(float(max(x.max() - x.min(), y.max() - y.min())), 3),
            "shake": round(jitter, 4),
        }


class Subject(BaseModel):
    """Main subject of a shot (normalized frame coordinates)."""

    cls: str
    cx: float
    cy: float
    w: float
    h: float


class EditEvent(BaseModel):
    """An editor's move inside a shot, re-created on new footage (see analysis/events.py)."""

    kind: Literal["whip_in", "whip_out", "punch", "shake", "push"]
    t: float  # seconds from the segment start
    duration: float = 0.0
    strength: float = 0.0  # punch: log-zoom; push: log-zoom per second; others: 0..1
    dx: float = 0.0  # whip direction (unit vector, frame coordinates)
    dy: float = 0.0
    zoom: float = 0.0  # +1 zoom in, -1 zoom out
    rot: float = 0.0  # shake rotation amplitude, degrees


class Segment(BaseModel):
    start: float
    end: float
    speed: list[SpeedPoint] = Field(default_factory=lambda: [SpeedPoint(at=0.0, speed=1.0)])
    transition_in: Transition = "cut"
    effects: list[Effect] = Field(default_factory=list)
    motion: float = 0.0  # normalized motion energy of the reference shot (0..1)
    camera: CameraCurve | None = None  # replayed on the new footage when present
    color: "ColorProfile | None" = None  # this shot's own grade (falls back to the blueprint's)
    thumb: list[float] = Field(default_factory=list)  # 16x16 normalized grey of one frame (content matching)
    thumb_t: float = 0.0  # when that frame is, in seconds from the segment start
    thumbs: list[list[float]] = Field(default_factory=list)  # several frames across the shot, for sequence alignment
    thumbs_t: list[float] = Field(default_factory=list)  # their times from the segment start
    flash_frames: int = 0
    events: list[EditEvent] = Field(default_factory=list)
    subject: Subject | None = None  # where the car/person is and how big, in the reference shot
    clip: list[float] = Field(default_factory=list)  # CLIP embedding: what kind of shot this is  # length of the white flash-in when transition_in == "flash"

    @property
    def duration(self) -> float:
        return self.end - self.start

    def source_offset(self, out_t: float) -> float:
        """Seconds of source footage consumed by output time `out_t` within this segment."""
        pts = sorted(self.speed, key=lambda p: p.at)
        total, frac = 0.0, out_t / self.duration if self.duration else 0.0
        for i, p in enumerate(pts):
            nxt = pts[i + 1].at if i + 1 < len(pts) else 1.0
            span = max(0.0, min(frac, nxt) - p.at)
            total += span * self.duration * p.speed
        return total

    def source_seconds_needed(self) -> float:
        """How many seconds of raw footage this segment consumes after speed changes."""
        pts = sorted(self.speed, key=lambda p: p.at)
        total = 0.0
        for i, p in enumerate(pts):
            nxt = pts[i + 1].at if i + 1 < len(pts) else 1.0
            total += (nxt - p.at) * self.duration * p.speed
        return total

    @property
    def is_slowmo(self) -> bool:
        return any(p.speed < 0.95 for p in self.speed)


class ColorProfile(BaseModel):
    """Target colour statistics; each clip is graded to match these at render time."""

    luma_mean: float = 0.45  # 0..1
    luma_std: float = 0.22
    saturation: float = 0.35  # 0..1 mean HSV saturation
    rgb_mean: tuple[float, float, float] = (0.45, 0.45, 0.45)
    lab_mean: tuple[float, float, float] | None = None  # CIE Lab (L 0..100), used for look transfer
    lab_std: tuple[float, float, float] | None = None
    lut_path: str | None = None  # optional .cube LUT applied after matching


class TextOverlay(BaseModel):
    text: str
    start: float
    end: float
    position: Literal["top", "center", "bottom"] = "center"
    size: int = 72
    color: str = "#FFFFFF"
    style: Literal["pop", "fade", "static"] = "pop"


Segment.model_rebuild()


class HookInfo(BaseModel):
    score: float = 0.0  # 0..100 "hook velocity"
    cuts_in_first_3s: int = 0
    motion_first_3s: float = 0.0
    flashes_in_first_3s: int = 0


class OutputSpec(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_bitrate: str = "16M"
    max_bitrate: str = "20M"
    audio_bitrate: str = "128k"
    audio_rate: int = 48000


class Blueprint(BaseModel):
    version: int = BLUEPRINT_VERSION
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    source: SourceInfo
    audio: AudioInfo = Field(default_factory=AudioInfo)
    segments: list[Segment]
    color: ColorProfile = Field(default_factory=ColorProfile)
    texts: list[TextOverlay] = Field(default_factory=list)
    hook: HookInfo = Field(default_factory=HookInfo)
    output: OutputSpec = Field(default_factory=OutputSpec)
    tier: Literal["standard", "pro"] = "standard"
    motion_blur: float = 0.0  # shutter fraction for camera moves (0.5 = 180 degrees, like After Effects)
    notes: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def summary(self) -> str:
        n = len(self.segments)
        avg = self.duration / n if n else 0
        effects = sorted({e for s in self.segments for e in s.effects})
        slow = sum(1 for s in self.segments if s.is_slowmo)
        moves = [s.camera.describe() for s in self.segments if s.camera]
        ev = [e.kind for s in self.segments for e in s.events]
        on_hits = sum(1 for s in self.segments for e in s.events if e.kind in ("punch", "shake") and any(abs(s.start + e.t - h) < 0.02 for h in self.audio.hits))
        zooms = sum(1 for m in moves if m["zoom"] > 1.03)
        rots = sum(1 for m in moves if m["rotation_deg"] > 1)
        shakes = sum(1 for m in moves if m["shake"] > 0.002)
        lines = [
            f"Duration: {self.duration:.1f}s · {n} shots (avg {avg:.2f}s)",
            f"Music: {self.audio.bpm:.0f} BPM, {len(self.audio.beats)} beats, {len(self.audio.drops)} drops"
            if self.audio.has_music
            else "Music: none detected",
            f"Camera moves: {zooms} zoom, {rots} rotation, {shakes} shake shots"
            + (" · motion blur" if self.motion_blur else ""),
            f"Edit moves: {ev.count('whip_in') + ev.count('whip_out')} whips, {ev.count('punch')} punches, {ev.count('shake')} shakes"
            f" ({on_hits} on music hits), {ev.count('push')} pushes",
            f"Effects: {', '.join(effects) or 'none'} · slow-mo shots: {slow}",
            f"Hook score: {self.hook.score:.0f}/100 ({self.hook.cuts_in_first_3s} cuts in first 3s)",
            f"Render tier: {self.tier}",
        ]
        return "\n".join(lines)
