import asyncio
import subprocess

import pytest

from video_agent.analysis import clone_video
from video_agent.blueprint import Blueprint, Segment, SourceInfo, SpeedPoint, TextOverlay
from video_agent.director import plan_edit
from video_agent.media import probe
from video_agent.render import render
from video_agent.render.engine import _crop_filter, _speed_graph
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


def test_speed_graph_splits_ramp():
    graph, label = _speed_graph([SpeedPoint(at=0, speed=1), SpeedPoint(at=0.5, speed=0.5)], 2.0, 1.0, 30, True)
    assert label == "sp" and "split=2" in graph and "minterpolate=fps=60" in graph


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
