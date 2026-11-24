"""Transitions that mix two shots on screen: the wheel portal (detected here), the corner panel and
the car cut-out (marked in the move review; see labels.py).

Wheel portal: the next shot is zoomed hugely into its wheel and zooms out, while the wheel's opening
shows the previous shot. Its signature is one circle that shrinks steadily over several frames
around the cut: on the TikTok car edit 134 -> 27 px (at 216 px width) over 8 frames, while circles
elsewhere (wheels, headlights) only jitter or grow (2026-11-17).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..media import iter_frames

WIDTH = 216
WINDOW = 9  # frames searched on each side of a cut
MIN_RUN = 5  # frames the circle keeps shrinking
MIN_SHRINK = 2.5  # first radius / last radius
NOISE = 1.05  # a frame's radius may exceed the one before by this much and still count as shrinking


def _circle(frame: np.ndarray) -> tuple[float, float, float] | None:
    w = frame.shape[1]
    g = cv2.medianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 5)
    c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.5, minDist=w, param1=90, param2=45,
                         minRadius=int(0.12 * w), maxRadius=int(1.2 * w))
    return None if c is None else tuple(float(v) for v in c[0][0])


def shrinking_circle(circles: list[tuple[float, float, float] | None], width: int) -> bool:
    """True when consecutive frames hold one circle that keeps shrinking (centre steady) by MIN_SHRINK."""
    run: list[tuple[float, float, float]] = []
    for c in circles + [None]:
        ok = c is not None and run and c[2] <= run[-1][2] * NOISE and np.hypot(c[0] - run[-1][0], c[1] - run[-1][1]) < 0.25 * width
        if ok:
            run.append(c)
            continue
        if len(run) >= MIN_RUN and max(r[2] for r in run) / run[-1][2] >= MIN_SHRINK:
            return True
        run = [c] if c is not None else []
    return False


def find_portals(path: str | Path, cuts: list[float], fps: float) -> set[float]:
    """Cuts (seconds, as given) that are wheel portals."""
    frames = [f for _, f in iter_frames(path, sample_fps=fps, width=WIDTH)]
    out = set()
    for cut in cuts:
        k = round(cut * fps)
        window = frames[max(0, k - WINDOW):k + WINDOW]
        if window and shrinking_circle([_circle(f) for f in window], WIDTH):
            out.add(cut)
    return out


# ---------------------------------------------------------------- gradual transitions

GAP, SPAN = 3, 6  # frames: compare the average of SPAN frames ending GAP before a frame with SPAN starting GAP after
GRADUAL = 0.155  # CLIP change (1 - cosine) above which the shot changed. BMW garage edit: its masked slides,
#                  portals and zoom-throughs peaked 0.159-0.341, its in-shot moves at most 0.154 (2026-11-18)
EDGE = 10  # frames: no transitions this close to the start or end of the video (fade-ins and end cards)


def clip_change(path: str | Path, fps: float) -> np.ndarray:
    """Per frame: how much what the picture shows (CLIP) changes from just before it to just after it."""
    from .. import vision

    emb = vision.embed_images([f for _, f in iter_frames(path, sample_fps=fps, width=224)])
    d = np.zeros(len(emb))
    for i in range(GAP + SPAN, len(emb) - GAP - SPAN):
        a, b = emb[i - GAP - SPAN:i - GAP].mean(axis=0), emb[i + GAP:i + GAP + SPAN].mean(axis=0)
        d[i] = 1 - float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    return d


def gradual_cuts(path: str | Path, fps: float, cuts: list[float], change: np.ndarray | None = None) -> list[float]:
    """Shot changes the frame-difference detector misses: whips, slides and masked reveals spread over several
    frames, so no two neighbouring frames differ much, but what the picture shows has changed. Returns only
    those more than SPAN + GAP frames from a known cut."""
    from .. import vision

    if not vision.available():
        return []
    d = clip_change(path, fps) if change is None else change
    known = [round(c * fps) for c in cuts]
    out: list[int] = []
    for i in range(EDGE, len(d) - EDGE):
        if d[i] > GRADUAL and d[i] >= d[i - 1] and d[i] >= d[i + 1]:
            if all(abs(i - k) > GAP + SPAN - 1 for k in known + out):
                out.append(i)
    return [round(i / fps, 3) for i in out]


# ---------------------------------------------------------------- transition types

PORTAL_ZOOM, THROUGH_ZOOM = -0.015, 0.014  # mean zoom of the 6 fastest frames: portals zoom out (BMW -0.018,
# -0.019, TikTok -0.023), zoom-throughs zoom in (+0.016, +0.021); slides and whips stay within +-0.012 (2026-11-18)
WHITE, GLOW_AREA, GLOW_BEFORE = 235, 0.02, 0.005  # Lab L, share of the frame: a white silhouette on the cut frame
FLASH_RISE = 0.02  # white share rising this much during a portal: it flashes as it lands (TikTok +0.05, BMW +0.001)


def _zoom(path: str | Path, cut: float, fps: float) -> float:
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for _, f in iter_frames(path, sample_fps=fps, width=144, start=max(0.0, cut - 0.4), end=cut + 0.4)]
    if len(g) < 3:
        return 0.0
    h, w = g[0].shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rx, ry = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    rows = []
    for a, b in zip(g, g[1:]):
        f = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        z = float(((f[..., 0] / w) * rx + (f[..., 1] / h) * ry).mean() / (rx ** 2 + ry ** 2).mean())
        rows.append((float(np.hypot(f[..., 0], f[..., 1]).mean()), z))
    rows.sort()
    return float(np.mean([z for _, z in rows[-6:]]))


def _white_area(frame: np.ndarray) -> float:
    m = (cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[..., 0] > WHITE).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    return 0.0 if n < 2 else float(st[1:, cv2.CC_STAT_AREA].max()) / m.size


def classify(path: str | Path, cuts: list[float], fps: float) -> dict[float, dict]:
    """Transition type at each cut where one is clear: "portal" (the next shot zooms out of a wheel with the
    previous shot inside), "zoom" (zoom-through: the previous shot zooms into the next), "silhouette" (a hard cut
    whose car flashes as a white silhouette and fades). Slides and cut-outs look like whips to every measure tried, so they are
    left to the move review. Each: {"kind", "size" (zoom per frame / white share), "flash" (portal)}."""
    frames = {round(t * fps): f for t, f in iter_frames(path, sample_fps=fps, width=144)}
    out = {}
    for cut in cuts:
        z = _zoom(path, cut, fps)
        k = round(cut * fps)
        before = max((_white_area(frames[i]) for i in range(k - 3, k) if i in frames), default=0.0)
        at = _white_area(frames[k]) if k in frames else 0.0
        after = [_white_area(frames[i]) for i in (k + 1, k + 2) if i in frames]
        if at > GLOW_AREA and before < GLOW_BEFORE and all(a < 0.5 * at for a in after):
            out[cut] = {"kind": "silhouette", "size": round(at, 4), "flash": False}
        elif z < PORTAL_ZOOM and _opens_on_wheel(path, cut):
            inside = [_white_area(frames[i]) for i in range(k, k + 9) if i in frames]
            flash = bool(inside) and max(inside) - before > FLASH_RISE
            out[cut] = {"kind": "portal", "size": round(z, 4), "flash": flash}
        elif z > THROUGH_ZOOM:
            out[cut] = {"kind": "zoom", "size": round(z, 4), "flash": False}
    return out


def _opens_on_wheel(path: str | Path, cut: float) -> bool:
    """A portal zooms out of a wheel, so the next shot shows one clearly once it has landed. A plain camera
    zoom-out has none (reel_1's cartoon at 2.7 s: CLIP 0.00; the portals 0.99-1.00)."""
    from ..media import frame_at
    from ..render.transitions import wheel

    frame = frame_at(path, cut + 0.35, width=540)
    if frame is None:
        return False
    sure, circle = wheel(frame)
    return circle is not None and sure >= 0.5
