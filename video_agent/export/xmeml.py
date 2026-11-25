"""Final Cut Pro 7 XML (xmeml v4): imported by Premiere Pro (File > Import) and DaVinci Resolve
(File > Import > Timeline), and by most editors that read "Premiere XML".

One video track of clipitems (a speed ramp is several clipitems, each with a Time Remap speed), the framing as
Basic Motion scale and centre, text as Premiere's Outline Text generator, flashes as white colour mattes, and
the music on an audio track. Times are frames at the edit's rate.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import quote
from xml.sax.saxutils import escape

from .timeline import Flat, Media, cover_offset, cover_scale


def pathurl(path: str) -> str:
    p = path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":  # C:/...
        return "file://localhost/" + quote(p, safe="/:")
    return "file://localhost" + quote(p if p.startswith("/") else "/" + p, safe="/")


def _rate(fps: int) -> str:
    return f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"


def write(flat: Flat, locate: Callable[[Media], str] = lambda m: m.path) -> str:
    fps, W, H = flat.fps, flat.width, flat.height
    ids = {m.path: f"file-{i + 1}" for i, m in enumerate(flat.media)}
    seen: set[str] = set()

    def file_el(m: Media, audio_only: bool = False) -> str:
        fid = ids.get(m.path, "file-music")
        if fid in seen:
            return f'<file id="{fid}"/>'
        seen.add(fid)
        video = "" if audio_only else (f"<video><samplecharacteristics>{_rate(fps)}<width>{m.width}</width>"
                                       f"<height>{m.height}</height></samplecharacteristics></video>")
        audio = "<audio><channelcount>2</channelcount></audio>" if m.has_audio or audio_only else ""
        return (f'<file id="{fid}"><name>{escape(m.name)}</name><pathurl>{escape(pathurl(locate(m)))}</pathurl>'
                f"{_rate(fps)}<duration>{round(m.duration * fps)}</duration><media>{video}{audio}</media></file>")

    clips = []
    for k, p in enumerate(flat.pieces):
        m = p.media
        s_in = round(p.src_in * fps)
        s_out = s_in + max(1, round(p.src_dur * fps))
        scale = cover_scale(m, W, H) * 100
        ox, oy = cover_offset(m, W, H, p.center)
        effects = [_motion(scale, ox / W * 2, oy / H * 2)]  # Basic Motion centre is in half-frames from the middle
        if abs(p.speed - 1.0) > 1e-3:
            effects.append(_time_remap(p.speed))
        clips.append(
            f'<clipitem id="clip-{k + 1}"><name>{escape(m.name)}</name><enabled>TRUE</enabled>'
            f"<duration>{round(m.duration * fps)}</duration>{_rate(fps)}"
            f"<start>{p.start}</start><end>{p.start + p.frames}</end><in>{s_in}</in><out>{s_out}</out>"
            f"{file_el(m)}{''.join(effects)}</clipitem>"
        )
    titles = [_title(t, fps, H, n) for n, t in enumerate(flat.texts)]
    mattes = [_flash(at, frames, fps, n) for n, (at, kind, frames) in enumerate(flat.transitions) if kind == "flash"]

    audio = ""
    if flat.music:
        m = flat.music
        audio = (f'<track><clipitem id="music-1"><name>{escape(m.name)}</name><enabled>TRUE</enabled>'
                 f"<duration>{round(m.duration * fps)}</duration>{_rate(fps)}<start>0</start><end>{flat.frames}</end>"
                 f"<in>0</in><out>{flat.frames}</out>{file_el(m, audio_only=True)}"
                 "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem></track>")

    tracks = [f"<track>{''.join(clips)}</track>"]
    if mattes:
        tracks.append(f"<track>{''.join(mattes)}</track>")
    if titles:
        tracks.append(f"<track>{''.join(titles)}</track>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="sequence-1">'
        f"<name>{escape(flat.name)}</name><duration>{flat.frames}</duration>{_rate(fps)}"
        "<media><video><format><samplecharacteristics>"
        f"{_rate(fps)}<width>{W}</width><height>{H}</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>"
        f"{''.join(tracks)}</video><audio>{audio}</audio></media></sequence></xmeml>\n"
    )


def _param(pid: str, name: str, value: str) -> str:
    return f"<parameter><parameterid>{pid}</parameterid><name>{name}</name><value>{value}</value></parameter>"


def _motion(scale: float, cx: float, cy: float) -> str:
    return ("<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>"
            f"{_param('scale', 'Scale', f'{scale:.3f}')}"
            f"<parameter><parameterid>center</parameterid><name>Center</name><value><horiz>{cx:.5f}</horiz><vert>{cy:.5f}</vert></value></parameter>"
            "</effect></filter>")


def _time_remap(speed: float) -> str:
    return ("<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype>"
            f"{_param('variablespeed', 'variablespeed', '0')}{_param('speed', 'speed', f'{speed * 100:.3f}')}"
            f"{_param('reverse', 'reverse', 'FALSE')}{_param('frameblending', 'frameblending', 'FALSE')}"
            "</effect></filter>")


def _generator(cid: str, name: str, start: int, end: int, fps: int, effect: str) -> str:
    return (f'<generatoritem id="{cid}"><name>{escape(name)}</name><enabled>TRUE</enabled><duration>{end - start}</duration>'
            f"{_rate(fps)}<start>{start}</start><end>{end}</end><in>0</in><out>{end - start}</out>{effect}</generatoritem>")


def _title(t, fps: int, height: int, n: int) -> str:
    start, end = round(t.start * fps), round(t.end * fps)
    y = {"top": -0.6, "center": 0.0, "bottom": 0.6}[t.position]
    rgb = t.color.lstrip("#")
    r, g, b = (int(rgb[i : i + 2], 16) for i in (0, 2, 4)) if len(rgb) == 6 else (255, 255, 255)
    effect = ("<effect><name>Outline Text</name><effectid>Outline Text</effectid><effectcategory>Text</effectcategory>"
              "<effecttype>generator</effecttype><mediatype>video</mediatype>"
              f"{_param('str', 'Text', escape(t.text))}{_param('fontsize', 'Size', str(t.size))}"
              f"<parameter><parameterid>origin</parameterid><name>Origin</name><value><horiz>0</horiz><vert>{y}</vert></value></parameter>"
              f"<parameter><parameterid>textcolor</parameterid><name>Text Color</name><value><alpha>255</alpha><red>{r}</red>"
              f"<green>{g}</green><blue>{b}</blue></value></parameter></effect>")
    return _generator(f"title-{n + 1}", t.text[:40], start, end, fps, effect)


def _flash(at: int, frames: int, fps: int, n: int) -> str:
    effect = ("<effect><name>Color</name><effectid>Color</effectid><effectcategory>Matte</effectcategory>"
              "<effecttype>generator</effecttype><mediatype>video</mediatype>"
              "<parameter><parameterid>fillcolor</parameterid><name>Color</name><value><alpha>255</alpha><red>255</red>"
              "<green>255</green><blue>255</blue></value></parameter></effect>"
              # fade the white out over the flash
              "<filter><effect><name>Opacity</name><effectid>opacity</effectid><effectcategory>motion</effectcategory>"
              "<effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>opacity</parameterid>"
              f"<name>opacity</name><value>100</value><keyframe><when>0</when><value>100</value></keyframe>"
              f"<keyframe><when>{frames}</when><value>0</value></keyframe></parameter></effect></filter>")
    return _generator(f"flash-{n + 1}", "Flash", at, at + frames, fps, effect)

