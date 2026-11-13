"""Real-Reels test runner.

Folder layout (default data/real/):

    data/real/
      links.txt                 optional: one Reel/TikTok/Shorts URL per line (downloaded with yt-dlp)
      _footage/                 optional: your own raw clips, used to test each style on *different* footage
      car_phonk_1/
        reference.mp4           the finished edit (any single video file in the folder works)
        raw/                    optional: the raw clips that edit was made from -> exact pixel test
        <any other folder>/     optional: footage to edit in this Reel's style (e.g. source_video_for_edit/)

For every case: clone the reference, re-edit it (from raw/ if present, and from _footage/ if present),
score it, and save a side-by-side contact sheet so you can judge it by eye.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import cv2
import numpy as np

from .analysis import clone_video
from .config import get_settings
from .director import plan_edit
from .downloader import download
from .media import frame_at, probe
from .render import RenderOptions, render
from .scoring import full_report

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _videos(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT) if d.exists() else []


def fetch_links(root: Path) -> list[str]:
    """Download every URL in links.txt into its own case folder (skips ones already there)."""
    links = root / "links.txt"
    if not links.exists():
        return []
    errors = []
    for line in links.read_text().splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        name = re.sub(r"[^\w]+", "_", url.split("?")[0].rstrip("/").split("/")[-1])[:40] or "link"
        case = root / f"link_{name}"
        if _videos(case):
            continue
        try:
            download(url, case)
        except Exception as e:  # keep going; report at the end
            errors.append(f"{url}: {e}")
    return errors


def find_cases(root: Path) -> list[Path]:
    return [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_") and _videos(d)]


def contact_sheet(reference: Path, outputs: dict[str, Path], dst: Path, n: int = 6) -> Path:
    """Rows of frames at the same moments: reference, then each output."""
    duration = probe(reference).duration
    times = [duration * (i + 0.4) / n for i in range(n)]  # off the exact cut/flash moments
    rows = []
    for label, video in {"reference": reference, **outputs}.items():
        tiles = []
        for t in times:
            f = frame_at(video, t, width=180)
            tiles.append(f if f is not None else np.zeros((320, 180, 3), np.uint8))
        h = min(t.shape[0] for t in tiles)
        row = cv2.hconcat([t[:h] for t in tiles])
        cv2.putText(row, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(row, label, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        rows.append(row)
    w = min(r.shape[1] for r in rows)
    cv2.imwrite(str(dst), cv2.vconcat([r[:, :w] for r in rows]))
    return dst


def _case_footage(case: Path) -> list[Path]:
    """Footage given for this Reel specifically: any subfolder with videos other than raw/ and _results/."""
    for sub in sorted(case.iterdir()):
        if sub.is_dir() and sub.name not in ("raw", "_results") and _videos(sub):
            return _videos(sub)
    return []


def run(root: Path | None = None, cross: bool = False) -> tuple[list[dict], list[str]]:
    root = Path(root or get_settings().dir("real"))
    errors = fetch_links(root)
    footage = _videos(root / "_footage")
    cases = find_cases(root)
    results = []
    for case in cases:
        reference = _videos(case)[0]
        # No footage folder: cross-apply, i.e. edit this style onto the other Reels' footage.
        # Different-footage test: this Reel's own footage folder, else _footage/, else (only with
        # cross=True) the other Reels — which is only meaningful when they are the same kind of edit.
        own = _case_footage(case)
        others = [_videos(c)[0] for c in cases if c != case] if cross else []
        test_footage, footage_label = (
            (own, "your footage") if own else (footage, "your footage") if footage else (others, "other reels' footage")
        )
        out_dir = case / "_results"
        out_dir.mkdir(exist_ok=True)
        t0 = time.time()
        try:
            bp = clone_video(reference, name=case.name)
            (out_dir / "blueprint.json").write_text(bp.model_dump_json(indent=2))
            row = {"case": case.name, "shots": len(bp.segments), "duration": round(bp.duration, 1), "summary": bp.summary()}
            outputs = {}
            raw = _videos(case / "raw")
            if raw:
                plan = plan_edit(bp, raw)
                outputs["from its own raw"] = render(bp, plan, out_dir / "from_raw.mp4", RenderOptions()).master
                rep = full_report(bp.source.path or reference, outputs["from its own raw"], pixels=True)
                row.update(raw_match=rep.overall, raw_ssim=rep.pixel_ssim, raw_parts=rep.parts,
                           raw_footage_found=f"{sum(1 for s in plan.shots if s.content_match > 0)}/{len(plan.shots)}")
            else:
                # Self-rebuild: the Reel as its own footage — the ceiling on real, compressed video.
                plan = plan_edit(bp, [reference])
                outputs["self-rebuild"] = render(bp, plan, out_dir / "self_rebuild.mp4", RenderOptions()).master
                rep = full_report(bp.source.path or reference, outputs["self-rebuild"], pixels=True)
                row.update(self_match=rep.overall, self_ssim=rep.pixel_ssim, self_parts=rep.parts)
            if test_footage:
                plan = plan_edit(bp, test_footage)
                outputs[footage_label] = render(bp, plan, out_dir / "other_footage.mp4", RenderOptions()).master
                rep = full_report(bp.source.path or reference, outputs[footage_label])
                row.update(style_match=rep.overall, style_parts=rep.parts, footage=footage_label)
            if outputs:
                row["sheet"] = str(contact_sheet(Path(bp.source.path or reference), outputs, out_dir / "compare.png"))
            row["seconds"] = round(time.time() - t0, 1)
        except Exception as e:
            row = {"case": case.name, "error": str(e)[:300]}
        results.append(row)
    _write_report(root, results, errors)
    return results, errors


def _write_report(root: Path, results: list[dict], errors: list[str]) -> None:
    with open(root / "history.jsonl", "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "results": results, "errors": errors}) + "\n")
    lines = ["# Real Reels test", "", f"Run: {time.strftime('%Y-%m-%d %H:%M')}", "",
             "| Case | Shots | Same-footage match | Pixel SSIM | Self-rebuild match | Self SSIM | Different-footage style match | Time |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['case']} | error: {r['error']} | | | | | | |")
            continue
        fmt = lambda k: f"{r[k]:.1f}%" if r.get(k) is not None else "–"  # noqa: E731
        lines.append(
            f"| {r['case']} | {r['shots']} | {fmt('raw_match')} | {fmt('raw_ssim')} | {fmt('self_match')} | {fmt('self_ssim')}"
            f" | {fmt('style_match')} | {r['seconds']}s |"
        )
    for r in results:
        if "error" in r:
            continue
        lines += ["", f"## {r['case']}", "", "```", r["summary"], "```"]
        for key in ("raw_parts", "self_parts", "style_parts"):
            if r.get(key):
                label = {"raw_parts": "Same footage", "self_parts": "Self-rebuild", "style_parts": r.get("footage", "Other footage")}[key]
                lines.append(f"{label}: " + " · ".join(f"{k} {v:.0f}%" for k, v in r[key].items()))
        if r.get("sheet"):
            lines += ["", f"![{r['case']}]({Path(r['sheet']).relative_to(root)})"]
    if errors:
        lines += ["", "## Download errors", *[f"- {e}" for e in errors]]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
