"""Colour grading as 3D LUTs (.cube), applied by FFmpeg in 16-bit so nothing is lost to rounding.

Two ways to build a shot's LUT:
- fitted: when the footage is the reference's own (content match), align reference and footage
  frames and fit the exact colour mapping between them (captures contrast, saturation, tint,
  split-toning). This is "copy the grade".
- transfer: otherwise, move the footage's look towards the reference's, blended by a strength:
  lightness and colourfulness distributions are matched (quantile mapping) when both sides have
  them, else Lab mean/contrast (Reinhard colour transfer).
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import ColorProfile
from ..media import QUANTILES
from ..media import orb_init as _orb_init

log = logging.getLogger(__name__)

LUT_SIZE = 33
# Back to limited-range BT.709 explicitly: left alone, FFmpeg returns full-range video here.
FILTER = "format=gbrp16le,lut3d=file='{path}':interp=tetrahedral,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
# Accurate rounding for every implicit YUV<->RGB conversion (default swscale truncates and darkens).
SWS_FLAGS = ["-sws_flags", "accurate_rnd+full_chroma_int+full_chroma_inp"]


def _grid() -> np.ndarray:
    v = np.linspace(0, 1, LUT_SIZE, dtype=np.float32)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")  # .cube order: red changes fastest
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)


def write_cube(path: Path, rgb_out: np.ndarray) -> Path:
    rgb_out = np.clip(rgb_out, 0, 1)
    with open(path, "w") as f:
        f.write(f"LUT_3D_SIZE {LUT_SIZE}\n")
        f.write("\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rgb_out))
        f.write("\n")
    return path


def _features(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    return np.stack([r, g, b, r * r, g * g, b * b, r * g, r * b, g * b, r * r * r, g * g * g, b * b * b, np.ones_like(r)], axis=1)


def fitted_lut(pairs: list[tuple[np.ndarray, np.ndarray]], path: Path) -> Path | None:
    """Fit footage->reference colour mapping from (footage BGR, reference BGR) frame pairs."""
    src_px, ref_px = [], []
    for src, ref in pairs:
        aligned = _align(src, ref)
        if aligned is None:
            continue
        warped, mask = aligned
        src_px.append(warped[mask][:, ::-1].astype(np.float32) / 255)
        ref_px.append(ref[mask][:, ::-1].astype(np.float32) / 255)
    if not src_px:
        return None
    x, y = np.concatenate(src_px), np.concatenate(ref_px)
    if len(x) < 2000:
        return None
    sel = np.random.default_rng(0).choice(len(x), min(len(x), 60000), replace=False)
    feats = _features(x[sel])
    coef, *_ = np.linalg.lstsq(feats, y[sel], rcond=None)
    pred = feats @ coef
    if float(np.abs(pred - y[sel]).mean()) > 0.08:  # alignment or content too different to trust
        return None
    return write_cube(path, _features(_grid()) @ coef)


def _align(src: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Warp `src` onto `ref` (both BGR, same size) with ECC; returns (warped, valid-pixel mask)."""
    sg = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY).astype(np.float32)
    rg = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    warp = _orb_init(sg.astype(np.uint8), rg.astype(np.uint8))
    try:
        cc, warp = cv2.findTransformECC(rg, sg, warp, cv2.MOTION_AFFINE, (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
    except cv2.error:
        return None
    if cc < 0.85:
        return None
    h, w = ref.shape[:2]
    warped = cv2.warpAffine(src, warp, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=(0, 0, 0))
    valid = cv2.warpAffine(np.full((h, w), 255, np.uint8), warp, (w, h), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    valid = cv2.erode(valid, np.ones((9, 9), np.uint8)) > 0
    # Ignore edges (misalignment shows up there first) and clipped pixels.
    edges = cv2.dilate(cv2.Canny(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), 40, 120), np.ones((5, 5), np.uint8)) > 0
    clipped = (ref.max(axis=2) >= 254) | (ref.min(axis=2) <= 1)
    return warped, valid & ~edges & ~clipped


def is_monochrome(profile: ColorProfile) -> bool:
    """Black-and-white (or near) look: almost no colour cast and almost no colour spread."""
    if not profile.lab_mean or not profile.lab_std:
        return False
    _, a, b = profile.lab_mean
    return float(np.hypot(a, b)) < 3.0 and profile.lab_std[1] < 3.5 and profile.lab_std[2] < 3.5


# Steepest the lightness / colourfulness curves may get. On the McLaren edit (grey rain, black car)
# 2.5x / 3x cut the colour miss from 1.04 (linear) to 0.83; 3x lightness gave 0.74 but stretches dark
# 8-bit footage enough to risk banding (picture quality first). Modelled 2026-11-17.
MAX_L_SLOPE, MIN_L_SLOPE = 2.5, 0.5
MAX_CHROMA_SLOPE = 3.0


def _bounded(xs: np.ndarray, ys: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """ys moved so every segment's slope stays within [lo, hi], pinned at the median (middle point)."""
    slope = np.clip(np.diff(ys) / np.maximum(np.diff(xs), 1e-6), lo, hi)
    out, mid = np.empty_like(ys), len(xs) // 2
    out[mid] = ys[mid]
    for k in range(mid + 1, len(xs)):
        out[k] = out[k - 1] + slope[k - 1] * (xs[k] - xs[k - 1])
    for k in range(mid - 1, -1, -1):
        out[k] = out[k + 1] - slope[k] * (xs[k + 1] - xs[k])
    return out


def _mean_of(quantiles: np.ndarray) -> float:
    """Average of a distribution known by its QUANTILES (area under the quantile function)."""
    p = np.concatenate([[0.0], QUANTILES, [1.0]])
    q = np.concatenate([[quantiles[0]], quantiles, [quantiles[-1]]])
    return float(np.sum((q[1:] + q[:-1]) / 2 * np.diff(p)))


def _match_lightness(L: np.ndarray, src_q: np.ndarray, tgt_q: np.ndarray) -> np.ndarray:
    """Quantile mapping of lightness; beyond the measured range the curve eases into black and white
    instead of clipping (a linear stretch clipped a black car's shadows and lost contrast)."""
    ty = _bounded(src_q, tgt_q, MIN_L_SLOPE, MAX_L_SLOPE)
    # Limiting the slopes leaves the curve short where the target jumps (half sky, half black car:
    # the McLaren 2.97s shot came out 10 L too dark, v7). Shift it so the average still matches.
    ty = np.clip(ty + _mean_of(tgt_q) - _mean_of(ty), 0.0, 100.0)
    out = np.interp(L, src_q, ty)
    low, high = L < src_q[0], L > src_q[-1]
    out[low] = ty[0] * L[low] / max(src_q[0], 1e-6)
    out[high] = ty[-1] + (L[high] - src_q[-1]) * (100.0 - ty[-1]) / max(100.0 - src_q[-1], 1e-6)
    return out


def _match_colour(a: np.ndarray, b: np.ndarray, src_mean, src_q: np.ndarray, tgt_mean, tgt_q: np.ndarray,
                  max_slope: float = MAX_CHROMA_SLOPE, protect: float = 0.0):
    """Moves the mean colour (cast) and maps colourfulness (distance from it) by quantiles, hue kept:
    grey footage gets as colourful as the reference, not only as spread out.

    protect (Lab chroma): like a vibrance control, colours this close to grey keep (most of) their
    colourfulness; only clearer colours get the full boost. Near-grey tints are light casts and
    reflections (a rainy sky in a wet road), and boosting them turns a whole shot blue.
    """
    da, db = a - src_mean[1], b - src_mean[2]
    c = np.hypot(da, db)
    xs = np.concatenate([[0.0], src_q])
    ys = np.maximum(np.concatenate([[0.0], _bounded(src_q, tgt_q, 0.0, max_slope)]), 0.0)
    ys[1] = min(ys[1], max_slope * xs[1])
    gain = np.where(c > 1e-3, np.interp(c, xs, ys) / np.maximum(c, 1e-3), ys[1] / max(xs[1], 1e-3))
    beyond = c > xs[-1]
    gain[beyond] = ys[-1] / max(xs[-1], 1e-3)
    if protect > 0:
        u = np.clip(c / protect, 0.0, 1.0)
        gain = 1.0 + (gain - 1.0) * u * u * (3 - 2 * u)  # smoothstep: grey-ish keeps its own colour
    return tgt_mean[1] + da * gain, tgt_mean[2] + db * gain


def transfer_lut(src: dict, target: ColorProfile, strength: float, path: Path, neutral_tint: float | None = None,
                 max_chroma: float = MAX_CHROMA_SLOPE, balance: tuple[float, float] = (0.0, 0.0), protect: float = 0.0
                 ) -> Path | None:
    """Transfer of the footage stats `src` towards `target`, blended by `strength`.

    neutral_tint: match greys to the target's greys (its grade) plus this share of the difference in
    average colour (which is mostly content); colourfulness is then measured from the greys. None:
    match the average colour. balance: extra (a, b) shift for this shot (shot matching).
    """
    if not src.get("lab_mean") or not target.lab_mean or not target.lab_std:
        return None
    if src.get("l_quantiles") and target.l_quantiles and src.get("chroma_quantiles") and target.chroma_quantiles \
            and not is_monochrome(target):
        rgb = _grid()
        lab = cv2.cvtColor(rgb[None, :, ::-1].copy(), cv2.COLOR_BGR2LAB)[0]
        if neutral_tint is not None and src.get("neutral_ab") and target.neutral_ab and target.neutral_chroma_quantiles:
            sn, tn = np.array(src["neutral_ab"]), np.array(target.neutral_ab)
            to = tn + neutral_tint * (np.array(target.lab_mean[1:]) - np.array(src["lab_mean"][1:])) + np.array(balance)
            colour = _match_colour(lab[:, 1], lab[:, 2], (0.0, *sn), np.array(src["neutral_chroma_quantiles"]),
                                   (0.0, *to), np.array(target.neutral_chroma_quantiles), max_chroma, protect)
        else:
            colour = _match_colour(lab[:, 1], lab[:, 2], src["lab_mean"], np.array(src["chroma_quantiles"]),
                                   target.lab_mean, np.array(target.chroma_quantiles))
        moved = np.stack([
            _match_lightness(lab[:, 0], np.array(src["l_quantiles"]), np.array(target.l_quantiles)),
            *colour,
        ], axis=1)
        lab = lab + (moved - lab) * strength
        out = cv2.cvtColor(lab[None].astype(np.float32), cv2.COLOR_LAB2BGR)[0][:, ::-1]
        return write_cube(path, out)
    ms, ss = np.array(src["lab_mean"]), np.maximum(np.array(src["lab_std"]), 1e-3)
    mt, st = np.array(target.lab_mean), np.array(target.lab_std)
    # Lightness contrast stays within 0.5-2x; colour may go all the way to zero (black-and-white looks).
    scale = np.clip(st / ss, [0.5, 0.0, 0.0], [2.0, 2.0, 2.0])
    amount = np.array([strength, strength, strength])
    if is_monochrome(target):
        amount[1:] = 1.0  # a B&W look is all-or-nothing; 80% desaturated just looks washed out
    rgb = _grid()
    lab = cv2.cvtColor(rgb[None, :, ::-1].copy(), cv2.COLOR_BGR2LAB)[0]
    moved = (lab - ms) * scale + mt
    lab = lab + (moved - lab) * amount
    out = cv2.cvtColor(lab[None].astype(np.float32), cv2.COLOR_LAB2BGR)[0][:, ::-1]
    return write_cube(path, out)
