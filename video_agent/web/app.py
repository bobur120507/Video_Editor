"""Web UI server (aiohttp, already installed with aiogram): the same pipeline as the Telegram bot.

    clone   upload or link a reference -> Blueprint (with its audio graph) -> move review -> template
    apply   template + clips (+ text, + changes) -> render -> video, cover, grid -> match score

Long steps (clone, render, scoring) run as background jobs the page polls. Everything belongs to
Settings.web_user_id. Only files under the data dir are served. Meant for localhost: there is no login.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aiohttp import web

from .. import labels, storage
from ..ae import aep, lottie
from ..analysis.audio import graph_for
from ..blueprint import Blueprint
from ..config import get_settings
from ..downloader import download, find_url
from ..media import sha256_file
from ..pipeline import apply, clone
from ..scoring import style_match
from ..tweak import parse_feedback

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 2 * 1024**3
JOBS = web.AppKey("jobs", dict)
TASKS = web.AppKey("tasks", set)
REVIEWS = web.AppKey("reviews", dict)  # blueprint id -> moves as detected + the user's corrections, until confirmed
RENDERS = web.AppKey("renders", dict)  # render job id -> what a re-render or a rating needs
RATINGS = {"timing": (1, -1), "shake": (-1, 0, 1), "smooth": (1, -1), "quality": (1, -1)}  # as the bot's buttons


@dataclass
class Job:
    id: str
    kind: str  # clone, render, graph
    status: str = "running"  # running, done, failed
    step: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""


def _user() -> int:
    return get_settings().web_user_id


def media_url(path: str | Path | None) -> str | None:
    """URL of a file under the data dir, or None for anything outside it (never served)."""
    if not path:
        return None
    root = get_settings().data_dir.resolve()
    p = Path(path).resolve()
    if not p.is_file() or not p.is_relative_to(root):
        return None
    return "/media/" + p.relative_to(root).as_posix()


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name or "clip.mp4").name)[-80:] or "clip.mp4"


def _job_dir() -> Path:
    return get_settings().dir("jobs", "web", uuid.uuid4().hex[:10])


def markers(bp: Blueprint) -> dict:
    """Everything the graph draws on its time axis, straight from the stored blueprint."""
    a = bp.audio
    return {
        "bpm": a.bpm,
        "sync_to": a.sync_to,
        "beats": a.beats,
        "drops": a.drops,
        "hits": [{"t": t, "kind": k} for t, k in zip(a.hits, a.hit_kinds)],
        "cuts": [{"t": s.start, "transition": s.transition_in} for s in bp.segments[1:]],
        "glows": [g.t for g in bp.glows],
        "moves": [{**asdict(m), "label": labels.KIND_WORDS[m.kind], "screen": labels.SCREEN[m.kind]} for m in labels.moves(bp)],
    }


def blueprint_view(row_id: int, bp: Blueprint) -> dict:
    return {
        "id": row_id,
        "name": bp.name,
        "summary": bp.summary(),
        "notes": bp.notes,
        "style": bp.style,
        "duration": bp.duration,
        "fps": bp.source.fps,
        "origin": bp.source.origin,
        "imported": bool({"lottie", "aep"} & set(bp.tags)),
        "video": f"/api/blueprints/{row_id}/video" if bp.source.path and Path(bp.source.path).is_file() else None,
        "has_graph": bp.audio.graph is not None,
        "has_audio": bp.audio.graph is not None or bp.audio.has_music,
        "markers": markers(bp),
    }


# ---------------------------------------------------------------- jobs


def _start(app: web.Application, kind: str, work) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    app[JOBS][job.id] = job

    async def run():
        try:
            await work(job)
            job.status = "done"
        except Exception as e:
            log.exception("%s job failed", kind)
            job.status, job.error = "failed", str(e) or e.__class__.__name__
        job.step = ""

    task = asyncio.create_task(run())
    app[TASKS].add(task)
    task.add_done_callback(app[TASKS].discard)
    return job


async def _clone_job(app: web.Application, job: Job, path: Path | None, url: str | None, origin: str) -> None:
    if url:
        job.step = "Downloading…"
        path = await asyncio.to_thread(download, url, path.parent if path else _job_dir())
    if path.suffix.lower() in (".json", ".aep"):  # an After Effects comp: Bodymovin export or project file
        job.step = "Reading the After Effects comp…"
        importer = aep.to_blueprint if path.suffix.lower() == ".aep" else lottie.to_blueprint
        bp = await asyncio.to_thread(importer, path, path.stem)
        row = await storage.save_blueprint(_user(), bp)
    else:
        job.step = "Analyzing cuts, beats, motion and colour… (about as long as the video)"
        bp, row = await clone(_user(), path, origin=origin)
    app[REVIEWS][row.id] = {"detected": labels.snapshot(bp), "commands": []}
    job.result = {"blueprint_id": row.id}


async def _render_job(app: web.Application, job: Job, bp: Blueprint, clips: list[str], job_dir: Path, *,
                      template_id: int | None, texts: list[str] | None, adjustments: dict | None,
                      use_preferences: bool, version: int) -> None:
    job.step = "Rendering… (shot planning, colour matching, effects, encode)"
    out = job_dir / f"edit_v{version}.mp4"
    outcome = await apply(_user(), bp, clips, out, texts=texts, adjustments=adjustments, use_preferences=use_preferences)
    rendered = await storage.save_blueprint(_user(), outcome.blueprint)
    r = outcome.result
    await storage.add_fingerprint(user_id=_user(), blueprint_id=rendered.id, file_sha256=sha256_file(r.master), master_path=str(r.master))
    if template_id:
        await storage.bump_template_usage(template_id, preview_path=str(r.preview))
    notes = [*outcome.plan.warnings]
    if outcome.plan.missing:
        notes.append("Shots worth filming next time:\n" + "\n".join(outcome.plan.missing))
    job.result = {
        "version": version,
        "blueprint_id": rendered.id,
        "template_id": template_id,
        "video": media_url(r.master),
        "cover": media_url(r.cover),
        "grid": media_url(r.grid),
        "duration": r.duration,
        "notes": notes,
        "score": None,
    }
    app[RENDERS][job.id] = {"clips": clips, "dir": job_dir, "master": str(r.master), "template_id": template_id,
                              "blueprint_id": rendered.id, "version": version, "scores": {}}
    reference = outcome.blueprint.source.path
    if reference and Path(reference).exists():
        job.step = "Measuring the match with the reference… (about as long as the video)"
        try:
            report = await asyncio.to_thread(style_match, reference, r.master)
            job.result["score"] = {"overall": report.overall, "parts": report.parts, "text": report.text()}
            app[RENDERS][job.id]["scores"] = report.parts
        except Exception:
            log.exception("match scoring failed")


# ---------------------------------------------------------------- handlers


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def get_job(request: web.Request) -> web.Response:
    job = request.app[JOBS].get(request.match_info["job_id"])
    if not job:
        raise web.HTTPNotFound(text="No such job")
    return web.json_response(asdict(job))


async def post_clone(request: web.Request) -> web.Response:
    """multipart with a `video` file (or an After Effects .aep / Bodymovin .json), or JSON / form with a `url`."""
    path, url, origin = None, None, ""
    if request.content_type.startswith("multipart/"):
        reader = await request.multipart()
        async for part in reader:
            if part.name == "video" and part.filename:
                path = _job_dir() / _safe_name(part.filename)
                with path.open("wb") as f:
                    while chunk := await part.read_chunk(1 << 20):
                        f.write(chunk)
                origin = part.filename
            elif part.name == "url":
                url = find_url(await part.text())
    else:
        url = find_url((await request.json()).get("url"))
    if not path and not url:
        raise web.HTTPBadRequest(text="Send a video file or a link to one.")
    origin = url or origin
    job = _start(request.app, "clone", lambda j: _clone_job(request.app, j, path, url, origin))
    return web.json_response({"job": job.id})


async def _row(request: web.Request):
    row = await storage.get_blueprint(int(request.match_info["bp_id"]))
    if not row or row.user_id != _user():
        raise web.HTTPNotFound(text="No such template")
    return row


async def get_blueprint(request: web.Request) -> web.Response:
    row = await _row(request)
    return web.json_response(blueprint_view(row.id, row.blueprint))


async def get_video(request: web.Request) -> web.FileResponse:
    """The blueprint's own reference video: CLI clones can live outside the data dir. The path comes from the database."""
    path = Path((await _row(request)).blueprint.source.path or "")
    if not path.is_file():
        raise web.HTTPNotFound(text="The reference video is no longer on disk")
    return web.FileResponse(path)


async def get_graph(request: web.Request) -> web.Response:
    row = await _row(request)
    graph = row.blueprint.audio.graph
    if graph is None:
        raise web.HTTPNotFound(text="No audio graph stored for this template")
    return web.json_response(graph.model_dump(mode="json"), headers={"Cache-Control": "private, max-age=3600"})


async def post_graph(request: web.Request) -> web.Response:
    """One-time backfill for blueprints cloned before the graph was stored; the result is saved."""
    row = await _row(request)
    bp = row.blueprint
    if bp.audio.graph is not None:
        return web.json_response({"job": None})
    if not bp.source.path or not Path(bp.source.path).exists():
        raise web.HTTPBadRequest(text="The reference video is no longer on disk")

    async def work(job: Job):
        job.step = "Analysing the sound once and storing it…"
        bp.audio.graph = await asyncio.to_thread(graph_for, bp.source.path)
        await storage.update_blueprint(row.id, bp)

    return web.json_response({"job": _start(request.app, "graph", work).id})


async def post_moves(request: web.Request) -> web.Response:
    """Plain-word corrections, as in the bot: "remove 3 5", "4 shake", "add 2.4 punch"."""
    row = await _row(request)
    text = str((await request.json()).get("text", "")).strip()
    bp, done = labels.apply_corrections(row.blueprint, text)
    if not done:
        raise web.HTTPBadRequest(text="I didn't understand that. Try “remove 3 5”, “4 shake” or “add 2.4 punch”.")
    await storage.update_blueprint(row.id, bp)
    review = request.app[REVIEWS].setdefault(row.id, {"detected": labels.snapshot(row.blueprint), "commands": []})
    review["commands"].append(text)
    return web.json_response({"done": done, "blueprint": blueprint_view(row.id, bp)})


async def post_moves_ok(request: web.Request) -> web.Response:
    """The moves are right (as corrected): stored as ground truth for detection, like the bot's ✅."""
    row = await _row(request)
    bp = row.blueprint
    review = request.app[REVIEWS].pop(row.id, None) or {"detected": labels.snapshot(bp), "commands": []}
    await storage.add_move_label(user_id=_user(), blueprint_id=row.id, source_sha256=bp.source.sha256,
                                 detected=review["detected"], corrected=labels.snapshot(bp), commands=review["commands"])
    return web.json_response({"ok": True})


def _template_view(t) -> dict:
    return {"id": t.id, "name": t.nickname, "blueprint_id": t.blueprint_id, "usage": t.usage_count,
            "created": t.created_at.isoformat() if t.created_at else None}


async def list_templates(request: web.Request) -> web.Response:
    return web.json_response([_template_view(t) for t in await storage.list_templates(_user())])


def _nickname(raw) -> str:
    name = str(raw or "").strip().replace(" ", "_")[:64]
    if not name:
        raise web.HTTPBadRequest(text="Give the template a name.")
    return name


async def post_template(request: web.Request) -> web.Response:
    body = await request.json()
    row = await storage.get_blueprint(int(body.get("blueprint_id", 0)))
    if not row or row.user_id != _user():
        raise web.HTTPNotFound(text="No such blueprint")
    name = _nickname(body.get("name"))
    if await storage.get_template(_user(), nickname=name):
        raise web.HTTPConflict(text=f"A template called “{name}” already exists.")
    return web.json_response(_template_view(await storage.save_template(_user(), name, row.id)))


async def patch_template(request: web.Request) -> web.Response:
    ok = await storage.rename_template(_user(), int(request.match_info["tpl_id"]), _nickname((await request.json()).get("name")))
    if not ok:
        raise web.HTTPNotFound(text="Template not found")
    return web.json_response({"ok": True})


async def delete_template(request: web.Request) -> web.Response:
    if not await storage.delete_template(_user(), int(request.match_info["tpl_id"])):
        raise web.HTTPNotFound(text="Template not found")
    return web.json_response({"ok": True})


async def post_apply(request: web.Request) -> web.Response:
    """multipart: template_id, clip files (several), text (one caption per line), tweak (plain-word changes)."""
    if not request.content_type.startswith("multipart/"):
        raise web.HTTPBadRequest(text="Send at least one clip.")
    job_dir, clips, fields = _job_dir(), [], {}
    reader = await request.multipart()
    async for part in reader:
        if part.name == "clips" and part.filename:
            path = job_dir / f"{len(clips):02d}_{_safe_name(part.filename)}"
            with path.open("wb") as f:
                while chunk := await part.read_chunk(1 << 20):
                    f.write(chunk)
            clips.append(str(path))
        elif part.name:
            fields[part.name] = await part.text()
    tpl = await storage.get_template(_user(), template_id=int(fields.get("template_id") or 0))
    if not tpl:
        raise web.HTTPNotFound(text="Template not found")
    if not clips:
        raise web.HTTPBadRequest(text="Send at least one clip.")
    bp = (await storage.get_blueprint(tpl.blueprint_id)).blueprint
    adjustments = await parse_feedback(fields["tweak"]) if fields.get("tweak", "").strip() else None
    texts = fields.get("text", "").splitlines()
    job = _start(request.app, "render", lambda j: _render_job(request.app, j, bp, clips, job_dir, template_id=tpl.id, texts=texts,
                                                              adjustments=adjustments, use_preferences=True, version=1))
    return web.json_response({"job": job.id})


def _render(request: web.Request) -> dict:
    render = request.app[RENDERS].get(request.match_info["job_id"])
    if not render:
        raise web.HTTPNotFound(text="That edit is no longer open; render it again.")
    return render


async def post_change(request: web.Request) -> web.Response:
    """Re-render a finished edit with plain-word changes ("darker, faster cuts, more zoom"), as after a bot render."""
    render = _render(request)
    text = str((await request.json()).get("text", ""))
    adj = await parse_feedback(text)
    if not adj:
        raise web.HTTPBadRequest(text="I didn't catch a change. Try e.g. “brighter”, “more slow-mo”, “fewer cuts”, “more effects”.")
    bp = (await storage.get_blueprint(render["blueprint_id"])).blueprint
    await storage.add_weights(_user(), adj)
    job = _start(request.app, "render", lambda j: _render_job(request.app, j, bp, render["clips"], render["dir"],
                                                              template_id=render["template_id"], texts=None, adjustments=adj,
                                                              use_preferences=False, version=render["version"] + 1))
    return web.json_response({"job": job.id, "applying": adj})


async def post_rate(request: web.Request) -> web.Response:
    render = _render(request)
    body = await request.json()
    aspect, value = body.get("aspect"), body.get("value")
    if aspect not in RATINGS or value not in RATINGS[aspect]:
        raise web.HTTPBadRequest(text="Unknown rating")
    await storage.add_rating(user_id=_user(), blueprint_id=render["blueprint_id"], master_path=render["master"],
                             aspect=aspect, value=value, scores=render["scores"])
    if aspect == "shake" and value:
        await storage.add_weights(_user(), {"effects": -0.5 * value})  # less/more shake next time
    return web.json_response({"ok": True})


async def get_media(request: web.Request) -> web.FileResponse:
    root = get_settings().data_dir.resolve()
    path = (root / request.match_info["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)  # handles Range requests, so the video can seek


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as e:
        if request.path.startswith("/api/") and e.status >= 400:
            return web.json_response({"error": e.text}, status=e.status)
        raise
    except ValueError as e:  # bad ids / JSON
        return web.json_response({"error": str(e)}, status=400)


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD, middlewares=[errors])
    app[JOBS], app[TASKS], app[REVIEWS], app[RENDERS] = {}, set(), {}, {}

    async def startup(_app):
        await storage.init_db()

    app.on_startup.append(startup)
    app.router.add_get("/", index)
    app.router.add_static("/static", STATIC)
    app.router.add_get("/media/{path:.+}", get_media)
    app.router.add_get("/api/jobs/{job_id}", get_job)
    app.router.add_post("/api/clone", post_clone)
    app.router.add_get("/api/blueprints/{bp_id:\\d+}", get_blueprint)
    app.router.add_get("/api/blueprints/{bp_id:\\d+}/video", get_video)
    app.router.add_get("/api/blueprints/{bp_id:\\d+}/graph", get_graph)
    app.router.add_post("/api/blueprints/{bp_id:\\d+}/graph", post_graph)
    app.router.add_post("/api/blueprints/{bp_id:\\d+}/moves", post_moves)
    app.router.add_post("/api/blueprints/{bp_id:\\d+}/moves/ok", post_moves_ok)
    app.router.add_get("/api/templates", list_templates)
    app.router.add_post("/api/templates", post_template)
    app.router.add_patch("/api/templates/{tpl_id:\\d+}", patch_template)
    app.router.add_delete("/api/templates/{tpl_id:\\d+}", delete_template)
    app.router.add_post("/api/apply", post_apply)
    app.router.add_post("/api/renders/{job_id}/change", post_change)
    app.router.add_post("/api/renders/{job_id}/rate", post_rate)
    return app


def run(host: str = "127.0.0.1", port: int = 8080) -> None:
    web.run_app(create_app(), host=host, port=port)
