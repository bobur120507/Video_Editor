"""The published techniques behind popular After Effects plugins, in numpy/OpenCV.

bloom              Deep Glow-style glow: bright-pass, blurs of growing radius, added in linear light
                   (LearnOpenGL "Bloom"; Blender Glare "Fog Glow")
vector_blur        RSMB-style motion blur: average along each pixel's motion vector over the shutter
                   (McGuire et al., "A Reconstruction Filter for Plausible Motion Blur", I3D 2012, without depth)
flow_interpolate   Twixtor-style retiming: flow both ways, warp both frames to the in-between time, blend
                   (intermediate-flow approximation of Super SloMo; RIFE learns the same thing)
shake_curve        S_Shake's parameter model (Boris FX docs) as a CameraCurve the compositor replays

Parameters and their sources: video_agent/knowledge/techniques.yaml (fx.*). Frames are BGR uint8.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .. import knowledge
from ..blueprint import CameraCurve
from .curves import noise

# ---------------------------------------------------------------- colour helpers


def to_linear(bgr: np.ndarray) -> np.ndarray:
    """sRGB/BT.709 display values (uint8) to linear light (float32 0..1); blurs and adds belong in linear light."""
    v = bgr.astype(np.float32) / 255
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def to_display(lin: np.ndarray) -> np.ndarray:
    v = np.clip(lin, 0, 1)
    v = np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)
    return np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- bloom


def bloom(frame: np.ndarray, threshold: float | None = None, knee: float | None = None, radii: list[float] | None = None,
          intensity: float | None = None, tint: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> np.ndarray:
    """Glow of the highlights: soft-knee bright-pass on luminance, Gaussian blurs of each radius (share of the
    frame width) averaged, tinted (BGR) and added to the picture in linear light."""
    threshold = knowledge.param("fx.bloom", "threshold") if threshold is None else threshold
    knee = knowledge.param("fx.bloom", "knee") if knee is None else knee
    radii = knowledge.param("fx.bloom", "radii_w") if radii is None else radii
    intensity = knowledge.param("fx.bloom", "intensity") if intensity is None else intensity
    lin = to_linear(frame)
    lum = lin @ np.array([0.0722, 0.7152, 0.2126], np.float32)  # BT.709 weights in B, G, R order
    # Soft knee: a smooth start below the threshold instead of a hard edge (no ringing around highlights).
    soft = np.clip(lum - threshold + knee, 0, 2 * knee) ** 2 / (4 * knee + 1e-6)
    weight = np.maximum(soft, lum - threshold) / np.maximum(lum, 1e-6)
    bright = lin * weight[..., None]
    w = frame.shape[1]
    glow = sum(cv2.GaussianBlur(bright, (0, 0), max(0.5, r * w)) for r in radii) / len(radii)
    return to_display(lin + intensity * glow * np.array(tint, np.float32))


# ---------------------------------------------------------------- motion blur


def dense_flow(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Per-pixel motion from frame a to frame b (px), OpenCV DIS optical flow (medium preset)."""
    ga, gb = (cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f for f in (a, b))
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    return dis.calc(ga, gb, None)


def _warp(img: np.ndarray, flow: np.ndarray) -> np.ndarray:
    """Backward warp: out(x) = img(x + flow(x))."""
    h, w = flow.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(img, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def vector_blur(frame: np.ndarray, flow: np.ndarray, shutter: float | None = None, samples: int | None = None) -> np.ndarray:
    """Smear each pixel along its motion vector (px per frame) over the shutter (0.5 = 180 degrees), centred on
    the frame's own time like a real shutter. Averaged in linear light."""
    shutter = knowledge.param("fx.vector_blur", "shutter") if shutter is None else shutter
    samples = knowledge.param("fx.vector_blur", "samples") if samples is None else samples
    peak = float(np.abs(flow).max()) * shutter
    n = int(min(max(2, samples, np.ceil(peak)), 64))  # at least a sample per pixel of smear: no stepping
    lin = to_linear(frame)
    acc = np.zeros_like(lin)
    for k in range(n):
        acc += _warp(lin, flow * (shutter * (k / (n - 1) - 0.5)))
    return to_display(acc / n)


# ---------------------------------------------------------------- retiming


def flow_interpolate(a: np.ndarray, b: np.ndarray, t: float, flows: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    """The frame a share `t` of the way from a to b. Flows a->b and b->a give approximate flows from the
    in-between time to each frame; both frames are warped there and blended by closeness in time."""
    if t <= 0:
        return a.copy()
    if t >= 1:
        return b.copy()
    f01, f10 = flows or (dense_flow(a, b), dense_flow(b, a))
    ft0 = -(1 - t) * t * f01 + t * t * f10
    ft1 = (1 - t) ** 2 * f01 - t * (1 - t) * f10
    la, lb = to_linear(a), to_linear(b)
    warped = (1 - t) * _warp(la, ft0) + t * _warp(lb, ft1)
    # Where the two flows disagree (occlusions, smeared or new content) the warp invents detail:
    # fall back to a plain cross-fade there, as Twixtor's frame-blend mode does.
    err = np.linalg.norm(f01 + _warp(f10, f01), axis=2)
    trust = np.clip(1.5 - err / (1 + 0.1 * np.linalg.norm(f01, axis=2)), 0, 1)
    trust = cv2.GaussianBlur(trust, (0, 0), 3)[..., None]
    return to_display(trust * warped + (1 - trust) * ((1 - t) * la + t * lb))


def retime_frames(frames: list[np.ndarray], n_out: int) -> list[np.ndarray]:
    """n_out frames evenly spaced in time over the clip (output frame i shows source time i * len/n_out),
    the in-between ones made by flow_interpolate. The same contract as rife.interpolate."""
    n_in = len(frames)
    out, cache = [], {}
    for i in range(n_out):
        p = min(i * n_in / n_out, n_in - 1)
        j, t = int(p), p - int(p)
        if t < 1e-3 or j + 1 >= n_in:
            out.append(frames[j].copy())
            continue
        if j not in cache:
            cache.clear()
            cache[j] = (dense_flow(frames[j], frames[j + 1]), dense_flow(frames[j + 1], frames[j]))
        out.append(flow_interpolate(frames[j], frames[j + 1], t, cache[j]))
    return out


def interpolate(in_dir: Path, out_dir: Path, n_frames: int) -> None:
    """Folder of numbered PNGs -> exactly n_frames numbered PNGs (the rife.interpolate interface, on the CPU)."""
    files = sorted(Path(in_dir).glob("*.png"))
    if len(files) < 2:
        raise RuntimeError("not enough source frames for interpolation")
    frames = [cv2.imread(str(f), cv2.IMREAD_COLOR) for f in files]
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(retime_frames(frames, n_frames), 1):
        cv2.imwrite(str(out_dir / f"{i:08d}.png"), f)


# ---------------------------------------------------------------- shake


def shake_curve(n_frames: int, fps: float, amplitude_px: float, frequency: float, width: int, height: int,
                tilt_deg: float = 0.0, zoom: float = 0.0, wave: float = 0.0, decay_frames: float | None = None,
                seed: int = 0) -> CameraCurve:
    """S_Shake's model per axis: Amplitude * (random part: smooth noise at Frequency + wave part: a sine at
    Frequency), here with amplitudes in pixels at the output size (wave = share of the sine part, 0..1).
    decay_frames eases the whole shake out like the Boris FX impact recipe (amplitude keyed down to 0)."""
    t = np.arange(n_frames) / fps
    env = np.ones(n_frames) if not decay_frames else np.clip(1 - np.arange(n_frames) / decay_frames, 0, 1) ** 2

    def axis(k: int) -> np.ndarray:
        rnd = noise(t * frequency + 13.7 * k, seed + k)
        sine = np.sin(2 * np.pi * frequency * t + 1.3 * k)
        return env * ((1 - wave) * rnd + wave * sine)

    return CameraCurve(
        fps=fps,
        s=list(1 + zoom * axis(3)),
        r=list(np.radians(tilt_deg) * axis(2)),
        x=list(amplitude_px * axis(0) / width),
        y=list(amplitude_px * axis(1) / height),
    )
