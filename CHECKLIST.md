# Project Checklist

Status of every roadmap item. `[x]` done and tested · `[~]` partly done · `[ ]` not started · `[-]` waiting on the Meta API key.
Updated after each milestone. The **core goal is matching the cloned video's edit as closely as possible**, so the
Matching Engine sections come first; publishing comes last.

Last updated: 2026-11-13 (milestone: pro TikTok edit test, hybrid tracker, PostgreSQL)

---

## Current match scores

### Real Reels (`video-agent realtest`, 3 Reels)

- **Self-rebuild**: the Reel used as its own footage. The ceiling on real, compressed video. Pixel SSIM is the
  trustworthy number here; the camera part of "match" under-reports on extreme whip/blur edits (metric limit).
- **Your footage**: the Reel's style edited onto footage you supplied for it.

| Reel | Self-rebuild pixel SSIM | Self-rebuild match | On your footage |
|---|---|---|---|
| reel_1: cartoon "aura" edit, 37 s, 29 shots | 76.9% → **98.6%** | 85.2% → **99.0%** | – |
| reel_2: "Yo Jordan" looksmax edit, 23 s, 22 shots | 78.1% → **98.3%** | 78.9% → **96.1%** | – |
| crazy_edit: pro TikTok car edit, 9.8 s, 13 shots | **99.7%** | **87.9%** | McLaren rain clip: **~50%** style match |

(first run → now). The McLaren edit has the same rhythm, angle changes, whip/zoom moves and motion blur
as the reference; what it can't copy is the content-dependent look (sunny teal sky and green grass vs a grey rainy
street) and the reference's most extreme radial zoom blurs. Judge it by eye (`compare.png`), not only by the number.

### Ground-truth benchmark (`video-agent bench`, synthetic edits from the same raw footage)

| Case | v0.1 style | v0.1 SSIM | **Now style** | **Now SSIM** |
|---|---|---|---|---|
| Cuts on the beat + warm grade | 47.2% | 67.0% | **99.5%** | **97.8%** |
| Zoom punches | 47.7% | 71.0% | **97.4%** | **94.8%** |
| Slow push-in + rotation sway | 42.5% | 66.6% | **95.3%** | **78.0%** |
| Camera shake | 66.5% | 73.1% | **97.4%** | **97.9%** |
| Speed ramps | 67.8% | 74.5% | **99.8%** | **90.5%** |
| Flash transitions + cool grade | 36.0% | 57.7% | **98.9%** | **97.5%** |
| **Average** | **51.3%** | **68.3%** | **98.0%** | **92.8%** |

Still not covered by any test: footage with moving subjects for speed ramps, on-screen text, and real raw footage.

---

## A. Matching Engine — measurement

- [x] **Match score** (`video-agent score ref.mp4 out.mp4`): cuts (±1.5 frames), camera motion (frame-to-frame), colour (Lab per shot), pace, flashes, duration → one %
- [x] Camera metric validated with control tests (identical 100%, re-encode 97.5%, upscaled real Reel 92.6%; v0.1 outputs 57–72%, new 91–100%); tolerance grows with move size, unreadable blurred frames skipped
- [ ] Camera metric still under-reports on extreme whip/blur edits (69% on a 99.7%-pixel-identical rebuild)
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
- [ ] Tracked dynamic crop (follows the subject during the shot); better face/person/car detection
- [ ] Content matching for *different* footage (close-up → close-up) using image embeddings
- [ ] Re-timing cuts to a different song's beats when the user brings their own music
- [ ] Stock footage fill (Pexels API key; code not written yet)
- [ ] AI image fill for missing shots

## E. Internet asset sourcing (AI Director, spec 5.2) — NOT BUILT YET

When you have no footage: find clips/images that match each reference shot (~95%) and edit them in.
- [ ] Stock video search with licences that allow posting: Pexels, Pixabay (free API keys), Coverr
- [ ] Query planning per shot with the LLM: subject + setting + shot type + camera move ("McLaren 720S Spider, rain, night, low angle rear, slow push-in")
- [ ] Ranking candidates against each reference shot: CLIP image-text and image-image similarity, framing, motion, colour, resolution
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
- [ ] Create your `video_agent` database/role and set `DATABASE_URL` (needs your PostgreSQL password — see README)
- [x] Redis FSM storage (optional via `REDIS_URL`)
- [x] Hybrid LLM: Claude → OpenRouter → Gemini → OpenAI with fallback; built-in rules when no key
- [x] Tests (10), incl. end-to-end clone → render, lossless compositor round trip, motion compensation
- [x] RIFE installed in `tools/rife/` (not in git; see README)
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
