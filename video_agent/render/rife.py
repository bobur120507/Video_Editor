"""RIFE AI frame interpolation (rife-ncnn-vulkan) for smooth slow motion. Runs on the GPU via Vulkan."""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

PROJECT_RIFE = Path(__file__).resolve().parents[2] / "tools" / "rife" / "rife-ncnn-vulkan"
MODEL = "rife-v4.6"


@lru_cache
def rife_binary() -> Path | None:
    configured = get_settings().rife_bin
    for candidate in (configured, shutil.which("rife-ncnn-vulkan"), str(PROJECT_RIFE)):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def interpolate(in_dir: Path, out_dir: Path, n_frames: int) -> None:
    """Turn the numbered PNGs in `in_dir` into exactly `n_frames` evenly spaced PNGs in `out_dir`."""
    binary = rife_binary()
    if binary is None:
        raise FileNotFoundError("rife-ncnn-vulkan not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(binary), "-i", str(in_dir), "-o", str(out_dir), "-n", str(n_frames), "-m", str(binary.parent / MODEL), "-f", "%08d.png"],
        capture_output=True,
        text=True,
    )
    produced = len(list(out_dir.glob("*.png")))
    if proc.returncode != 0 or produced == 0:
        raise RuntimeError(f"RIFE failed ({proc.returncode}): {proc.stderr[-500:]}")
