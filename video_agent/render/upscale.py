"""Real-ESRGAN AI upscaling (realesrgan-ncnn-vulkan) for footage smaller than the output frame.

A plain resize only spreads the pixels it has; Real-ESRGAN redraws edges and texture, so 576-720 p
material (downloaded Reels, phone screen recordings) holds up at 1080 p. Runs on the GPU via Vulkan.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

PROJECT_ESRGAN = Path(__file__).resolve().parents[2] / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan"
# Video-trained model: fast enough for every frame of a Reel and temporally stable (no shimmer).
MODEL = "realesr-animevideov3"
NEEDED = 1.1  # upscale only when the footage would be enlarged more than this
# The video model cleans compression blocks but smooths skin and fine texture ("waxy"); a little of
# the plain resize mixed back in keeps natural texture.
KEEP_TEXTURE = 0.25


@lru_cache
def esrgan_binary() -> Path | None:
    for candidate in (shutil.which("realesrgan-ncnn-vulkan"), str(PROJECT_ESRGAN)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def upscale_frames(in_dir: Path, out_dir: Path, factor: float, keep_texture: float = KEEP_TEXTURE) -> None:
    """Upscale every PNG in `in_dir` into `out_dir` (same names) by the model scale covering `factor`."""
    binary = esrgan_binary()
    if binary is None:
        raise FileNotFoundError("realesrgan-ncnn-vulkan not found")
    scale = 2 if factor <= 2 else 3 if factor <= 3 else 4
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(binary), "-i", str(in_dir), "-o", str(out_dir), "-n", f"{MODEL}-x{scale}", "-s", str(scale),
         "-m", str(binary.parent / "models"), "-f", "png"],
        capture_output=True,
        text=True,
    )
    produced = len(list(out_dir.glob("*.png")))
    if proc.returncode != 0 or produced < len(list(in_dir.glob("*.png"))):
        raise RuntimeError(f"Real-ESRGAN failed ({proc.returncode}): {proc.stderr[-500:]}")
    if keep_texture > 0:
        for up_path in out_dir.glob("*.png"):
            up = cv2.imread(str(up_path), cv2.IMREAD_UNCHANGED)
            src = cv2.imread(str(in_dir / up_path.name), cv2.IMREAD_UNCHANGED)
            if up is None or src is None or up.shape[2:] != src.shape[2:]:
                continue
            plain = cv2.resize(src, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_LANCZOS4)
            cv2.imwrite(str(up_path), cv2.addWeighted(up, 1 - keep_texture, plain, keep_texture, 0))
