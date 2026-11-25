"""Editable project: a finished edit as a timeline you can change and render again, or export to another editor.

Every web render saves one (project.json next to the video). It holds the blueprint actually rendered (cut
times, speed, moves, transitions, texts, glows, music) and, per shot, the footage that fills it: which clip,
from where, and how it is framed. The editor changes it with the operations below and renders it again
without re-planning; video_agent/export writes it for CapCut, After Effects, Premiere, Resolve and Final Cut.

Cuts sit on the music, so the edit operations keep the total length: moving a cut gives one shot's time to its
neighbour, splitting makes two shots of one, removing a shot lets the one before take its place, and moving
footage swaps it between two slots (the slots keep their timing and moves). Per-frame data measured on the
reference (camera curves, blur, the motion wave) is re-cut with the shot, so a move stays where it was in time.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from .blueprint import Blueprint, Segment, SpeedPoint, TextOverlay, Transition
from .director import ClipInfo, EditPlan, Shot
from .media import Probe, probe

PROJECT_FILE = "project.json"
HISTORY = "history"
MIN_SHOT = 2  # frames: the shortest shot an edit may leave
EPS = 1e-4


class ShotSource(BaseModel):
    """The footage in one slot of the edit."""

    clip: str  # path of the clip
    src_start: float = 0.0  # seconds into the clip where the shot begins
    center: tuple[float, float] = (0.5, 0.5)  # what the 9:16 crop centres on, 0..1 of the clip frame
    content_match: float = 0.0  # 1 = the reference's own footage (its grade and camera are fitted exactly)
    stats: dict = Field(default_factory=dict)  # colour statistics of the chosen window (the renderer re-measures when empty)


class RenderSettings(BaseModel):
    upscale: bool = True  # Real-ESRGAN on footage smaller than the frame
    blur: str = "auto"  # auto | fast_only | off
    glows: bool = True
    transitions: bool = True


class EditProject(BaseModel):
    format: int = 1
    id: str = ""
    name: str = ""
    blueprint: Blueprint  # what is rendered: segments are the shots, in order
    shots: list[ShotSource]  # one per segment
    clips: list[str] = Field(default_factory=list)  # all footage the user added (the media bin)
    music: str | None = None  # None: the template's own track
    settings: RenderSettings = Field(default_factory=RenderSettings)
    look: dict[str, float] = Field(default_factory=dict)  # brightness, contrast, saturation, warmth, effects: -2..2 (tweak.py)
    template_id: int | None = None
    blueprint_id: int | None = None
    renders: list[str] = Field(default_factory=list)  # rendered videos, newest last

    @property
    def fps(self) -> int:
        return self.blueprint.output.fps or 30

    @property
    def duration(self) -> float:
        return self.blueprint.duration

    def music_path(self) -> str | None:
        return self.music or self.blueprint.audio.track_path

    def check(self) -> None:
        segs = self.blueprint.segments
        if not segs or len(segs) != len(self.shots):
            raise ValueError("Every shot needs footage")
        if abs(segs[0].start) > EPS or any(abs(a.end - b.start) > EPS for a, b in zip(segs, segs[1:])):
            raise ValueError("Shots must follow each other without gaps")
        if any(s.duration < MIN_SHOT / self.fps - EPS for s in segs):
            raise ValueError("A shot can't be shorter than two frames")


# ---------------------------------------------------------------- from a render


def from_plan(bp: Blueprint, plan: EditPlan, clips: list[str], *, id: str = "", name: str = "", music: str | None = None,
              settings: RenderSettings | None = None, template_id: int | None = None, blueprint_id: int | None = None,
              rendered: str | None = None) -> EditProject:
    shots = [ShotSource(clip=str(s.clip.path), src_start=round(s.src_start, 4), center=tuple(float(v) for v in s.center),
                        content_match=float(s.content_match), stats=_plain(s.stats)) for s in plan.shots]
    return EditProject(id=id, name=name or bp.name, blueprint=bp, shots=shots, clips=[str(c) for c in clips], music=music,
                       settings=settings or RenderSettings(), template_id=template_id, blueprint_id=blueprint_id,
                       renders=[rendered] if rendered else [])


def _plain(stats: dict) -> dict:
    """Colour statistics as JSON-able values (numpy arrays and floats become lists and floats)."""
    out = {}
    for k, v in (stats or {}).items():
        if isinstance(v, np.ndarray):
            out[k] = v.tolist()
        elif isinstance(v, (np.floating, np.integer)):
            out[k] = v.item()
        elif isinstance(v, (int, float, str, bool, list, tuple, type(None))):
            out[k] = v
    return out


def to_plan(p: EditProject) -> EditPlan:
    """The plan the renderer needs, straight from the project: no watching the clips again."""
    p.check()
    probes: dict[str, Probe] = {}
    shots, warnings = [], []
    for i, (seg, src) in enumerate(zip(p.blueprint.segments, p.shots)):
        pr = probes.get(src.clip) or probes.setdefault(src.clip, probe(src.clip))
        clip = ClipInfo(probe=pr, times=np.zeros(0), motion=np.zeros(0), thumbs=np.zeros((0, 1, 256)), cut_after=np.zeros(0, bool))
        need = seg.source_seconds_needed()
        start = min(max(0.0, src.src_start), max(0.0, pr.duration - 1.0 / p.fps))
        length = max(1.0 / p.fps, min(need, pr.duration - start))
        if length < need - 1 / p.fps:
            warnings.append(f"Shot {i + 1}: the clip ends {need - length:.1f}s early, so the shot is slowed to fill its time.")
        stats = {k: tuple(v) if isinstance(v, list) else v for k, v in src.stats.items()}
        shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length, center=tuple(src.center),
                          stats=stats, content_match=src.content_match))
    return EditPlan(shots=shots, warnings=warnings)


# ---------------------------------------------------------------- re-cutting a shot in time


def _speed_at(seg: Segment, t: float) -> float:
    """Playback speed in effect at edit time t (may be outside the shot: its first/last speed holds)."""
    pts = sorted(seg.speed, key=lambda q: q.at)
    frac = (t - seg.start) / seg.duration if seg.duration else 0.0
    cur = pts[0].speed
    for q in pts:
        if q.at <= frac + EPS:
            cur = q.speed
    return cur


def _source_at(seg: Segment, src_start: float, t: float) -> float:
    """Where in the clip edit time t falls, following the shot's speed (before its start: its first speed)."""
    if t <= seg.start:
        return src_start - (seg.start - t) * sorted(seg.speed, key=lambda q: q.at)[0].speed
    if t >= seg.end:
        return src_start + seg.source_seconds_needed() + (t - seg.end) * _speed_at(seg, seg.end)
    return src_start + seg.source_offset(t - seg.start)


def _respan(seg: Segment, start: float, end: float, fps_ref: float, same_cuts: bool = False) -> Segment:
    """The same shot over a new [start, end): speed, moves and per-frame curves stay where they were in time.
    Whips belong to the cuts: they stay when `same_cuts` (the cuts moved, not went away), else they go with a cut
    that is no longer at the shot's start or end."""
    old = seg.model_copy(deep=True)
    new = seg.model_copy(deep=True)
    new.start, new.end = round(start, 4), round(end, 4)
    dur = new.end - new.start

    # Speed points are fractions of the shot: keep each one's moment, and the speed in effect at the new start.
    times = [(old.start + q.at * old.duration, q.speed) for q in sorted(old.speed, key=lambda q: q.at)]
    pts = [SpeedPoint(at=0.0, speed=_speed_at(old, new.start))]
    for t, v in times:
        if new.start + EPS < t < new.end - EPS:
            pts.append(SpeedPoint(at=round((t - new.start) / dur, 4), speed=v))
    new.speed = pts

    events = []
    for e in old.events:
        e = e.model_copy()
        if e.kind == "whip_in":  # belongs to the cut at the shot's start
            if abs(new.start - old.start) > EPS and not same_cuts:
                continue
            e.t = 0.0
        elif e.kind == "whip_out":  # belongs to the cut at its end
            if abs(new.end - old.end) > EPS and not same_cuts:
                continue
            e.t = round(dur, 4)
        elif e.kind == "push":  # a slow zoom over the whole shot
            e.t, e.duration = 0.0, round(dur, 4)
        else:
            at = old.start + e.t
            if not (new.start - EPS <= at < new.end - EPS):
                continue
            e.t = round(at - new.start, 4)
        events.append(e)
    new.events = events

    def frames(values: list[float], fps: float) -> list[float]:
        if not values:
            return values
        idx = np.clip(np.round((new.start + np.arange(max(1, round(dur * fps))) / fps - old.start) * fps), 0, len(values) - 1)
        return [values[int(i)] for i in idx]

    if new.camera:
        cf = new.camera.fps or fps_ref
        new.camera.s, new.camera.r = frames(old.camera.s, cf), frames(old.camera.r, cf)
        new.camera.x, new.camera.y = frames(old.camera.x, cf), frames(old.camera.y, cf)
    new.blur, new.velocity = frames(old.blur, fps_ref), frames(old.velocity, fps_ref)
    return new


def _snap(p: EditProject, t: float) -> float:
    return round(round(t * p.fps) / p.fps, 4)


# ---------------------------------------------------------------- edit operations (each returns a new project)


def move_cut(p: EditProject, cut: int, t: float) -> EditProject:
    """Move cut `cut` (between shot cut-1 and shot cut) to edit time t; the neighbours trade the time."""
    p = p.model_copy(deep=True)
    segs = p.blueprint.segments
    if not 0 < cut < len(segs):
        raise ValueError("No such cut")
    a, b = segs[cut - 1], segs[cut]
    lo, hi = a.start + MIN_SHOT / p.fps, b.end - MIN_SHOT / p.fps
    t = _snap(p, min(max(t, lo), hi))
    src = p.shots[cut]
    src.src_start = round(max(0.0, _source_at(b, src.src_start, t)), 4)
    fps_ref = p.blueprint.source.fps or p.fps
    segs[cut - 1] = _respan(a, a.start, t, fps_ref, same_cuts=True)
    segs[cut] = _respan(b, t, b.end, fps_ref, same_cuts=True)
    p.check()
    return p


def split(p: EditProject, i: int, t: float) -> EditProject:
    """Make two shots of shot i at edit time t; the second continues the same footage after a plain cut."""
    p = p.model_copy(deep=True)
    segs = p.blueprint.segments
    seg = segs[i]
    t = _snap(p, t)
    if not seg.start + MIN_SHOT / p.fps - EPS <= t <= seg.end - MIN_SHOT / p.fps + EPS:
        raise ValueError("Split inside the shot, at least two frames from its ends")
    fps_ref = p.blueprint.source.fps or p.fps
    first, second = _respan(seg, seg.start, t, fps_ref), _respan(seg, t, seg.end, fps_ref)
    second.transition_in, second.flash_frames, second.transition_from = "cut", 0, ""
    src2 = p.shots[i].model_copy(deep=True)
    src2.src_start = round(_source_at(seg, p.shots[i].src_start, t), 4)
    segs[i : i + 1] = [first, second]
    p.shots.insert(i + 1, src2)
    p.check()
    return p


def remove(p: EditProject, i: int) -> EditProject:
    """Remove shot i; the shot before takes its time (the first shot's neighbour starts earlier), so the
    cuts after it stay on the music."""
    p = p.model_copy(deep=True)
    segs = p.blueprint.segments
    if len(segs) < 2:
        raise ValueError("An edit needs at least one shot")
    fps_ref = p.blueprint.source.fps or p.fps
    if i == 0:
        nxt = segs[1]
        p.shots[1].src_start = round(max(0.0, _source_at(nxt, p.shots[1].src_start, 0.0)), 4)
        segs[1] = _respan(nxt, 0.0, nxt.end, fps_ref)
        segs[1].transition_in, segs[1].flash_frames = "cut", 0  # the first shot comes in from nothing
    else:
        prev = _respan(segs[i - 1], segs[i - 1].start, segs[i].end, fps_ref)
        prev.events += [e.model_copy(update={"t": round(prev.duration, 4)}) for e in segs[i].events if e.kind == "whip_out"]
        segs[i - 1] = prev
    del segs[i]
    del p.shots[i]
    p.check()
    return p


def swap(p: EditProject, i: int, j: int) -> EditProject:
    """Exchange the footage of shots i and j; each slot keeps its timing, speed, moves and transition."""
    p = p.model_copy(deep=True)
    if not (0 <= i < len(p.shots) and 0 <= j < len(p.shots)):
        raise ValueError("No such shot")
    p.shots[i], p.shots[j] = p.shots[j], p.shots[i]
    return p


def set_shot(p: EditProject, i: int, *, clip: str | None = None, src_start: float | None = None,
             center: tuple[float, float] | None = None, speed: float | None = None,
             transition: Transition | None = None) -> EditProject:
    """Change one shot: its footage and in-point, its framing, a constant speed, or the transition into it."""
    p = p.model_copy(deep=True)
    if not 0 <= i < len(p.shots):
        raise ValueError("No such shot")
    src, seg = p.shots[i], p.blueprint.segments[i]
    if clip is not None:
        if clip not in p.clips:
            raise ValueError("That clip isn't in this project")
        if clip != src.clip:
            src.clip, src.content_match, src.stats = clip, 0.0, {}
    if src_start is not None:
        src.src_start = round(max(0.0, float(src_start)), 4)
        src.stats = {}
    if center is not None:
        src.center = (min(max(float(center[0]), 0.0), 1.0), min(max(float(center[1]), 0.0), 1.0))
    if speed is not None:
        if not 0.1 <= speed <= 8:
            raise ValueError("Speed must be between 0.1x and 8x")
        seg.speed = [SpeedPoint(at=0.0, speed=round(float(speed), 3))]
    if transition is not None:
        if i == 0 and transition != "cut":
            raise ValueError("The first shot has nothing to transition from")
        seg.transition_in = transition
        if transition == "flash":
            seg.flash_frames = seg.flash_frames or 4
    return p


LOOK_KEYS = ("brightness", "contrast", "saturation", "warmth", "effects")


def set_look(p: EditProject, look: dict) -> EditProject:
    p = p.model_copy(deep=True)
    p.look = {k: round(min(2.0, max(-2.0, float(v))), 2) for k, v in look.items() if k in LOOK_KEYS and float(v)}
    return p


def set_texts(p: EditProject, texts: list[dict]) -> EditProject:
    p = p.model_copy(deep=True)
    out = []
    for t in texts:
        o = TextOverlay(**t)
        o.start, o.end = max(0.0, o.start), min(p.duration, o.end)
        if o.text.strip() and o.end - o.start >= 0.1:
            out.append(o)
    p.blueprint.texts = sorted(out, key=lambda o: o.start)
    return p


# ---------------------------------------------------------------- saving, undo and redo


def save(p: EditProject, folder: str | Path, *, record: bool = True) -> Path:
    """Write project.json; with `record` also keep this state in history/ for undo (and drop any redo)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PROJECT_FILE
    text = p.model_dump_json()
    path.write_text(text)
    if record:
        hist = folder / HISTORY
        hist.mkdir(exist_ok=True)
        pos = _position(folder)
        for f in hist.glob("*.json"):
            if int(f.stem) > pos:
                f.unlink()
        (hist / f"{pos + 1:04d}.json").write_text(text)
        (hist / "position").write_text(str(pos + 1))
    return path


def load(folder: str | Path) -> EditProject:
    path = Path(folder) / PROJECT_FILE
    if not path.is_file():
        raise FileNotFoundError(path)
    return EditProject.model_validate_json(path.read_text())


def _position(folder: Path) -> int:
    f = folder / HISTORY / "position"
    return int(f.read_text()) if f.is_file() else 0


def step(folder: str | Path, back: bool) -> EditProject:
    """Undo (back) or redo: move through history/ and make that state the project."""
    folder = Path(folder)
    pos = _position(folder)
    target = pos - 1 if back else pos + 1
    f = folder / HISTORY / f"{target:04d}.json"
    if target < 1 or not f.is_file():
        raise ValueError("Nothing to undo" if back else "Nothing to redo")
    shutil.copyfile(f, folder / PROJECT_FILE)
    (folder / HISTORY / "position").write_text(str(target))
    return load(folder)


def can_step(folder: str | Path) -> dict:
    folder = Path(folder)
    pos = _position(folder)
    return {"undo": (folder / HISTORY / f"{pos - 1:04d}.json").is_file(), "redo": (folder / HISTORY / f"{pos + 1:04d}.json").is_file()}

