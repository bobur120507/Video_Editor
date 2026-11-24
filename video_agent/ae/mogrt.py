"""Motion Graphics Templates (.mogrt): the controls a template exposes (text fields, colours, sliders, checkboxes).

A .mogrt is a zip holding `definition.json` (the exposed controls and metadata), the AE/Premiere project
(`project.aegraphic`, binary) and a thumbnail. Adobe publishes no field-level schema for definition.json,
so this reader does not assume one layout: it walks the JSON for objects that carry a control name
(`uiName`, `name`, `capsuleName`...) and a value, and reads strings in Adobe's localised form
({"strDB": [{"localeString": "en_US", "str": "..."}]}) or plain. Checked against hand-made files only,
not a real .mogrt yet: treat the result as a best effort and look at `raw` when it misses a control.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

NAME_KEYS = ("uiName", "displayName", "name", "label")
VALUE_KEYS = ("value", "defaultValue", "default")


@dataclass
class Control:
    name: str
    kind: str  # text, color, number, checkbox, other
    value: object = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Mogrt:
    name: str
    controls: list[Control]
    files: list[str]
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def texts(self) -> list[str]:
        """Default text of every text control, in order: usable as on-screen text lines at /apply."""
        return [str(c.value) for c in self.controls if c.kind == "text" and c.value not in (None, "")]


def _string(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        db = v.get("strDB")
        if isinstance(db, list) and db:
            english = next((d for d in db if isinstance(d, dict) and str(d.get("localeString", "")).startswith("en")), db[0])
            if isinstance(english, dict) and isinstance(english.get("str"), str):
                return english["str"]
        for k in ("str", "text", "textEditValue"):
            if isinstance(v.get(k), str):
                return v[k]
    return None


def _kind_and_value(v) -> tuple[str, object]:
    if isinstance(v, bool):
        return "checkbox", v
    if isinstance(v, (int, float)):
        return "number", v
    s = _string(v)
    if s is not None:
        return "text", s
    if isinstance(v, (list, tuple)) and 3 <= len(v) <= 4 and all(isinstance(x, (int, float)) for x in v):
        return "color", list(v)
    if isinstance(v, dict):
        for k in ("strDB", "textEditValue"):
            if k in v:
                return "text", _string(v)
        rgb = [v.get(c) for c in ("red", "green", "blue")]
        if all(isinstance(x, (int, float)) for x in rgb):
            return "color", rgb
    return "other", v


def _controls(node, out: list[Control], seen: set[int]) -> None:
    if isinstance(node, dict):
        name = next((_string(node[k]) for k in NAME_KEYS if k in node and _string(node[k])), None)
        vkey = next((k for k in VALUE_KEYS if k in node), None)
        if name and vkey and id(node) not in seen:
            seen.add(id(node))
            kind, value = _kind_and_value(node[vkey])
            out.append(Control(name=name, kind=kind, value=value, raw=node))
            return
        for v in node.values():
            _controls(v, out, seen)
    elif isinstance(node, list):
        for v in node:
            _controls(v, out, seen)


def read(path: str | Path) -> Mogrt:
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        files = z.namelist()
        definition = next((f for f in files if f.rsplit("/", 1)[-1].lower() == "definition.json"), None)
        if definition is None:
            raise ValueError(f"{path.name}: no definition.json inside (is it a .mogrt?)")
        data = json.loads(z.read(definition).decode("utf-8-sig"))
    out: list[Control] = []
    for key in ("clientControls", "controls"):  # the usual home of the exposed controls
        if key in data:
            _controls(data[key], out, set())
    if not out:
        _controls(data, out, set())
    name = _string(data.get("capsuleName")) or _string(data.get("name")) or path.stem
    return Mogrt(name=name, controls=out, files=files, raw=data)
