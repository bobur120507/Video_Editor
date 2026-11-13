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
    )


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
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
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


def color_stats(frames: list[np.ndarray]) -> dict:
    """Mean colour statistics over BGR frames, all in 0..1."""
    if not frames:
        return {"luma_mean": 0.45, "luma_std": 0.22, "saturation": 0.35, "rgb_mean": (0.45, 0.45, 0.45)}
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
    }
