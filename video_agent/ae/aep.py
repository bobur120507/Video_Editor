"""After Effects project (.aep) -> Blueprint, read with py-aep (MIT, github.com/forticheprod/py-aep).

py-aep reads the binary RIFX project file directly: no After Effects needed. It mirrors AE's scripting
object model (comps, layers, transform properties, keyframes) and evaluates keyframes with AE's own
temporal ease (speed and influence). It does not run expressions; wiggle(...) and loopOut(...) are
evaluated here (curves.py), others are reported in the notes. Shots, flashes, speed and text are
decided by timeline.py, the same as for Lottie files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from ..blueprint import Blueprint, TextOverlay
from . import timeline

TRANSFORM = {"anchor": "ADBE Anchor Point", "position": "ADBE Position", "scale": "ADBE Scale",
             "rotation": "ADBE Rotate Z", "opacity": "ADBE Opacity"}


def _py_aep():
    try:
        import py_aep
    except ImportError as e:  # optional dependency
        raise RuntimeError("Reading .aep files needs py-aep: .venv/bin/pip install py-aep") from e
    return py_aep


class AepProp:
    def __init__(self, prop, fr: float, seed: int):
        self.prop, self.fr, self.seed = prop, fr, seed
        self.expr = (getattr(prop, "expression", "") or "") if getattr(prop, "expression_enabled", True) else ""
        self.key_frames = [k.time * fr for k in prop.keyframes] if prop.keyframes else []

    def raw(self, frame: float) -> np.ndarray:
        return np.atleast_1d(np.asarray(self.prop.value_at_time(frame / self.fr), dtype=np.float64))

    def at(self, frame: float) -> np.ndarray:
        return timeline.expression(self.expr, self.raw(frame), frame, self.fr, self.key_frames, self.raw, self.seed)


class AepLayer:
    def __init__(self, layer, fr: float, depth: int = 0, seen: frozenset = frozenset()):
        self.layer, self.fr = layer, fr
        self.name = layer.name
        self.kind = _kind(layer)
        self.hidden = not layer.enabled or bool(getattr(layer, "guide_layer", False))
        self.is_3d = bool(getattr(layer, "three_d_layer", False))
        self.is_matte = bool(getattr(layer, "is_track_matte", False))
        self.ip, self.op = layer.in_point * fr, layer.out_point * fr
        comp = layer.containing_comp
        self.w = float(getattr(layer, "width", 0) or comp.width)
        self.h = float(getattr(layer, "height", 0) or comp.height)
        seed = (layer.index + 1) * 101
        self.props = {k: AepProp(layer.transform.property(m), fr, seed + i) for i, (k, m) in enumerate(TRANSFORM.items())}
        self.zoom = AepProp(layer.property("ADBE Camera Options Group").property("ADBE Camera Zoom"), fr, 0) if self.kind == "camera" else None
        self.remap = AepProp(layer.property("ADBE Time Remapping"), fr, 0) if getattr(layer, "time_remap_enabled", False) else None
        self.parent = None  # set by layers_of
        self.motion_blur = bool(getattr(layer, "motion_blur", False))
        fx = layer.effects if self.kind != "camera" else None
        self.effect_names = [q.name for q in fx.properties] if fx is not None else []
        self.children, self.inner_fps = [], fr
        if self.kind == "precomp":
            inner = layer.source
            if id(inner) not in seen and depth < timeline.MAX_NESTING:  # AE forbids cycles; guard anyway
                self.inner_fps = float(inner.frame_rate)
                self.children = layers_of(inner, depth + 1, seen | {id(inner)})

    def visible(self, frame: float) -> bool:
        return not self.hidden and self.ip - timeline.EPS <= frame < self.op - timeline.EPS and (
            self.kind == "camera" or float(self.props["opacity"].at(frame)[0]) >= 50)

    def local(self, frame: float) -> np.ndarray:
        p = self.props
        return timeline.transform(p["position"].at(frame), p["anchor"].at(frame), p["scale"].at(frame), p["rotation"].at(frame)[0])

    def world(self, frame: float, depth: int = 0) -> np.ndarray:
        m = self.local(frame)
        if self.parent is not None and depth < timeline.MAX_PARENTS:
            m = self.parent.world(frame, depth + 1) @ m
        return m

    def depth(self, frame: float) -> float:
        p = self.props["position"].at(frame)
        return float(p[2]) if p.size > 2 else 0.0

    def camera(self, frame: float) -> tuple[float, float, float, float, float]:
        x, y, z = (list(self.props["position"].at(frame)) + [0.0, 0.0, 0.0])[:3]
        return x, y, z, float(self.zoom.at(frame)[0]), float(self.props["rotation"].at(frame)[0])

    def is_white_solid(self) -> bool:
        if self.kind != "solid":
            return False
        color = getattr(self.layer.source.main_source, "color", None)
        return color is not None and min(color[:3]) >= 0.9

    def source_time(self, frame: float) -> float:
        if self.remap is not None:
            return float(self.remap.at(frame)[0])
        return (frame / self.fr - self.layer.start_time) / ((self.layer.stretch or 100) / 100)

    def text_style(self, frame: float) -> tuple[str, float, list[float]] | None:
        if self.layer.text is None:
            return None
        doc = self.layer.text.property("ADBE Text Document").value_at_time(frame / self.fr)
        return str(getattr(doc, "text", "") or ""), float(getattr(doc, "font_size", 72) or 72), list(getattr(doc, "fill_color", None) or [1, 1, 1])

    def understood(self) -> bool:
        return all(timeline.understood(p.expr) for p in self.props.values())


def _kind(layer) -> str:
    cls = type(layer).__name__
    if cls == "TextLayer":
        return "text"
    if cls == "CameraLayer":
        return "camera"
    if cls != "AVLayer" or getattr(layer, "null_layer", False) or getattr(layer, "adjustment_layer", False):
        return "other"  # shape, light, null, adjustment
    src = layer.source
    if type(src).__name__ == "CompItem":
        return "precomp"
    if src is not None and type(getattr(src, "main_source", None)).__name__ == "SolidSource":
        return "solid"
    if layer.has_video:
        return "footage"
    return "audio" if getattr(layer, "has_audio", False) else "other"


def comps(path: str | Path) -> list:
    """The project's compositions, the likely edit first: not used inside another comp, most footage, longest."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"No such file: {path}")
    try:
        project = _py_aep().parse(str(path)).project
    except (OSError, ValueError, KeyError, IndexError, AttributeError) as e:
        raise ValueError(f"{Path(path).name}: could not read this project ({e}). py-aep reads projects saved by "
                         "After Effects CC 2018 or later; re-save older ones in a recent AE, or export with Bodymovin") from e
    all_comps = list(project.compositions)
    used = {id(la.source) for c in all_comps for la in c.layers if getattr(la, "source", None) is not None}
    return sorted(all_comps, key=lambda c: (id(c) in used, -sum(1 for la in c.layers if _kind(la) in ("footage", "precomp")), -c.duration))


def layers_of(comp, depth: int = 0, seen: frozenset = frozenset()) -> list[AepLayer]:
    fr = float(comp.frame_rate)
    layers = [AepLayer(la, fr, depth, seen | {id(comp)}) for la in comp.layers]  # top of the stack first
    by_layer = {id(a.layer): a for a in layers}
    for a in layers:
        parent = a.layer.parent
        a.parent = by_layer.get(id(parent)) if parent is not None else None
    return layers


def to_blueprint(path: str | Path, name: str = "", comp_name: str | None = None) -> Blueprint:
    path = Path(path)
    candidates = comps(path)
    if not candidates:
        raise ValueError(f"{path.name}: no compositions in the project")
    comp = next((c for c in candidates if c.name == comp_name), None) if comp_name else candidates[0]
    if comp is None:
        raise ValueError(f"No composition named “{comp_name}”; the project has: {', '.join(c.name for c in candidates)}")
    fr = float(comp.frame_rate)
    layers = layers_of(comp)
    bp = timeline.assemble(layers, fr, int(comp.width), int(comp.height), 0.0,
                           comp.duration * fr, name=name, origin=f"{path.name} › {comp.name}",
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tag="aep", source_label=f".aep, comp “{comp.name}”")
    others = [c.name for c in candidates if c is not comp]
    if others and not comp_name:
        bp.notes.insert(0, f"Imported comp “{comp.name}”; the project also has: {', '.join(others[:8])}.")
    return bp
