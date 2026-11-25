"""CMX 3600 EDL: the oldest and most widely read cut list (Premiere, Resolve, Avid, Final Cut via converters).

Cuts and speeds only: one event per piece, speed changes as M2 lines, the music on an audio event. Reel names
are the clip names (8-character reels are an old tape limit most editors no longer enforce); the
`* FROM CLIP NAME` comments let an editor relink by file name.
"""

from __future__ import annotations

from .timeline import Flat, timecode


def write(flat: Flat) -> str:
    fps = flat.fps
    lines = [f"TITLE: {flat.name}", "FCM: NON-DROP FRAME", ""]
    n = 0
    for p in flat.pieces:
        n += 1
        src_fps = fps  # source timecode is counted at the edit's rate, as editors do when they conform
        s_in = round(p.src_in * src_fps)
        s_out = s_in + max(1, round(p.src_dur * src_fps))
        reel = _reel(p.media.name)
        lines.append(f"{n:03d}  {reel:<8} V     C        {timecode(s_in, fps)} {timecode(s_out, fps)} "
                     f"{timecode(p.start, fps)} {timecode(p.start + p.frames, fps)}")
        if abs(p.speed - 1.0) > 1e-3:
            lines.append(f"M2   {reel:<8}       {p.speed * fps:05.1f}    {timecode(s_in, fps)}")
        lines.append(f"* FROM CLIP NAME: {p.media.name}")
        lines.append("")
    if flat.music:
        n += 1
        reel = _reel(flat.music.name)
        lines.append(f"{n:03d}  {reel:<8} A     C        {timecode(0, fps)} {timecode(flat.frames, fps)} "
                     f"{timecode(0, fps)} {timecode(flat.frames, fps)}")
        lines.append(f"* FROM CLIP NAME: {flat.music.name}")
        lines.append("")
    return "\n".join(lines)


def _reel(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return ("".join(c for c in stem if c.isalnum()) or "AX")[:8].upper()
