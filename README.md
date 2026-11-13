# Video Editor Agent

Clone the editing style of any short vertical video (cuts, beat sync, speed changes, zooms, flashes,
colour grade) into a reusable **Edit Blueprint**, then apply it to your own raw clips. You can drive
it from Telegram or the command line. Full roadmap: [Fully Automated Instagram.md](Fully%20Automated%20Instagram.md).

## Setup (Ubuntu)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then fill in TELEGRAM_BOT_TOKEN and any LLM keys
```

FFmpeg must be on PATH (`sudo apt install ffmpeg`).

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
| `video_agent/render/engine.py` | Standard tier (FFmpeg): crop, speed ramps, colour matching, zoom/shake/flash, text, final encode |
| `video_agent/tweak.py` | Natural-language feedback to blueprint changes, logged to `Learning_Weights` |
| `video_agent/llm.py` | Hybrid LLM: Claude, OpenRouter, Gemini, OpenAI, tried in `LLM_PROVIDERS` order with fallback |
| `video_agent/storage.py` | `Brand_Identity`, `Edit_Blueprints`, `Template_Vault`, `Published_Media_Fingerprints`, `Learning_Weights` (SQLite by default, PostgreSQL via `DATABASE_URL`) |
| `video_agent/meta.py` | Placeholder for every Instagram Graph API feature |

### After Effects replacement (hybrid render tiers)

After Effects doesn't run on Linux, and NexRender needs Windows or macOS. The plan:

| Tier | Engine | Status |
|---|---|---|
| Standard | FFmpeg filter graphs | Working |
| Slow-mo | FFmpeg `minterpolate` optical flow | Working (CPU, slow on long shots) |
| Slow-mo (AI) | RIFE (`rife-ncnn-vulkan`, runs on Intel iGPU via Vulkan) | Next |
| Motion graphics, kinetic text | Remotion (React templates) | Planned |
| 3D text, camera, VFX | Blender headless (`blender -b -P`) | Planned |
| After Effects | NexRender on a remote Windows worker | Optional, if a subscription comes back |

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
