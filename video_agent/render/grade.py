"""Colour grading as 3D LUTs (.cube), applied by FFmpeg in 16-bit so nothing is lost to rounding.

Two ways to build a shot's LUT:
- fitted: when the footage is the reference's own (content match), align reference and footage
  frames and fit the exact colour mapping between them (captures contrast, saturation, tint,
  split-toning). This is "copy the grade".
- transfer: otherwise, move the footage's Lab mean/contrast towards the reference look
  (Reinhard colour transfer), blended by a strength.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import ColorProfile

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


def transfer_lut(src: dict, target: ColorProfile, strength: float, path: Path) -> Path | None:
    """Reinhard Lab transfer of the footage stats `src` towards `target`, blended by `strength`."""
    if not src.get("lab_mean") or not target.lab_mean or not target.lab_std:
        return None
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


def _orb_init(src_gray: np.ndarray, ref_gray: np.ndarray) -> np.ndarray:
    """Rough src->ref similarity from ORB matches (handles big zooms ECC can't start from)."""
    orb = cv2.ORB_create(1500)
    k1, d1 = orb.detectAndCompute(ref_gray, None)
    k2, d2 = orb.detectAndCompute(src_gray, None)
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return np.eye(2, 3, dtype=np.float32)
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
    if len(matches) < 10:
        return np.eye(2, 3, dtype=np.float32)
    ref_pts = np.float32([k1[m.queryIdx].pt for m in matches])
    src_pts = np.float32([k2[m.trainIdx].pt for m in matches])
    m, _ = cv2.estimateAffinePartial2D(ref_pts, src_pts, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    return m.astype(np.float32) if m is not None else np.eye(2, 3, dtype=np.float32)
