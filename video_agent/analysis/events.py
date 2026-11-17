"""Edit events: what the editor *did*, separated from how the original camera moved.

A literal camera curve copied onto different footage reproduces the reference's camera work (orbit
shots, handheld drift), which static footage can never match and which reads as random wobble.
What transfers is the edit's intent: whip transitions in and out of shots, punch-zooms and shake
jolts on the music's hits, and the overall push. These are extracted per shot and timed to the hits.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import librosa
import numpy as np

from ..blueprint import CameraCurve, EditEvent, Segment

log = logging.getLogger(__name__)

WHIP_SPEED = 0.04  # frame widths per frame at a shot edge
WHIP_ZOOM = 0.08  # log-zoom per frame at a shot edge
PUNCH_ZOOM = 0.06  # log-zoom per frame inside a shot
JOLT = 0.012  # frame widths: high-passed position offset that counts as a shake jolt
SNAP_FRAMES = 2  # a move this close to a hit is the editor syncing to it
SOFT = 0.6  # reference frames softer than this (vs the shot's sharpest) are too blurred to track reliably
CARRY = 0.5  # strength of the whip-in added to continue a whip-out across the cut

HOP = 256  # 11.6 ms at 22.05 kHz: finer than a video frame
MAX_BACKTRACK = 0.06  # seconds from the onset peak back to where the sound starts


def detect_hits(wav_path: str | Path, keep: float = 0.6) -> tuple[list[float], list[str]]:
    """Strong drum hits as (times, kinds): the moments an editor syncs moves to.

    Times are where each sound *starts* (its attack), not where its loudness peaks: the peak
    comes ~45 ms (1.5 frames) later, and editors cut on the attack. Kinds: "kick" (kick drum,
    808 bass), "snare" (snare, clap, hats) or "hit" (both at once / other).
    """
    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    peaks = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, units="frames")
    if len(peaks) == 0:
        return [], []
    starts = np.maximum(librosa.onset.onset_backtrack(peaks, env), peaks - int(MAX_BACKTRACK * sr / HOP))

    spec = np.abs(librosa.stft(y, n_fft=1024, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    flux = np.maximum(0.0, np.diff(spec, axis=1, prepend=spec[:, :1]))
    low = flux[freqs < 150].sum(0)
    high = flux[(freqs >= 1500) & (freqs < 6000)].sum(0)
    low, high = low / (np.quantile(low, 0.95) + 1e-9), high / (np.quantile(high, 0.95) + 1e-9)

    strength = env[peaks]
    floor = np.quantile(strength, 1 - keep)
    times, kinds = [], []
    for p, s0, st in zip(peaks, starts, strength):
        if st < floor:
            continue
        a, b = max(0, p - 1), min(len(low), p + 2)
        lo, hi = float(low[a:b].max()), float(high[a:b].max())
        kinds.append("kick" if lo > 1.3 * hi else "snare" if hi > 1.3 * lo else "hit")
        times.append(round(float(librosa.frames_to_time(s0, sr=sr, hop_length=HOP)), 3))
    return times, kinds


def sync_target(cuts: list[float], hits: list[float], kinds: list[str], fps: float) -> str:
    """Which drum the editor cut on ("kick", "snare", "hit"), or "" when the cuts don't follow hits."""
    if not cuts or not hits:
        return ""
    h = np.array(hits)
    on = [kinds[int(np.argmin(np.abs(h - c)))] for c in cuts if np.min(np.abs(h - c)) <= 1.5 / fps]
    if len(on) < max(2, len(cuts) // 2):
        return ""
    return max(set(on), key=on.count)


def _snap(t: float, hits: list[float], fps: float) -> float | None:
    """The hit within SNAP_FRAMES of t, or None."""
    if not hits:
        return None
    nearest = min(hits, key=lambda h: abs(h - t))
    return nearest if abs(nearest - t) <= SNAP_FRAMES / fps + 1e-6 else None


def _frame_t(t: float, fps: float) -> float:
    return round(round(t * fps) / fps, 4)


def extract_events(seg: Segment, hits: list[float]) -> list[EditEvent]:
    c: CameraCurve | None = seg.camera
    if c is None or len(c) < 4:
        return []
    fps = c.fps
    ls, r, x, y = np.log(np.array(c.s)), np.array(c.r), np.array(c.x), np.array(c.y)
    vs, vr = np.diff(ls), np.degrees(np.diff(r))
    vx, vy = np.diff(x), np.diff(y)
    vt = np.hypot(vx, vy)
    n = len(vs)
    soft = np.array(seg.blur[: len(c)] + [0.0] * max(0, len(c) - len(seg.blur)))
    events: list[EditEvent] = []

    # Whips: violent movement on the first / last frames of a shot (the cut hides inside the blur).
    for kind, idx in (("whip_in", range(min(2, n))), ("whip_out", range(max(0, n - 2), n))):
        best = max(idx, key=lambda i: vt[i] + abs(vs[i]))
        speed, zoom = float(vt[best]), float(abs(vs[best]))
        if speed > WHIP_SPEED or zoom > WHIP_ZOOM:
            norm = math.hypot(vx[best], vy[best]) or 1.0
            events.append(
                EditEvent(
                    kind=kind,
                    t=0.0 if kind == "whip_in" else _frame_t(seg.duration, fps),
                    strength=min(1.0, max(0.3, speed / 0.15 + zoom / 0.3)),
                    dx=float(vx[best] / norm),
                    dy=float(vy[best] / norm),
                    zoom=float(np.sign(vs[best])),
                )
            )

    # Punches: zoom spikes inside the shot that sit on a music hit (editors punch on hits; spikes
    # between hits are the original camera's movement). One punch per hit, the strongest.
    punch_hits: dict[float, int] = {}
    for i in range(2, max(2, n - 2)):
        if abs(vs[i]) > PUNCH_ZOOM and abs(vs[i]) >= abs(vs[i - 1]) and abs(vs[i]) >= abs(vs[min(i + 1, n - 1)]):
            hit = _snap(seg.start + i / fps, hits, fps)
            if hit is not None and (hit not in punch_hits or abs(vs[i]) > abs(vs[punch_hits[hit]])):
                punch_hits[hit] = i
    for hit, i in sorted(punch_hits.items()):
        t = _frame_t(hit - seg.start, fps)
        if 0 < t < seg.duration:
            events.append(EditEvent(kind="punch", t=t, strength=min(0.25, abs(float(vs[i])) * 2.0), zoom=float(np.sign(vs[i]))))

    events += _jolts(seg, x, y, vr, soft, hits, fps, punch_hits)

    # Push: the shot's overall zoom trend, ignoring the edges where whips live.
    body = ls[2:-2] if len(ls) > 6 else ls
    if len(body) > 3:
        rate = float(np.polyfit(np.arange(len(body)) / fps, body, 1)[0])
        if abs(rate) > 0.03:
            # Capped at 1.6x over the shot: a digital push-in/out an editor would do on static
            # footage; beyond that the picture goes soft (and it was the camera flying, not the edit).
            cap = min(0.45, math.log(1.6) / max(seg.duration, 0.1))
            events.append(EditEvent(kind="push", t=0.0, duration=seg.duration, strength=max(-cap, min(cap, rate))))
    return events


def _jolts(seg: Segment, x: np.ndarray, y: np.ndarray, vr: np.ndarray, soft: np.ndarray, hits: list[float], fps: float,
           punch_hits: dict[float, int]) -> list[EditEvent]:
    """Shake as editors do it: a short jolt on a hit that dies away, not a constant wobble.

    The position is high-passed (the camera's own smooth flying/orbit motion removed) and only
    frames sharp enough to track count, so tracking noise in blurred frames is not read as shake.
    A jolt must shake back and forth (direction flips) to count; a single shove is camera motion.
    """
    if len(x) < 7:
        return []
    k = np.ones(5) / 5
    hx = x - np.convolve(np.pad(x, 2, mode="edge"), k, mode="valid")
    hy = y - np.convolve(np.pad(y, 2, mode="edge"), k, mode="valid")
    amp = np.hypot(hx, hy)
    amp[soft > SOFT] = 0.0
    out: list[EditEvent] = []
    used: set[float] = set()
    for i in range(1, len(amp) - 1):
        if amp[i] < JOLT or amp[i] < amp[i - 1] or amp[i] < amp[i + 1]:
            continue
        hit = _snap(seg.start + i / fps, hits, fps)
        if hit is None or hit in used:
            continue
        tail = hx[i : i + 6]
        if np.sum(np.diff(np.sign(tail[np.abs(tail) > 1e-4])) != 0) < 1:
            continue
        # How long it rings: until the offset falls under a third of the peak.
        end = i + 1
        while end < len(amp) and amp[end] > amp[i] / 3:
            end += 1
        used.add(hit)
        strength = min(1.0, float(amp[i]) / 0.04) * (0.5 if hit in punch_hits else 1.0)  # punch already hits here
        t = _frame_t(hit - seg.start, fps)
        if 0 <= t < seg.duration:
            out.append(
                EditEvent(
                    kind="shake",
                    t=t,
                    duration=round(min(0.5, max(0.15, (end - i) / fps)), 3),
                    strength=round(strength, 3),
                    rot=round(min(3.0, float(np.abs(vr[max(0, i - 1) : i + 3]).max(initial=0.0)) / 2), 2),
                )
            )

    # Blur impacts: in heavily blurred edits the shake on a hit is a burst of smear (the picture is
    # too blurred to track): the frame goes soft right on the hit and clears again within a few frames.
    for hit in hits:
        i = round((hit - seg.start) * fps)
        if hit in used or not 2 <= i < len(soft) - 2:
            continue
        before = soft[max(0, i - 3) : i].min()
        peak = soft[i : i + 3].max()
        after = soft[i + 3 : i + 10]
        if peak > SOFT and peak - before > 0.35 and len(after) and after.min() < peak - 0.3:
            ring = int(np.argmax(after < peak - 0.3)) + 3
            used.add(hit)
            out.append(EditEvent(kind="shake", t=_frame_t(hit - seg.start, fps), duration=round(min(0.4, max(0.12, ring / fps)), 3),
                                 strength=0.6, rot=1.0))
    return out


def link_whips(segments: list[Segment]) -> None:
    """Make motion flow across cuts: a whip-out continues into the next shot's whip-in.

    Both halves of a whip transition share one direction and speed, so the movement carries over
    the cut instead of resetting; a whip-out with no whip-in after it gets a lighter one.
    """
    for a, b in zip(segments, segments[1:]):
        out = next((e for e in a.events if e.kind == "whip_out"), None)
        if out is None:
            continue
        inn = next((e for e in b.events if e.kind == "whip_in"), None)
        if inn is None:
            b.events.insert(0, EditEvent(kind="whip_in", t=0.0, strength=round(out.strength * CARRY, 3), dx=out.dx, dy=out.dy,
                                         zoom=out.zoom, source="carried"))
            continue
        dx, dy = out.dx + inn.dx, out.dy + inn.dy
        norm = math.hypot(dx, dy)
        if norm > 0.5:  # roughly the same way: share the direction; opposite moves are a deliberate bounce
            out.dx, out.dy = inn.dx, inn.dy = dx / norm, dy / norm
            out.strength = inn.strength = max(out.strength, inn.strength)
