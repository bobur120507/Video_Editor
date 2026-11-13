"""AI Director: audits footage, assigns clip windows to blueprint shots, and finds the subject to crop around."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .blueprint import Blueprint, Segment
from . import vision
from .analysis.visual import detect_cuts
from .media import Probe, color_stats, frame_at, iter_frames, probe, thumb

log = logging.getLogger(__name__)

_FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


CONTENT_MATCH = 0.8  # thumbnail correlation above which a clip is treated as the reference's own footage
THUMB_ZOOMS = (1.0, 1.2, 1.45, 1.75)  # reference shots are often zoomed in (punches, push-ins)


@dataclass
class ClipInfo:
    probe: Probe
    times: np.ndarray
    motion: np.ndarray  # normalized 0..1 motion per sampled frame
    thumbs: np.ndarray  # (N, zooms, 256) content thumbnails at `times`, one per THUMB_ZOOMS
    cut_after: np.ndarray  # (N,) True where a new shot starts in this footage
    sem_times: np.ndarray = field(default_factory=lambda: np.zeros(0))  # CLIP samples (3 per second)
    sem: np.ndarray = field(default_factory=lambda: np.zeros((0, 512)))

    def semantic_at(self, t: float) -> np.ndarray | None:
        if not len(self.sem_times):
            return None
        return self.sem[int(np.argmin(np.abs(self.sem_times - t)))]

    @property
    def path(self) -> Path:
        return self.probe.path

    @property
    def duration(self) -> float:
        return self.probe.duration

    def motion_between(self, start: float, end: float) -> float:
        m = (self.times >= start) & (self.times < end)
        return float(self.motion[m].mean()) if m.any() else 0.0


@dataclass
class Shot:
    segment: Segment
    clip: ClipInfo
    src_start: float
    src_len: float  # seconds of source used
    stretch: float = 1.0  # >1 when the clip was too short and must be slowed further
    center: tuple[float, float] = (0.5, 0.5)  # subject position in the frame, 0..1
    stats: dict = field(default_factory=dict)  # colour statistics of the chosen window
    content_match: float = 0.0  # correlation with the reference shot (1 = same footage)


@dataclass
class EditPlan:
    shots: list[Shot]
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # shot list for footage the user should film


def audit_clip(path: str | Path) -> ClipInfo:
    info = probe(path)
    times, energy, thumbs = [], [], []
    prev = None
    for t, frame in iter_frames(path, sample_fps=info.fps, width=240):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        energy.append(0.0 if prev is None else float(cv2.absdiff(prev, gray).mean()))
        times.append(t)
        thumbs.append([thumb(frame, zoom=z) for z in THUMB_ZOOMS])
        prev = gray
    e = np.array(energy)
    norm = e / (np.percentile(e, 90) + 1e-6) if e.size else e
    th = np.array(thumbs).reshape(-1, len(THUMB_ZOOMS), 256)
    # Cuts inside the footage itself (when it is an already-edited video).
    cuts = np.zeros(len(th), bool)
    if len(th):
        cuts[0] = True
        for c in detect_cuts(path, info.fps):
            cuts[min(len(th) - 1, int(round(c * info.fps)))] = True
    clip = ClipInfo(probe=info, times=np.array(times), motion=np.clip(norm, 0, 1), thumbs=th, cut_after=cuts)
    if vision.available():
        samples = list(iter_frames(path, sample_fps=3, width=320))
        clip.sem_times = np.array([t for t, _ in samples])
        clip.sem = vision.embed_images([f for _, f in samples])
    return clip


def plan_edit(blueprint: Blueprint, clip_paths: list[str | Path]) -> EditPlan:
    clips = [audit_clip(p) for p in clip_paths]
    clips = [c for c in clips if c.duration >= 0.3]
    if not clips:
        raise ValueError("No usable video clips were provided.")

    warnings: list[str] = []
    for c in clips:
        w, h = c.probe.display_size
        if min(w, h) < 720:
            warnings.append(f"{c.path.name}: low resolution {w}x{h}; output will be upscaled.")
        if c.probe.fps < 24:
            warnings.append(f"{c.path.name}: low frame rate ({c.probe.fps:.0f} fps).")

    needed = sum(s.source_seconds_needed() for s in blueprint.segments)
    have = sum(c.duration for c in clips)
    if have < needed:
        warnings.append(f"Footage is {have:.1f}s but the template needs {needed:.1f}s; some footage will be reused.")

    shots: list[Shot] = []
    used: dict[tuple[int, int], int] = {}  # (clip, source shot) -> times used
    used_ranges: list[tuple[int, float, float]] = []
    prev_key = None
    for seg in blueprint.segments:
        need = seg.source_seconds_needed()
        found = _content_window(seg, clips, need, blueprint.source.path)
        if found:
            clip, start, corr = found
            length = min(need, clip.duration - start)
            shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length, content_match=corr))
            continue
        best = None
        for ci, c in enumerate(clips):
            for start, end, key_shot, score in _rank_windows(seg, c, need):
                key = (ci, key_shot)
                score -= 0.6 if key == prev_key else 0.0  # never the same camera angle twice in a row
                score -= 0.25 * used.get(key, 0)  # spread the edit over every angle
                score -= 0.3 if any(u == ci and start < b and a < end for u, a, b in used_ranges) else 0.0
                if best is None or score > best[0]:
                    best = (score, c, start, end, key)
        _, clip, start, end, key = best
        used[key] = used.get(key, 0) + 1
        used_ranges.append((key[0], start, end))
        prev_key = key
        length = end - start
        shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length))

    for shot in shots:
        shot.center, shot.stats = _subject_and_color(shot)

    missing = _missing_shots(blueprint, have, needed)
    return EditPlan(shots=shots, warnings=warnings, missing=missing)


def _content_window(seg: Segment, clips: list[ClipInfo], need: float, reference: str = "") -> tuple[ClipInfo, float, float] | None:
    """If a clip contains the reference shot's own picture, return (clip, source start, correlation).

    The whole shot is aligned as a sequence (several frames at their relative times), so still or
    repetitive shots land on the right moment; starting exactly on a cut in the footage is preferred.
    """
    if seg.thumbs:
        refs, rel = np.array(seg.thumbs, dtype=np.float32), [seg.source_offset(t) for t in seg.thumbs_t]
    elif seg.thumb:
        refs, rel = np.array([seg.thumb], dtype=np.float32), [seg.source_offset(seg.thumb_t)]
    else:
        return None
    best = None
    for c in clips:
        n = len(c.thumbs)
        if n == 0:
            continue
        fps = c.probe.fps
        offs = np.round(np.array(rel) * fps).astype(int)
        span = int(offs.max()) + 1
        if n <= span:
            continue
        corr = np.einsum("nzd,kd->knz", c.thumbs, refs)  # (k, N, zooms)
        starts = np.arange(n - span + 1)
        per_zoom = np.mean([corr[i, starts + o] for i, o in enumerate(offs)], axis=0)  # (starts, zooms)
        score = per_zoom.max(axis=1) + 0.03 * c.cut_after[starts]
        j = int(np.argmax(score))
        if best is None or score[j] > best[2]:
            best = (c, float(c.times[j]), float(score[j]), THUMB_ZOOMS[int(np.argmax(per_zoom[j]))])
    if best is None or best[2] < CONTENT_MATCH:
        return None
    clip, start, corr, zoom = best
    start = min(max(0.0, start), max(0.0, clip.duration - need))
    # Similar framing (car centred, sky above) also correlates highly; only call it the same footage
    # when the actual picture details line up geometrically.
    k = len(seg.thumbs_t) // 2 if seg.thumbs_t else 0
    t_rel = seg.thumbs_t[k] if seg.thumbs_t else seg.thumb_t
    if not _same_picture(reference, seg.start + t_rel, clip.path, start + seg.source_offset(t_rel), strict=not reference, zoom=zoom):
        return None
    return clip, start, min(1.0, corr)


def _same_picture(reference: str, ref_t: float, clip: Path, clip_t: float, strict: bool, zoom: float = 1.0) -> bool:
    if strict or not reference or not Path(reference).exists():
        return False  # without the reference file we cannot verify: treat as different footage
    a, b = frame_at(reference, ref_t, width=480), frame_at(clip, clip_t, width=480)
    if a is None or b is None:
        return False
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(2000)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 50 or len(kb) < 50:
        # Flat pictures (graphics, sky, smooth walls) have too few feature points; compare the
        # whole image instead. Same footage aligns at >= 0.95, different scenes stay below.
        try:
            h, w = ga.shape
            # Start from the zoom the thumbnails matched at (reference frame = footage zoomed in).
            init = np.array([[1 / zoom, 0, w / 2 * (1 - 1 / zoom)], [0, 1 / zoom, h / 2 * (1 - 1 / zoom)]], np.float32)
            cc, _ = cv2.findTransformECC(ga.astype(np.float32), gb.astype(np.float32), init, cv2.MOTION_AFFINE,
                                         (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
        except cv2.error:
            return False
        return cc >= 0.95
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m for m, *rest in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * rest[0].distance]
    if len(good) < 25:
        return False
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    _, inliers = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    return inliers is not None and int(inliers.sum()) >= 25 and inliers.mean() > 0.35


def _rank_windows(seg: Segment, clip: ClipInfo, need: float) -> list[tuple[float, float, int, float]]:
    """Candidate source windows as (start, end, source-shot index, score).

    Score = framing similarity to the reference shot (wide vs close-up, where the subject sits)
    minus motion mismatch. Windows that would cross a cut in the footage are shortened to the
    shot and penalised for it.
    """
    n, fps = len(clip.thumbs), clip.probe.fps
    if n == 0:
        return [(0.0, min(need, clip.duration), 0, -1.0)]
    shot_id = np.cumsum(clip.cut_after) - 1
    shot_end = np.empty(n)
    for sid in np.unique(shot_id):
        idx = np.where(shot_id == sid)[0]
        shot_end[idx] = clip.times[idx[-1]] + 1 / fps
    span = max(1, int(round(need * fps)))
    starts = np.arange(0, max(1, n - min(span, n - 1)), max(1, int(fps // 10)))
    if seg.thumbs:
        refs = np.array(seg.thumbs, dtype=np.float32)
        offs = np.minimum(np.round(np.array([seg.source_offset(t) for t in seg.thumbs_t]) * fps).astype(int), span - 1)
        corr = np.einsum("nzd,kd->knz", clip.thumbs, refs)
        framing = np.mean([corr[i, np.minimum(starts + o, n - 1)] for i, o in enumerate(offs)], axis=0).max(axis=1)
    else:
        framing = np.zeros(len(starts))
    cum = np.concatenate([[0.0], np.cumsum(clip.motion)])
    ref_sem = np.array(seg.clip, dtype=np.float32) if seg.clip else None
    out = []
    for j, st in enumerate(starts):
        t0 = float(clip.times[st])
        end = min(t0 + need, float(shot_end[st]))
        length = end - t0
        if length < min(need, 0.15):
            continue
        e = min(n, st + span)
        motion = (cum[e] - cum[st]) / max(1, e - st)
        score = float(framing[j]) - abs(motion - seg.motion) - 0.8 * max(0.0, 1 - length / need)
        if ref_sem is not None:
            sem = clip.semantic_at(t0 + length / 2)
            if sem is not None:
                # What the shot shows (wide/close, front/rear/wheel...) matters most. CLIP similarities
                # between related car shots sit around 0.6-0.95, so this term dominates the choice.
                score += 4.0 * float(sem @ ref_sem)
        out.append((t0, end, int(shot_id[st]), score))
    return out or [(0.0, min(need, clip.duration), 0, -1.0)]


def _best_window(clip: ClipInfo, need: float, target_motion: float) -> float:
    if need >= clip.duration:
        return 0.0
    starts = np.arange(0.0, clip.duration - need, 0.25)
    if starts.size == 0:
        return 0.0
    # A window that crosses a cut in the footage would add a cut the reference doesn't have.
    scores = [-abs(clip.motion_between(s, s + need) - target_motion) - (1.0 if _cuts_in(clip, s, s + need) else 0.0) for s in starts]
    return float(starts[int(np.argmax(scores))])


def _cut_times(clip: ClipInfo) -> np.ndarray:
    return clip.times[clip.cut_after][1:] if len(clip.times) else np.array([])


def _cuts_in(clip: ClipInfo, start: float, end: float) -> bool:
    ct = _cut_times(clip)
    return bool(((ct > start + 0.02) & (ct < end - 0.02)).any())


def _skip_cuts(clip: ClipInfo, start: float, need: float) -> float | None:
    """Earliest start at or after `start` whose window has no cut inside, or None if none fits."""
    for _ in range(50):
        if start + need > clip.duration:
            return None
        inside = [c for c in _cut_times(clip) if start + 0.02 < c < start + need - 0.02]
        if not inside:
            return start
        start = float(inside[-1])
    return None


def _subject_and_color(shot: Shot) -> tuple[tuple[float, float], dict]:
    """Median face position over a few frames of the window (identity preservation), plus colour stats."""
    end = shot.src_start + shot.src_len
    frames = [f for _, f in iter_frames(shot.clip.path, sample_fps=4, width=480, start=shot.src_start, end=end)][:8]
    centers = []
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = _FACE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
        if len(faces):
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            centers.append(((x + w / 2) / f.shape[1], (y + h / 2) / f.shape[0]))
    center = tuple(np.median(np.array(centers), axis=0).tolist()) if centers else (0.5, 0.5)
    return center, color_stats(frames)


def _missing_shots(blueprint: Blueprint, have: float, needed: float) -> list[str]:
    if have >= needed * 0.6:
        return []
    out = []
    for i, seg in enumerate(blueprint.segments, 1):
        kind = "fast-moving action" if seg.motion > 0.6 else "steady close-up" if seg.motion < 0.25 else "medium shot with movement"
        extra = " (filmed at 60fps for slow-mo)" if seg.is_slowmo else ""
        out.append(f"Shot {i}: {seg.duration:.1f}s {kind}{extra}")
    return out
