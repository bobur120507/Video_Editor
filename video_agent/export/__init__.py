"""Export an edit project to other editors: one file per format, or a zip with the media and every format.

    aftereffects  .jsx script that builds the comp (After Effects has no timeline import)
    premiere      Final Cut Pro 7 XML: Premiere Pro, DaVinci Resolve
    fcpxml        FCPXML 1.10: Final Cut Pro, DaVinci Resolve
    otio          OpenTimelineIO: Resolve 18.5+, Premiere 26+, Kdenlive 25.04+, Avid
    edl           CMX 3600: cuts and speeds for anything
    capcut        zip: one ready-cut clip per shot, the music and the text (CapCut imports no timeline file)
    bundle        zip: media/ (clips, music, the agent's render) + every timeline format + how to open each

`base` is where the media folder will be on the computer that opens the files (e.g. C:/Users/me/Desktop/edit):
the files then point at base/media/<name> and open without relinking. Without it they point at the files on
this machine, and the After Effects script also looks in a media/ folder next to itself.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Callable

from ..project import EditProject
from . import aftereffects, capcut, edl, fcpxml, otio, xmeml
from .timeline import Flat, Media, flatten

FORMATS = {
    # key: (file suffix, label, writer)
    "aftereffects": ("_aftereffects.jsx", "After Effects script", aftereffects.write),
    "premiere": ("_premiere_resolve.xml", "Premiere Pro / Resolve XML", xmeml.write),
    "fcpxml": (".fcpxml", "Final Cut Pro / Resolve FCPXML", fcpxml.write),
    "otio": (".otio", "OpenTimelineIO", otio.write),
    "edl": (".edl", "EDL (CMX 3600)", lambda flat, locate: edl.write(flat)),
}


def locator(base: str | None) -> Callable[[Media], str]:
    if not base:
        return lambda m: m.path
    base = base.strip().rstrip("/\\")
    sep = "\\" if "\\" in base and "/" not in base else "/"
    return lambda m: f"{base}{sep}media{sep}{m.name}"


def stem(p: EditProject) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in (p.name or "edit"))
    return keep.strip("_")[:60] or "edit"


def export(p: EditProject, fmt: str, base: str | None = None, flat: Flat | None = None) -> tuple[str, str]:
    """(file name, text) of one format."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    flat = flat or flatten(p)
    suffix, _, writer = FORMATS[fmt]
    locate = locator(base)
    if fmt == "aftereffects":
        guide = p.renders[-1] if p.renders and Path(p.renders[-1]).is_file() else None
        text = aftereffects.write(flat, locate, guide=_guide(guide, base))
    else:
        text = writer(flat, locate)
    return stem(p) + suffix, text


def _guide(path: str | None, base: str | None) -> str | None:
    if not path:
        return None
    return locator(base)(Media(path=path, name=Path(path).name, duration=0, width=0, height=0, fps=0, has_audio=False)) if base else path


def _names_unique(flat: Flat) -> None:
    """Two clips with one file name would overwrite each other in media/: number the later ones."""
    seen: dict[str, int] = {}
    for m in [*flat.media, *([flat.music] if flat.music else [])]:
        n = seen.get(m.name.lower(), 0)
        seen[m.name.lower()] = n + 1
        if n:
            stem_, dot, ext = m.name.rpartition(".")
            m.name = f"{stem_}_{n + 1}{dot}{ext}" if dot else f"{m.name}_{n + 1}"


def bundle(p: EditProject, base: str | None = None, with_media: bool = True) -> bytes:
    """A zip holding every format, the media they use and a note on how to open each."""
    flat = flatten(p)
    _names_unique(flat)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fmt in FORMATS:
            name, text = export(p, fmt, base, flat)
            z.writestr(name, text)
        if with_media:
            for m in [*flat.media, *([flat.music] if flat.music else [])]:
                z.write(m.path, "media/" + m.name, compress_type=zipfile.ZIP_STORED)
            if p.renders and Path(p.renders[-1]).is_file():
                z.write(p.renders[-1], "media/" + Path(p.renders[-1]).name, compress_type=zipfile.ZIP_STORED)
        if flat.texts:
            z.writestr("texts.srt", capcut.srt(flat))
        z.writestr("HOW_TO_OPEN.txt", how_to(stem(p), base))
    return buf.getvalue()


def capcut_kit(p: EditProject, work: Path) -> bytes:
    render = p.renders[-1] if p.renders else None
    return capcut.kit(flatten(p), work, render)


def how_to(name: str, base: str | None) -> str:
    where = (f"The files point at {base}/media/, so unzip this folder to {base} and they open without relinking."
             if base else "The files point at the media on the computer that exported them. On another computer, "
                          "your editor will ask for the missing media: point it at the media/ folder in this zip.")
    return f"""Edit exported by Video Editor Agent
===================================

media/    the clips, the music, and the agent's finished render (to compare against)

{where}

After Effects        File > Scripts > Run Script File... > {name}_aftereffects.jsx
                     Builds a comp with every shot, its speed, the moves as keyframes, flashes, texts and music.
                     It finds media/ next to the script by itself.
Premiere Pro         File > Import > {name}_premiere_resolve.xml   (Premiere 26+ also reads {name}.otio)
DaVinci Resolve      File > Import > Timeline > {name}_premiere_resolve.xml (or {name}.fcpxml / {name}.otio)
Final Cut Pro        File > Import > XML > {name}.fcpxml
Kdenlive             File > Import > OpenTimelineIO > {name}.otio
CapCut               CapCut imports no timeline file: export the "CapCut kit" from the editor instead
Anything else        {name}.edl (cuts and speeds only); texts.srt has the on-screen text as captions

What carries over: every cut, each shot's footage, in-point and speed, the framing, the music, text, flashes.
The After Effects script also rebuilds punches, pushes, shakes and whips as keyframes.
What doesn't: the colour grade, beat glows, motion smear and the shaped transitions (wheel portal, corner
panel, cut-out) are made by the agent's renderer; they are in its render in media/ and marked on the timeline.
"""
