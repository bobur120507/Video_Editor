"""Edit Blueprint: the reusable "editing DNA" extracted from a reference video.

All times are in seconds on the output timeline (which matches the reference video's timeline,
so cuts stay locked to the music beats).
"""

from __future__ import annotations

from typing import Literal

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


class AudioInfo(BaseModel):
    bpm: float = 0.0
    beats: list[float] = Field(default_factory=list)
    drops: list[float] = Field(default_factory=list)  # big energy jumps (beat drops)
    track_path: str | None = None  # extracted music track, reused at apply time
    has_music: bool = False


class SpeedPoint(BaseModel):
    """Speed for a slice of a segment: from `at` (0..1 fraction of the segment) until the next point."""

    at: float = Field(ge=0.0, le=1.0)
    speed: float = Field(gt=0.0)


class Segment(BaseModel):
    start: float
    end: float
    speed: list[SpeedPoint] = Field(default_factory=lambda: [SpeedPoint(at=0.0, speed=1.0)])
    transition_in: Transition = "cut"
    effects: list[Effect] = Field(default_factory=list)
    motion: float = 0.0  # normalized motion energy of the reference shot (0..1)

    @property
    def duration(self) -> float:
        return self.end - self.start

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
    lut_path: str | None = None  # optional .cube LUT applied after matching


class TextOverlay(BaseModel):
    text: str
    start: float
    end: float
    position: Literal["top", "center", "bottom"] = "center"
    size: int = 72
    color: str = "#FFFFFF"
    style: Literal["pop", "fade", "static"] = "pop"


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
    notes: list[str] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def summary(self) -> str:
        n = len(self.segments)
        avg = self.duration / n if n else 0
        effects = sorted({e for s in self.segments for e in s.effects})
        slow = sum(1 for s in self.segments if s.is_slowmo)
        lines = [
            f"Duration: {self.duration:.1f}s · {n} shots (avg {avg:.2f}s)",
            f"Music: {self.audio.bpm:.0f} BPM, {len(self.audio.beats)} beats, {len(self.audio.drops)} drops"
            if self.audio.has_music
            else "Music: none detected",
            f"Effects: {', '.join(effects) or 'none'} · slow-mo shots: {slow}",
            f"Hook score: {self.hook.score:.0f}/100 ({self.hook.cuts_in_first_3s} cuts in first 3s)",
            f"Render tier: {self.tier}",
        ]
        return "\n".join(lines)
