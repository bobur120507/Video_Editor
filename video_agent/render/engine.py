"""Standard tier render engine (FFmpeg).

Pipeline: each planned shot is rendered to an intermediate 9:16 clip (crop around subject, speed,
colour match, effects), the intermediates are concatenated, then a final pass adds text + music
and encodes to Instagram's native spec.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import Blueprint, ColorProfile, TextOverlay
from ..config import get_settings
from ..director import CONTENT_MATCH, EditPlan, Shot
from ..media import color_stats, ffmpeg, frame_at, iter_frames, probe
from .. import vision
from ..analysis.camera import estimate_curves, register_curve
from ..analysis.visual import track_frames
from .compositor import apply_camera, framed, is_static, relative_curve
from . import transitions
from .motion import (PUNCH_ATTACK, VELOCITY_PUNCH_ATTACK, VELOCITY_WHIP_EASE, WHIP_EASE, WHIP_FRAMES, drift_directions, reframe,
                     synthesize)
from . import velocity as vel
from .grade import FILTER as LUT_FILTER, MAX_CHROMA_SLOPE, SWS_FLAGS, fitted_lut, is_monochrome, transfer_lut
from .rife import interpolate, rife_binary
from .upscale import NEEDED as UPSCALE_NEEDED, esrgan_binary, upscale_frames

log = logging.getLogger(__name__)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
]
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024
# Beat-synced moves start one frame early: the eye needs a frame to register motion, so this lands on
# the beat. Chosen by the user in a blind -1 / 0 / +1 frame test (2026-11-15).
SYNC_OFFSET = -1


@dataclass
class RenderOptions:
    music_path: str | None = None  # overrides the blueprint's extracted track
    font_path: str | None = None
    color_strength: float = 0.8  # 0 = keep footage colours, 1 = fully match the reference look
    # Velocity edits match the reference look fully: at 0.8 the McLaren edit kept a grey cast (colour
    # miss 1.42 vs 1.03 at 1.0, modelled, 2026-11-17); your choice for matching.
    velocity_color_strength: float = 1.0
    # Every shot gets the edit's overall tint (colour cast); brightness, contrast and colourfulness
    # still follow each shot. The reference's per-shot tint comes from what is in its shots (a blue
    # car, a sky): copied onto other footage it tinted shots blue, then warm (McLaren v8, your
    # review). Tint jumps 6.7 -> 1.6, colour miss 0.80 -> 1.49 (stills, 2026-11-17). Velocity edits only:
    # a per-shot tint can also be a deliberate grade (benchmark push_rotate: colour 79.5% -> 48.3% with it).
    one_tint: bool = True
    # With one tint: greys go to the reference's greys (its grade) plus this share of its average colour,
    # which is mostly its content (grass, a blue sky). Any share of it turned grey roads and the sky
    # cyan: 1.0 in v9, 0.5 in v10 (your reviews, 2026-11-17).
    tint_share: float = 0.0
    # Velocity edits boost colourfulness at most this much: at 3x the warm sidewalks turned yellow
    # and colour noise grew; 1.6x still doubles the rainy footage's colour (5.4 -> 11.3; TikTok 13.3).
    velocity_chroma_cap: float = 1.6
    # Shot matching: each shot's overall colour balance moves this share of the way to the edit's
    # average, so a sky-heavy street shot is not bluer than the wall shots around it (the footage
    # itself differs: blue-green -1.8 vs -5 to -11; your v10 review, 3-5 s looked blue).
    shot_balance: float = 0.5
    # Keep the footage's greys neutral instead of moving them to the reference's greys. The TikTok's
    # greys carry its slight teal grade (green-red -1.8); on footage that is mostly grey road, sidewalk
    # and overcast sky it turned whole street shots cyan (3-4 s, your v12 review: +5.6 green, +3.3 blue).
    keep_greys: bool = True
    transitions: bool = True  # panel / wheel portal / car cut-out transitions from the template (render/transitions.py)
    # Colour-only noise reduction after the grade (FFmpeg hqdn3d: luma 0, chroma spatial/temporal): the
    # colour boost turns rain texture into coloured speckle (v15: 1.16 vs the TikTok's 0.85). 0:4:0:3
    # cut it by 12% with sharpness unchanged (5175 -> 5162). "" turns it off.
    chroma_denoise: str = "0:4:0:3"
    effect_intensity: float = 1.0
    interpolate_slowmo: bool = True
    replay_camera: bool = True  # replay the reference's per-frame zoom/rotation/pan/shake
    upscale: bool = True  # Real-ESRGAN for footage smaller than the output frame
    # Motion blur: "auto" follows the reference (blurred where it was blurred), "fast_only" blurs only
    # whips and punches (crisp shakes and calm frames), "off" never blurs.
    blur: str = "auto"
    sync_offset_frames: int = SYNC_OFFSET  # move every synced edit move this many frames
    velocity: bool = True  # velocity-style references: speed ramps, real smear, never-stopping camera


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

    # On the data disk, not /tmp: lossless 1080p intermediates and PNG frame sequences outgrow a RAM /tmp.
    with tempfile.TemporaryDirectory(prefix="render_", dir=get_settings().dir("tmp")) as tmp:
        tmp = Path(tmp)
        parts = []
        frame_cursor = 0
        edit_colour = _footage_colour(plan, spec.width, spec.height) if opts.one_tint and blueprint.style == "velocity" else None
        for i, shot in enumerate(plan.shots):
            end_frame = round(shot.segment.end * fps)
            n_frames = max(1, end_frame - frame_cursor)
            frame_cursor = end_frame
            part = tmp / f"shot_{i:03d}.mp4"
            work = tmp / f"shot_{i:03d}_work"
            work.mkdir()
            _render_shot(shot, blueprint, opts, fps, n_frames, part, work, edit_colour)
            shutil.rmtree(work, ignore_errors=True)  # frames and intermediates of a finished shot
            parts.append(part)

        for i in range(1, len(parts)) if opts.transitions else ():
            seg = plan.shots[i].segment
            if seg.transition_in in transitions.FRAMES:
                done = transitions.apply(seg.transition_in, parts[i - 1], parts[i], spec.width, spec.height, fps, LOSSLESS,
                                         seg.transition_frames, seg.transition_from)
                log.info("transition at %.2fs: %s%s", seg.start, seg.transition_in, "" if done else " (skipped, plain cut)")

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


def _render_shot(shot: Shot, bp: Blueprint, opts: RenderOptions, fps: int, n_frames: int, dst: Path, tmp: Path,
                 edit_colour: dict | None = None) -> None:
    """Speed pieces (FFmpeg or RIFE) -> colour + effects -> camera-motion compositor."""
    seg = shot.segment
    W, H = bp.output.width, bp.output.height
    src_w, src_h = shot.clip.probe.display_size
    crop = _crop_filter(src_w, src_h, W, H, shot.center)
    colour = []
    src_range = "pc" if shot.clip.probe.color_range == "pc" else "tv"
    if shot.clip.probe.color_space != "bt709":
        colour.append(TO_BT709.format(range=src_range))
    elif src_range == "pc":
        colour.append("scale=in_range=pc:out_range=tv")
    framing = [crop, f"scale={W}:{H}:flags=lanczos", "setsar=1", *colour]
    enlarge = H / _crop_size(src_w, src_h, W, H)[1]
    upscale = (crop, colour, enlarge, W, H) if opts.upscale and enlarge > UPSCALE_NEEDED and esrgan_binary() else None

    velocity = _velocity_mode(shot, bp, opts)
    pieces = []
    if velocity:
        base = tmp / f"{dst.stem}_vel.mp4"
        try:
            _velocity_base(shot, crop, colour, enlarge if upscale else 1.0, W, H, fps, n_frames, opts, base, tmp)
            pieces = [base]
        except Exception as e:  # never lose the edit over the speed ramp: plain pieces instead
            log.warning("velocity retime failed, using plain speed pieces: %s", e)
            velocity = False
    if not pieces:
        for j, (offset, src_len, speed, n_out) in enumerate(_plan_pieces(seg, shot.stretch, fps, n_frames)):
            piece = tmp / f"{dst.stem}_p{j}.mp4"
            _render_piece(shot, shot.src_start + offset, src_len, speed, n_out, framing, fps, opts, piece, tmp, upscale)
            pieces.append(piece)
    if len(pieces) == 1:
        base = pieces[0]
    else:
        base = tmp / f"{dst.stem}_base.mp4"
        lst = tmp / f"{dst.stem}_pieces.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in pieces))
        ffmpeg(["-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", base])

    camera, absolute = None, False
    reference = Path(bp.source.path) if bp.source.path else None
    if opts.replay_camera and shot.content_match >= REGISTER_MATCH and reference and reference.exists():
        # Same footage as the reference: register frame against frame — the editor's exact transform.
        camera = register_curve(base, reference, seg.start, n_frames, fps)
        absolute = camera is not None
    mirror_ok, centre, fast = None, True, None
    same_footage = shot.content_match >= CONTENT_MATCH
    if camera is None and opts.replay_camera and (seg.events or velocity) and not same_footage:
        # Different footage: re-create the editor's moves (whips, punches, shakes on the hits)
        # rather than the reference camera's literal path. Same footage keeps the literal path below.
        foot_subject = None
        if vision.available() and seg.subject is not None:
            frames = [f for _, f in iter_frames(base, sample_fps=6, width=480)][:: max(1, n_frames // 18)]
            foot_subject = vision.main_subject(frames[:4])
        wave = vel.wave(seg.velocity, n_frames) if velocity else None
        direction = drift_directions(bp.segments).get(round(seg.start, 3), 0.0)

        ease = VELOCITY_WHIP_EASE if velocity else WHIP_EASE
        attack = VELOCITY_PUNCH_ATTACK if velocity else PUNCH_ATTACK

        def build(amp: float, gain: float, whip_gain: float = 1.0):
            c, m, f = synthesize(seg.events, n_frames, fps, seg.start, opts.sync_offset_frames, wave, direction, amp, gain,
                                 whip_gain, ease, attack)
            return reframe(c, seg.subject, foot_subject, W, H), m, f

        amp, gain, whip_gain = 0.0, 1.0, 1.0
        if velocity:
            # Move like the reference: drift and move strength set so the edit's typical and fast
            # motion match the reference's (not a guess at "how much shake"). Whip frames are left
            # out of that (the reference's are smeared past what optical flow can measure), then
            # whips are set to move whip_ratio x the shot's motion: speeding up out of the drift.
            whips = build(0.0, 1.0)[1][:n_frames]
            calm = ~whips
            amp = vel.DEFAULT_AMP
            footage = track_frames(base, fps).flow
            target, ratio = vel.motion_targets(seg, bp.segments, n_frames, calm)
            got = target
            if calm.sum() >= vel.MIN_CALM:
                amp, gain, got, got_ratio = vel.calibrate(lambda a, g: build(a, g)[0], footage, target, ratio, n_frames, W, H, calm)
                log.info("shot %.2fs velocity: drift %.2f, moves x%.2f -> motion %.2f (target %.2f), fast/typical %.1f (target %.1f)",
                         seg.start, amp, gain, got, target, got_ratio, ratio)
            if whips.any():
                whip_target = vel.whip_ratio(bp.segments, WHIP_FRAMES) * got
                whip_gain, whip_got = vel.calibrate_whips(lambda g: build(amp, gain, g)[0], footage, whip_target, whips,
                                                          n_frames, W, H)
                log.info("shot %.2fs velocity: whips x%.2f -> motion %.2f (target %.2f)", seg.start, whip_gain, whip_got, whip_target)
        camera, mirror_ok, fast = build(amp, gain, whip_gain)
        centre = False
    elif camera is None and opts.replay_camera and not is_static(seg.camera):
        own = estimate_curves(base, [(0.0, n_frames / fps)], fps)[0]
        camera = relative_curve(seg.camera, own, n_frames, fps, W, H)
    target = _target_color(shot, bp, opts.one_tint and velocity)
    # Grade what ends up on screen: the camera's reframe and push change what fills the frame (a
    # 1.67x push into a black car darkened shots by 7-14 L, McLaren v5, 2026-11-16).
    samples = [(round(t * fps), f) for t, f in iter_frames(base, sample_fps=4, width=320)]
    seen = framed(samples, camera, W, H, fps, n_frames, absolute=absolute, mirror_ok=mirror_ok, centre=centre) if camera else [
        f for _, f in samples]
    stats = color_stats(seen) or shot.stats
    strength = opts.velocity_color_strength if velocity else opts.color_strength
    tint, chroma, balance = None, None, (0.0, 0.0)
    if opts.one_tint and velocity:
        # One colour transform for the whole edit: greys and colourfulness are measured over all the
        # footage, so no shot drifts bluer or warmer than the next; only lightness follows the shot.
        tint, chroma = opts.tint_share, opts.velocity_chroma_cap
        if edit_colour and edit_colour.get("neutral_ab"):
            balance = tuple(opts.shot_balance * (np.array(edit_colour["lab_mean"][1:]) - np.array(stats["lab_mean"][1:])))
            stats = {**stats, "neutral_ab": edit_colour["neutral_ab"], "neutral_chroma_quantiles": edit_colour["neutral_chroma_quantiles"]}
            if opts.keep_greys:
                target = target.model_copy(update={"neutral_ab": edit_colour["neutral_ab"]})
    lut = _shot_lut(shot, bp, base, stats, target, strength, fps, tmp / f"{dst.stem}.cube", tint, chroma, balance)
    post = []
    if lut:
        post.append(LUT_FILTER.format(path=lut))
        if opts.chroma_denoise:
            post.append(f"hqdn3d={opts.chroma_denoise}")
    else:
        eq = _color_eq(stats, target, strength)
        post.append("eq=" + ":".join(f"{k}={v}" for k, v in eq.items()))
        mixer = _tint_mixer(stats, target, strength)
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

    graded = tmp / f"{dst.stem}_graded.mp4" if camera else dst
    ffmpeg([*SWS_FLAGS, "-i", base, "-vf", ",".join(post), "-frames:v", n_frames, "-an", *LOSSLESS, "-r", fps, graded])
    if camera:
        shutter = _shutter(seg, bp, opts, n_frames, fast, velocity)
        if absolute and np.any(shutter) and _already_blurred(base, reference, seg.start, n_frames, fps):
            shutter = 0.0  # the footage already carries the editor's blur (it is an edited video, not raw)
        apply_camera(graded, dst, camera, W, H, fps, n_frames, shutter, LOSSLESS, absolute=absolute, mirror_ok=mirror_ok, centre=centre)


BLURRED = 0.5  # reference frames this much softer than the shot's sharpest carry motion blur
FAST_SHUTTER = 0.5  # 180 degrees: blur for fast moves when the reference gives no measure


def _shutter(seg, bp: Blueprint, opts: RenderOptions, n_frames: int, fast: np.ndarray | None,
             velocity: bool = False) -> float | np.ndarray:
    """Motion blur per output frame.

    auto: follow the reference: blur exactly where its frames are blurred (so its crisp shakes stay
    crisp); for old blueprints without per-frame blur, only fast moves. fast_only: whips and punch
    attacks only, even when the reference has no blur. off: none.

    Velocity shots blur only fast camera moves, at most 180 degrees: their smear comes from the
    footage's own sped-up motion (the speed ramp). The reference is soft because its footage moves
    fast; copying its 360-degree blur onto ours smeared 94% of frames, 38% without (McLaren test
    edit, 2026-11-15).
    """
    if opts.blur == "off":
        return 0.0
    if velocity and fast is not None:
        strength = min(bp.motion_blur, FAST_SHUTTER) if opts.blur == "auto" else FAST_SHUTTER
        return np.where(fast[:n_frames], strength, 0.0)
    fast_only = opts.blur == "fast_only" or not seg.blur
    if fast_only:
        if fast is None:  # literal camera replay: no move list, keep the reference's shutter
            return bp.motion_blur if opts.blur == "auto" else 0.0
        strength = max(bp.motion_blur, FAST_SHUTTER) if opts.blur == "fast_only" else bp.motion_blur
        return np.where(fast[:n_frames], strength, 0.0)
    if not bp.motion_blur:
        return 0.0
    soft = np.array([seg.blur[min(i, len(seg.blur) - 1)] for i in range(n_frames)])
    return np.where(soft > BLURRED, bp.motion_blur, 0.0)


def _already_blurred(footage: Path, reference: Path, ref_start: float, n_frames: int, fps: int) -> bool:
    """True when the footage is no sharper than the reference at the same moments."""
    ratios = []
    for i in np.linspace(0, n_frames - 1, min(8, n_frames)).round().astype(int):
        a, b = frame_at(footage, i / fps, width=360), frame_at(reference, ref_start + i / fps, width=360)
        if a is None or b is None:
            continue
        sa = cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var()
        sb = cv2.Laplacian(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var()
        ratios.append(sa / (sb + 1e-6))
    return bool(ratios) and float(np.median(ratios)) < 1.5


def _shot_lut(shot: Shot, bp: Blueprint, base: Path, stats: dict, target: ColorProfile, strength: float, fps: int, path: Path,
              neutral_tint: float | None = None, max_chroma: float | None = None, balance: tuple[float, float] = (0.0, 0.0)
              ) -> Path | None:
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
    return transfer_lut(stats, target, strength if shot.content_match < CONTENT_MATCH else 1.0, path, neutral_tint,
                        max_chroma or MAX_CHROMA_SLOPE, balance)


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


def _render_piece(shot: Shot, start: float, src_len: float, speed: float, n_out: int, framing: list[str], fps: int, opts: RenderOptions,
                  dst: Path, tmp: Path, upscale: tuple | None = None) -> None:
    # Seek half a frame early: start times are rounded (1.167 s for frame 35 = 1.1667 s), and
    # seeking exactly there would skip to the next frame and put the whole shot one frame early.
    src_fps = shot.clip.probe.fps or fps
    half = 0.5 / src_fps
    src = ["-ss", f"{max(0.0, start - half):.4f}", "-t", f"{src_len + 0.25 + half:.4f}", "-i", shot.clip.path]
    if upscale:
        try:
            src, framing = _esrgan_source(src, *upscale, src_fps, dst, tmp)
        except Exception as e:  # GPU/driver trouble: plain Lanczos resize
            log.warning("Real-ESRGAN failed, using a plain resize: %s", e)
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


def _velocity_mode(shot: Shot, bp: Blueprint, opts: RenderOptions) -> bool:
    """Velocity rendering is for velocity-style references on new footage (the reference's own
    footage already carries its speed ramps)."""
    return (opts.velocity and bp.style == "velocity" and bool(shot.segment.velocity)
            and shot.content_match < CONTENT_MATCH and not shot.segment.is_slowmo)


def _velocity_base(shot: Shot, crop: str, colour: list[str], enlarge: float, W: int, H: int, fps: int, n_frames: int,
                   opts: RenderOptions, dst: Path, tmp: Path) -> None:
    """The shot's footage speed-ramped along the reference's motion wave, with real smear.

    Source frames are cropped at source resolution; only the frames that land sharp (slow, near
    the hits) get Real-ESRGAN, because smeared frames hide the detail anyway; then every output
    frame blends the source frames its shutter sweeps across (velocity.retime).
    """
    seg = shot.segment
    src_fps = shot.clip.probe.fps or fps
    span = sum(src_len for _, src_len, _, _ in _plan_pieces(seg, shot.stretch, fps, n_frames))
    speed = vel.speed_curve(seg.velocity, n_frames, span * fps / n_frames)
    p = vel.time_map(speed, fps)
    half = 0.5 / src_fps
    frames_in = tmp / f"{dst.stem}_vin"
    frames_in.mkdir()
    ffmpeg(["-ss", f"{max(0.0, shot.src_start - half):.4f}", "-t", f"{span + 0.25 + half:.4f}", "-i", shot.clip.path,
            "-vf", ",".join([crop, *colour]), "-fps_mode", "passthrough", frames_in / "%08d.png"])
    paths = sorted(frames_in.glob("*.png"))
    if len(paths) < 2:
        raise RuntimeError("not enough source frames for the speed ramp")
    shutter = 0.0 if opts.blur == "off" else vel.TIME_SHUTTER

    if enlarge > UPSCALE_NEEDED:
        need = vel.sharp_frames(p, speed, fps, src_fps, len(paths))
        sharp_in, sharp_up = tmp / f"{dst.stem}_vsharp", tmp / f"{dst.stem}_vsharp_up"
        sharp_in.mkdir()
        for k in np.flatnonzero(need):
            shutil.copy(paths[k], sharp_in / paths[k].name)
        try:
            upscale_frames(sharp_in, sharp_up, enlarge)
            paths = [sharp_up / q.name if need[k] else q for k, q in enumerate(paths)]
            log.info("velocity shot %.2fs: AI-upscaled %d of %d source frames (the sharp ones)", seg.start, int(need.sum()), len(need))
        except Exception as e:  # GPU/driver trouble: plain resize
            log.warning("Real-ESRGAN failed, using a plain resize: %s", e)

    load = vel.load_frames(paths, W, H)
    if speed.min() < 0.75 and opts.interpolate_slowmo and rife_binary():
        # Slow frames would repeat or ghost: AI in-betweens double the source frame rate first.
        try:
            full, doubled = tmp / f"{dst.stem}_vfull", tmp / f"{dst.stem}_vrife"
            full.mkdir()
            for k in range(len(paths)):
                cv2.imwrite(str(full / f"{k + 1:08d}.png"), load(k))
            interpolate(full, doubled, 2 * len(paths) - 1)
            paths = sorted(doubled.glob("*.png"))
            load, src_fps = vel.load_frames(paths, W, H), src_fps * (len(paths) - 1) / (len(list(full.glob("*.png"))) - 1)
        except Exception as e:
            log.warning("RIFE failed, blending neighbour frames instead: %s", e)

    writer = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         "-vf", "format=gbrp16le,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", *map(str, LOSSLESS), str(dst)],
        stdin=subprocess.PIPE,
    )
    try:
        for frame in vel.retime(load, len(paths), p, speed, fps, src_fps, shutter):
            writer.stdin.write(frame.tobytes())
    finally:
        writer.stdin.close()
        if writer.wait() != 0:
            raise RuntimeError("ffmpeg failed while writing the speed-ramped shot")


def _esrgan_source(src: list, crop: str, colour: list[str], enlarge: float, W: int, H: int, src_fps: float, dst: Path, tmp: Path) -> tuple[list, list[str]]:
    """Crop at source resolution, upscale with Real-ESRGAN, and return the frames as the new source.

    The returned framing only resizes to the output frame (one Lanczos step down from the AI
    upscale) and converts RGB to BT.709 limited range, like the RIFE path.
    """
    frames_in, frames_up = tmp / f"{dst.stem}_ein", tmp / f"{dst.stem}_eup"
    frames_in.mkdir()
    ffmpeg([*src, "-vf", ",".join([crop, *colour]), "-fps_mode", "passthrough", frames_in / "%08d.png"])
    upscale_frames(frames_in, frames_up, enlarge)
    return (
        ["-framerate", f"{src_fps:.5f}", "-i", frames_up / "%08d.png"],
        [f"scale={W}:{H}:flags=lanczos:out_color_matrix=bt709:out_range=tv", "setsar=1", "format=yuv420p"],
    )


def _rife_piece(src: list, framing: list[str], n_out: int, fps: int, dst: Path, tmp: Path) -> None:
    frames_in, frames_out = tmp / f"{dst.stem}_rin", tmp / f"{dst.stem}_rout"
    frames_in.mkdir()
    ffmpeg([*src, "-vf", ",".join(framing), "-fps_mode", "passthrough", frames_in / "%08d.png"])
    if len(list(frames_in.glob("*.png"))) < 2:
        raise RuntimeError("not enough source frames for interpolation")
    interpolate(frames_in, frames_out, n_out)
    ffmpeg([*SWS_FLAGS, "-framerate", fps, "-i", frames_out / "%08d.png", "-vf", "tpad=stop_mode=clone:stop_duration=3,format=gbrp16le,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
            "-frames:v", n_out, *LOSSLESS, dst])


def _target_color(shot: Shot, bp: Blueprint, one_tint: bool = False) -> ColorProfile:
    """The shot's own grade when the footage is the reference's own or the look is B&W; otherwise mostly
    the shot's look, with the edit's overall tint when `one_tint`."""
    own = shot.segment.color
    if own is None:
        return bp.color
    if shot.content_match >= CONTENT_MATCH or is_monochrome(own):
        return own
    g = bp.color
    mix = lambda a, b: 0.75 * a + 0.25 * b  # noqa: E731  (the shot's own look is a deliberate edit choice)
    luma = mix(own.luma_mean, g.luma_mean)
    lab_mean = tuple(mix(a, b) for a, b in zip(own.lab_mean, g.lab_mean)) if own.lab_mean and g.lab_mean else None
    rgb_mean = tuple(mix(a, b) for a, b in zip(own.rgb_mean, g.rgb_mean))
    neutral = tuple(mix(a, b) for a, b in zip(own.neutral_ab, g.neutral_ab)) if own.neutral_ab and g.neutral_ab else None
    neutral_q = tuple(mix(a, b) for a, b in zip(own.neutral_chroma_quantiles, g.neutral_chroma_quantiles)) \
        if own.neutral_chroma_quantiles and g.neutral_chroma_quantiles else None
    if one_tint:
        lab_mean = (lab_mean[0], *g.lab_mean[1:]) if lab_mean else None
        neutral, neutral_q = g.neutral_ab, g.neutral_chroma_quantiles
        rgb_mean = tuple(c * luma / max(g.luma_mean, 1e-6) for c in g.rgb_mean)
    return ColorProfile(
        luma_mean=luma,
        luma_std=mix(own.luma_std, g.luma_std),
        saturation=mix(own.saturation, g.saturation),
        rgb_mean=rgb_mean,
        lab_mean=lab_mean,
        lab_std=tuple(mix(a, b) for a, b in zip(own.lab_std, g.lab_std)) if own.lab_std and g.lab_std else None,
        l_quantiles=tuple(mix(a, b) for a, b in zip(own.l_quantiles, g.l_quantiles)) if own.l_quantiles and g.l_quantiles else None,
        chroma_quantiles=tuple(mix(a, b) for a, b in zip(own.chroma_quantiles, g.chroma_quantiles))
        if own.chroma_quantiles and g.chroma_quantiles else None,
        neutral_ab=neutral,
        neutral_chroma_quantiles=neutral_q,
        lut_path=g.lut_path,
    )


def _crop_size(src_w: int, src_h: int, W: int, H: int) -> tuple[int, int]:
    """Size of the source area that fills the output frame (before resizing)."""
    target = W / H
    if abs(src_w / src_h - target) < 0.01:
        return src_w, src_h
    if src_w / src_h > target:
        return int(src_h * target) // 2 * 2, src_h
    return src_w, int(src_w / target) // 2 * 2


def _footage_colour(plan: EditPlan, W: int, H: int) -> dict:
    """Colour statistics over every shot's footage window (cropped to the output shape): the edit-wide
    neutral point and colourfulness, so one colour transform serves every shot."""
    frames = []
    for shot in plan.shots:
        for _, f in iter_frames(shot.clip.path, sample_fps=4, width=320, start=shot.src_start, end=shot.src_start + shot.src_len):
            h, w = f.shape[:2]
            if w / h > W / H:
                cw = int(h * W / H)
                x = min(max(0, int(shot.center[0] * w - cw / 2)), w - cw)
                f = f[:, x:x + cw]
            else:
                ch = int(w * H / W)
                y = min(max(0, int(shot.center[1] * h - ch / 2)), h - ch)
                f = f[y:y + ch]
            frames.append(f)
    return color_stats(frames)


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
