"""Match score: how closely an output edit reproduces the reference edit.

style_match compares what the edit *does* (cut timing, camera motion, colour grade, pace, flashes,
duration), so it works even when the footage is different. pixel_match compares frames directly
(SSIM) and only makes sense when the output was made from the same raw footage as the reference,
e.g. in the synthetic benchmark.

Every part uses closeness(e) = exp(-(e/2)^2), where e = 1 is roughly "just noticeable".
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .analysis.camera import motion_velocities
from .analysis.visual import detect_cuts, find_flashes, flash_aware_cuts, track_frames
from .media import iter_frames, probe

log = logging.getLogger(__name__)

# "Feel" parts (timing, shake, smooth, sharp, flow) measure what a viewer notices first: moves late or
# scattered around the beat, more shaking than the reference, jerky motion, smeared pictures, and
# motion that does not rise and fall with the music the way the reference's does.
WEIGHTS = {
    "cuts": 0.20, "camera": 0.15, "color": 0.15, "pace": 0.04, "flashes": 0.05, "duration": 0.05,
    "timing": 0.10, "shake": 0.08, "smooth": 0.07, "sharp": 0.05, "flow": 0.06,
    "music_sync": 0.0,  # reported only: superseded by timing
}
FRAME_MS = 1000 / 30


@dataclass
class MatchReport:
    overall: float  # 0..100
    parts: dict[str, float]  # each 0..100
    pixel_ssim: float | None = None  # 0..100 when computed
    details: dict = field(default_factory=dict)

    def text(self) -> str:
        parts = " · ".join(f"{k} {v:.0f}%" for k, v in self.parts.items())
        pix = f"\nPixel similarity (SSIM): {self.pixel_ssim:.1f}%" if self.pixel_ssim is not None else ""
        return f"Match: {self.overall:.1f}%\n{parts}{pix}"


def closeness(e: float) -> float:
    return math.exp(-((e / 2) ** 2))


def _f1(ref: list[float], out: list[float], tol: float) -> float:
    if not ref and not out:
        return 1.0
    if not ref or not out:
        return 0.0
    used, hits = set(), 0
    for r in ref:
        best = min((i for i in range(len(out)) if i not in used), key=lambda i: abs(out[i] - r), default=None)
        if best is not None and abs(out[best] - r) <= tol:
            used.add(best)
            hits += 1
    precision, recall = hits / len(out), hits / len(ref)
    return 0.0 if hits == 0 else 2 * precision * recall / (precision + recall)


def _bounds(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    pts = [0.0, *[c for c in cuts if 0 < c < duration], duration]
    return list(zip(pts[:-1], pts[1:]))


def _camera_score(ref_vel: list[np.ndarray], out_vel: list[np.ndarray]) -> float:
    """Compare frame-to-frame camera movement (smoothed over 5 frames) shot by shot."""
    tol = np.array([0.006, 0.004, 0.004, 0.004])  # per frame: zoom (log), rotation (rad), x, y (fraction of frame)
    k = np.ones(5) / 5
    scores = []
    for a, b in zip(ref_vel, out_vel):
        n = min(len(a), len(b))
        if n == 0:
            continue
        a, b = a[:n], b[:n]
        known = ~(np.isnan(a).any(axis=1) | np.isnan(b).any(axis=1))  # frames neither tracker could read are skipped
        a, b = np.nan_to_num(a), np.nan_to_num(b)
        if n >= 5:
            a = np.stack([np.convolve(a[:, c], k, "same") for c in range(4)], axis=1)
            b = np.stack([np.convolve(b[:, c], k, "same") for c in range(4)], axis=1)
        # Tolerance grows with the size of the move: a 2% miss on a 30%-per-frame whip zoom is invisible.
        e = np.abs(a - b) / (tol + 0.25 * np.abs(a))
        scores += [closeness(float(v)) for v in e[known].sum(axis=1)]
    return float(np.mean(scores)) if scores else 1.0


def _lab_stats(path: Path, start: float, end: float) -> np.ndarray | None:
    frames = [f for _, f in iter_frames(path, sample_fps=4, width=160, start=start, end=end)]
    if not frames:
        return None
    lab = np.concatenate([cv2.cvtColor(f, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32) for f in frames])
    L, a, b = lab[:, 0] * 100 / 255, lab[:, 1] - 128, lab[:, 2] - 128
    return np.array([L.mean(), L.std(), a.mean(), b.mean(), np.hypot(a, b).mean()])


def _color_score(ref: Path, out: Path, bounds) -> float:
    tol = np.array([6.0, 5.0, 4.0, 4.0, 5.0])  # L mean, L contrast, green-red cast, blue-yellow cast, saturation
    scores = []
    for a, b in bounds:
        s1, s2 = _lab_stats(ref, a, b), _lab_stats(out, a, b)
        if s1 is not None and s2 is not None:
            scores.append(closeness(float(np.sqrt((((s1 - s2) / tol) ** 2).sum()))))
    return float(np.mean(scores)) if scores else 1.0


def _pace_score(ref_flow: np.ndarray, out_flow: np.ndarray) -> float:
    n = min(len(ref_flow), len(out_flow))
    if n < 5:
        return 1.0
    k = np.ones(5) / 5
    a, b = np.convolve(ref_flow[:n], k, "same"), np.convolve(out_flow[:n], k, "same")
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 1.0 if a.std() < 1e-6 and b.std() < 1e-6 else 0.0
    return max(0.0, float(np.corrcoef(a, b)[0, 1]))


def _music_sync(reference: Path, output: Path, out_track, ref_track, cuts: list[float], fps: float) -> float:
    """Share of big moves landing on a music hit or a cut (±2 frames), ours relative to the reference's.

    Measures the edit's rhythm without asking for the same camera path, so it is fair to footage
    that differs from the reference's.
    """
    import tempfile

    from .analysis.events import detect_hits
    from .media import extract_audio

    try:
        with tempfile.TemporaryDirectory() as tmp:
            hits, _ = detect_hits(extract_audio(reference, Path(tmp) / "a.wav"))
    except Exception:
        return 1.0
    marks = np.array(sorted([*hits, *cuts]))
    if not len(marks):
        return 1.0

    def rate(track) -> float:
        from scipy.signal import find_peaks

        f = track.flow
        peaks, _ = find_peaks(f, height=np.percentile(f, 80), distance=4)
        if not len(peaks):
            return 0.0
        t = track.times[peaks]
        return float(np.mean([np.min(np.abs(marks - x)) <= 2 / fps for x in t]))

    ref_rate = rate(ref_track)
    return 1.0 if ref_rate == 0 else min(1.0, rate(out_track) / ref_rate)


def _hits(reference: Path) -> list[float]:
    import tempfile

    from .analysis.events import detect_hits
    from .media import extract_audio

    try:
        with tempfile.TemporaryDirectory() as tmp:
            return detect_hits(extract_audio(reference, Path(tmp) / "a.wav"))[0]
    except Exception:
        return []


def _moves(track) -> np.ndarray:
    """Times of the big motion peaks (whips, punches, jolts): what the eye reads as a move."""
    from scipy.signal import find_peaks

    f = track.flow
    if len(f) < 5:
        return np.zeros(0)
    peaks, _ = find_peaks(f, height=np.percentile(f, 80), distance=3)
    return track.times[peaks]


def _offsets(times, hits: np.ndarray, fps: float) -> np.ndarray:
    """Milliseconds from each move/cut to its hit, for those within 3 frames of one."""
    if not len(hits):
        return np.zeros(0)
    d = np.array([t - hits[np.argmin(np.abs(hits - t))] for t in times])
    return d[np.abs(d) <= 3 / fps] * 1000 if len(d) else d


def _feel(ref_track, out_track, ref_cuts, out_cuts, hits: list[float], fps: float, reference: Path, output: Path) -> tuple[dict, dict]:
    """Timing, shake amount, smoothness and sharpness of the output, each judged against the reference."""
    h = np.array(hits)
    ref_off = _offsets([*ref_cuts, *_moves(ref_track)], h, fps)
    out_off = _offsets([*out_cuts, *_moves(out_track)], h, fps)
    if len(ref_off) and len(out_off):
        # Late/early overall (median) and scattered (spread) are both felt; a frame is the unit.
        shift = abs(float(np.median(out_off) - np.median(ref_off))) / FRAME_MS
        spread = max(0.0, float(np.mean(np.abs(out_off - np.median(out_off))) - np.mean(np.abs(ref_off - np.median(ref_off))))) / FRAME_MS
        timing = closeness(shift) * closeness(spread)
    else:
        timing = 1.0 if not len(ref_off) else 0.5

    dur_r, dur_o = max(ref_track.times[-1], 1e-3), max(out_track.times[-1], 1e-3)
    rate_r, rate_o = len(_moves(ref_track)) / dur_r, len(_moves(out_track)) / dur_o
    # Moves per second: 25% more or fewer than the reference is just noticeable.
    shake = closeness(abs(math.log((rate_o + 0.2) / (rate_r + 0.2))) / math.log(1.25))

    def rough(f):
        return float(np.abs(np.diff(f, 2)).mean() / (f.mean() + 1e-6)) if len(f) > 3 else 0.0

    smooth = closeness(max(0.0, rough(out_track.flow) / (rough(ref_track.flow) + 1e-6) - 1) / 0.15)

    def smeared(path):
        s = np.array([cv2.Laplacian(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var() for _, fr in iter_frames(path, sample_fps=10, width=360)])
        return float((s < 0.35 * np.quantile(s, 0.9)).mean()) if len(s) else 0.0

    flow, wave_r, wave_o = _flow(ref_track.flow, out_track.flow, h, fps)
    sm_r, sm_o = smeared(reference), smeared(output)
    sharp = closeness(max(0.0, sm_o - sm_r) / 0.1)  # only blurrier than the reference counts against
    details = {
        "timing_ms": {"ref_median": round(float(np.median(ref_off)), 1) if len(ref_off) else None,
                      "out_median": round(float(np.median(out_off)), 1) if len(out_off) else None},
        "moves_per_s": {"ref": round(float(rate_r), 2), "out": round(float(rate_o), 2)},
        "roughness": {"ref": round(rough(ref_track.flow), 3), "out": round(rough(out_track.flow), 3)},
        "smeared": {"ref": round(sm_r, 2), "out": round(sm_o, 2)},
        "motion_wave": {"ref": wave_r, "out": wave_o},
    }
    return {"timing": timing, "shake": shake, "smooth": smooth, "sharp": sharp, "flow": flow}, details


WAVE = 8  # frames either side of a hit


def _wave(flow: np.ndarray, hits: np.ndarray, fps: float) -> tuple[np.ndarray, float]:
    """Average motion around the music hits (÷ typical motion), and fast ÷ typical motion."""
    flow = np.asarray(flow, dtype=np.float64)
    med = float(np.median(flow)) + 1e-6
    rows = [flow[f - WAVE:f + WAVE + 1] / med for f in (np.round(hits * fps).astype(int) if len(hits) else [])
            if WAVE <= f < len(flow) - WAVE]
    profile = np.mean(rows, axis=0) if rows else np.ones(2 * WAVE + 1)
    return profile, float(np.quantile(flow, 0.9)) / med if len(flow) else 1.0


def _flow(ref_flow, out_flow, hits: np.ndarray, fps: float) -> tuple[float, dict, dict]:
    """Does the edit's motion breathe like the reference's: the same rise and fall around the hits
    (correlation of the two waves) and the same contrast between fast and typical moments."""
    pr, rr = _wave(ref_flow, hits, fps)
    po, ro = _wave(out_flow, hits, fps)
    shape = float(np.corrcoef(pr, po)[0, 1]) if pr.std() > 1e-6 and po.std() > 1e-6 else (1.0 if pr.std() <= 1e-6 and po.std() <= 1e-6 else 0.0)
    contrast = closeness(abs(math.log((ro + 1e-6) / (rr + 1e-6))) / math.log(1.5))  # 1.5x the contrast is noticeable
    score = contrast * (0.5 + 0.5 * max(0.0, shape))

    def info(p, r):
        return {"fast_vs_typical": round(r, 2), "around_hit": [round(float(v), 2) for v in p]}

    return score, info(pr, rr), info(po, ro)


def style_match(reference: str | Path, output: str | Path) -> MatchReport:
    reference, output = Path(reference), Path(output)
    ri, oi = probe(reference), probe(output)
    fps = ri.fps

    ref_track, out_track = track_frames(reference, fps), track_frames(output, oi.fps)
    ref_flashes, out_flashes = find_flashes(ref_track), find_flashes(out_track)
    ref_cuts = flash_aware_cuts(reference, detect_cuts(reference, fps), ref_flashes, fps)
    out_cuts = flash_aware_cuts(output, detect_cuts(output, oi.fps), out_flashes, oi.fps)
    bounds = _bounds(ref_cuts, ri.duration)

    parts = {
        "cuts": _f1(ref_cuts, out_cuts, 1.5 / fps),
        "camera": _camera_score(motion_velocities(reference, bounds, fps), motion_velocities(output, bounds, oi.fps)),
        "color": _color_score(reference, output, bounds),
        "pace": _pace_score(ref_track.flow, out_track.flow),
        "flashes": _f1(ref_flashes, out_flashes, 2.5 / fps),
        "duration": closeness(abs(ri.duration - oi.duration) / 0.1),
        "music_sync": _music_sync(reference, output, out_track, ref_track, ref_cuts, fps),
    }
    feel, feel_details = _feel(ref_track, out_track, ref_cuts, out_cuts, _hits(reference), fps, reference, output)
    parts.update(feel)
    overall = sum(WEIGHTS[k] * v for k, v in parts.items())
    return MatchReport(
        overall=round(overall * 100, 1),
        parts={k: round(v * 100, 1) for k, v in parts.items()},
        details={"ref_cuts": ref_cuts, "out_cuts": out_cuts, **feel_details},
    )


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(np.float32), b.astype(np.float32)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    mu_a, mu_b = blur(a), blur(b)
    var_a, var_b, cov = blur(a * a) - mu_a**2, blur(b * b) - mu_b**2, blur(a * b) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2))
    return float(m.mean())


def pixel_match(reference: str | Path, output: str | Path, width: int = 270) -> float:
    fps = probe(reference).fps
    ref = (cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in iter_frames(reference, sample_fps=fps, width=width))
    out = (cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in iter_frames(output, sample_fps=fps, width=width))
    vals = [_ssim(a, b) for a, b in zip(ref, out) if a.shape == b.shape]
    return round(float(np.mean(vals)) * 100, 1) if vals else 0.0


def full_report(reference: str | Path, output: str | Path, pixels: bool = False) -> MatchReport:
    report = style_match(reference, output)
    if pixels:
        report.pixel_ssim = pixel_match(reference, output)
    return report
