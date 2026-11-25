"""Editor API: open a finished edit as a project, change it, render it again, export it.

Projects live next to their render (data/jobs/web/<id>/project.json), so they survive a restart. Every change
is saved with its own undo step. Only files the project lists are served (its clips, music and renders).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from aiohttp import web

from .. import export, labels, project as P, storage
from ..config import get_settings
from ..media import audio_duration, ffmpeg, probe
from ..render import RenderOptions, render
from ..tweak import apply_adjustments, effect_intensity

THUMB_W = 160


def projects_root() -> Path:
    return get_settings().dir("jobs", "web")


def folder(pid: str) -> Path:
    if not pid.isalnum():
        raise web.HTTPNotFound(text="No such project")
    f = projects_root() / pid
    if not (f / P.PROJECT_FILE).is_file():
        raise web.HTTPNotFound(text="No such project. Edits rendered before the editor existed can't be opened; render again.")
    return f


def media_list(p: P.EditProject) -> list[str]:
    """Every file the project may serve: clips, music, renders (by index in this list)."""
    return [*p.clips, *([p.music_path()] if p.music_path() else []), *p.renders]


def view(pid: str, p: P.EditProject) -> dict:
    files = media_list(p)
    url = lambda path: f"/api/projects/{pid}/file/{files.index(path)}" if path in files else None  # noqa: E731
    bp = p.blueprint
    clips = []
    for c in p.clips:
        try:
            pr = probe(c)
            clips.append({"url": url(c), "name": Path(c).name, "duration": pr.duration, "width": pr.display_size[0],
                          "height": pr.display_size[1]})
        except Exception:
            clips.append({"url": None, "name": Path(c).name, "duration": 0, "width": 0, "height": 0, "missing": True})
    moves = labels.moves(bp)
    shots = []
    for i, (seg, src) in enumerate(zip(bp.segments, p.shots)):
        shots.append({
            "start": seg.start, "end": seg.end, "clip": p.clips.index(src.clip) if src.clip in p.clips else -1,
            "src_start": src.src_start, "center": list(src.center), "speed": [{"at": s.at, "speed": s.speed} for s in seg.speed],
            "transition": seg.transition_in, "own_footage": src.content_match >= 0.8,
            "moves": [{"n": m.n, "kind": m.kind, "t": m.t, "label": labels.KIND_WORDS[m.kind]} for m in moves
                      if seg.start - 1e-4 <= m.t < seg.end - 1e-4],
        })
    a = bp.audio
    return {
        "id": pid, "name": p.name, "duration": p.duration, "fps": p.fps, "width": bp.output.width, "height": bp.output.height,
        "shots": shots, "clips": clips, "texts": [t.model_dump() for t in bp.texts],
        "music": url(p.music_path()) if p.music_path() else None, "custom_music": bool(p.music),
        "renders": [{"url": url(r), "name": Path(r).name} for r in p.renders if Path(r).is_file()],
        "settings": p.settings.model_dump(), "look": p.look,
        "beats": a.beats, "hits": a.hits, "has_graph": a.graph is not None, "markers": _markers(bp),
        "transitions": ["cut", "flash", "zoom", "panel", "portal", "cutout", "silhouette"],
        "move_kinds": {k: labels.KIND_WORDS[k] for k in ("punch", "shake", "whip", "push", "flash", "slowmo", "glow")},
        "formats": {"capcut": "CapCut kit (zip)", **{k: v[1] for k, v in export.FORMATS.items()}, "bundle": "Everything + media (zip)"},
        **P.can_step(folder(pid)),
    }


def _markers(bp) -> dict:
    from .app import markers  # the template page's graph markers, for the same graph here

    return markers(bp)


def _load(request: web.Request) -> tuple[str, Path, P.EditProject]:
    pid = request.match_info["pid"]
    f = folder(pid)
    return pid, f, P.load(f)


async def list_projects(request: web.Request) -> web.Response:
    out = []
    for f in sorted(projects_root().glob(f"*/{P.PROJECT_FILE}"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            p = P.load(f.parent)
        except Exception:
            continue
        cover = next((r for r in reversed(p.renders) if Path(r).is_file()), None)
        out.append({"id": f.parent.name, "name": p.name, "duration": p.duration, "shots": len(p.shots),
                    "renders": len(p.renders), "updated": f.stat().st_mtime,
                    "cover": f"/api/projects/{f.parent.name}/thumb?render=1&t=0.5" if cover else None})
    return web.json_response(out)


async def get_project(request: web.Request) -> web.Response:
    pid, _, p = _load(request)
    return web.json_response(view(pid, p))


async def get_file(request: web.Request) -> web.FileResponse:
    _, _, p = _load(request)
    files = media_list(p)
    n = int(request.match_info["n"])
    if not 0 <= n < len(files) or not Path(files[n]).is_file():
        raise web.HTTPNotFound(text="That file is no longer on disk")
    return web.FileResponse(files[n])


async def get_graph(request: web.Request) -> web.Response:
    _, _, p = _load(request)
    if p.blueprint.audio.graph is None:
        raise web.HTTPNotFound(text="No audio graph stored for this edit")
    return web.json_response(p.blueprint.audio.graph.model_dump(mode="json"), headers={"Cache-Control": "private, max-age=3600"})


async def get_thumb(request: web.Request) -> web.FileResponse:
    """A small JPEG of a clip (clip=N) or the last render (render=1) at t seconds, cached in thumbs/."""
    _, f, p = _load(request)
    q = request.query
    t = max(0.0, float(q.get("t", 0)))
    if q.get("render"):
        src = next((r for r in reversed(p.renders) if Path(r).is_file()), None)
        key = "render"
    else:
        n = int(q.get("clip", 0))
        src = p.clips[n] if 0 <= n < len(p.clips) else None
        key = f"c{n}"
    if not src or not Path(src).is_file():
        raise web.HTTPNotFound(text="No such clip")
    out = f / "thumbs" / f"{key}_{round(t * 30)}_{Path(src).stat().st_mtime_ns % 10**6}.jpg"  # one per frame at 30 fps
    if not out.is_file():
        out.parent.mkdir(exist_ok=True)
        await asyncio.to_thread(ffmpeg, ["-ss", f"{t:.2f}", "-i", src, "-frames:v", 1, "-vf", f"scale={THUMB_W}:-2", "-q:v", 5, out])
    return web.FileResponse(out, headers={"Cache-Control": "private, max-age=86400"})


async def post_edit(request: web.Request) -> web.Response:
    """One change: {"op": "cut"|"split"|"remove"|"swap"|"shot"|"texts"|"moves"|"look"|"settings", ...}."""
    pid, f, p = _load(request)
    body = await request.json()
    op = body.get("op")
    done = []
    if op == "cut":
        p = P.move_cut(p, int(body["cut"]), float(body["t"]))
    elif op == "split":
        p = P.split(p, int(body["shot"]), float(body["t"]))
    elif op == "remove":
        p = P.remove(p, int(body["shot"]))
    elif op == "swap":
        p = P.swap(p, int(body["shot"]), int(body["with"]))
    elif op == "shot":
        clip = body.get("clip")
        p = P.set_shot(p, int(body["shot"]), clip=p.clips[int(clip)] if clip is not None else None,
                       src_start=body.get("src_start"), center=body.get("center"), speed=body.get("speed"),
                       transition=body.get("transition"))
    elif op == "texts":
        p = P.set_texts(p, list(body.get("texts", [])))
    elif op == "moves":  # the review's plain words: "remove 3", "add 2.4 punch", "4 shake"
        bp, done = labels.apply_corrections(p.blueprint, str(body.get("text", "")))
        if not done:
            raise web.HTTPBadRequest(text="I didn't understand that. Try “remove 3”, “add 2.4 punch” or “4 shake”.")
        p = p.model_copy(update={"blueprint": bp})
    elif op == "look":
        p = P.set_look(p, dict(body.get("look", {})))
    elif op == "settings":
        p = p.model_copy(update={"settings": P.RenderSettings(**{**p.settings.model_dump(), **dict(body.get("settings", {}))})})
    elif op == "rename":
        p = p.model_copy(update={"name": str(body.get("name", "")).strip()[:80] or p.name})
    else:
        raise web.HTTPBadRequest(text="Unknown change")
    P.save(p, f)
    return web.json_response({**view(pid, p), "done": done})


async def post_step(request: web.Request) -> web.Response:
    pid, f, _ = _load(request)
    p = P.step(f, back=request.match_info["way"] == "undo")
    return web.json_response(view(pid, p))


async def _upload(request: web.Request, dest: Path, field_name: str) -> list[Path]:
    if not request.content_type.startswith("multipart/"):
        raise web.HTTPBadRequest(text="Send a file.")
    from .app import _safe_name

    out = []
    reader = await request.multipart()
    async for part in reader:
        if part.name == field_name and part.filename:
            path = dest / f"{int(time.time() * 1000) % 10**9}_{_safe_name(part.filename)}"
            with path.open("wb") as fh:
                while chunk := await part.read_chunk(1 << 20):
                    fh.write(chunk)
            out.append(path)
    if not out:
        raise web.HTTPBadRequest(text="Send a file.")
    return out


async def post_clips(request: web.Request) -> web.Response:
    pid, f, p = _load(request)
    for path in await _upload(request, f, "clips"):
        try:
            probe(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise web.HTTPBadRequest(text=f"{path.name} isn't a video I can read.")
        p.clips.append(str(path))
    P.save(p, f)
    return web.json_response(view(pid, p))


async def post_music(request: web.Request) -> web.Response:
    """Replace the music (multipart `music`), or JSON {"reset": true} for the template's own track."""
    pid, f, p = _load(request)
    if request.content_type.startswith("multipart/"):
        path = (await _upload(request, f, "music"))[0]
        try:
            if audio_duration(path) <= 0:
                raise ValueError
        except Exception:
            path.unlink(missing_ok=True)
            raise web.HTTPBadRequest(text="That isn't an audio file I can read.")
        p.music = str(path)
    else:
        p.music = None
    P.save(p, f)
    return web.json_response(view(pid, p))


async def _render_job(job, f: Path) -> None:
    p = P.load(f)
    brand = await storage.get_brand(get_settings().web_user_id)
    out = f / f"edit_v{len(p.renders) + 1}.mp4"
    while out.exists():
        out = out.with_name(f"edit_v{int(out.stem.split('_v')[-1]) + 1}.mp4")
    job.stage, job.stage_started = "Rendering your changes" + ("" if p.settings.upscale else " (fast: no AI upscaling)"), time.time()
    job.progress, job.step = 0.0, "Starting…"

    def progress(share: float, what: str) -> None:
        job.progress, job.step = round(share, 4), what

    bp = apply_adjustments(p.blueprint, p.look) if p.look else p.blueprint
    opts = RenderOptions(music_path=p.music_path(), font_path=brand.font_path if brand else None,
                         effect_intensity=effect_intensity(p.look), blur=p.settings.blur, upscale=p.settings.upscale,
                         glows=p.settings.glows, transitions=p.settings.transitions, progress=progress)
    plan = await asyncio.to_thread(P.to_plan, p)
    r = await asyncio.to_thread(render, bp, plan, out, opts)
    p = P.load(f)  # changes made while it rendered are kept
    p.renders.append(str(r.master))
    P.save(p, f, record=False)
    job.progress, job.stage = None, ""
    job.result = {"project": f.name, "video": f"/api/projects/{f.name}/file/{media_list(p).index(str(r.master))}",
                  "notes": plan.warnings}


async def post_render(request: web.Request) -> web.Response:
    from .app import _start

    pid, f, _ = _load(request)
    job = _start(request.app, "render", lambda j: _render_job(j, f))
    return web.json_response({"job": job.id})


async def get_export(request: web.Request) -> web.Response:
    pid, _, p = _load(request)
    fmt = request.match_info["fmt"]
    base = request.query.get("base", "").strip() or None
    if fmt == "bundle":
        data = await asyncio.to_thread(export.bundle, p, base, request.query.get("media", "1") != "0")
        name, ctype = export.stem(p) + "_edit.zip", "application/zip"
    elif fmt == "capcut":
        work = folder(pid) / "capcut_kit"
        shutil.rmtree(work, ignore_errors=True)
        data = await asyncio.to_thread(export.capcut_kit, p, work)
        shutil.rmtree(work, ignore_errors=True)
        name, ctype = export.stem(p) + "_capcut_kit.zip", "application/zip"
    else:
        if fmt not in export.FORMATS:
            raise web.HTTPNotFound(text="Unknown format")
        name, text = await asyncio.to_thread(export.export, p, fmt, base)
        data, ctype = text.encode(), "application/octet-stream"
    return web.Response(body=data, content_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/projects", list_projects)
    r.add_get("/api/projects/{pid}", get_project)
    r.add_get("/api/projects/{pid}/file/{n:\\d+}", get_file)
    r.add_get("/api/projects/{pid}/graph", get_graph)
    r.add_get("/api/projects/{pid}/thumb", get_thumb)
    r.add_post("/api/projects/{pid}/edit", post_edit)
    r.add_post("/api/projects/{pid}/{way:undo|redo}", post_step)
    r.add_post("/api/projects/{pid}/clips", post_clips)
    r.add_post("/api/projects/{pid}/music", post_music)
    r.add_post("/api/projects/{pid}/render", post_render)
    r.add_get("/api/projects/{pid}/export/{fmt}", get_export)
