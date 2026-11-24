"""After Effects expression functions and keyframe easing, as numpy curve operators.

Sources (techniques.yaml: ease.bezier, ease.easy_ease, expr.wiggle):
- Cubic-bezier easing: W3C CSS Easing Level 1, the same curve Lottie stores per keyframe.
- linear/ease/easeIn/easeOut, wiggle, loopOut, smooth: the AE expression reference
  (ae-expressions.docsforadobe.dev). AE documents what these do, not their exact curves: `ease` uses
  Easy Ease's bezier (speed 0, 33.33 % influence) and wiggle's noise is our own Perlin noise, so those
  two match AE's shape and statistics, not its exact numbers.
"""

from __future__ import annotations

import numpy as np

from .. import knowledge

EASY_EASE = tuple(knowledge.param("ease.easy_ease", "cubic_bezier"))  # (0.333, 0, 0.667, 1)


def cubic_bezier(x1: float, y1: float, x2: float, y2: float, x) -> np.ndarray:
    """CSS/Lottie easing: y at progress x (0..1) for the curve (0,0) (x1,y1) (x2,y2) (1,1)."""
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    x1, x2 = min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)  # the spec keeps x monotonic

    def bx(u):
        return 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u**2 * x2 + u**3

    def dbx(u):
        return 3 * (1 - u) ** 2 * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u**2 * (1 - x2)

    u = x.copy()
    for _ in range(8):  # Newton, then bisection where the slope is too flat to trust
        d = dbx(u)
        ok = np.abs(d) > 1e-6
        u = np.where(ok, u - (bx(u) - x) / np.where(ok, d, 1), u)
        u = np.clip(u, 0, 1)
    lo, hi = np.zeros_like(x), np.ones_like(x)
    for _ in range(30):
        bad = np.abs(bx(u) - x) > 1e-7
        if not bad.any():
            break
        mid = (lo + hi) / 2
        below = bx(mid) < x
        lo, hi = np.where(below, mid, lo), np.where(below, hi, mid)
        u = np.where(bad, mid, u)
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u**2 * y2 + u**3


def _interp(t, t_min, t_max, v1, v2, shape):
    t = np.asarray(t, dtype=np.float64)
    p = np.clip((t - t_min) / (t_max - t_min), 0, 1) if t_max != t_min else (t >= t_max).astype(np.float64)
    k = shape(p)
    v1, v2 = np.asarray(v1, dtype=np.float64), np.asarray(v2, dtype=np.float64)
    return v1 + (v2 - v1) * (k[..., None] if v1.ndim else k)


def linear(t, t_min, t_max, v1, v2):
    """AE linear(t, tMin, tMax, value1, value2): clamped outside [tMin, tMax]."""
    return _interp(t, t_min, t_max, v1, v2, lambda p: p)


def ease(t, t_min, t_max, v1, v2):
    """AE ease(): zero speed at both ends (Easy Ease's bezier)."""
    return _interp(t, t_min, t_max, v1, v2, lambda p: cubic_bezier(*EASY_EASE, p))


def ease_in(t, t_min, t_max, v1, v2):
    """AE easeIn(): eased only where it arrives (zero speed at tMax)."""
    return _interp(t, t_min, t_max, v1, v2, lambda p: cubic_bezier(0.0, 0.0, EASY_EASE[2], 1.0, p))


def ease_out(t, t_min, t_max, v1, v2):
    """AE easeOut(): eased only where it leaves (zero speed at tMin)."""
    return _interp(t, t_min, t_max, v1, v2, lambda p: cubic_bezier(EASY_EASE[0], 0.0, 1.0, 1.0, p))


def noise(x, seed: int = 0) -> np.ndarray:
    """1-D Perlin gradient noise in about [-1, 1]: nearby inputs give nearby outputs, like AE's noise()."""
    x = np.asarray(x, dtype=np.float64)
    grads = np.random.default_rng(seed).uniform(-1, 1, 512)
    i0 = np.floor(x).astype(np.int64)
    f = x - i0
    g0, g1 = grads[i0 & 511], grads[(i0 + 1) & 511]
    fade = f * f * f * (f * (f * 6 - 15) + 10)
    return 2 * (g0 * f + (g1 * (f - 1) - g0 * f) * fade)


def wiggle(t, freq: float, amp: float, octaves: int | None = None, amp_mult: float | None = None, seed: int = 0) -> np.ndarray:
    """AE wiggle(freq, amp, octaves=1, amp_mult=0.5, t): the offset to add to a property's value.

    One array per call; call once per dimension with different seeds (AE's dimensions move independently).
    """
    octaves = int(octaves if octaves is not None else knowledge.param("expr.wiggle", "octaves"))
    amp_mult = float(amp_mult if amp_mult is not None else knowledge.param("expr.wiggle", "amp_mult"))
    t = np.asarray(t, dtype=np.float64)
    out = np.zeros_like(t)
    for k in range(max(1, octaves)):
        out += amp * amp_mult**k * noise(t * freq * 2**k + 17.31 * k, seed + k)
    return out


def loop_out(t, key_t: list[float], key_v: list, kind: str = "cycle", num_keyframes: int = 0, value=None) -> np.ndarray:
    """AE loopOut(type, numKeyframes): carry a keyframed curve past its last key.

    `value(t)` evaluates the keyframed property inside its keys (defaults to linear between them).
    cycle repeats, pingpong goes back and forth, offset repeats shifted by the loop's change,
    continue keeps the last key's speed.
    """
    t = np.asarray(t, dtype=np.float64)
    key_t = list(key_t)
    value = value or (lambda x: np.interp(x, key_t, np.asarray(key_v, dtype=np.float64)))
    first = key_t[-1 - num_keyframes] if 0 < num_keyframes < len(key_t) else key_t[0]
    last = key_t[-1]
    span = last - first
    if span <= 0:
        return value(np.minimum(t, last))
    after = t > last
    if kind == "continue":
        eps = min(1e-3, span / 10)
        speed = (value(np.array(last)) - value(np.array(last - eps))) / eps
        return np.where(after, value(np.array(last)) + speed * (t - last), value(np.minimum(t, last)))
    phase = np.mod(t - first, span)
    n = np.floor((t - first) / span)
    if kind == "pingpong":
        local = np.where(np.mod(n, 2) == 1, last - phase, first + phase)
    else:
        local = first + phase
    v = value(np.where(after, local, np.minimum(t, last)))
    if kind == "offset":
        v = v + np.where(after, n * (value(np.array(last)) - value(np.array(first))), 0)
    return v


def smooth(values, width_frames: int = 6) -> np.ndarray:
    """AE smooth(width, samples): a box average over `width_frames` around each frame."""
    v = np.asarray(values, dtype=np.float64)
    if width_frames <= 1 or len(v) < 2:
        return v
    k = np.ones(width_frames) / width_frames
    pad = width_frames // 2
    return np.convolve(np.pad(v, (pad, width_frames - 1 - pad), mode="edge"), k, mode="valid")


def bezier_point(p0, p1, p2, p3, u) -> np.ndarray:
    """Point on a cubic bezier (spatial keyframe paths: p1 = p0 + out tangent, p2 = p3 + in tangent)."""
    u = np.asarray(u, dtype=np.float64)[..., None]
    p0, p1, p2, p3 = (np.asarray(p, dtype=np.float64) for p in (p0, p1, p2, p3))
    return (1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1 + 3 * (1 - u) * u**2 * p2 + u**3 * p3


def along_path(p0, p1, p2, p3, s: float, steps: int = 64) -> np.ndarray:
    """The point a share `s` of the way along a bezier path by length (AE moves along spatial paths by distance)."""
    us = np.linspace(0, 1, steps + 1)
    pts = bezier_point(p0, p1, p2, p3, us)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] < 1e-9:
        return pts[0]
    u = float(np.interp(min(max(s, 0.0), 1.0) * cum[-1], cum, us))
    return bezier_point(p0, p1, p2, p3, u)
