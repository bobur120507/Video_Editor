"""Hybrid LLM layer: Claude, OpenRouter, Gemini and OpenAI behind one call.

Providers are tried in LLM_PROVIDERS order; ones without a key are skipped, and a failing
provider falls through to the next. Every caller has a rule-based fallback for when no
provider is configured.
"""

from __future__ import annotations

import json
import logging
import re

from .config import get_settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


async def _anthropic(system: str, prompt: str, max_tokens: int) -> str:
    import anthropic

    s = get_settings()
    client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
    response = await client.beta.messages.create(
        model=s.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    return "".join(b.text for b in response.content if b.type == "text")


async def _openai_compatible(system: str, prompt: str, max_tokens: int, *, api_key: str, model: str, base_url: str | None) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    response = await client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


async def _openrouter(system: str, prompt: str, max_tokens: int) -> str:
    s = get_settings()
    return await _openai_compatible(
        system, prompt, max_tokens, api_key=s.openrouter_api_key, model=s.openrouter_model, base_url="https://openrouter.ai/api/v1"
    )


async def _openai(system: str, prompt: str, max_tokens: int) -> str:
    s = get_settings()
    return await _openai_compatible(system, prompt, max_tokens, api_key=s.openai_api_key, model=s.openai_model, base_url=None)


async def _gemini(system: str, prompt: str, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key)
    response = await client.aio.models.generate_content(
        model=s.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens),
    )
    return response.text or ""


PROVIDERS = {
    "anthropic": (_anthropic, "anthropic_api_key"),
    "openrouter": (_openrouter, "openrouter_api_key"),
    "gemini": (_gemini, "gemini_api_key"),
    "openai": (_openai, "openai_api_key"),
}


def configured_providers() -> list[str]:
    s = get_settings()
    order = [p.strip() for p in s.llm_providers.split(",") if p.strip() in PROVIDERS]
    return [p for p in order if getattr(s, PROVIDERS[p][1])]


async def complete(system: str, prompt: str, max_tokens: int = 4000) -> str:
    errors = []
    for name in configured_providers():
        fn, _ = PROVIDERS[name]
        try:
            text = await fn(system, prompt, max_tokens)
            if text.strip():
                return text
        except Exception as e:  # provider-specific errors; fall through to the next provider
            log.warning("LLM provider %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise LLMUnavailable("; ".join(errors) or "No LLM provider configured")


async def complete_json(system: str, prompt: str, max_tokens: int = 4000) -> dict:
    text = await complete(system + "\nRespond with a single JSON object and nothing else.", prompt, max_tokens)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMUnavailable(f"LLM did not return JSON: {text[:200]}")
    return json.loads(match.group(0))
