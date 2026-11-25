import io
import json
import subprocess
import zipfile
import xml.dom.minidom

import pytest

from video_agent import export, project as P
from video_agent.blueprint import Blueprint, CameraCurve, EditEvent, Segment, SourceInfo, SpeedPoint, TextOverlay
from video_agent.export import capcut, flatten
from video_agent.media import probe


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    a, b, music = d / "a.mp4", d / "b clip.mp4", d / "music.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", a], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "smptebars=s=360x640:r=25:d=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", b], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=110:d=4", music], check=True)
    return str(a), str(b), str(music)


def make(clips) -> P.EditProject:
    a, b, music = clips
    cam = CameraCurve(fps=30, s=[1 + 0.01 * i for i in range(30)], r=[0.0] * 30, x=[0.0] * 30, y=[0.0] * 30)
    segs = [
        Segment(start=0, end=1.0, camera=cam, events=[EditEvent(kind="punch", t=0.5, strength=0.1, zoom=1),
                                                      EditEvent(kind="whip_out", t=1.0, dx=1, strength=0.8)]),
        Segment(start=1.0, end=2.0, transition_in="flash", flash_frames=4, events=[EditEvent(kind="whip_in", t=0, dx=1, strength=0.8)],
                speed=[SpeedPoint(at=0, speed=1), SpeedPoint(at=0.5, speed=0.5)]),
        Segment(start=2.0, end=3.0, events=[EditEvent(kind="push", t=0, duration=1, strength=0.2)]),
    ]
    bp = Blueprint(name="test edit", source=SourceInfo(duration=3, fps=30, width=540, height=960), segments=segs,
                   texts=[TextOverlay(text="POV: day 1", start=0.2, end=1.5)])
    bp.output.width, bp.output.height = 270, 480  # small frames keep the render test quick
    return P.EditProject(name="test edit", blueprint=bp, clips=[a, b], music=music,
                         shots=[P.ShotSource(clip=a, src_start=0.5), P.ShotSource(clip=b, src_start=0.0), P.ShotSource(clip=a, src_start=2.0)])


def spans(p):
    return [(s.start, s.end) for s in p.blueprint.segments]


def test_moving_a_cut_trades_time_and_keeps_its_whips(clips):
    p = make(clips)
    q = P.move_cut(p, 1, 1.21)
    assert spans(q) == [(0.0, 1.2), (1.2, 2.0), (2.0, 3.0)]  # snapped to a frame, total length kept
    assert q.shots[1].src_start == pytest.approx(0.2)  # the next shot starts later in its clip
    a, b = q.blueprint.segments[:2]
    assert ("whip_out", 1.2) in [(e.kind, e.t) for e in a.events] and b.events[0].kind == "whip_in"  # whips follow the cut
    assert ("punch", 0.5) in [(e.kind, e.t) for e in a.events]
    assert b.transition_in == "flash"
    assert [(s.at, s.speed) for s in b.speed] == [(0.0, 1.0), (0.375, 0.5)]  # the slow-down stays at 1.5 s
    assert len(a.camera.s) == 36 and a.camera.s[-1] == pytest.approx(1.29)  # the curve holds its last value
    with pytest.raises(ValueError):
        P.move_cut(p, 0, 0.5)
    assert spans(P.move_cut(p, 1, 2.5))[1][1] - spans(P.move_cut(p, 1, 2.5))[1][0] >= 2 / 30 - 1e-6  # never under two frames


def test_split_remove_and_swap(clips):
    p = make(clips)
    q = P.split(p, 1, 1.5)
    assert spans(q) == [(0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0)]
    assert [s.transition_in for s in q.blueprint.segments][1:3] == ["flash", "cut"]
    assert q.shots[2].src_start == pytest.approx(0.5) and q.blueprint.segments[2].speed[0].speed == 0.5

    r = P.remove(p, 1)  # the shot before takes its place: later cuts stay on the music
    assert spans(r) == [(0.0, 2.0), (2.0, 3.0)]
    assert [e.kind for e in r.blueprint.segments[0].events] == ["punch"]  # its cut is gone, so are the whips
    r0 = P.remove(p, 0)
    assert spans(r0)[0] == (0.0, 2.0) and r0.shots[0].src_start == 0.0 and r0.blueprint.segments[0].transition_in == "cut"

    s = P.swap(p, 0, 2)
    assert (s.shots[0].src_start, s.shots[2].src_start) == (2.0, 0.5) and spans(s) == spans(p)
    with pytest.raises(ValueError):
        P.remove(P.remove(r, 0), 0)


def test_shot_changes_texts_look_and_undo(clips, tmp_path):
    p = make(clips)
    q = P.set_shot(p, 2, clip=clips[1], src_start=1.0, center=(0.2, 2), speed=2, transition="zoom")
    assert q.shots[2].clip == clips[1] and q.shots[2].center == (0.2, 1.0) and q.blueprint.segments[2].speed[0].speed == 2
    with pytest.raises(ValueError):
        P.set_shot(p, 0, transition="flash")
    with pytest.raises(ValueError):
        P.set_shot(p, 0, clip="/elsewhere.mp4")
    t = P.set_texts(q, [{"text": "hi", "start": 2.5, "end": 9}, {"text": " ", "start": 0, "end": 1}])
    assert [(o.text, o.end) for o in t.blueprint.texts] == [("hi", 3.0)]
    assert P.set_look(t, {"brightness": 5, "nonsense": 1, "warmth": 0}).look == {"brightness": 2.0}

    P.save(p, tmp_path)
    P.save(q, tmp_path)
    assert P.can_step(tmp_path) == {"undo": True, "redo": False}
    assert P.step(tmp_path, back=True).shots[2].clip == clips[0]
    assert P.step(tmp_path, back=False).shots[2].clip == clips[1]
    P.step(tmp_path, back=True)
    P.save(t, tmp_path)  # a new change drops the redo
    assert P.can_step(tmp_path)["redo"] is False and P.load(tmp_path).blueprint.texts[0].text == "hi"


def test_flatten_cuts_speed_ramps_into_pieces(clips):
    f = flatten(make(clips))
    assert [(x.shot, x.start, x.frames, x.src_in, x.speed) for x in f.pieces] == [
        (0, 0, 30, 0.5, 1.0), (1, 30, 15, 0.0, 1.0), (1, 45, 15, 0.5, 0.5), (2, 60, 30, 2.0, 1.0)]
    assert f.frames == 90 and sum(x.frames for x in f.pieces) == 90
    assert f.transitions == [(30, "flash", 4)]
    assert {m.kind for m in f.moves} == {"punch", "whip_out", "whip_in", "push"} and f.music is not None


def test_every_format_parses_and_points_at_the_media(clips):
    p = make(clips)
    for fmt in export.FORMATS:
        name, text = export.export(p, fmt, base="C:/Users/me/edit")
        assert name.startswith("test_edit")
        if fmt in ("premiere", "fcpxml"):
            xml.dom.minidom.parseString(text)
            assert "C:/Users/me/edit/media/b%20clip.mp4" in text
        if fmt == "otio":
            tl = json.loads(text)
            video = tl["tracks"]["children"][0]["children"]
            assert len(video) == 4 and video[2]["effects"][0]["time_scalar"] == 0.5
        if fmt == "aftereffects":
            data = json.loads(text.split("var DATA = ", 1)[1].split(";\n", 1)[0])
            assert [L["speed"] for L in data["layers"]] == [1.0, 1.0, 0.5, 1.0] and data["flashes"] == [{"at": 30, "frames": 4}]
            assert len(data["layers"][0]["keys"]["t"]) == 30  # the punch and the whip, baked per frame
            assert max(data["layers"][0]["keys"]["scale"]) > data["layers"][0]["scale"]
            assert "=>" not in text and "let " not in text  # ExtendScript is ES3
        if fmt == "edl":
            assert text.count("* FROM CLIP NAME") == 5 and "M2" in text  # 4 pieces + music, one speed change
    fcp = export.export(p, "fcpxml")[1]
    assert 'version="1.10"' in fcp and "<timeMap>" in fcp and 'adjust-conform type="fill"' in fcp


def test_bundle_and_capcut_kit(clips, tmp_path):
    p = make(clips)
    z = zipfile.ZipFile(io.BytesIO(export.bundle(p)))
    names = set(z.namelist())
    assert {"media/a.mp4", "media/b clip.mp4", "media/music.wav", "HOW_TO_OPEN.txt", "texts.srt", "test_edit.fcpxml"} <= names

    kit = zipfile.ZipFile(io.BytesIO(export.capcut_kit(p, tmp_path / "kit")))
    shots = sorted(n for n in kit.namelist() if n.startswith("shots/"))
    assert shots == ["shots/01_shot.mp4", "shots/02_shot.mp4", "shots/03_shot.mp4"] and "music.m4a" in kit.namelist()
    for n, frames in zip(shots, (30, 30, 30)):
        out = tmp_path / n.replace("/", "_")
        out.write_bytes(kit.read(n))
        pr = probe(out)
        assert (pr.width, pr.height) == (270, 480) and round(pr.duration * 30) == frames  # exactly the shot, framed 9:16
    assert "00:00:00,200 --> 00:00:01,500" in capcut.srt(flatten(p))


def test_an_edited_project_renders(clips, tmp_path):
    from video_agent.render import RenderOptions, render

    p = P.split(P.move_cut(make(clips), 2, 2.4), 0, 0.5)
    plan = P.to_plan(p)
    assert [round(s.src_len, 3) for s in plan.shots][:2] == [0.5, 0.5]
    r = render(p.blueprint, plan, tmp_path / "out.mp4", RenderOptions(upscale=False, music_path=p.music_path(), interpolate_slowmo=False))
    pr = probe(r.master)
    assert (pr.width, pr.height) == (270, 480) and pr.duration == pytest.approx(3.0, abs=0.05) and pr.has_audio
