# Video Editor Agent

Clone the editing style of any short vertical video (cuts, beat sync, speed changes, zooms, flashes,
colour grade) into a reusable **Edit Blueprint**, then apply it to your own raw clips. You can drive
it from Telegram or the command line. Full roadmap: [Fully Automated Instagram.md](Fully%20Automated%20Instagram.md).
What's done and what's left: [CHECKLIST.md](CHECKLIST.md).

## Setup (Ubuntu)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then fill in TELEGRAM_BOT_TOKEN and any LLM keys
```

FFmpeg must be on PATH (`sudo apt install ffmpeg`).

### Database (PostgreSQL)

Create a role and database once (pick your own password):

```bash
sudo -u postgres psql -c "CREATE ROLE video_agent LOGIN PASSWORD 'your-password';"
sudo -u postgres createdb -O video_agent video_agent
```

Then in `.env`:

```
DATABASE_URL=postgresql+asyncpg://video_agent:your-password@localhost:5432/video_agent
```

Tables are created on first start. To bring over data from the earlier SQLite file: `.venv/bin/video-agent migrate-db`.
Video files are never stored in the database; they stay under `data/`.

## Use it

Telegram:

```bash
.venv/bin/video-agent bot
```

| Command | What it does |
|---|---|
| `/clone` | Send a video or link and get its editing DNA saved as a named template. You get the reference back with every detected move numbered on screen, and fix them in plain words ("remove 3", "4 shake", "add 2.4 punch", "add 1.17 corner panel", "add 4.93 cut-out") |
| `/apply` | Pick a template, send raw clips, add on-screen text, and get a 1080×1920 edit plus cover and 1:1 grid preview |
| *(after a render)* | Type changes like "darker, faster cuts, more zoom, slow-mo" to re-render, and rate timing, shake, smoothness and quality with one tap each |
| `/templates` | Browse templates: apply, rename, delete |
| `/brand` | Set brand colours (text colour), a font file, and past captions (your voice) |
| `/publish` | Get a caption and hashtags in your voice (auto-posting waits for the Meta key) |
| Forward a finished video back to the bot | Reuses its template on new clips |

CLI:

```bash
.venv/bin/video-agent clone reference.mp4 --name car_phonk_v1     # or a link
.venv/bin/video-agent apply car_phonk_v1 clip1.mp4 clip2.mp4 --text "POV: day 1" --tweak "darker"
#   --blur auto|fast_only|off   motion blur like the reference / only on whips and punches / none
#   --sync-offset 0             moves exactly on the hit (default -1: one frame early, picked by ear)
#   --no-upscale                skip Real-ESRGAN (faster renders of low-resolution footage)
#   --no-velocity               velocity templates rendered like calm edits (no speed ramps / drift)
.venv/bin/video-agent templates
.venv/bin/video-agent caption "BMW M4 night drive"
.venv/bin/video-agent score reference.mp4 my_edit.mp4        # how closely an edit matches its reference
.venv/bin/video-agent bench                                 # ground-truth match benchmark (~10 min)
.venv/bin/video-agent realtest                              # clone, re-edit and score real Reels in data/real/ (see data/real/HOW_TO.txt)
```

## How it works

```
clone:  video ─► librosa (BPM, beats, drops) + PySceneDetect (cuts) + optical flow
               (motion, zoom punches, shake, slow-mo hints) + colour stats  ─► Blueprint JSON

apply:  Blueprint + clips ─► AI Director (motion-matched clip windows, face-centred 9:16 crop,
               missing-shot list) ─► render tier ─► H.264 High / yuv420p / AAC 128k 48 kHz /
               1080×1920 / 30 fps / 16–20 Mbps master + Telegram preview + cover + grid crop
```

| Module | Role |
|---|---|
| `video_agent/analysis/` | `/clone` deconstruction |
| `video_agent/director.py` | Footage audit, shot assignment, subject detection |
| `video_agent/render/engine.py` | Shot pipeline: crop, speed pieces, grading, effects, text, final encode |
| `video_agent/analysis/camera.py` | Per-frame camera motion of every reference shot (zoom, rotation, pan, shake) |
| `video_agent/render/compositor.py` | Keyframe compositor: replays that motion on new footage (minus the footage's own motion), cover zoom, motion blur |
| `video_agent/render/grade.py` | Colour as 3D LUTs: fitted from aligned frames when the footage is the reference's own, Lab look transfer otherwise |
| `video_agent/render/rife.py` | RIFE AI frame interpolation for slow motion (Vulkan, runs on the Intel GPU) |
| `video_agent/render/upscale.py` | Real-ESRGAN AI upscaling of footage smaller than the output (Vulkan, Intel GPU) |
| `video_agent/advice.py` | Filming advice: reference shots your footage can't match, and what to film |
| `video_agent/labels.py` | Move review: annotated reference preview and plain-word corrections |
| `video_agent/analysis/velocity.py` | Edit style (velocity vs calm) and each shot's motion wave |
| `video_agent/render/velocity.py` | Velocity edits: smooth in-shot speed ramps, real smear from the footage, motion calibration |
| `video_agent/analysis/transitions.py` | Finds wheel-portal transitions in a reference (a circle that keeps shrinking around a cut) |
| `video_agent/render/transitions.py` | Two-shot transitions across a cut: corner panel, wheel portal (CLIP finds the wheel), car cut-out (GrabCut) |
| `video_agent/analysis/events.py` | Edit moves per shot (whips, punches, shake jolts, push) and the drum hits they sit on, timed at each sound's attack and typed kick/snare |
| `video_agent/render/motion.py` | Re-creates those moves on new footage with eased keyframes, whips carried across cuts, and a motion budget |
| `video_agent/vision.py` | YOLOX subject detection and CLIP shot understanding (ONNX, CPU) |
| `video_agent/scoring.py` / `bench.py` | Match score and the ground-truth benchmark |
| `video_agent/tweak.py` | Natural-language feedback to blueprint changes, logged to `Learning_Weights` |
| `video_agent/llm.py` | Hybrid LLM: Claude, OpenRouter, Gemini, OpenAI, tried in `LLM_PROVIDERS` order with fallback |
| `video_agent/storage.py` | `Brand_Identity`, `Edit_Blueprints`, `Template_Vault`, `Published_Media_Fingerprints`, `Learning_Weights` (PostgreSQL with JSONB; SQLite fallback) |
| `video_agent/meta.py` | Placeholder for every Instagram Graph API feature |

### After Effects replacement (hybrid render tiers)

After Effects doesn't run on Linux, and NexRender needs Windows or macOS. The plan:

| Tier | Engine | Status |
|---|---|---|
| Standard | FFmpeg filter graphs | Working |
| Camera moves | Keyframe compositor (per-frame transforms + motion blur) | Working |
| Slow-mo (AI) | RIFE (`rife-ncnn-vulkan` in `tools/rife/`, Intel iGPU via Vulkan) | Working; FFmpeg `minterpolate` is the fallback |
| Upscaling (AI) | Real-ESRGAN (`realesrgan-ncnn-vulkan` in `tools/realesrgan/`, video model, 25% original texture mixed back) | Working; ~1–2 s per frame; Lanczos is the fallback |
| Motion graphics, kinetic text | Remotion (React templates) | Planned |
| 3D text, camera, VFX | Blender headless (`blender -b -P`) | Planned |
| After Effects | NexRender on a remote Windows worker | Optional, if a subscription comes back |

### Colour pipeline

Every clip is converted to limited-range BT.709 at the first step (untagged clips are treated as
BT.601, full-range phone footage is compressed to TV range), every intermediate is tagged, and
grading runs in 16-bit with accurate rounding. Mismatched tags were the biggest colour error found
by the benchmark.

### Vision models

`tools/models/` (not in git) holds the subject detector and the shot-understanding model:

```bash
mkdir -p tools/models/clip
curl -L -o tools/models/yolox_s.onnx https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
H=https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main
for f in onnx/vision_model_quantized.onnx onnx/text_model_quantized.onnx tokenizer.json; do curl -L -o tools/models/clip/$(basename $f) $H/$f; done
```

YOLOX-s (Megvii, Apache-2.0) finds the car/person in each shot; CLIP ViT-B/32 (OpenAI, MIT; 8-bit ONNX
export) describes what each shot shows. Without them the agent still works, with simpler framing and matching.

### RIFE and Real-ESRGAN install

`tools/` is not in git. To reinstall:

- RIFE: download `rife-ncnn-vulkan-20221029-ubuntu.zip` from https://github.com/nihui/rife-ncnn-vulkan/releases and unzip its contents into `tools/rife/`.
- Real-ESRGAN: download `realesrgan-ncnn-vulkan-20220424-ubuntu.zip` from https://github.com/xinntao/Real-ESRGAN/releases (v0.2.5.0), unzip it into `tools/realesrgan/` and `chmod +x tools/realesrgan/realesrgan-ncnn-vulkan`.

## Limits to know

- **Telegram file size:** bots can download files up to 20 MB and upload up to 50 MB. Masters larger than that get a smaller preview in chat, and the full file stays in `data/jobs/`. To lift both limits to 2 GB, run a [local Bot API server](https://github.com/tdlib/telegram-bot-api) (needs `api_id`/`api_hash` from my.telegram.org) and set `TELEGRAM_API_BASE`.
- **Link downloads** use yt-dlp. Downloading from Instagram breaks its Terms of Use, so use it on your own content or with permission.
- **Reference quality:** downloader bots and Telegram "video" messages recompress videos (576 px at under 0.2 Mb/s is common). The bot warns when a reference looks recompressed; paste the original link, or send the video as a File.
- **Detection is heuristic:** speed changes and effects are guessed from the reference video. Tweak them by feedback after the first render.
- **OCR** of on-screen text in reference videos is not implemented yet (it needs `tesseract`). Text comes from you or the LLM.

## Waiting on the Meta API key

OAuth and webhooks, comment-to-DM, `/analyze` trend ingestion, auto-publishing, `/audit` insights,
`/solve`, and the performance half of the learning loop. They all go through `video_agent/meta.py`,
which currently replies "coming soon".

## Tests

```bash
.venv/bin/python -m pytest -q
```
