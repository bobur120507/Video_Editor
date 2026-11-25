"""FCPXML 1.10 (flat .fcpxml file): Final Cut Pro 10.6+ (File > Import > XML) and DaVinci Resolve 18+.

1.10 is the newest version both read (Resolve 19.0 refused 1.11). The video is the spine: one asset-clip per
piece, filled to the vertical frame (adjust-conform fill, then nudged to the shot's framing), with a timeMap
for its speed. The music and the texts (Basic Title) are connected clips. Flashes and shaped transitions are
markers. Times are rational seconds on a 1/(fps*100) grid, as FCPXML wants.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

from .timeline import Flat, Media, cover_offset
from .xmeml import pathurl

BASIC_TITLE = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"


def _t(seconds: float, fps: int) -> str:
    """Rational seconds, e.g. 31/30s."""
    f = Fraction(round(seconds * fps * 100), fps * 100)
    return f"{f.numerator}/{f.denominator}s" if f.denominator != 1 else f"{f.numerator}s"


def write(flat: Flat, locate: Callable[[Media], str] = lambda m: m.path) -> str:
    fps, W, H = flat.fps, flat.width, flat.height
    res = [f'<format id="r1" name="FFVideoFormat{W}x{H}p{fps}" frameDuration="1/{fps}s" width="{W}" height="{H}"/>',
           f'<effect id="r2" name="Basic Title" uid="{BASIC_TITLE}"/>']
    ids: dict[str, str] = {}
    for m in [*flat.media, *([flat.music] if flat.music else [])]:
        if m.path in ids:
            continue
        aid, fid = f"r{len(res) + 1}", f"r{len(res) + 2}"
        ids[m.path] = aid
        video = m is not flat.music
        if video:
            res.append(f'<format id="{fid}" frameDuration="1/{round(m.fps) or fps}s" width="{m.width}" height="{m.height}"/>')
        res.append(f'<asset id="{aid}" name={quoteattr(m.name)} start="0s" duration="{_t(m.duration, fps)}" '
                   f'hasVideo="{int(video)}" hasAudio="{int(m.has_audio or not video)}"'
                   + (f' format="{fid}"' if video else "") + ' audioSources="1" audioChannels="2">'
                   f'<media-rep kind="original-media" src={quoteattr(pathurl(locate(m)).replace("file://localhost", "file://"))}/></asset>')

    total = flat.frames / fps
    spine = []
    for k, p in enumerate(flat.pieces):
        offset, dur = p.start / fps, p.frames / fps
        start = p.src_in / p.speed  # the clip's own (retimed) time runs 1:1 with the timeline
        # The DTD fixes the order: timeMap, adjust-conform, adjust-transform, connected clips, markers.
        children = []
        if abs(p.speed - 1.0) > 1e-3:
            media_end = p.media.duration
            children.append(f'<timeMap><timept time="0s" value="0s" interp="linear"/>'
                            f'<timept time="{_t(media_end / p.speed, fps)}" value="{_t(media_end, fps)}" interp="linear"/></timeMap>')
        children.append('<adjust-conform type="fill"/>')
        ox, oy = cover_offset(p.media, W, H, p.center)
        if abs(ox) > 0.5 or abs(oy) > 0.5:  # position is in % of the frame height, y up
            children.append(f'<adjust-transform position="{ox / H * 100:.4f} {-oy / H * 100:.4f}" scale="1 1"/>')
        if k == 0 and flat.music:
            children.append(f'<asset-clip ref="{ids[flat.music.path]}" lane="-1" offset="{_t(start, fps)}" '
                            f'name={quoteattr(flat.music.name)} start="0s" duration="{_t(total, fps)}" audioRole="music"/>')
        for n, t in enumerate(flat.texts):
            if offset - 1e-6 <= t.start < offset + dur - 1e-6:
                y = {"top": 30, "center": 0, "bottom": -30}[t.position]
                children.append(
                    f'<title ref="r2" lane="1" offset="{_t(start + t.start - offset, fps)}" name={quoteattr(t.text[:40])} '
                    f'duration="{_t(t.end - t.start, fps)}" start="0s"><param name="Position" key="9999/999166631/999166633/1/100/101" '
                    f'value="0 {y * H / 100:.0f}"/><text><text-style ref="ts{n}">{escape(t.text)}</text-style></text>'
                    f'<text-style-def id="ts{n}"><text-style font="Helvetica" fontSize="{t.size}" fontColor="{_rgba(t.color)}" '
                    f'bold="1" alignment="center"/></text-style-def></title>')
        for at, kind, frames in flat.transitions:
            if p.start <= at < p.start + p.frames:
                children.append(f'<marker start="{_t(start + (at - p.start) / fps, fps)}" duration="1/{fps}s" value={quoteattr(kind + " transition")}/>')
        spine.append(f'<asset-clip ref="{ids[p.media.path]}" offset="{_t(offset, fps)}" name={quoteattr(p.media.name)} '
                     f'start="{_t(start, fps)}" duration="{_t(dur, fps)}" tcFormat="NDF">{"".join(children)}</asset-clip>')

    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10"><resources>'
            + "".join(res) + '</resources><library><event name="Video Editor Agent">'
            f'<project name={quoteattr(flat.name)}><sequence format="r1" duration="{_t(total, fps)}" tcStart="0s" '
            f'tcFormat="NDF" audioLayout="stereo" audioRate="48k"><spine>{"".join(spine)}</spine></sequence></project>'
            "</event></library></fcpxml>\n")


def _rgba(color: str) -> str:
    c = color.lstrip("#")
    if len(c) != 6:
        return "1 1 1 1"
    return " ".join(f"{int(c[i : i + 2], 16) / 255:.3f}" for i in (0, 2, 4)) + " 1"
