"""Keyframe compositor: replays a reference shot's per-frame camera transform on new footage.

Works like an After Effects transform layer: scale/rotation/position keyframes on every frame,
an automatic cover zoom so no borders show, and optional motion blur (several sub-frame
transforms averaged, like AE's 180-degree shutter).
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from ..blueprint import CameraCurve

log = logging.getLogger(__name__)

MAX_COVER_ZOOM = 1.8
MAX_BLUR_SAMPLES = 24


def is_static(curve: CameraCurve | None) -> bool:
    if curve is None or len(curve) < 2:
        return True
    d = curve.describe()
    return d["zoom"] < 1.004 and d["rotation_deg"] < 0.15 and d["pan"] < 0.003 and d["shake"] < 0.0005


def _to_matrix(s: float, r: float, x: float, y: float, w: int, h: int) -> np.ndarray:
    m = np.eye(3)
    m[:2] = _matrix(s, r, x, y, 1.0, w, h)
    return m


def relative_curve(target: CameraCurve, footage: CameraCurve | None, n_frames: int, fps: float, w: int, h: int) -> CameraCurve:
    """Transform to apply so the footage's perceived motion equals `target`: target ∘ footage⁻¹.

    Without this, footage that already moves (handheld, drift) would get the reference's camera
    motion on top of its own.
    """
    out = CameraCurve(fps=fps)
    for i in range(n_frames):
        m = _to_matrix(*target.at(i / fps), w, h)
        if footage is not None and len(footage) > 1:
            m = m @ np.linalg.inv(_to_matrix(*footage.at(i / fps), w, h))
        s = math.hypot(m[0, 0], m[1, 0])
        r = math.atan2(m[1, 0], m[0, 0])
        c = np.array([w / 2, h / 2])
        t = m[:2, :2] @ c + m[:2, 2] - c
        out.s.append(s)
        out.r.append(r)
        out.x.append(float(t[0] / w))
        out.y.append(float(t[1] / h))
    return out


def _params(curve: CameraCurve, n_frames: int, fps: float) -> np.ndarray:
    """(n_frames, 4) array of scale, rotation, x, y for the output frames, centred on the typical framing."""
    p = np.array([curve.at(i / fps) for i in range(n_frames)], dtype=np.float64)
    # Centre on the shot's typical framing (median), not the middle of its range: one wild
    # blurred frame must not tilt or zoom the whole shot. The cover zoom hides any border.
    p[:, 0] /= np.median(p[:, 0])
    for col in (1, 2, 3):
        p[:, col] -= np.median(p[:, col])
    return p


def _cover_zoom(p: np.ndarray, w: int, h: int) -> float:
    """Smallest extra zoom so every output corner maps inside the source frame on every frame."""
    corners = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [-w / 2, h / 2], [w / 2, h / 2]])
    k = 1.0
    for s, r, x, y in p:
        inv = np.array([[math.cos(r), math.sin(r)], [-math.sin(r), math.cos(r)]]) / s
        v = (corners - [x * w, y * h]) @ inv.T
        k = max(k, float(np.max(np.abs(v[:, 0]) * 2 / w)), float(np.max(np.abs(v[:, 1]) * 2 / h)))
    return min(k, MAX_COVER_ZOOM)


def _matrix(s: float, r: float, x: float, y: float, k: float, w: int, h: int) -> np.ndarray:
    a = k * s * np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
    c = np.array([w / 2, h / 2])
    b = c + [x * w, y * h] - a @ c
    return np.hstack([a, b.reshape(2, 1)])


def _corner_shift(m1: np.ndarray, m2: np.ndarray, w: int, h: int) -> float:
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], dtype=np.float64)
    return float(np.linalg.norm(corners @ m1.T - corners @ m2.T, axis=1).max())


def _warp_planes(planes: list[np.ndarray], m: np.ndarray, w: int, h: int) -> list[np.ndarray]:
    """Warp Y at full size and U/V at half size with the same transform (no YUV->RGB round trip)."""
    mc = m.copy()
    mc[:, 2] /= 2
    flags, border = cv2.INTER_LINEAR, cv2.BORDER_REFLECT
    return [
        cv2.warpAffine(planes[0], m, (w, h), flags=flags, borderMode=border),
        cv2.warpAffine(planes[1], mc, (w // 2, h // 2), flags=flags, borderMode=border),
        cv2.warpAffine(planes[2], mc, (w // 2, h // 2), flags=flags, borderMode=border),
    ]


def apply_camera(src: Path, dst: Path, curve: CameraCurve, width: int, height: int, fps: int, n_frames: int, shutter: float,
                 encode_args: list, absolute: bool = False, mirror_ok: np.ndarray | None = None, centre: bool = True) -> None:
    """absolute=True: the curve is an exact per-frame registration, applied as is (no re-centring or cover zoom).

    mirror_ok marks frames allowed to show mirrored edges (whip frames, hidden by motion blur, like
    After Effects' Motion Tile); they are left out of the cover-zoom calculation.
    centre=False keeps a synthesized curve's framing as designed.
    """
    if absolute:
        p = np.array([curve.at(i / fps) for i in range(n_frames)], dtype=np.float64)
        k = 1.0
    else:
        p = _params(curve, n_frames, fps) if centre else np.array([curve.at(i / fps) for i in range(n_frames)], dtype=np.float64)
        keep = ~mirror_ok[:n_frames] if mirror_ok is not None and (~mirror_ok[:n_frames]).any() else np.ones(n_frames, bool)
        k = _cover_zoom(p[keep], width, height)
    idx = np.arange(n_frames, dtype=np.float64)

    def at(f: float) -> np.ndarray:
        return np.array([np.interp(f, idx, p[:, c]) for c in range(4)])

    raw = ["-f", "rawvideo", "-pix_fmt", "yuv420p"]
    tags = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    reader = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", str(src), *raw, *tags, "-"], stdout=subprocess.PIPE)
    writer = subprocess.Popen(
        # Raw frames carry no colour tags: declare them, or FFmpeg converts them on the way out.
        ["ffmpeg", "-loglevel", "error", "-y", *raw, "-s", f"{width}x{height}", "-r", str(fps), *tags,
         "-i", "-", *map(str, encode_args), str(dst)],
        stdin=subprocess.PIPE,
    )
    ys, cs = width * height, (width // 2) * (height // 2)
    planes = None
    try:
        for i in range(n_frames):
            buf = reader.stdout.read(ys + 2 * cs)
            if len(buf) == ys + 2 * cs:
                a = np.frombuffer(buf, np.uint8)
                planes = [
                    a[:ys].reshape(height, width),
                    a[ys : ys + cs].reshape(height // 2, width // 2),
                    a[ys + cs :].reshape(height // 2, width // 2),
                ]
            if planes is None:
                raise RuntimeError(f"No frames could be read from {src}")
            m = _matrix(*at(i), k, width, height)
            samples = 1
            if shutter > 0:
                m_a, m_b = _matrix(*at(i - shutter / 2), k, width, height), _matrix(*at(i + shutter / 2), k, width, height)
                samples = int(min(MAX_BLUR_SAMPLES, max(1, math.ceil(_corner_shift(m_a, m_b, width, height) / 2))))
            if samples == 1:
                out = _warp_planes(planes, m, width, height)
            else:
                acc = [np.zeros(pl.shape, np.float32) for pl in planes]
                for j in range(samples):
                    f = i + (j / (samples - 1) - 0.5) * shutter
                    for a_, w_ in zip(acc, _warp_planes(planes, _matrix(*at(f), k, width, height), width, height)):
                        a_ += w_
                out = [np.clip(a_ / samples + 0.5, 0, 255).astype(np.uint8) for a_ in acc]
            for pl in out:
                writer.stdin.write(pl.tobytes())
    finally:
        writer.stdin.close()
        reader.stdout.close()
        reader.wait()
        if writer.wait() != 0:
            raise RuntimeError("ffmpeg failed while writing the composited shot")
