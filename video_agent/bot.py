"""Telegram interface (aiogram 3): /clone, /apply, /templates, /brand, /publish and feedback re-renders."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, FSInputFile, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import meta, storage
from .captions import format_caption, generate_caption
from .config import get_settings
from .downloader import download, find_url, instagram_shortcode
from .media import sha256_file
from .pipeline import ApplyOutcome, apply, clone
from .scoring import style_match
from .tweak import parse_feedback

log = logging.getLogger(__name__)
router = Router()
TEXT = F.text & ~F.text.startswith("/")  # plain text, not a command

BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024

HELP = (
    "🎬 Video Editor Agent\n\n"
    "/clone – send a video (or link) whose editing style you love; I save its editing DNA as a template\n"
    "/apply – pick a template, send your raw clips, get a finished 9:16 edit\n"
    "/templates – browse, rename, delete your templates\n"
    "/brand – brand colours, font and your caption voice\n"
    "/publish – caption + hashtags for your last edit\n"
    "/cancel – stop the current step\n\n"
    "Tips: forward one of my finished videos back to me to reuse its style. "
    "After a render, just type changes like “darker, faster cuts, more zoom”.\n"
    "Coming with Meta API: /analyze, /audit, /solve and auto-publishing."
)


class CloneFlow(StatesGroup):
    waiting_source = State()
    waiting_name = State()


class ApplyFlow(StatesGroup):
    collecting = State()
    waiting_text = State()
    feedback = State()


class BrandFlow(StatesGroup):
    colors = State()
    font = State()
    tone = State()


class RenameFlow(StatesGroup):
    waiting_name = State()


class PublishFlow(StatesGroup):
    waiting_topic = State()


class Tpl(CallbackData, prefix="tpl"):
    action: str
    id: int


class Act(CallbackData, prefix="act"):
    action: str


# ---------------------------------------------------------------- helpers


class TooBig(Exception):
    pass


def _video_of(message: Message):
    if message.video:
        return message.video
    if message.animation:
        return message.animation
    if message.document and (message.document.mime_type or "").startswith("video/"):
        return message.document
    return None


def _job_dir(user_id: int) -> Path:
    return get_settings().dir("jobs", str(user_id), uuid.uuid4().hex[:10])


async def _download(bot: Bot, media, dest: Path) -> Path:
    if (media.file_size or 0) > BOT_API_DOWNLOAD_LIMIT and not get_settings().telegram_api_base:
        raise TooBig()
    path = dest / f"{media.file_unique_id}.mp4"
    try:
        await bot.download(media, destination=path)
    except TelegramBadRequest as e:
        if "too big" in str(e).lower():
            raise TooBig() from e
        raise
    return path


TOO_BIG_TEXT = (
    "That file is over Telegram's 20 MB bot download limit. Send a shorter/compressed clip, "
    "or a link to it. (Running a local Bot API server removes this limit — see README.)"
)


def _done_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Done, render it", callback_data=Act(action="done"))
    return kb.as_markup()


def _after_render_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Caption", callback_data=Act(action="caption"))
    kb.button(text="💾 Save as template", callback_data=Act(action="save_variant"))
    return kb.as_markup()


# ---------------------------------------------------------------- basic commands


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Cancelled.")


@router.message(Command("analyze", "audit", "solve"))
async def meta_only(message: Message):
    feature = {"/analyze": "Trend analysis", "/audit": "Performance analytics", "/solve": "Growth advice"}
    cmd = (message.text or "").split()[0].split("@")[0]
    await message.answer(str(meta.MetaNotConfigured(feature.get(cmd, cmd))) + "\n\nThis will switch on once the Meta API key is added.")


# ---------------------------------------------------------------- /clone


@router.message(Command("clone"))
async def clone_start(message: Message, state: FSMContext):
    await state.set_state(CloneFlow.waiting_source)
    await message.answer("Send me the video whose editing style you want to copy (file or link).")


@router.message(CloneFlow.waiting_source)
async def clone_receive(message: Message, state: FSMContext, bot: Bot):
    await _run_clone(message, state, bot)


async def _run_clone(message: Message, state: FSMContext, bot: Bot, url: str | None = None):
    media = _video_of(message)
    url = url or find_url(message.text)
    if not media and not url:
        await message.answer("Please send a video file or a link to one.")
        return
    job = _job_dir(message.from_user.id)
    status = await message.answer("⏳ Downloading…")
    try:
        path = await _download(bot, media, job) if media else await asyncio.to_thread(download, url, job)
        await status.edit_text("🔬 Analyzing cuts, beats, motion and colour… (about as long as the video)")
        bp, row = await clone(message.from_user.id, path, origin=url or (getattr(media, "file_name", None) or "telegram upload"))
    except TooBig:
        await status.edit_text(TOO_BIG_TEXT)
        return
    except Exception as e:
        log.exception("clone failed")
        await status.edit_text(f"❌ Could not analyze that video: {e}")
        return
    await state.set_state(CloneFlow.waiting_name)
    await state.update_data(blueprint_id=row.id)
    await status.edit_text(f"✅ Editing DNA extracted\n\n{bp.summary()}\n\nName this template (e.g. car_phonk_v1):")


@router.message(CloneFlow.waiting_name, TEXT)
async def clone_name(message: Message, state: FSMContext):
    name = message.text.strip().replace(" ", "_")[:64]
    data = await state.get_data()
    tpl = await storage.save_template(message.from_user.id, name, data["blueprint_id"])
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Apply to new media", callback_data=Tpl(action="apply", id=tpl.id))
    await message.answer(f"Saved as “{name}”.", reply_markup=kb.as_markup())


# ---------------------------------------------------------------- /templates


@router.message(Command("templates"))
async def templates(message: Message, state: FSMContext):
    await state.clear()
    rows = await storage.list_templates(message.from_user.id)
    if not rows:
        await message.answer("No templates yet. Use /clone to create one.")
        return
    kb = InlineKeyboardBuilder()
    for t in rows:
        views = f" • Avg views {t.avg_views:,.0f}" if t.avg_views else ""
        kb.button(text=f"{t.nickname} • used {t.usage_count}x{views}", callback_data=Tpl(action="open", id=t.id))
    kb.adjust(1)
    await message.answer("Your templates:", reply_markup=kb.as_markup())


@router.callback_query(Tpl.filter(F.action == "open"))
async def template_open(cb: CallbackQuery, callback_data: Tpl):
    tpl = await storage.get_template(cb.from_user.id, template_id=callback_data.id)
    if not tpl:
        await cb.answer("Template not found", show_alert=True)
        return
    bp = (await storage.get_blueprint(tpl.blueprint_id)).blueprint
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Apply to new media", callback_data=Tpl(action="apply", id=tpl.id))
    kb.button(text="✏️ Rename", callback_data=Tpl(action="rename", id=tpl.id))
    kb.button(text="🗑 Delete", callback_data=Tpl(action="delete", id=tpl.id))
    kb.adjust(1, 2)
    text = f"{tpl.nickname}\n\n{bp.summary()}"
    if tpl.preview_path and Path(tpl.preview_path).exists():
        await cb.message.answer_video(FSInputFile(tpl.preview_path), caption=text, reply_markup=kb.as_markup())
    else:
        await cb.message.answer(text, reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(Tpl.filter(F.action == "delete"))
async def template_delete(cb: CallbackQuery, callback_data: Tpl):
    ok = await storage.delete_template(cb.from_user.id, callback_data.id)
    await cb.answer("Deleted" if ok else "Not found")
    if ok:
        await cb.message.answer("Template deleted.")


@router.callback_query(Tpl.filter(F.action == "rename"))
async def template_rename(cb: CallbackQuery, callback_data: Tpl, state: FSMContext):
    await state.set_state(RenameFlow.waiting_name)
    await state.update_data(template_id=callback_data.id)
    await cb.message.answer("Send the new name:")
    await cb.answer()


@router.message(RenameFlow.waiting_name, TEXT)
async def template_rename_done(message: Message, state: FSMContext):
    data = await state.get_data()
    name = message.text.strip().replace(" ", "_")[:64]
    ok = await storage.rename_template(message.from_user.id, data["template_id"], name)
    await state.clear()
    await message.answer(f"Renamed to “{name}”." if ok else "Template not found.")


# ---------------------------------------------------------------- /apply


@router.message(Command("apply"))
async def apply_start(message: Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        tpl = await storage.get_template(message.from_user.id, nickname=parts[1].strip())
        if tpl:
            await _begin_collecting(message, state, tpl.blueprint_id, tpl.id, tpl.nickname)
            return
    rows = await storage.list_templates(message.from_user.id)
    if not rows:
        await message.answer("No templates yet. Use /clone first.")
        return
    kb = InlineKeyboardBuilder()
    for t in rows:
        kb.button(text=t.nickname, callback_data=Tpl(action="apply", id=t.id))
    kb.adjust(2)
    await message.answer("Which template?", reply_markup=kb.as_markup())


@router.callback_query(Tpl.filter(F.action == "apply"))
async def apply_pick(cb: CallbackQuery, callback_data: Tpl, state: FSMContext):
    tpl = await storage.get_template(cb.from_user.id, template_id=callback_data.id)
    if not tpl:
        await cb.answer("Template not found", show_alert=True)
        return
    await state.clear()
    await _begin_collecting(cb.message, state, tpl.blueprint_id, tpl.id, tpl.nickname)
    await cb.answer()


async def _begin_collecting(message: Message, state: FSMContext, blueprint_id: int, template_id: int | None, label: str):
    bp = (await storage.get_blueprint(blueprint_id)).blueprint
    await state.set_state(ApplyFlow.collecting)
    await state.update_data(blueprint_id=blueprint_id, template_id=template_id, clips=[], job=str(_job_dir(message.chat.id)))
    await message.answer(
        f"Using “{label}” ({bp.duration:.1f}s, {len(bp.segments)} shots).\n"
        "Send your raw clips now (several is best), then tap Done.",
        reply_markup=_done_keyboard(),
    )


@router.message(ApplyFlow.collecting, F.video | F.document | F.animation)
async def apply_collect(message: Message, state: FSMContext, bot: Bot):
    media = _video_of(message)
    if not media:
        await message.answer("That isn't a video.")
        return
    data = await state.get_data()
    try:
        path = await _download(bot, media, Path(data["job"]))
    except TooBig:
        await message.answer(TOO_BIG_TEXT)
        return
    clips = [*data["clips"], str(path)]
    await state.update_data(clips=clips)
    await message.answer(f"Got clip {len(clips)}. Send more or tap Done.", reply_markup=_done_keyboard())


@router.message(ApplyFlow.collecting, F.text.lower().in_({"done", "/done"}))
@router.callback_query(ApplyFlow.collecting, Act.filter(F.action == "done"))
async def apply_done(event: Message | CallbackQuery, state: FSMContext):
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    data = await state.get_data()
    if not data.get("clips"):
        await message.answer("Send at least one clip first.")
        return
    await state.set_state(ApplyFlow.waiting_text)
    kb = InlineKeyboardBuilder()
    kb.button(text="Skip", callback_data=Act(action="skip_text"))
    await message.answer(
        "On-screen text? Send one line per caption (first line is the hook), or tap Skip.", reply_markup=kb.as_markup()
    )


@router.message(ApplyFlow.waiting_text, TEXT)
@router.callback_query(ApplyFlow.waiting_text, Act.filter(F.action == "skip_text"))
async def apply_render(event: Message | CallbackQuery, state: FSMContext):
    if isinstance(event, CallbackQuery):
        await event.answer()
        message, texts = event.message, []
    else:
        message, texts = event, event.text.splitlines()
    data = await state.get_data()
    bp = (await storage.get_blueprint(data["blueprint_id"])).blueprint
    await _render_and_send(message, state, event.from_user.id, bp, data, texts=texts, adjustments=None, use_preferences=True)


async def _render_and_send(message: Message, state: FSMContext, user_id: int, bp, data: dict, *, texts, adjustments, use_preferences):
    status = await message.answer("🎞 Rendering… (shot planning, colour matching, effects, encode)")
    version = data.get("version", 0) + 1
    out = Path(data["job"]) / f"edit_v{version}.mp4"
    try:
        outcome: ApplyOutcome = await apply(
            user_id, bp, data["clips"], out, texts=texts, adjustments=adjustments, use_preferences=use_preferences
        )
    except Exception as e:
        log.exception("render failed")
        await status.edit_text(f"❌ Render failed: {e}")
        return
    rendered_row = await storage.save_blueprint(user_id, outcome.blueprint)
    r = outcome.result
    notes = [*outcome.plan.warnings]
    if outcome.plan.missing:
        notes.append("Shots worth filming next time:\n" + "\n".join(outcome.plan.missing))
    if r.preview != r.master:
        notes.append(f"Full-quality master is too big for Telegram; saved on the server: {r.master}")
    caption = f"✅ v{version} • {r.duration:.1f}s • 1080x1920 H.264"
    sent = await message.answer_video(FSInputFile(r.preview), caption=caption, supports_streaming=True, width=1080, height=1920)
    await message.answer_media_group([InputMediaPhoto(media=FSInputFile(r.cover), caption="Cover • 1:1 grid preview"), InputMediaPhoto(media=FSInputFile(r.grid))])
    await status.delete()
    fp = await storage.add_fingerprint(
        user_id=user_id,
        blueprint_id=rendered_row.id,
        file_sha256=sha256_file(r.master),
        tg_file_unique_id=sent.video.file_unique_id if sent.video else None,
        tg_chat_id=sent.chat.id,
        tg_message_id=sent.message_id,
        master_path=str(r.master),
    )
    if data.get("template_id"):
        await storage.bump_template_usage(data["template_id"], preview_path=str(r.preview))
    await state.set_state(ApplyFlow.feedback)
    await state.update_data(version=version, rendered_blueprint_id=rendered_row.id, fingerprint_id=fp.id)
    tail = ("\n\n" + "\n".join(notes)) if notes else ""
    await message.answer(
        "Want changes? Just type them, e.g. “darker, faster cuts, more zoom, bigger text”." + tail,
        reply_markup=_after_render_keyboard(),
    )
    reference = outcome.blueprint.source.path
    if reference and Path(reference).exists():
        asyncio.create_task(_send_match_score(message, reference, r.master))


async def _send_match_score(message: Message, reference: str, output: Path):
    """Measure how closely the edit matches the reference (runs after delivery; takes ~a video length)."""
    try:
        report = await asyncio.to_thread(style_match, reference, output)
        await message.answer("📏 " + report.text())
    except Exception:
        log.exception("match scoring failed")


@router.message(ApplyFlow.feedback, TEXT)
async def apply_feedback(message: Message, state: FSMContext):
    adj = await parse_feedback(message.text)
    if not adj:
        await message.answer("I didn't catch a change. Try e.g. “brighter”, “more slow-mo”, “fewer cuts”, “more effects”.")
        return
    data = await state.get_data()
    bp = (await storage.get_blueprint(data["rendered_blueprint_id"])).blueprint
    await storage.add_weights(message.from_user.id, adj)
    pretty = ", ".join(f"{k} {'+' if v > 0 else '−'}" for k, v in adj.items())
    await message.answer(f"Applying: {pretty}")
    await _render_and_send(message, state, message.from_user.id, bp, data, texts=None, adjustments=adj, use_preferences=False)


@router.callback_query(Act.filter(F.action == "save_variant"))
async def save_variant(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("rendered_blueprint_id"):
        await cb.answer("Nothing rendered yet", show_alert=True)
        return
    await state.set_state(CloneFlow.waiting_name)
    await state.update_data(blueprint_id=data["rendered_blueprint_id"])
    await cb.message.answer("Name for this version as a template:")
    await cb.answer()


# ---------------------------------------------------------------- /publish (caption only until Meta is connected)


@router.message(Command("publish"))
@router.callback_query(Act.filter(F.action == "caption"))
async def publish_start(event: Message | CallbackQuery, state: FSMContext):
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    data = await state.get_data()
    await state.set_state(PublishFlow.waiting_topic)
    await state.update_data(fingerprint_id=data.get("fingerprint_id"))
    await message.answer("What's the video about? (one sentence, e.g. “BMW M4 night drive in Tashkent”)")


@router.message(PublishFlow.waiting_topic, TEXT)
async def publish_caption(message: Message, state: FSMContext):
    brand = await storage.get_brand(message.from_user.id)
    await message.bot.send_chat_action(message.chat.id, "typing")
    result = await generate_caption(message.text, brand.tone_samples if brand else [])
    text = format_caption(result)
    data = await state.get_data()
    if data.get("fingerprint_id"):
        await storage.update_fingerprint(data["fingerprint_id"], caption=text)
    await message.answer(text)
    try:
        await meta.publish_reel("", text)
    except meta.MetaNotConfigured:
        await message.answer(
            "Auto-publishing turns on once the Meta API key is added. For now, save the video above and post it with this caption."
        )
    await state.set_state(ApplyFlow.feedback if data.get("rendered_blueprint_id") else None)


# ---------------------------------------------------------------- /brand


@router.message(Command("brand"))
async def brand_start(message: Message, state: FSMContext):
    await state.set_state(BrandFlow.colors)
    await message.answer("Brand colours as hex codes, e.g. #FF0055 #FFFFFF (first one is used for on-screen text). Or send “skip”.")


@router.message(BrandFlow.colors, TEXT)
async def brand_colors(message: Message, state: FSMContext):
    colors = re.findall(r"#?[0-9a-fA-F]{6}\b", message.text)
    if colors:
        await storage.upsert_brand(message.from_user.id, colors=[f"#{c.lstrip('#').upper()}" for c in colors])
    await state.set_state(BrandFlow.font)
    await message.answer("Send your brand font as a .ttf/.otf file (as a document), or “skip”.")


@router.message(BrandFlow.font, F.document)
async def brand_font(message: Message, state: FSMContext, bot: Bot):
    name = (message.document.file_name or "").lower()
    if not name.endswith((".ttf", ".otf")):
        await message.answer("Please send a .ttf or .otf file, or “skip”.")
        return
    dest = get_settings().dir("brand", str(message.from_user.id)) / Path(name).name
    await bot.download(message.document, destination=dest)
    await storage.upsert_brand(message.from_user.id, font_path=str(dest))
    await _brand_ask_tone(message, state)


@router.message(BrandFlow.font, TEXT)
async def brand_font_skip(message: Message, state: FSMContext):
    await _brand_ask_tone(message, state)


async def _brand_ask_tone(message: Message, state: FSMContext):
    await state.set_state(BrandFlow.tone)
    await message.answer("Paste 3–10 of your past captions (separate them with a blank line) so I can write like you. Or “skip”.")


@router.message(BrandFlow.tone, TEXT)
async def brand_tone(message: Message, state: FSMContext):
    if message.text.strip().lower() != "skip":
        samples = [s.strip() for s in message.text.split("\n\n") if s.strip()]
        await storage.upsert_brand(message.from_user.id, tone_samples=samples)
    await state.clear()
    await message.answer("✅ Brand saved. It's used for text colour, font and captions.")


# ---------------------------------------------------------------- no active step: forwards and links


@router.message(StateFilter(None), F.video | F.document | F.animation)
async def loose_video(message: Message, state: FSMContext, bot: Bot):
    media = _video_of(message)
    if media and message.forward_origin:
        fp = await storage.find_fingerprint(message.from_user.id, tg_file_unique_id=media.file_unique_id)
        if fp:
            await _begin_collecting(message, state, fp.blueprint_id, None, "the style of that video")
            return
    await _run_clone(message, state, bot)


@router.message(StateFilter(None), F.text.regexp(r"https?://"))
async def loose_link(message: Message, state: FSMContext, bot: Bot):
    url = find_url(message.text)
    code = instagram_shortcode(url) if url else None
    if code:
        fp = await storage.find_fingerprint(message.from_user.id, ig_shortcode=code)
        if fp:
            await _begin_collecting(message, state, fp.blueprint_id, None, "the style of that Reel")
            return
    await _run_clone(message, state, bot, url=url)


# ---------------------------------------------------------------- entry point


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first (copy .env.example).")
    logging.getLogger().setLevel(logging.INFO)  # the CLI configured WARNING for other commands
    await storage.init_db()

    session = None
    if settings.telegram_api_base:
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_base, is_local=True))
    bot = Bot(settings.telegram_bot_token, session=session)

    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        fsm = RedisStorage.from_url(settings.redis_url)
    else:
        fsm = MemoryStorage()
    dp = Dispatcher(storage=fsm)
    dp.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(command="clone", description="Copy a video's editing style"),
            BotCommand(command="apply", description="Edit your clips with a template"),
            BotCommand(command="templates", description="Your saved styles"),
            BotCommand(command="brand", description="Brand colours, font, voice"),
            BotCommand(command="publish", description="Caption + hashtags"),
            BotCommand(command="cancel", description="Stop current step"),
            BotCommand(command="help", description="How it works"),
        ]
    )
    log.info("Bot started")
    await dp.start_polling(bot)
