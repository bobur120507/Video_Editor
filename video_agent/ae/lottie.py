"""Lottie / Bodymovin JSON -> Blueprint: an After Effects comp exported with Bodymovin becomes a template.

Format: Lottie spec (lottie.github.io/lottie-spec), readable guide lottiefiles.github.io/lottie-docs.
Keyframes are eased with their own bezier handles; spatial position keys follow their path by distance.
What becomes a shot, a flash, a speed change or text is decided in timeline.py, shared with .aep import.
There is no music in a Lottie file: the edit is timed to the comp's frames, and music comes at /apply.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from ..blueprint import Blueprint
from types import SimpleNamespace

from . import curves, expr, timeline

FOOTAGE = {2, 7, 8, 9, 10}  # image, video placeholder, image sequence, video, image placeholder
KINDS = {0: "precomp", 1: "solid", 5: "text", 6: "audio", 13: "camera"}


def _arr(v) -> np.ndarray:
    return np.atleast_1d(np.asarray(v, dtype=np.float64))


def _handle(h, dim: int) -> np.ndarray:
    """A keyframe's bezier handle coordinate, one number per dimension (Lottie stores one or a list)."""
    a = _arr(h)
    return np.resize(a, dim) if a.size else np.zeros(dim)


class Prop:
    """One Lottie property: static ({"a":0,"k":v}) or keyframed ({"a":1,"k":[...]}), plus an optional expression."""

    def __init__(self, raw: dict | None, default, fr: float, seed: int = 0, comp: SimpleNamespace | None = None):
        raw = raw or {}
        self.default, self.fr, self.seed, self.comp, self.failed = _arr(default), fr, seed, comp, False
        self.expr = raw.get("x") if isinstance(raw.get("x"), str) else ""
        k = raw.get("k", default)
        self.keys = k if raw.get("a") == 1 or (isinstance(k, list) and k and isinstance(k[0], dict) and "t" in k[0]) else None
        self.static = None if self.keys else _arr(k)

    def _start(self, i: int) -> np.ndarray:
        key = self.keys[i]
        if "s" in key:
            return _arr(key["s"])
        return _arr(self.keys[i - 1]["e"]) if i and "e" in self.keys[i - 1] else self.default

    def raw(self, frame: float) -> np.ndarray:
        if self.keys is None:
            return self.static
        keys = self.keys
        if frame <= keys[0]["t"]:
            return self._start(0)
        for i in range(len(keys) - 1):
            a, b = keys[i], keys[i + 1]
            if a["t"] <= frame < b["t"]:
                v0 = self._start(i)
                v1 = _arr(a["e"]) if "e" in a else self._start(i + 1)
                if a.get("h") == 1:
                    return v0
                p = (frame - a["t"]) / max(b["t"] - a["t"], 1e-9)
                o, n = a.get("o", {}), a.get("i", {})
                dim = v0.size
                ox, oy = _handle(o.get("x", 0), dim), _handle(o.get("y", 0), dim)
                ix, iy = _handle(n.get("x", 1), dim), _handle(n.get("y", 1), dim)
                to, ti = a.get("to"), a.get("ti")
                if to is not None and ti is not None and (np.any(_arr(to)) or np.any(_arr(ti))) and dim >= 2:
                    # Spatial keyframe: the position moves along a bezier path, by distance.
                    s = float(curves.cubic_bezier(ox[0], oy[0], ix[0], iy[0], p))
                    pt = curves.along_path(v0[:2], v0[:2] + _arr(to)[:2], v1[:2] + _arr(ti)[:2], v1[:2], s)
                    return np.concatenate([pt, v0[2:] + (v1[2:] - v0[2:]) * s])
                eased = np.array([float(curves.cubic_bezier(ox[d], oy[d], ix[d], iy[d], p)) for d in range(dim)])
                return v0 + (v1 - v0) * eased
        return self._start(len(keys) - 1)

    def at(self, frame: float) -> np.ndarray:
        keys = [k["t"] for k in self.keys] if self.keys else []
        raw = self.raw(frame)
        if self.expr and self.comp is not None:
            def loop(kind, n):
                v = timeline.expression(f"loopOut('{kind}', {n})", raw, frame, self.fr, keys, self.raw)
                return v.tolist() if v.size > 1 else float(v[0])

            try:
                v = np.atleast_1d(np.asarray(expr.evaluate(self.expr, expr.Context(raw.tolist() if raw.size > 1 else float(raw[0]),
                                                                                   frame / self.fr, self.comp, None, loop, self.seed)), dtype=np.float64))
                return np.concatenate([v, raw[v.size:]]) if v.size < raw.size else v[: max(raw.size, 1)]
            except (expr.Unsupported, ValueError, TypeError):
                self.failed = True
        return timeline.expression(self.expr, raw, frame, self.fr, keys, self.raw, self.seed)


class LottieLayer:
    def __init__(self, raw: dict, fr: float, comp_w: int, comp_h: int, assets: dict, depth: int = 0, duration: float = 0.0):
        self.raw, self.fr = raw, fr
        comp = SimpleNamespace(width=float(comp_w), height=float(comp_h), duration=duration, frameDuration=1.0 / fr)
        ty = raw.get("ty")
        self.kind = "footage" if ty in FOOTAGE else KINDS.get(ty, "other")
        self.ind, self.parent_ind, self.parent = raw.get("ind"), raw.get("parent"), None
        self.name = raw.get("nm", "")
        self.ip, self.op = float(raw.get("ip", 0)), float(raw.get("op", 0))
        self.st, self.sr = float(raw.get("st", 0)), float(raw.get("sr", 1) or 1)
        self.hidden = bool(raw.get("hd"))
        self.is_3d, self.is_matte = raw.get("ddd") == 1, raw.get("td", 0) not in (0, None)
        seed = int(self.ind or 0) * 101
        ks = raw.get("ks", {})
        p = ks.get("p", {})
        self.split = isinstance(p, dict) and bool(p.get("s"))  # position stored as separate X and Y
        self.props = {
            "anchor": Prop(ks.get("a"), [0, 0, 0], fr, seed, comp),
            "position": None if self.split else Prop(p, [0, 0, 0], fr, seed, comp),
            "px": Prop(p.get("x"), 0, fr, seed, comp) if self.split else None,
            "py": Prop(p.get("y"), 0, fr, seed + 1, comp) if self.split else None,
            "pz": Prop(p.get("z"), 0, fr, seed + 4, comp) if self.split and "z" in p else None,
            "scale": Prop(ks.get("s"), [100, 100, 100], fr, seed + 2, comp),
            "rotation": Prop(ks.get("r") or ks.get("rz"), 0, fr, seed + 3, comp),
            "opacity": Prop(ks.get("o"), 100, fr, 0, comp),
            "zoom": Prop(raw.get("pe"), 0, fr) if self.kind == "camera" else None,
        }
        self.tm = Prop(raw["tm"], 0, fr) if "tm" in raw else None
        asset = assets.get(raw.get("refId"), {})
        self.w = float(raw.get("w") or asset.get("w") or comp_w)
        self.h = float(raw.get("h") or asset.get("h") or comp_h)
        self.inner_fps = fr  # Lottie precomps run at the comp's frame rate
        inner = asset.get("layers") if self.kind == "precomp" and depth < timeline.MAX_NESTING else None
        self.children = layers_from(inner, fr, self.w, self.h, assets, depth + 1) if inner else []
        self.color = raw.get("sc", "")
        self.motion_blur = bool(raw.get("mb"))
        self.effect_names = [e["nm"] for e in raw.get("ef", []) if isinstance(e, dict) and e.get("nm")]

    def visible(self, frame: float) -> bool:
        return not self.hidden and self.ip - timeline.EPS <= frame < self.op - timeline.EPS and float(self.props["opacity"].at(frame)[0]) >= 50

    def _position(self, frame: float) -> np.ndarray:
        pr = self.props
        if not self.split:
            return pr["position"].at(frame)
        z = pr["pz"].at(frame)[0] if pr["pz"] is not None else 0.0
        return np.array([pr["px"].at(frame)[0], pr["py"].at(frame)[0], z])

    def local(self, frame: float) -> np.ndarray:
        pr = self.props
        return timeline.transform(self._position(frame), pr["anchor"].at(frame), pr["scale"].at(frame), pr["rotation"].at(frame)[0])

    def world(self, frame: float, depth: int = 0) -> np.ndarray:
        m = self.local(frame)
        if self.parent is not None and depth < timeline.MAX_PARENTS:
            m = self.parent.world(frame, depth + 1) @ m
        return m

    def depth(self, frame: float) -> float:
        p = self._position(frame)
        return float(p[2]) if p.size > 2 else 0.0

    def camera(self, frame: float) -> tuple[float, float, float, float, float]:
        p = self._position(frame)
        return float(p[0]), float(p[1]), float(p[2]) if p.size > 2 else -1.0, float(self.props["zoom"].at(frame)[0]), float(self.props["rotation"].at(frame)[0])

    def is_white_solid(self) -> bool:
        if self.kind != "solid" or not self.color.startswith("#") or len(self.color) < 7:
            return False
        return min(int(self.color[i : i + 2], 16) for i in (1, 3, 5)) >= 230

    def source_time(self, frame: float) -> float:
        if self.tm is not None:
            return float(self.tm.at(frame)[0])
        return (frame - self.st) / self.fr / self.sr

    def text_style(self, frame: float) -> tuple[str, float, list[float]] | None:
        try:
            doc = self.raw["t"]["d"]["k"][0]["s"]
        except (KeyError, IndexError, TypeError):
            return None
        return str(doc.get("t", "")), float(doc.get("s", 72)), list(doc.get("fc") or [1, 1, 1])

    def understood(self) -> bool:
        return all(not p.expr or (expr.supported(p.expr) and not p.failed) for p in self.props.values() if p is not None)


def layers_from(raw_layers: list, fr: float, w: float, h: float, assets: dict, depth: int = 0) -> list[LottieLayer]:
    layers = [LottieLayer(raw, fr, w, h, assets, depth) for raw in raw_layers if isinstance(raw, dict)]  # top of the stack first
    by_ind = {la.ind: la for la in layers if la.ind is not None}
    for la in layers:
        la.parent = by_ind.get(la.parent_ind)
    return layers


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "layers" not in data or "fr" not in data:
        raise ValueError("Not a Lottie/Bodymovin file (needs 'fr' and 'layers')")
    return data


def layers_of(data: dict) -> list[LottieLayer]:
    assets = {a.get("id"): a for a in data.get("assets", []) if isinstance(a, dict)}
    return layers_from(data["layers"], float(data["fr"]), int(data.get("w", 1080)), int(data.get("h", 1920)), assets)


def to_blueprint(path: str | Path, name: str = "") -> Blueprint:
    path = Path(path)
    data = load(path)
    return timeline.assemble(layers_of(data), float(data["fr"]), int(data.get("w", 1080)), int(data.get("h", 1920)),
                             float(data.get("ip", 0)), float(data["op"]), name=name, origin=path.name,
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tag="lottie", source_label="Lottie/Bodymovin")
