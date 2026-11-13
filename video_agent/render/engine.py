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
from ..director import CONTENT_MATCH, EditPlan, Shot
from ..media import color_stats, ffmpeg, frame_at, iter_frames, probe
from ..analysis.camera import estimate_curves, register_curve
from .compositor import apply_camera, is_static, relative_curve
from .grade import FILTER as LUT_FILTER, SWS_FLAGS, fitted_lut, is_monochrome, transfer_lut
from .rife import interpolate, rife_binary

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
    replay_camera: bool = True  # replay the reference's per-frame zoom/rotation/pan/shake


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
            _render_shot(shot, blueprint, opts, fps, n_frames, part, tmp)
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


BT709 = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
LOSSLESS = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", 0, "-pix_fmt", "yuv420p", *BT709]
# Untagged/SD-matrix footage is converted to BT.709 values (matrix only) so every later stage,
# the final BT.709 tag and every decoder agree on what the colours are.
REGISTER_MATCH = 0.9  # content match above which shots are registered frame-by-frame against the reference
TO_BT709 = "colorspace=all=bt709:ispace=bt470bg:iprimaries=bt709:itrc=bt709:irange={range}:range=tv"


def _render_shot(shot: Shot, bp: Blueprint, opts: RenderOptions, fps: int, n_frames: int, dst: Path, tmp: Path) -> None:
    """Speed pieces (FFmpeg or RIFE) -> colour + effects -> camera-motion compositor."""
    seg = shot.segment
    W, H = bp.output.width, bp.output.height
    src_w, src_h = shot.clip.probe.display_size
    framing = [_crop_filter(src_w, src_h, W, H, shot.center), f"scale={W}:{H}:flags=lanczos", "setsar=1"]
    src_range = "pc" if shot.clip.probe.color_range == "pc" else "tv"
    if shot.clip.probe.color_space != "bt709":
        framing.append(TO_BT709.format(range=src_range))
    elif src_range == "pc":
        framing.append("scale=in_range=pc:out_range=tv")

    pieces = []
    for j, (offset, src_len, speed, n_out) in enumerate(_plan_pieces(seg, shot.stretch, fps, n_frames)):
        piece = tmp / f"{dst.stem}_p{j}.mp4"
        _render_piece(shot, shot.src_start + offset, src_len, speed, n_out, framing, fps, opts, piece, tmp)
        pieces.append(piece)
    if len(pieces) == 1:
        base = pieces[0]
    else:
        base = tmp / f"{dst.stem}_base.mp4"
        lst = tmp / f"{dst.stem}_pieces.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in pieces))
        ffmpeg(["-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", base])

    target = _target_color(shot, bp)
    stats = color_stats([f for _, f in iter_frames(base, sample_fps=4, width=320)]) or shot.stats
    lut = _shot_lut(shot, bp, base, stats, target, opts, fps, tmp / f"{dst.stem}.cube")
    post = []
    if lut:
        post.append(LUT_FILTER.format(path=lut))
    else:
        eq = _color_eq(stats, target, opts.color_strength)
        post.append("eq=" + ":".join(f"{k}={v}" for k, v in eq.items()))
        mixer = _tint_mixer(stats, target, opts.color_strength)
        if mixer:
            post.append(mixer)
    if seg.transition_in == "flash":
        # Flash transition: start white and fade into the shot, over the measured length.
        frames = seg.flash_frames or 4
        post.append(f"fade=t=in:st=0:d={frames / fps * min(opts.effect_intensity, 1.5):.4f}:color=white")
    if "flash" in seg.effects:
        t = _mid_beat(seg, bp)
        post.append(f"eq=brightness='0.55*{opts.effect_intensity}*max(0,1-(t-{t:.3f})/0.12)*gte(t,{t:.3f})':eval=frame")
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
    post += ["tpad=stop_mode=clone:stop_duration=5", f"fps={fps}", "scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p"]

    camera, absolute = None, False
    reference = Path(bp.source.path) if bp.source.path else None
    if opts.replay_camera and shot.content_match >= REGISTER_MATCH and reference and reference.exists():
        # Same footage as the reference: register frame against frame — the editor's exact transform.
        camera = register_curve(base, reference, seg.start, n_frames, fps)
        absolute = camera is not None
    if camera is None and opts.replay_camera and not is_static(seg.camera):
        own = estimate_curves(base, [(0.0, n_frames / fps)], fps)[0]
        camera = relative_curve(seg.camera, own, n_frames, fps, W, H)
    graded = tmp / f"{dst.stem}_graded.mp4" if camera else dst
    ffmpeg([*SWS_FLAGS, "-i", base, "-vf", ",".join(post), "-frames:v", n_frames, "-an", *LOSSLESS, "-r", fps, graded])
    if camera:
        apply_camera(graded, dst, camera, W, H, fps, n_frames, bp.motion_blur, LOSSLESS, absolute=absolute)


def _shot_lut(shot: Shot, bp: Blueprint, base: Path, stats: dict, target: ColorProfile, opts: RenderOptions, fps: int, path: Path) -> Path | None:
    seg = shot.segment
    ref = Path(bp.source.path) if bp.source.path else None
    if shot.content_match >= CONTENT_MATCH and ref and ref.exists():
        skip = 0.15 if seg.transition_in == "flash" else 0.0
        pairs = []
        for frac in (0.3, 0.55, 0.8):
            t = skip + (seg.duration - skip) * frac
            a, b = frame_at(base, t, width=270), frame_at(ref, seg.start + t, width=270)
            if a is not None and b is not None and a.shape == b.shape:
                pairs.append((a, b))
        lut = fitted_lut(pairs, path)
        if lut:
            return lut
    return transfer_lut(stats, target, opts.color_strength if shot.content_match < CONTENT_MATCH else 1.0, path)


def _plan_pieces(seg, stretch: float, fps: int, n_frames: int) -> list[tuple[float, float, float, int]]:
    """(source offset, source seconds, speed, output frames) for each speed piece of the segment."""
    pts = sorted(seg.speed, key=lambda p: p.at)
    out, offset = [], 0.0
    for i, p in enumerate(pts):
        nxt = pts[i + 1].at if i + 1 < len(pts) else 1.0
        n_out = round(nxt * n_frames) - round(p.at * n_frames)
        if n_out <= 0:
            continue
        speed = p.speed / stretch
        src_len = n_out / fps * speed
        out.append((offset, src_len, speed, n_out))
        offset += src_len
    return out


def _render_piece(shot: Shot, start: float, src_len: float, speed: float, n_out: int, framing: list[str], fps: int, opts: RenderOptions, dst: Path, tmp: Path) -> None:
    src = ["-ss", f"{start:.4f}", "-t", f"{src_len + 0.25:.4f}", "-i", shot.clip.path]
    if speed < 0.95 and opts.interpolate_slowmo and rife_binary():
        try:
            _rife_piece(src, framing, n_out, fps, dst, tmp)
            return
        except Exception as e:  # GPU/driver trouble: fall back to FFmpeg's optical flow
            log.warning("RIFE failed, using minterpolate: %s", e)
    chain = list(framing)
    if speed < 0.95 and opts.interpolate_slowmo:
        chain.append(f"minterpolate=fps={round(fps / speed)}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    chain += [f"setpts=(PTS-STARTPTS)/{speed:.5f}", f"fps={fps}", "tpad=stop_mode=clone:stop_duration=3"]
    ffmpeg([*SWS_FLAGS, *src, "-vf", ",".join(chain), "-frames:v", n_out, "-an", *LOSSLESS, "-r", fps, dst])


def _rife_piece(src: list, framing: list[str], n_out: int, fps: int, dst: Path, tmp: Path) -> None:
    frames_in, frames_out = tmp / f"{dst.stem}_rin", tmp / f"{dst.stem}_rout"
    frames_in.mkdir()
    ffmpeg([*src, "-vf", ",".join(framing), "-fps_mode", "passthrough", frames_in / "%08d.png"])
    if len(list(frames_in.glob("*.png"))) < 2:
        raise RuntimeError("not enough source frames for interpolation")
    interpolate(frames_in, frames_out, n_out)
    ffmpeg([*SWS_FLAGS, "-framerate", fps, "-i", frames_out / "%08d.png", "-vf", "tpad=stop_mode=clone:stop_duration=3,format=gbrp16le,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
            "-frames:v", n_out, *LOSSLESS, dst])


def _target_color(shot: Shot, bp: Blueprint) -> ColorProfile:
    """The shot's own grade when the footage is the reference's own or the look is B&W; otherwise mostly the shot's look."""
    own = shot.segment.color
    if own is None:
        return bp.color
    if shot.content_match >= CONTENT_MATCH or is_monochrome(own):
        return own
    g = bp.color
    mix = lambda a, b: 0.75 * a + 0.25 * b  # noqa: E731  (the shot's own look is a deliberate edit choice)
    return ColorProfile(
        luma_mean=mix(own.luma_mean, g.luma_mean),
        luma_std=mix(own.luma_std, g.luma_std),
        saturation=mix(own.saturation, g.saturation),
        rgb_mean=tuple(mix(a, b) for a, b in zip(own.rgb_mean, g.rgb_mean)),
        lab_mean=tuple(mix(a, b) for a, b in zip(own.lab_mean, g.lab_mean)) if own.lab_mean and g.lab_mean else None,
        lab_std=tuple(mix(a, b) for a, b in zip(own.lab_std, g.lab_std)) if own.lab_std and g.lab_std else None,
        lut_path=g.lut_path,
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
    args += [*SWS_FLAGS, "-i", video]
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
        "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
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
