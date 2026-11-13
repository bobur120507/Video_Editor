"""Music analysis: tempo, beat timestamps and beat drops (librosa)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class AudioAnalysis:
    bpm: float = 0.0
    beats: list[float] = field(default_factory=list)
    drops: list[float] = field(default_factory=list)
    has_music: bool = False


def analyze_audio(wav_path: str | Path) -> AudioAnalysis:
    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    if y.size < sr // 2 or float(np.sqrt(np.mean(y**2))) < 0.005:
        return AudioAnalysis()

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0

    rms = librosa.feature.rms(y=y)[0]
    times = librosa.times_like(rms, sr=sr)
    drops = _find_drops(rms, times)

    return AudioAnalysis(
        bpm=round(bpm, 1),
        beats=[round(float(b), 3) for b in beats],
        drops=drops,
        has_music=len(beats) >= 4,
    )


def _find_drops(rms: np.ndarray, times: np.ndarray, window_s: float = 0.6, ratio: float = 2.2) -> list[float]:
    """Moments where loudness jumps sharply compared to the preceding window."""
    if rms.size < 4:
        return []
    hop_s = float(times[1] - times[0])
    w = max(1, int(window_s / hop_s))
    floor = float(np.median(rms))
    drops: list[float] = []
    for i in range(w, rms.size - w):
        before = rms[i - w : i].mean() + 1e-6
        after = rms[i : i + w].mean()
        if after / before >= ratio and after > floor:
            t = float(times[i])
            if not drops or t - drops[-1] > 2.0:
                drops.append(round(t, 3))
    return drops


def snap_to_beats(t: float, beats: list[float], tolerance: float = 0.12) -> float:
    if not beats:
        return t
    nearest = min(beats, key=lambda b: abs(b - t))
    return nearest if abs(nearest - t) <= tolerance else t
