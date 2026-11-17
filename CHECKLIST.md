# Project Checklist

Status of every roadmap item. `[x]` done and tested · `[~]` partly done · `[ ]` not started · `[-]` waiting on the Meta API key.
Updated after each milestone. The **core goal is matching the cloned video's edit as closely as possible**, so the
Matching Engine sections come first; publishing comes last.

Last updated: 2026-11-17 (milestone: velocity edits P7, grade on the framed picture, cut detector)

---

## Current match scores

### Real Reels (`video-agent realtest`, 3 Reels)

- **Self-rebuild**: the Reel used as its own footage. The ceiling on real, compressed video. Pixel SSIM is the
  trustworthy number here; the camera part of "match" under-reports on extreme whip/blur edits (metric limit).
- **Your footage**: the Reel's style edited onto footage you supplied for it.

| Reel | Self-rebuild pixel SSIM | Self-rebuild match | On your footage |
|---|---|---|---|
| reel_1: cartoon "aura" edit, 37 s, 29 shots | 76.9% → **98.9%** | 85.2% → **99.3%** | – |
| reel_2: "Yo Jordan" looksmax edit, 23 s, 22 shots | 78.1% → **96.7%** (best 98.3%) | 78.9% → **96.4%** | – |
| crazy_edit: pro TikTok car edit, 9.8 s, 13 shots | **99.7%** | **92.1%** | McLaren rain clip (velocity edit v16, transitions + neutral greys): **71.0%** (cuts 96%, timing 100%, smooth 92%, pace 77%, sharp 100%, color 21%, camera 4%; v12 71.8%, v8 per-shot tint 78.1%) |

(first run → now). For the McLaren edit the literal-camera "style" number is the wrong yardstick: we now
re-create the edit's moves (whips at the cuts, punches/shakes on the hits, push-ins) around the car instead of
copying the orbit camera's path, so **music sync** (big moves landing on hits/cuts, vs the reference) is the
meaningful number. What still differs: the reference's camera work (orbit/FPV shots), its sunny teal look, and its
most extreme radial blurs. Judge by eye: `data/real/crazy_edit/_results/tiktok_vs_mclaren.png`.

### Ground-truth benchmark (`video-agent bench`, synthetic edits from the same raw footage)

| Case | v0.1 style | v0.1 SSIM | **Now style** | **Now SSIM** |
|---|---|---|---|---|
| Cuts on the beat + warm grade | 47.2% | 67.0% | **99.5%** | **97.8%** |
| Zoom punches | 47.7% | 71.0% | **97.6%** | **96.1%** |
| Slow push-in + rotation sway | 42.5% | 66.6% | **90.8%** | **76.0%** |
| Camera shake | 66.5% | 73.1% | **97.5%** | **96.2%** |
| Speed ramps | 67.8% | 74.5% | **99.8%** | **94.4%** |
| Flash transitions + cool grade | 36.0% | 57.7% | **99.5%** | **97.5%** |
| **Average** | **51.3%** | **68.3%** | **97.5%** | **93.0%** |

Still not covered by any test: footage with moving subjects for speed ramps, on-screen text, and real raw footage.

---

## Open problems (2026-11-14)

Measured diagnosis of late/uneven timing, too much shake, no flow between shots and quality loss, plus the questions that need your answers: see [PROBLEMS.md](PROBLEMS.md).

Done on 2026-11-14 after your answers (details and numbers in PROBLEMS.md Part 4):
- [x] Real-ESRGAN upscaling (Intel GPU) with texture blend; bicubic warps; blur follows the reference or only fast moves; reframe zoom capped at 1.5×
- [x] Footage windows ranked by sharpness and subject size; subject-centred crop (YOLOX) when no face
- [x] Filming advice per shot (what it shows + camera work your footage lacks)
- [x] Eased moves, whips carried across cuts, motion budget
- [x] Shake = short jolts on hits (or blur bursts), no constant wobble; detection ignores smeared frames
- [x] Hits at the drum's attack, typed kick/snare, the editor's sync drum recorded; moves land on exact frames; `--sync-offset`
- [x] Scorer parts timing / shake / smooth / sharp
- [x] Bot: move review with annotated preview and plain-word fixes (stored as ground truth); rating buttons (stored with scores)
- [x] Low-quality reference warning
- [x] Your pick from the −1/0/+1 blind test: **A = −1 frame**, now the default for every edit (`SYNC_OFFSET` in `render/engine.py`; `--sync-offset 0` for exactly on the hit)
- [x] Smoothness (P7) steps 1–7 built (PROBLEMS.md Part 5): velocity/calm style check, smooth in-shot speed ramps with real smear, never-stopping drift, motion calibration, Real-ESRGAN only on sharp frames, `flow` score, "film moving footage" advice; `--no-velocity` to turn off
- [x] P7 step 8: McLaren test edit rendered (`velocity_test/mclaren_velocity.mp4`): shake 85→100%, flow 11→20%, render 22→16 min; timing, cuts and sharp got worse, overall 56.1→53.7% (PROBLEMS.md)
- [x] Fixed after the test: time map was a frame late; whips 2× the reference's (0.45→0.25); smear shutter 270°→180°; render temp files on the data disk, cleaned per shot
- [x] Second velocity test edit (`velocity_test/mclaren_velocity_v2.mp4`): overall **59.1%** (best so far; previous 56.1%), cuts 88%, timing 96%, smooth 0→18%
- [x] Found the remaining spikes (whips 7× in-shot motion vs the TikTok's 2×; cut frames) and the extra smear (copied 360° camera blur on 74% of frames: 94% of those smeared), PROBLEMS.md
- [x] Fix: velocity shots blur only fast camera moves (≤180°); whips calibrated to 1.9× in-shot motion (the TikTok's own ratio) with a gentler ease. Modelled: whips 10.6× → 1.9×, camera blur 74% → 27% of frames
- [x] Third velocity test edit (`velocity_test/mclaren_velocity_v3.mp4`): overall **63.8%** (best so far), flow 21→74%, sharp 85→100%, fast÷typical 7.2→3.8×; smooth 18→1% (jerk fell, but mean motion fell more)
- [x] Fix: drift sways inside its caps (pan curves, push and roll ease back and forth). Modelled: long shots 0.55–0.73 → 1.87–2.19 px/frame (targets ~1.9–2.2), typical motion 1.32 → 2.34 (TikTok 2.06)
- [x] Fourth velocity test edit (`velocity_test/mclaren_velocity_v4.mp4`): overall **66.5%** (best so far), smooth 1→70%, flow 74→89%, sharp 100%; cuts 88→76% (punches now read as cuts)
- [x] Fix: velocity punches attack over 3 frames (35/75/100%, biggest step still on the hit). Modelled: biggest zoom step 0.112 → 0.082; aims at the false cuts at 3.93s and 4.27s (the ones at 5.57s and 7.53s are steady fast drift at the detector's threshold)
- [x] Fifth velocity test edit (`velocity_test/mclaren_velocity_v5.mp4`): overall **70.0%** (best so far), smooth 70→97%, cuts 76→82%, timing 100%; punch false cuts gone
- [x] Found why camera (4%) and color (32%) score low: the camera moves in different directions from the TikTok's (standing still would score 15%; ceiling within quality caps ~38%); the grade is measured before the camera zooms into the dark car (zoomed shots up to 21 brightness points too dark), and saturation is capped at 2×
- [x] Fix: grade measured on the framed picture (camera decided first). Modelled: zoomed shots' brightness on target (4.93s 20 → 40, TikTok 40)
- [x] Tried drift following the TikTok's camera path: modelled camera 4.9 → 5.9% with rougher motion, dropped (ceiling ~15% on different footage; standing still scores 14.9%)
- [x] Sixth velocity test edit (`velocity_test/mclaren_velocity_v6.mp4`): overall 69.7% (v5 70.0%); color 32→43% (brightness fixed), cuts 82→73% (3 more false cuts on steady motion in the brightened shots)
- [x] Cut detector: a weak detection on a continuous picture is motion, not a cut (moves to the real jump when one is next to it). TikTok, reel_1, reel_2 unchanged; v5/v6 false cuts gone. Re-scored: v6 **73.4%** (best), v5 72.0%, cuts 92%. Benchmark 97.8% (was 97.7%), cuts 100%
- [x] Found where v6's remaining colour miss comes from: 80% grade strength (~0.4 of 1.87) and stages after the grade (~0.45); the LUT is exact, the saturation cap barely matters
- [x] Colour: velocity edits grade at 100% (your call). One-shot test: 2.97s 1.88 → 1.55, 4.93s 1.45 → 0.85; motion blur is not the loss; the linear grade clips the black car's shadows (contrast) and can't make grey footage colourful (saturation)
- [x] Colour: the grade matches lightness and colourfulness distributions (quantiles) instead of mean/spread. On McLaren stills through the real LUT path: colour miss 1.04 → 0.88, yellow cast gone, pixels crushed to black 8.6% → 0.7%, blown to white 3.0% → 0%. Benchmark 97.8% → 98.0% (colour 95.2 → 95.7%)
- [x] Seventh velocity test edit (`velocity_test/mclaren_velocity_v7.mp4`): overall **76.7%** (best so far), color 43→66%, cuts 92%; one shot (3.10–4.93s) came out too dark
- [x] Fix: the slope-limited brightness curve keeps the target's average (that shot on stills: 32.6 → 42.2, TikTok 42.6; whole edit colour miss 0.88 → 0.81). Benchmark 98.0%
- [x] Eighth velocity test edit (`velocity_test/mclaren_velocity_v8.mp4`): overall **77.2%** (best so far), color 66→70%; the dark shot fixed (brightness 32.6 → 43.3, TikTok 42.6). What's left of colour is contrast (2.5× stretch limit)
- [x] Your v8 review: colours jump shot to shot because each shot copies its TikTok shot's content-driven colours; measured the score vs consistency trade-off (PROBLEMS.md Part 6)
- [x] Region-aware grade tried on stills: worse score (2.1–3.5 vs 0.80) and coloured speckles on the black car, not built
- [x] Colour consistency (your call): one tint for every shot in velocity edits. v9 (`velocity_test/mclaren_velocity_v9.mp4`): tint jumps 6.8 → 1.9, overall 77.2 → 74.0% (colour 70 → 48%)
- [x] Your v9 review (colours off after 4 s): v9 gave grey roads a teal cast. Greys now go to the TikTok's greys plus half its average tint (your pick). v10 (`velocity_test/mclaren_velocity_v10.mp4`): 72.8%, roads grey again
- [x] Your v10 review (blue rises at 3–5 s): frame-by-frame (5/s) analysis; cause: copied content tint + bluer source shots. Fix built: one colour transform for the whole edit, greys to the TikTok's greys, colourfulness ≤1.6×, shot matching
- [x] Eleventh velocity test edit (`velocity_test/mclaren_velocity_v11.mp4`): cyan cast gone (red−green −10…−15 → −2…−6 per second), greys near neutral; overall 70.2% (colour 29%: no longer copies the TikTok's content colours)
- [x] Director: no repeated picture within 3 shots (same angle or a look-alike window); 2.17s no longer repeats the wheel close-up
- [x] Cut detector: adds back clear cuts PySceneDetect skips while motion keeps its score above the threshold. v5–v11 now find all 12 cuts (1.87s was missed since v3); TikTok and reel_2 unchanged, reel_1 +1 real cut. Re-scored: v11 70.2 → 72.0%, cuts 96%
- [x] Twelfth velocity test edit (`velocity_test/mclaren_velocity_v12.mp4`): 71.8%, no repeated footage, all 12 cuts detected; scripts kept in `velocity_test/scripts/`
- [x] Your v12 review (blue at 3–4 s): the grade moved our greys to the TikTok's teal greys; greys now stay neutral (`keep_greys`). Street shot 2.97s: blue−red +3.8 → +0.9
- [x] Detached transitions: corner panel, wheel portal, car cut-out rendered across cuts (no download needed: CLIP finds the wheel, GrabCut cuts out the car). Portal detected automatically (1 hit, 0 false alarms on 3 references); panel and cut-out marked in the move review. Director picks a wheel shot for a portal
- [x] Thirteenth velocity test edit (`velocity_test/mclaren_velocity_v13.mp4`): all three transitions rendered (portal closest to the TikTok); 67.0% (67.9% re-scored). Fixed after: portal and cut-out start 5 frames before the cut, detector counts cuts 3 frames apart, footage windows end before the clip's own cuts
- [x] Fourteenth velocity test edit (`velocity_test/mclaren_velocity_v14.mp4`): 70.1%, cuts 92%; portal on time; after it, the rim frame between the two flashes fixed (stills)
- [x] Where the remaining points are (v14): camera (14 pts, footage-bound) and colour tint (12 pts, the TikTok's content colours you chose not to copy); contrast and hue-selective colour tried on stills, too small or bluer, not built. Ceiling with this footage ≈72–75%
- [x] Polish, fifteenth velocity test edit (`velocity_test/mclaren_velocity_v15.mp4`): 70.1%, pace 72 → 76%; portal is an iris around the still previous shot, cut-out fades in and grows (4.80s still reads as a cut)
- [x] More polish (not rendered): cut-out car enters at 50% size and fades in (no extra cut on a test clip); chroma-only noise reduction after the grade (colour noise −12%, sharpness unchanged). Benchmark 97.7%
- [x] Sixteenth velocity test edit (`velocity_test/mclaren_velocity_v16.mp4`): **71.0%**, cuts 96% (no extra detections), colour noise −9%
- [ ] Detect corner panels and car cut-outs automatically (needs more reference edits to learn from)
- [ ] P7 follow-up if still jerky: rebuild moves as one spline path instead of bumps on the drift
- [ ] 5 more reference edits (you), then tune detection on them and the move reviews
- [ ] Faster upscaling (22 min render): tile tuning or upscale only the used frames once per clip

## Beating human editors: the gaps, in priority order

1. [~] **Subject understanding**: YOLOX finds the car/person per shot; footage is reframed so the subject's size/position match the reference, and moves happen around the subject. Next: track the subject through the shot (it's a per-shot median now).
2. [~] **Shot understanding**: CLIP describes every reference shot and every moment of your footage; the Director uses it to pick moments. Weak on fine angle differences (front vs rear 3/4) — next: angle classifier from CLIP text prompts + subject box shape.
3. [ ] **Camera work vs editing**: footage filmed static can't become an orbit/FPV shot. Detect when the reference's power is camera work and tell the user what to film (or source it).
4. [ ] **Music**: own-song re-timing (cuts and moves to the new track's hits), drops, risers, sound-effect hits (whooshes, impacts) added on transitions.
5. [ ] **Effects a pro uses**: radial zoom blur, directional blur, RSMB-style motion blur, glow, chromatic aberration, sky replacement, subject masks (text behind subject), speed ramps (velocity edits).
6. [ ] **Look**: grade transfer that respects the content (mood, sky, skin), not only statistics.
7. [ ] **Taste and judgement**: which moments are the best (sharpest, most dramatic), when to break the template, story/hook — learned from many edits + your feedback + performance data.
8. [ ] **Measurement of what humans see**: the style score is still a proxy; a human-preference check (A/B with you) is the final judge.

## A. Matching Engine — measurement

- [x] **Match score** (`video-agent score ref.mp4 out.mp4`): cuts (±1.5 frames), camera motion (frame-to-frame), colour (Lab per shot), pace, flashes, duration → one %
- [x] Camera metric validated with control tests (identical 100%, re-encode 97.5%, upscaled real Reel 92.6%; v0.1 outputs 57–72%, new 91–100%); tolerance grows with move size, unreadable blurred frames skipped
- [ ] Camera metric still under-reports on extreme whip/blur edits (68% on a 99.7%-pixel-identical rebuild)
- [x] **Music sync** measure: share of big moves on hits/cuts vs the reference (reported, not yet weighted in the overall %)
- [ ] Known: push_rotate benchmark down 95% → 91% (its flat-gradient shot isn't confirmed as same footage, so it gets re-created moves); reel_2 self-rebuild 98.3% → 96.7% pixel
- [x] **Pixel similarity** (SSIM) for same-footage tests
- [x] **Ground-truth benchmark** (`video-agent bench`): 6 synthetic edits, history saved in `data/bench/history.jsonl`
- [x] Score sent in Telegram after every render (when the reference file is on the server)
- [x] Real-Reels test runner (`video-agent realtest`): same-footage pixel test, your-footage style test, side-by-side frame sheets, `REPORT.md`
- [x] Variable-frame-rate references normalised to constant fps before cloning
- [~] **Real reference test set**: 3 real Reels (cartoon aura edit, Jordan looksmax edit, pro TikTok car edit); need 20–30 across styles, ideally with raw footage
- [x] Self-rebuild test (Reel as its own footage); per-Reel footage folders for the different-footage test; cross-apply between Reels only with `--cross` (same kind of edit only)
- [ ] Benchmark footage with moving subjects (people, cars) so speed ramps and motion are really tested
- [ ] Style-match benchmark with *different* footage (the everyday case)

## B. Matching Engine — deconstruction (`/clone`)

- [x] Shot cuts, frame-exact, with false cuts removed (shake, whip) and cuts hidden under flashes recovered
- [x] Music: BPM, beat timestamps, beat drops (librosa); track extracted for reuse
- [x] Motion energy per shot (optical flow)
- [x] White-flash detection, with flash length in frames
- [x] **Per-frame camera curves**: zoom, rotation, pan, shake for every frame of every shot; features tracked continuously and fitted to the shot's first frame (no drift)
- [x] **Shot curves anchored on the sharpest frame** (tracked forwards and backwards); a blurred transition frame at a shot's start no longer tilts/zooms the whole shot
- [x] **Edit events** (motion translation): whips in/out, punch-zooms, shake bursts and push per shot, separated from the original camera's movement
- [x] **Music hits** (strong onsets: kicks, snares, 808s); punches and shakes are snapped to them
- [x] **Hybrid tracker for whip pans / zoom blurs**: frames too blurred for features use whole-image alignment at low resolution, with sanity limits
- [x] Motion-blur detection: fastest vs slowest third of frames per shot → none / 180° / 360° shutter (pro edits never hold still)
- [x] Per-shot colour profile (Lab mean/contrast) + content thumbnail for footage matching
- [x] Hook score for the first 3 seconds
- [~] Slow motion: only duplicate-frame slow-mo detected, judged against the video's own held-frame rate (cartoons animated on twos no longer read as slow-mo); **no speed-ramp estimation yet**
- [x] Output keeps the reference's shape (9:16, 4:5, 1:1, 16:9)
- [x] **Frame-exact shot starts**: rounded start times used to make every shot one frame early on real edits (pixel match +3 to +11 points)
- [ ] **Speed curve estimation** from optical flow, including ramps (next priority)
- [ ] Transition classification: whip pan, zoom blur, glitch, RGB split, crossfade (flash/cut done; whips/zooms are captured as camera motion)
- [ ] Sky enhancement / sky replacement (pro car edits use dramatic skies)
- [ ] Effect detection: glow, chromatic aberration, grain, vignette, letterbox, velocity edits
- [ ] On-screen text via OCR: words, position, timing, size, colour, animation (tesseract is installed)
- [ ] Sound effects: whoosh/impact hits and their timing
- [ ] Separate editor moves from the original camera's moves in the reference (both are copied together now)
- [ ] Ignore burned-in captions when tracking (they fight the background motion on selfie footage)

## C. Matching Engine — rendering (After Effects replacement)

- [x] FFmpeg shot pipeline: 9:16 crop, speed pieces, effects, text, Instagram-spec encode
- [x] **Frame-by-frame registration** when the footage is the reference's own: the editor's exact transform, no guessing (real-Reel pixel match 80% → 96%)
- [x] **Motion translation on different footage**: the editor's moves are re-created on the hits (whip smears with mirrored edges like AE Motion Tile, punches, shakes, capped digital push) instead of copying the reference camera's literal path
- [x] Replay centred on the shot's typical (median) framing, not the middle of its range
- [x] **Keyframe compositor**: replays per-frame camera curves on new footage, cancels the footage's own motion first, automatic cover zoom, AE-style motion blur. Runs on the CPU (OpenCV), directly on YUV planes (lossless).
- [x] **RIFE AI slow motion** on the Intel GPU (Vulkan), FFmpeg `minterpolate` as automatic fallback
- [x] **Colour as 3D LUTs in 16-bit**: fitted from aligned frames when the footage matches the reference, Lab look transfer otherwise
- [x] **Colour pipeline fixed**: everything converted to limited-range BT.709, all intermediates tagged, accurate rounding (wrong tags/ranges were the biggest colour error found)
- [x] Flash transitions as a real white fade with the measured length
- [x] Black-and-white looks transferred fully (were only half-desaturated)
- [x] Outputs: master, Telegram preview, cover frame, 1:1 grid crop
- [ ] Rotation/push-in shots: pixel match only 78% on the synthetic test (flat gradient footage)
- [ ] Extreme radial zoom blur / directional blur as explicit effects (the compositor's motion blur covers only part of the look)
- [x] Motion blur is not added twice when the footage already carries it
- [ ] Render speed: ~8–10× slower than real time on this laptop (30 s Reel ≈ 4–5 min); GPU compositor (OpenCL/shaders) would cut this
- [ ] Effects library: glow, chromatic aberration, RGB split, glitch, zoom blur, whip pan, grain, vignette, letterbox, light leaks
- [ ] Subject segmentation (MediaPipe/rembg) for text-behind-subject and background effects
- [ ] Motion tracking to pin text or stickers to objects
- [ ] Kinetic typography templates (Remotion)
- [ ] 3D text / 3D camera (Blender headless)
- [ ] After Effects via NexRender on a Windows worker (optional, only if a subscription comes back)
- [ ] HDR phone footage (iPhone HLG / BT.2020) tone-mapped to SDR

## D. AI Director (`/apply`)

- [x] Footage audit (resolution, fps, length), with warnings
- [x] Same-footage claims verified geometrically (ORB + RANSAC): similar framing of a *different* car is no longer mistaken for the reference's footage
- [x] **Angle-aware shot choice**: each camera angle in your clip is a separate source shot; never the same angle twice in a row; framing similarity (wide vs close-up) picks which angle fits each reference shot
- [x] **Content matching**: finds the exact clip and frame when you give it the reference's own footage — whole shots aligned as sequences, zoom-tolerant, ignores frame edges, prefers footage cut points
- [x] Never picks a footage window that crosses a cut in the footage (that added cuts the reference doesn't have)
- [x] Motion-matched clip-window assignment otherwise, avoids repeating clips
- [x] Face-centred vertical crop (static per shot)
- [x] Missing-shot list when footage is short
- [x] Car/person detection (YOLOX-s) and subject-matched framing per shot
- [x] CLIP shot similarity in footage selection
- [ ] Tracked dynamic crop (follows the subject during the shot)
- [ ] Content matching for *different* footage (close-up → close-up) using image embeddings
- [ ] Re-timing cuts to a different song's beats when the user brings their own music
- [ ] Stock footage fill (Pexels API key; code not written yet)
- [ ] AI image fill for missing shots

## E. Internet asset sourcing (AI Director, spec 5.2) — NOT BUILT YET

When you have no footage: find clips/images that match each reference shot (~95%) and edit them in.
- [ ] Stock video search with licences that allow posting: Pixabay (key requested, ~1 day), Pexels (new keys paused by Pexels), Coverr
- [ ] Query planning per shot with the LLM: subject + setting + shot type + camera move ("McLaren 720S Spider, rain, night, low angle rear, slow push-in")
- [~] Ranking candidates against each reference shot: CLIP is installed and used for footage; still to wire into sourcing with framing, motion, colour, resolution
- [ ] Download, trim the best window, then edit as usual; report the match % per shot and which shots had no good candidate
- [ ] AI image/video generation to fill shots nothing matches (needs a paid API: e.g. Runway, Luma, Kling, Veo)
- [ ] Legal guardrails: only licensed sources; brand footage from press sites only under their terms; no scraping of other creators' videos

## F. Learning loop

- [x] Typed feedback → edit changes → re-render ("darker, faster cuts, more zoom")
- [x] Feedback stored in `Learning_Weights` and applied to the user's next edits
- [-] Link views/retention to blueprint settings (needs Meta insights)

## G. Infrastructure

- [x] Own git repo, Python 3.12 venv, `pyproject.toml`, CLI (`video-agent`)
- [x] Database: all 5 tables from the spec. **PostgreSQL** (JSONB, connection pool) via `DATABASE_URL`; SQLite only as a fallback. Tested on a temporary PostgreSQL 18
- [x] `video-agent migrate-db` copies the old SQLite data into PostgreSQL
- [x] Your `video_agent` PostgreSQL database created, `DATABASE_URL` set, SQLite data migrated
- [x] Redis FSM storage (optional via `REDIS_URL`)
- [x] Hybrid LLM: Claude → OpenRouter → Gemini → OpenAI with fallback; built-in rules when no key
- [x] Tests (13), incl. end-to-end clone → render, lossless compositor round trip, motion compensation
- [x] RIFE installed in `tools/rife/`; YOLOX-s and CLIP in `tools/models/` (not in git; see README)
- [~] Render queue: in-process limiter (`MAX_PARALLEL_RENDERS`); no Celery workers yet
- [ ] Local Telegram Bot API server (2 GB file limit instead of 20/50 MB)
- [ ] GPU/cloud render server for scale

## H. Telegram bot

- [x] `/start`, `/help`, `/cancel`
- [x] `/clone` (file or link), save with nickname
- [x] `/apply` (pick template, upload clips, on-screen text, render) + match score message
- [x] `/templates` gallery: apply, rename, delete, preview, usage count
- [x] `/brand`: colours, font file, caption voice samples
- [x] Forward a finished video → reuse its style
- [~] `/publish`: caption + hashtags only; posting waits for Meta
- [ ] GIF previews in the template gallery (uses the last MP4 render now)

## I. Waiting on the Meta API key

- [-] OAuth + webhooks
- [-] Comment-to-DM lead magnet, AI persona replies
- [-] `/analyze` trending Reels by hashtag
- [-] Auto-publish Reels / Stories / Feed
- [-] `/audit` insights at 24h / 48h / 7d
- [-] `/solve` growth advice
