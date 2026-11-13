"""Meta / Instagram Graph API integration — placeholder until API keys are available.

Planned (spec phases 2, 4.1, 6.2, 7): OAuth + webhooks, comment-to-DM lead magnet, hashtag trend
ingestion, Reel publishing, and insights for /audit. Every entry point raises MetaNotConfigured
so callers can show a clear "coming soon" message.
"""

from __future__ import annotations

from .config import get_settings


class MetaNotConfigured(RuntimeError):
    def __init__(self, feature: str):
        super().__init__(f"{feature} needs the Instagram Graph API. Add META_ACCESS_TOKEN to .env once you have it.")


def _require(feature: str) -> None:
    if not get_settings().meta_enabled:
        raise MetaNotConfigured(feature)
    raise NotImplementedError(f"{feature} is not implemented yet.")


async def publish_reel(video_path: str, caption: str) -> str:
    _require("Publishing to Instagram")
    return ""


async def fetch_insights(media_id: str) -> dict:
    _require("Performance analytics (/audit)")
    return {}


async def top_media_for_hashtag(hashtag: str) -> list[dict]:
    _require("Trend analysis (/analyze)")
    return []
