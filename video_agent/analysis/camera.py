"""Per-frame camera motion (zoom, rotation, pan, shake) for every shot.

For each pair of consecutive frames inside a shot we track features (Lucas-Kanade) and fit a
similarity transform (scale + rotation + translation, RANSAC). Chaining them gives, for every
frame, the transform from the shot's first frame — the exact movement an editor keyframed
(zoom punches, shakes, rotations) plus the camera's own movement.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import CameraCurve
from ..media import iter_frames

log = logging.getLogger(__name__)

_LK = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))


def frame_motion(prev: np.ndarray, gray: np.ndarray) -> np.ndarray | None:
    """2x3 similarity transform mapping `prev` points to `gray` points, or None if tracking failed."""
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=500, qualityLevel=0.005, minDistance=7, blockSize=7)
    if pts is None or len(pts) < 12:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, pts, None, **_LK)
    if nxt is None:
        return None
    ok = st.reshape(-1) == 1
    if ok.sum() < 12:
        return None
    m, inliers = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=2000)
    if m is None or inliers is None or inliers.sum() < 10:
        return None
    return m


def _decompose(cum: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    """Centre-based (scale, rotation, tx/w, ty/h) of a 3x3 affine."""
    a, b = cum[0, 0], cum[1, 0]
    s = math.hypot(a, b)
    r = math.atan2(b, a)
    c = np.array([w / 2, h / 2])
    t = cum[:2, :2] @ c + cum[:2, 2] - c
    return s, r, float(t[0] / w), float(t[1] / h)


def estimate_curves(path: str | Path, bounds: list[tuple[float, float]], fps: float, width: int = 480) -> list[CameraCurve]:
    """One CameraCurve per shot in `bounds` (seconds), sampled at every source frame."""
    return _estimate(path, bounds, fps, width)[0]


def estimate_curves_and_blur(path: str | Path, bounds: list[tuple[float, float]], fps: float, width: int = 480
                             ) -> tuple[list[CameraCurve], float, list[list[float]]]:
    """Curves, the editor's motion-blur shutter (0 = none, 0.5 = 180°, 1.0 = 360° heavy blur) and,
    per shot, how soft each frame is next to the shot's sharpest one (0 crisp .. 1 smeared).

    Within each shot, the sharpness of the fastest-moving third of frames is compared with the
    slowest third (pro edits rarely hold still, so there may be no truly still frames).
    """
    curves, sharp = _estimate(path, bounds, fps, width)
    softness = [[round(float(np.clip(1 - v / (max(sh) + 1e-6), 0, 1)), 2) for v in sh] for sh in sharp]
    ratios = []
    for curve, sh in zip(curves, sharp):
        speeds = _corner_speeds(curve, width, width * 16 / 9)
        sh = np.array(sh[: len(speeds)])
        if len(speeds) < 6 or np.ptp(speeds) < 6:
            continue
        order = np.argsort(speeds)
        k = len(speeds) // 3
        ratios.append(float(np.median(sh[order[-k:]]) / (np.median(sh[order[:k]]) + 1e-6)))
    if not ratios:
        return curves, 0.0, softness
    r = float(np.median(ratios))
    return curves, 1.0 if r < 0.5 else 0.5 if r < 0.75 else 0.0, softness


def _corner_speeds(curve: CameraCurve, w: float, h: float) -> np.ndarray:
    """Per-frame max pixel displacement of the frame corners caused by the transform."""
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float64)
    c = np.array([w / 2, h / 2])
    pos = []
    for s, r, x, y in zip(curve.s, curve.r, curve.x, curve.y):
        a = s * np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
        pos.append((corners - c) @ a.T + c + [x * w, y * h])
    pos = np.array(pos)
    if len(pos) < 2:
        return np.zeros(len(pos))
    d = np.linalg.norm(np.diff(pos, axis=0), axis=2).max(axis=1)
    return np.concatenate([[0.0], d])


_ECC_WIDTH = 96
_ECC_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)


def _ecc_step(prev: np.ndarray, gray: np.ndarray) -> np.ndarray | None:
    """3x3 transform prev -> gray from whole-image intensity alignment at low resolution."""
    h, w = gray.shape
    small = (_ECC_WIDTH, max(2, int(h * _ECC_WIDTH / w)))
    a = cv2.resize(prev, small, interpolation=cv2.INTER_AREA).astype(np.float32)
    b = cv2.resize(gray, small, interpolation=cv2.INTER_AREA).astype(np.float32)
    try:
        cc, warp = cv2.findTransformECC(b, a, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE, _ECC_CRITERIA, None, 5)
    except cv2.error:
        return None
    if cc < 0.85:
        return None
    fwd = np.linalg.inv(np.vstack([warp, [0, 0, 1]]))  # ECC maps gray -> prev coordinates
    k = w / _ECC_WIDTH
    scale = np.diag([k, k, 1.0])
    step = scale @ fwd @ np.linalg.inv(scale)
    z, r, x, y = _decompose(step, w, h)
    # One frame of even the wildest edit stays within these; beyond them the fit has failed.
    if not (0.7 <= z <= 1.45) or abs(r) > math.radians(30) or max(abs(x), abs(y)) > 0.35:
        return None
    return step


class _ShotTracker:
    """Tracks the same features through a shot and fits each frame directly to the first frame.

    Points are followed frame to frame with Lucas-Kanade, but the transform is always estimated
    from their *original* positions, so errors do not accumulate. When too many points are lost,
    new ones are seeded and mapped back into first-frame coordinates.
    """

    def __init__(self, gray: np.ndarray):
        self.m = np.eye(3)
        self.prev = gray
        self.p0 = np.empty((0, 2), np.float32)
        self.cur = np.empty((0, 2), np.float32)
        self.seeded = 0
        self.sharpness = [float(cv2.Laplacian(gray, cv2.CV_32F).var())]
        self._seed(gray)

    def _seed(self, gray: np.ndarray) -> None:
        pts = cv2.goodFeaturesToTrack(gray, maxCorners=600, qualityLevel=0.005, minDistance=7, blockSize=7)
        if pts is None:
            return
        pts = pts.reshape(-1, 2)
        inv = np.linalg.inv(self.m)
        back = (np.hstack([pts, np.ones((len(pts), 1))]) @ inv.T)[:, :2]
        self.p0 = np.vstack([self.p0, back]).astype(np.float32)
        self.cur = np.vstack([self.cur, pts]).astype(np.float32)
        self.seeded = len(self.cur)

    def update(self, gray: np.ndarray) -> np.ndarray:
        sharp = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        self.sharpness.append(sharp)
        blurred = sharp < 0.35 * float(np.median(self.sharpness))
        before = self.m.copy()
        tracked = False
        if len(self.cur) >= 12 and not blurred:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(self.prev, gray, self.cur.reshape(-1, 1, 2), None, **_LK)
            back, st2, _ = cv2.calcOpticalFlowPyrLK(gray, self.prev, nxt, None, **_LK)
            fb = np.linalg.norm(back.reshape(-1, 2) - self.cur, axis=1)
            ok = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1) & (fb < 1.0)
            self.p0, self.cur = self.p0[ok], nxt.reshape(-1, 2)[ok]
            if len(self.cur) >= 12:
                m, inl = cv2.estimateAffinePartial2D(self.p0, self.cur, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=3000)
                if m is not None and inl is not None and inl.sum() >= 10:
                    self.m = np.vstack([m, [0, 0, 1]])
                    keep = inl.reshape(-1).astype(bool)
                    self.p0, self.cur = self.p0[keep], self.cur[keep]
                    tracked = True
        if not tracked:
            # Whip pans and zoom blurs smear features away; whole-image alignment at low
            # resolution still sees the move.
            step = _ecc_step(self.prev, gray)
            if step is not None and not 0.25 <= _decompose(step @ before, *gray.shape[::-1])[0] <= 4.0:
                step = None  # a run of blurred frames compounding into an impossible zoom: hold instead
            if step is not None:
                self.m = step @ before
                self.p0 = np.empty((0, 2), np.float32)
                self.cur = np.empty((0, 2), np.float32)
                self.seeded = 0
        self.prev = gray
        if len(self.cur) < max(60, 0.4 * self.seeded):
            self._seed(gray)
        return self.m


def _estimate(path, bounds, fps, width):
    """Per shot: transforms measured from the shot's sharpest frame, tracking forwards and backwards.

    Anchoring on the first frame let a blurred transition frame at the start of a shot (the tail of
    the previous whip) spoil every measurement after it; from the sharpest frame a bad frame can
    only spoil itself. Transforms are relative to that sharpest frame.
    """
    frames: list[list[np.ndarray]] = [[] for _ in bounds]
    span = (max(0.0, min(a for a, _ in bounds) - 1 / fps), max(b for _, b in bounds) + 1 / fps) if bounds else (0.0, None)
    for t, frame in iter_frames(path, sample_fps=fps, width=width, start=span[0], end=span[1]):
        idx = next((i for i, (a, b) in enumerate(bounds) if a - 0.5 / fps <= t < b - 0.5 / fps), None)
        if idx is not None:
            frames[idx].append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    curves, sharp = [], []
    for shot in frames:
        sharpness = [float(cv2.Laplacian(g, cv2.CV_32F).var()) for g in shot]
        curve = CameraCurve(fps=fps)
        if shot:
            key = int(np.argmax(sharpness))
            mats = [None] * len(shot)
            mats[key] = np.eye(3)
            for order in (range(key + 1, len(shot)), range(key - 1, -1, -1)):
                tracker, prev_lum = _ShotTracker(shot[key]), float(shot[key].mean())
                for i in order:
                    lum = float(shot[i].mean())
                    # flash frames: hold the last transform, keep tracking from the last good frame
                    mats[i] = tracker.m if abs(lum - prev_lum) > 40 else tracker.update(shot[i])
                    prev_lum = lum
            # Relative to the sharpest frame: the shot's "true" framing is the identity transform.
            h, w = shot[0].shape
            for m in mats:
                sc, r, x, y = _decompose(m, w, h)
                curve.s.append(round(sc, 5))
                curve.r.append(round(r, 5))
                curve.x.append(round(x, 5))
                curve.y.append(round(y, 5))
        curves.append(curve)
        sharp.append(sharpness)
    return curves, sharp


def register_curve(footage: str | Path, reference: str | Path, ref_start: float, n_frames: int, fps: float, width: int = 360) -> CameraCurve | None:
    """Per-frame transform that turns `footage` frames into the matching `reference` frames.

    Used when the footage is the reference's own: comparing the two pictures directly gives the
    editor's transform exactly, with no camera-motion guessing and no drift.
    """
    foot = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in iter_frames(footage, sample_fps=fps, width=width)][:n_frames]
    ref = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in iter_frames(reference, sample_fps=fps, width=width, start=ref_start, end=ref_start + (n_frames + 0.5) / fps)][:n_frames]
    n = min(len(foot), len(ref))
    if n < 2:
        return None
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)
    warp = np.eye(2, 3, dtype=np.float32)
    params, good = [], []
    h, w = foot[0].shape
    for i in range(n):
        try:
            cc, warp = cv2.findTransformECC(ref[i].astype(np.float32), foot[i].astype(np.float32), warp.copy(), cv2.MOTION_AFFINE, criteria, None, 5)
        except cv2.error:
            cc = 0.0
        if cc < 0.8:
            params.append(None)
            warp = np.eye(2, 3, dtype=np.float32) if not good else warp
            continue
        fwd = np.linalg.inv(np.vstack([warp, [0, 0, 1]]))  # ECC maps reference -> footage; we need footage -> reference
        params.append(_decompose(fwd, w, h))
        good.append(i)
    if len(good) < max(2, n // 2):
        return None
    arr = np.array([p if p is not None else (np.nan,) * 4 for p in params], dtype=np.float64)
    idx = np.arange(n)
    for c in range(4):  # fill frames that could not be registered (flashes, blur) from their neighbours
        ok = ~np.isnan(arr[:, c])
        arr[:, c] = np.interp(idx, idx[ok], arr[ok, c])
    return CameraCurve(fps=fps, s=arr[:, 0].tolist(), r=arr[:, 1].tolist(), x=arr[:, 2].tolist(), y=arr[:, 3].tolist())


def motion_velocities(path: str | Path, bounds: list[tuple[float, float]], fps: float, width: int = 360) -> list[np.ndarray]:
    """Per shot, the frame-to-frame camera change (log-scale, rotation, x, y) as an (n, 4) array.

    Local, so one tracking hiccup affects one frame instead of the rest of the shot; used for scoring.
    """
    out: list[list] = [[] for _ in bounds]
    shot, prev = -1, None
    for t, frame in iter_frames(path, sample_fps=fps, width=width):
        idx = next((i for i, (a, b) in enumerate(bounds) if a - 0.5 / fps <= t < b - 0.5 / fps), None)
        if idx is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if idx == shot and prev is not None:
            m = frame_motion(prev, gray)
            step = np.vstack([m, [0, 0, 1]]) if m is not None else _ecc_step(prev, gray)  # blurred frames: whole-image fallback
            if step is None:
                out[idx].append((np.nan,) * 4)
            else:
                h, w = gray.shape
                s, r, x, y = _decompose(step, w, h)
                out[idx].append((math.log(max(s, 1e-3)), r, x, y))
        shot, prev = idx, gray
    return [np.array(v, dtype=np.float64).reshape(-1, 4) for v in out]
