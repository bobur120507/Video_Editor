# Research: related projects, learning sources, and After Effects

Researched 2026-11-24 by reading project pages, the GitHub REST API, Hugging Face pages and legal texts.
Nothing was downloaded or installed. Stars and dates move; re-check before depending on anything.

1. [Related open projects](#related-open-projects-researched-2026-11-24): what each does, licence, maturity, what to reuse
2. [Learning sources for the agent](learning-sources.md): editing techniques, datasets and papers, organised for the agent (separate file, with a machine-readable index in [`video_agent/knowledge/techniques.yaml`](../video_agent/knowledge/techniques.yaml))
3. [Can After Effects be reverse-engineered?](#can-after-effects-be-reverse-engineered)
4. [What this means for this project](#what-this-means-for-this-project)

## Related open projects (researched 2026-11-24)

Star counts, licences and last-push dates come from the GitHub REST API (`api.github.com/repos/...`) or the Hugging Face page, read on 2026-11-24. Stars are rounded. "Last push" is the repo's `pushed_at`. **[unverified]** marks claims I could not confirm on the project's own page.

Licence notes that matter for a Telegram bot or web service: **AGPL** (essentia, python-lottie) carries network-use obligations. **GPL** (color-matcher, smoothie-rs, Kdenlive, Natron, Olive) is fine to run as a separate CLI but not to import into a closed codebase. **CC BY-NC(-SA)** model weights (madmom models) are non-commercial only.

---

### 1. AI auto-editing and LLM video agents

| Project | What it is | Licence / maturity | What video_agent could reuse or learn |
|---|---|---|---|
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | CLI that cuts video automatically, mainly by audio loudness, optionally by motion (`--edit audio:threshold=…`, `motion:…`, combined with `or`). | Unlicense. ~6.1k stars, pushed today. Now written in Nim. | Its **export layer**: it writes the same edit to Premiere XML, DaVinci Resolve, Final Cut Pro, Shotcut, Kdenlive and CMX 3600 EDL. That is a good model for letting users open a cloned template in a real NLE. It also shows how to label timeline moments with numbered classes. |
| [Zulko/moviepy](https://github.com/Zulko/moviepy) | Python video editing library (compositing, effects, audio). | MIT. ~15.2k stars, last push Aug 2026. Active. | Probably already familiar. Useful as a reference for clip, composite and effect abstractions. Slower than raw FFmpeg filtergraphs for the final render. |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | FunASR transcription plus LLM-assisted clipping, with a local Gradio UI. | MIT. ~6.6k stars, pushed Nov 2026. | A compact example of "LLM picks the timestamps, FFmpeg cuts" with a web UI. Relevant to the web UI and to subtitle or lyric overlays. |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | Python library that turns long videos into clips, with transcript-based segmentation and speaker-aware resizing to 9:16. | MIT. ~650 stars, last push Jan 2024. Stale. | Its reframing logic (crop a horizontal source to 9:16 around the subject) for vertical Reels when the user's footage is 16:9. |
| [video-db/Director](https://github.com/video-db/Director) | Framework for building AI video agents (chat UI plus agent tools). | MIT. ~1.6k stars, pushed Nov 2026. | Agent and tool structure for a conversational editing front end (for example "make it more aggressive on the drop"). Tied to the VideoDB cloud. |
| [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) | Agentic framework for video understanding, editing and remaking: beat-synced edits, meme and music-video remakes, commentary. Uses a graph router over tool nodes. | MIT. ~2.1k stars, last push Jul 2026. EMNLP 2026 paper. | The intent-to-tool-graph planning pattern and the "beat-synced edit" demo. Heavy: 8 GB GPU, several API keys, and the README says Claude powers the router. The README does not name its beat library (librosa appears only in the acknowledgements). |
| [GVCLab/CutClaw](https://github.com/GVCLab/CutClaw) | Multi-agent (Screenwriter, Editor, Reviewer) **music-synchronised** editing of hours-long footage into short videos. Cuts on downbeats, pitch or mel energy. Content-aware 9:16 crop. | **No licence file** (the API reports none; a search snippet claims CC BY-NC-SA 4.0 **[unverified]**). ~1.0k stars, last push Apr 2026. | The closest agent-style relative. Worth reading: its `shot_plan_*.json` and `shot_point_*.json` plan format, its beat-segment length limits (3–5 s), and its critic loop. The licence is unclear, so read it, don't copy it. |
| LAVE ([project page](https://dgp.toronto.edu/~bryanw/lave), [arXiv 2402.10294](https://arxiv.org/abs/2402.10294)) | IUI 2024. A GPT-4 plan-and-execute agent proposes edit actions, and the user approves each one before it runs as a Python function call. | **Paper only. No code link** on the project page. | UX pattern: the agent proposes, the user approves, then the edit runs. Fits the Telegram and web flow. |
| EditDuet ([arXiv 2509.10761](https://arxiv.org/abs/2509.10761), [Adobe Research](https://research.adobe.com/publication/editduet-a-multi-agent-system-for-video-non-linear-editing)) | SIGGRAPH 2025. An Editor agent and a Critic agent do non-linear B-roll editing. | **Paper only. No public repo found.** | The critic loop: have a second pass score the rendered edit against the reference template (cut density, beat alignment) before sending it. |
| Reframe Anything / RAVA ([arXiv 2403.06070](https://arxiv.org/abs/2403.06070)) | NeurIPS 2025. An LLM agent reframes video to new aspect ratios (perception, planning, execution). | **Paper only. No repo found.** | Design reference for subject-aware 9:16 reframing. ClipsAI above is the runnable alternative. |

### 2. Beat-synced and music-video auto-cut

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [scherroman/mugen](https://github.com/scherroman/mugen) | CLI music-video generator built on MoviePy and librosa. It cuts clips to beats and can preview the cut with beeps and flashes. | MIT. ~250 stars, last push Oct 2023. Unmaintained. | The same stack as video_agent. Its **beep-and-flash preview** is a cheap way to check beat alignment before a full render. Its beat-grouping options (cut every N beats) are worth comparing. |
| CutClaw (above) | Music-synced agentic cutting. | See above. | Its downbeat and energy cut modes. |
| HKUDS/VideoAgent (above) | Beat-synced edit demo. | See above. | — |

(Smaller repos such as `kenyerman/mvmake`, with 0 stars, exist but are not worth listing.)

### 3. Edit-style transfer, template editing and transition recommendation

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| **Automatic Non-Linear Video Editing Transfer** (Frey, Chi, Yang, Essa, Google, CVPR 2021 AI4CC workshop; [arXiv 2105.06988](https://arxiv.org/abs/2105.06988)) | Extracts per-shot editing style from an edited source video (framing, content type, playback speed, lighting) and applies it to matched raw footage. Evaluated on 3,872 shots. Google also holds a related patent, "Automatic non-linear editing style transfer". | **Paper only. No code.** | Conceptually **the same problem as video_agent**. Its per-shot attribute set and footage-matching step are a checklist for the template schema. Note the patent if this ever goes commercial. |
| [yaojie-shen/AutoTransition](https://github.com/yaojie-shen/AutoTransition) plus the [HF dataset](https://huggingface.co/datasets/yaojie-shen/AutoTransition) | ECCV 2022 (ByteDance). Recommends a transition effect for each cut from visual and audio context. The dataset is annotated transitions from **online video-editing templates** (CapCut-style). | Code: **no licence**. ~70 stars, last push Mar 2025. Dataset: CC-BY-4.0, 52.9 GB WebDataset of mp4s, empty dataset card, and the HF viewer currently errors (`unexpected end of data`). No pretrained recommendation weights, only the audio Harmonic-CNN weights. | Real template-derived transition examples to **train or evaluate the transition-type classifier** in `analysis/transitions.py`, and a learned "which transition fits this cut" prior for re-applying templates to new footage. |
| [GX77/Edit3K](https://github.com/GX77/Edit3K) ([arXiv 2403.16048](https://arxiv.org/abs/2403.16048)) | 618,800 rendered videos covering **3,094 editing components** of 6 types: effects, animation, **transition**, **filter**, sticker, text. Each video applies exactly one component. Contrastive embeddings; state of the art on AutoTransition. | Repo says MIT on GitHub, but the paper says academic use **[conflict]**. 5 stars, last push Feb 2025. | Embeddings for **recognising which effect or transition or filter a reference uses**. This is the closest public resource to "classify the reference's edit components". Check what is actually released before relying on it. |
| [dawitmureja/AVE](https://github.com/dawitmureja/AVE) (Anatomy of Video Editing, ECCV 2022) | Dataset and benchmark of shot attributes from movie scenes: shot size, angle, type, camera motion, and so on. | No licence. ~60 stars, last push Nov 2022. Dormant. | Taxonomy and labels for camera-motion and shot-size classification. These would feed the camera-move part of the template. |
| [mifi/editly](https://github.com/mifi/editly) | Declarative **JSON5 edit spec**, rendered with FFmpeg and headless-gl. Clips contain layers; each layer has start and stop; transitions are named from gl-transitions; there are audio tracks, normalisation and crossfade curves. | MIT. ~5.7k stars, last push May 2025. Single maintainer, slowing. | **The best schema reference for video_agent's JSON template**: the clip, layer and transition structure, defaults, and audio-track model. A concrete example of a CapCut-like template engine in the open. |
| [AcademySoftwareFoundation/OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | Standard API and interchange format for editorial timelines, with adapters to NLE formats. | Apache-2.0. ~2.1k stars, pushed Nov 2026. Industry-backed. | Serialise the cloned edit (cuts, speed effects, transitions, markers for beats) as OTIO so it opens in Resolve, Premiere or Kdenlive. It also gives a well-tested time model (RationalTime, TimeRange) for the template. |

### 4. Shot-boundary and transition detection

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [soCzech/TransNetV2](https://github.com/soCzech/TransNetV2) | Original TensorFlow shot-boundary network and weights (the source of video_agent's numpy port). | MIT. ~1.1k stars, last push Dec 2023. Finished, not developed further. | Already used. |
| [transnetv2-pytorch (PyPI)](https://pypi.org/project/transnetv2-pytorch/) / [allenday/transnetv2_pytorch](https://github.com/allenday/transnetv2_pytorch) | PyTorch reimplementation that its author describes as giving results identical to TF. CLI and API; CPU, CUDA or MPS. | MIT. v1.0.5, Jun 2025. Whether weights ship with it is unclear **[unverified]**. | A **reference oracle** for regression-testing the numpy port's per-frame probabilities. |
| [HF: Sn4kehead/TransNetV2](https://huggingface.co/Sn4kehead/TransNetV2) | PyTorch TransNetV2 weights on Hugging Face (also loaded by NVIDIA NeMo Curator). | Apache-2.0 on the card. | A convenient weight source if the project ever drops its own checkpoint loader. |
| [wentaozhu/AutoShot](https://github.com/wentaozhu/AutoShot) | CVPR-W 2023. A NAS-found shot-boundary model and the **SHOT dataset: 853 short-form videos, 11,606 shot annotations**. The paper reports +4.2% F1 over TransNetV2 on SHOT. | MIT. ~270 stars, last push Apr 2023. Dormant. | SHOT is **short-video (Douyin/TikTok-like) ground truth**, a much better benchmark for Reels cut detection than BBC or RAI. Use it to measure the current detector, and consider AutoShot weights if they beat TransNetV2 on fast edits. |
| [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | Classical detectors: ContentDetector, AdaptiveDetector (two-pass, robust to fast camera motion), ThresholdDetector (fades), plus split-to-FFmpeg. | BSD-3. ~5.6k stars, pushed today. v0.7.3 (Nov 2026). Very active. | A cheap CPU fallback or second opinion next to TransNetV2. ThresholdDetector is suited to the **flash and fade-to-black** events the glow detector already looks for. |
| HF datasets: [SportsShot](https://huggingface.co/datasets/SportsShot/SportsShot), [it-just-works/shot-boundary-detection](https://huggingface.co/datasets/it-just-works/shot-boundary-detection) | SportsShot: 1,200 sports videos with transition start and end frames (CC BY-NC 4.0, gated). The second is an MIT WebDataset whose contents I did not confirm **[unverified]**. | — | Extra evaluation data for gradual transitions. |

### 5. Audio beats, onsets, structure and waveform UI

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [CPJKU/beat_this](https://github.com/CPJKU/beat_this) ("Beat This!", ISMIR 2024) | Neural **beat and downbeat** tracker that needs no DBN post-processing. CPU fallback. Checkpoints of about 78 MB (final) and 8 MB (small). Available on `torch.hub` and pip `beat-this`. | MIT (code and weights). ~500 stars, last push May 2026. | **The strongest drop-in upgrade over `librosa.beat.beat_track`**, and it adds downbeats, so cuts can land on bar starts and phrases. There is also an in-browser port, [HF musetric/beat-this-onnx](https://huggingface.co/musetric/beat-this-onnx) (MIT, ~190 MB ONNX, onnxruntime-web with WebGPU or wasm, needs 22.05 kHz log-mel input), which could drive the web UI's audio graph client-side. |
| [mir-aidj/all-in-one](https://github.com/mir-aidj/all-in-one) (`allin1`, WASPAA 2023) | BPM, beats, downbeats, beat positions, and **labelled segments** (intro, verse, chorus, break, bridge, inst, solo, outro). Also 100 fps activations and embeddings. | MIT. ~900 stars, last push May 2024. Quiet. Heavy dependencies: PyTorch, NATTEN, madmom (from git), Demucs. The [HF Space](https://huggingface.co/spaces/taejunkim/all-in-one) currently shows a runtime error. | Section boundaries give a principled source for **"drop" and chorus starts** and for template sections ("fast cuts in the chorus, slow-mo in the break"). The README warns that MP3 decoders can shift timing by 20–40 ms, which matters for frame-accurate beat glows. |
| [CPJKU/madmom](https://github.com/CPJKU/madmom) | Classic MIR library: RNN onsets, beats, downbeats, tempo, DBN beat tracker. | Code BSD, but **model files CC BY-NC-SA 4.0 (non-commercial)**. ~1.7k stars, last push Mar 2026. Few releases; all-in-one installs it from git. | Best-in-class onset detection (`RNNOnsetProcessor`) for kick and snare hit timing. Mind the non-commercial model licence. |
| [mjhydri/BeatNet](https://github.com/mjhydri/BeatNet) | Real-time and offline joint beat, downbeat, tempo and meter tracking (CRNN plus particle filter). | CC-BY-4.0. ~540 stars, last push Apr 2026. | Its real-time mode could give a live beat cursor in the web UI. Otherwise Beat This! is more accurate. |
| [MTG/essentia](https://github.com/MTG/essentia) | C++ audio analysis with Python bindings: rhythm, onsets, key, loudness, many pretrained TF models. | **AGPL-3.0**. ~3.8k stars, pushed Nov 2026. Very active. | Rich features (danceability, loudness/EBU R128, onset rate) for scoring. The AGPL **network clause** applies if it is shipped inside the bot or web service. |
| [katspaugh/wavesurfer.js](https://github.com/katspaugh/wavesurfer.js) | Browser waveform player with official Regions, Timeline, Minimap, Spectrogram, Hover, Envelope and Record plugins. | BSD-3. ~10.5k stars, pushed Nov 2026. Very active. | The quickest way to give the web UI an interactive audio graph: **Regions** for segments and drops, markers for beats and onsets, Spectrogram for kick and snare inspection. |
| [bbc/peaks.js](https://github.com/bbc/peaks.js) | Waveform UI with zoom view, overview, **point and segment markers**, and precomputed waveform data from `audiowaveform`. Uses Konva and waveform-data. | LGPL-3.0. ~3.4k stars, last push Oct 2026. Development moving to Codeberg. | An alternative to wavesurfer when tracks are long: precomputed `.dat` peaks render fast, and points and segments map directly onto beats and sections. |

### 6. Frame interpolation and motion blur (speed ramps, velocity edits)

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [hzwer/Practical-RIFE](https://github.com/hzwer/Practical-RIFE) | Maintained, practical RIFE models for video frame interpolation. | MIT. ~1.1k stars, last push Aug 2026. | Generate real in-between frames for **slow-motion sections of velocity ramps** instead of duplicating or blending frames. Also usable for motion-blur synthesis (interpolate, then average). |
| [nihui/rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) | RIFE as a standalone ncnn/Vulkan binary, with no PyTorch. | MIT. ~1.1k stars, last push Jan 2024. Stable but dormant. | Fits a subprocess-based FFmpeg pipeline: run it on a frame folder and get interpolated frames back, without a torch dependency on the bot server. |
| [google-research/frame-interpolation](https://github.com/google-research/frame-interpolation) (FILM) | ECCV 2022 large-motion interpolation (TensorFlow). | Apache-2.0. ~3.2k stars. **Archived** (Aug 2024). | Better than RIFE on large motion, such as fast car pans. Read-only now, so use it for comparison only. |
| [couleur-tweak-tips/smoothie-rs](https://github.com/couleur-tweak-tips/smoothie-rs) | Adds motion blur by frame blending and interpolation (RIFE, MVTools, through VapourSynth), configured as a "recipe" filter chain. Aimed at fast-paced game clips and editors. | GPL-3.0. ~170 stars, last push Apr 2026. Windows-oriented. | A reference for **blend-weight recipes** (how many frames to blend, interpolate first then blend) for velocity-edit motion blur. Reimplement the idea in OpenCV or FFmpeg (`tmix`/`minterpolate`) rather than linking the GPL code. |

### 7. Segmentation and colour (cut-out, silhouette, grade cloning)

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [facebookresearch/sam2](https://github.com/facebookresearch/sam2) | Segment Anything 2: promptable image **and video** segmentation that propagates masks across frames. | Apache-2.0. ~20.2k stars, last push May 2026. | **Temporally consistent car masks** for cut-out and silhouette transitions. Prompt once, propagate through the shot. Avoids the per-frame flicker of image-only matting. |
| [ZhengPeng7/BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | High-resolution dichotomous segmentation (sharp foreground masks). | MIT. ~4.4k stars, pushed Oct 2026. | Built for high-resolution masks with fine edges, which suits a clean car outline in silhouette glows. |
| [danielgatis/rembg](https://github.com/danielgatis/rembg) | Background-removal library and CLI wrapping several segmentation models via ONNX (BiRefNet among them **[unverified]**). | MIT. ~25.7k stars, pushed today. | The easiest CPU path for cut-outs. |
| [hahnec/color-matcher](https://github.com/hahnec/color-matcher) | Automatic colour transfer and grading: histogram matching, Reinhard, MKL, and combinations. | **GPL-3.0**. ~710 stars, last push Feb 2026. | Methods to **clone the reference's grade** onto new footage (for example MKL plus histogram matching on reference keyframes). Reimplement in numpy, since the methods are standard, if GPL is a problem. |

### 8. Open render, compositing and animation engines

| Project | What it is | Licence / maturity | Reuse |
|---|---|---|---|
| [gl-transitions/gl-transitions](https://github.com/gl-transitions/gl-transitions) | More than 100 community GLSL transitions with a tiny interface: `vec4 transition(vec2 uv)`, `getFromColor`, `getToColor`, `progress`, `ratio`. | MIT, except two BSD transitions. ~2.3k stars, pushed Nov 2026. | A **ready catalogue of transitions** (zoom, swirl, directional wipes, morphs). Each shader is simple enough to port to numpy or OpenCV, or to render through editly, Remotion, or FFmpeg builds with gl-transition support (`xfade-easing`, `ffmpeg-gl-transition`, **[not checked]**). |
| [mltframework/mlt](https://github.com/mltframework/mlt) | The multimedia framework behind Kdenlive and Shotcut: producers, filters, transitions, an XML project format, and the `melt` CLI. | LGPL-2.1. ~1.9k stars, pushed Nov 2026. Very active. | A possible **render backend** with ready-made keyframeable filters and transitions, or an export target, since MLT XML opens in Kdenlive and Shotcut. |
| [KDE/kdenlive](https://github.com/KDE/kdenlive) | Full NLE built on MLT. | GPL-3.0. ~6.2k stars, pushed today (mirror). | Export target, and a reference for how speed ramps and time remap are modelled on a timeline. |
| [NatronGitHub/Natron](https://github.com/NatronGitHub/Natron) | Node-based compositor (AE or Nuke-like), OpenFX, Python scripting. | GPL-2.0. ~5.8k stars, pushed today. | A reference for compositing ops (rotoscope, glow, motion blur nodes). Too heavy as a dependency. |
| [olive-editor/olive](https://github.com/olive-editor/olive) | Node-based NLE (0.2 rewrite). | GPL-3.0. ~9.2k stars, **last push Dec 2024**. Effectively stalled. | Little to reuse. Listed for completeness. |
| [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | Make videos programmatically with React; renders with headless Chrome and FFmpeg. | **Custom licence**: free for individuals, non-profits and companies of up to 3 people, otherwise a paid company licence. ~75k stars, pushed Nov 2026. | Template-as-code approach. Could render text and overlay layers. Check the licence before building a product on it. |
| [motion-canvas/motion-canvas](https://github.com/motion-canvas/motion-canvas) | TypeScript generator-based animation engine with an editor. | MIT. ~20.2k stars, last push Jul 2026. | Its signal and tween timing model for camera curves and easing. |
| [midrender/revideo](https://github.com/midrender/revideo) (formerly redotvideo/revideo) | Motion Canvas fork for programmatic, API-driven video rendering. | MIT. ~4.2k stars, last push Jul 2026. The repo has moved owner. | Server-side template rendering with an API, similar in spirit to video_agent's template-to-render step. |
| [airbnb/lottie-web](https://github.com/airbnb/lottie-web) | Renders After Effects (Bodymovin) animations in the browser. | MIT. ~32.6k stars, last push Sep 2025. | Preview animated text and sticker overlays in the web UI. |
| [Samsung/rlottie](https://github.com/Samsung/rlottie) | Standalone C++ Lottie player and rasteriser. | Mixed / NOASSERTION (check `COPYING`). ~1.5k stars, last push Sep 2026. | Rasterise Lottie overlays to frames server-side for FFmpeg compositing. |
| [mattbas/python-lottie](https://gitlab.com/mattbas/python-lottie) (GitLab) | Python framework to manipulate Lottie and **Telegram animated-sticker** files. | **AGPL-3.0**. 1,005 commits, 15 releases. Last activity not shown. | Telegram-sticker handling for the bot. Mind the AGPL. |

---

### Top picks for this project (ranked)

1. **Beat This! ([CPJKU/beat_this](https://github.com/CPJKU/beat_this))**: MIT, runs on CPU, gives accurate beats plus **downbeats**. Replacing or validating librosa beat tracking would tighten every beat-synced cut and glow, and downbeats enable phrase-level cut placement. The ONNX port can also drive the web audio graph in the browser.
2. **AutoShot SHOT dataset plus transnetv2-pytorch as an oracle**: SHOT is 853 annotated **short-form** videos, the right benchmark for Reels and TikTok cut detection. transnetv2-pytorch gives reference outputs to regression-test the numpy TransNetV2 port.
3. **AutoTransition dataset plus Edit3K**: the only public data and embeddings built from real **editing templates**. Use them to train or evaluate the transition-type classifier and to learn which transition fits each cut when re-applying a template. Check the dataset's integrity and Edit3K's licence first.
4. **all-in-one (`allin1`)**: labelled song sections (chorus, break and so on) plus downbeats give a principled way to detect drops and to structure templates by section. Dependencies are heavy, so it is better as an offline analysis step.
5. **SAM 2 (with BiRefNet or rembg as a fast path)**: mask propagation across frames fixes cut-out and silhouette flicker. BiRefNet gives crisp car edges. Both are permissively licensed.
6. **gl-transitions plus the editly JSON spec**: a large MIT catalogue of transitions with a trivial math interface to port, and a proven open template schema (clips, layers, transitions, audio tracks) to compare with the video_agent template.
7. **Practical-RIFE / rife-ncnn-vulkan**: real interpolated frames for slow sections of velocity ramps and for synthesising motion blur. The ncnn binary avoids a PyTorch dependency on the server.
8. **OpenTimelineIO**: export cloned edits to Resolve, Premiere or Kdenlive, and borrow its time model. auto-editor shows how much users value NLE export.

Honourable mention: **wavesurfer.js** (Regions and Spectrogram plugins) for the web audio graph. The Frey et al. Google paper, which has no code, is the closest published description of the whole "edit-style transfer" idea and is worth reading for its per-shot attribute set.


## Can After Effects be reverse-engineered?

*Research notes, checked 2026-11-24. This is not legal advice.*

### Short answer

**Taking apart the After Effects program itself (decompiling or disassembling its code): no, not for this purpose.** Adobe's terms forbid it. The narrow interoperability exceptions in EU and US law are there to let your program work with Adobe's. They do not let you copy how AE renders.

**Matching what AE produces: yes, and that is normal practice.** You can rebuild AE behaviour from:
- Adobe's published documentation (expressions, effect parameters, scripting API, SDK)
- open file formats (Lottie JSON, .aepx XML, .mogrt zip)
- community parsers of the binary .aep format
- published papers on the same techniques (optical flow, motion-vector blur, bloom)

None of this needs you to touch AE's binaries. It also fits a Python FFmpeg/OpenCV pipeline better than trying to run AE.

---

### 1. Legality

| Source | What it says (plain terms) | Link |
|---|---|---|
| **Adobe General Terms of Use, §17** ("No Modifications, Reverse Engineering, AI/ML") | You may not "modify, port, adapt, or translate" the Software. You may not reverse engineer it, and this explicitly includes "monitoring or tracking the inputs and outputs". You may not decompile or disassemble it, or try to find its source code. One carve-out: where your local law gives you a right to decompile for interoperability, *"you must first request such information from us."* §6.5 also bans using the services "for the purpose of reverse engineering". | https://www.adobe.com/legal/terms.html |
| **EU Software Directive 2009/24/EC, Art. 6** | Decompiling is allowed only when all of these hold: you are a lawful user, it is needed for **interoperability with an independently created program**, the information was not already readily available, and you touch only the parts you need. You may not use what you learn for anything else, share it except for interoperability, or use it to **build a substantially similar program**. Art. 6(3) adds a "no unreasonable prejudice" test. **Art. 8:** contract terms that override Art. 6 are "null and void". | https://www.legislation.gov.uk/eudr/2009/24/article/6 · https://www.legislation.gov.uk/eudr/2009/24/article/8 |
| **US DMCA 17 U.S.C. §1201(f)** | Someone who lawfully uses a program may get around a technical protection "for the sole purpose of identifying and analyzing" what is needed to make an **independently created program interoperate**, as long as that information wasn't readily available and the act doesn't infringe. Tools and information may be shared only for that purpose. This is an exception to the *anti-circumvention* rule. It does not override the licence contract, which still applies in the US. | https://www.law.cornell.edu/uscode/text/17/1201 |

**What this means here:**
- A video re-renderer that copies how AE effects *look* is not "interoperability" in the legal sense. It is much closer to the "substantially similar program" the EU directive excludes. Neither exception helps.
- Re-implementing from **public documentation, published papers and your own observation of output files** is generally fine. Copyright protects code, not ideas, algorithms or looks. Adobe's terms do ban "monitoring … inputs and outputs" of *its software*, so if you hold an AE licence, keep AE out of automated input/output probing. Using Lottie/.aepx/.mogrt files you or others made, and Adobe's public docs, is a different thing.
- Reading the **.aep file format** (your own project files) is a different act from decompiling the AE program. Open-source parsers do it by inspecting the files (for example with Kaitai), not AE's code. This is still worth a check with a lawyer if it becomes a product feature.
- Third-party plugins (Twixtor, RSMB, Deep Glow, Sapphire) have their own EULAs. Take the same approach: re-implement the published technique, never their code.

---

### 2. Realistic routes

| Route | What you get | Effort | Link |
|---|---|---|---|
| **AE expression reference** (Adobe and community) | Exact signatures and meaning of `wiggle(freq, amp, octaves=1, amp_mult=0.5, t=time)`, `valueAtTime`, `loopOut(type="cycle", numKeyframes=0)`, `smooth`, `linear/ease/easeIn/easeOut(t, tMin, tMax, v1, v2)`. The JS engine is ECMAScript 2018; the legacy engine is ES3. | Low | https://ae-expressions.docsforadobe.dev/objects/property/ · https://ae-expressions.docsforadobe.dev/general/interpolation/ · Adobe: https://helpx.adobe.com/after-effects/desktop/work-with-expressions/expression-language-reference/expression-language-reference.html (listed in search; returns 403 to automated fetch) |
| **AE effect reference** (helpx) | Every built-in effect by category, with parameters and bit depth/GPU support (for example Gaussian Blur in Blur & Sharpen, Glow in Stylize, Turbulent Displace in Distort). Good for matching parameter names and meaning. | Low | https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/effects-and-animation-presets/effect-list.html · https://helpx.adobe.com/after-effects/desktop/apply-effects-and-animation-presets/list-of-effects/stylize-effects.html (from search; 403 to fetch) |
| **Scripting guide** (ExtendScript/JS object model) | Project, CompItem, Layer and Property APIs. This is the vocabulary .aep parsers copy. | Low to read; needs AE to run | https://ae-scripting.docsforadobe.dev/ (community-run, based on Adobe's CS6 guide) |
| **C++ plugin SDK guide** | How AE effects/AEGPs/AEIOs plug in: effect params, SmartFX, render calls. Tells you how plugins are structured, not how Adobe's own effects are coded. | High | https://ae-plugins.docsforadobe.dev/ (SDK download: https://developer.adobe.com/after-effects/) |
| **.aep binary (RIFX) parsers** | Comps, layers, keyframes and effect params straight from .aep files, with no AE needed. **No expression evaluation.** | Medium | **py-aep** (Python, MIT, forticheprod): https://github.com/forticheprod/py-aep. The older PyPI name `aep-parser` (0.6.1) now says "renamed, use py-aep": https://pypi.org/project/aep-parser/. **boltframe/aftereffects-aep-parser** (Go, MIT; RIFX = big-endian RIFF): https://github.com/boltframe/aftereffects-aep-parser |
| **.aepx (XML project)** | AE's own XML save format (File > Save As > Save a Copy As XML). Text-editable names, paths and markers. Easier to diff than .aep, but needs AE to create. | Low to medium | https://helpx.adobe.com/after-effects/using/projects.html (from search) |
| **.mogrt** | A zip containing `definition.json` (exposed controls/metadata), `project.aegraphic` and `thumb.png` (from secondary sources; no field-level schema is published). Unzip and read the JSON. | Low (JSON); unknown (aegraphic) | https://fileinfo.com/extension/mogrt · https://helpx.adobe.com/after-effects/using/creating-motion-graphics-templates.html |
| **Lottie / Bodymovin** | Plain JSON: layers, transforms, keyframes with bezier in/out tangents, and expressions as JS that sets `$bm_rt`. Exported from AE with the Bodymovin plugin. **The best open, documented version of an AE timeline.** | Low | Spec (Lottie Animation Community, Linux Foundation): https://lottie.github.io/ → https://lottie.github.io/lottie-spec/latest/ · Readable guide: https://lottiefiles.github.io/lottie-docs/ (expressions: https://lottiefiles.github.io/lottie-docs/expressions/) · lottie-web (MIT): https://github.com/airbnb/lottie-web · docs: https://github.com/airbnb/lottie |
| **python-lottie** (`pip install lottie`) | Load/save Lottie/TGS; convert to/from SVG, GIF, WebP and MP4 (via OpenCV). **AGPL-3.0**: fine for local use, but read the licence before shipping it. | Low | https://mattbas.gitlab.io/python-lottie/ · https://gitlab.com/mattbas/python-lottie |
| **aerender** (AE's CLI renderer) | Headless rendering of an .aep comp. Requires an installed AE. Render-only nodes are set up with `ae_render_only_node.txt`. | Low if you own AE | https://helpx.adobe.com/after-effects/using/automated-rendering-network-rendering.html (from search; 403 to fetch) |
| **NexRender** (MIT) | JSON jobs → aerender. Replaces footage (`layerName`), text and properties (`property` + `value`/`expression`), with pre/post-render actions. Claims workers need no extra licence because of `ae_render_only_node.txt`, but **still needs AE installed** on each worker. | Medium | https://github.com/inlife/nexrender |
| **Templater** (Dataclay, commercial) | Data-driven AE versioning from Sheets, TSV or JSON. Runs inside AE. | Low (paid) | https://dataclay.com/templater/ |
| **Adobe Dynamic Graphics Render** (Firefly Services) | Adobe's own cloud API that renders MOGRTs from CSV/JSON rows, up to 4K60. Pricing not published on that page. | Low (paid/enterprise) | https://developer.adobe.com/audio-video-firefly-services/guides/dgr/dgr-best-practices |
| **Natron** (GPL-2.0) | Node compositor in the style of AE/Nuke. Hosts OpenFX plugins and has Python scripting. Latest stable 2.5.0 (Nov 2022). | Medium | https://natrongithub.github.io/ · https://github.com/NatronGitHub/Natron |
| **OpenFX** (BSD-3, ASWF) | Open C plugin API used by Nuke, Resolve/Fusion, Vegas, Baselight and Natron. Many open OFX plugins exist. | High | https://openeffects.org/ |
| **Blender compositor** | Glare node (Bloom, Fog Glow, Streaks, Ghosts, Simple Star, Sun Beams, Kernel), vector blur and more. Scriptable with Python. | Medium | https://docs.blender.org/manual/en/latest/compositing/types/filter/glare.html |
| **Glaxnimate** (GPL-3.0, now at KDE) | Vector motion-design editor with Lottie support and Python scripting. | Low | https://glaxnimate.org/ · https://invent.kde.org/graphics/glaxnimate/ |
| **Friction** (GPL-3.0) | Motion-graphics editor with ECMAScript expressions on parameters and SVG/SMIL export. | Low | https://friction.graphics/ · https://github.com/friction2d/friction |
| **Olive** (GPL-3.0) | NLE/compositor, alpha. The site says "Olive will return…". | n/a | https://github.com/olive-editor/olive |
| **MLT** (Kdenlive, Shotcut, Flowblade) | Filter catalogue (frei0r.glow, movit.glow/blur, avfilter.*, openfx.*). A useful list of ready-made, open equivalents. | Low | https://www.mltframework.org/ · https://www.mltframework.org/plugins/PluginsFilters/ |
| **FFmpeg filters** | All confirmed to exist: `minterpolate`, `tblend`, `tmix`, `framerate`, `dblur`, `gblur`, `boxblur`, `curves`, `lut3d`, `eq`, `colorbalance`, `zoompan`, `perspective`, `rotate`, `crop`, `scale`, `overlay`, `blend`, `xfade`, `lagfun`, `unsharp`, `vignette`, `chromashift`, `rgbashift`, `noise`, `deflicker`, `setpts` | Low | https://ffmpeg.org/ffmpeg-filters.html |
| **Motion Canvas** (MIT, TypeScript) / **Remotion** (React; free for individuals and companies of up to 3 people, paid company licence above that) | Code-first motion graphics. Useful references for keyframe/easing designs. | Medium | https://motioncanvas.io/ · https://github.com/motion-canvas/motion-canvas · https://www.remotion.dev/ |

---

### 3. Popular third-party effects: proprietary, but the techniques are public

| Effect | Vendor / status | What it does (vendor docs) | Open technique to re-implement |
|---|---|---|---|
| **Twixtor** | RE:Vision Effects, commercial; runs in AE, Resolve, Nuke, Natron and others | Optical-flow retiming: estimates per-pixel motion, then warps and interpolates new frames. https://revisionfx.com/products/twixtor/ | Dense flow: OpenCV Farneback/DIS, or **RAFT** (https://arxiv.org/abs/2003.12039, code https://github.com/princeton-vl/RAFT). Frame synthesis: **RIFE** (ECCV 2022, https://arxiv.org/abs/2011.06294, code https://github.com/megvii-research/ECCV2022-RIFE). FFmpeg `minterpolate=mi_mode=mci`. |
| **RSMB** (ReelSmart Motion Blur) | RE:Vision, commercial (trial only) | "Automatically tracking every pixel" to add motion blur; can also use 3D motion vectors. https://revisionfx.com/products/rsmb/ | Flow vectors, then blur each pixel along its vector. Screen-space reconstruction filter: McGuire et al., *A Reconstruction Filter for Plausible Motion Blur*, I3D 2012 (https://api.crossref.org/works/10.1145%2F2159616.2159639). Follow-up: https://research.nvidia.com/publication/2013-11_fast-and-stable-feature-aware-motion-blur-filter. Cheap alternatives: FFmpeg `tmix`/`tblend` on an upsampled (minterpolated) clip, or `dblur`. |
| **Deep Glow** | Plugin Everything via aescripts, commercial | "Physically accurate" glow in a non-linear colour space; v2 adds tonemapping and per-channel radius. https://aescripts.com/deep-glow (from search; 403 to fetch) | Bloom: bright-pass threshold, then layered Gaussian blurs, then **additive** blend before tonemapping (https://learnopengl.com/Advanced-Lighting/Bloom). Blender Glare "Fog Glow" is an open, more physical version. Work in linear light for the "deep" look. |
| **Saber** | Video Copilot, **free** (closed source) | Energy beams, neon and lightsabers: glow falloff, core settings, built-in distortion, Add blend. https://www.videocopilot.net/blog/2016/03/new-plug-in-saber-now-available-100-free/ | Path/mask stroke as core, then several blurs with increasing radius and falloff, then Add. Distortion = animated noise displacement. |
| **S_Shake** (Sapphire) | Boris FX, commercial (AE/OFX/AVX) | Documented parameters: Amplitude, Frequency, Phase. Per axis X/Y/Z(zoom)/Tilt: *Rand Amp/Freq*, *Wave Amp/Freq*, Phase. Seeded, repeatable randomness, with optional motion blur. https://borisfx.com/documentation/sapphire/ae/shake/ | Per-axis offset(t) = RandAmp·noise(RandFreq·Freq·t + seed) + WaveAmp·sin(2π·WaveFreq·t + phase), all × Amplitude. Apply as an affine transform, then add motion blur along the per-frame displacement. The vendor's full parameter list is a ready-made spec. |

---

### 4. What this means for the Python FFmpeg/OpenCV project

1. **Use Lottie JSON as a second template source, next to "clone from a reference Reel".**
   - It is plain JSON, so no dependency is needed (python-lottie only if AGPL is acceptable).
   - Map layer `ip`/`op` to clip in/out. Map `ks` (anchor `a`, position `p`, scale `s`, rotation `r`, opacity `o`) to existing camera/transform curves.
   - Map keyframes `{t, s, i:{x,y}, o:{x,y}}` to a **cubic-bezier evaluator in numpy**. It works like CSS `cubic-bezier`: solve x(u)=t by Newton or bisection, then return y(u).
   - This gives AE-exact easing for anything exported with Bodymovin.
2. **Re-implement the documented expression functions as numpy curve operators** (plain functions on sampled curves):
   - `linear/ease/easeIn/easeOut(t, tMin, tMax, v1, v2)`. Adobe documents only that ease "eases in and out", not the exact curve, so fit it against a known Lottie/AE export if you need exact output.
   - `valueAtTime(t)` = sample the curve at t.
   - `loopOut("cycle"|"pingpong"|"offset"|"continue", n)` = curve extension modes.
   - `wiggle(freq, amp, octaves, amp_mult, t)` = seeded fractal 1-D noise. Octave i has frequency freq·2^i and amplitude amp·amp_mult^i. AE's exact noise function is unpublished, so match statistically, not bit-for-bit.
   - Lottie stores expressions as JS (`$bm_rt = …`). Handle the common one-liners (`wiggle(…)`, `loopOut()`) with a small pattern matcher rather than embedding a JS engine.
3. **.mogrt and .aep as inputs (read-only):**
   - .mogrt: unzip, then read `definition.json` for the exposed controls (text fields, colours, sliders). This makes a clean "parameters" layer for the project's templates.
   - .aep: **py-aep** (MIT) for comp/layer/keyframe structure. Treat expression-driven values as unknown, because it has no evaluator.
   - Use files the user made or has rights to.
4. **Effect parity with FFmpeg/OpenCV.** Write each one as a documented technique, not a clone:
   - Glow/beat flash: threshold, then a multi-radius `gblur`/`cv2.GaussianBlur` pyramid, then add in linear light.
   - Velocity/retime: `setpts` plus `minterpolate`, or RIFE for quality.
   - Motion blur: flow plus directional accumulation, or `minterpolate`→`tmix`.
   - Shake: the S_Shake-style parameter model above.
   - Grade: `curves`/`lut3d` (a .cube LUT exported from any grader).
   - Push/zoom: `zoompan` or OpenCV `warpAffine`/`warpPerspective`.
   - Transitions: `xfade`.
5. **Optional, only if the user owns an AE licence: NexRender → aerender as a reference renderer.**
   - Feed a JSON job (template .aep + comp + replaced footage/text) to get a ground-truth render.
   - Compare it to the Python render with SSIM/colour stats to tune parameters.
   - Keep this to rendering your own projects. Don't build automated probing harnesses aimed at reproducing AE internals: Adobe §17 explicitly names input/output monitoring.
   - This adds a Windows/macOS + AE dependency, so keep it outside the core pipeline.
6. **Don't:** decompile AE or plugin binaries, copy shader/kernel code out of them, or ship Adobe/vendor preset files. Do: cite the public docs and papers above in code comments as the source for each implementation.

**Verification notes:**
- Adobe helpx pages return HTTP 403 to automated fetching. Those links (effect list, expression reference, aerender, projects/.aepx, mogrt) were confirmed to exist through search results only.
- The aescripts Deep Glow page also returned 403 and was confirmed through search.
- Every other link was opened and read.
- `lottiefiles.github.io/lottie-docs/aep` (listed in search) returned 404, so it is not cited.

---

## What this means for this project

**The existing approach is on the safe side of the After Effects question.** `/clone` measures finished
videos (cuts, motion, colour, sound) and re-creates the look with FFmpeg/OpenCV. That analyses the
*output of an editor*, not Adobe's software, which is what the clean-room route above recommends. Keep it
that way: cite the doc or paper each effect follows (as `render/glow.py` and `render/compositor.py`
already describe their methods), and never ship Adobe or plugin preset files or code.

**Highest-value next steps, ranked by what they fix that is measured as weak in `PROBLEMS.md`:**

| Step | Why | Cost |
|---|---|---|
| Benchmark cut detection on AutoShot's SHOT dataset (853 short-form videos) | The only public ground truth made of Reels/TikTok-style edits; today cuts are checked on the synthetic `bench` and a few real Reels (`realtest`) | Dataset download (ask first), no new runtime dependency |
| Regression-test the numpy TransNetV2 port against `transnetv2-pytorch` | Proves the port's per-frame probabilities match the original | Dev-only dependency (PyTorch) |
| Try Beat This! for beats and **downbeats** next to librosa | Downbeats let cuts land on bar starts; MIT, CPU | PyTorch dependency; ~8–78 MB checkpoint |
| Use the AutoTransition / Edit3K data to evaluate `analysis/transitions.py` | Real template transitions; our classifier is rule-based | 53 GB dataset; Edit3K licence unclear |
| SAM 2 mask propagation for cut-out and silhouette | Per-frame GrabCut flickers; SAM 2 tracks the car through the shot | PyTorch + GPU preferred |
| Read Lottie JSON as a second template source, with a numpy cubic-bezier easing evaluator | AE-exact easing curves for anything exported with Bodymovin; no AE needed | Small, pure Python |
| Export templates as OpenTimelineIO | Opens a cloned edit in Resolve/Premiere/Kdenlive | `opentimelineio` package |

**The web UI's audio graph** is drawn on a plain `<canvas>` from data stored at clone time
(`Blueprint.audio.graph`), so it needs no JS library. If it ever needs waveform editing (dragging regions,
recording), wavesurfer.js (BSD-3) with its Regions and Spectrogram plugins is the drop-in choice;
peaks.js (LGPL-3.0) suits very long tracks.

**Licence traps found:** madmom's models are non-commercial (CC BY-NC-SA); essentia and python-lottie are
AGPL (network clause applies to the bot and site); color-matcher and smoothie-rs are GPL (run as separate
tools or re-implement the published methods); Remotion needs a paid licence for companies over 3 people;
CutClaw and AutoTransition's code have no licence file (read, don't copy). A Google patent covers
"automatic non-linear editing style transfer" (Frey et al.): check it before selling this as a product.

---

## Implemented: matching After Effects' results (2026-11-24)

Code in `video_agent/ae/`, parameters with their sources in `video_agent/knowledge/techniques.yaml`, tests in
`tests/test_ae.py`. Measurement scripts were one-off (not in the repo); the numbers are below.

| Route | What it does now | Measured |
|---|---|---|
| **Expressions & easing** (`ae/curves.py`) | Lottie/CSS cubic-bezier easing; `linear/ease/easeIn/easeOut`, `wiggle`, `loopOut` (cycle, pingpong, offset, continue), `smooth`; spatial paths followed by distance | CSS `ease` at 50 % = 0.8024 (the spec's value). `ease` uses Easy Ease's bezier and `wiggle` our own Perlin noise: AE's shape and statistics, not its exact numbers (AE doesn't publish them) |
| **Lottie/Bodymovin** (`ae/lottie.py`; `video-agent import-ae`, or a `.json` on the site's Clone page) | An AE comp becomes a template: shots from the top footage layer, camera from layer transforms through parents, time remap → speed points, white solids → flashes, text layers → on-screen text, motion-blur switch → 180° shutter, `wiggle`/`loopOut` expressions evaluated | Rendered through the normal pipeline on static footage, an eased 100→130 % zoom under a rotating parent null came out within **0.04 % zoom and 0.003° rotation of AE's keyframe values on every frame**. Flash: AE's 2 full-white frames render as 1 full + 1 half (our flash fades) |
| **.mogrt** (`ae/mogrt.py`; `video-agent mogrt`) | Lists the controls in `definition.json` (text, colour, number, checkbox); text defaults can be passed to `/apply` | Only against hand-made files: Adobe publishes no schema and no real .mogrt was available |
| **.aep** (`ae/aep.py` with py-aep 0.20, MIT; `video-agent import-ae file.aep --comp NAME`, or an `.aep` on the Clone page) | The project file itself, no AE needed. Nested comps are flattened (time and transforms composed through every level), 3D cameras projected (zoom ÷ distance, pan, roll), track mattes skipped, covered words hidden, long text blocks treated as texture. Transform expressions run in a safe interpreter (`ae/expr.py`: layout rigs, `comp().layer().effect()` controls, if/else, Math, wiggle, loopOut) | A project written with py-aep imports to the same template as its Bodymovin version; AE's Easy Ease and our bezier agree within **0.013 %**. **Real project:** Mixkit "Tagline bold text" (AE CC 2019, 10 comps, 3 cameras, 12 distinct expressions, all evaluated): 6 cuts exactly on the layers' in-points, the camera slam (8.29× → 1×) rendered within 0.05 % per step, tagline captions "make" → "your / work / matter" → "Mixkit" at the right times. Bodymovin's pre-2018 helper project does not parse |
| **Bloom** (`ae/effects.py: bloom`) | Deep Glow technique: soft-knee bright-pass, four blur radii, added in linear light | Unit-tested only. Not wired into the beat glow: the project's only glow reference (the TikTok) turned out to have no beat glows (PROBLEMS.md), so there is nothing to measure a change against |
| **Motion-vector blur** (`vector_blur`) | RSMB technique: average along each pixel's flow over the shutter, linear light | Against a true 180° shutter (64 sub-frames) on a moving texture: RMSE **0.8 / 2.4** (of 255) at 8 / 24 px per frame, vs 4.1 / 23.6 unblurred; same with flow estimated by DIS. The compositor already blurs camera moves this way, so it isn't wired in |
| **Optical-flow retime** (`flow_interpolate`, `interpolate`) | Twixtor technique: DIS flow both ways, intermediate-flow warp, cross-fade where the flows disagree | Hold-out test on 4 real clips (odd frames rebuilt from even ones), mean PSNR vs FFmpeg `minterpolate`: 29.2/30.4, 37.0/36.5, 34.5/33.9, 37.2/36.9 dB; **worst frames worse on 3 of 4** (13.7–16.7 vs 19–24 dB). Not wired in as the slow-mo fallback |
| **Parametric shake** (`shake_curve`) | S_Shake's model (noise + sine per axis, amplitude, frequency, tilt, zoom, ease-out) as a CameraCurve | Unit-tested for amplitude and decay. Not wired in; the motion renderer has its own measured shake |
