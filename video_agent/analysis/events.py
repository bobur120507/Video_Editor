"""Edit events: what the editor *did*, separated from how the original camera moved.

A literal camera curve copied onto different footage reproduces the reference's camera work (orbit
shots, handheld drift), which static footage can never match and which reads as random wobble.
What transfers is the edit's intent: whip transitions in and out of shots, punch-zooms and shakes
on the music's hits, and the overall push. These are extracted per shot and timed to the hits.
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
SHAKE_JITTER = 0.012  # mean |2nd difference| of position
SNAP = 0.1  # seconds: how far an event may move to land on a hit


def detect_hits(wav_path: str | Path, keep: float = 0.6) -> list[float]:
    """Strong onsets (kicks, snares, 808 hits): the moments an editor syncs moves to."""
    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="frames")
    if len(frames) == 0:
        return []
    strength = env[frames]
    times = librosa.frames_to_time(frames, sr=sr)
    return [round(float(t), 3) for t, s in zip(times, strength) if s >= np.quantile(strength, 1 - keep)]


def _snap(t: float, hits: list[float]) -> float:
    if not hits:
        return t
    nearest = min(hits, key=lambda h: abs(h - t))
    return nearest if abs(nearest - t) <= SNAP else t


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
    events: list[EditEvent] = []

    def edge(i: int) -> tuple[float, float, float]:
        return float(vt[i]), float(abs(vs[i])), float(abs(vr[i]))

    # Whips: violent movement on the first / last frames of a shot (the cut hides inside the blur).
    for kind, idx in (("whip_in", range(min(2, n))), ("whip_out", range(max(0, n - 2), n))):
        best = max(idx, key=lambda i: vt[i] + abs(vs[i]))
        speed, zoom, rot = edge(best)
        if speed > WHIP_SPEED or zoom > WHIP_ZOOM:
            norm = math.hypot(vx[best], vy[best]) or 1.0
            events.append(
                EditEvent(
                    kind=kind,
                    t=0.0 if kind == "whip_in" else seg.duration,
                    strength=min(1.0, max(0.3, speed / 0.15 + zoom / 0.3)),
                    dx=float(vx[best] / norm),
                    dy=float(vy[best] / norm),
                    zoom=float(np.sign(vs[best])),
                )
            )

    # Punches: zoom spikes inside the shot that sit on a music hit (editors punch on hits; spikes
    # between hits are the original camera's movement). One punch per hit, the strongest.
    inner = np.arange(2, max(2, n - 2))
    peaks = [i for i in inner if abs(vs[i]) > PUNCH_ZOOM and abs(vs[i]) >= abs(vs[i - 1]) and abs(vs[i]) >= abs(vs[min(i + 1, n - 1)])]
    on_hit: dict[float, int] = {}
    for i in peaks:
        t_abs = seg.start + i / fps
        hit = _snap(t_abs, hits)
        if hit != t_abs and (hit not in on_hit or abs(vs[i]) > abs(vs[on_hit[hit]])):
            on_hit[hit] = i
    for hit, i in sorted(on_hit.items()):
        t = hit - seg.start
        if 0 < t < seg.duration:
            events.append(EditEvent(kind="punch", t=round(t, 3), strength=min(0.25, abs(float(vs[i])) * 2.0), zoom=float(np.sign(vs[i]))))

    # Shake: sustained high-frequency jitter; starts on a hit when one is close.
    jitter = np.abs(np.diff(x, 2)) + np.abs(np.diff(y, 2))
    if len(jitter) and float(jitter.mean()) > SHAKE_JITTER:
        hot = np.where(jitter > SHAKE_JITTER)[0]
        t0 = _snap(seg.start + hot[0] / fps, hits) - seg.start
        t1 = (hot[-1] + 2) / fps
        events.append(
            EditEvent(
                kind="shake",
                t=round(max(0.0, t0), 3),
                duration=round(max(0.1, t1 - max(0.0, t0)), 3),
                strength=min(1.0, float(jitter.mean()) / 0.04),
                rot=min(5.0, float(np.std(vr)) / 2),
            )
        )

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
