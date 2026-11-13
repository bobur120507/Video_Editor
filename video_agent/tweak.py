"""Natural-language edit feedback ("darker, faster cuts, more zoom") -> blueprint adjustments.

Each accepted tweak is also logged into Learning_Weights so future edits lean towards what this
user keeps asking for (feedback capture half of the continuous learning loop).
"""

from __future__ import annotations

import logging
import re

from .blueprint import Blueprint, Segment, SpeedPoint
from .llm import LLMUnavailable, complete_json

log = logging.getLogger(__name__)

KEYS = ["brightness", "contrast", "saturation", "warmth", "cut_pace", "zoom", "slowmo", "effects", "text_size"]

SYSTEM = (
    "You translate a video creator's edit feedback into numeric adjustments. "
    f"Keys: {', '.join(KEYS)}. Each value is between -1 and 1 (0 = no change, 1 = strong increase, "
    "-1 = strong decrease). cut_pace>0 means more/faster cuts; effects is overall effect intensity "
    "(flashes, shake, zoom strength); slowmo>0 adds slow motion on the drop. Only include keys the "
    "feedback asks for."
)

_RULES: list[tuple[str, str, float]] = [
    (r"dark|moody", "brightness", -1), (r"bright|lighter", "brightness", 1),
    (r"punch|more contrast|contrasty", "contrast", 1), (r"flat|less contrast|soft", "contrast", -1),
    (r"vibrant|saturat|colou?rful|more colou?r", "saturation", 1), (r"muted|desaturat|less colou?r|black and white", "saturation", -1),
    (r"warm", "warmth", 1), (r"cool|cold|blue", "warmth", -1),
    (r"faster|quick|more cuts|snappier", "cut_pace", 1), (r"slower cuts|fewer cuts|less cuts|calmer pace", "cut_pace", -1),
    (r"more zoom|zoom in|zooms", "zoom", 1), (r"no zoom|less zoom", "zoom", -1),
    (r"slow[- ]?mo|slow motion", "slowmo", 1), (r"no slow", "slowmo", -1),
    (r"crazier|more effects|intense|hype", "effects", 1), (r"subtle|less effects|clean", "effects", -1),
    (r"bigger text|larger text", "text_size", 1), (r"smaller text", "text_size", -1),
]


async def parse_feedback(text: str) -> dict[str, float]:
    try:
        raw = await complete_json(SYSTEM, f"Feedback: {text}", max_tokens=1000)
        adj = {k: max(-1.0, min(1.0, float(v))) for k, v in raw.items() if k in KEYS and v}
        if adj:
            return adj
    except (LLMUnavailable, ValueError, TypeError) as e:
        log.info("Falling back to keyword feedback parser: %s", e)
    adj: dict[str, float] = {}
    lower = text.lower()
    for pattern, key, value in _RULES:
        if re.search(pattern, lower) and key not in adj:
            adj[key] = value
    return adj


def apply_adjustments(bp: Blueprint, adj: dict[str, float]) -> Blueprint:
    bp = bp.model_copy(deep=True)
    c = bp.color
    if v := adj.get("brightness"):
        c.luma_mean = min(0.85, max(0.1, c.luma_mean + 0.07 * v))
    if v := adj.get("contrast"):
        c.luma_std = min(0.4, max(0.08, c.luma_std * (1 + 0.2 * v)))
    if v := adj.get("saturation"):
        c.saturation = min(0.9, max(0.0, c.saturation * (1 + 0.3 * v)))
    if v := adj.get("warmth"):
        r, g, b = c.rgb_mean
        c.rgb_mean = (min(1, r + 0.035 * v), g, max(0, b - 0.035 * v))
    if v := adj.get("cut_pace"):
        bp.segments = _split_segments(bp) if v > 0 else _merge_segments(bp)
    if v := adj.get("zoom"):
        for i, seg in enumerate(bp.segments):
            if v < 0:
                seg.effects = [e for e in seg.effects if e != "zoom_punch"]
            elif "zoom_punch" not in seg.effects and (i % 2 == 0 or _starts_on_drop(seg, bp)):
                seg.effects.append("zoom_punch")
    if v := adj.get("slowmo"):
        if v < 0:
            for seg in bp.segments:
                seg.speed = [SpeedPoint(at=0.0, speed=1.0)]
        else:
            target = _drop_segment(bp)
            target.speed = [SpeedPoint(at=0.0, speed=1.0), SpeedPoint(at=0.3, speed=0.5)]
        bp.tier = "pro" if any(s.is_slowmo for s in bp.segments) else "standard"
    if v := adj.get("text_size"):
        for t in bp.texts:
            t.size = int(min(140, max(36, t.size * (1 + 0.25 * v))))
    return bp


def effect_intensity(adj: dict[str, float], base: float = 1.0) -> float:
    return min(2.0, max(0.3, base * (1 + 0.4 * adj.get("effects", 0.0))))


def _split_segments(bp: Blueprint) -> list[Segment]:
    """Split shots that span several beats at the beat nearest their middle."""
    out = []
    for seg in bp.segments:
        beats = [b for b in bp.audio.beats if seg.start + 0.3 < b < seg.end - 0.3]
        if seg.duration > 0.9 and beats:
            mid = min(beats, key=lambda b: abs(b - (seg.start + seg.end) / 2))
        elif seg.duration > 1.2:
            mid = (seg.start + seg.end) / 2
        else:
            out.append(seg)
            continue
        a = seg.model_copy(deep=True, update={"end": round(mid, 3)})
        b = seg.model_copy(deep=True, update={"start": round(mid, 3), "transition_in": "cut"})
        b.effects = [e for e in b.effects if e != "flash"]
        out += [a, b]
    return out


def _merge_segments(bp: Blueprint) -> list[Segment]:
    segs = bp.segments
    if len(segs) < 3:
        return segs
    avg = bp.duration / len(segs)
    out: list[Segment] = []
    for seg in segs:
        if out and out[-1].duration < avg and seg.duration < avg and not seg.is_slowmo:
            out[-1] = out[-1].model_copy(update={"end": seg.end})
        else:
            out.append(seg)
    return out


def _starts_on_drop(seg: Segment, bp: Blueprint) -> bool:
    return any(abs(d - seg.start) < 0.25 for d in bp.audio.drops)


def _drop_segment(bp: Blueprint) -> Segment:
    for d in bp.audio.drops:
        for seg in bp.segments:
            if seg.start <= d < seg.end:
                return seg
    return max(bp.segments, key=lambda s: s.duration)
