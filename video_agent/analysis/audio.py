"""Music analysis: tempo, beat timestamps, beat drops and the stored audio graph (librosa)."""

from __future__ import annotations

import base64
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np

from ..blueprint import AudioGraph
from ..media import extract_audio

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


GRAPH_SR, GRAPH_HOP, GRAPH_BANDS = 22050, 512, 64
LOW_HZ, HIGH_HZ = 150, 1500  # band edges, as in events.detect_hits


def audio_graph(wav_path: str | Path, floor_db: float = -80.0) -> AudioGraph:
    """Waveform envelope, loudness, onset strength, band energy and a dB mel spectrogram, one column per hop.

    dB scales (not linear) so quiet sounds stay visible next to the loud beats.
    """
    y, sr = librosa.load(str(wav_path), sr=GRAPH_SR, mono=True)
    hop = GRAPH_HOP
    power = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)) ** 2
    n = power.shape[1]

    # Column i is centred on sample i * hop (librosa's centred frames): the waveform window matches.
    padded = np.pad(y, (hop // 2, n * hop))[: n * hop].reshape(n, hop)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bands = [power[(freqs >= a) & (freqs < b)].sum(0) for a, b in ((20, LOW_HZ), (LOW_HZ, HIGH_HZ), (HIGH_HZ, sr / 2))]
    ref = max(float(b.max()) for b in bands) + 1e-12

    mel = librosa.feature.melspectrogram(S=power, sr=sr, n_mels=GRAPH_BANDS, fmin=30.0, fmax=sr / 2)
    mel_db = librosa.power_to_db(mel, ref=float(mel.max()) + 1e-12, top_db=-floor_db)
    onset = librosa.onset.onset_strength(S=mel_db, sr=sr, hop_length=hop)
    rms = librosa.feature.rms(S=np.sqrt(power), frame_length=2048, hop_length=hop)[0]

    def db(a: np.ndarray, r: float) -> list[float]:
        return [round(float(v), 1) for v in np.maximum(10 * np.log10(a / r + 1e-12), floor_db)]

    spec = np.round((mel_db - floor_db) / -floor_db * 255).clip(0, 255).astype(np.uint8)
    return AudioGraph(
        fps=round(sr / hop, 4),
        wave_min=[round(float(v), 3) for v in padded.min(1)],
        wave_max=[round(float(v), 3) for v in padded.max(1)],
        loudness=[round(float(v), 1) for v in np.maximum(20 * np.log10(rms + 1e-9), floor_db)],
        onset=[round(float(v), 3) for v in onset[:n] / (float(onset.max()) + 1e-9)],
        low=db(bands[0], ref),
        mid=db(bands[1], ref),
        high=db(bands[2], ref),
        mel_hz=[round(float(f), 1) for f in librosa.mel_frequencies(n_mels=GRAPH_BANDS, fmin=30.0, fmax=sr / 2)],
        spec=base64.b64encode(spec.tobytes()).decode(),
        floor_db=floor_db,
    )


def graph_for(video_path: str | Path) -> AudioGraph:
    """audio_graph of a video's sound track (for blueprints cloned before the graph was stored)."""
    with tempfile.TemporaryDirectory() as tmp:
        return audio_graph(extract_audio(video_path, Path(tmp) / "a.wav"))


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
