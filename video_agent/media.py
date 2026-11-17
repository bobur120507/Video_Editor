"""Thin wrappers around ffprobe/ffmpeg and frame sampling."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    pass


@dataclass
class Probe:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0
    color_space: str = ""  # matrix tag, e.g. "bt709"; empty when untagged
    color_range: str = ""  # "tv" (limited), "pc" (full, some Android phones) or empty
    variable_fps: bool = False  # phone/Instagram downloads often are; frame-exact timing needs constant fps

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height


def run(cmd: list[str], timeout: float | None = None) -> str:
    log.debug("run: %s", " ".join(map(str, cmd)))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc.stdout


def ffmpeg(args: list[str], timeout: float | None = None) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], timeout=timeout)


def probe(path: str | Path) -> Probe:
    path = Path(path)
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"No video stream in {path.name}")
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    rnum, _, rden = (video.get("r_frame_rate") or "0/1").partition("/")
    r_fps = float(rnum) / float(rden or 1) if float(rden or 1) else fps
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 360
    if not rotation and "rotate" in (video.get("tags") or {}):
        rotation = abs(int(video["tags"]["rotate"])) % 360
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return Probe(
        path=path,
        duration=duration,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps if 1 <= fps <= 240 else 30.0,
        has_audio=any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
        rotation=rotation,
        color_space="" if video.get("color_space") in (None, "unknown") else video["color_space"],
        color_range="" if video.get("color_range") in (None, "unknown") else video["color_range"],
        variable_fps=abs(r_fps - fps) > 0.5,
    )


STANDARD_FPS = (23.976, 24, 25, 29.97, 30, 50, 59.94, 60)


def constant_fps_copy(path: str | Path, dest_dir: str | Path) -> Path:
    """Return `path`, or a constant-frame-rate copy of it when it is variable or off-standard."""
    info = probe(path)
    nearest = min(STANDARD_FPS, key=lambda f: abs(f - info.fps))
    if not info.variable_fps and abs(nearest - info.fps) < 0.05:
        return Path(path)
    dst = Path(dest_dir) / f"{Path(path).stem}_cfr{round(nearest)}.mp4"
    if not dst.exists():
        ffmpeg(["-i", path, "-fps_mode", "cfr", "-r", nearest, "-c:v", "libx264", "-crf", 12, "-preset", "fast",
                "-pix_fmt", "yuv420p", "-c:a", "copy", dst])
    return dst


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 22050, mono: bool = True) -> Path:
    dst = Path(dst)
    args = ["-i", src, "-vn", "-ar", sample_rate]
    if mono:
        args += ["-ac", 1]
    ffmpeg([*args, dst])
    return dst


def iter_frames(path: str | Path, sample_fps: float = 10.0, width: int = 320, start: float = 0.0, end: float | None = None):
    """Yield (time, BGR frame) downscaled to `width`, sampled at roughly `sample_fps`."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise MediaError(f"OpenCV cannot open {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(src_fps / sample_fps))
    if start > 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    idx = int(round(start * src_fps))
    first = True
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if first and start > 0:
                # Trust the decoder's real position after a seek, not the requested one.
                idx = max(0, int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
            first = False
            t = idx / src_fps
            if end is not None and t > end:
                break
            if idx % step == 0:
                h, w = frame.shape[:2]
                scale = width / w
                yield t, cv2.resize(frame, (width, max(2, int(h * scale))), interpolation=cv2.INTER_AREA)
            idx += 1
    finally:
        cap.release()


def frame_at(path: str | Path, t: float, width: int | None = None) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        return None
    if width:
        h, w = frame.shape[:2]
        frame = cv2.resize(frame, (width, int(h * width / w) // 2 * 2), interpolation=cv2.INTER_AREA)
    return frame


QUANTILES = (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)


def color_stats(frames: list[np.ndarray]) -> dict:
    """Mean colour statistics over BGR frames, all in 0..1; Lab (L 0..100) means, spreads and the
    distributions of lightness and of colourfulness (distance from the mean colour), for grading; and
    the neutral point (the tint of the greys and whites) with colourfulness measured from it."""
    if not frames:
        return {"luma_mean": 0.45, "luma_std": 0.22, "saturation": 0.35, "rgb_mean": (0.45, 0.45, 0.45)}
    lab = np.concatenate([cv2.cvtColor(f.astype(np.float32) / 255, cv2.COLOR_BGR2LAB).reshape(-1, 3) for f in frames])
    lum, lstd, sat, rgb = [], [], [], []
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        lum.append(gray.mean())
        lstd.append(gray.std())
        sat.append(hsv[..., 1].mean() / 255)
        b, g, r = (f.reshape(-1, 3).mean(axis=0) / 255).tolist()
        rgb.append((r, g, b))
    return {
        "luma_mean": float(np.mean(lum)),
        "luma_std": float(np.mean(lstd)),
        "saturation": float(np.mean(sat)),
        "rgb_mean": tuple(float(x) for x in np.mean(rgb, axis=0)),
        "lab_mean": tuple(round(float(x), 4) for x in lab.mean(axis=0)),
        "lab_std": tuple(round(float(x), 4) for x in lab.std(axis=0)),
        "l_quantiles": tuple(round(float(x), 3) for x in np.quantile(lab[:, 0], QUANTILES)),
        "chroma_quantiles": tuple(round(float(x), 3) for x in np.quantile(
            np.hypot(lab[:, 1] - lab[:, 1].mean(), lab[:, 2] - lab[:, 2].mean()), QUANTILES)),
        **_neutral(lab),
    }


def _neutral(lab: np.ndarray) -> dict:
    """The tint of the greys and whites (least colourful quarter of the mid-tones) and colourfulness
    measured from it. A video's average colour is mostly its content (grass, a blue car); its greys
    show the grade."""
    mid = (lab[:, 0] > 15) & (lab[:, 0] < 92)
    if mid.sum() < 100:
        return {}
    c = np.hypot(lab[:, 1], lab[:, 2])
    grey = mid & (c <= np.quantile(c[mid], 0.25))
    n = lab[grey, 1:].mean(axis=0)
    return {
        "neutral_ab": tuple(round(float(x), 4) for x in n),
        "neutral_chroma_quantiles": tuple(round(float(x), 3) for x in np.quantile(
            np.hypot(lab[:, 1] - n[0], lab[:, 2] - n[1]), QUANTILES)),
    }


def thumb(frame: np.ndarray, aspect: float = 9 / 16, size: int = 16, zoom: float = 1.0) -> np.ndarray:
    """Tiny zero-mean, unit-norm grey thumbnail of the centre `aspect` crop, for content matching.

    The dot product of two thumbs is their correlation (1 = same picture), which ignores
    brightness/contrast grading.
    """
    h, w = frame.shape[:2]
    if zoom > 1.0:
        zh, zw = int(h / zoom), int(w / zoom)
        frame = frame[(h - zh) // 2 : (h - zh) // 2 + zh, (w - zw) // 2 : (w - zw) // 2 + zw]
        h, w = frame.shape[:2]
    if w / h > aspect:
        cw = int(h * aspect)
        frame = frame[:, (w - cw) // 2 : (w - cw) // 2 + cw]
    else:
        ch = int(w / aspect)
        frame = frame[(h - ch) // 2 : (h - ch) // 2 + ch]
    h, w = frame.shape[:2]
    frame = frame[int(h * 0.15) : int(h * 0.85), int(w * 0.15) : int(w * 0.85)]  # skip edges: corners, borders
    g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    n = float(np.linalg.norm(g))
    return (g / n).reshape(-1) if n > 1e-6 else g.reshape(-1)


def orb_init(src_gray: np.ndarray, ref_gray: np.ndarray) -> np.ndarray:
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
