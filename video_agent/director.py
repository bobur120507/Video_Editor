"""AI Director: audits footage, assigns clip windows to blueprint shots, and finds the subject to crop around."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .blueprint import Blueprint, Segment
from . import vision
from .advice import filming_advice, velocity_advice
from .analysis.camera import estimate_curves
from .analysis.visual import detect_cuts, track_frames
from .media import Probe, color_stats, frame_at, iter_frames, probe, thumb

log = logging.getLogger(__name__)

_FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


CONTENT_MATCH = 0.8  # thumbnail correlation above which a clip is treated as the reference's own footage
THUMB_ZOOMS = (1.0, 1.2, 1.45, 1.75)  # reference shots are often zoomed in (punches, push-ins)
# A shot must not look like any of the last few: the McLaren edit used the same wheel close-up at
# 1.57 s and 2.17 s (one shot apart), and the cut between near-identical pictures doesn't read as a
# cut. Repeats cost REPEAT_PENALTY on the next shot, fading to nothing RECENT_SHOTS shots later.
RECENT_SHOTS = 3
REPEAT_PENALTY = 0.6
LOOKS_SAME = 0.85  # thumbnail similarity above which two windows show the same picture (as visual._same_picture)


@dataclass
class ClipInfo:
    probe: Probe
    times: np.ndarray
    motion: np.ndarray  # normalized 0..1 motion per sampled frame
    thumbs: np.ndarray  # (N, zooms, 256) content thumbnails at `times`, one per THUMB_ZOOMS
    cut_after: np.ndarray  # (N,) True where a new shot starts in this footage
    sem_times: np.ndarray = field(default_factory=lambda: np.zeros(0))  # CLIP samples (3 per second)
    sem: np.ndarray = field(default_factory=lambda: np.zeros((0, 512)))
    sharp: np.ndarray = field(default_factory=lambda: np.zeros(0))  # per frame at `times`: 1 = as sharp as the clip gets
    subj_h: np.ndarray = field(default_factory=lambda: np.zeros(0))  # at `sem_times`: main subject's height (0 = none)
    subj_c: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))  # ... and its centre

    def subject_between(self, start: float, end: float) -> tuple[float, tuple[float, float] | None]:
        """Median subject height in the window (0 when it is mostly absent) and its centre."""
        m = (self.sem_times >= start) & (self.sem_times < end) if len(self.subj_h) else np.zeros(0, bool)
        if not m.any():
            return 0.0, None
        h = self.subj_h[m]
        if (h > 0).mean() < 0.5:
            return 0.0, None
        return float(np.median(h[h > 0])), tuple(np.median(self.subj_c[m][h > 0], axis=0).tolist())

    def sharpness_between(self, start: float, end: float) -> float:
        m = (self.times >= start) & (self.times < end)
        return float(self.sharp[m].mean()) if m.any() and len(self.sharp) else 1.0

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
    times, energy, thumbs, sharp = [], [], [], []
    prev = None
    for t, frame in iter_frames(path, sample_fps=info.fps, width=240):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        energy.append(0.0 if prev is None else float(cv2.absdiff(prev, gray).mean()))
        sharp.append(float(cv2.Laplacian(gray, cv2.CV_32F).var()))
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
    sh = np.array(sharp)
    clip = ClipInfo(probe=info, times=np.array(times), motion=np.clip(norm, 0, 1), thumbs=th, cut_after=cuts,
                    sharp=np.clip(sh / (np.percentile(sh, 90) + 1e-6), 0, 1) if sh.size else sh)
    if vision.available():
        samples = list(iter_frames(path, sample_fps=3, width=320))
        clip.sem_times = np.array([t for t, _ in samples])
        clip.sem = vision.embed_images([f for _, f in samples])
        boxes = [vision.detect(f) for _, f in samples]
        clip.subj_h = np.array([b[0].h if b else 0.0 for b in boxes])
        clip.subj_c = np.array([(b[0].cx, b[0].cy) if b else (0.5, 0.5) for b in boxes]).reshape(-1, 2)
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
    recent: list[tuple[tuple[int, int], np.ndarray]] = []  # (angle, picture) of the last shots, newest last
    for seg in blueprint.segments:
        need = seg.source_seconds_needed()
        found = _content_window(seg, clips, need, blueprint.source.path)
        if found:
            clip, start, corr = found
            length = min(need, clip.duration - start)
            shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length, content_match=corr))
            recent.append(((clips.index(clip), -1), _window_picture(clip, start, start + length)))
            continue
        options = []
        for ci, c in enumerate(clips):
            for start, end, key_shot, score in _rank_windows(seg, c, need):
                key = (ci, key_shot)
                score -= _repeat_penalty(key, _window_picture(c, start, end), recent)  # not the same picture again soon
                score -= 0.25 * used.get(key, 0)  # spread the edit over every angle
                score -= 0.3 if any(u == ci and start < b and a < end for u, a, b in used_ranges) else 0.0
                options.append((score, c, start, end, key))
        if seg.transition_in == "portal":
            options = _prefer_wheels(options, need)
        _, clip, start, end, key = max(options, key=lambda o: o[0])
        used[key] = used.get(key, 0) + 1
        used_ranges.append((key[0], start, end))
        recent.append((key, _window_picture(clip, start, end)))
        length = end - start
        shots.append(Shot(segment=seg, clip=clip, src_start=start, src_len=length, stretch=need / length))

    for shot in shots:
        shot.center, shot.stats = _subject_and_color(shot)

    missing = _missing_shots(blueprint, have, needed)
    if vision.available() and not missing:
        # Footage is long enough but may not show what the reference shows: shot-by-shot advice.
        missing = filming_advice(blueprint, shots, _footage_curves(shots))
    if blueprint.style == "velocity" and (line := velocity_advice(blueprint, _footage_motion(clips))):
        missing = [line, *missing]
    return EditPlan(shots=shots, warnings=warnings, missing=missing)


# A portal shot opens on a wheel (the TikTok's: a wheel close-up). CLIP is nearly always sure it sees
# a wheel, even head-on where the wheels hide in shadow, so the wheel's size decides: full bonus from
# a radius of BIG_WHEEL of the frame width (a close-up), little for a distant one.
WHEEL_BONUS = 1.6  # 0.8 still let a front view (no wheel visible) open the BMW's second portal
BIG_WHEEL = 0.12
WHEEL_CANDIDATES = 20  # best-ranked windows checked for a wheel (CLIP on ~100 patches each, ~4 s)


def _prefer_wheels(options: list[tuple], need: float) -> list[tuple]:
    """Re-rank the best windows for a wheel-portal shot by how clearly their first frame shows a wheel.

    The bonus shrinks with a window too short for the shot and with soft footage: unscaled, a 0.20 s wheel
    window won the BMW template's 0.63 s portal shot and was slowed 3.2x into a soft shot (v4, 2026-11-18)."""
    from .render.transitions import wheel

    top = sorted(options, key=lambda o: o[0], reverse=True)[:WHEEL_CANDIDATES]
    out = []
    for score, clip, start, end, key in top:
        frame = frame_at(clip.path, start + 0.02, width=540)
        _, circle = wheel(frame) if frame is not None else (0.0, None)
        size = circle[2] / frame.shape[1] if circle else 0.0
        fills = min(1.0, (end - start) / max(need, 1e-6))
        bonus = WHEEL_BONUS * min(1.0, size / BIG_WHEEL) * fills ** 2 * clip.sharpness_between(start, end)
        out.append((score + bonus, clip, start, end, key))
    return out


def _window_picture(clip: ClipInfo, start: float, end: float) -> np.ndarray:
    """Unit-norm content thumbnail of a footage window (its frames' thumbnails averaged)."""
    if len(clip.thumbs) == 0:
        return np.zeros(256, np.float32)
    m = (clip.times >= start) & (clip.times < max(end, start + 1e-3))
    th = clip.thumbs[m, 0] if m.any() else clip.thumbs[[int(np.argmin(np.abs(clip.times - start)))], 0]
    v = th.mean(axis=0)
    return v / (np.linalg.norm(v) + 1e-9)


def _repeat_penalty(key: tuple[int, int], picture: np.ndarray, recent: list[tuple[tuple[int, int], np.ndarray]]) -> float:
    """Penalty for showing the same angle, or a picture that looks the same, as one of the last
    RECENT_SHOTS shots: full on the next shot, fading with distance."""
    worst = 0.0
    for d, (k, pic) in enumerate(reversed(recent[-RECENT_SHOTS:]), start=1):
        if k == key or float(picture @ pic) > LOOKS_SAME:
            worst = max(worst, REPEAT_PENALTY * (1 - (d - 1) / RECENT_SHOTS))
    return worst


def _footage_motion(clips: list[ClipInfo]) -> float:
    """Typical motion of the footage (px/frame at 160 px width, like the reference's Segment.velocity)."""
    flows = []
    for c in clips:
        try:
            flows.append(track_frames(c.path, c.probe.fps).flow[1:])
        except Exception as e:  # advice is optional; never fail the edit over it
            log.warning("footage motion for advice failed: %s", e)
    return float(np.median(np.concatenate(flows))) if flows and sum(map(len, flows)) else 1e9


def _footage_curves(shots: list[Shot]) -> list:
    out = []
    for shot in shots:
        try:
            out.append(estimate_curves(shot.clip.path, [(shot.src_start, shot.src_start + shot.src_len)], shot.clip.probe.fps, width=320)[0])
        except Exception as e:  # advice is optional; never fail the edit over it
            log.warning("camera estimate for advice failed: %s", e)
            out.append(None)
    return out


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
        # Up to the angle's last frame, not the next angle's first: with the speed ramp's rounding that frame
        # showed up as a one-frame flash of the next angle at the end of the McLaren v13 edit (9.80 s).
        shot_end[idx] = clip.times[idx[-1]]
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
        # Sharp, in-focus moments: soft or shaky stretches look like low quality once enlarged.
        crisp = clip.sharpness_between(t0, end)
        score += 0.6 * crisp - (0.5 if crisp < 0.35 else 0.0)
        if seg.subject is not None and len(clip.subj_h):
            # The subject at about the reference's size needs no digital zoom to match (which softens).
            h, _ = clip.subject_between(t0, end)
            score += 0.6 * max(0.0, 1 - abs(np.log(h / seg.subject.h))) if h > 0 else -0.4
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
    if centers:
        center = tuple(np.median(np.array(centers), axis=0).tolist())
    else:  # no face: the main subject (car, person, ...) when the detector found one
        center = shot.clip.subject_between(shot.src_start, end)[1] or (0.5, 0.5)
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
