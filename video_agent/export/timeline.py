"""The edit flattened into what every editor understands: clips on a track, each at one constant speed.

A shot with a speed ramp becomes one piece per speed (consecutive clips of the same footage), exactly as the
renderer cuts it, so CapCut, Premiere, Resolve, Final Cut and After Effects all play the same frames at the same
moments. Frame counts are cumulative from the start, like the renderer's, so no piece drifts off the beat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..blueprint import EditEvent, TextOverlay
from ..media import audio_duration
from ..project import EditProject, to_plan
from ..render.engine import _plan_pieces


@dataclass
class Media:
    path: str
    name: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool

    @property
    def frames(self) -> int:
        return max(1, round(self.duration * self.fps))


@dataclass
class Piece:
    shot: int  # index of the shot it belongs to
    media: Media
    start: int  # timeline frame where it begins
    frames: int  # timeline frames it lasts
    src_in: float  # seconds into the clip
    src_dur: float  # seconds of the clip it plays
    speed: float  # src_dur / (frames / fps)
    center: tuple[float, float]  # what the 9:16 crop centres on, 0..1 of the clip frame


@dataclass
class Move:
    shot: int
    kind: str  # punch, shake, push, whip_in, whip_out
    at: int  # timeline frame
    frames: int
    strength: float
    dx: float = 0.0
    dy: float = 0.0
    rot: float = 0.0


@dataclass
class Flat:
    name: str
    fps: int
    width: int
    height: int
    frames: int
    pieces: list[Piece]
    shots: list[tuple[int, int]]  # (start frame, frames) per shot
    transitions: list[tuple[int, str, int]]  # (frame of the cut, kind, frames) for every cut that isn't plain
    moves: list[Move]
    texts: list[TextOverlay]
    music: Media | None
    media: list[Media] = field(default_factory=list)  # every clip used, in order of first use

    @property
    def seconds(self) -> float:
        return self.frames / self.fps


def _media(pr, path: str) -> Media:
    w, h = pr.display_size
    return Media(path=str(Path(path).resolve()), name=Path(path).name, duration=pr.duration, width=w, height=h,
                 fps=pr.fps or 30.0, has_audio=pr.has_audio)


def flatten(p: EditProject) -> Flat:
    plan = to_plan(p)
    bp = p.blueprint
    fps = p.fps
    pieces, shots, moves, transitions, media = [], [], [], [], {}
    cursor = 0
    for i, shot in enumerate(plan.shots):
        seg = shot.segment
        end = round(seg.end * fps)
        n = max(1, end - cursor)
        m = media.get(str(shot.clip.path)) or media.setdefault(str(shot.clip.path), _media(shot.clip.probe, str(shot.clip.path)))
        local = 0
        for offset, src_len, speed, n_out in _plan_pieces(seg, shot.stretch, fps, n):
            pieces.append(Piece(shot=i, media=m, start=cursor + local, frames=n_out, src_in=round(shot.src_start + offset, 4),
                                src_dur=round(src_len, 4), speed=round(speed, 4), center=tuple(shot.center)))
            local += n_out
        shots.append((cursor, n))
        if i and seg.transition_in != "cut":
            frames = seg.flash_frames if seg.transition_in == "flash" else seg.transition_frames
            transitions.append((cursor, seg.transition_in, frames or 4))
        for e in seg.events:
            moves.append(_move(i, e, cursor, n, fps))
        cursor = end
    music_path = p.music_path()
    music = None
    if music_path and Path(music_path).is_file():
        music = Media(path=str(Path(music_path).resolve()), name=Path(music_path).name, duration=audio_duration(music_path),
                      width=0, height=0, fps=fps, has_audio=True)
    return Flat(name=p.name or bp.name or "edit", fps=fps, width=bp.output.width, height=bp.output.height, frames=cursor,
                pieces=pieces, shots=shots, transitions=transitions, moves=moves, texts=list(bp.texts), music=music,
                media=list(media.values()))


def _move(i: int, e: EditEvent, start: int, n: int, fps: int) -> Move:
    at = start + min(n - 1, max(0, round(e.t * fps)))
    frames = max(1, round(e.duration * fps)) if e.duration else {"punch": 8, "shake": 8, "whip_in": 4, "whip_out": 4, "push": n}[e.kind]
    if e.kind == "whip_out":
        at = max(start, start + n - frames)
    return Move(shot=i, kind=e.kind, at=at, frames=min(frames, start + n - at), strength=e.strength, dx=e.dx, dy=e.dy, rot=e.rot)


def cover_scale(m: Media, width: int, height: int) -> float:
    """Scale (1 = 100 %) that fills the output frame with the clip, as the renderer's crop does."""
    return max(width / m.width, height / m.height)


def cover_offset(m: Media, width: int, height: int, center: tuple[float, float]) -> tuple[float, float]:
    """Pixels the scaled clip moves from the frame centre so `center` is centred, without showing an edge."""
    s = cover_scale(m, width, height)
    w, h = m.width * s, m.height * s
    dx = (0.5 - center[0]) * w
    dy = (0.5 - center[1]) * h
    max_x, max_y = (w - width) / 2, (h - height) / 2
    return min(max(dx, -max_x), max_x), min(max(dy, -max_y), max_y)


def punch_scale(strength: float) -> float:
    """Peak zoom of a punch (strength is a log-zoom)."""
    return math.exp(strength or 0.15)


def timecode(frame: int, fps: int) -> str:
    s, f = divmod(frame, fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
