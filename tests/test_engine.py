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
