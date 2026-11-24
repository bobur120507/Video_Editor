import asyncio
import base64
import json
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from video_agent import storage
from video_agent.analysis.audio import audio_graph
from video_agent.blueprint import AudioInfo, Blueprint, EditEvent, Segment, SourceInfo
from video_agent.config import get_settings
from video_agent.pipeline import ApplyOutcome
from video_agent.render.engine import RenderResult
from video_agent.web import app as webapp

from tests.test_ae import _comp, make_aep

SR = 22050


def _drums(path, seconds=4.0):
    """55 Hz kicks every 0.5 s, plus hats 40 dB quieter half-way between them."""
    t = np.arange(int(seconds * SR)) / SR
    kick = 0.8 * np.sin(2 * np.pi * 55 * t) * np.exp(-25 * np.mod(t, 0.5))
    rng = np.random.default_rng(0)
    hats = 0.008 * rng.standard_normal(t.size) * (np.mod(t - 0.25, 0.5) < 0.02)
    sf.write(path, kick + hats, SR)
    return path


def test_audio_graph_shows_kicks_low_and_quiet_hats(tmp_path):
    y, _ = sf.read(_drums(tmp_path / "d.wav"))
    g = audio_graph(tmp_path / "d.wav")
    n = len(g)
    assert g.fps == pytest.approx(SR / 512, rel=1e-3) and n == pytest.approx(4.0 * g.fps, abs=2)
    assert all(len(v) == n for v in (g.wave_min, g.wave_max, g.loudness, g.low, g.mid, g.high))
    spec = np.frombuffer(base64.b64decode(g.spec), np.uint8).reshape(len(g.mel_hz), n)
    assert spec.max() == 255 and g.mel_hz == sorted(g.mel_hz)

    col = lambda s: int(round(s * g.fps))  # noqa: E731
    kicks = [col(0.5 * k) for k in range(1, 8)]
    hats = [col(0.25 + 0.5 * k) for k in range(1, 7)]
    assert np.mean([g.low[c] for c in kicks]) > np.mean([g.high[c] for c in kicks]) + 20  # kicks live in the low band
    onset = np.array(g.onset)
    quiet = np.median(onset)
    # Hats at -40 dB still show as onsets, well above the floor between sounds.
    assert np.mean([onset[c - 1 : c + 3].max() for c in hats]) > 3 * quiet
    assert max(g.wave_max) == pytest.approx(y.max(), abs=0.01) and min(g.wave_min) == pytest.approx(y.min(), abs=0.01)


def _blueprint(tmp_path, video=None) -> Blueprint:
    segs = [
        Segment(start=0, end=1.0, events=[EditEvent(kind="punch", t=0.5, strength=0.1, zoom=1)]),
        Segment(start=1.0, end=2.0, transition_in="portal"),
        Segment(start=2.0, end=3.0),
    ]
    audio = AudioInfo(bpm=120, beats=[0.5, 1.0, 1.5, 2.0], drops=[2.0], hits=[0.5, 1.0, 1.25], hit_kinds=["kick", "kick", "snare"],
                      has_music=True, graph=audio_graph(_drums(tmp_path / "g.wav", 3.0)))
    return Blueprint(source=SourceInfo(duration=3.0, fps=30, width=540, height=960, path=str(video or ""), origin="ref.mp4"),
                     audio=audio, segments=segs)


def test_markers_come_from_the_blueprint(tmp_path):
    m = webapp.markers(_blueprint(tmp_path))
    assert m["cuts"] == [{"t": 1.0, "transition": "portal"}, {"t": 2.0, "transition": "cut"}]
    assert m["hits"][2] == {"t": 1.25, "kind": "snare"} and m["drops"] == [2.0]
    assert [(x["t"], x["kind"]) for x in m["moves"]] == [(0.5, "punch"), (1.0, "portal")]
    assert m["moves"][0]["screen"] == "PUNCH"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LLM_PROVIDERS", "")  # keyword feedback parser, no network
    get_settings.cache_clear()
    monkeypatch.setattr(storage, "_sessionmaker", None)
    monkeypatch.setattr(storage, "_engine", None)
    ref = get_settings().dir("references") / "ref.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=220:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", ref], check=True)
    bp = _blueprint(tmp_path, ref)

    async def fake_clone(user_id, path, origin=""):
        assert path.exists()
        row = await storage.save_blueprint(user_id, bp)
        return bp, row

    async def fake_apply(user_id, bp, clips, out, **kw):
        out.write_bytes(b"edit")
        for name in ("cover.jpg", "grid.jpg"):
            (out.parent / name).write_bytes(b"jpg")
        res = RenderResult(master=out, preview=out, cover=out.parent / "cover.jpg", grid=out.parent / "grid.jpg", duration=3.0)
        return ApplyOutcome(result=res, plan=SimpleNamespace(warnings=["w"], missing=[]), blueprint=bp)

    monkeypatch.setattr(webapp, "clone", fake_clone)
    monkeypatch.setattr(webapp, "apply", fake_apply)
    monkeypatch.setattr(webapp, "style_match", lambda ref, out: SimpleNamespace(overall=71.5, parts={"cuts": 80.0}, text=lambda: "Match: 71.5%"))
    async with TestClient(TestServer(webapp.create_app())) as c:
        yield c
    get_settings.cache_clear()


async def _wait(client, job):
    for _ in range(100):
        j = await (await client.get(f"/api/jobs/{job}")).json()
        if j["status"] != "running":
            return j
        await asyncio.sleep(0.05)
    raise TimeoutError(job)


async def test_clone_review_template_flow(client):
    data = FormData()
    data.add_field("video", b"fake video bytes", filename="my ref.mp4", content_type="video/mp4")
    job = (await (await client.post("/api/clone", data=data)).json())["job"]
    done = await _wait(client, job)
    assert done["status"] == "done", done
    bp_id = done["result"]["blueprint_id"]

    view = await (await client.get(f"/api/blueprints/{bp_id}")).json()
    assert view["has_graph"] and view["video"] == f"/api/blueprints/{bp_id}/video"
    assert len(view["markers"]["moves"]) == 2
    graph = await (await client.get(f"/api/blueprints/{bp_id}/graph")).json()
    assert len(graph["onset"]) == len(graph["low"]) > 100

    # The video seeks: Range requests are answered with partial content.
    r = await client.get(view["video"], headers={"Range": "bytes=0-99"})
    assert r.status == 206 and len(await r.read()) == 100
    assert (await client.get("/media/../../etc/passwd")).status == 404
    assert (await client.get("/media/references/ref.mp4")).status == 200

    bad = await client.post(f"/api/blueprints/{bp_id}/moves", json={"text": "hello there"})
    assert bad.status == 400 and "didn't understand" in (await bad.json())["error"]
    r = await (await client.post(f"/api/blueprints/{bp_id}/moves", json={"text": "add 2.5 shake"})).json()
    assert r["done"] and len(r["blueprint"]["markers"]["moves"]) == 3
    assert (await client.post(f"/api/blueprints/{bp_id}/moves/ok")).status == 200
    rows = await storage_rows(storage.MoveLabel)
    assert rows[0].commands == ["add 2.5 shake"] and len(rows[0].detected) == 2 and len(rows[0].corrected) == 3

    tpl = await (await client.post("/api/templates", json={"blueprint_id": bp_id, "name": "car phonk"})).json()
    assert tpl["name"] == "car_phonk"
    assert (await client.post("/api/templates", json={"blueprint_id": bp_id, "name": "car_phonk"})).status == 409
    assert (await client.patch(f"/api/templates/{tpl['id']}", json={"name": "v2"})).status == 200
    assert [t["name"] for t in await (await client.get("/api/templates")).json()] == ["v2"]
    assert (await client.delete(f"/api/templates/{tpl['id']}")).status == 200
    assert await (await client.get("/api/templates")).json() == []


async def storage_rows(model):
    async with storage.session() as s:
        return list((await s.execute(select(model))).scalars())


async def test_apply_change_and_rate(client):
    bp = _blueprint(get_settings().data_dir)
    row = await storage.save_blueprint(get_settings().web_user_id, bp)
    tpl = await storage.save_template(get_settings().web_user_id, "t", row.id)

    empty = FormData()
    empty.add_field("template_id", str(tpl.id))
    assert (await client.post("/api/apply", data=empty)).status == 400

    data = FormData()
    data.add_field("template_id", str(tpl.id))
    data.add_field("text", "POV: day 1")
    for i in range(2):
        data.add_field("clips", b"clip", filename=f"c{i}.mp4", content_type="video/mp4")
    job = (await (await client.post("/api/apply", data=data)).json())["job"]
    done = await _wait(client, job)
    r = done["result"]
    assert done["status"] == "done" and r["version"] == 1 and r["video"].endswith("edit_v1.mp4") and r["notes"] == ["w"]
    assert r["score"] is None  # the test blueprint has no reference on disk
    assert (await client.get(r["cover"])).status == 200

    bad = await client.post(f"/api/renders/{job}/change", json={"text": "hmm"})
    assert bad.status == 400
    again = await (await client.post(f"/api/renders/{job}/change", json={"text": "darker please"})).json()
    assert again["applying"] == {"brightness": -1}
    assert (await _wait(client, again["job"]))["result"]["version"] == 2

    assert (await client.post(f"/api/renders/{job}/rate", json={"aspect": "shake", "value": 1})).status == 200
    assert (await client.post(f"/api/renders/{job}/rate", json={"aspect": "shake", "value": 5})).status == 400
    assert (await storage.get_weights(get_settings().web_user_id))["effects"] == -0.5


async def test_graph_backfill_for_old_templates(client):
    bp = _blueprint(get_settings().data_dir, get_settings().dir("references") / "ref.mp4")
    bp.audio.graph = None  # cloned before the graph was stored
    row = await storage.save_blueprint(get_settings().web_user_id, bp)
    assert (await client.get(f"/api/blueprints/{row.id}/graph")).status == 404
    view = await (await client.get(f"/api/blueprints/{row.id}")).json()
    assert not view["has_graph"] and view["markers"]["beats"]  # markers still drawn without it

    job = (await (await client.post(f"/api/blueprints/{row.id}/graph")).json())["job"]
    assert (await _wait(client, job))["status"] == "done"
    graph = await (await client.get(f"/api/blueprints/{row.id}/graph")).json()
    assert len(graph["onset"]) == pytest.approx(3 * graph["fps"], abs=3)
    assert (await (await client.post(f"/api/blueprints/{row.id}/graph")).json())["job"] is None  # stored: never again


async def test_clone_accepts_a_bodymovin_export(client):
    data = FormData()
    data.add_field("video", json.dumps(_comp()).encode(), filename="ae edit.json", content_type="application/json")
    job = (await (await client.post("/api/clone", data=data)).json())["job"]
    done = await _wait(client, job)
    assert done["status"] == "done", done
    view = await (await client.get(f"/api/blueprints/{done['result']['blueprint_id']}")).json()
    assert view["imported"] and view["video"] is None and not view["has_graph"]
    assert [c["transition"] for c in view["markers"]["cuts"]] == ["flash", "cut"]


async def test_clone_accepts_an_aep_project(client, tmp_path):
    make_aep(tmp_path / "edit.aep")
    data = FormData()
    data.add_field("video", (tmp_path / "edit.aep").read_bytes(), filename="edit.aep", content_type="application/octet-stream")
    done = await _wait(client, (await (await client.post("/api/clone", data=data)).json())["job"])
    assert done["status"] == "done", done
    view = await (await client.get(f"/api/blueprints/{done['result']['blueprint_id']}")).json()
    assert view["imported"] and [c["transition"] for c in view["markers"]["cuts"]] == ["flash", "cut"]
