# Learning sources for the video_agent editing agent

Part of the [research notes](research.md). The YAML index below is also saved as [`video_agent/knowledge/techniques.yaml`](../video_agent/knowledge/techniques.yaml).

Compiled 2026-11-24. Scope: short-form car / velocity edits (cuts, transitions, speed ramps, shake, zoom, motion blur, beat-synced glows and flashes, grading), re-rendered with FFmpeg/OpenCV.

**Verification legend**
- `[V]`: page fetched and read in this session.
- `[S]`: existence and content confirmed from search-result snippets of that exact URL. The page itself was not fetched, usually because helpx.adobe.com returns HTTP 403 to the fetcher.
- `[D]`: a value derived here from verified facts, such as a formula or a bezier equivalent. It is not quoted from the source.
- `[H]`: a practitioner heuristic for this project. Treat it as a tunable default, not a documented value.
- `[U]`: uncertain or unverified. Check before relying on it.

---

## 1. Techniques by topic

### 1.1 Camera shake (wiggle / noise)

| Source | Status | Takeaway for the agent |
|---|---|---|
| AE expression reference, Property object: https://ae-expressions.docsforadobe.dev/objects/property/ | [V] | Signature `wiggle(freq, amp, octaves=1, amp_mult=0.5, t=time)`. `freq` is wiggles per second and `amp` is in the property's units (px for position, degrees for rotation). `octaves` adds that many noise layers, each scaled by `amp_mult`. Also documents `temporalWiggle`, `smooth(width=.2, samples=5)`, `loopOut`, `valueAtTime`, `velocityAtTime` and `speedAtTime`. |
| AE expression reference, Random Numbers: https://ae-expressions.docsforadobe.dev/general/random-numbers/ | [V] | `noise(x)` is Perlin noise with output in [-1, 1], so nearby inputs give nearby outputs. That makes it the right primitive for smooth shake. `seedRandom(offset, timeless=false)` also sets the wiggle's starting value. `gaussRandom` puts about 90% of values inside the range. |
| Motionscript, Expression Speed and Frequency Control: https://docs.motionscript.com/articles/speed-control.html | [V] | Key math: to animate frequency or speed without jumps, integrate it: `A(t)=∫ s(τ)dτ`. Use a trapezoid sum over linear keyframes. To animate a wiggle's frequency, use `wiggle(1, amp, 1, .5, A(t))`. The same integral maps a speed curve to source time in velocity edits (see 1.3). |
| School of Motion, camera shake: https://www.schoolofmotion.com/blog/how-to-simulate-camera-shake-adobe-after-effects | [V] | `wiggle(1, 25)` on position gives a subtle handheld drift. To shake every layer, apply it to an adjustment layer's Transform effect. Scale the layer up so frame edges stay hidden: the needed overscale is about `1 + 2·amp/width` [D]. |
| Boris FX shake tutorial: https://borisfx.com/blog/shake-effect-after-effects-tutorial/ | [V] | Impact-shake recipe with Sapphire S_Shake. Amplitude starts at 6 and eases to 0 over about 20 frames. Frequency 15. X random amp 100, Y 150, tilt 5. Turn motion blur on. Closer keyframes give a more aggressive shake. |
| Adobe Community, wiggle on a camera: https://community.adobe.com/t5/after-effects-discussions/combining-two-wiggle-expressions/td-p/13447225 | [S] | Layer a 2D wiggle on top of a 3D camera wiggle. `wiggle(5,100)` means 5 times per second, ±100. |

Agent defaults [H]:
- Impact or beat shake: `freq` 10–15 Hz, `amp` 10–40 px at 1080 px width, rotation ±1–3°, decaying exponentially over 6–20 frames.
- Handheld drift: `freq` 0.5–2 Hz, `amp` 5–25 px.
- Implement shake as `amp(t)·noise(freq·t + seed)` per axis, with 2 octaves and amp_mult 0.5. For non-linear decay or time-varying frequency, use the integrated-phase trick from the speed-control article.

### 1.2 Easing, bezier and the graph editor

| Source | Status | Takeaway |
|---|---|---|
| AE interpolation functions: https://ae-expressions.docsforadobe.dev/general/interpolation/ | [V] | `linear/ease/easeIn/easeOut(t, tMin, tMax, v1, v2)`. Output is clamped outside [tMin, tMax]. `ease` has zero velocity at both ends. `easeIn` and `easeOut` have zero tangent on one side only. |
| W3C CSS Easing Level 1: https://www.w3.org/TR/css-easing-1/ | [V] | Canonical cubic-bezier definition. P0=(0,0) and P3=(1,1). x1 and x2 must lie in [0,1]. Input progress is x and output is y. Keywords: `ease`=(0.25,0.1,0.25,1), `ease-in`=(0.42,0,1,1), `ease-out`=(0,0,0.58,1), `ease-in-out`=(0.42,0,0.58,1). Use it to solve x→t by Newton iteration, then evaluate y. |
| AE, Apply and control speed changes: https://helpx.adobe.com/after-effects/using/speed.html | [S] | Value graph vs speed graph. Easy Ease (F9) sets speed 0 with **33.33% influence** on both sides. Influence controls how far the handle reaches toward the neighbouring keyframe. |
| AE, Keyframe interpolation: https://helpx.adobe.com/after-effects/using/keyframe-interpolation.html | [S] | Linear, Bezier, Continuous Bezier, Auto Bezier and Hold. Hold is flat in the value graph. |
| Adobe Learn, Graph Editor dynamic movement: https://helpx.adobe.com/after-effects/how-to/adjusting-keyframes-dynamic-movement.html | [S] | Workflow for ramps in the speed graph: set the keyframes, ease them, then drag the handles. |
| Motionscript, Bounce and Overshoot: https://docs.motionscript.com/articles/bounce-and-overshoot.html | [V] | Overshoot: `v0/ω · sin(ω τ) · e^(−decay τ)` with ω=2π·freq. Typical freq 3, decay 5. Decaying-sine "pop": `amp·sin(2πft)/e^(decay·t)`. Bounce: parabolic arcs with elasticity e≈0.7, g≈5000 and nMax≈9. Good for zoom-punch settle and scale pops on beats. |
| easings.net: https://easings.net/ | [V] | Catalogue of the Sine, Quad, Cubic, Quart, Quint, Expo, Circ, Back, Elastic and Bounce families, each with in, out and in-out. Only the names were verified; the page body has no formulas. |

Derived [D]:
- AE Easy Ease, with 33.33% influence and speed 0, corresponds to roughly `cubic-bezier(0.333, 0, 0.667, 1)` on a 1-D value curve.
- An influence of 80–100% gives the "hard snap" speed-graph shape typical of velocity edits. 100% is approximately `cubic-bezier(1, 0, 0, 1)`.

### 1.3 Speed ramps, velocity edits, time remapping, frame interpolation

| Source | Status | Takeaway |
|---|---|---|
| AE, Time-stretching and time-remapping: https://helpx.adobe.com/after-effects/using/time-stretching-time-remapping.html | [S] | Time Remap maps comp time to source time. Speed is shown in sec/sec: source seconds played per second of comp time. Frame blending has two modes. **Frame Mix** is cheaper and better across scene cuts. **Pixel Motion** builds motion vectors and is much better for heavy slow motion, but is hurt by motion blur in the source. Adobe recommends Timewarp for the highest-quality remapping. |
| AE, Time effects (Timewarp, Pixel Motion Blur): https://helpx.adobe.com/after-effects/using/time-effects.html | [S] | Timewarp exposes Vector Detail, Error Threshold (higher values give fewer vectors and more blending), Block Size, RGB weighting, and built-in motion blur with a manual shutter. Pixel Motion Blur has Shutter Angle (1°≈none, 720°=heavy), Shutter Samples (higher is smoother and slower) and Vector Detail (100 = one vector per pixel). |
| Premiere, Time Remapping: https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-speed/change-clip-speed-and-duration-using-time-remapping.html | [S] | Speed keyframes on a rubber band. Split a keyframe and drag the bezier handles for a ramp. Dragging to 0% gives a freeze. Speeds of about 10% and below look choppy even with Optical Flow. |
| Premiere, Time interpolation methods: https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-speed/apply-time-interpolation-methods-to-adjust-clip-speed.html | [S] | Three methods. **Frame Sampling** repeats or drops frames. **Frame Blending** repeats and blends. **Optical Flow** synthesises new frames and works best with clear motion and defined edges. |
| RE:Vision, getting the most out of Twixtor: https://revisionfx.com/faq/how_do_i_get_the_most_out_of_twixtor/ | [V] | Hard limit: keep per-frame displacement under about **5% of frame width**, for example about 35 px at 720 width. Higher source fps halves the search window. Use a short shutter, since baked-in blur breaks the vectors. Remove duplicate frames and pre-stabilise before retiming. Keyframe Motion Vector Quality between Best and None around problem areas. |
| Twixtor product page: https://revisionfx.com/products/twixtor/after-effects/ | [S] | Speed can be a constant or keyframed. Supports up to 12 tracking points and forward or inverse warping with smart blending. |
| DaVinci Resolve 21 Reference Manual: https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_21_Reference_Manual.pdf | [S]/[U] | Official manual, about 4,443 pages. The URL comes from a search snippet and the PDF was not opened. Relevant chapters: Retime Controls and Retime Curve (control points map 1:1 to speed points, with bezier handles). Retime Process can be Nearest, Frame Blend or Optical Flow. Motion Estimation runs through Standard, Enhanced and Speed Warp; Speed Warp uses the Neural Engine and is Studio-only. Older manuals are archived at https://ltbits.github.io/davinci-resolve-manuals [S]. |
| Frame.io, Mixing frame rates in Resolve, part 3: https://blog.frame.io/2019/10/17/mixed-frame-rates-part-3/ | [S] | Practical guide to Resolve's interpolation modes. |

Velocity-edit model for the agent [D]/[H]:
- Treat the edit as a speed curve `s(t)` in sec/sec. Source time is `src(t) = src0 + ∫₀ᵗ s(τ)dτ` (the speed-control integral).
- A typical velocity shape alternates slow sections (s≈0.2–0.4) on the beat with fast sections (s≈2–4) between beats.
- Make the transitions with high-influence beziers (70–100%) so speed snaps across 2–6 frames around each beat.
- Keep `s·(per-frame motion)` under the 5%-of-width limit wherever interpolated frames are needed (s<1). Otherwise use frame mixing or accept the stutter.

### 1.4 Motion blur

| Source | Status | Takeaway |
|---|---|---|
| AE, Composition basics (motion blur settings): https://helpx.adobe.com/after-effects/desktop/work-with-compositions/composition-settings/composition-basics.html | [S] | Shutter angle is in degrees and exposure derives from fps. A 90° angle at 24 fps gives 1/96 s. |
| AE, Assorted animation tools (motion blur): https://helpx.adobe.com/after-effects/using/assorted-animation-tools.html | [S] | Samples Per Frame is the minimum and Adaptive Sample Limit is the maximum. Shutter Phase = −angle/2 (for example −90 at 180°) centres the blur on the object, which matters for tracked layers. |
| Adobe Learn, Pixel Motion Blur: https://helpx.adobe.com/vn_vi/after-effects/how-to/aftereffects-pixel-motion-blur-cc.html | [S] | Optical-flow blur on footage. A 180° shutter is the filmic norm. |
| Adobe Community, CC Force Motion Blur vs Pixel Motion Blur: https://community.adobe.com/t5/after-effects/difference-between-cc-force-motion-blur-and-pixel-motion-blur/m-p/10879544 | [S] | CC Force Motion Blur samples rendered sub-frames of animated layers and suits transforms, while Pixel Motion Blur estimates flow on footage. A forum answer gives default samples of 8 for CCFMB and 5 for PMB [U]. The "Override Shutter Angle" checkbox is described in https://www.evercast.us/blog/after-effects-motion-blur [S]. |
| ReelSmart Motion Blur (RSMB): https://revisionfx.com/products/rsmb/after-effects/ | [V] | Per-pixel tracking blur with a variable Blur Amount. It can also remove blur. Pro adds a foreground matte for object separation and up to 12 track points. The page lists no numeric defaults. |

Derived [D]:
- Exposure is `(angle/360)/fps`.
- Blur length in pixels is `|v_px_per_frame| · angle/360`.
- For a synthetic transform (zoom, whip, shake), render N sub-frame samples at `t + phase + k·(angle/360)/fps/N` and average them. N=8 to 16 is enough for whips [H].
- For speed-ramped footage at speed s, the effective blur should scale with s. Fast sections need more blur and slow-motion sections need less or none, because Pixel Motion and Twixtor fail on baked-in blur.

### 1.5 Transitions

| Technique | Source | Status | Takeaway |
|---|---|---|---|
| Whip pan | Creative Cow tutorial: https://creativecow.net/?p=2451776 ; Photofocus: https://photofocus.com/software/tutorials-software/easy-whip-swish-pan-in-after-effects/ | [S] | 10-frame whip: Motion Tile/offset keyframes 5 frames before and 5 frames after the cut. Directional or box blur keyframed 0 → peak at mid → 0. The cut sits at the blur peak. |
| Whip pan in Premiere | PremiumBeat: https://www.premiumbeat.com/blog/create-whip-pan-using-built-effects-premiere-pro | [S] | Directional blur on the x axis. Add Levels to fix the dark edges the blur creates. |
| Whip pan (concept) | ProVideo Coalition: https://www.provideocoalition.com/whip-swish-pans-in-after-effects-premiere/ | [S] | Transform animation plus directional blur on the outgoing clip. |
| Zoom / crash zoom | Motion Array zoom-blur transition: https://motionarray.com/learn/after-effects/zoom-blur-transition-after-effects/ ; PremiumBeat crash zoom: https://www.premiumbeat.com/blog/create-crash-zoom-from-two-shots | [S] | Scale through the cut, with Motion Tile **Mirror Edges** (output width and height about 550% in the PremiumBeat setup) to hide borders. Add radial or zoom blur at the peak. |
| Mirror edges | Pixflow Motion Tile guide: https://pixflow.net/blog/motion-tile-effect-in-after-effects-for-seamless-tiling/ | [S] | Mirror edges make tiled overscan seamless. In OpenCV this is `cv2.BORDER_REFLECT` [D]. |
| Luma / track matte (portal, ink, silhouette reveal) | AE Track Mattes: https://helpx.adobe.com/after-effects/using/track-mattes-and-traveling-mattes.html ; Premiere Track Matte Key: https://helpx.adobe.com/ph_fil/premiere-pro/how-to/add-track-matte-key.html ; Ink transition: https://helpx.adobe.com/ph_fil/premiere-pro/how-to/ink-transition.html ; Luma mattes: https://helpx.adobe.com/mena_ar/after-effects/how-to/transparency-luma-mattes.html | [S] | In a matte, white is opaque, black is transparent and grey is partial. Alpha or Luma mode, with an optional invert. An animated matte, such as a growing shape, the subject's silhouette or an ink clip, drives the reveal. Formula: `out = A·(1−m) + B·m` [D]. |
| Cut-out / rotoscope | AE Roto Brush and Refine Matte: https://helpx.adobe.com/after-effects/using/roto-brush-refine-matte.html ; Object Matte: https://helpx.adobe.com/lu_en/after-effects/desktop/roto-brush-and-refine-matte/roto-brush/object-matte.html ; Adobe Learn Roto Brush: https://helpx.adobe.com/after-effects/how-to/roto-brush-isolate-elements.html | [S] | Foreground and background strokes produce a segmentation that propagates through the shot. Freeze locks it. Refine Matte softens edges and decontaminates colour. In this pipeline the equivalent is a segmentation mask (for example SAM-style) plus feathering, rendered as a track matte. |
| Flash / glow on beat | Deep Glow product page: https://aescripts.com/deep-glow ; Deep Glow manual (Scribd copy): https://www.scribd.com/document/504674014/Deep-Glow-Manual | [S] | Glow pipeline: threshold, then exposure (applied after the threshold, so it does not add pixels), then a blur radius of 0–2000. Large radii need more iterations. Alternatives: Sapphire Glow https://borisfx.com/documentation/sapphire/ae/glow/ and BCC Film Glow https://borisfx.com/documentation/continuum/bcc-film-glow/ [S]. |

Transition timing defaults [H]:
- Whip: 8–12 frames total, with the cut at peak blur.
- Zoom-through: 6–12 frames, scale 100%→250–400% on the outgoing clip, then 40–60%→100% on the incoming clip, with ease-in on A and ease-out on B. Mirror the borders.
- Flash: peak on the beat frame, attack 0–1 frame, exponential decay over 4–10 frames.
- Portal or silhouette: matte expansion over 6–15 frames with an ease-in-out.

### 1.6 Beat sync

| Source | Status | Takeaway |
|---|---|---|
| Motionscript, audio trigger: https://www.motionscript.com/design-guide/audio-trigger.html | [V] | Turn audio amplitude into triggers. Walk back frame by frame to the most recent rising edge where the level crosses `threshold` (example 10.0 on "Both Channels"). Set `t = time − t_edge` and drive a decaying wobble with it (amp 25, freq 5, decay 4). This is the simple onset-trigger model behind beat glows and pops. |
| AE Convert Audio to Keyframes (manual copy): https://www.manualsdir.com/manuals/753844/adobe-after-effects.html?page=233 | [S] | Creates an "Audio Amplitude" layer with Left, Right and Both Channels sliders that hold per-frame amplitude. It converts all comp audio, so mute everything else first. The keyframes do nothing until linked by an expression. |
| Premiere on iPhone, Beat Detection: https://helpx.adobe.com/premiere/mobile/audio-editing/use-beat-detection-for-music-synced-edits.html | [S] | Beat markers with density Fewer, More or All, and clips snap to them. This reflects the creator norm of cutting on a subset of beats, not every beat. |
| Premiere markers: https://helpx.adobe.com/ph_fil/premiere/desktop/organize-media/apply-labeling/add-a-marker-to-a-clip.html | [S] | Manual marker workflow (M key). |
| Prétet et al., "Is there a language of music-video clips?": https://arxiv.org/abs/2108.00970 | [V] | Editors favour co-occurrence of music and video events, including **anticipation** (the visual event lands slightly before the beat). The amount depends on genre. |

Beat-sync defaults [H]:
- Snap cuts to beats or downbeats from a beat tracker (see 3.4), not to raw amplitude peaks.
- Allow a −1 to −2 frame anticipation offset, which is consistent with the Prétet finding.
- Use downbeats for major transitions and plain beats for flashes and shakes.

### 1.7 Colour grading (LUTs, log→Rec.709, teal-orange, Lumetri)

| Source | Status | Takeaway |
|---|---|---|
| Premiere, Add Look-up Tables: https://helpx.adobe.com/premiere/desktop/correct-color/add-color-effects/add-look-up-tables.html ; Install custom LUTs: https://helpx.adobe.com/premiere/desktop/correct-color/add-color-effects/install-custom-look-up-table-files.html | [S] | Two roles. An **Input/technical LUT** goes first and normalises log footage, and lives in the Technical folder. A **Creative LUT (Look)** goes after correction, has an Intensity slider, and lives in the Creative folder. Grades export as .cube. |
| Premiere, Lumetri Color changes in 27.0: https://helpx.adobe.com/premiere/desktop/correct-color/color-mode-fundamentals/lumetri-color.html | [S] | In Premiere 27.0 the Lumetri panel is retired. Lumetri remains as an effect edited in Properties, and log and raw transforms move to a Color Management panel. |
| Premiere, Color correction workflow: https://helpx.adobe.com/lu_en/premiere/desktop/correct-color/color-correction-fundamentals/color-correction-workflow.html | [S] | Order of operations: basic correction (WB, exposure), then creative look, then secondaries. |
| Adobe, Why use orange-teal grading: https://www.adobe.com/creativecloud/video/hub/features/why-use-orange-teal-grading | [S] | Skin sits in the orange range, so push shadows and background toward teal for colour contrast. |
| ProVideo Coalition, colour-contrast looks: https://www.provideocoalition.com/creating-color-contrast-looks-final-cut-pro-x/ | [S] | The skin-tone line sits at about 11 o'clock on the vectorscope. Push the background about 180° away (blue/cyan) while holding skin on the line. |
| Frame.io, skin tones in Resolve: https://blog.frame.io/?p=13111 | [S] | Use the vectorscope skin-tone indicator to check that skin stays on the line after the look is applied. |
| DaVinci Resolve 21 manual (colour management, Color Space Transform): see 1.3 | [S]/[U] | CST OFX sets the input and output gamut plus gamma. RCM or ACES handle log→Rec.709 by tone mapping. |
| Netflix Partner Help, Color Managed Workflow in Resolve (ACES): https://backlothelp.netflix.com/hc/en-us/articles/360002088888-Color-Managed-Workflow-in-Resolve-ACES | [S] | Reference ACES setup in Resolve. |
| Sony LUT library: https://pro.sony/ue_US/support-resources | [S] | Official S-Log3/S-Gamut3.Cine look LUTs and the s709 monitoring look. Neither is a neutral technical transform. |
| .cube format (colour-science implementation): https://colour.readthedocs.io/en/develop/_modules/colour/io/luts/iridas_cube.html | [S] | Header: `LUT_3D_SIZE N` (often 33), `DOMAIN_MIN 0 0 0`, `DOMAIN_MAX 1 1 1`, then N³ rows with **red varying fastest**. Resolve ignores DOMAIN_* (FFmpeg ticket https://fftrac-bg.ffmpeg.org/ticket/10431 [S]). The original Adobe Cube spec PDF was not located [U]. |
| Academy Common LUT Format (CLF): https://docs.acescentral.com/specifications/clf | [S] | XML LUT and process-node format. Useful if the agent stores grades as an operator chain rather than a baked 3D LUT. |

Grading defaults [H]:
- Estimate the reference grade as a 3D LUT: fit 33³ per-frame histogram matching, or a parametric lift/gamma/gain plus saturation plus hue shifts.
- For teal-orange, rotate shadow hue toward about 180–200° and highlights toward about 30–40°, keeping a skin mask near the skin line.
- Apply in FFmpeg with `lut3d` (see 1.8).

### 1.8 FFmpeg / tooling references (implementation side)

| Source | Status | Takeaway |
|---|---|---|
| FFmpeg filters documentation: https://ffmpeg.org/ffmpeg-filters.html | [V] partial | The table of contents lists `lut3d`, `minterpolate`, `tmix`, `tblend`, `xfade`, `zoompan`, `setpts`, `eq`, `colorbalance`, `curves`, `gblur`, `dblur` and `vignette`. Read and verified: `colorbalance` takes rs/gs/bs, rm/gm/bm and rh/gh/bh in [−1,1] (default 0) plus `pl` to preserve lightness. `curves` takes `preset`, per-channel `x/y` point lists and `interp=natural|pchip`. `dblur` has `angle=45` and `radius=5`. Options for the other filters are [U] here; check them with `ffmpeg -h filter=<name>`. |
| PySceneDetect detectors: https://www.scenedetect.com/docs/api/detectors.html | [S] | `ContentDetector` measures a weighted HSV change (threshold about 27, min_scene_len 15). `AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15, window_width=2, min_content_val=15.0)` uses a rolling-average ratio and is more robust to fast camera motion, which matters for car edits. |
| visbeat (Visual Rhythm and Beat code): https://github.com/abedavis/visbeat ; PyPI port: https://pypi.org/project/visbeat3 | [S] | Extracts "visual beats" from motion and warps video timing to music beats. See 3.3. |

---

## 2. Datasets

| Dataset | Link | Size | Licence | Labels | Status |
|---|---|---|---|---|---|
| **ClipShots** | https://github.com/Tangshitao/ClipShots ; paper https://arxiv.org/abs/1808.04234 | 4,039 YouTube and Weibo videos (1–20 min, 20+ categories). 128,636 cuts and 38,120 gradual transitions. | MIT (repo) | Shot boundaries: hard cut vs gradual (dissolve, fade in/out, slide in/out). Train, test and an `only_gradual` split. | [V] |
| **SHOT (AutoShot)** | https://github.com/wentaozhu/AutoShot ; paper https://arxiv.org/abs/2304.06116 | 853 short-form videos with 11,606 shot annotations. Test set: 200 videos with 2,716 high-quality boundaries. | MIT (repo) | Start and end frame per shot (`kuaishou_v2.txt`). Source is Kuaishou, and it is the closest public match to Reels/TikTok cutting. | [V] |
| **BBC Planet Earth** (AImageLab) | https://zenodo.org/records/14865504 ; info https://aimagelab.ing.unimore.it/imagelab/page.asp?IdPage=12 | 11 episodes of about 50 min (about 4.8 GB of MP4). About 4,900 shots and 670 scenes. | CC BY 4.0 (Zenodo record) | Shot boundaries plus scene boundaries, scenes from 5 annotators. Known frame-offset errors in some markup (Gushchin et al. 2021). | [V] Zenodo / [S] counts |
| **RAI** (Baraldi et al.) | Paper: https://iris.unimore.it/bitstream/11380/1074331/1/2015CAIP.pdf | About 10 Rai Scuola broadcast videos (documentaries, talk shows); the count comes from snippets. | [U] | Shot boundaries. Standard SBD test set (TransNetV2 F1 93.9). | [S] / size [U] |
| **AVE: Anatomy of Video Editing** | https://github.com/dawitmureja/AVE ; paper https://arxiv.org/abs/2207.09812 | 196,176 shots from movie scenes, more than 1.5M tags. | Not stated. Clips come from the MovieClips YouTube channel and must be downloaded yourself. | Per shot: shot size, angle, type, **shot motion** (for example handheld), subject, location, number of people, sound source. Tasks: footage organisation and assisted assembly. | [V] |
| **MovieCuts** | https://github.com/PardoAlejo/MovieCuts ; paper https://arxiv.org/abs/2109.05569 | About 174k clips. | [U] (see repo) | 10 **cut types** defined by professionals, such as cutting on action, match cut, J/L cut and cut-away. Best model 47.7% mAP. | [S] |
| **Match-cut pairs (Netflix)** | Paper https://arxiv.org/abs/2210.05766 (code and embeddings on GitHub per abstract) | About 20k labelled shot pairs. | [U] | Character-framing and motion match cuts. | [S] |
| **Learning-to-Cut** | Paper https://arxiv.org/abs/2108.04294 | About 255–260k cuts and shot pairs from edited movies. | [U] | Cut plausibility (contrastive). | [S] |
| **MovieNet** | https://movienet.github.io ; paper https://arxiv.org/abs/2007.10937 | 1,100 movies. 42K scene boundaries, 92K cinematic-style tags, **46K shots with view scale and camera motion**, and trailer-to-movie shot alignment. | Not found [U] | Shot scale, camera movement, scenes, characters, trailers. | [S] |
| **CameraBench** | https://huggingface.co/datasets/syCen/CameraBench ; paper https://arxiv.org/abs/2504.15376 | About 3,000 videos, over 150K binary labels. Public test set of 1,000+ videos and a fine-tuned 7B model. | See HF card [U] | Camera-motion primitives (pan, tilt, dolly, zoom, roll and so on) defined with cinematographers. NeurIPS 2025 D&B. | [S] |
| **VEU-Bench** | https://labazh.github.io/VEU-Bench.github.io ; paper https://arxiv.org/abs/2504.17828 | 30,000 videos, 49,536 QA. | [U] | 19 tasks over 10 editing dimensions, including cut and transition type, shot attributes and judging. Useful for testing whether a VLM director can name edits. CVPR 2025. | [S] |
| **AutoTransition** | https://github.com/acherstyx/AutoTransition ; paper https://arxiv.org/abs/2207.13479 | Videos taken from online editing-platform **templates**, released as links only. | [U] | Transition category per adjacent shot pair, with audio. This is the closest public data to "template → transitions". | [S] |
| **V3C (V3C1/V3C2)** | Paper https://arxiv.org/abs/1810.04401 ; V3C1 https://catalog.data.gov/dataset/vimeo-creative-commons-collection-v3c1 | 28,450 Vimeo CC videos (about 3,800 h). V3C1 is 7,475 videos (about 1,000 h) with 1,082,659 master-shot segments. | CC videos. V3C1 requires a signed data agreement. | Master shot boundaries, keyframes, metadata (TRECVid). | [S] |
| **TAPOS** | Paper https://arxiv.org/abs/2005.10229 ; https://paperswithcode.com/dataset/tapos | 16,294 Olympic-sport instances across 21 classes (average 9.4 s). Splits 13,094 / 1,790 / 1,763. | [U] | Unlabelled sub-action boundaries within one shot. Useful for detecting motion "events" (for example a car drift peak) to place speed ramps. | [S] |
| **SportsShot** (HF) | https://huggingface.co/datasets/SportsShot/SportsShot | 1,200 sports videos. | CC BY-NC 4.0, gated | Shot segmentation and boundary ground truth. | [S] |
| **Harmonix Set** | https://github.com/urinieto/harmonixset | 912 Western pop tracks, about 56 h. | MIT (repo). The spectrograms carry their own licence you must agree to. No audio; YouTube URLs plus a DTW alignment notebook instead. | Beats with bar position (downbeat = position 1), functional segments (verse, chorus, ...), BPM, time signature. TSV plus JAMS. | [V] |
| **GTZAN-Rhythm** | https://hal.archives-ouvertes.fr/hal-01252607 ; also https://c4dm.github.io/isophonics.net/content/metrical-structure-annotations-gtzan-dataset.html | 1,000 × 30 s excerpts. | Annotations: see source [U]. GTZAN audio has known licensing and duplication issues. | Beats, downbeats, swing, tempo, meter. | [S] |
| **Ballroom / Extended Ballroom** | Original: https://mtg.upf.edu/ismir2004/contest/tempoContest/node6.html ; Extended: https://bpb-us-e1.wpmucdn.com/wp.nyu.edu/dist/2/2294/files/2016/08/marchand-extended.pdf | Original 698 × 30 s [U count]. Extended has more tracks. | [U] | Tempo, beats (Krebs et al. 2013 annotations), dance style. Do not merge with the original (audio quality differs). | [S] |
| **Beat This! annotation bundle** | https://github.com/CPJKU/beat_this_annotations ; spectrograms https://zenodo.org/records/13922116 | 16 datasets, including GTZAN, Hainsworth and Ballroom (corrected in v1.1). | Varies by dataset. Cite the originals. | Beats and downbeats. | [S] |
| **AIST++** | https://google.github.io/aistplusplus_dataset/factsfigures.html ; paper https://arxiv.org/abs/2101.08779 | 5.2 h, 1,408 sequences, 10 dance genres, multi-view video. | See site [U] | 3D SMPL motion paired with music. Defines a **Beat Alignment Score** (motion beats vs music beats), which can be reused to score cut/flash-to-beat alignment. | [S] |
| **MuseChat dataset** | https://dongzhikang.github.io/musechat | 98,206 video / music / conversation examples. | [U] | Video, original music, candidate music. | [S] |

No public Hugging Face dataset of car or velocity edit templates with per-transition labels was found. AutoTransition (template-derived) and SHOT (short-form cuts) come closest.

---

## 3. Papers

### 3.1 Shot boundary / cut detection
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| TransNet V2 (Souček & Lokoč, 2020) | https://arxiv.org/abs/2008.04838 ; code https://github.com/soCzech/TransNetV2 (MIT) | Dilated 3D-CNN SBD. F1 77.9 on ClipShots, 96.2 on BBC, 93.9 on RAI. TF and PyTorch inference. | The repo already has `analysis/transnet.py`; these are the reference numbers to regression-test against. | [V] |
| AutoShot (Zhu et al., CVPR 2023 Workshops) | https://arxiv.org/abs/2304.06116 ; code https://github.com/wentaozhu/AutoShot | NAS over 3D ConvNets and Transformers. +4.2% F1 over TransNetV2 on short-video SHOT, and +1.1, +0.9 and +1.2% on ClipShots, BBC and RAI. | Best fit for Reels/TikTok cut detection. | [V] |
| Tang et al., Fast Video Shot Transition Localization with Deep Structured Models (2018) | https://arxiv.org/abs/1808.04234 | Introduces ClipShots. Cascade of a cut detector and a gradual detector. | Dataset paper plus cut-vs-gradual baseline. | [V] |
| TransNet v1 (2019) | https://arxiv.org/abs/1906.03363 | Lightweight dilated-CNN predecessor. | Fallback. | [S] |
| Hassanien et al., Large-scale Fast and Accurate SBD (DeepSBD, 2017) | https://arxiv.org/abs/1705.03281 | Spatio-temporal CNN trained on synthetic transitions. | Recipe for generating synthetic transition training data (cuts, dissolves, wipes, fades). | [S] |
| Baraldi et al., Deep Siamese Network for Scene Detection (2015) | https://arxiv.org/abs/1510.08893 | Shot-to-scene grouping, with the BBC Planet Earth dataset. | Scene grouping. | [S] |

### 3.2 Transitions and cut semantics
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| AutoTransition (Shen et al., ECCV 2022) | https://arxiv.org/abs/2207.13479 | Video Transition Recommendation. Learns transition embeddings, then a multimodal (vision plus audio) transformer ranks transitions for each shot pair. Trained on editing templates. | Directly matches "which transition goes here"; its transition taxonomy can seed the agent's labels. | [S] |
| MovieCuts (Pardo et al., ECCV 2022) | https://arxiv.org/abs/2109.05569 | Cut-type recognition over 10 classes (audio-visual). | Taxonomy for cut semantics. | [S] |
| Learning to Cut by Watching Movies (Pardo et al., ICCV 2021) | https://arxiv.org/abs/2108.04294 | Contrastive ranking of where to cut. | Scoring candidate cut points. | [S] |
| Match Cutting (Chen et al., Netflix, WACV 2023) | https://arxiv.org/abs/2210.05766 | Retrieves shot pairs with similar framing or motion for match cuts. | Choosing source clips for seamless cut-out and silhouette transitions. | [S] |
| TVG: Training-free Transition Video Generation (Zhang et al., 2024) | https://arxiv.org/abs/2408.13413 | Video diffusion with interpolation conditioning and frequency-aware bidirectional fusion for generative transitions. | Optional generative morph transitions. | [S] |
| Versatile Transition Generation with I2V Diffusion (VTG, 2025) | https://arxiv.org/abs/2508.01698 | First/last-frame plus prompt transition generation, with the TransitBench benchmark. | Same. | [S] |
| "Transition is all you need" | none found | No paper with this exact title exists on arXiv as far as search shows. | Do not cite. | [U] not found |

### 3.3 Music ↔ video sync, beat-synced editing
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| Visual Rhythm and Beat (Davis & Agrawala, SIGGRAPH 2018 / TOG) | https://openaccess.thecvf.com/content_cvpr_2018_workshops/w49/html/Davis_Visual_Rhythm_and_CVPR_2018_paper.html ; code https://github.com/abedavis/visbeat | Defines visual beats from motion (directional changes in flow) and time-warps video so they land on music beats. | Core idea for velocity edits: align motion peaks, not just cuts, to beats. | [S] |
| AudeoSynth: Music-driven video montage (Liao et al., SIGGRAPH 2015) | https://i.cs.hku.hk/%7Eyzyu/publication/audeosynth-sig2015.pdf | Cuts clips to significant note onsets and solves montage parameters with MCMC. | Optimisation formulation for cut placement. | [S] |
| Is there a "language of music-video clips"? (Prétet, Richard, Peeters 2021) | https://arxiv.org/abs/2108.00970 | Editors co-locate music and video events, using anticipation, with genre-dependent strength. | Justifies beat snapping plus an anticipation offset. | [V] |
| Video-to-Music Recommendation using Temporal Alignment of Segments (Prétet et al., IEEE TMM 2023) | https://arxiv.org/abs/2306.07187 | Self-supervised music↔video matching with segment alignment. | Music retrieval for a template. | [S] |
| Content-based Video–Music Retrieval, VM-NET (Hong et al., 2017) | https://arxiv.org/abs/1704.06761 | Two-branch embedding of video and music. | Baseline. | [S] |
| MuseChat (Dong et al., CVPR 2024) | https://arxiv.org/abs/2310.06282 | Conversational music recommendation for videos with a Vicuna-7B reasoning module. | LLM-in-the-loop music choice. | [S] |
| V2Meow (Su et al., AAAI 2024) | https://arxiv.org/abs/2305.06594 | Video-to-music waveform generation without MIDI, with optional text style. | Only if generating music. | [S] |
| AI Choreographer / AIST++ (Li et al., ICCV 2021) | https://arxiv.org/abs/2101.08779 | Music-conditioned 3D dance, with a Beat Alignment Score. | Reuse the metric to score how well edit events align to beats. | [S] |

### 3.4 Beat / downbeat tracking
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| Beat This! (Foscarin, Schlüter, Widmer, ISMIR 2024) | https://arxiv.org/abs/2407.21658 ; code https://github.com/CPJKU/beat_this (MIT) | Rotary transformer on spectrograms with no DBN; state-of-the-art F1. API: `File2Beats(checkpoint_path="final0", dbn=False)` returns (beats, downbeats). A `small0` model is about 8 MB. Continuity metrics are weaker without the DBN. | Recommended primary beat and downbeat source. | [V] |
| madmom (Böck et al., 2016) | https://arxiv.org/abs/1605.07008 ; https://github.com/cpjku/madmom | Python MIR library covering onsets, beats, downbeats (DBN) and tempo. | Onset detection for flash triggers. Optional DBN for Beat This!. | [S] |
| BeatNet (Heydari et al., ISMIR 2021) | https://arxiv.org/abs/2108.03576 ; https://github.com/mjhydri/BeatNet | Causal CRNN plus particle filters for online beat, downbeat and meter. | Streaming or low-latency use. | [S] |

### 3.5 Frame interpolation (slow-mo inside speed ramps)
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| RIFE (Huang et al., ECCV 2022) | https://arxiv.org/abs/2011.06294 ; code megvii-research (linked from the PDF) | IFNet estimates intermediate flow directly, at an arbitrary timestep. 4–27× faster than SuperSlomo or DAIN. | Practical real-time choice for s<1 sections. | [S] |
| FILM (Reda et al., ECCV 2022) | https://arxiv.org/abs/2202.04901 ; https://film-net.github.io | Multi-scale shared-weight feature pyramid for **large motion**, with a Gram-matrix loss for disocclusions. | Better when per-frame motion is large (fast cars). Slower. | [S] |

### 3.6 Editing agents / LLM directors
| Paper | Link | One line | Relevance | Status |
|---|---|---|---|---|
| LAVE (Wang et al., IUI 2024) | https://arxiv.org/abs/2402.10294 ; https://dgp.toronto.edu/~bryanw/lave | GPT-4 agent that plans and executes edit actions over VLM-generated clip descriptions, mixed with direct manipulation. | Architecture template for the LLM director: describe footage, plan, then act. | [S] |
| Reframe Anything / RAVA (Cao et al., 2024, NeurIPS 2025) | https://arxiv.org/abs/2403.06070 | LLM agent with a perception → planning → execution loop using visual foundation models for reframing. | Pattern for 9:16 reframing and subject-centred crops. | [S] |
| EditDuet (SIGGRAPH 2025) | https://arxiv.org/abs/2509.10761 | Editor and Critic agents iterating over an NLE timeline, judged by an LLM on structure, relevance, aesthetics and pacing. | Critic loop for self-checking a cloned edit. | [S] |
| CutClaw (2026) | https://arxiv.org/abs/2603.29664 | Multi-agent long-footage-to-short-video editing with audio alignment. It criticises fixed-slot templates. | Recent; same problem space. | [S] |
| VEU-Bench / Oscars (CVPR 2025) | https://arxiv.org/abs/2504.17828 | Benchmark showing Vid-LLMs are weak at recognising edits, and a fine-tuned model that improves this. | Do not trust a VLM alone to label transitions; keep the rule-based detectors. | [S] |
| VideoAgent (Wang et al., 2024) | https://arxiv.org/abs/2403.10517 | LLM agent for long-video QA that iteratively picks frames. Understanding only, not editing. | Frame-sampling strategy for the director. | [S] |
| VideoAgent, memory-augmented (Fan et al., 2024) | https://arxiv.org/abs/2403.11481 | A different paper with the same name. Understanding, not editing. | Low. | [S] |
| "Edit Anything" (video) | none found | No arXiv paper. Only an HF LoRA card exists: https://huggingface.co/Alissonerdx/EditAnything. Related: VACE https://arxiv.org/abs/2503.07598 | Do not cite as a paper. | [U] not found |

---

## 4. How the agent should use this

1. **Detection side (analysis).** Use AutoShot or TransNetV2 for cuts. Run PySceneDetect AdaptiveDetector as a cheap second opinion on fast camera motion. Use Beat This! for beats and downbeats and madmom onsets for flash triggers. Classify transitions against the AutoTransition and MovieCuts taxonomies plus the project's own labels (portal, cut-out, zoom-through, silhouette, whip, flash).
2. **Parameter side (render).** Look up a technique entry, then fill its parameter template from measured reference values: speed curve, shake amplitude, flash decay. Fall back to the [H] defaults only when no measurement exists.
3. **Guards.** Enforce the documented limits:
   - Interpolation: per-frame motion under 5% of width, otherwise frame-mix.
   - Speed: no optical flow below about 10% speed.
   - Shutter: phase −angle/2 for tracked layers.
   - Easing: x1 and x2 in [0,1].
   - LUTs: red-fastest .cube ordering.
4. **Scoring.** Use a Beat Alignment Score (as in AIST++) between edit events (cuts, flash peaks, speed-ramp snaps) and beats, with a tolerance of about ±1 frame and an allowance for anticipation.
5. **Provenance.** Every value the agent applies should carry the `source_id` it came from, so the director can explain its choices and humans can audit them.

### Suggested machine-readable index (YAML)

A seed version is in [`video_agent/knowledge/techniques.yaml`](../video_agent/knowledge/techniques.yaml), one entry per technique. The director prompt gets only the `takeaway` and `parameters` of the entries matching the detected event type.

```yaml
- id: shake.wiggle
  topic: camera_shake
  sources:
    - url: "https://ae-expressions.docsforadobe.dev/objects/property/"
      status: verified
    - url: "https://borisfx.com/blog/shake-effect-after-effects-tutorial/"
      status: verified
  takeaway: >
    Smoothed-noise offset per axis; amplitude envelope decays after the hit.
  model: "offset(t) = env(t) * sum_k amp_mult^k * noise(2^k * freq * t + seed)"
  parameters:
    freq_hz:      {default: 12, range: [8, 15], kind: heuristic}
    amp_px_1080w: {default: 25, range: [10, 40], kind: heuristic}
    rot_deg:      {default: 2,  range: [0, 3],  kind: heuristic}
    octaves:      {default: 1,  doc_default: 1}
    amp_mult:     {default: 0.5, doc_default: 0.5}
    decay_frames: {default: 20, source: borisfx S_Shake amp 6->0 over ~20f}
  constraints: ["overscale >= 1 + 2*amp/width", "enable motion blur"]

- id: ease.easy_ease
  topic: easing
  sources: [{url: "https://helpx.adobe.com/after-effects/using/speed.html", status: snippet}]
  takeaway: Easy Ease = speed 0, influence 33.33% both sides.
  parameters:
    cubic_bezier: {value: [0.333, 0.0, 0.667, 1.0], kind: derived}

- id: speed.velocity_ramp
  topic: speed_ramp
  sources:
    - {url: "https://docs.motionscript.com/articles/speed-control.html", status: verified}
    - {url: "https://revisionfx.com/faq/how_do_i_get_the_most_out_of_twixtor/", status: verified}
  takeaway: Source time is the integral of the speed curve; snap speed at beats with high-influence beziers.
  model: "src(t) = src0 + integral_0^t s(tau) dtau"
  parameters:
    slow_speed:   {range: [0.2, 0.4], kind: heuristic}
    fast_speed:   {range: [2.0, 4.0], kind: heuristic}
    snap_frames:  {range: [2, 6],     kind: heuristic}
    influence:    {range: [0.7, 1.0], kind: heuristic}
  constraints:
    - "interpolate only if per_frame_motion_px * s < 0.05 * width (Twixtor FAQ)"
    - "avoid optical flow below 10% speed (Premiere docs)"

- id: blur.shutter
  topic: motion_blur
  sources: [{url: "https://helpx.adobe.com/after-effects/using/assorted-animation-tools.html", status: snippet}]
  model: "blur_len_px = |v_px_per_frame| * angle/360 ; sample times t + phase + k*(angle/360)/fps/N"
  parameters:
    shutter_angle: {default: 180}
    shutter_phase: {default: -90}
    samples:       {default: 12, range: [8, 16], kind: heuristic}

- id: transition.whip
  topic: transition
  sources: [{url: "https://creativecow.net/?p=2451776", status: snippet}]
  parameters:
    total_frames:  {default: 10, note: 5 before + 5 after cut}
    blur_profile:  {value: "0 -> peak at cut -> 0"}
    direction:     {from: reference_flow_angle}

- id: beat.trigger_decay
  topic: beat_sync
  sources: [{url: "https://www.motionscript.com/design-guide/audio-trigger.html", status: verified}]
  model: "t = time - last_rising_edge ; y = amp*sin(2*pi*freq*t)/exp(decay*t)"
  parameters: {amp: 25, freq: 5, decay: 4, threshold: 10.0}

- id: grade.lut_cube
  topic: color_grade
  sources: [{url: "https://colour.readthedocs.io/en/develop/_modules/colour/io/luts/iridas_cube.html", status: snippet}]
  parameters:
    lut_size: {default: 33}
    order: {value: red_fastest}
    domain: {min: [0,0,0], max: [1,1,1], note: "Resolve ignores DOMAIN_*"}
```

Implementation hint: keep `status` (verified / snippet / derived / heuristic / uncertain) as a field. Then the rule code can refuse to treat `heuristic` values as hard constraints, and the director can say how confident it is in each choice.
