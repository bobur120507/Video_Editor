"""Caption + hashtag generation in the creator's own voice (/publish)."""

from __future__ import annotations

import logging

from .llm import LLMUnavailable, complete_json

log = logging.getLogger(__name__)

SYSTEM = (
    "You write Instagram Reel captions for a creator, in their own voice. Match the tone, length, "
    "emoji use and slang of their sample captions when given. Open with a hook line, keep it under "
    "60 words, end with a soft call to action. Return JSON: "
    '{"caption": str, "hashtags": [str, ...]} with 8-15 relevant hashtags (without #).'
)


async def generate_caption(topic: str, tone_samples: list[str], template_tags: list[str] | None = None) -> dict:
    samples = "\n---\n".join(tone_samples[:10]) or "(no samples; use a confident, casual tone)"
    prompt = f"Video topic: {topic}\nStyle tags: {', '.join(template_tags or []) or 'none'}\nSample captions:\n{samples}"
    try:
        data = await complete_json(SYSTEM, prompt, max_tokens=1500)
        caption = str(data.get("caption", "")).strip()
        tags = [str(t).lstrip("#").replace(" ", "") for t in data.get("hashtags", []) if str(t).strip()]
        if caption:
            return {"caption": caption, "hashtags": tags[:15], "source": "llm"}
    except (LLMUnavailable, ValueError) as e:
        log.info("Caption LLM unavailable, using template: %s", e)
    words = [w.strip(".,!?").lower() for w in topic.split() if len(w) > 3]
    return {
        "caption": f"{topic.strip()[:1].upper()}{topic.strip()[1:]} 🔥\n\nWatch till the end. Save this for later 👇",
        "hashtags": list(dict.fromkeys([*words[:6], "reels", "viral", "explore"]))[:12],
        "source": "template",
    }


def format_caption(data: dict) -> str:
    tags = " ".join(f"#{t}" for t in data.get("hashtags", []))
    return f"{data['caption']}\n\n{tags}".strip()
