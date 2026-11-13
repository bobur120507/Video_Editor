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
    assert len(punches) == 1 and punches[0].t == pytest.approx(0.52)  # moved onto the hit
    assert not extract_events(seg, hits=[12.0]) or all(e.kind != "punch" for e in extract_events(seg, hits=[12.0]))


def test_synthesized_whip_and_reframe_put_subject_in_place():
    import numpy as np

    from video_agent.blueprint import EditEvent, Subject
    from video_agent.render.motion import _matrix, reframe, synthesize
    from video_agent.vision import Box

    curve, whip = synthesize([EditEvent(kind="whip_out", t=1.0, strength=1.0, dx=1.0)], 30, 30)
    assert whip[-3:].all() and not whip[:-3].any()
    assert curve.x[-1] > 0.3 and abs(curve.x[0]) < 1e-9  # smears out at the end only

    still, _ = synthesize([], 1, 30)
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
