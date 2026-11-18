"""Move review: show what the cloner detected in a reference and let the user correct it.

The user gets the reference back with every detected move written on screen ("#3 SHAKE") plus a
numbered list, and answers in plain words:
    ok                      everything is right
    remove 3 5              those were not moves
    3 punch                 #3 is a zoom punch, not what I said
    add 4.2 shake           there is a shake at 4.2 s that I missed

Corrections fix the template right away and are stored as ground truth for detection and scoring.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .blueprint import Blueprint, EditEvent, Glow, Segment, SpeedPoint
from .media import ffmpeg

KIND_WORDS = {
    "whip": "Whip / slide transition",
    "punch": "Zoom punch",
    "shake": "Shake",
    "push": "Slow zoom",
    "flash": "Flash",
    "slowmo": "Slow motion",
    "panel": "Corner panel (next shot grows in from a corner)",
    "portal": "Wheel portal (next shot zooms out of its wheel)",
    "cutout": "Car cut-out (next shot's car rises in)",
    "glow": "Glow (picture turns warm and golden on the beat)",
}
SCREEN = {"whip": "WHIP", "punch": "PUNCH", "shake": "SHAKE", "push": "ZOOM", "flash": "FLASH", "slowmo": "SLOW-MO",
          "panel": "PANEL", "portal": "PORTAL", "cutout": "CUT-OUT", "glow": "GLOW"}
TRANSITIONS = ("panel", "portal", "cutout")  # shown at a cut and stored as Segment.transition_in
SYNONYMS = [
    (r"glow|shine|shiny|golden|light ?leak|gleam", "glow"),
    (r"cut.?out|car (?:rise|fl[yi])|rises? in|detach", "cutout"),
    (r"portal|wheel|iris|circle|rim", "portal"),
    (r"panel|corner|window|picture.in", "panel"),
    (r"shak|jolt|wobbl", "shake"),
    (r"punch|bounce|zoom ?hit", "punch"),
    (r"whip|slide|swipe|swish|transition", "whip"),
    (r"push|slow ?zoom|zoom ?in|zoom ?out", "push"),
    (r"flash|white", "flash"),
    (r"slow", "slowmo"),
]
SNAP = 0.12  # a time the user types snaps to a music hit this close (people round to tenths)
ON_SCREEN = 0.5  # seconds each label stays on the preview


@dataclass
class Move:
    n: int
    t: float  # seconds on the reference timeline
    kind: str
    detail: str = ""

    def line(self) -> str:
        return f"#{self.n}  {self.t:5.2f}s  {KIND_WORDS[self.kind]}{(' ' + self.detail) if self.detail else ''}"


def _arrow(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "→" if dx >= 0 else "←"
    return "↓" if dy >= 0 else "↑"


def moves(bp: Blueprint) -> list[Move]:
    found: list[Move] = []
    segs = bp.segments
    for i, seg in enumerate(segs):
        out = next((e for e in segs[i - 1].events if e.kind == "whip_out"), None) if i else None
        inn = next((e for e in seg.events if e.kind == "whip_in"), None)
        if out or inn:
            e = out or inn
            found.append(Move(0, seg.start, "whip", _arrow(e.dx, e.dy)))
        if seg.transition_in == "flash" or "flash" in seg.effects:
            found.append(Move(0, seg.start, "flash"))
        if seg.transition_in in TRANSITIONS:
            found.append(Move(0, seg.start, seg.transition_in, seg.transition_from.replace("_", " ")))
        if seg.is_slowmo:
            found.append(Move(0, seg.start, "slowmo"))
        for e in seg.events:
            if e.kind in ("punch", "shake"):
                found.append(Move(0, round(seg.start + e.t, 3), e.kind, "out" if e.kind == "punch" and e.zoom < 0 else ""))
            elif e.kind == "push" and e.source == "user":
                # Detected pushes are mostly the camera flying, on nearly every shot: listing them
                # buries the real moves. They stay in the template; only user-added ones are listed.
                found.append(Move(0, seg.start, "push", "in" if e.strength > 0 else "out"))
    for g in bp.glows:
        found.append(Move(0, g.t, "glow"))
    found.sort(key=lambda m: (m.t, m.kind))
    for n, m in enumerate(found, 1):
        m.n = n
    return found


STYLE_WORDS = {
    "velocity": "Style: velocity edit (speeds up between hits, lands sharp on them, never stops moving)",
    "calm": "Style: calm edit (still between moves, sharp)",
}


def as_text(bp: Blueprint) -> str:
    ms = moves(bp)
    body = "\n".join(m.line() for m in ms) if ms else "No edit moves found (only cuts)."
    return f"{STYLE_WORDS[bp.style]}\n{body}"


def annotate(bp: Blueprint, reference: str | Path, out: str | Path, font: str) -> Path:
    """The reference with each move's number and name on screen when it happens, plus a time code."""
    out = Path(out)
    draws, shown = [], []
    for m in moves(bp):
        label = f"#{m.n} {SCREEN[m.kind]} {m.detail}".strip()
        row = sum(1 for t in shown if abs(t - m.t) < ON_SCREEN)  # stack labels that are on screen together
        shown.append(m.t)
        draws.append(
            f"drawtext=fontfile='{font}':text='{label}':fontsize=h/22:fontcolor=yellow:borderw=4:bordercolor=black"
            f":x=(w-text_w)/2:y=h*(0.1+{row * 0.06:.2f}):enable='between(t,{max(0.0, m.t - 0.05):.3f},{m.t + ON_SCREEN:.3f})'"
        )
    clock = (f"drawtext=fontfile='{font}':text='%{{eif\\:t\\:d}}.%{{eif\\:mod(t*100\\,100)\\:d\\:2}}s'"
             ":fontsize=h/24:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.9")
    vf = ",".join(["scale=-2:960", *draws, clock])
    ffmpeg(["-i", reference, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", 24, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
    return out


# ---------------------------------------------------------------- corrections


def _kind(word: str) -> str | None:
    for pattern, kind in SYNONYMS:
        if re.search(pattern, word, re.I):
            return kind
    return None


def is_ok(text: str) -> bool:
    return bool(re.fullmatch(r"\s*(ok|okay|correct|all correct|yes|done|good|fine|👍|✅)\W*\s*", text, re.I))


def apply_corrections(bp: Blueprint, text: str) -> tuple[Blueprint, list[str]]:
    """Apply the user's fixes; returns the corrected blueprint and what was understood (one line each)."""
    bp = bp.model_copy(deep=True)
    by_n = {m.n: m for m in moves(bp)}
    done: list[str] = []
    for part in re.split(r"[;\n]+|,(?=\s*(?:remove|delete|add|\+|-|\d+\s+[a-z]))", text, flags=re.I):
        part = part.strip()
        if not part:
            continue
        if m := re.match(r"(?:remove|delete|del|no|-)\s*#?([\d\s,#and]+)$", part, re.I):
            for n in map(int, re.findall(r"\d+", m.group(1))):
                if n in by_n:
                    _remove(bp, by_n[n])
                    done.append(f"removed #{n} ({KIND_WORDS[by_n[n].kind]})")
        elif m := re.match(r"(?:add|\+)\s*([\d.]+)\s*s?\s+(.+)$", part, re.I):
            kind = _kind(m.group(2))
            if kind:
                t = _add(bp, float(m.group(1)), kind)
                done.append(f"added {KIND_WORDS[kind]} at {t:.2f}s")
        elif m := re.match(r"#?(\d+)\s*(?:=|is|->|:)?\s*(?:a |an )?(.+)$", part, re.I):
            n, kind = int(m.group(1)), _kind(m.group(2))
            if n in by_n and kind and kind != by_n[n].kind:
                _remove(bp, by_n[n])
                t = _add(bp, by_n[n].t, kind)
                done.append(f"#{n} is now {KIND_WORDS[kind]} at {t:.2f}s")
    return bp, done


def _seg_at(bp: Blueprint, t: float) -> tuple[int, Segment]:
    for i, s in enumerate(bp.segments):
        if s.start <= t < s.end:
            return i, s
    return len(bp.segments) - 1, bp.segments[-1]


def _remove(bp: Blueprint, move: Move) -> None:
    i, seg = _seg_at(bp, move.t + 1e-4)
    if move.kind == "whip":
        seg.events = [e for e in seg.events if e.kind != "whip_in"]
        if i:
            bp.segments[i - 1].events = [e for e in bp.segments[i - 1].events if e.kind != "whip_out"]
    elif move.kind in ("punch", "shake"):
        seg.events = [e for e in seg.events if not (e.kind == move.kind and abs(seg.start + e.t - move.t) < 0.02)]
    elif move.kind == "push":
        seg.events = [e for e in seg.events if e.kind != "push"]
    elif move.kind == "flash":
        seg.transition_in, seg.effects = "cut", [e for e in seg.effects if e != "flash"]
    elif move.kind in TRANSITIONS:
        seg.transition_in, seg.transition_from = "cut", ""
    elif move.kind == "glow":
        bp.glows = [g for g in bp.glows if abs(g.t - move.t) > 0.02]
    elif move.kind == "slowmo":
        seg.speed = [SpeedPoint(at=0.0, speed=1.0)]


def _add(bp: Blueprint, t: float, kind: str) -> float:
    """Add a move at about `t` (snapped to a music hit, or to the nearest cut for transitions); returns where it went."""
    hits = bp.audio.hits
    near = min(hits, key=lambda h: abs(h - t)) if hits else t
    if abs(near - t) <= SNAP:
        t = near
    if kind == "glow":
        bp.glows = sorted([*bp.glows, Glow(t=round(t, 3), frames=8, warm=9.0)], key=lambda g: g.t)
        return t
    if kind in ("whip", "flash", *TRANSITIONS):
        cut = min((s.start for s in bp.segments[1:]), key=lambda c: abs(c - t), default=0.0)
        i, seg = _seg_at(bp, cut + 1e-4)
        if kind == "whip" and i:
            bp.segments[i - 1].events.append(EditEvent(kind="whip_out", t=bp.segments[i - 1].duration, strength=0.8, dx=1.0, source="user"))
            seg.events.insert(0, EditEvent(kind="whip_in", t=0.0, strength=0.8, dx=1.0, source="user"))
        elif kind == "flash":
            seg.transition_in, seg.flash_frames = "flash", seg.flash_frames or 4
        elif kind in TRANSITIONS:
            seg.transition_in = kind
            seg.transition_from = seg.transition_from or {"panel": "top_right", "cutout": "bottom"}.get(kind, "")
        return cut
    i, seg = _seg_at(bp, t)
    fps = seg.camera.fps if seg.camera else bp.source.fps or 30.0
    rel = round(round((t - seg.start) * fps) / fps, 4)
    if kind == "punch":
        seg.events.append(EditEvent(kind="punch", t=rel, strength=0.15, zoom=1.0, source="user"))
    elif kind == "shake":
        seg.events.append(EditEvent(kind="shake", t=rel, duration=0.25, strength=0.6, rot=1.0, source="user"))
    elif kind == "push":
        seg.events = [e for e in seg.events if e.kind != "push"] + [EditEvent(kind="push", t=0.0, duration=seg.duration, strength=0.2, source="user")]
    elif kind == "slowmo":
        seg.speed = [SpeedPoint(at=0.0, speed=0.5)]
    return t


def snapshot(bp: Blueprint) -> list[dict]:
    """The move list as plain data, for storing as ground truth."""
    return [asdict(m) for m in moves(bp)]
