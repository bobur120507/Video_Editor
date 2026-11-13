"""Download reference videos from pasted links (Instagram, TikTok, YouTube Shorts...) via yt-dlp.

Note: downloading from Instagram is against its Terms of Use and may get the account used for
cookies flagged. Use for your own content or with permission.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import get_settings

URL_RE = re.compile(r"https?://\S+")
IG_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:reel|reels|p|tv)/([\w-]+)")
MAX_BYTES = 300 * 1024 * 1024


def find_url(text: str | None) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def instagram_shortcode(url: str) -> str | None:
    m = IG_SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def download(url: str, dest_dir: str | Path) -> Path:
    from yt_dlp import YoutubeDL

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "format": "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_BYTES,
    }
    cookies = get_settings().ytdlp_cookies_file
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():
        candidates = sorted(dest_dir.glob(f"{info['id']}.*"))
        if not candidates:
            raise FileNotFoundError(f"yt-dlp finished but no file found for {url}")
        path = candidates[0]
    return path
