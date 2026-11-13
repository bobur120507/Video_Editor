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
| `/clone` | Send a video or link and get its editing DNA saved as a named template |
| `/apply` | Pick a template, send raw clips, add on-screen text, and get a 1080×1920 edit plus cover and 1:1 grid preview |
| *(after a render)* | Type changes like "darker, faster cuts, more zoom, slow-mo" to re-render |
| `/templates` | Browse templates: apply, rename, delete |
| `/brand` | Set brand colours (text colour), a font file, and past captions (your voice) |
| `/publish` | Get a caption and hashtags in your voice (auto-posting waits for the Meta key) |
| Forward a finished video back to the bot | Reuses its template on new clips |

CLI:

```bash
.venv/bin/video-agent clone reference.mp4 --name car_phonk_v1     # or a link
.venv/bin/video-agent apply car_phonk_v1 clip1.mp4 clip2.mp4 --text "POV: day 1" --tweak "darker"
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
| Motion graphics, kinetic text | Remotion (React templates) | Planned |
| 3D text, camera, VFX | Blender headless (`blender -b -P`) | Planned |
| After Effects | NexRender on a remote Windows worker | Optional, if a subscription comes back |

### Colour pipeline

Every clip is converted to limited-range BT.709 at the first step (untagged clips are treated as
BT.601, full-range phone footage is compressed to TV range), every intermediate is tagged, and
grading runs in 16-bit with accurate rounding. Mismatched tags were the biggest colour error found
by the benchmark.

### RIFE install

`tools/` is not in git. To reinstall: download `rife-ncnn-vulkan-20221029-ubuntu.zip` from
https://github.com/nihui/rife-ncnn-vulkan/releases and unzip its contents into `tools/rife/`.

## Limits to know

- **Telegram file size:** bots can download files up to 20 MB and upload up to 50 MB. Masters larger than that get a smaller preview in chat, and the full file stays in `data/jobs/`. To lift both limits to 2 GB, run a [local Bot API server](https://github.com/tdlib/telegram-bot-api) (needs `api_id`/`api_hash` from my.telegram.org) and set `TELEGRAM_API_BASE`.
- **Link downloads** use yt-dlp. Downloading from Instagram breaks its Terms of Use, so use it on your own content or with permission.
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
