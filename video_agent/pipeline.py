"""High-level clone/apply operations shared by the CLI and the Telegram bot."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from . import storage
from .analysis import clone_video
from .blueprint import Blueprint, TextOverlay
from .config import get_settings
from .director import EditPlan, plan_edit
from .render import RenderOptions, RenderResult, render
from .render.engine import SYNC_OFFSET
from .tweak import apply_adjustments, effect_intensity

log = logging.getLogger(__name__)

# Style keys learned from repeated feedback and nudged into every new edit for that user.
LEARNED_KEYS = ("brightness", "contrast", "saturation", "warmth", "effects")

_render_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _render_slots
    if _render_slots is None:
        _render_slots = asyncio.Semaphore(get_settings().max_parallel_renders)
    return _render_slots


@dataclass
class ApplyOutcome:
    result: RenderResult
    plan: EditPlan
    blueprint: Blueprint  # the blueprint actually rendered (after tweaks/preferences)


async def clone(user_id: int, video_path: str | Path, origin: str = "") -> tuple[Blueprint, storage.EditBlueprint]:
    bp = await asyncio.to_thread(clone_video, video_path, "", origin)
    row = await storage.save_blueprint(user_id, bp)
    return bp, row


def with_texts(bp: Blueprint, lines: list[str], color: str | None = None) -> Blueprint:
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        return bp
    bp = bp.model_copy(deep=True)
    color = color or "#FFFFFF"
    if bp.texts:
        for t, line in zip(bp.texts, lines):
            t.text = line
        return bp
    slot = bp.duration / len(lines)
    bp.texts = [
        TextOverlay(
            text=line,
            start=round(i * slot + 0.05, 3),
            end=round((i + 1) * slot - 0.05, 3),
            position="center" if i == 0 else "bottom",
            size=84 if i == 0 else 68,
            color=color,
            style="pop",
        )
        for i, line in enumerate(lines)
    ]
    return bp


async def learned_preferences(user_id: int) -> dict[str, float]:
    weights = await storage.get_weights(user_id)
    return {k: max(-0.5, min(0.5, weights.get(k, 0.0) / 6)) for k in LEARNED_KEYS if weights.get(k)}


async def apply(
    user_id: int,
    bp: Blueprint,
    clips: list[str | Path],
    out_path: str | Path,
    *,
    texts: list[str] | None = None,
    music: str | None = None,
    adjustments: dict[str, float] | None = None,
    use_preferences: bool = True,
    blur: str = "auto",
    sync_offset_frames: int = SYNC_OFFSET,
    upscale: bool = True,
    velocity: bool = True,
) -> ApplyOutcome:
    brand = await storage.get_brand(user_id)
    prefs = await learned_preferences(user_id) if use_preferences else {}
    adj = {**prefs, **(adjustments or {})}

    bp = apply_adjustments(bp, adj)
    bp = with_texts(bp, texts or [], (brand.colors[0] if brand and brand.colors else None))
    opts = RenderOptions(
        music_path=music,
        font_path=brand.font_path if brand else None,
        effect_intensity=effect_intensity(adj),
        blur=blur,
        sync_offset_frames=sync_offset_frames,
        upscale=upscale,
        velocity=velocity,
    )
    async with _slots():
        plan = await asyncio.to_thread(plan_edit, bp, clips)
        result = await asyncio.to_thread(render, bp, plan, out_path, opts)
    return ApplyOutcome(result=result, plan=plan, blueprint=bp)
