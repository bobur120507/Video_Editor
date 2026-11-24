"""Transitions that put two shots on screen at once, composited across a cut after the shots are rendered.

panel:  the next shot enters as a window growing from a corner (TikTok car edit, 0.97-1.23 s)
portal: the next shot zooms out of its wheel; the wheel's opening shows the previous shot, then a
        white flash (2.87-3.13 s)
cutout: the next shot's car, cut out of its background, rises in from below (4.80-4.93 s)

The frames before the cut show the next shot's first frame (motion-blurred, so a held frame does not
read as frozen); frames after the cut show its own frames.
"""

from __future__ import annotations

import logging
import math
import subprocess
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from .. import vision

log = logging.getLogger(__name__)

# Frames before / after the cut, as measured on the TikTok car edit.
# Portal: the opening covers the frame 5 frames before the cut (2.80 s), shrinks onto the wheel by
# 3.00 s, flashes white, shows the rim, flashes again (3.10 s). Cut-out: the car shows at the bottom
# 5 frames before the cut (4.77 s). Shorter versions put the biggest change after the cut and the cut
# detector read it 2 frames late (McLaren v13).
FRAMES = {"panel": (7, 2), "portal": (5, 5), "cutout": (5, 0), "zoom": (3, 4), "silhouette": (0, 5)}
# Blur (sigma, share of the frame width) per unit of log zoom on the zoomed-in next shot. 0.004 smeared 83% of the
# frames around the BMW template's first portal (v3), whose own wheel arch stays crisp.
PORTAL_BLUR = 0.0013
# The cut-out car enters smaller and fainter, like the TikTok's small, blurred one. At 70% / 40% it read as
# a cut of its own (detector score 27.4 > 27, McLaren v15 at 4.80 s); 55% / 60% scored 24.8.
CUTOUT_START_SIZE = 0.5  # the cut-out car's size when it enters, growing to full size as it lands
CUTOUT_FADE = 0.7  # share of the rise over which the car fades in


def ease(u: float) -> float:
    return 1 - (1 - min(max(u, 0.0), 1.0)) ** 3


STREAK = 0.5  # motion-blur length per pixel moved (a 180-degree shutter); 1.0 smeared the cut-outs (BMW template v3)


def _streak(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Motion blur along (dx, dy) pixels."""
    n = int(min(41, max(1, round(STREAK * math.hypot(dx, dy)))))
    if n < 3:
        return img
    k = np.zeros((n, n), np.float32)
    c = (n - 1) / 2
    for t in np.linspace(-0.5, 0.5, n):
        k[int(round(c + t * dy * (n - 1) / max(math.hypot(dx, dy), 1e-6))),
          int(round(c + t * dx * (n - 1) / max(math.hypot(dx, dy), 1e-6)))] = 1
    return cv2.filter2D(img, -1, k / k.sum())


# ---------------------------------------------------------------- corner panel

PANEL_START = {  # normalized (x0, y0, x1, y1) of the window on its first frame: a sliver at the corner
    "top_right": (0.62, -0.12, 1.12, 0.06),
    "top_left": (-0.12, -0.12, 0.38, 0.06),
    "bottom_right": (0.62, 0.94, 1.12, 1.12),
    "bottom_left": (-0.12, 0.94, 0.38, 1.12),
}


def panel_frame(a: np.ndarray, b: np.ndarray, u: float, prev_u: float, corner: str = "top_right") -> np.ndarray:
    """Previous shot `a` with the next shot `b` shown through a window that has grown `u` (0..1) of the
    way from a sliver at `corner` to the whole frame."""
    h, w = a.shape[:2]
    s = np.array(PANEL_START.get(corner, PANEL_START["top_right"]))
    full = np.array([0.0, 0.0, 1.0, 1.0])

    def rect(v: float) -> np.ndarray:
        return (s + (full - s) * ease(v)) * [w, h, w, h]

    r, r0 = rect(u), rect(prev_u)
    move = (r[:2] + r[2:]) / 2 - (r0[:2] + r0[2:]) / 2  # how far the window's centre moved this frame
    mask = np.zeros((h, w), np.float32)
    x0, y0, x1, y1 = (int(round(v)) for v in r)
    mask[max(0, y0):max(0, min(h, y1)), max(0, x0):max(0, min(w, x1))] = 1
    mask = cv2.GaussianBlur(mask, (0, 0), max(1.0, 0.004 * w))
    inside = _streak(b, *move)
    return (a * (1 - mask[..., None]) + inside * mask[..., None]).astype(np.uint8)


# ---------------------------------------------------------------- wheel portal


WHEEL_PROMPTS = ["a close-up photo of a car wheel with its rim and tire", "a car headlight", "a car hood", "a car windshield",
                 "a car door", "a side mirror", "a wet road", "a sidewalk", "a tree", "a building"]
WHEEL_CONFIDENCE = 0.5  # CLIP's share for "wheel" among the prompts
RIM_SUPPORT = 0.65  # share of a nearby circle's outline that must be a real edge to snap to it


def find_wheel(frame: np.ndarray) -> tuple[float, float, float] | None:
    """(x, y, radius) in pixels of the clearest wheel, or None."""
    return wheel(frame)[1]


def wheel(frame: np.ndarray) -> tuple[float, tuple[float, float, float] | None]:
    """(how sure CLIP is that a wheel is visible, 0..1; its (x, y, radius) in pixels or None).

    Circle shapes alone pick up headlights, hood curves and the road; CLIP judges what square
    patches in the lower part of the car show, and the most wheel-like one wins if it is confident.
    """
    if not vision.available():
        return 0.0, None
    h, w = frame.shape[:2]
    scale = 480 / w
    small = cv2.resize(frame, (480, int(h * scale)))
    box = vision.main_subject([small])
    if box is None:
        return 0.0, None
    sh, sw = small.shape[:2]
    bx0, by0, bx1, by1 = box.x0 * sw, box.y0 * sh, box.x1 * sw, box.y1 * sh
    bw, bh = bx1 - bx0, by1 - by0
    patches, where = [], []
    for frac in (0.2, 0.3, 0.42, 0.6):
        size = frac * min(bw, 1.6 * bh)
        if size < 24:
            continue
        step = size / 3
        for cy in np.arange(by0 + 0.45 * bh, by1 - 0.2 * size + 1, step):
            for cx in np.arange(bx0 + size / 2, bx1 - size / 2 + 1, step):
                x0, y0 = int(cx - size / 2), int(cy - size / 2)
                if x0 < 0 or y0 < 0 or x0 + size > sw or y0 + size > sh:
                    continue
                patches.append(small[y0:y0 + int(size), x0:x0 + int(size)])
                where.append((cx, cy, size / 2))
    if not patches:
        return 0.0, None
    img = vision.embed_images(patches)
    txt = _wheel_texts()
    logits = 100 * img @ txt.T
    prob = np.exp(logits - logits.max(axis=1, keepdims=True))
    prob = prob[:, 0] / prob.sum(axis=1)
    if prob.max() < WHEEL_CONFIDENCE:
        return float(prob.max()), None
    # A big wheel is covered by several squares, each showing part of it: combine the confident ones.
    pts = np.array(where)
    top = pts[int(np.argmax(prob))]
    near = np.hypot(pts[:, 0] - top[0], pts[:, 1] - top[1]) <= 1.5 * top[2]  # only squares around the best one
    keep = near & (prob >= max(WHEEL_CONFIDENCE, 0.8 * prob.max()))
    wts = prob[keep] / prob[keep].sum()
    cx, cy, r = float(wts @ pts[keep, 0]), float(wts @ pts[keep, 1]), float(wts @ pts[keep, 2])
    cx, cy, r = _snap_to_rim(small, cx, cy, r)
    return float(prob.max()), (cx / scale, cy / scale, r / scale)


def _snap_to_rim(img: np.ndarray, cx: float, cy: float, r: float) -> tuple[float, float, float]:
    """The circle with the strongest outline near (cx, cy, r), or the estimate itself when none is clear."""
    gray = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 3)
    edges = cv2.dilate(cv2.Canny(gray, 50, 140), np.ones((5, 5), np.uint8))
    c = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=max(4, r * 0.3), param1=100, param2=20,
                         minRadius=max(4, int(0.7 * r)), maxRadius=int(2.0 * r))
    best, best_support = (cx, cy, 0.85 * r), RIM_SUPPORT
    angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    for x, y, rr in ([] if c is None else c[0]):
        if np.hypot(x - cx, y - cy) > r:
            continue
        px = np.clip((x + rr * np.cos(angles)).astype(int), 0, img.shape[1] - 1)
        py = np.clip((y + rr * np.sin(angles)).astype(int), 0, img.shape[0] - 1)
        support = float((edges[py, px] > 0).mean())
        if support > best_support:
            best, best_support = (float(x), float(y), float(rr)), support
    return best


_TEXTS = None


def _wheel_texts() -> np.ndarray:
    global _TEXTS
    if _TEXTS is None:
        _TEXTS = vision.embed_texts(WHEEL_PROMPTS)
    return _TEXTS


def _iris(frame: np.ndarray) -> tuple[float, float, float]:
    """A round opening for a portal when no wheel is clear: on the car's lower half, or the frame centre."""
    h, w = frame.shape[:2]
    box = vision.main_subject([cv2.resize(frame, (480, int(h * 480 / w)))]) if vision.available() else None
    if box is None:
        return w / 2, h / 2, 0.18 * w
    return box.cx * w, (box.y0 + 0.65 * box.h) * h, 0.22 * box.w * w


def portal_frame(a: np.ndarray, b: np.ndarray, u: float, wheel: tuple[float, float, float], flash: float = 0.0,
                 opening: bool = True) -> np.ndarray:
    """Next shot `b` zoomed into its wheel and `u` (0..1) of the way back out; the wheel's opening
    shows the previous shot `a`, shrunk to fit it; `flash` (0..1) whitens the opening. opening=False:
    the wheel itself (the rim) shows, under the flash."""
    h, w = a.shape[:2]
    cx, cy, r = wheel
    start = 1.1 * math.hypot(w, h) / max(r, 1.0)  # the opening first covers the whole frame
    # Steady in log scale, like the reference: the opening shrinks by the same factor every frame.
    s = math.exp(math.log(start) * (1 - min(max(u, 0.0), 1.0)))
    centre = np.array([w / 2, h / 2]) + (np.array([cx, cy]) - [w / 2, h / 2]) * min(max(u, 0.0), 1.0)
    m = np.float32([[s, 0, centre[0] - s * cx], [0, s, centre[1] - s * cy]])
    zoomed = cv2.warpAffine(b, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    if s > 1.05:
        # Zoomed this far in a lens shows a smooth blur, not sharp texture (the TikTok's: a smooth blue body).
        # Sharp raindrops at 40x streaked and doubled the motion (5.4 vs 3.0 px/frame, McLaren test clip).
        zoomed = cv2.GaussianBlur(zoomed, (0, 0), PORTAL_BLUR * w * math.log(s))
    hole = r * s
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot(xx - centre[0], yy - centre[1])
    inside = np.clip((hole - d) / max(2.0, 0.06 * hole), 0, 1)
    rim = np.exp(-((d - hole) / max(2.0, 0.12 * hole)) ** 2) * 0.6  # the wheel arch's shadow around the opening
    # The previous shot stays where it is, full size: the opening closes around it like an iris (the
    # TikTok's car keeps its size inside the shrinking opening). Shrinking it as well added a second
    # big zoom and doubled the motion (5.4 vs the TikTok's 3.0 px/frame, McLaren test clip).
    inner = zoomed.copy() if not opening else a.copy()
    if flash > 0:
        glow = np.clip(1 - d / max(hole * 1.3, 1.0), 0, 1) ** 0.5 * flash
        inner = inner * (1 - glow[..., None]) + 255 * glow[..., None]
    if not opening:
        rim = rim * 0  # no arch shadow once the rim itself shows
    out = zoomed.astype(np.float32) * (1 - rim[..., None] * (1 - inside[..., None]))
    out = out * (1 - inside[..., None]) + inner.astype(np.float32) * inside[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- car cut-out


def car_mask(frame: np.ndarray) -> np.ndarray | None:
    """Soft mask (0..1) of the main subject: its YOLOX box refined by GrabCut."""
    if not vision.available():
        return None
    h, w = frame.shape[:2]
    scale = 360 / w
    small = cv2.resize(frame, (360, int(h * scale)))
    box = vision.main_subject([small])
    if box is None:
        return None
    sh, sw = small.shape[:2]
    x0, y0, x1, y1 = int(box.x0 * sw), int(box.y0 * sh), int(box.x1 * sw), int(box.y1 * sh)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    m = np.zeros((sh, sw), np.uint8)
    cv2.grabCut(small, m, (x0, y0, x1 - x0, y1 - y0), np.zeros((1, 65)), np.zeros((1, 65)), 4, cv2.GC_INIT_WITH_RECT)
    fg = np.isin(m, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype(np.float32)
    return cv2.GaussianBlur(cv2.resize(fg, (w, h)), (0, 0), max(1.0, 0.003 * w))


def cutout_frame(a: np.ndarray, b: np.ndarray, mask: np.ndarray, u: float, prev_u: float, edge: str = "bottom") -> np.ndarray:
    """Previous shot `a` with the next shot's car (b * mask) risen `u` (0..1) of the way from below the frame.

    The car fades in over the first frames and grows from CUTOUT_START_SIZE to full size while it rises:
    entering at full size and opacity read as a cut of its own (4.80 s, McLaren v14)."""
    h, w = a.shape[:2]
    horizontal = edge in ("left", "right")
    span, sign = (w, -1 if edge == "left" else 1) if horizontal else (h, 1 if edge != "top" else -1)
    off, prev = (1 - ease(u)) * span * sign, (1 - ease(prev_u)) * span * sign
    k = CUTOUT_START_SIZE + (1 - CUTOUT_START_SIZE) * ease(u)
    cx, cy = w / 2, h / 2
    ox, oy = (off, 0.0) if horizontal else (0.0, off)
    m = np.float32([[k, 0, cx - k * cx + ox], [0, k, cy - k * cy + oy]])
    layer = cv2.warpAffine(b, m, (w, h), borderValue=0)
    alpha = cv2.warpAffine(mask, m, (w, h), borderValue=0)
    sx, sy = (prev - off, 0) if horizontal else (0, prev - off)
    layer, alpha = _streak(layer, sx, sy), _streak(alpha, sx, sy)
    alpha = alpha * min(1.0, u / CUTOUT_FADE)
    return np.clip(a * (1 - alpha[..., None]) + layer * alpha[..., None], 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- zoom-through and white silhouette

ZOOM_IN = 2.2  # how far the previous shot zooms in before the cut (and the next shot zooms out from)
ZOOM_SAMPLES = 7  # zoom-blur samples


def _zoomed(img: np.ndarray, scale: float, blur_to: float) -> np.ndarray:
    """img scaled about its centre, with zoom blur spread from `scale` to `blur_to`."""
    h, w = img.shape[:2]
    acc = np.zeros(img.shape, np.float32)
    n = int(min(32, max(ZOOM_SAMPLES, abs(scale - blur_to) * w / 12)))  # one sample per ~12 px of spread: no stepping
    for s_ in np.linspace(scale, blur_to, n):
        m = np.float32([[s_, 0, w / 2 * (1 - s_)], [0, s_, h / 2 * (1 - s_)]])
        acc += cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return np.clip(acc / n + 0.5, 0, 255).astype(np.uint8)


def zoom_frame(a: np.ndarray, b: np.ndarray, u: float, prev_u: float, zoom_in: float = ZOOM_IN) -> np.ndarray:
    """Zoom-through: the previous shot rushes in towards its centre, the next shot lands out of the same zoom."""
    if u <= 0.5:
        s0, s1 = 1 + (zoom_in - 1) * (2 * prev_u) ** 2, 1 + (zoom_in - 1) * (2 * u) ** 2
        return _zoomed(a, s1, s0)
    s0, s1 = zoom_in - (zoom_in - 1) * ease(2 * prev_u - 1), zoom_in - (zoom_in - 1) * ease(2 * u - 1)
    return _zoomed(b, s1, s0)


def zoom_amount(size: float) -> float:
    """How far a zoom-through zooms, from the reference's measured zoom per frame (BMW: +0.016 / +0.021 ->
    about 1.2x). A fixed 2.2x blurred the McLaren v2 edit far past the reference (2026-11-18)."""
    return float(np.exp(np.clip(abs(size), 0.008, 0.05) * 10)) if size else 1.2


def silhouette_frame(b: np.ndarray, mask: np.ndarray, strength: float) -> np.ndarray:
    """The next shot with its car filled white (`strength` 0..1) and a soft glow around it."""
    if strength <= 0:
        return b
    h, w = b.shape[:2]
    glow = cv2.GaussianBlur(mask, (0, 0), 0.02 * w)
    white = np.clip(mask + 0.6 * glow, 0, 1)[..., None] * strength
    return np.clip(b * (1 - white) + 255 * white, 0, 255).astype(np.uint8)


SILHOUETTE = (1.0, 0.75, 0.45, 0.2, 0.05)  # white strength on the cut frame and after (the BMW edit fades in ~5)


# ---------------------------------------------------------------- applying them to rendered shots


def _reader(path: Path, w: int, h: int, frames: int = 0):
    cmd = ["ffmpeg", "-loglevel", "error", "-sws_flags", "accurate_rnd+full_chroma_int+full_chroma_inp", "-i", str(path),
           "-vf", "scale=in_color_matrix=bt709:in_range=tv,format=bgr24", *(["-frames:v", str(frames)] if frames else []),
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _writer(dst: Path, w: int, h: int, fps: int, encode: list):
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-vf", "format=gbrp16le,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", *map(str, encode), str(dst)],
        stdin=subprocess.PIPE)


def _last_frame(path: Path, w: int, h: int) -> np.ndarray | None:
    last = None
    for last in _reader(path, w, h):
        pass
    return last


def _first_frame(path: Path, w: int, h: int) -> np.ndarray | None:
    return next(iter(_reader(path, w, h, frames=1)), None)


def apply(kind: str, prev: Path, nxt: Path, w: int, h: int, fps: int, encode: list, frames: int = 0, direction: str = "",
          size: float = 0.0, flash: bool = False) -> bool:
    """Composite one transition across the cut between rendered shots `prev` and `nxt` (rewritten in place)."""
    pre, post = FRAMES[kind]
    if frames:
        pre = max(1, round(frames * pre / (pre + post)))
        post = frames - pre
    b0 = _first_frame(nxt, w, h)
    if b0 is None:
        return False
    wheel = mask = None
    if kind == "portal":
        wheel = find_wheel(b0) or _iris(b0)  # no clear wheel: a round opening on the car (or the frame centre)
    if kind == "silhouette" and size:
        # The white car is whichever car matches the reference's white area: the BMW's 7.03 s is the previous
        # shot's small, distant car over the next shot (3% of the frame), not the next shot's close-up (~40%).
        a_last = _last_frame(prev, w, h)
        options = [(m, which) for m, which in ((car_mask(b0), "next"), (car_mask(a_last) if a_last is not None else None, "prev"))
                   if m is not None and m.mean() >= 0.005]
        if options:
            mask = min(options, key=lambda o: abs(o[0].mean() - size))[0]
    if kind in ("cutout", "silhouette") and mask is None:
        mask = car_mask(b0)
        if mask is None or mask.mean() < 0.01:
            if kind == "silhouette":
                log.warning("silhouette transition: no car found in the next shot, plain cut instead")
                return False
            mask = np.ones(b0.shape[:2], np.float32)  # no car to cut out: the whole next shot slides in
    total = pre + post

    def compose(a: np.ndarray, b: np.ndarray, j: int) -> np.ndarray:
        u, pu = (j + 1) / total, j / total
        if kind == "panel":
            return panel_frame(a, b, u, pu, direction or "top_right")
        if kind == "portal":
            zoom_end = total - 3 if flash else total - 1  # with a flash: zoom out; flash; the rim; a softer flash
            light = (1.0 if j == zoom_end else 0.6 if j == zoom_end + 2 else 0.0) if flash else 0.0
            return portal_frame(a, b, min(1.0, (j + 1) / max(zoom_end, 1)), wheel, light, opening=j <= zoom_end)
        if kind == "zoom":
            return zoom_frame(a, b, u, pu, zoom_amount(size))
        if kind == "silhouette":
            return silhouette_frame(b, mask, SILHOUETTE[min(j - pre, len(SILHOUETTE) - 1)])
        return cutout_frame(a, b, mask, u, pu, direction or "bottom")

    out_prev, out_next = prev.with_name(prev.stem + "_tr.mp4"), nxt.with_name(nxt.stem + "_tr.mp4")
    writer = _writer(out_prev, w, h, fps, encode)
    tail: deque = deque(maxlen=max(pre, 1))
    last_a = None
    for f in _reader(prev, w, h):
        if len(tail) == tail.maxlen:
            writer.stdin.write(tail[0].tobytes())
        tail.append(f)
        last_a = f
    if pre == 0:  # nothing before the cut changes (a silhouette): write the held last frame as it is
        for f in tail:
            writer.stdin.write(f.tobytes())
        tail.clear()
    for j, a in enumerate(tail):
        writer.stdin.write(compose(a, b0, j + (pre - len(tail))).tobytes())
    writer.stdin.close()
    ok = writer.wait() == 0
    writer = _writer(out_next, w, h, fps, encode)
    for k, b in enumerate(_reader(nxt, w, h)):
        if k < post and last_a is not None:
            b = compose(last_a, b, pre + k)
        writer.stdin.write(b.tobytes())
    writer.stdin.close()
    ok = writer.wait() == 0 and ok
    if not ok:
        log.warning("%s transition failed while encoding, plain cut instead", kind)
        return False
    out_prev.replace(prev)
    out_next.replace(nxt)
    return True
