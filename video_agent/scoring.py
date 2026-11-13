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

WEIGHTS = {"cuts": 0.25, "camera": 0.25, "color": 0.20, "pace": 0.15, "flashes": 0.10, "duration": 0.05}


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
        a, b = np.nan_to_num(a[:n]), np.nan_to_num(b[:n])
        if n >= 5:
            a = np.stack([np.convolve(a[:, c], k, "same") for c in range(4)], axis=1)
            b = np.stack([np.convolve(b[:, c], k, "same") for c in range(4)], axis=1)
        e = np.abs(a - b) / tol
        scores += [closeness(float(v)) for v in e.sum(axis=1)]
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
    }
    overall = sum(WEIGHTS[k] * v for k, v in parts.items())
    return MatchReport(
        overall=round(overall * 100, 1),
        parts={k: round(v * 100, 1) for k, v in parts.items()},
        details={"ref_cuts": ref_cuts, "out_cuts": out_cuts},
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
