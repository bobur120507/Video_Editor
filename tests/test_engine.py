import asyncio
import subprocess

import pytest

from video_agent.analysis import clone_video
from video_agent.blueprint import Blueprint, Segment, SourceInfo, SpeedPoint, TextOverlay
from video_agent.director import plan_edit
from video_agent.media import probe
from video_agent.render import render
from video_agent.render.engine import _crop_filter, _plan_pieces
from video_agent.tweak import apply_adjustments, parse_feedback


def _bp(segments) -> Blueprint:
    return Blueprint(source=SourceInfo(duration=segments[-1].end, fps=30, width=1080, height=1920), segments=segments)


def test_source_seconds_with_speed_ramp():
    seg = Segment(start=0, end=2, speed=[SpeedPoint(at=0, speed=1.0), SpeedPoint(at=0.5, speed=0.5)])
    assert seg.source_seconds_needed() == pytest.approx(1.5)
    assert seg.is_slowmo


def test_crop_keeps_subject_inside_frame():
    assert _crop_filter(1920, 1080, 1080, 1920, (0.5, 0.5)) == "crop=606:1080:657:0"
    assert _crop_filter(1920, 1080, 1080, 1920, (1.0, 0.5)) == "crop=606:1080:1314:0"  # clamped to right edge
    assert _crop_filter(1080, 1920, 1080, 1920, (0.5, 0.5)) == "null"


def test_speed_ramp_pieces_cover_every_frame():
    seg = Segment(start=0, end=2, speed=[SpeedPoint(at=0, speed=1), SpeedPoint(at=0.5, speed=0.5)])
    pieces = _plan_pieces(seg, 1.0, 30, 60)
    assert [n for *_, n in pieces] == [30, 30]
    assert pieces[1][0] == pytest.approx(1.0) and pieces[1][1] == pytest.approx(0.5)


def test_keyword_feedback_and_adjustments():
    adj = asyncio.run(parse_feedback("make it darker with more zoom and faster cuts"))
    assert adj == {"brightness": -1, "zoom": 1, "cut_pace": 1}
    bp = _bp([Segment(start=0, end=2), Segment(start=2, end=4)])
    bp.texts = [TextOverlay(text="x", start=0, end=1)]
    out = apply_adjustments(bp, adj)
    assert out.color.luma_mean < bp.color.luma_mean
    assert len(out.segments) == 4
    assert "zoom_punch" in out.segments[0].effects


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("samples")
    ref, raw = d / "ref.mp4", d / "raw.mp4"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=540x960:r=30:d=1.5",
            "-f", "lavfi", "-i", "smptebars=s=540x960:r=30:d=1.5",
            "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':s=48000:d=3",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", ref,
        ],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=1280x720:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", raw],
        check=True,
    )
    return ref, raw


def test_clone_and_render_end_to_end(samples, tmp_path):
    ref, raw = samples
    bp = clone_video(ref)
    assert len(bp.segments) == 2
    assert abs(bp.segments[1].start - 1.5) < 0.1
    assert bp.audio.has_music and bp.audio.track_path

    bp.segments[0].effects = ["zoom_punch"]
    bp.texts = [TextOverlay(text="Hook line", start=0.1, end=1.4)]
    result = render(bp, plan_edit(bp, [raw]), tmp_path / "out.mp4")

    info = probe(result.master)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio
    assert info.duration == pytest.approx(bp.duration, abs=0.1)
    assert result.cover.exists() and result.grid.exists()


def test_relative_curve_cancels_footage_motion():
    from video_agent.blueprint import CameraCurve
    from video_agent.render.compositor import relative_curve

    drift = CameraCurve(fps=30, s=[1.0, 1.02, 1.04], r=[0.0, 0.01, 0.02], x=[0.0, 0.01, 0.02], y=[0.0, 0.0, 0.01])
    out = relative_curve(drift, drift, 3, 30, 1080, 1920)  # target motion == footage motion -> nothing to add
    assert out.s == pytest.approx([1.0] * 3, abs=1e-6)
    assert out.r == pytest.approx([0.0] * 3, abs=1e-6)
    assert out.x == pytest.approx([0.0] * 3, abs=1e-6) and out.y == pytest.approx([0.0] * 3, abs=1e-6)


def test_cover_zoom_hides_borders():
    import numpy as np

    from video_agent.render.compositor import _cover_zoom

    still = np.array([[1.0, 0.0, 0.0, 0.0]])
    assert _cover_zoom(still, 1080, 1920) == pytest.approx(1.0)
    panned = np.array([[1.0, 0.0, 0.05, 0.0]])  # 5% pan needs 10% extra width
    assert _cover_zoom(panned, 1080, 1920) == pytest.approx(1.1)


def test_compositor_identity_is_lossless(samples, tmp_path):
    from video_agent.blueprint import CameraCurve
    from video_agent.render.compositor import apply_camera
    from video_agent.render.engine import LOSSLESS

    _, raw = samples
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", raw, "-vf", "scale=1080:1920", "-frames:v", "10", *map(str, LOSSLESS), src], check=True)
    dst = tmp_path / "dst.mp4"
    apply_camera(src, dst, CameraCurve(fps=30, s=[1.0] * 10, r=[0.0] * 10, x=[0.0] * 10, y=[0.0] * 10), 1080, 1920, 30, 10, 0.0, LOSSLESS)
    yuv = lambda p: subprocess.run(["ffmpeg", "-loglevel", "error", "-i", p, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout  # noqa: E731
    assert yuv(src) == yuv(dst)


def test_transfer_lut_identity_when_stats_match(tmp_path):
    import numpy as np

    from video_agent.blueprint import ColorProfile
    from video_agent.render.grade import _grid, transfer_lut

    stats = {"lab_mean": (50.0, 5.0, -3.0), "lab_std": (20.0, 8.0, 9.0)}
    path = transfer_lut(stats, ColorProfile(lab_mean=stats["lab_mean"], lab_std=stats["lab_std"]), 1.0, tmp_path / "id.cube")
    values = np.loadtxt(path, skiprows=1)
    assert np.abs(values - _grid()).max() < 0.01


def test_black_and_white_look_fully_desaturates(tmp_path):
    import numpy as np

    from video_agent.blueprint import ColorProfile
    from video_agent.render.grade import _grid, is_monochrome, transfer_lut

    bw = ColorProfile(lab_mean=(55.0, 0.3, -0.5), lab_std=(22.0, 0.8, 0.9))
    assert is_monochrome(bw)
    colourful = {"lab_mean": (50.0, 12.0, 20.0), "lab_std": (20.0, 15.0, 18.0)}
    path = transfer_lut(colourful, bw, 0.8, tmp_path / "bw.cube")
    rgb = np.loadtxt(path, skiprows=1)
    mid = rgb[np.abs(_grid() - 0.5).max(axis=1) < 0.3]  # ignore extreme corners of the cube
    assert np.abs(mid - mid.mean(axis=1, keepdims=True)).max() < 0.06  # R≈G≈B: grey


def test_events_snap_punches_to_hits_and_cap_push():
    from video_agent.analysis.events import extract_events
    from video_agent.blueprint import CameraCurve

    n = 30
    s = [1.0] * n
    s[15] = 1.2  # one zoom spike inside the shot
    for i in range(16, n):
        s[i] = 1.2
    curve = CameraCurve(fps=30, s=s, r=[0.0] * n, x=[0.0] * n, y=[0.0] * n)
    seg = Segment(start=10.0, end=11.0, camera=curve)
    events = extract_events(seg, hits=[10.52])
    punches = [e for e in events if e.kind == "punch"]
    assert len(punches) == 1 and punches[0].t == pytest.approx(16 / 30, abs=1e-3)  # moved onto the hit's frame
    assert not extract_events(seg, hits=[12.0]) or all(e.kind != "punch" for e in extract_events(seg, hits=[12.0]))


def test_synthesized_whip_and_reframe_put_subject_in_place():
    import numpy as np

    from video_agent.blueprint import EditEvent, Subject
    from video_agent.render.motion import _matrix, reframe, synthesize
    from video_agent.vision import Box

    curve, whip, fast = synthesize([EditEvent(kind="whip_out", t=1.0, strength=1.0, dx=1.0)], 30, 30)
    assert whip[-4:].all() and not whip[:-4].any() and fast[-4:].all()
    assert curve.x[-1] == pytest.approx(0.25) and abs(curve.x[0]) < 1e-9  # smears out at the end only

    still, _, _ = synthesize([], 1, 30)
    out = reframe(still, Subject(cls="car", cx=0.4, cy=0.6, w=0.7, h=0.3), Box("car", 0.9, 0.4, 0.55, 0.6, 0.7), 1080, 1920)
    p = _matrix(out.s[0], out.r[0], out.x[0], out.y[0], 1080, 1920) @ np.array([540, 1200, 1])
    assert (p[0] / 1080, p[1] / 1920) == pytest.approx((0.4, 0.6), abs=1e-3)


@pytest.mark.skipif(not __import__("video_agent.vision", fromlist=["available"]).available(), reason="vision models not installed")
def test_vision_models_find_a_car_and_describe_it(samples):
    import numpy as np

    from video_agent import vision

    texts = vision.embed_texts(["a sports car on a road", "a bowl of soup"])
    assert texts.shape == (2, 512) and np.allclose(np.linalg.norm(texts, axis=1), 1, atol=1e-3)
    frame = np.full((640, 360, 3), 128, np.uint8)
    assert vision.detect(frame) == []  # nothing to find in a flat grey frame


def test_hits_are_timed_at_the_attack_and_typed(tmp_path):
    import numpy as np
    import soundfile as sf

    from video_agent.analysis.events import detect_hits

    sr = 22050
    y = np.zeros(sr * 4, np.float32)
    starts = [0.5, 1.25, 2.0, 2.75, 3.5]
    for k, t in enumerate(starts):
        n = np.arange(int(0.25 * sr))
        if k % 2 == 0:  # kick: low thump
            burst = np.sin(2 * np.pi * 60 * n / sr) * np.exp(-n / (0.06 * sr))
        else:  # snare/clap: noise burst
            burst = np.random.default_rng(k).normal(0, 0.5, len(n)) * np.exp(-n / (0.03 * sr))
        i = int(t * sr)
        y[i : i + len(n)] += burst.astype(np.float32)
    sf.write(tmp_path / "a.wav", y, sr)
    hits, kinds = detect_hits(tmp_path / "a.wav", keep=1.0)
    for t in starts:
        assert min(abs(h - t) for h in hits) < 0.02  # within ~half a frame of where the sound starts
    by_time = {round(min(starts, key=lambda s: abs(s - h)), 2): k for h, k in zip(hits, kinds)}
    assert by_time[0.5] == "kick" and by_time[1.25] == "snare"


def test_moves_land_on_the_hit_frame_and_ease():
    import numpy as np

    from video_agent.blueprint import EditEvent
    from video_agent.render.motion import synthesize

    fps = 30
    seg_start = 2.0
    punch = EditEvent(kind="punch", t=0.5, strength=0.2, zoom=1.0)
    curve, _, fast = synthesize([punch], 40, fps, seg_start)
    s = np.log(curve.s)
    hit = round((seg_start + 0.5) * fps) - round(seg_start * fps)
    assert s[hit - 1] == 0 and s[hit] > 0.1 and s[hit + 1] == pytest.approx(0.2)  # impact on the hit frame
    assert fast[hit] and not fast[hit + 3]
    assert s[hit + 3] < s[hit + 1] and s[-1] == pytest.approx(0, abs=1e-6)  # eased back to rest

    early, _, _ = synthesize([punch], 40, fps, seg_start, offset_frames=-1)
    assert np.log(early.s)[hit - 1] > 0.1  # one frame early when asked

    from video_agent.render.motion import VELOCITY_PUNCH_ATTACK

    flowing, _, _ = synthesize([punch], 40, fps, seg_start, punch_attack=VELOCITY_PUNCH_ATTACK)
    step = np.diff(np.log(flowing.s), prepend=0.0)
    assert int(np.argmax(step)) == hit  # velocity punches still land on the hit...
    assert step.max() < 0.65 * np.diff(s, prepend=0.0).max()  # ...but flow in over 3 frames (40% steps, not 65%)

    jolt = EditEvent(kind="shake", t=0.5, duration=0.25, strength=1.0)
    curve, _, _ = synthesize([jolt], 40, fps, seg_start)
    d = np.hypot(curve.x, curve.y)
    assert d[:hit].max() == 0 and d[hit] == d.max()  # biggest jolt exactly on the hit
    assert d[hit + int(0.25 * fps) + 2 :].max() < 0.15 * d[hit]  # and gone soon after: no constant wobble


def test_whip_out_continues_into_the_next_shot():
    from video_agent.analysis.events import link_whips
    from video_agent.blueprint import EditEvent

    a = Segment(start=0, end=1, events=[EditEvent(kind="whip_out", t=1.0, strength=0.8, dx=1.0, dy=0.0)])
    b = Segment(start=1, end=2)
    c = Segment(start=2, end=3, events=[EditEvent(kind="whip_out", t=1.0, strength=0.4, dx=0.0, dy=1.0)])
    d = Segment(start=3, end=4, events=[EditEvent(kind="whip_in", t=0.0, strength=1.0, dx=1.0, dy=0.0)])
    link_whips([a, b, c, d])
    carried = b.events[0]
    assert carried.kind == "whip_in" and carried.source == "carried" and carried.dx == 1.0 and carried.strength == pytest.approx(0.4)
    out, inn = c.events[0], d.events[0]
    assert (out.dx, out.dy) == (inn.dx, inn.dy) and out.strength == inn.strength == 1.0  # one shared direction and speed


def test_blur_follows_the_reference_or_only_fast_moves():
    import numpy as np

    from video_agent.blueprint import Blueprint, SourceInfo
    from video_agent.render.engine import RenderOptions, _shutter

    seg = Segment(start=0, end=0.2, blur=[0.0, 0.9, 0.9, 0.1, 0.0, 0.0])
    bp = Blueprint(source=SourceInfo(duration=1, fps=30, width=1080, height=1920), segments=[seg], motion_blur=1.0)
    fast = np.array([1, 0, 0, 0, 0, 1], bool)
    assert list(_shutter(seg, bp, RenderOptions(), 6, fast)) == [0, 1, 1, 0, 0, 0]  # blurred where the reference is
    assert list(_shutter(seg, bp, RenderOptions(blur="fast_only"), 6, fast)) == [1, 0, 0, 0, 0, 1]
    assert _shutter(seg, bp, RenderOptions(blur="off"), 6, fast) == 0.0
    bp.motion_blur = 0.0
    assert list(_shutter(seg, bp, RenderOptions(blur="fast_only"), 6, fast)) == [0.5, 0, 0, 0, 0, 0.5]  # no reference blur: 180° on whips
    bp.motion_blur = 1.0
    # Velocity shots: the smear comes from the speed ramp, the camera blurs only its fast moves (180° at most)
    assert list(_shutter(seg, bp, RenderOptions(), 6, fast, velocity=True)) == [0.5, 0, 0, 0, 0, 0.5]


def test_user_corrections_fix_the_detected_moves():
    from video_agent import labels
    from video_agent.blueprint import AudioInfo, Blueprint, CameraCurve, EditEvent, SourceInfo

    cam = CameraCurve(fps=30, s=[1.0] * 30, r=[0.0] * 30, x=[0.0] * 30, y=[0.0] * 30)
    segs = [
        Segment(start=0, end=1, camera=cam, events=[EditEvent(kind="punch", t=0.5, strength=0.2, zoom=1.0), EditEvent(kind="whip_out", t=1.0, strength=1, dx=1)]),
        Segment(start=1, end=2, camera=cam, events=[EditEvent(kind="whip_in", t=0.0, strength=1, dx=1), EditEvent(kind="shake", t=0.4, duration=0.2, strength=0.5)]),
    ]
    bp = Blueprint(source=SourceInfo(duration=2, fps=30, width=1080, height=1920), segments=segs, audio=AudioInfo(hits=[1.7]))
    assert [m.kind for m in labels.moves(bp)] == ["punch", "whip", "shake"]
    fixed, done = labels.apply_corrections(bp, "remove 2; 3 punch, add 1.65 shake")
    assert len(done) == 3
    kinds = [(m.kind, m.t) for m in labels.moves(fixed)]
    assert ("whip", 1.0) not in kinds and ("punch", 1.4) in kinds and ("shake", 1.7) in kinds  # 1.65 snapped to the hit
    assert all(e.source == "user" for s in fixed.segments for e in s.events if e.kind == "shake")
    assert labels.is_ok("All correct") and not labels.is_ok("remove 1")


def test_style_tells_velocity_edits_from_calm_ones():
    import numpy as np

    from video_agent.analysis.velocity import edit_style, segment_velocity

    rng = np.random.default_rng(0)
    moving = 2 + np.sin(np.arange(300) / 2.5) + rng.random(300) * 0.3  # never stops, rises and falls
    calm = np.where(np.arange(300) % 30 < 20, 0.01, 2.0)  # still between moves
    assert edit_style(moving, np.full(300, 0.13)) == "velocity"
    assert edit_style(moving, np.full(300, 0.55)) == "calm"  # moving but sharp: a calm edit with a moving camera
    assert edit_style(calm, np.full(300, 0.13)) == "calm"
    v = segment_velocity(np.r_[np.zeros(10), 9.0, np.ones(20)], 10 / 30, 1.0, 30)
    assert len(v) == 20 and max(v) < 2  # the cut's own jump is not motion


def test_velocity_time_map_ramps_smoothly_and_uses_the_planned_footage():
    import numpy as np

    from video_agent.render import velocity as vel

    wave = list(1.4 + np.cos(np.arange(45) * 2 * np.pi / 14))  # hits every 14 frames: slow on them, fast between
    speed = vel.speed_curve(wave, 45, 1.2)
    p = vel.time_map(speed, 30)
    assert np.all(np.diff(p) > 0)  # never runs backwards
    assert p[-1] + speed[0] / 30 == pytest.approx(45 / 30 * 1.2)  # exactly the source the Director planned
    assert np.diff(p) * 30 == pytest.approx(speed[1:])  # motion into each frame is that frame's own speed: not a frame late
    assert speed.min() < 0.8 * 1.2 < 1.25 * 1.2 < speed.max()  # a real ramp
    assert np.abs(np.diff(speed)).max() < 0.35 * speed.mean()  # eased, no steps
    # smear: a fast frame blends several source frames, a slow one shows one
    assert len(vel.sample_points(0.0, 3.0, 30, 30)) >= 3 and len(vel.sample_points(0.0, 0.3, 30, 30)) == 1
    frames = [np.full((4, 4, 3), k * 5, np.uint8) for k in range(40)]
    out = list(vel.retime(lambda k: frames[k], 40, np.array([0.0, 0.5]), np.array([3.0, 0.3]), 30, 30))
    assert 2 < out[0].mean() < 12 and out[1].mean() == 75  # averaged sweep over 1.5 frames; one crisp frame
    need = vel.sharp_frames(p, speed, 30, 30, 60)
    assert 0 < need.sum() < 60  # only the frames that land sharp get AI upscaling


def test_drift_never_stops_and_calibration_matches_the_reference_motion():
    import numpy as np

    from video_agent.blueprint import EditEvent
    from video_agent.render import velocity as vel
    from video_agent.render.motion import drift_directions, synthesize

    n = 40
    wave = vel.wave([2, 1, 0.5, 1, 2, 3] * 7, n)
    events = [EditEvent(kind="punch", t=0.5, strength=0.15, zoom=1.0)]
    curve, _, _ = synthesize(events, n, 30, 0.0, 0, wave, 0.0, 2.0, 0.5)
    cam = vel.camera_flow(curve, n, 1080, 1920)
    assert cam.min() > 0.05  # the camera never stands still
    plain, _, _ = synthesize(events, n, 30)
    same, _, _ = synthesize(events, n, 30, 0.0, 0, None, 0.0, 0.0, 1.0)
    assert plain.s == same.s and plain.x == same.x  # calm edits are untouched

    still = np.full(n, 0.14)  # McLaren-like footage that barely moves
    build = lambda a, g: synthesize(events, n, 30, 0.0, 0, wave, 0.0, a, g)[0]  # noqa: E731
    amp, gain, got, ratio = vel.calibrate(build, still, 0.6, 2.6, n, 1080, 1920)
    assert 0 < amp < vel.AMPS[-1] and got == pytest.approx(0.6, rel=0.15)  # reachable: matched
    amp, _, got_max, _ = vel.calibrate(build, still, 5.0, 2.6, n, 1080, 1920)
    assert got_max > got  # out of reach without moving footage: as close as the quality limits allow

    segs = [Segment(start=0, end=1), Segment(start=1, end=2, events=[EditEvent(kind="whip_in", t=0, strength=0.8, dx=0.0, dy=1.0)]),
            Segment(start=2, end=3)]
    d = drift_directions(segs)
    assert d[0.0] == 0.0 and d[1.0] == pytest.approx(np.pi / 2) and d[2.0] == d[1.0]  # carries on across the cut


def test_long_shots_keep_drifting_inside_the_quality_caps():
    import math

    import numpy as np

    from video_agent.render import velocity as vel
    from video_agent.render.motion import DRIFT_TRAVEL, DRIFT_ZOOM_TRAVEL, drift_path

    n = 60
    wave = vel.wave([2, 1, 0.5, 1, 2, 3] * 10, n)
    for amp in (1.0, 4.0):
        x, y, zoom, roll = drift_path(wave, 0.3, amp)
        assert np.ptp(x) <= DRIFT_TRAVEL + 1e-6 and np.ptp(y * 16 / 9) <= DRIFT_TRAVEL + 1e-6
        assert zoom.min() >= 0 and zoom.max() <= DRIFT_ZOOM_TRAVEL + 1e-6
        assert np.abs(roll).max() <= math.radians(1.5) + 1e-6
    moving = lambda amp: np.median(np.hypot(np.diff(drift_path(wave, 0.3, amp)[0]), np.diff(drift_path(wave, 0.3, amp)[1] * 16 / 9)))  # noqa: E731
    assert moving(4.0) > 3.5 * moving(1.0)  # 4x the drift moves ~4x faster: curves, does not stop at the cap


def test_grade_matches_lightness_and_colourfulness_distributions():
    import cv2
    import numpy as np

    from video_agent.blueprint import ColorProfile
    from video_agent.media import color_stats
    from video_agent.render.grade import _match_colour, _match_lightness

    rng = np.random.default_rng(0)
    grey = np.clip(rng.normal(60, 25, (64, 64, 3)) * [1, 1, 1], 0, 255).astype(np.uint8)  # dark, colourless footage
    grey[:32] = np.clip(grey[:32] * 0.25, 0, 255).astype(np.uint8)  # half of it a black car
    vivid = np.clip(rng.normal(120, 60, (64, 64, 3)), 0, 255).astype(np.uint8)  # bright, contrasty, colourful look
    src, tgt = color_stats([grey]), ColorProfile(**color_stats([vivid]))
    lab = cv2.cvtColor(grey.astype(np.float32) / 255, cv2.COLOR_BGR2LAB).reshape(-1, 3)
    L = _match_lightness(lab[:, 0], np.array(src["l_quantiles"]), np.array(tgt.l_quantiles))
    assert np.all(np.diff(L[np.argsort(lab[:, 0])]) >= -1e-6)  # keeps the order of tones
    assert (L <= 0.5).mean() < 0.02 and L.std() > lab[:, 0].std()  # more contrast, shadows not crushed to black
    a, b = _match_colour(lab[:, 1], lab[:, 2], src["lab_mean"], np.array(src["chroma_quantiles"]), tgt.lab_mean,
                         np.array(tgt.chroma_quantiles))
    before = np.hypot(lab[:, 1] - lab[:, 1].mean(), lab[:, 2] - lab[:, 2].mean()).mean()
    assert np.hypot(a - a.mean(), b - b.mean()).mean() > 2 * before  # grey footage gets colourful


def test_every_shot_gets_the_edits_tint():
    from types import SimpleNamespace

    from video_agent.blueprint import Blueprint, ColorProfile, SourceInfo
    from video_agent.render.engine import _target_color

    look = ColorProfile(lab_mean=(45.0, -5.0, -3.0), lab_std=(30.0, 8.0, 10.0))
    blue_shot = ColorProfile(lab_mean=(35.0, -2.0, -12.0), lab_std=(28.0, 9.0, 14.0))  # a blue car close-up
    bp = Blueprint(source=SourceInfo(duration=1, fps=30, width=1080, height=1920), segments=[], color=look)
    shot = SimpleNamespace(segment=SimpleNamespace(color=blue_shot), content_match=0.0)
    tinted = _target_color(shot, bp, one_tint=True)
    assert tinted.lab_mean[1:] == look.lab_mean[1:]  # the edit's tint, not the blue car's
    assert tinted.lab_mean[0] == _target_color(shot, bp).lab_mean[0] == 0.75 * 35 + 0.25 * 45  # brightness still per shot


def test_neutral_point_reads_the_grade_not_the_content():
    import numpy as np

    from video_agent.media import color_stats

    frame = np.zeros((100, 60, 3), np.uint8)
    frame[:60] = (40, 160, 50)  # mostly green grass (BGR)
    frame[60:] = (130, 128, 125)  # a grey road, very slightly warm
    st = color_stats([frame])
    assert st["lab_mean"][1] < -15  # the average is the grass...
    assert abs(st["neutral_ab"][0]) < 2 and abs(st["neutral_ab"][1]) < 3  # ...the neutral point is the road


def test_director_does_not_repeat_a_picture_within_a_few_shots():
    import numpy as np

    from video_agent.director import REPEAT_PENALTY, _repeat_penalty

    wheel, front, side = np.eye(3)
    recent = [((0, 1), wheel), ((0, 2), front)]  # the wheel close-up, then the front
    assert _repeat_penalty((0, 1), wheel, recent) == REPEAT_PENALTY * (1 - 1 / 3)  # the wheel again, one shot later
    assert _repeat_penalty((0, 3), wheel, recent) > 0  # another angle that looks the same counts too
    assert _repeat_penalty((0, 3), side, recent) == 0  # something new is free
    assert _repeat_penalty((0, 1), wheel, recent + [((0, 3), side)] * 3) == 0  # long ago: free again


def test_detached_transitions_composite_two_shots():
    import numpy as np

    from video_agent.analysis.transitions import shrinking_circle
    from video_agent.render.transitions import cutout_frame, panel_frame, portal_frame

    a = np.full((320, 180, 3), 40, np.uint8)  # previous shot: dark
    b = np.full((320, 180, 3), 200, np.uint8)  # next shot: bright
    assert panel_frame(a, b, 0.1, 0.0).mean() < 80  # a small window of the next shot at the corner...
    assert panel_frame(a, b, 1.0, 0.85).mean() > 190  # ...that grows to the whole frame
    assert panel_frame(a, b, 0.4, 0.3)[5, 175].mean() > panel_frame(a, b, 0.4, 0.3)[315, 5].mean()  # from the top right

    wheel = (90.0, 220.0, 20.0)
    assert portal_frame(a, b, 0.0, wheel).mean() < 60  # zoomed into the wheel: its opening (the previous shot) fills the frame
    end = portal_frame(a, b, 1.0, wheel)
    assert end[30, 90].mean() > 190 and end[220, 90].mean() < 60  # zoomed out: the next shot, the opening at the wheel
    assert portal_frame(a, b, 1.0, wheel, flash=1.0)[220, 90].mean() > 190  # the white flash in the opening

    mask = np.zeros((320, 180), np.float32)
    mask[150:250, 40:140] = 1  # the next shot's car
    assert np.array_equal(cutout_frame(a, b, mask, 0.0, 0.0), a)  # still below the frame
    landed = cutout_frame(a, b, mask, 1.0, 0.9)
    assert landed[200, 90].mean() > 150 and landed[50, 90].mean() < 60  # the car in place, the background still the previous shot

    shrinking = [(90, 160, r) for r in (130, 128, 115, 100, 80, 52, 30)]
    assert shrinking_circle(shrinking, 216)  # the portal's signature
    assert not shrinking_circle([(90, 160, r) for r in (60, 62, 59, 61, 60, 58, 60)], 216)  # a wheel that just sits there
    assert not shrinking_circle([(90, 160, 130), None, (20, 30, 40), None, (150, 200, 90)], 216)  # unrelated circles


def test_beat_glow_warms_the_sky_on_the_beat():
    import cv2
    import numpy as np

    from video_agent import labels
    from video_agent.blueprint import Blueprint, Glow, SourceInfo
    from video_agent.render.glow import envelope, glow_frame

    frame = np.full((320, 180, 3), 60, np.uint8)
    frame[:120] = 225  # a bright overcast sky over a dark street
    g = Glow(t=1.0, frames=6, warm=10.0, bright=-10.0)
    lab = lambda f: cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)  # noqa: E731
    out = glow_frame(frame, g, 1.0)
    assert lab(out)[:120, :, 2].mean() - lab(frame)[:120, :, 2].mean() > 5  # the sky turns golden...
    assert abs(lab(out)[250:, :, 0].mean() - lab(frame)[250:, :, 0].mean()) < 3  # ...the street keeps its brightness
    assert np.array_equal(glow_frame(frame, g, 0.0), frame)
    assert [envelope(k, 6) for k in (-1, 0, 3, 5, 6)] == [0.0, 1.0, 1.0, 1 / 3, 0.0]  # on at the beat, eases off

    bp = Blueprint(source=SourceInfo(duration=3, fps=30, width=1080, height=1920), segments=[Segment(start=0, end=3)], glows=[g])
    assert any(m.kind == "glow" for m in labels.moves(bp))
    bp2, done = labels.apply_corrections(bp, "add 2.0 shine")
    assert done and [x.t for x in bp2.glows] == [1.0, 2.0]


def test_grade_measures_the_framed_picture():
    import numpy as np

    from video_agent.blueprint import CameraCurve
    from video_agent.render.compositor import framed

    frame = np.full((320, 180, 3), 220, np.uint8)
    frame[110:210, 50:130] = 10  # a black car in the middle of a bright street
    push = CameraCurve(fps=30, s=[1.6] * 10, r=[0.0] * 10, x=[0.0] * 10, y=[0.0] * 10)
    seen = framed([(0, frame)], push, 1080, 1920, 30, 10, centre=False)[0]
    assert seen.shape == frame.shape and seen.mean() < frame.mean() - 20  # pushed in: the car fills more of it
    still = framed([(0, frame)], CameraCurve(fps=30, s=[1.0] * 10, r=[0.0] * 10, x=[0.0] * 10, y=[0.0] * 10), 1080, 1920, 30, 10,
                   centre=False)[0]
    assert np.abs(still.astype(int) - frame).max() <= 1


def test_velocity_whips_speed_up_out_of_the_drift():
    import numpy as np

    from video_agent.blueprint import EditEvent
    from video_agent.render import velocity as vel
    from video_agent.render.motion import VELOCITY_WHIP_EASE, synthesize

    n = 40
    wave = vel.wave([1.0] * n, n)
    events = [EditEvent(kind="whip_out", t=1.2, strength=1.0, dx=1.0)]
    full, whips, _ = synthesize(events, n, 30, 0.0, 0, wave, 0.0, 1.0, 1.0)
    gentle, _, _ = synthesize(events, n, 30, 0.0, 0, wave, 0.0, 1.0, 1.0, 1.0, VELOCITY_WHIP_EASE)
    assert gentle.x[-1] == pytest.approx(full.x[-1])  # same travel...
    last = lambda c: abs(c.x[-1] - c.x[-2]) / abs(c.x[-1] - c.x[-5])  # noqa: E731
    assert last(gentle) < last(full)  # ...less of it on the last frame

    segs = [Segment(start=0, end=1, velocity=[1.0] * 26 + [2.0] * 4, events=events)]
    assert vel.whip_ratio(segs, 4) == pytest.approx(2.0)
    assert vel.whip_ratio([Segment(start=0, end=1)], 4) == vel.WHIP_RATIO  # nothing to measure

    still = np.full(n, 0.14)
    build = lambda g: synthesize(events, n, 30, 0.0, 0, wave, 0.0, 1.0, 1.0, g, VELOCITY_WHIP_EASE)[0]  # noqa: E731
    cam = vel.camera_flow(build(1.0), n, 1080, 1920)
    calm = float(np.median(cam[~whips]))
    assert np.median(cam[whips]) > 4 * calm  # at full travel whips jump far out of the drift
    g, got = vel.calibrate_whips(build, still, 2.0 * calm, whips, n, 1080, 1920)
    assert g < 1.0 and got == pytest.approx(2.0 * calm, rel=0.2)  # calibrated: twice the shot's motion


def test_flow_score_and_advice_for_velocity_templates():
    import numpy as np

    from video_agent.advice import velocity_advice
    from video_agent.scoring import _flow

    wave = 2 + np.sin(np.arange(300) * 2 * np.pi / 14)
    hits = np.arange(1, 20) * 14 / 30
    spikes = 0.2 + np.where(np.arange(300) % 14 == 0, 8.0, 0.0)  # still, then bursts: what we used to make
    assert _flow(wave, wave, hits, 30)[0] == pytest.approx(1.0)
    assert _flow(wave, spikes, hits, 30)[0] < 0.5

    bp = _bp([Segment(start=0, end=1, velocity=[2.0] * 30)])
    assert velocity_advice(bp, 1.5) is None  # calm template: nothing to say
    bp.style = "velocity"
    assert velocity_advice(bp, 1.5) is None  # footage moves enough
    assert "14x less" in velocity_advice(bp, 0.14)


def test_velocity_render_ramps_smears_and_drifts(samples, tmp_path, caplog):
    import logging

    import numpy as np

    from video_agent.render import RenderOptions

    ref, raw = samples
    bp = clone_video(ref)
    bp.style = "velocity"
    for seg in bp.segments:
        n = round(seg.duration * 30)
        seg.velocity = [round(2 + 1.5 * np.cos(i * 2 * np.pi / 14), 3) for i in range(n)]  # slow on hits, fast between
        seg.camera = None
    caplog.set_level(logging.INFO, logger="video_agent.render.engine")
    result = render(bp, plan_edit(bp, [raw]), tmp_path / "vel.mp4", RenderOptions(interpolate_slowmo=False))
    info = probe(result.master)
    assert info.duration == pytest.approx(bp.duration, abs=0.1)
    text = caplog.text
    assert "velocity: drift" in text and "velocity retime failed" not in text


def test_new_transition_types_render_and_replace_whips():
    import numpy as np

    from video_agent import labels
    from video_agent.blueprint import Blueprint, EditEvent, SourceInfo
    from video_agent.render.transitions import cutout_frame, silhouette_frame, zoom_frame

    a = np.full((320, 180, 3), 40, np.uint8)
    b = np.full((320, 180, 3), 200, np.uint8)
    assert zoom_frame(a, b, 0.25, 0.0).mean() < 60 and zoom_frame(a, b, 1.0, 0.75).mean() > 190  # the old shot, then the new
    mask = np.zeros((320, 180), np.float32)
    mask[150:250, 40:140] = 1
    assert silhouette_frame(b * 0 + 30, mask, 1.0)[200, 90].mean() > 240  # the car flashes white...
    assert np.array_equal(silhouette_frame(b, mask, 0.0), b)  # ...and fades back to the picture
    left = cutout_frame(a, b, mask, 0.5, 0.4, "left")
    assert left[200, :60].mean() > left[200, 120:].mean()  # coming in from the left: the car is on the left side first

    segs = [Segment(start=0, end=1, events=[EditEvent(kind="whip_out", t=1.0, strength=1.0, dx=1.0)]),
            Segment(start=1, end=2, events=[EditEvent(kind="whip_in", t=0.0, strength=1.0, dx=1.0)])]
    bp = Blueprint(source=SourceInfo(duration=2, fps=30, width=1080, height=1920), segments=segs)
    bp2, done = labels.apply_corrections(bp, "add 1.0 cut-out from the left")
    assert done and bp2.segments[1].transition_in == "cutout" and bp2.segments[1].transition_from == "left"
    assert not any(e.kind.startswith("whip") for s in bp2.segments for e in s.events)  # the cut-out replaces the whip


def test_transitions_apply_across_real_clips(tmp_path, monkeypatch):
    import subprocess

    import numpy as np

    from video_agent.render import transitions as T
    from video_agent.render.engine import LOSSLESS

    for name, colour in (("a", "red"), ("b", "blue")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=180x320:r=30:d=0.5",
                        *map(str, LOSSLESS), str(tmp_path / f"{name}.mp4")], check=True)
    mask = np.zeros((320, 180), np.float32)
    mask[150:250, 40:140] = 1
    monkeypatch.setattr(T, "car_mask", lambda frame: mask)
    monkeypatch.setattr(T, "find_wheel", lambda frame: None)  # portal falls back to a round opening
    monkeypatch.setattr(T.vision, "available", lambda: False)
    for kind in ("silhouette", "portal", "cutout", "zoom", "panel"):  # silhouette: nothing before the cut
        for n in ("a", "b"):
            (tmp_path / f"{n}_{kind}.mp4").write_bytes((tmp_path / f"{n}.mp4").read_bytes())
        assert T.apply(kind, tmp_path / f"a_{kind}.mp4", tmp_path / f"b_{kind}.mp4", 180, 320, 30, LOSSLESS, 0, "left"), kind
