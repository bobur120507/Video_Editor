"""Filming advice: when the footage cannot do what a reference shot does, say what to film.

Each reference shot is described by what it shows (CLIP, zero-shot) and how its camera moves (its
camera curve). The footage window the Director picked is checked for both; a shot whose content or
camera work is missing becomes one line of a shot list, e.g.
"Shot 4 (0.3 s at 2.2 s): wheel close-up, camera orbits around the car - your footage has no
wheel close-up".
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from . import vision
from .blueprint import Blueprint, CameraCurve, Segment

log = logging.getLogger(__name__)

# What a Reel shot typically shows: CLIP prompt -> words for the shot list.
SHOT_KINDS = {
    "a close-up photo of a car wheel and tyre": "wheel close-up",
    "a close-up photo of a car headlight": "headlight close-up",
    "a photo of the rear of a sports car": "rear of the car",
    "a photo of the front of a sports car": "front of the car",
    "a side view photo of a sports car": "side profile of the car",
    "a photo of a car interior with steering wheel and dashboard": "interior / steering wheel",
    "a photo of a car driving fast on a road": "car driving past",
    "an aerial drone photo of a car from above": "drone shot from above",
    "a close-up portrait photo of a person's face": "face close-up",
    "a full body photo of a person": "person, full body",
    "a photo of a city street at night": "city street at night",
    "a landscape photo": "landscape / scenery",
}
# A velocity template on footage moving less than this share of the reference: most motion is digital.
TOO_STILL = 0.35
SAME_CONTENT = 0.66  # CLIP image similarity (best anywhere in the footage) below which that kind of shot is missing
# Moves a digital zoom/rotation can fake are not worth a reshoot; parallax moves are.
NEEDS_CAMERA = ("orbit", "pan")


@lru_cache
def _kind_embeddings() -> tuple[list[str], np.ndarray]:
    return list(SHOT_KINDS.values()), vision.embed_texts(list(SHOT_KINDS))


def shot_kind(seg: Segment) -> str | None:
    if not seg.clip or not vision.available():
        return None
    names, emb = _kind_embeddings()
    return names[int(np.argmax(emb @ np.array(seg.clip, dtype=np.float32)))]


def camera_work(curve: CameraCurve | None, subject: str | None = None) -> tuple[str, str]:
    """(kind, words) for how the camera moves in a shot: steady, handheld, pan, orbit, push, pull, roll."""
    if curve is None or len(curve) < 3:
        return "steady", "steady camera"
    if len(curve) > 6:  # the first/last frames are whip transitions, not the shot's camera work
        curve = CameraCurve(fps=curve.fps, s=curve.s[2:-2], r=curve.r[2:-2], x=curve.x[2:-2], y=curve.y[2:-2])
    d = curve.describe()
    x = np.array(curve.x)
    s = np.log(np.array(curve.s))
    if d["pan"] > 0.3:
        if subject:
            return "orbit", f"camera moves around the {subject} (orbit or tracking shot)"
        return "pan", f"fast pan to the {'right' if x[-1] < x[0] else 'left'}"
    if d["zoom"] > 1.25:
        return ("push", "camera pushes in toward the subject (walk or drive closer)") if s[-1] > s[0] else (
            "pull", "camera pulls back from the subject")
    if d["rotation_deg"] > 8:
        return "roll", "camera rolls (turn the phone while moving)"
    if d["shake"] > 0.01:
        return "handheld", "handheld, energetic camera"
    return "steady", "steady camera (tripod or stabilised)"


def filming_advice(blueprint: Blueprint, shots: list, footage_curves: list[CameraCurve | None]) -> list[str]:
    """Shot-list lines for reference shots the chosen footage cannot match."""
    out = []
    for i, (shot, foot_curve) in enumerate(zip(shots, footage_curves), 1):
        seg = shot.segment
        if shot.content_match >= 0.8:
            continue  # the reference's own footage: nothing to film
        subject = seg.subject.cls if seg.subject else None
        kind = shot_kind(seg)
        work, words = camera_work(seg.camera, subject)
        reasons = []
        if kind and seg.clip and len(shot.clip.sem):
            if float((shot.clip.sem @ np.array(seg.clip, dtype=np.float32)).max()) < SAME_CONTENT:
                reasons.append(f"your footage has no {kind}")
        foot_work, _ = camera_work(foot_curve, subject)
        if work in NEEDS_CAMERA and foot_work != work:
            reasons.append("your footage has no such camera move" + (" (it is " + foot_work + ")" if foot_work != "steady" else ", it is steady"))
        if not reasons:
            continue
        what = ", ".join(p for p in (kind, words) if p)
        extra = "; film at 60 fps for the slow-mo" if seg.is_slowmo else ""
        out.append(f"Shot {i} ({seg.duration:.1f} s at {seg.start:.1f} s): {what}{extra} - {'; '.join(reasons)}")
    return out


def velocity_advice(blueprint: Blueprint, footage_flow: float) -> str | None:
    """One line when a velocity template meets footage too still to carry it.

    footage_flow is the footage's typical motion (px/frame at 160 px width, as Segment.velocity).
    """
    if blueprint.style != "velocity":
        return None
    ref = [v for s in blueprint.segments for v in s.velocity]
    if not ref:
        return None
    typical = float(np.median(ref))
    if footage_flow >= TOO_STILL * typical:
        return None
    times = typical / max(footage_flow, 1e-3)
    return (f"This template is a velocity edit: it needs footage that moves (driving past, a fast walk-around, a rolling or "
            f"tracking shot). Your clips move about {times:.0f}x less than the reference, so most of the motion will be "
            "digital zoom and pan; real moving shots will look much better.")
