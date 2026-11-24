"""An After Effects comp's layers -> Blueprint, shared by the Lottie (lottie.py) and .aep (aep.py) importers.

Real projects nest comps (title -> main -> parts -> words), so the layers are first flattened: every
leaf layer is placed through its chain of precomp layers, composing their timing (start, stretch, time
remap) and transforms, and the 3D cameras of each comp on the way.

Then:
- shots: the topmost visible picture (footage or solid) at each frame; a cut where it changes.
  Track mattes are not pictures. A white solid shown for a few frames before a shot is a flash
- camera: footage moves with its own transform; a solid background moves with what it sits in. A comp's
  3D camera is projected onto the picture (zoom / distance, x/y pan, z roll), so a camera push in AE
  becomes a push on the new footage. X/Y rotation and orientation are not projected (perspective)
- speed: time remap or time stretch through every level, as speed points
- text: each text layer as on-screen text while it is visible, sized by its scale on screen
- motion blur: any layer with AE's motion blur switch on gives a 180 degree shutter
- expressions: wiggle(...) and loopOut(...) are evaluated (curves.py); others are listed in the notes
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

from ..blueprint import Blueprint, CameraCurve, HookInfo, Segment, SourceInfo, SpeedPoint, TextOverlay
from . import curves

MAX_FLASH = 6  # frames: a white solid on top for longer than this is content, not a flash
SPEED_PIECES = 6  # a time remap is summarised as this many constant-speed pieces per shot (Segment.speed)
MAX_PARENTS = 16
MAX_NESTING = 10
EPS = 1e-3  # frames: AE stores in/out points in its own time base, so 10 frames can read as 10.0000001
MAX_CAPTION = 60  # characters: a longer text block is a texture (scrolling lorem ipsum), not a caption
PICTURES = ("footage", "solid", "precomp")  # a precomp left unopened (empty, or nested too deep) is footage


class Layer(Protocol):
    kind: str  # footage, precomp, solid, text, audio, camera, other
    name: str
    ip: float  # first frame, in its comp's frames
    op: float  # end frame (exclusive)
    w: float  # content size in pixels (what the layer's anchor point refers to)
    h: float
    is_3d: bool
    is_matte: bool
    motion_blur: bool
    effect_names: list[str]
    children: list  # precomp: its comp's layers, top first
    inner_fps: float  # precomp: its comp's frame rate

    def visible(self, frame: float) -> bool: ...
    def world(self, frame: float) -> np.ndarray: ...  # 3x3: layer pixels -> its comp's pixels, parents included
    def depth(self, frame: float) -> float: ...  # z position (3D layers)
    def is_white_solid(self) -> bool: ...
    def source_time(self, frame: float) -> float: ...  # seconds into the layer's own footage / inner comp
    def camera(self, frame: float) -> tuple[float, float, float, float, float]: ...  # camera: x, y, z, zoom, z rotation deg
    def text_style(self, frame: float) -> tuple[str, float, list[float]] | None: ...  # text, font size, fill rgb
    def understood(self) -> bool: ...  # every expression on it is one we evaluate


# ---------------------------------------------------------------- shared helpers for importers


def transform(position, anchor, scale_pct, rotation_deg) -> np.ndarray:
    """AE's 2-D layer transform: translate(position) . rotate . scale . translate(-anchor), as a 3x3 matrix."""
    p, a = np.asarray(position, float)[:2], np.asarray(anchor, float)[:2]
    s = np.asarray(scale_pct, float)[:2] / 100
    r = math.radians(float(rotation_deg))
    c, si = math.cos(r), math.sin(r)
    rs = np.array([[c * s[0], -si * s[1]], [si * s[0], c * s[1]]])
    m = np.eye(3)
    m[:2, :2] = rs
    m[:2, 2] = p - rs @ a
    return m


def understood(expr: str) -> bool:
    return not expr or bool(re.search(r"wiggle\(|loopOut\(", expr))


def expression(expr: str, value: np.ndarray, frame: float, fr: float, key_frames: list[float],
               value_at: Callable[[float], np.ndarray], seed: int = 0) -> np.ndarray:
    """The value after a property's expression, for the expressions we evaluate: loopOut(...) past the last
    key, then wiggle(...) added on top. Anything else leaves the keyframed value as it is."""
    if not expr:
        return value
    if (m := re.search(r"loopOut\(\s*(?:['\"](\w+)['\"])?\s*(?:,\s*(\d+))?\s*\)", expr)) and key_frames and frame > key_frames[-1]:
        kind, n = m.group(1) or "cycle", int(m.group(2) or 0)
        value = np.array([float(curves.loop_out(frame, key_frames, [0] * len(key_frames), kind, n,
                                                value=lambda x, d=d: np.array([value_at(float(xx))[d] for xx in np.atleast_1d(x)]).reshape(np.shape(x))))
                          for d in range(value.size)])
    if m := re.search(r"wiggle\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*(?:,\s*([-\d.]+))?\s*(?:,\s*([-\d.]+))?", expr):
        f, a = float(m.group(1)), float(m.group(2))
        octv = int(float(m.group(3))) if m.group(3) else None
        mult = float(m.group(4)) if m.group(4) else None
        value = value + np.array([float(curves.wiggle(frame / fr, f, a, octv, mult, seed=seed + 7 * d)) for d in range(value.size)])
    return value


def hex_color(rgb) -> str:
    return "#" + "".join(f"{int(round(min(max(c, 0), 1) * 255)):02X}" for c in list(rgb)[:3])


# ---------------------------------------------------------------- flattening


@dataclass
class Comp:
    layers: list
    w: float
    h: float


@dataclass
class Placed:
    """A leaf layer and the precomp layers it sits in (outermost first), with each level's comp."""

    leaf: object
    chain: tuple  # precomp layers
    comps: tuple  # Comp of each level: comps[i] holds chain[i] (or the leaf, for the last)

    def frames(self, f: float) -> list[float]:
        fs = [f]
        for p in self.chain:
            fs.append(p.source_time(fs[-1]) * p.inner_fps)
        return fs

    def visible(self, f: float) -> bool:
        fs = self.frames(f)
        return all(p.visible(x) for p, x in zip(self.chain, fs)) and self.leaf.visible(fs[-1])

    def view(self, f: float, fs: list[float] | None = None) -> np.ndarray:
        """Leaf-comp pixels -> top-comp pixels: each precomp layer, seen through its comp's 3D camera if it is 3D."""
        fs = fs or self.frames(f)
        m = np.eye(3)
        for p, comp, x in zip(self.chain, self.comps, fs):
            m = m @ (camera_2d(comp, x, p.depth(x)) if p.is_3d else np.eye(3)) @ p.world(x)
        return m

    def picture(self, f: float) -> tuple[np.ndarray, float, float]:
        """Where the shot's picture is on screen (3x3 from its content pixels) and the content size.

        Footage moves with its own transform. A solid is a background: it moves with what holds it and
        with its comp's camera, so a camera push in the comp becomes a push on the new footage."""
        fs = self.frames(f)
        m, comp, x = self.view(f, fs), self.comps[-1], fs[-1]
        if self.leaf.kind != "solid":
            cam = camera_2d(comp, x, self.leaf.depth(x)) if self.leaf.is_3d else np.eye(3)
            return m @ cam @ self.leaf.world(x), self.leaf.w, self.leaf.h
        return m @ camera_2d(comp, x, 0.0), comp.w, comp.h

    def source_time(self, f: float) -> float:
        return self.leaf.source_time(self.frames(f)[-1])


def flatten(layers: list, w: float, h: float, chain: tuple = (), comps: tuple = ()) -> list[Placed]:
    """Every leaf layer, top of the stack first, through its precomps."""
    comp = Comp(layers, w, h)
    out: list[Placed] = []
    for la in layers:
        if la.kind == "precomp" and la.children and len(chain) < MAX_NESTING and not la.is_matte:
            out += flatten(la.children, la.w, la.h, chain + (la,), comps + (comp,))
        else:
            out.append(Placed(la, chain, comps + (comp,)))
    return out


def camera_2d(comp: Comp, frame: float, z: float) -> np.ndarray:
    """The comp's active 3D camera seen on a plane at depth z, as a 2D transform of comp pixels.

    One-node camera looking down +z: scale = zoom / distance, the camera's x/y pans, its z rotation rolls."""
    cam = next((la for la in comp.layers if la.kind == "camera" and la.visible(frame)), None)
    if cam is None:
        return np.eye(3)
    x, y, zc, zoom, rz = cam.camera(frame)
    s = zoom / max(z - zc, 1.0)
    r = -math.radians(rz)
    c, si = math.cos(r), math.sin(r)
    m = np.eye(3)
    m[:2, :2] = s * np.array([[c, -si], [si, c]])
    m[:2, 2] = np.array([comp.w / 2, comp.h / 2]) - m[:2, :2] @ np.array([x, y])
    return m


def _deep(layers: list, depth: int = 0) -> list:
    out = []
    for la in layers:
        out.append(la)
        if la.kind == "precomp" and la.children and depth < MAX_NESTING:
            out += _deep(la.children, depth + 1)
    return out


# ---------------------------------------------------------------- assembly


def _camera_at(p: Placed, frame: float, W: int, H: int) -> tuple[float, float, float, float]:
    """The shot's picture as the compositor's (scale, rotation, x, y): centre-based, scale 1 = fills the frame."""
    m, cw, ch = p.picture(frame)
    centre = m @ np.array([cw / 2, ch / 2, 1.0])
    s = math.sqrt(abs(np.linalg.det(m[:2, :2])))
    fill = max(W / cw, H / ch)  # the renderer crops footage to fill the frame: that is scale 1
    return s / fill, math.atan2(m[1, 0], m[0, 0]), (centre[0] - W / 2) / W, (centre[1] - H / 2) / H


def _speeds(p: Placed, f0: int, f1: int, fr: float) -> tuple[list[SpeedPoint], bool]:
    """Speed of the picture's own time through every level (a time remap on a precomp speeds what it holds)."""
    n = f1 - f0
    times = np.array([p.source_time(f) for f in range(f0, f1 + 1)])
    rate = np.diff(times) * fr  # source seconds per comp second, per frame
    reverse = bool((rate < -1e-6).any())
    rate = np.maximum(np.abs(rate), 0.05)
    bounds = np.linspace(0, n, min(SPEED_PIECES, n) + 1).round().astype(int)
    pts: list[SpeedPoint] = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        sp = round(float(rate[a:b].mean()), 3)
        if not pts or abs(pts[-1].speed - sp) > 0.02:
            pts.append(SpeedPoint(at=round(a / n, 4), speed=sp))
    return pts or [SpeedPoint(at=0.0, speed=1.0)], reverse


def _texts(placed: list[Placed], frames: range, fr: float, ip: float, H: int, out_w: int, out_h: int,
           top: dict[int, int]) -> tuple[list[TextOverlay], int]:
    """Captions: each text layer while visible and above the picture on top (a later part's opaque
    background hides the words under it); words on screen together become one caption, top to bottom.
    Returns the captions and how many long text blocks were left out."""
    shown: list[tuple[int, int, float, str, float, list]] = []  # first frame, end frame, y, text, size px, rgb
    blocks = 0
    for i, p in enumerate(placed):
        if p.leaf.kind != "text" or p.leaf.is_matte or any(c.is_matte for c in p.chain):
            continue
        runs: list[list[int]] = []
        for f in frames:
            if i < top.get(f, len(placed)) and p.visible(f):
                if runs and f == runs[-1][1]:
                    runs[-1][1] = f + 1
                else:
                    runs.append([f, f + 1])
        for f0, f1 in runs:
            mid = (f0 + f1) // 2
            fs = p.frames(mid)
            style = p.leaf.text_style(fs[-1])
            if not style or not style[0].strip():
                continue
            text, size, rgb = style
            text = " ".join(text.replace("\r", "\n").split())
            if len(text) > MAX_CAPTION:
                blocks += 1
                continue
            cam = camera_2d(p.comps[-1], fs[-1], p.leaf.depth(fs[-1])) if p.leaf.is_3d else np.eye(3)
            m = p.view(mid, fs) @ cam @ p.leaf.world(fs[-1])
            # Point text hangs off its baseline: the middle of the letters is ~0.35 em above the layer's origin.
            y = (m @ np.array([0.0, -0.35 * size, 1.0]))[1]
            shown.append((f0, f1, y, text, size * math.sqrt(abs(np.linalg.det(m[:2, :2]))), rgb))
    # One caption per stretch of time: same words back to back merge, words shown together stack.
    groups: dict[tuple[int, int], list] = {}
    for item in shown:
        groups.setdefault((item[0], item[1]), []).append(item)
    captions = []
    for (f0, f1), items in sorted(groups.items()):
        items.sort(key=lambda it: it[2])
        captions.append([f0, f1, "\n".join(it[3] for it in items), float(np.mean([it[2] for it in items])),
                         max(it[4] for it in items), items[0][5]])
    merged: list[list] = []
    for c in captions:
        if merged and merged[-1][2] == c[2] and c[0] - merged[-1][1] <= 1:
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    def fit(size: float, text: str) -> int:
        """Size on the output that stays inside the frame, even at the renderer's 140 % pop-in: bold glyphs are
        ~0.6 em wide, lines wrap at 18 characters and are 1.2 em apart."""
        lines = text.split("\n")
        longest = max(min(len(line), 18) for line in lines)
        return int(max(24, min(220, size * out_h / H, 0.9 * out_w / (0.6 * 1.4 * max(longest, 1)), 0.9 * out_h / (1.2 * 1.4 * len(lines)))))

    out = [TextOverlay(text=text, start=round((f0 - ip) / fr, 3), end=round((f1 - ip) / fr, 3),
                       position="top" if y / H < 0.33 else "bottom" if y / H > 0.67 else "center",
                       size=fit(size, text), color=hex_color(rgb or [1, 1, 1]), style="pop" if f1 - f0 < fr else "static")
           for f0, f1, text, y, size, rgb in merged]
    return out, blocks


def assemble(layers: list, fr: float, W: int, H: int, ip: float, op: float, *, name: str, origin: str,
             sha256: str, tag: str, source_label: str) -> Blueprint:
    """`layers` top of the stack first, times in comp frames."""
    from ..analysis.cloner import output_for  # the same output shape rules as a cloned reference

    notes: list[str] = []
    placed = flatten(layers, W, H)
    pictures = [p for p in placed if p.leaf.kind in PICTURES and not p.leaf.is_matte]
    frames = range(int(round(ip)), int(math.ceil(op - EPS)))
    runs: list[list] = []  # [kind, placed, first frame, end frame]
    order = {id(p): i for i, p in enumerate(placed)}  # stacking position, top first
    top_at: dict[int, int] = {}
    for f in frames:
        top = next((p for p in pictures if p.visible(f)), None)
        if top is not None:
            top_at[f] = order[id(top)]
        kind = "flash" if top is not None and top.leaf.is_white_solid() else "shot"
        if runs and runs[-1][0] == kind and runs[-1][1] is top:
            runs[-1][3] = f + 1
        else:
            runs.append([kind, top, f, f + 1])

    output = output_for(W, H)
    segments: list[Segment] = []
    flash_before, flash_from = 0, None
    for kind, p, f0, f1 in runs:
        if kind == "flash" and f1 - f0 <= MAX_FLASH:
            flash_before, flash_from = f1 - f0, f0  # the next shot starts under the white, as in AE
            continue
        if flash_from is not None and p is not None:
            f0 = flash_from
        flash_from = None
        if p is None and segments:  # a gap: the shot before holds
            segments[-1].end = round((f1 - ip) / fr, 3)
            continue
        cam = [_camera_at(p, f, W, H) for f in range(f0, f1)] if p is not None else []
        curve = CameraCurve(fps=fr, s=[round(c[0], 5) for c in cam], r=[round(c[1], 5) for c in cam],
                            x=[round(c[2], 5) for c in cam], y=[round(c[3], 5) for c in cam])
        moving = len(curve) > 1 and any(max(v) - min(v) > 1e-4 for v in (curve.s, curve.r, curve.x, curve.y))
        speed, reverse = _speeds(p, f0, f1, fr) if p is not None else ([SpeedPoint(at=0.0, speed=1.0)], False)
        if reverse:
            notes.append(f"“{p.leaf.name}” plays backwards in places; rendered forwards at the same speed.")
        seg = Segment(start=round((f0 - ip) / fr, 3), end=round((f1 - ip) / fr, 3), speed=speed, camera=curve if moving else None)
        if flash_before:
            seg.transition_in, seg.flash_frames = "flash", flash_before
        flash_before = 0
        segments.append(seg)
    if not segments:
        raise ValueError("No pictures (footage, precomps or solids) found in the comp")

    texts, blocks = _texts(placed, frames, fr, ip, H, output.width, output.height, top_at)
    if blocks:
        notes.append(f"{blocks} long text block(s) (over {MAX_CAPTION} characters) read as background texture, not captions.")
    every = _deep(layers)
    if not any(p.leaf.kind != "solid" for p in pictures):
        notes.append("No footage in the comp (a motion-graphics piece): shots follow its background changes, and its camera "
                     "moves become moves on your clips.")
    if any(la.kind == "camera" for la in every):
        notes.append("3D cameras are read as zoom (zoom ÷ distance), pan and roll; their X/Y tilt and perspective are not re-created.")
    if any(la.kind == "audio" for la in every):
        notes.append("The comp has audio layers; their sound is not imported, so add the music at /apply.")
    odd = sorted({la.name for la in every if not la.understood()})
    if odd:
        notes.append(f"Expressions other than wiggle/loopOut ignored on: {', '.join(odd[:12])}{'…' if len(odd) > 12 else ''}.")
    effects = sorted({e for la in every for e in la.effect_names})
    if effects:
        notes.append(f"Layer effects not re-created from the file: {', '.join(effects[:12])}{'…' if len(effects) > 12 else ''}.")
    notes.append(f"Imported from After Effects ({source_label}): cuts, camera keyframes, time remaps and text follow the comp; "
                 "there is no music analysis, so cuts follow the comp's frames.")

    return Blueprint(
        name=name,
        tags=[tag],
        source=SourceInfo(duration=round((op - ip) / fr, 3), fps=fr, width=W, height=H, sha256=sha256, origin=origin),
        segments=segments,
        texts=texts,
        output=output,
        hook=HookInfo(cuts_in_first_3s=sum(1 for s in segments if 0 < s.start < 3.0),
                      flashes_in_first_3s=sum(1 for s in segments if s.transition_in == "flash" and s.start < 3.0)),
        tier="pro" if any(s.is_slowmo for s in segments) else "standard",
        motion_blur=0.5 if any(la.motion_blur for la in every) else 0.0,  # AE's default shutter angle is 180 degrees
        notes=notes,
    )
