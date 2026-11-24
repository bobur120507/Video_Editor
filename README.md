# Video Editor Agent

**Show it an edit you like. It learns the edit and re-cuts your own clips the same way.**

The agent watches a short vertical video (Reel, TikTok, Short) and writes down its editing:
cuts and their timing against the music, speed ramps, zoom punches, whips, shake, flashes,
transitions and colour grade. It saves all of that as a reusable **Edit Blueprint**. Give it your
raw clips and it renders a 1080×1920 edit in that style, plus a cover, a grid crop and a score for
how closely it matched. You can drive it from a **web UI**, a **Telegram bot** or the **command line**.
It runs on one Linux machine with no After Effects, and it can also import After Effects projects as templates.

---

## Results on real Reels

Each clip is a few seconds of a **real render** made by the agent. Click a GIF to open the full
clip with sound. The left panel is the original Reel, the middle panel is the agent's rebuild from
the Reel's own footage, and the right panel is the same blueprint applied to different footage.

### reel_1: cartoon "aura" edit (37 s, 29 shots, 99 BPM)

<p align="center">
  <a href="docs/media/reel_1.mp4"><img src="docs/media/reel_1.gif" alt="reel_1 side by side" width="720"></a>
</p>

| Rebuild vs reference | Pixel SSIM | Match score | Breakdown |
|---|---|---|---|
| First version | 76.9 % | 85.2 % | |
| **Now** | **98.9 %** | **99.3 %** | cuts 100 · camera 99 · colour 100 · pace 97 |

The reference was a recompressed 576×576 download. The rebuild is a 1080×1080 master with 5 whips,
3 punches, 3 shakes, 6 push-ins and 4 slow-motion shots.

### reel_2: "Yo Jordan" looksmax edit (23 s, 22 shots, 129 BPM)

<p align="center">
  <a href="docs/media/reel_2.mp4"><img src="docs/media/reel_2.gif" alt="reel_2 side by side" width="720"></a>
</p>

| Rebuild vs reference | Pixel SSIM | Match score | Breakdown |
|---|---|---|---|
| First version | 78.1 % | 78.9 % | |
| **Now** | **96.7 %** | **96.4 %** | cuts 100 · camera 92 · colour 94 · pace 99 |

The blueprint holds 20 slow push-ins, 19 rotation sways and a drop, and the rebuild is made from it alone.

The right-hand panels are a style-transfer test. reel_1's blueprint cut reel_2's clips, and
reel_2's blueprint, including its muted colour grade, cut reel_1's cartoon. They aren't scored,
because there is no ground truth for "how reel_1 would have edited these clips". Judge them by eye.

### Your footage: a McLaren clip in the style of a pro TikTok car edit

<p align="center">
  <a href="docs/media/mclaren.mp4"><img src="docs/media/mclaren.gif" alt="raw McLaren clip, the reference TikTok, and the agent's edit" width="528"></a>
</p>

A single 17-second clip of a McLaren in the rain was cut into a 9.8-second velocity edit timed
to the reference's music. The edit has whips across the cuts, punches and shakes on the
drum hits, speed ramps, a wheel-portal transition and warm glows on the beat. **Match: 70.7 %**
(cuts 96, music timing 100, smoothness 92, sharpness 100). The low colour and camera numbers are
expected: the reference is a sunny teal orbit shot, and the clip is a grey street.

> Self-rebuild means the Reel's own footage is cut again from the blueprint. That is the ceiling
> on real, recompressed video, and it shows the blueprint holds everything needed to reproduce the
> edit. The McLaren case is everyday use: someone else's style on your footage.
> All numbers come from `video-agent realtest` and `video-agent score`; see [CHECKLIST.md](CHECKLIST.md).

---

## The web UI

```bash
.venv/bin/video-agent web          # then open http://127.0.0.1:8080
```

<p align="center">
  <img src="docs/media/web_template.png" alt="Template page: the reference video, its editing DNA and the numbered list of detected moves" width="860">
</p>

**Clone** a video file or link, then check the detected moves, which are numbered on the video and
in the table. Fix them in plain words, such as `remove 3 5 · 4 shake · add 2.4 punch`, and save
the template. **Apply** it to your clips and watch the render move through
*Step 1 of 2: planning the shots* and *Step 2 of 2: rendering*, with a progress bar and time left.
When it's done you get the video, the cover, the 1:1 grid crop and the match score. Type changes
such as "darker, faster cuts" to re-render, and rate the result with one tap.

Every template has a graph of the reference's sound. It is computed once at clone time and stored
in the blueprint. Click the graph to seek the video.

<p align="center">
  <img src="docs/media/web_graph.png" alt="Audio graph: moves, waveform, dB spectrogram, low/mid/high band energy and onsets with kick/snare hits, on one time axis" width="860">
</p>

The graph has these lanes, all on one time axis:
- **Moves**: the edit's moves, numbered.
- **Waveform**.
- **dB spectrogram**.
- **Low/mid/high band energy**.
- **Onsets**, with kick (blue), snare (green) and both (orange) hits.

Beat, cut and drop lines run through every lane, so you can see that the cuts follow the snare.

---

## Quick start (Ubuntu)

```bash
sudo apt install ffmpeg
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env               # fill in TELEGRAM_BOT_TOKEN and any LLM keys
```

Then:

```bash
.venv/bin/video-agent clone reference.mp4 --name car_phonk_v1          # file or link
.venv/bin/video-agent apply car_phonk_v1 clip1.mp4 clip2.mp4 --text "POV: day 1"
```

The output is written under `data/jobs/` as an H.264 master, a preview for chat, a cover image and a grid crop.

<details>
<summary><b>Database (PostgreSQL)</b></summary>

Create a role and database once (pick your own password):

```bash
sudo -u postgres psql -c "CREATE ROLE video_agent LOGIN PASSWORD 'your-password';"
sudo -u postgres createdb -O video_agent video_agent
```

Then in `.env`:

```
DATABASE_URL=postgresql+asyncpg://video_agent:your-password@localhost:5432/video_agent
```

Tables are created on first start. Without `DATABASE_URL` the agent falls back to SQLite. To bring
over data from the earlier SQLite file, run `.venv/bin/video-agent migrate-db`. Video files are
never stored in the database; they stay under `data/`.
</details>

<details>
<summary><b>Optional AI tools: RIFE slow motion, Real-ESRGAN upscaling, vision models</b></summary>

`tools/` is not in git. Without these tools the agent still works, with FFmpeg fallbacks and
simpler framing.

- **RIFE** (AI slow motion): download `rife-ncnn-vulkan-20221029-ubuntu.zip` from
  https://github.com/nihui/rife-ncnn-vulkan/releases and unzip its contents into `tools/rife/`.
- **Real-ESRGAN** (AI upscaling): download `realesrgan-ncnn-vulkan-20220424-ubuntu.zip` from
  https://github.com/xinntao/Real-ESRGAN/releases (v0.2.5.0), unzip it into `tools/realesrgan/`, then run
  `chmod +x tools/realesrgan/realesrgan-ncnn-vulkan`.
- **Vision models**: YOLOX-s (Megvii, Apache-2.0) finds the car or person in each shot, and
  CLIP ViT-B/32 (OpenAI, MIT; 8-bit ONNX) describes what each shot shows:

```bash
mkdir -p tools/models/clip
curl -L -o tools/models/yolox_s.onnx https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
H=https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main
for f in onnx/vision_model_quantized.onnx onnx/text_model_quantized.onnx tokenizer.json; do curl -L -o tools/models/clip/$(basename $f) $H/$f; done
```

RIFE and Real-ESRGAN run on the GPU through Vulkan; an Intel iGPU is enough.
</details>

---

## Three ways to use it

### Web

```bash
.venv/bin/video-agent web                 # http://127.0.0.1:8080
.venv/bin/video-agent web --port 9000     # another port
```

The site has no login. Keep it on `127.0.0.1`, or put it behind your own authentication before
using `--host 0.0.0.0`. It shows the templates of `WEB_USER_ID`. The default is 0, which is the
CLI's user; set it to your Telegram user id to see the bot's templates.

### Telegram bot

```bash
.venv/bin/video-agent bot
```

| Command | What it does |
|---|---|
| `/clone` | Send a video or link to get its editing DNA saved as a template. The reference comes back with every detected move numbered on screen. Fix the moves in plain words, for example "remove 3", "4 shake", "add 2.4 punch", "add 1.17 corner panel" or "add 4.93 cut-out" |
| `/apply` | Pick a template, send raw clips, add on-screen text, and get a 1080×1920 edit plus a cover and a 1:1 grid preview |
| *(after a render)* | Type changes such as "darker, faster cuts, more zoom, slow-mo" to re-render, and rate timing, shake, smoothness and quality with one tap each |
| `/templates` | Browse, apply, rename or delete templates |
| `/brand` | Set brand colours, a font file and past captions (your voice) |
| `/publish` | Get a caption and hashtags in your voice. Auto-posting waits for the Meta API key |
| Forward a finished video back to the bot | Reuses its template on new clips |

### Command line

```bash
.venv/bin/video-agent clone reference.mp4 --name car_phonk_v1     # or a link
.venv/bin/video-agent apply car_phonk_v1 clip1.mp4 clip2.mp4 --text "POV: day 1" --tweak "darker"
#   --blur auto|fast_only|off   motion blur like the reference / only on whips and punches / none
#   --sync-offset 0             moves exactly on the hit (default -1: one frame early, picked by ear)
#   --no-upscale                skip Real-ESRGAN (much faster on low-resolution footage)
#   --no-velocity               render velocity templates like calm edits (no speed ramps or drift)
.venv/bin/video-agent templates                              # list templates
.venv/bin/video-agent show car_phonk_v1                      # print a blueprint as JSON
.venv/bin/video-agent score reference.mp4 my_edit.mp4        # how closely an edit matches its reference
.venv/bin/video-agent caption "BMW M4 night drive"           # caption + hashtags
.venv/bin/video-agent bench                                  # ground-truth benchmark (~10 min)
.venv/bin/video-agent realtest                               # clone, re-edit and score the Reels in data/real/
```

`realtest` reads one folder per Reel from `data/real/`, which is not in git. `data/real/HOW_TO.txt` explains the layout: the Reel, plus optional raw clips or your own footage for it.

### From After Effects

```bash
.venv/bin/pip install -e ".[ae]"                                         # py-aep, MIT
.venv/bin/video-agent import-ae my_edit.aep --comp "Main" --name ae_v1   # After Effects project
.venv/bin/video-agent import-ae my_ae_comp.json --name ae_v2             # or a Bodymovin/Lottie export
.venv/bin/video-agent mogrt lower_third.mogrt                            # controls a .mogrt exposes
```

The importer reads these parts of a project:
- Nested comps.
- Keyframes, with AE's own speed/influence easing.
- Parenting and 3D camera moves.
- Time remaps, which become speed changes.
- White-solid flashes and text layers.
- Common expressions (`wiggle`, `loopOut`, `ease`, `linear`…), evaluated by a safe interpreter with no `eval`.

The rendered camera lands within 0.04 % zoom and 0.003° of the AE keyframes. It was tested on a
real Mixkit kinetic-typography template. Projects must come from AE CC 2018 or later.

---

## How it works

```
clone   video or link
          ├─ librosa .............. BPM, beats, drops, kick/snare hits, stored audio graph
          ├─ PySceneDetect + own .. cuts, incl. ones PySceneDetect misses under heavy motion
          ├─ optical flow ......... per-frame camera: zoom, rotation, pan, shake, slow-mo hints
          ├─ event detector ....... whips, punches, shakes, pushes and the hit each one sits on
          ├─ transition finder .... wheel portals, corner panels, cut-outs, beat glows
          └─ colour stats ......... grade as a 3D LUT / Lab look
                                   ─► Edit Blueprint (JSON, versioned, stored in PostgreSQL)

apply   Blueprint + your clips
          ├─ AI Director .......... watches the clips, picks motion-matched windows per shot,
          │                         face/subject-centred 9:16 crop, lists shots you're missing
          ├─ keyframe compositor .. replays the reference's camera minus your footage's own motion
          ├─ velocity engine ...... speed ramps, real smear from the footage, motion blur
          ├─ RIFE / Real-ESRGAN ... AI slow motion and upscaling (Vulkan, CPU fallbacks)
          └─ final encode ......... text, music, grade ─► H.264 High, 1080×1920, 30 fps, 16–20 Mbps
                                   + chat preview + cover + 1:1 grid crop + match score
```

### What it can re-create

| Category | Moves |
|---|---|
| Timing | Cuts on beats and snares, one frame early by default (picked by ear), drops, shot pace |
| Camera | Zoom punches, slow push-ins, rotation sway, shake jolts, whips and slides carried across cuts, pans |
| Speed | Slow motion (RIFE), speed ramps inside a shot, velocity edits that never stop moving |
| Transitions | Flash, whip, corner panel, wheel portal (CLIP finds the wheel), car cut-out (GrabCut), zoom-through, white silhouette |
| Look | Colour grade as a 3D LUT (fitted on the reference's own footage, Lab look transfer on yours), beat glows (the warm golden flash and bloom on a hit), motion blur |
| Text | On-screen text in your brand colour and font; captions and hashtags in your voice (LLM) |

### Project layout

| Module | Role |
|---|---|
| `video_agent/analysis/` | Clone: audio, cuts, camera motion, events, velocity style, transitions, glows |
| `video_agent/director.py` | Footage audit, shot assignment, subject detection |
| `video_agent/render/engine.py` | Shot pipeline: crop, speed pieces, grading, effects, text, final encode, progress |
| `video_agent/render/compositor.py` | Keyframe compositor: replays reference motion on new footage, cover zoom, motion blur |
| `video_agent/render/velocity.py` | Velocity edits: smooth in-shot speed ramps, real smear, motion calibration |
| `video_agent/render/motion.py` | Re-creates whips, punches, shakes and pushes with eased keyframes and a motion budget |
| `video_agent/render/transitions.py` | Two-shot transitions across a cut: corner panel, wheel portal, car cut-out |
| `video_agent/render/grade.py` · `glow.py` | Colour as 3D LUTs (fitted or Lab look transfer) · beat glows |
| `video_agent/render/rife.py` · `upscale.py` | RIFE slow motion · Real-ESRGAN upscaling |
| `video_agent/vision.py` | YOLOX subject detection and CLIP shot understanding (ONNX, CPU) |
| `video_agent/labels.py` · `tweak.py` | Move review in plain words · natural-language changes, logged to `Learning_Weights` |
| `video_agent/advice.py` | Filming advice: reference shots your footage can't match, and what to film |
| `video_agent/scoring.py` · `bench.py` · `realtest.py` | Match score, ground-truth benchmark, real-Reels test |
| `video_agent/ae/` | After Effects: easing and expression functions, `.aep`/Lottie import, safe expression interpreter, `.mogrt`, bloom, motion-vector blur, optical-flow retime |
| `video_agent/knowledge/` | `techniques.yaml`: editing techniques with parameters and the source each came from |
| `video_agent/web/` | Web UI (aiohttp, no extra install); `static/graph.js` draws the audio graph |
| `video_agent/bot.py` · `cli.py` | Telegram bot · command line |
| `video_agent/llm.py` | Claude, OpenRouter, Gemini and OpenAI, tried in `LLM_PROVIDERS` order with fallback |
| `video_agent/storage.py` | `Brand_Identity`, `Edit_Blueprints`, `Template_Vault`, `Published_Media_Fingerprints`, `Learning_Weights` (PostgreSQL JSONB; SQLite fallback) |
| `video_agent/meta.py` | Placeholder for every Instagram Graph API feature |

### Render tiers (replacing After Effects on Linux)

| Tier | Engine | Status |
|---|---|---|
| Standard | FFmpeg filter graphs | Working |
| Camera moves | Keyframe compositor (per-frame transforms + motion blur) | Working |
| Slow motion (AI) | RIFE, `rife-ncnn-vulkan` | Working; FFmpeg `minterpolate` fallback |
| Upscaling (AI) | Real-ESRGAN video model, 25 % original texture mixed back, ~1–2 s per frame | Working; Lanczos fallback |
| After Effects comps | `.aep` (py-aep) and Bodymovin/Lottie import | Working |
| Motion graphics, kinetic text | Remotion (React templates) | Planned |
| 3D text, camera, VFX | Blender headless | Planned |
| Real After Effects | NexRender on a remote Windows worker | Optional |

**Colour pipeline.** Every clip is converted to limited-range BT.709 at the first step. Untagged
clips are treated as BT.601, and full-range phone footage is compressed to TV range. Every
intermediate is tagged, and grading runs in 16-bit with accurate rounding. Mismatched tags were
the largest colour error the benchmark found.

---

## How it's measured

The **ground-truth benchmark** (`video-agent bench`) edits the same raw footage synthetically, so
the correct answer is known exactly:

| Case | v0.1 style | v0.1 SSIM | **Now style** | **Now SSIM** |
|---|---|---|---|---|
| Cuts on the beat + warm grade | 47.2 % | 67.0 % | **99.5 %** | **97.8 %** |
| Zoom punches | 47.7 % | 71.0 % | **97.6 %** | **96.1 %** |
| Slow push-in + rotation sway | 42.5 % | 66.6 % | **90.8 %** | **76.0 %** |
| Camera shake | 66.5 % | 73.1 % | **97.5 %** | **96.2 %** |
| Speed ramps | 67.8 % | 74.5 % | **99.8 %** | **94.4 %** |
| Flash transitions + cool grade | 36.0 % | 57.7 % | **99.5 %** | **97.5 %** |

The **real-Reels test** (`video-agent realtest`) produced the results at the top of this page.
The **test suite** (64 tests) covers the engine, the After Effects import and the web UI:

```bash
.venv/bin/python -m pytest -q
```

---

## Limits to know

- **Your footage sets the ceiling.** The agent matches cuts, timing and moves reliably. It can't
  invent camera work your clips don't have, such as orbit or FPV shots, or change the weather. It
  tells you which shots are missing and what to film.
- **Telegram file size.** Bots can download files up to 20 MB and upload up to 50 MB, so larger
  masters get a smaller preview in chat and stay whole in `data/jobs/`. A
  [local Bot API server](https://github.com/tdlib/telegram-bot-api) lifts both limits to 2 GB (set `TELEGRAM_API_BASE`).
- **Reference quality.** Downloader bots and Telegram "video" messages recompress videos, often
  to 576 px at under 0.2 Mb/s. The agent warns about this; send the original link or the video as a File.
- **Link downloads** use yt-dlp. Downloading other people's Instagram content breaks Instagram's Terms
  of Use, so use it on your own content or with permission.
- **Detection is heuristic.** Moves are guessed from the video, which is why every clone ends with a
  plain-words review step.
- **Not yet:** OCR of on-screen text in references (needs `tesseract`); Remotion/Blender tiers.
- **Waiting on the Meta API key:** OAuth and webhooks, comment-to-DM, trend ingestion, auto-publishing,
  insights, and the performance half of the learning loop. These all go through `video_agent/meta.py`.
- **The web UI is for local use only.** It has no login, and jobs are held in memory.

---

## Documentation

| File | What's in it |
|---|---|
| [CHECKLIST.md](CHECKLIST.md) | Every roadmap item, what's done and how it was measured |
| [PROBLEMS.md](PROBLEMS.md) | Open problems and what was tried |
| [docs/research.md](docs/research.md) | Related open-source projects and whether After Effects can be reverse-engineered |
| [docs/learning-sources.md](docs/learning-sources.md) | Sources the agent's techniques come from |
| [Fully Automated Instagram.md](Fully%20Automated%20Instagram.md) | The full product roadmap |
