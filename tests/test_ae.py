import asyncio
import json
import math
import subprocess
import zipfile

import cv2
import numpy as np
import pytest

from video_agent import knowledge
from video_agent.ae import curves, effects, lottie, mogrt
from video_agent.director import plan_edit
from video_agent.render import render
from video_agent.render.engine import RenderOptions

W, H, FR = 1080, 1920, 30
EASE_OUT, EASE_IN = {"x": [0.333], "y": [0]}, {"x": [0.667], "y": [1]}
LINEAR_OUT, LINEAR_IN = {"x": [0], "y": [0]}, {"x": [1], "y": [1]}


def _tr(p=(540, 960), s=None, r=None, a=(540, 960)):
    return {"a": {"a": 0, "k": list(a)}, "p": p if isinstance(p, dict) else {"a": 0, "k": list(p)},
            "s": s or {"a": 0, "k": [100, 100]}, "r": r or {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}}


def _comp() -> dict:
    """An AE edit as Bodymovin exports it: 3 shots, a 2-frame white flash, an eased zoom, a rotating parent
    null, a time remap (half speed then triple), a wiggle expression and a text layer."""
    return {"v": "5.12.0", "fr": FR, "ip": 0, "op": 90, "w": W, "h": H, "assets": [{"id": "c1", "w": W, "h": H, "layers": []}],
            "layers": [
                {"ty": 5, "ind": 9, "nm": "Title", "ip": 0, "op": 30, "st": 0, "ks": _tr(p=(540, 300)),
                 "t": {"d": {"k": [{"s": {"t": "POV: day 1", "s": 96, "fc": [1, 0.8, 0]}, "t": 0}]}}},
                {"ty": 1, "ind": 8, "nm": "White flash", "sc": "#ffffff", "ip": 28, "op": 30, "st": 0, "ks": _tr()},
                {"ty": 0, "ind": 1, "nm": "Shot A", "refId": "c1", "ip": 0, "op": 30, "st": 0, "w": W, "h": H, "mb": True,
                 "ks": _tr(p={"a": 0, "k": [540, 960], "x": "wiggle(12, 30)"})},
                {"ty": 0, "ind": 2, "nm": "Shot B", "refId": "c1", "ip": 30, "op": 60, "st": 30, "w": W, "h": H, "parent": 5,
                 "ks": _tr(s={"a": 1, "k": [{"t": 30, "s": [100, 100], "o": EASE_OUT, "i": EASE_IN}, {"t": 59, "s": [130, 130]}]})},
                {"ty": 0, "ind": 3, "nm": "Shot C", "refId": "c1", "ip": 60, "op": 90, "st": 60, "w": W, "h": H, "ks": _tr(),
                 "tm": {"a": 1, "k": [{"t": 60, "s": [0], "o": LINEAR_OUT, "i": LINEAR_IN}, {"t": 75, "s": [0.25]}, {"t": 90, "s": [1.75]}]}},
                {"ty": 3, "ind": 5, "nm": "Camera null", "ip": 0, "op": 90, "st": 0,
                 "ks": _tr(r={"a": 1, "k": [{"t": 30, "s": [0], "o": LINEAR_OUT, "i": LINEAR_IN}, {"t": 60, "s": [6]}]})},
            ]}


# ---------------------------------------------------------------- curves


def test_bezier_easing_matches_the_css_spec():
    assert curves.cubic_bezier(0.25, 0.1, 0.25, 1, 0.5) == pytest.approx(0.8024, abs=1e-3)  # CSS `ease` at 50 %
    x = np.linspace(0, 1, 11)
    assert np.allclose(curves.cubic_bezier(0, 0, 1, 1, x), x, atol=1e-6)
    v = curves.ease(np.linspace(0, 1, 101), 0, 1, 0, 10)
    assert v[0] == 0 and v[-1] == 10 and v[1] - v[0] < 0.01 and v[-1] - v[-2] < 0.01  # zero speed at both ends
    assert curves.linear(2.0, 0, 1, 0, 10) == 10  # clamped like AE


def test_loop_out_modes_and_wiggle():
    kt, kv = [0, 1], [0, 10]
    assert list(curves.loop_out([0.5, 1.5, 2.5], kt, kv, "cycle")) == [5, 5, 5]
    assert list(curves.loop_out([1.25], kt, kv, "pingpong")) == [7.5]
    assert list(curves.loop_out([2.0], kt, kv, "offset")) == [20]
    assert list(curves.loop_out([3.0], kt, kv, "continue")) == pytest.approx([30])
    t = np.linspace(0, 20, 6000)
    w = curves.wiggle(t, 2, 20, seed=3)
    assert np.array_equal(w, curves.wiggle(t, 2, 20, seed=3)) and not np.array_equal(w, curves.wiggle(t, 2, 20, seed=4))
    assert np.abs(w).max() <= 20 * 1.5 + 1e-9  # octave 2 adds at most amp * amp_mult
    assert np.abs(np.diff(w)).max() < 0.025 * 20  # smooth: under 2.5 % of the amplitude between samples 3.3 ms apart


def test_spatial_keyframes_move_by_distance():
    mid = curves.along_path([0, 0], [0, 0], [10, 0], [10, 0], 0.5)
    assert mid == pytest.approx([5, 0], abs=0.05)


# ---------------------------------------------------------------- lottie


def test_lottie_edit_becomes_a_blueprint(tmp_path):
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(_comp()))
    bp = lottie.to_blueprint(path, "ae")
    assert [(s.start, s.end, s.transition_in, s.flash_frames) for s in bp.segments] == [
        (0.0, 0.933, "cut", 0), (0.933, 2.0, "flash", 2), (2.0, 3.0, "cut", 0)]
    a, b, c = bp.segments
    assert a.camera is not None and a.camera.describe()["shake"] > 0.005  # the wiggle expression
    cam = b.camera
    i30, i59 = 2, len(cam) - 1  # shot B starts under the flash, 2 frames before its layer
    assert cam.s[i30] == pytest.approx(1.0, abs=1e-4) and cam.s[i59] == pytest.approx(1.3, abs=1e-4)
    assert math.degrees(cam.r[i59]) == pytest.approx(5.8, abs=1e-3)  # inherited from the parent null
    mid = cam.s[i30 + 15]  # Easy Ease starts slower than linear
    assert 1.0 < cam.s[i30 + 5] < 1.0 + 0.3 * 5 / 29 < mid
    assert [(p.at, p.speed) for p in c.speed] == [(0.0, 0.5), (0.5, 3.0)]  # the time remap
    assert c.camera is None and bp.tier == "pro" and bp.motion_blur == 0.5
    t = bp.texts[0]
    assert (t.text, t.start, t.end, t.position, t.color) == ("POV: day 1", 0.0, 1.0, "top", "#FFCC00")


def test_lottie_rejects_other_json(tmp_path):
    (tmp_path / "x.json").write_text('{"hello": 1}')
    with pytest.raises(ValueError):
        lottie.to_blueprint(tmp_path / "x.json")


def test_rendered_edit_moves_like_the_ae_comp(tmp_path):
    """End to end: the comp's eased zoom and parented rotation, measured on the rendered video."""
    (tmp_path / "edit.json").write_text(json.dumps(_comp()))
    bp = lottie.to_blueprint(tmp_path / "edit.json")
    rng = np.random.default_rng(5)
    cv2.imwrite(str(tmp_path / "tex.png"), cv2.resize((rng.random((96, 54, 3)) * 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_CUBIC))
    clip = tmp_path / "static.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-i", tmp_path / "tex.png", "-t", "8", "-r", "30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", clip], check=True)
    out = render(bp, plan_edit(bp, [clip]), tmp_path / "out.mp4", RenderOptions(upscale=False)).master
    cap, frames = cv2.VideoCapture(str(out)), []
    while (ok := cap.read())[0]:
        frames.append(cv2.cvtColor(ok[1], cv2.COLOR_BGR2GRAY))
    sift = cv2.SIFT_create(3000)

    def rel(i, j):
        ka, da = sift.detectAndCompute(frames[i], None)
        kb, db = sift.detectAndCompute(frames[j], None)
        good = [x for x, y in cv2.BFMatcher().knnMatch(da, db, k=2) if x.distance < 0.7 * y.distance]
        m, _ = cv2.estimateAffinePartial2D(np.float32([ka[g.queryIdx].pt for g in good]), np.float32([kb[g.trainIdx].pt for g in good]))
        return math.hypot(m[0, 0], m[1, 0]), math.degrees(math.atan2(m[1, 0], m[0, 0]))

    cam = bp.segments[1].camera  # its index 0 is comp frame 28
    for f in (45, 59):
        zoom, rot = rel(31, f)
        assert zoom == pytest.approx(cam.s[f - 28] / cam.s[31 - 28], rel=2e-3)
        assert rot == pytest.approx(math.degrees(cam.r[f - 28] - cam.r[31 - 28]), abs=0.05)
    assert np.mean(frames[28]) > 240  # the white flash


# ---------------------------------------------------------------- .aep (py-aep)


def make_aep(path) -> None:
    """The same edit as _comp(), as a real After Effects project written by py-aep (AE's Easy Ease on the zoom)."""
    py_aep = pytest.importorskip("py_aep")
    from py_aep.enums import KeyframeInterpolationType as KI
    from py_aep.models.properties.keyframe_ease import KeyframeEase

    app = py_aep.new()
    root = app.project.root_folder
    main = root.add_comp("Edit", W, H, 1.0, 3.0, float(FR))
    shots = []
    for name, rgb in (("Shot A", [0.2, 0.4, 0.6]), ("Shot B", [0.6, 0.3, 0.2]), ("Shot C", [0.3, 0.6, 0.3])):
        c = root.add_comp(name, W, H, 1.0, 3.0, float(FR))
        c.add_solid(rgb, "bg")
        shots.append(c)
    null = main.add_null()
    null.name = "Camera null"
    rot = null.transform.property("ADBE Rotate Z")
    rot.set_value_at_time(1.0, 0)
    rot.set_value_at_time(2.0, 6)
    c = main.add(shots[2])  # added bottom first: AE puts each new layer on top
    c.start_time, c.in_point, c.out_point, c.time_remap_enabled = 2.0, 2.0, 3.0, True
    tm = c.property("ADBE Time Remapping")
    tm.remove_all_keys()
    for t, v in ((2.0, 0.0), (2.5, 0.25), (3.0, 1.75)):
        tm.set_value_at_time(t, v)
    b = main.add(shots[1])
    b.start_time, b.in_point, b.out_point, b.parent = 1.0, 1.0, 2.0, null
    sc = b.transform.property("ADBE Scale")
    sc.set_value_at_time(1.0, [100, 100, 100])
    sc.set_value_at_time(59 / FR, [130, 130, 100])
    for k in sc.keyframes:  # Easy Ease (F9): speed 0, 33.33 % influence
        k.in_interpolation_type = k.out_interpolation_type = KI.BEZIER
        k.in_temporal_ease = k.out_temporal_ease = [KeyframeEase(0, 33.333)] * len(k.in_temporal_ease)
    a = main.add(shots[0])
    a.in_point, a.out_point, a.motion_blur = 0.0, 1.0, True
    a.transform.property("ADBE Position").expression = "wiggle(12, 30)"
    f = main.add_solid([1, 1, 1], "White flash")
    f.in_point, f.out_point = 28 / FR, 1.0
    t = main.add_text("POV: day 1")
    t.in_point, t.out_point = 0.0, 1.0
    t.transform.property("ADBE Position").value = [540, 300, 0]
    app.project.save(str(path))


def test_aep_project_imports_like_its_bodymovin_export(tmp_path):
    from video_agent.ae import aep

    make_aep(tmp_path / "edit.aep")
    (tmp_path / "edit.json").write_text(json.dumps(_comp()))
    a, b = aep.to_blueprint(tmp_path / "edit.aep"), lottie.to_blueprint(tmp_path / "edit.json")
    assert [(s.start, s.end, s.transition_in, s.flash_frames, [(p.at, p.speed) for p in s.speed]) for s in a.segments] == \
        [(s.start, s.end, s.transition_in, s.flash_frames, [(p.at, p.speed) for p in s.speed]) for s in b.segments]
    assert a.segments[0].camera.describe()["shake"] > 0.005 and a.motion_blur == 0.5 and a.tier == "pro"
    assert [t.text for t in a.texts] == ["POV: day 1"] and a.texts[0].position == "top"
    assert "aep" in a.tags and "Edit" in a.source.origin
    # AE's Easy Ease (speed 0, 33.33 % influence, evaluated by py-aep the way AE does) is the bezier
    # (0.333, 0, 0.667, 1) the Lottie side uses: the zoom matches within 0.05 % of the move on every frame.
    za, zl = np.array(a.segments[1].camera.s), np.array(b.segments[1].camera.s)
    assert np.abs(za - zl).max() < 0.0005 * 0.3
    assert np.allclose(a.segments[1].camera.r, b.segments[1].camera.r, atol=1e-6)


def test_unreadable_aep_says_why(tmp_path):
    from video_agent.ae import aep

    pytest.importorskip("py_aep")
    (tmp_path / "old.aep").write_bytes(b"RIFX\0\0\0\x10Egg!not a project")
    with pytest.raises(ValueError, match="could not read"):
        aep.to_blueprint(tmp_path / "old.aep")


# ---------------------------------------------------------------- mogrt


def test_mogrt_controls(tmp_path):
    definition = {"capsuleName": {"strDB": [{"localeString": "en_US", "str": "Lower third"}]}, "clientControls": [
        {"id": 1, "type": 6, "uiName": {"strDB": [{"localeString": "en_US", "str": "Title"}]},
         "value": {"strDB": [{"localeString": "en_US", "str": "BMW M4"}]}},
        {"id": 2, "type": 2, "uiName": "Size", "value": 72},
        {"id": 3, "type": 4, "uiName": "Accent", "value": [1, 0.5, 0, 1]},
        {"id": 4, "type": 1, "uiName": "Show logo", "value": True},
    ]}
    path = tmp_path / "t.mogrt"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("definition.json", json.dumps(definition))
        z.writestr("project.aegraphic", b"\0")
    m = mogrt.read(path)
    assert m.name == "Lower third"
    assert [(c.name, c.kind, c.value) for c in m.controls] == [
        ("Title", "text", "BMW M4"), ("Size", "number", 72), ("Accent", "color", [1, 0.5, 0, 1]), ("Show logo", "checkbox", True)]
    assert m.texts == ["BMW M4"]
    with zipfile.ZipFile(tmp_path / "bad.mogrt", "w") as z:
        z.writestr("x.txt", "no")
    with pytest.raises(ValueError):
        mogrt.read(tmp_path / "bad.mogrt")


# ---------------------------------------------------------------- effects


def _texture(seed=1, w=640, h=480):
    rng = np.random.default_rng(seed)
    return cv2.resize((rng.random((h // 8, w // 8, 3)) * 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)


def _shift(img, dx, dy=0.0):
    h, w = img.shape[:2]
    return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _rmse(a, b, m=40):
    return float(np.sqrt(np.mean((a[m:-m, m:-m].astype(float) - b[m:-m, m:-m].astype(float)) ** 2)))


def test_bloom_glows_only_the_highlights():
    dark = np.full((200, 300, 3), 60, np.uint8)
    assert np.abs(effects.bloom(dark).astype(int) - dark).max() <= 1  # nothing above the threshold: unchanged
    spot = dark.copy()
    cv2.circle(spot, (150, 100), 15, (255, 255, 255), -1)
    out = effects.bloom(spot).astype(int)
    assert out[100, 175].mean() > spot[100, 175].mean() + 3  # light spills around the highlight
    assert np.abs(out[10, 10] - spot[10, 10]).max() <= 1  # and not across the frame


def test_vector_blur_matches_a_real_shutter():
    tex = _texture()
    for v in (8, 24):  # px per frame
        truth = effects.to_display(np.mean([effects.to_linear(_shift(tex, v * 0.5 * (k / 63 - 0.5))) for k in range(64)], axis=0))
        flow = np.zeros((*tex.shape[:2], 2), np.float32)
        flow[..., 0] = v
        assert _rmse(effects.vector_blur(tex, flow), truth) < 0.25 * _rmse(tex, truth)


def test_flow_interpolation_finds_the_frame_between():
    tex = _texture(2)
    a, b, truth = _shift(tex, -6, -2), _shift(tex, 6, 2), tex
    blend = cv2.addWeighted(a, 0.5, b, 0.5, 0)
    assert _rmse(effects.flow_interpolate(a, b, 0.5), truth) < 0.3 * _rmse(blend, truth)
    out = effects.retime_frames([a, b], 4)
    assert len(out) == 4 and np.array_equal(out[0], a)


def test_shake_curve_decays_and_scales():
    c = effects.shake_curve(60, 30, amplitude_px=30, frequency=12, width=1080, height=1920, tilt_deg=3, decay_frames=20, seed=1)
    x = np.array(c.x) * 1080
    assert len(c) == 60 and np.abs(x).max() <= 30 + 1e-6 and np.abs(x[:20]).max() > 5
    assert np.abs(x[20:]).max() == 0 and np.abs(np.degrees(c.r)).max() <= 3 + 1e-6


# ---------------------------------------------------------------- knowledge


def test_knowledge_index_has_provenance():
    t = knowledge.techniques()
    assert len(t) >= 13
    for tid, entry in t.items():
        assert entry.get("topic") and entry.get("sources"), tid
        for s in entry["sources"]:
            assert s["url"].startswith("http") and s["status"] in {"verified", "snippet", "derived", "heuristic", "uncertain"}, tid
    assert knowledge.param("ease.easy_ease", "cubic_bezier") == [0.333, 0.0, 0.667, 1.0]
    assert knowledge.param("fx.bloom", "threshold") == 0.8
    assert "wiggle" in knowledge.brief(["camera_shake"])


def test_cli_imports_lottie(tmp_path, monkeypatch):
    from video_agent import storage
    from video_agent.cli import cmd_import_ae
    from video_agent.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    monkeypatch.setattr(storage, "_sessionmaker", None)
    monkeypatch.setattr(storage, "_engine", None)
    (tmp_path / "edit.json").write_text(json.dumps(_comp()))

    async def go():
        await storage.init_db()
        await cmd_import_ae(type("A", (), {"file": str(tmp_path / "edit.json"), "name": "ae_edit", "comp": None})())
        return await storage.get_template(0, nickname="ae_edit")

    assert asyncio.run(go()) is not None
    get_settings.cache_clear()
