"""Standard tier render engine (FFmpeg).

Pipeline: each planned shot is rendered to an intermediate 9:16 clip (crop around subject, speed,
colour match, effects), the intermediates are concatenated, then a final pass adds text + music
and encodes to Instagram's native spec.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from ..blueprint import Blueprint, ColorProfile, TextOverlay
from ..config import get_settings
from ..director import EditPlan, Shot
from ..media import ffmpeg, probe

log = logging.getLogger(__name__)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
]
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024


@dataclass
class RenderOptions:
    music_path: str | None = None  # overrides the blueprint's extracted track
    font_path: str | None = None
    color_strength: float = 0.8  # 0 = keep footage colours, 1 = fully match the reference look
    effect_intensity: float = 1.0
    interpolate_slowmo: bool = True


@dataclass
class RenderResult:
    master: Path
    preview: Path  # <= Telegram upload limit (same file as master when it fits)
    cover: Path
    grid: Path  # 1:1 crop for the profile grid
    duration: float


def render(blueprint: Blueprint, plan: EditPlan, out_path: str | Path, opts: RenderOptions | None = None) -> RenderResult:
    opts = opts or RenderOptions()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spec = blueprint.output
    fps = spec.fps or get_settings().output_fps

    with tempfile.TemporaryDirectory(prefix="render_") as tmp:
        tmp = Path(tmp)
        parts = []
        frame_cursor = 0
        for i, shot in enumerate(plan.shots):
            end_frame = round(shot.segment.end * fps)
            n_frames = max(1, end_frame - frame_cursor)
            frame_cursor = end_frame
            part = tmp / f"shot_{i:03d}.mp4"
            _render_shot(shot, blueprint, opts, fps, n_frames, part)
            parts.append(part)

        concat_list = tmp / "list.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in parts))
        joined = tmp / "joined.mp4"
        ffmpeg(["-f", "concat", "-safe", 0, "-i", concat_list, "-c", "copy", joined])

        total = frame_cursor / fps
        _final_pass(joined, blueprint, opts, total, fps, out_path, tmp)

    preview = _telegram_preview(out_path)
    cover, grid = _cover_and_grid(out_path, blueprint)
    return RenderResult(master=out_path, preview=preview, cover=cover, grid=grid, duration=total)


# ---------------------------------------------------------------- shots


def _render_shot(shot: Shot, bp: Blueprint, opts: RenderOptions, fps: int, n_frames: int, dst: Path) -> None:
    seg = shot.segment
    W, H = bp.output.width, bp.output.height
    src_w, src_h = shot.clip.probe.display_size

    chain = [_crop_filter(src_w, src_h, W, H, shot.center), f"scale={W}:{H}:flags=lanczos", "setsar=1"]

    pieces = sorted(seg.speed, key=lambda p: p.at)
    speed_graph, label = _speed_graph(pieces, seg.duration, shot.stretch, fps, opts.interpolate_slowmo)

    post = []
    eq = _color_eq(shot.stats, bp.color, opts.color_strength)
    flash_times = []
    if seg.transition_in == "flash":
        flash_times.append(0.0)
    if "flash" in seg.effects:
        flash_times.append(_mid_beat(seg, bp))
    if flash_times:
        flash = "+".join(f"0.55*{opts.effect_intensity}*max(0,1-(t-{t:.3f})/0.12)*gte(t,{t:.3f})" for t in flash_times)
        eq["brightness"] = f"'{eq['brightness']}+{flash}'"
        eq["eval"] = "frame"
    post.append("eq=" + ":".join(f"{k}={v}" for k, v in eq.items()))
    mixer = _tint_mixer(shot.stats, bp.color, opts.color_strength)
    if mixer:
        post.append(mixer)
    if bp.color.lut_path and Path(bp.color.lut_path).exists():
        post.append(f"lut3d=file='{bp.color.lut_path}'")
    if "zoom_punch" in seg.effects:
        z = 0.22 * opts.effect_intensity
        post.append(
            f"zoompan=z='1+{z:.3f}*pow(max(0,1-on/9),2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={W}x{H}:fps={fps}"
        )
    if "shake" in seg.effects:
        a = int(18 * opts.effect_intensity)
        m = a + 4
        cw, ch = W - 2 * m, int((W - 2 * m) * H / W) // 2 * 2
        post.append(f"crop={cw}:{ch}:x='{m}+{a}*sin(n*1.7)':y='{(H - ch) // 2}+{a}*cos(n*2.3)'")
        post.append(f"scale={W}:{H}")
    post += ["tpad=stop_mode=clone:stop_duration=5", f"fps={fps}", "format=yuv420p"]

    graph = f"[0:v]{','.join(chain)}[base];{speed_graph};[{label}]{','.join(post)}[out]"
    ffmpeg(
        [
            "-ss", f"{shot.src_start:.3f}", "-t", f"{shot.src_len + 0.1:.3f}", "-i", shot.clip.path,
            "-filter_complex", graph, "-map", "[out]", "-frames:v", n_frames, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", 15, "-r", fps, dst,
        ]
    )


def _crop_filter(src_w: int, src_h: int, W: int, H: int, center: tuple[float, float]) -> str:
    target = W / H
    if abs(src_w / src_h - target) < 0.01:
        return "null"
    if src_w / src_h > target:
        cw, ch = int(src_h * target) // 2 * 2, src_h
        x = min(max(0, int(center[0] * src_w - cw / 2)), src_w - cw)
        return f"crop={cw}:{ch}:{x}:0"
    cw, ch = src_w, int(src_w / target) // 2 * 2
    y = min(max(0, int(center[1] * src_h - ch / 2)), src_h - ch)
    return f"crop={cw}:{ch}:0:{y}"


def _speed_graph(pieces, seg_dur: float, stretch: float, fps: int, interpolate: bool) -> tuple[str, str]:
    """Split the base stream into speed pieces, retime each, and concatenate. Returns (graph, out label)."""
    src_t = 0.0
    parts = []
    for i, p in enumerate(pieces):
        nxt = pieces[i + 1].at if i + 1 < len(pieces) else 1.0
        out_len = (nxt - p.at) * seg_dur
        speed = p.speed / stretch
        src_len = out_len * speed
        f = [f"trim=start={src_t:.4f}:duration={src_len:.4f}", "setpts=PTS-STARTPTS"]
        if speed < 0.95 and interpolate:
            # Optical-flow interpolation so slow motion stays smooth instead of stuttering.
            f.append(f"minterpolate=fps={round(fps / speed)}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
        f.append(f"setpts=PTS/{speed:.5f}")
        parts.append(f)
        src_t += src_len
    if len(parts) == 1:
        return f"[base]{','.join(parts[0])}[sp]", "sp"
    split = f"[base]split={len(parts)}" + "".join(f"[s{i}]" for i in range(len(parts)))
    chains = [f"[s{i}]{','.join(f)}[p{i}]" for i, f in enumerate(parts)]
    concat = "".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[sp]"
    return ";".join([split, *chains, concat]), "sp"


def _color_eq(src: dict, target: ColorProfile, strength: float) -> dict:
    """Match the clip's brightness/contrast/saturation to the blueprint's look."""
    if not src:
        return {"brightness": "0", "contrast": "1", "saturation": "1"}
    lerp = lambda a, b: a + (b - a) * strength  # noqa: E731
    contrast = min(1.8, max(0.6, lerp(1.0, target.luma_std / max(src["luma_std"], 0.02))))
    predicted = (src["luma_mean"] - 0.5) * contrast + 0.5
    brightness = min(0.3, max(-0.3, lerp(0.0, target.luma_mean - predicted)))
    saturation = min(2.5, max(0.3, lerp(1.0, target.saturation / max(src["saturation"], 0.02))))
    return {"brightness": f"{brightness:.4f}", "contrast": f"{contrast:.4f}", "saturation": f"{saturation:.4f}"}


def _tint_mixer(src: dict, target: ColorProfile, strength: float) -> str | None:
    if not src:
        return None
    s_rgb, t_rgb = src["rgb_mean"], target.rgb_mean
    s_avg, t_avg = sum(s_rgb) / 3 or 1e-3, sum(t_rgb) / 3 or 1e-3
    gains = []
    for s, t in zip(s_rgb, t_rgb):
        g = (t / t_avg) / max(s / s_avg, 1e-3)
        gains.append(min(1.25, max(0.8, 1 + (g - 1) * strength)))
    if all(abs(g - 1) < 0.01 for g in gains):
        return None
    return f"colorchannelmixer=rr={gains[0]:.4f}:gg={gains[1]:.4f}:bb={gains[2]:.4f}"


def _mid_beat(seg, bp: Blueprint) -> float:
    inside = [b - seg.start for b in bp.audio.beats if seg.start + 0.1 < b < seg.end - 0.05]
    return inside[len(inside) // 2] if inside else seg.duration / 2


# ---------------------------------------------------------------- final pass


def _final_pass(video: Path, bp: Blueprint, opts: RenderOptions, total: float, fps: int, out: Path, tmp: Path) -> None:
    spec = bp.output
    font = opts.font_path if opts.font_path and Path(opts.font_path).exists() else _default_font()
    vf = [_drawtext(t, i, font, tmp, spec.height) for i, t in enumerate(bp.texts) if t.text.strip()]
    vaapi = get_settings().video_encoder.endswith("_vaapi")
    vf.append("format=nv12,hwupload" if vaapi else "format=yuv420p")

    music = opts.music_path or bp.audio.track_path
    args = ["-vaapi_device", "/dev/dri/renderD128"] if vaapi else []
    args += ["-i", video]
    if music and Path(music).exists():
        args += ["-i", music]
        af = f"atrim=0:{total:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0, total - 0.4):.3f}:d=0.4"
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", f"anullsrc=r={spec.audio_rate}:cl=stereo"]
        af = "anull"

    args += [
        "-filter_complex", f"[0:v]{','.join(vf)}[v];[1:a]{af},aresample={spec.audio_rate}[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
        *_video_codec_args(spec),
        "-r", fps, "-c:a", "aac", "-b:a", spec.audio_bitrate, "-ar", spec.audio_rate, "-ac", 2,
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart", out,
    ]
    ffmpeg(args)


def _video_codec_args(spec) -> list:
    encoder = get_settings().video_encoder
    rate = ["-b:v", spec.video_bitrate, "-maxrate", spec.max_bitrate, "-bufsize", "32M"]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-pix_fmt", "yuv420p", "-g", 60, *rate]
    if encoder.endswith("_vaapi"):
        return ["-c:v", encoder, "-profile:v", "high", "-g", 60, *rate]
    return ["-c:v", encoder, "-pix_fmt", "yuv420p", "-g", 60, *rate]


def _drawtext(t: TextOverlay, i: int, font: str, tmp: Path, height: int) -> str:
    # One drawtext per line: FFmpeg 8.0 renders "\n" in textfile as a box glyph.
    lines = textwrap.wrap(t.text, 18) or [t.text]
    files = []
    for j, line in enumerate(lines):
        f = tmp / f"text_{i}_{j}.txt"
        f.write_text(line)
        files.append(f)
    top = {"top": "h*0.14", "center": "h*0.5", "bottom": "h*0.78"}[t.position]
    alpha = f"'min(1,max(0,(t-{t.start:.3f})/0.25))'" if t.style == "fade" else "1"
    color = t.color.lstrip("#")

    def draw(size: int, start: float, end: float) -> str:
        lh = int(size * 1.2)
        block = lh * len(lines)
        anchor = {"top": "0", "center": f"{block / 2:.0f}", "bottom": f"{block}"}[t.position]
        return ",".join(
            f"drawtext=fontfile='{font}':textfile='{f}':fontsize={size}:fontcolor=0x{color}:alpha={alpha}"
            f":borderw=5:bordercolor=black@0.7:x=(w-text_w)/2:y={top}-{anchor}+{j * lh}"
            f":enable='between(t,{start:.3f},{end:.3f})'"
            for j, f in enumerate(files)
        )

    if t.style != "pop":
        return draw(t.size, t.start, t.end)
    # "Pop" in: oversized for a few frames, then settle. Stepped sizes, because an animated
    # fontsize expression segfaults FFmpeg 8.0's drawtext.
    steps = [(1.4, 0.04), (1.2, 0.04), (1.08, 0.04)]
    out, start = [], t.start
    for scale, dur in steps:
        out.append(draw(int(t.size * scale), start, start + dur - 0.001))
        start += dur
    out.append(draw(t.size, start, t.end))
    return ",".join(out)


def _default_font() -> str:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    raise FileNotFoundError("No bold font found; install fonts-dejavu or set a brand font.")


# ---------------------------------------------------------------- deliverables


def _telegram_preview(master: Path) -> Path:
    if master.stat().st_size <= TELEGRAM_UPLOAD_LIMIT:
        return master
    preview = master.with_name(master.stem + "_preview.mp4")
    duration = probe(master).duration or 1
    kbps = int(TELEGRAM_UPLOAD_LIMIT * 8 / 1000 / duration * 0.85) - 128
    ffmpeg(
        [
            "-i", master, "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{max(kbps, 800)}k",
            "-maxrate", f"{max(kbps, 800)}k", "-bufsize", f"{2 * max(kbps, 800)}k", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", preview,
        ]
    )
    return preview


def _cover_and_grid(master: Path, bp: Blueprint) -> tuple[Path, Path]:
    cover = master.with_name(master.stem + "_cover.jpg")
    grid = master.with_name(master.stem + "_grid.jpg")
    t = min(1.0, max(0.0, bp.duration / 3))
    ffmpeg(["-ss", f"{t:.2f}", "-i", master, "-frames:v", 1, "-q:v", 2, cover])
    ffmpeg(["-i", cover, "-vf", "crop=iw:iw:0:(ih-iw)/2", "-q:v", 2, grid])
    return cover, grid


def check_dependencies() -> list[str]:
    missing = [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
    return missing
