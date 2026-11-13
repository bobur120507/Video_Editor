"""Match benchmark with ground truth.

Builds raw "footage" from photos (slow handheld-like drift + grain), then makes reference edits
from it with FFmpeg — cuts on the beat, zoom punches, push-ins, rotation sway, shake, speed ramps,
flashes and colour grades. Each reference is cloned and re-edited from the same raw footage; the
output is scored against the reference (style match + pixel SSIM). Since the footage is the same,
a perfect engine would score ~100%.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import clone_video
from .config import get_settings
from .director import plan_edit
from .media import ffmpeg
from .render import RenderOptions, render
from .scoring import full_report

PHOTO_DIRS = [Path("/usr/share/backgrounds"), Path("/usr/share/backgrounds/contest")]
FPS = 30
W, H = 1080, 1920
BEAT = 0.5  # 120 BPM


@dataclass
class ShotSpec:
    clip: int
    start: float  # seconds into the raw clip
    dur: float  # output seconds
    speed: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 1.0)])
    zoom: str | None = None  # zoompan z expression in `on` (frame number in the shot)
    rotate: str | None = None  # rotate angle expression in t (radians)
    shake: int = 0  # shake amplitude in px
    flash: bool = False
    grade: str = ""  # extra eq/colorbalance filters

    def need(self) -> float:
        pts = self.speed + [(1.0, 0.0)]
        return sum((pts[i + 1][0] - a) * self.dur * s for i, (a, s) in enumerate(self.speed))


WARM = "eq=contrast=1.15:saturation=1.3,colorbalance=rs=0.08:gs=0.02:bs=-0.08"
COOL = "eq=contrast=1.2:saturation=0.8:brightness=-0.03,colorbalance=rs=-0.06:bs=0.08"
PUNCH = "1.3-0.3*min(1,on/9)*(2-min(1,on/9))"

CASES: dict[str, list[ShotSpec]] = {
    "cuts_grade": [ShotSpec(0, 0.5, 1.0, grade=WARM), ShotSpec(1, 1.0, 1.0, grade=WARM), ShotSpec(2, 0.3, 1.0, grade=WARM), ShotSpec(3, 2.0, 1.5, grade=WARM)],
    "zoom_punch": [ShotSpec(i, 0.4 + i * 0.3, 1.0, zoom=PUNCH) for i in range(4)],
    "push_rotate": [
        ShotSpec(0, 0.5, 2.0, zoom="1.12+0.004*on", rotate="0.045*sin(2*PI*t*0.8)"),
        ShotSpec(2, 1.0, 2.0, zoom="1.12+0.004*on", rotate="-0.045*sin(2*PI*t*0.8)"),
    ],
    "shake": [ShotSpec(1, 0.5, 1.0, shake=22), ShotSpec(3, 0.8, 1.0, shake=22), ShotSpec(0, 1.5, 1.0, shake=22)],
    "speed_ramp": [
        ShotSpec(0, 0.3, 2.0, speed=[(0.0, 1.0), (0.3, 0.35), (0.8, 1.0)]),
        ShotSpec(2, 0.5, 2.0, speed=[(0.0, 1.0), (0.4, 0.35)]),
    ],
    "flash_cool": [ShotSpec(i, 0.5 + 0.2 * i, 1.0, flash=i > 0, zoom=PUNCH if i % 2 else None, grade=COOL) for i in range(4)],
}


def _photos() -> list[Path]:
    found = []
    for d in PHOTO_DIRS:
        if d.exists():
            found += sorted(p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.stat().st_size > 300_000)
    return found


def make_raw_clips(out_dir: Path, n: int = 4, seconds: float = 7.0) -> list[Path]:
    photos = _photos()
    if len(photos) < n:
        raise FileNotFoundError("Not enough photos in /usr/share/backgrounds to build test footage")
    clips = []
    for i in range(n):
        dst = out_dir / f"raw_{i}.mp4"
        if not dst.exists():
            photo = photos[(i * 3) % len(photos)]
            drift = (
                f"scale=-2:2300,crop={W}:{H}:x='(iw-{W})/2+70*sin(t*0.9+{i})':y='(ih-{H})/2+45*sin(t*0.6+{i * 2})',"
                "noise=alls=5:allf=t,format=yuv420p"
            )
            ffmpeg(["-loop", 1, "-i", photo, "-vf", drift, "-t", seconds, "-r", FPS, "-c:v", "libx264", "-crf", 14, dst])
        clips.append(dst)
    return clips


def _shot_filter(spec: ShotSpec) -> str:
    chains, pts, src_t = [], spec.speed + [(1.0, 0.0)], 0.0
    for i, (at, speed) in enumerate(spec.speed):
        out_len = (pts[i + 1][0] - at) * spec.dur
        f = [f"trim=start={src_t:.4f}:duration={out_len * speed:.4f}", "setpts=PTS-STARTPTS"]
        if speed < 0.95:
            f.append(f"minterpolate=fps={round(FPS / speed)}:mi_mode=mci")
        f.append(f"setpts=PTS/{speed}")
        chains.append(f)
        src_t += out_len * speed
    n = len(chains)
    graph = [f"[0:v]split={n}" + "".join(f"[s{i}]" for i in range(n))] if n > 1 else []
    graph += [f"[{'s' + str(i) if n > 1 else '0:v'}]{','.join(c)}[p{i}]" for i, c in enumerate(chains)]
    graph.append("".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,fps={FPS}[v0]")
    post = []
    if spec.zoom:
        post.append(f"zoompan=z='{spec.zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
    if spec.rotate:
        # Rotate, then zoom 10% so no black corners show — what an editor does.
        post += [f"rotate=a='{spec.rotate}':fillcolor=black", f"scale={int(W * 1.1) // 2 * 2}:-2", f"crop={W}:{H}"]
    if spec.shake:
        a, m = spec.shake, spec.shake + 4
        cw = W - 2 * m
        ch = int(cw * H / W) // 2 * 2
        post += [f"crop={cw}:{ch}:x='{m}+{a}*sin(n*1.9)':y='{(H - ch) // 2}+{a}*cos(n*2.7)'", f"scale={W}:{H}"]
    if spec.grade:
        post.append(spec.grade)
    if spec.flash:
        post.append("fade=t=in:st=0:d=0.12:color=white")
    post += ["format=yuv420p"]
    graph.append(f"[v0]{','.join(post)}[v]")
    return ";".join(graph)


def make_reference(name: str, shots: list[ShotSpec], raws: list[Path], out_dir: Path) -> Path:
    dst = out_dir / f"{name}_reference.mp4"
    if dst.exists():
        return dst
    parts = []
    for i, spec in enumerate(shots):
        part = out_dir / f"{name}_part{i}.mp4"
        n = round(spec.dur * FPS)
        ffmpeg(["-ss", spec.start, "-t", spec.need() + 0.3, "-i", raws[spec.clip], "-filter_complex", _shot_filter(spec),
                "-map", "[v]", "-frames:v", n, "-c:v", "libx264", "-preset", "ultrafast", "-qp", 0, part])
        parts.append(part)
    total = sum(s.dur for s in shots)
    lst = out_dir / f"{name}_parts.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    kick = f"aevalsrc='0.7*sin(2*PI*55*t)*exp(-22*mod(t,{BEAT}))+0.15*sin(2*PI*330*t)*exp(-40*mod(t+{BEAT / 2},{BEAT}))':s=48000:d={total}"
    ffmpeg(["-f", "concat", "-safe", 0, "-i", lst, "-f", "lavfi", "-i", kick, "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-crf", 16, "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", dst])
    for p in parts:
        p.unlink()
    return dst


def run(cases: list[str] | None = None, camera: bool = True, rife: bool = True, label: str = "") -> list[dict]:
    root = get_settings().dir("bench")
    raws = make_raw_clips(root)
    results = []
    for name in cases or list(CASES):
        t0 = time.time()
        ref = make_reference(name, CASES[name], raws, root)
        bp = clone_video(ref, name=name)
        plan = plan_edit(bp, raws)
        out = root / f"{name}_output.mp4"
        opts = RenderOptions(replay_camera=camera, interpolate_slowmo=True)
        if not rife:
            from .render import rife as rife_mod

            rife_mod.rife_binary.cache_clear()
            orig, rife_mod.PROJECT_RIFE = rife_mod.PROJECT_RIFE, Path("/nonexistent")
        try:
            render(bp, plan, out, opts)
        finally:
            if not rife:
                rife_mod.PROJECT_RIFE = orig
                rife_mod.rife_binary.cache_clear()
        report = full_report(ref, out, pixels=True)
        results.append(
            {
                "case": name,
                "overall": report.overall,
                "ssim": report.pixel_ssim,
                **report.parts,
                "content_matched": sum(1 for s in plan.shots if s.content_match > 0),
                "shots": len(plan.shots),
                "seconds": round(time.time() - t0, 1),
            }
        )
    _save(results, label or f"camera={camera},rife={rife}")
    return results


def _save(results: list[dict], label: str) -> None:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    with open(get_settings().dir("bench") / "history.jsonl", "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "commit": commit, "label": label, "results": results}) + "\n")


def table(results: list[dict]) -> str:
    cols = ["case", "overall", "ssim", "cuts", "camera", "color", "pace", "flashes", "duration", "content_matched", "seconds"]
    rows = [" | ".join(cols)] + [" | ".join(str(r.get(c, "")) for c in cols) for r in results]
    if results:
        avg = lambda k: round(sum(r[k] or 0 for r in results) / len(results), 1)  # noqa: E731
        rows.append(f"AVERAGE | {avg('overall')} | {avg('ssim')} | " + " | ".join(str(avg(c)) for c in cols[3:9]))
    return "\n".join(rows)
