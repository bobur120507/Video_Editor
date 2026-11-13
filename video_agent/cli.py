"""Command line: video-agent clone|apply|templates|caption|bot."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import storage
from .captions import format_caption, generate_caption
from .config import get_settings
from .downloader import download, find_url
from .llm import configured_providers
from .pipeline import apply, clone
from .tweak import parse_feedback

LOCAL_USER = 0  # templates made from the CLI belong to this user id


async def cmd_clone(args) -> None:
    source = args.source
    if find_url(source):
        print("Downloading…")
        source = str(await asyncio.to_thread(download, source, get_settings().dir("downloads")))
    print("Analyzing… (this takes roughly the length of the video)")
    bp, row = await clone(LOCAL_USER, source, origin=args.source)
    name = args.name or Path(source).stem
    await storage.save_template(LOCAL_USER, name, row.id)
    out = get_settings().dir("blueprints") / f"{name}.json"
    out.write_text(bp.model_dump_json(indent=2))
    print(f"\nSaved template '{name}' (blueprint #{row.id}) → {out}\n{bp.summary()}")
    for note in bp.notes:
        print(f"note: {note}")


async def cmd_apply(args) -> None:
    tpl = await storage.get_template(LOCAL_USER, nickname=args.template)
    if not tpl:
        sys.exit(f"No template named '{args.template}'. Run: video-agent templates")
    bp = (await storage.get_blueprint(tpl.blueprint_id)).blueprint
    adjustments = await parse_feedback(args.tweak) if args.tweak else None
    if adjustments:
        print(f"Tweaks: {adjustments}")
    out = Path(args.out or get_settings().dir("outputs") / f"{args.template}_{len(list(get_settings().dir('outputs').glob('*.mp4')))}.mp4")
    print("Rendering…")
    outcome = await apply(LOCAL_USER, bp, args.clips, out, texts=args.text, music=args.music, adjustments=adjustments)
    await storage.bump_template_usage(tpl.id)
    for w in outcome.plan.warnings:
        print(f"warning: {w}")
    if outcome.plan.missing:
        print("Suggested shots to film:\n  " + "\n  ".join(outcome.plan.missing))
    r = outcome.result
    print(f"\nDone: {r.master} ({r.duration:.1f}s)\ncover: {r.cover}\ngrid:  {r.grid}")


async def cmd_templates(args) -> None:
    rows = await storage.list_templates(LOCAL_USER)
    if not rows:
        print("No templates yet. Run: video-agent clone <video or link> --name my_style")
    for t in rows:
        print(f"{t.nickname:30} used {t.usage_count}x  (blueprint #{t.blueprint_id})")


async def cmd_caption(args) -> None:
    brand = await storage.get_brand(LOCAL_USER)
    data = await generate_caption(args.topic, brand.tone_samples if brand else [])
    print(format_caption(data))
    print(f"\n[{data['source']}]")


async def cmd_show(args) -> None:
    tpl = await storage.get_template(LOCAL_USER, nickname=args.template)
    if not tpl:
        sys.exit(f"No template named '{args.template}'.")
    print(json.dumps((await storage.get_blueprint(tpl.blueprint_id)).data, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="video-agent", description="AI video editor agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("clone", help="Extract a reusable edit blueprint from a video file or link")
    p.add_argument("source")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_clone)

    p = sub.add_parser("apply", help="Apply a saved template to your clips")
    p.add_argument("template")
    p.add_argument("clips", nargs="+")
    p.add_argument("--text", action="append", help="On-screen text; repeat for several")
    p.add_argument("--music", help="Audio file to use instead of the template's track")
    p.add_argument("--tweak", help='Natural-language changes, e.g. "darker, more zoom"')
    p.add_argument("--out")
    p.set_defaults(fn=cmd_apply)

    sub.add_parser("templates", help="List saved templates").set_defaults(fn=cmd_templates)

    p = sub.add_parser("show", help="Print a template's blueprint JSON")
    p.add_argument("template")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("caption", help="Write a caption + hashtags")
    p.add_argument("topic")
    p.set_defaults(fn=cmd_caption)

    sub.add_parser("bot", help="Run the Telegram bot")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "bot":
        from .bot import run_bot

        asyncio.run(run_bot())
        return

    async def runner():
        await storage.init_db()
        await args.fn(args)

    if args.cmd in ("caption",) and not configured_providers():
        print("(no LLM key configured — using the built-in template)")
    asyncio.run(runner())


if __name__ == "__main__":
    main()
