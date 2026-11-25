"""CapCut kit. CapCut imports no timeline format: its help says projects from other editors can't be
imported, and the draft files community tools write are reverse-engineered and reportedly break on CapCut 10.
So the kit rebuilds the edit with what every CapCut (desktop, web, phone) does import: plain media.

shots/ holds one clip per shot, already cut, at its speed (ramps included) and framed to the edit's shape, exactly as long as
the shot, numbered in order. Select them all, drag them onto the timeline, and the cuts land where the
agent put them, on the music. music.m4a is the track trimmed to the edit; texts.srt has the on-screen text
with its timing; the agent's render is there to compare. The look and the moves (punches, shakes, whips,
glows) are left for CapCut's own effects, so the shots stay clean to edit.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..media import ffmpeg
from ..render.engine import _crop_filter
from .timeline import Flat

CRF = 17  # near-lossless: CapCut re-encodes on export


def shot_clip(flat: Flat, shot: int, out: Path) -> Path:
    """One shot as a clip: its pieces trimmed, sped and framed, joined, exactly its frame count long."""
    fps, W, H = flat.fps, flat.width, flat.height
    pieces = [p for p in flat.pieces if p.shot == shot]
    start, n = flat.shots[shot]
    args, chains = [], []
    for k, p in enumerate(pieces):
        m = p.media
        args += ["-ss", f"{p.src_in:.4f}", "-t", f"{p.src_dur + 2 / max(m.fps, 1):.4f}", "-i", m.path]
        crop = _crop_filter(m.width, m.height, W, H, p.center)
        chains.append(f"[{k}:v]setpts=(PTS-STARTPTS)/{p.speed:.6f},fps={fps},{crop},scale={W}:{H}:flags=lanczos,setsar=1,"
                      f"trim=end_frame={p.frames},setpts=PTS-STARTPTS[p{k}]")
    joined = "".join(f"[p{k}]" for k in range(len(pieces)))
    graph = ";".join(chains) + f";{joined}concat=n={len(pieces)}:v=1:a=0,trim=end_frame={n},format=yuv420p[v]"
    ffmpeg([*args, "-filter_complex", graph, "-map", "[v]", "-an", "-r", fps, "-c:v", "libx264", "-preset", "medium",
            "-crf", CRF, "-movflags", "+faststart", out])
    return out


def srt(flat: Flat) -> str:
    def ts(s: float) -> str:
        ms = round(s * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        sec, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    return "\n".join(f"{i + 1}\n{ts(t.start)} --> {ts(t.end)}\n{t.text}\n" for i, t in enumerate(flat.texts))


def kit(flat: Flat, work: Path, render: str | None = None) -> bytes:
    """The kit as a zip (clips are made in `work`, a scratch folder)."""
    work.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        width = max(2, len(str(len(flat.shots))))
        for i in range(len(flat.shots)):
            name = f"{i + 1:0{width}d}_shot.mp4"
            z.write(shot_clip(flat, i, work / name), f"shots/{name}")
        if flat.music:
            music = work / "music.m4a"
            total = flat.seconds
            ffmpeg(["-i", flat.music.path, "-vn", "-af", f"atrim=0:{total:.3f},afade=t=out:st={max(0, total - 0.4):.3f}:d=0.4",
                    "-c:a", "aac", "-b:a", "192k", music])
            z.write(music, "music.m4a")
        if flat.texts:
            z.writestr("texts.srt", srt(flat))
        if render and Path(render).is_file():
            z.write(render, "agent_render.mp4")
        z.writestr("HOW_TO_OPEN_IN_CAPCUT.txt", how_to(flat))
    return buf.getvalue()


def how_to(flat: Flat) -> str:
    w, h = flat.width, flat.height
    ratio = {(9, 16): "9:16", (1, 1): "1:1", (16, 9): "16:9", (4, 5): "4:5"}.get(_ratio(w, h), f"{w}:{h}")
    cuts = "\n".join(f"  shot {i + 1:>2}: {s / flat.fps:6.2f}s - {(s + n) / flat.fps:6.2f}s" for i, (s, n) in enumerate(flat.shots))
    return f"""Your edit as a CapCut kit
=========================

1. CapCut > New project, ratio {ratio} ({w}×{h}).
2. Import > select every file in shots/ (they are numbered in order) and drag them onto the timeline together.
   Each one is exactly as long as its shot, so the cuts land where the agent put them:
{cuts}
3. Drag music.m4a onto the audio track at 0:00. It is trimmed to the edit ({flat.seconds:.2f}s); the cuts sit on its beats.
4. Text: texts.srt has the on-screen text and when it shows. Import it as captions if your CapCut offers that,
   or add the lines with Text > Add text.
5. agent_render.mp4 is the agent's finished render with its grade and moves: use it to compare, or place it on
   a track above and switch it off.

The shots are clean (no grade, punches, shakes, whips or glows), so CapCut's own effects and filters can go on top.
"""


def _ratio(w: int, h: int) -> tuple[int, int]:
    from math import gcd

    g = gcd(w, h) or 1
    return w // g, h // g
