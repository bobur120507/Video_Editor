# Problems and questions

Diagnosis from 2026-11-14. Nothing was re-rendered. Every number comes from measuring the edits
already on disk: the TikTok reference, `crazy_edit/_results/mclaren_final.mp4` and the self-rebuilds.

---

## Part 1: What is wrong, measured

### P1. Moves land late, and the lateness is inconsistent

You saw this as "a tiny delay before and after the beat".

| | TikTok (pro) | Ours (McLaren) |
|---|---|---|
| Motion peaks near a music hit, median offset | −49 ms (lands *on or before* the sound) | **+25 ms (late)** |
| Average distance from the hit | 82 ms | 76 ms, scattered both ways (10 early, 15 late, 2 on) |
| Cuts vs the start of the drum sound | +9 ms | +19 ms |

**Causes in the code:**
1. **Our "hit" times are about 46 ms (1.4 frames) late.**
   - `detect_hits` takes the *peak* of librosa's onset envelope. The kick or snare actually starts 46 ms earlier (measured with backtracking).
   - The pro editor cuts within 9 ms of the drum's *start*. Every move we sync is late by about 1.5 frames.
2. **Snapping is loose.** `SNAP = 0.1` s lets a move sit up to 3 frames off a hit, and moves with no hit nearby keep their raw times, so some land early and some late.
3. **Rounding to frames adds error.**
   - Punches start at `f >= f0`, so they begin on the next frame (up to 33 ms late).
   - Shake start times like 0.035 and 0.067 s are not frame-aligned.
4. **The shake's first jolt is not on the hit.** A shake is a sine wave with a 2-frame fade-in and a random phase, so its biggest jolt can come anywhere after the hit.
5. **The beat grid is wrong for this song.**
   - We found 12 beats in 10 s ("95.7 BPM"), and the pro's cuts sit 235 ms off that grid.
   - The editor follows drum hits, not our beat grid. Only the hits are usable today.

### P2. Too much shaking

| | TikTok (pro) | Ours |
|---|---|---|
| Distinct motion peaks in 10 s | 20 | **35** (+75%) |
| Back-and-forth direction flips per frame | 0.29 | **0.39** (+34%) |
| Roughness (jerk ÷ motion) | 0.75 | **1.02** (+36%) |

**Causes:**
1. **A shake is one long, constant wobble.**
   - A detected "shake" becomes a continuous 9–13 Hz sine wave for the *whole shot*, up to 1.87 s.
   - Pros do a short jolt on the hit that dies within a few frames, then the picture rests.
2. **Shake detection gives false positives.**
   - 7 of 13 shots were labelled "shake".
   - The detector (`SHAKE_JITTER` on second differences of position) also fires on the reference camera's flying/orbit movement and on tracking noise in blurred frames.
   - So we add shake the editor never added.
3. **Moves pile up.** Push, punch, shake and whip are added on top of each other with no limit, so a shot can have all four at once.

### P3. No smoothness or connection between shots

You described your reference edits as smooth and connected; ours are not. Each shot's moves are built in isolation (`render/motion.py`):
- **No easing.**
  - Pros keyframe with ease-in/ease-out curves and often a small overshoot.
  - Our punches jump to full zoom in one frame. Whips are fixed 3-frame squared ramps.
- **No hand-off between shots.**
  - The direction of a whip-in is hard-coded (`sign = -1.0`), not taken from the previous shot's whip-out.
  - Speed and direction do not carry across the cut, so every cut "resets" the motion.
- **No rhythm plan.**
  - Nothing decides the overall flow: calm on verses, hits on the drop, a breather after a big move.
  - Every event is reproduced with the same weight.
- **Whips read as glitches.** Our render shows 16 cut-like jumps where the reference has 13. Some synthesized whips (2.0 s, 4.07 s, 6.5 s, 6.93 s) look like extra cuts, not transitions.

### P4. Quality loss

1. **reel_2: the reference itself is low quality.**
   - All three reference files are only **576 px wide**. reel_2 is 576×576 at **187 kb/s**, which is very low; Instagram usually serves 720–1080 px.
   - A self-rebuild uses the reference as its footage, so it can never be sharper than that file.
   - Upscaling it to 1080 px just makes the softness bigger. A side-by-side crop of our frame and the reference frame looks identical.
   - **Our pipeline did not lose quality here; the input did.**
2. **McLaren: motion blur is too heavy.**
   - The blur strength was copied from the TikTok, so every synthesized move gets full (360°) motion blur, *including the shakes*.
   - 76% of output frames are smeared, against only 4% in the raw footage. The TikTok itself is 63%, but its blur comes from real fast camera moves; ours smears static shots.
3. **Two resampling passes.**
   - Footage is upscaled first (Lanczos), then warped again (bilinear) for camera moves.
   - Reframing can zoom up to 2×. On 720p footage that is an effective 2.7× enlargement, which looks soft.
4. **No AI upscaler or detail recovery.**

### P5. The scorer said "good" when you said "bad"

- `music_sync` gave the McLaren edit 95%, but you could see the timing was off.
- Why it misses:
  - Its tolerance (±2 frames) is looser than the ±1 frame a viewer feels.
  - It measures against our late hit times, so it inherits P1.
  - Nothing in the score measures *too much shake*, *roughness*, *connection between shots* or *sharpness*.
- **Until the scorer catches what you see, every improvement is a guess.** This is a system problem, not an edit problem.

### P6. System-level problems (beyond the edit)

| Problem | Effect |
|---|---|
| The only real "reference + its own raw footage" case is crazy_edit, and its footage is one 15 s clip | Nothing to learn or test against; footage gets reused |
| No human feedback is stored | The system cannot learn your taste; the learning loop (CHECKLIST F) is empty |
| The Telegram Bot API downloads at most 20 MB, and Telegram compresses videos sent as "video" | Bot users will send low-quality references, like P4.1 |
| The benchmark is synthetic | High benchmark scores (97.5%) did not mean good real edits |
| Edit events are guessed from the *finished* reference video | We cannot tell editor moves from camera moves with certainty (P2.2) |

---

## Part 2: What I can fix myself (no answers needed)

1. Time hits from the drum's **attack start**, not the envelope peak. Snap every move to the exact frame, within ±1 frame.
2. Replace the shake model with **impulse jolts on hits** that decay within a few frames. Only real jitter between hits counts as shake; no sine wave across the whole shot.
3. Add **eased keyframes** (ease-in/out with a small overshoot) to punches, whips and pushes.
4. Make each whip-in **continue the previous shot's whip-out** in direction and speed.
5. Add a **motion budget** per shot so moves never pile up.
6. Apply motion blur **only on fast moves** (whips and punches), never on shakes or calm frames.
7. Combine framing and camera warp into **one high-quality resample**.
8. **New scores:**
   - timing in milliseconds against attack starts
   - shake amount and roughness compared with the reference
   - sharpness kept
   - cut-like glitches

   I'll check these against your verdicts (good or bad) before trusting them.

---

## Part 3: Questions I need you for

**Q1 — Timing feel.**
Should a move land *exactly* on the drum's first frame, or **1 frame before** it? Some editors put the cut one frame early because it feels tighter. I can make a short 3-version test clip (−1 / 0 / +1 frame) for you to pick by ear and eye. Only if you say yes; no new edits otherwise.

**Q2 — Which sounds drive the moves?**
In the TikTok, do the shakes and punches follow the **kick**, the **snare/clap**, the **808 bass**, **vocal accents**, or a mix? And does every hit get a move, or only the strong ones? You hear the "why" behind the edit; I only see where the motion is.

**Q3 — Label one reference (the most valuable thing you can give).**
Watch the TikTok and write down the moments you see a move, for example:
```
1.17 whip
1.60 shake (short)
3.0-4.9 shake (long, bass)
4.07 zoom punch
```
The kind and rough time are enough. This tells me which of my 7 "shake" shots are real (P2.2), and it gives the scorer a true answer to measure against.

**Q4 — Where did the reference files come from?**
They are 576 px and very low bitrate.
- Were they saved from Instagram/TikTok with an app or website, or sent through Telegram?
- Can you get the original-quality versions? In Telegram, sending *as File* keeps the quality.

Also, for reel_2: is the quality loss you saw **compared with the reference on your phone**, or compared with what a good Reel should look like? If the second, only better input or an AI upscaler can fix it.

**Q5 — Motion blur: style or damage?**
The TikTok has blur on 63% of its frames; it is part of that "crazy" look.
- Do you want blur **only on whips/fast moves** (crisp shakes and calm frames)?
- Or full blur like the TikTok?
- Or should it depend on the reference?

**Q6 — AI upscaler.**
May I download **Real-ESRGAN (ncnn-vulkan, about 50 MB, same type of tool as RIFE)**? It would upscale 576/720 p material to 1080 p with recovered detail, and runs on your Intel GPU. If not, should output stay at the source's resolution instead of being blown up?

**Q7 — What "connected" means to you.**
Point me to **2–3 timestamps** in reel_1 or reel_2 where the flow between shots feels best. For example:
- the motion carries over the cut
- the next shot starts moving the same way
- the speed slows into a beat

I'll build exactly that behaviour instead of guessing.

**Q8 — A target we know is right.**
The only way to measure "beats a mid editor" is to compare with a real human edit of the **same footage**. Could you (or an editor friend) make one short edit of the McLaren footage in CapCut on your phone, in the TikTok's style? Then we have a true target: ours against a human's, same clips, same song.

**Q9 — A golden test set.**
Can you collect **5–10 reference edits in the style you want to beat**, in original quality? Ideally each comes with raw footage that is similar to (or the same as) what the editor used. Today we have one usable pair (crazy_edit), which is too few to tune on without overfitting to that one video.

**Q10 — Rating outputs.**
Would you rate each output in the Telegram bot with quick buttons, for example:
```
timing 👍/👎
shake too much / ok / too little
smooth 👍/👎
quality 👍/👎
```
Those ratings would train the scorer to see what you see (P5) and start the learning loop.

**Q11 — When the footage cannot match.**
The TikTok was shot with a flying/orbiting camera; your McLaren clip is mostly static or handheld. When the footage cannot do what the reference does, which should the agent do?
- (a) Fake the move digitally, as now.
- (b) Pick a different, calmer style.
- (c) Tell you what to film. For example: "film a slow orbit around the car, 3 s, low angle."
- (d) Mix these.

**Q12 — Priority order.**
Rank these so I fix in the order you care about:
1. timing on the beat
2. shake amount and feel
3. smoothness between shots
4. picture quality
5. choosing the right footage moments

**Q13 — Music.**
Will the finished posts always use the **reference's song**, or your own chosen music? Your own music means re-timing the whole edit to a new song (a big feature), and it also affects copyright on Instagram.

---

## Part 4: Your answers and what was done (2026-11-14)

Your priority order: **1 picture quality → 2 footage moments → 3 smoothness → 4 shake → 5 timing.**

| # | Your answer | Done |
|---|---|---|
| Q1 | Show −1 / 0 / +1 | Blind test clips A, B, C in `data/real/crazy_edit/_results/timing_test/`. **You picked A = −1 frame**, so every edit (bot and CLI) now starts beat-synced moves one frame early. `--sync-offset 0` puts them exactly on the hit |
| Q2 | What do pros follow? | See "Q2 answer" below. Hits are now typed kick / snare, and the cloner records which one the editor cut on (`sync_to`) |
| Q3 | Show detected moves and let users fix them | Built: after `/clone`, the bot sends the reference with every move numbered on screen plus a list; fix in plain words ("remove 3", "4 shake", "add 2.4 punch") or tap ✅. Every review is stored as ground truth (`move_labels` table) |
| Q4 | From a Telegram TikTok downloader bot | That is why references were 576 px at 0.19 Mb/s. Clone now warns on recompressed references and asks for the link or a File; paste links to our bot (yt-dlp picks the best quality) |
| Q5 | Follow the reference; else blur only on fast moves | Built: `auto` blurs exactly where the reference frames are blurred; `fast_only` blurs whips and punch attacks only; `off` |
| Q6 | Download Real-ESRGAN | Installed (video model on the Intel GPU, ~1 s per 576p frame) and used for any footage smaller than the output; 25% of the original texture is mixed back so skin does not go waxy. Comparison: `data/real/reel_2/_results/upscale_compare.png` |
| Q7 | Smooth transitions between moves | Eased keyframes for every move; a whip-out now continues into the next shot's whip-in (same direction and speed, or a lighter carried-over one) |
| Q8 | Can't edit | Dropped. The move review (Q3) and ratings (Q10) replace it as ground truth |
| Q9 | 5 more edits coming | Waiting. Drop each in `data/real/<name>/` with its footage in a subfolder |
| Q10 | Ratings: yes | Built: after each render, one tap per row: on/off the beat, shake too little/right/too much, smooth/jerky, sharp/blurry. Stored next to the scorer's numbers (`edit_ratings`); "shake too much/little" also nudges your future edits |
| Q11 | Tell me what to film | Built: per reference shot, what it shows (CLIP) and its camera work; when your footage lacks that kind of shot, or an orbit/pan a digital move can't fake, you get a line like "Shot 10 (0.6 s at 6.4 s): rear of the car, camera moves around the car - your footage has no such camera move" |
| Q12 | Priority order | Followed |
| Q13 | Always the reference's song | Kept as is |

### Q2 answer: what pro editors sync to

- **Cuts** go on the **kick or the snare/clap**. These are the sharpest sounds, so the eye and ear agree on them. Most editors cut on the attack, the very start of the sound. Some put the cut one frame earlier because a picture change takes slightly longer to register than a sound.
- **Big moves** (zoom punches, whips, flashes) go on the **strongest hits**: the drop, the first beat of a bar, accents.
- **Shake** goes on the **808 / bass hits**, or on a snare as a single jolt that dies within a few frames.
- **Not every beat gets a move.** Pros alternate busy and calm moments; a move on every hit feels like noise.
- **The music decides what to follow:** in phonk and trap the 808 and snare carry the edit; in pop, the kick and vocal accents.

So the system should **copy the choice from the reference**, not guess it. The cloner now types each hit (kick/snare), and for the TikTok it found the editor **cuts on the snare**.

### What the fixes changed (measured on the analysis, no new edits except the Q1 test)

- **Timing:** hits are now where each drum sound starts, not ~46 ms later at its loudness peak. TikTok cuts landing within one frame of a hit went from **2 of 12 to 6 of 12** (the other 6 are cuts the editor made between hits). Moves snap within 2 frames (was 3) and land on exact output frames.
- **Shake:**
  - The old detector found 7 "shakes" in the TikTok.
  - It actually has **none in the picture**: its frames are smeared almost everywhere and snap sharp for 2–4 frames right after each hit. Those were misreads, which explains the over-shaking you saw.
  - Shake is now a short jolt on a hit that dies away, detected only on frames sharp enough to measure, or as a blur burst on a hit.
- **Smoothness:** eased punches (2-frame attack with the impact on the hit frame, 8-frame eased release, small spring), eased whips over 4 frames, whips carried across cuts, and a motion budget (no frame beyond 9% shift, 1.6× zoom or 4° rotation outside whips).
- **Quality:** Real-ESRGAN, bicubic warps on sharp frames, blur only where the reference has it, reframing zoom capped at 1.5× (was 2×), and footage windows chosen for sharpness and a subject already near the reference's size (less digital zoom).
- **Scorer:** new parts that catch what you saw: `timing` (late/early and scatter vs the reference), `shake` (moves per second), `smooth` (jerkiness) and `sharp` (smeared frames). On the old McLaren edit they read 82 / 78 / 1 / 85, while the untouched self-rebuild reads ~100 on all four.

### New problem found: P7, velocity edits

The TikTok is a **velocity edit**: footage speeds up between hits (heavy smear) and slows down right on each hit, so the picture lands sharp. Our system cannot make speed ramps inside a shot yet; it only has whole-shot slow-mo. That is the biggest remaining gap for this style.

Building it needs:
- a per-frame speed curve read from the reference's smear pattern;
- footage with real motion (driving, orbit). Speeding up a parked car shows nothing.

### The Q1 test render (the one edit made, because you asked to see −1/0/+1)

McLaren footage with all fixes, `timing_test/mclaren_fixed.mp4`, scored against the TikTok. The old edit (`mclaren_final.mp4`) is shown for comparison:

| | Old edit | New edit | TikTok |
|---|---|---|---|
| Timing (median offset from the hit) | +22 ms late → 82% | **+5.5 ms → 97%** | −6 ms |
| Shake (moves per second) | 2.35 → 78% | **2.24 → 85%** | 1.84 |
| Sharp (smeared frames) | 77% → 85% | **56% → 100%** | 69% |
| Cuts | 69% | **82%** | |
| **Smooth (jerkiness)** | 0.64 → 1% | **0.69 → 0% (not fixed)** | 0.38 |
| Overall | 53.4% | 59.0% | |

- **Smoothness did not improve.** The TikTok's motion never stops (flying camera plus velocity ramps). Our footage is mostly still between moves, so every move is a burst out of stillness, however well it is eased.
- That is P7. The fix is continuous motion: speed ramps on moving footage, or a slow continuous digital drift between moves.
- Render time went from about 4 to **22 minutes**, mostly Real-ESRGAN on 720p footage. `--no-upscale` is the fast path.

---

## Part 5: plan for P7, smoothness and velocity edits (2026-11-15)

Nothing in this part is built yet. It is the plan you asked for. Each step ends in tests, and no real edit is rendered until you say so.

### What I measured first (existing files only, no renders)

Motion is optical flow per frame (pixels at 160 px width); "sharp" is per frame, relative to the video's sharpest frames.

| | TikTok (velocity edit) | reel_2 (calm) | reel_1 (calm) | Our McLaren edit | Raw McLaren footage |
|---|---|---|---|---|---|
| Typical motion (median) | **2.06** | 0.50 | 0.05 | 1.00 | **0.14** |
| Fast moments (90th percentile) | 5.4 | 2.0 | 1.4 | **8.5** | 0.22 |
| Fast ÷ typical | **2.6×** | 4× | 29× | **8.5×** | 1.6× |
| Frames standing still | 12% | 9% | 34% | 2% | 0% |
| Typical sharpness | 0.13 (smeared) | 0.56 | 0.55 | 0.16 | 0.72 |
| Jerkiness | **0.38** | 1.41 | 2.23 | **0.69** | (smooth) |

Around each music hit the TikTok's motion is a continuous wave: it **slows to 0.6× about 5 frames before the hit** (the sharpest frames, 0.52), **speeds up into the hit** (2.1× on the frame before) and eases off again by +4 frames. It never stops; it breathes with the music.

What this says:
1. **Ours is too still between moves and too violent in them.** Our typical motion is half the TikTok's, while our fast moments are 1.6× the TikTok's. The motion comes in spikes, not a wave, and that is the "not connected" feeling.
2. **Calm edits are a different style.** reel_1 and reel_2 stand still a lot and are sharp. Their jerkiness is high and that is fine, because their cuts do the work. The fix must apply only to velocity-style references, or it would spoil the calm ones. (Your reel_2 self-rebuild "was good", which fits: it is calm.)
3. **The McLaren footage barely moves** (0.14, a slow walk-around). Even sped up 4× it reaches 0.56, a quarter of the TikTok. Speed ramps alone cannot make this edit, so camera motion has to carry most of it. (I checked for stutter or duplicated frames in the footage: there are none.)

### The plan, in order

**Step 1. Recognise the style (cloner).**
Mark a reference `velocity` when it is mostly smeared (typical sharpness < 0.3 of its sharpest) and rarely still (< 20% of frames). Otherwise it is `calm`, and everything stays as it is today.
Save the reference's per-frame motion curve in each segment (`seg.velocity`, smoothed), next to `seg.blur`. This also shows in the move review ("Style: velocity edit").
*Check:* TikTok → velocity; reel_1 and reel_2 → calm; the benchmark cases unchanged.

**Step 2. Real speed ramps inside a shot (renderer).**
Today a shot can only have whole pieces at one speed, so speed changes jump. New: a per-frame time map, where output frame *i* shows source time *p(i)* and speed is the slope of *p*. The map is a smooth curve with no steps.
- Fast frames **average every source frame they sweep across**. That is real motion blur from the footage itself, the true velocity-edit smear, and it looks better than the blur we fake now.
- Slow frames on the hit use one frame (sharp), or RIFE in-betweens below 1×.
- The speed curve comes from the reference's motion wave divided by the footage's own motion, kept between 0.3× and 4×. The Director then picks footage windows long enough for the sped-up parts.
*Check:* tests that the map never runs backwards, that it uses exactly the planned source length, and that frames are sharp on hits and averaged between them.

**Step 3. Continuous camera ("drift") instead of moves out of stillness.**
For velocity references, the camera layer gets a baseline motion that never stops: a slow push plus a gentle orbit-like pan or rotation. Its speed follows the reference's motion wave.
Punches, jolts and whips ride on top of the drift, and each starts at the speed the drift is already moving, so there is no burst out of zero. Successive moves blend into each other as one path (a spline through keyframes) instead of separate bumps added together.
Across cuts the drift continues in the same direction (as linked whips already do).
*Check:* tests that camera speed never drops to zero inside a velocity shot and that there are no speed jumps between frames.

**Step 4. Match the amount of motion (calibration).**
After building a shot, estimate its motion per frame (camera motion plus footage motion × speed). Scale the drift and the moves until the edit's typical and fast motion match the reference's: for the TikTok, typical 2.1 and fast ÷ typical 2.6×. This turns "too much shaking" from a guess into a measured number.
*Check:* a test on synthetic footage that the result lands within 15% of the target.

**Step 5. Faster renders as a side effect.**
In a velocity edit about 70% of frames are smeared, and Real-ESRGAN detail is invisible there. Upscale only the frames that land sharp (near hits) and use a plain resize for the rest. That should take the 22-minute renders back to about 6–8 minutes.
*Check:* timing on a short test clip, not a full edit.

**Step 6. Score it the way you feel it (scorer).**
Add `flow`: how closely the edit's motion wave around hits matches the reference's, plus the fast ÷ typical ratio. Keep `smooth` (jerkiness). For calm references both compare against the calm reference, so calm edits are not pushed to move.

**Step 7. Tell you what to film (advice, your Q11 "C").**
When a velocity template meets very still footage, say so: "This style needs moving shots (driving past, a fast walk-around, a rolling shot). Your clips barely move, so the motion will mostly be digital."

**Step 8. One test edit, only with your OK.**
McLaren footage with the TikTok template, scored against the TikTok next to the 59% edit. Aim: smooth from 0% to 60%+ and overall from 59% to 70%+.

### Honest limits
- With footage this still, steps 3 and 4 make the motion digital (zoom, pan, rotate). It will feel connected, but a pro's velocity edit on moving footage still has real parallax that a digital move cannot fake. Your 5 new edits will show how far this gets.
- Velocity edits are one style. When your 5 edits include a new style, the style check in step 1 gets a new branch, not a rewrite.

Size: steps 1–7 are about two to three working sessions of code and tests. Step 8 is one render (about 10 minutes once step 5 is in).

### Built (2026-11-15): steps 1–7

| Step | What it does now | Checked by |
|---|---|---|
| 1 Style | The cloner labels each reference `velocity` or `calm` and stores every shot's motion wave (`seg.velocity`). TikTok → velocity; reel_1, reel_2 → calm. The move review starts with the style line. | Real references (analysis only) and a unit test |
| 2 Speed ramps | Velocity shots on new footage get one smooth time map: 0.4× on the hits up to about 1.6× between them, with no steps. Fast frames blend every source frame they sweep across (real smear); slow frames show one real frame (sharp). RIFE adds in-betweens when the shot slows below 0.75×. | Unit tests; synthetic fast footage: sharpness follows speed (correlation −0.68) |
| 3 Drift | A camera that never stops (push plus a pan that carries its direction across cuts and turns only where a whip does). Punches and shakes ride on it, scaled down. | Unit test: camera motion never reaches zero; calm templates byte-identical |
| 4 Calibration | Drift and move strength are chosen so typical motion and fast ÷ typical match the reference; each shot logs the numbers. | Unit test |
| 5 Faster upscale | In velocity shots only source frames that land sharp get Real-ESRGAN (24 of 54 in the test shot); the rest get a plain resize, hidden by the smear. | Synthetic render log |
| 6 Score | New `flow` part (6%, taken from `pace`): the motion wave around hits and the fast ÷ typical contrast vs the reference. Details in `motion_wave`. | Unit test: same wave 100%, spikes from stillness < 50% |
| 7 Advice | A velocity template on still footage adds a "what to film" line. For the McLaren footage: "Your clips move about 15x less than the reference…" | Real footage + TikTok re-clone (analysis only) |

`--no-velocity` (CLI and `apply()`) renders a velocity template the old way.

**What came out differently from the plan:**
- **Step 3 is simpler than planned.** Moves are not rebuilt as one spline. They are scaled down and ride on the drift, so they no longer burst out of stillness, but each move is still its own eased bump. If the test edit still feels jerky between moves, that rewrite is the next step.
- **Still footage can't reach the reference's motion.** With McLaren-like footage (0.14 motion) the drift tops out at about 0.9–1.7, against about 2.3 for the TikTok, because pans are capped at 12% of the frame and the push at 1.2× to keep the picture sharp (quality is your #1). The calibration gets as close as those limits allow, and the advice line says so.
- I lowered the drift's zoom limit from 1.35× to 1.2× after a test shot visibly softened at the end of its push.

**Benchmark after P7 (synthetic, calm cases):** average 97.4 overall (was 97.5) and 92.9 SSIM (unchanged). Pixel output is identical because these cases never use the velocity path. Overall moves slightly because the new `flow` score takes 6% of `pace`'s weight: cuts_grade 98.5 → 97.0, zoom_punch 96.4 → 97.1, shake 97.5 → 98.0. All 23 tests pass.

### Step 8: the McLaren test edit (2026-11-15, with your OK)

`data/real/crazy_edit/_results/velocity_test/mclaren_velocity.mp4`, scored against the TikTok. The previous edit is re-scored with today's scorer (that is why it reads 56.1%, not 59.0%: the new `flow` part counts against it).

| | Previous edit | Velocity edit | TikTok |
|---|---|---|---|
| **Overall** | 56.1% | **53.7%** | |
| Shake (moves per second) | 85% (2.24) | **100% (1.84)** | 1.84 |
| Flow (motion wave around hits) | 11% | **20%** | |
| Smooth (jerkiness) | 0% (0.69) | 2% (0.60) | 0.38 |
| Pace | 59% | 64% | |
| Timing | 97% (+5.5 ms) | **82% (+20.7 ms, late)** | −6.3 ms |
| Cuts | 82% | **69%** | |
| Sharp (smeared frames) | 100% (56%) | 88% (76%) | 69% |
| Render time | 22 min | **16 min** | |

The first attempt stopped at shot 12: the render's temporary frames filled the 7.4 GB RAM `/tmp`. Renders now keep temp files on the data disk (`data/tmp`) and delete each shot's frames as soon as it is done.

**What the numbers showed, and what is fixed since (not rendered yet):**
1. **Timing was a frame late: a bug.** The time map gave frame i the speed of frame i−1, so every speed peak arrived one frame late (+27 ms against the reference). Fixed, with a test.
2. **Whips are about twice the reference's.** At the cuts our motion measured 6.1 against the TikTok's 3.2 px/frame, in both edits. This is the main source of the "fast vs typical" spikes (7.3× vs 2.6×) and likely part of the "too much shaking" you saw. Whip travel lowered from 0.45 to 0.25 frame widths. That is tuned on this one reference; your 5 new edits will check it.
3. **A bit too smeared** (76% vs 69%). Speed-ramp shutter lowered from 270° to 180°.
4. **Between cuts the edit moves half as much as the TikTok** (0.84 vs 1.61). The drift sits at its quality limits on this footage, so only moving footage closes this gap. The advice line says so.
5. **Cuts:** the cut detector finds most of our whip cuts 1–4 frames off. Calmer whips should help; to check on the next edit.

**Benchmark after these fixes:** average 97.7 overall (was 97.4) and 92.9 SSIM (unchanged). All 23 tests pass.

### Second test edit with the fixes (2026-11-15, with your OK)

`velocity_test/mclaren_velocity_v2.mp4`, same footage and template, rendered in 16 min:

| | Previous edit | Velocity v1 | **Velocity v2** | TikTok |
|---|---|---|---|---|
| **Overall** | 56.1% | 53.7% | **59.1%** (best so far) | |
| Cuts | 82% | 69% | **88%** | |
| Timing | 97% | 82% (+20.7 ms) | **96% (+6.0 ms)** | −6.3 ms |
| Music sync | 91% | 86% | **100%** | |
| Smooth (jerkiness) | 0% (0.69) | 2% (0.60) | **18% (0.53)** | 0.38 |
| Flow | 11% | 20% | 21% | |
| Shake (moves/s) | 85% (2.24) | 100% (1.84) | 88% (1.53) | 1.84 |
| Sharp (smeared) | 100% (56%) | 88% (76%) | 85% (77%) | 69% |

**Still open:**
- **Fast ÷ typical is still 7.2× (TikTok 2.6×)**, even with calmer whips. The spikes come from somewhere besides whips; this is next to find. The cause is likely that our between-cut motion is low (the typical value is half the TikTok's), so any move looks like a spike next to it.
- **Smear did not drop** with the lower shutter (77%). The camera layer's own motion blur is probably smearing frames the speed ramp left sharp; to check.
- **Shake rate is now a little low** (1.53 vs 1.84 moves/s): the calmer whips register as fewer moves.

### Where the remaining spikes and smear come from (measured on v2, no renders)

**Spikes: whips and cuts, not the moves inside shots.** Each frame was classified by what happens on it:

| Frames | TikTok motion | Ours (v2) | TikTok share of fastest 10% | Ours |
|---|---|---|---|---|
| Cut frame | 5.45 | 8.32 | 20% | 27% |
| Whip (4 frames each side of a cut) | 3.25 | 6.30 | 50% | **73%** |
| Punch | 2.13 | 1.01 | 0% | 0% |
| Everything else (drift) | 1.58 | 0.86 | **30%** | **0%** |

- **TikTok whips move only about 2× its in-shot motion** (3.25 vs 1.58). They are the flowing motion speeding up into the cut, not a separate slide. Ours are 7×: a sudden slide out of near-stillness. (This fits P7: what the cloner reads as "whips" in a velocity edit is the speed ramp accelerating into the cut.)
- **Lowering whip travel from 0.45 to 0.25 did not show up** (6.07 → 6.30 px/frame). Optical flow tops out around 6 px/frame, so both read as saturated. The cubic ease-in also puts most of a whip's travel on its last frame.
- In the TikTok, 30% of the fastest frames are ordinary in-shot motion; in ours none are. Its speed is spread through each shot; ours is concentrated at the cuts.

**Smear: the camera's copied motion blur.** The template's camera shutter is 360° (`motion_blur` 1.0), applied on every frame where the TikTok is soft (74% of frames):

| v2 frames | Smeared | Median sharpness |
|---|---|---|
| Camera blur on (217) | **94%** | 0.09 |
| Camera blur off (78) | 38% | 0.41 |
| Fast, plain resize (79) | 97% | 0.06 (previous edit, AI-upscaled: 81%) |
| Slow, AI-upscaled (216) | 72% | 0.16 |

The TikTok's frames are soft because its footage moves fast. Ours barely moves, so the copied blur just softens the picture without showing motion. This is probably part of the "lost quality" you saw.

**Proposed fixes (not built yet):**
1. **Velocity shots: camera blur only on fast camera moves** (the `fast_only` rule, at most 180°). The speed ramp already makes the smear from real footage motion. Expected: smeared ~40–50% (sharp 85% → ~100%) and visibly crisper frames.
2. **Velocity shots: whips continue the drift.** Whip strength is calibrated like the drift: whip-frame motion about 2× in-shot motion, as in the reference (ratio from the reference's own `velocity` at its whip frames vs the rest), with a gentler ease so a whip's travel is spread over its 4 frames. Expected: fast ÷ typical well down from 7.2×, smooth up. Risk: the "shake" rate may fall further, because fewer spikes register as moves.
3. **Optional:** AI-upscale fast frames too when the camera blur is off, since they no longer hide behind smear. That costs render time (back toward 22 min).

### Fixes 1 and 2 built (2026-11-15, not rendered yet)

1. **Velocity shots blur only fast camera moves**: whips and punch attacks, at most 180° (`_shutter(..., velocity=True)`). The smear comes from the speed ramp's own footage motion.
2. **Whips speed up out of the drift**: per shot, a whip gain (`calibrate_whips`, 0.02–1× of full travel) sets whip-frame motion to `whip_ratio` × the shot's calibrated motion. The ratio is measured from the reference's own `velocity` at its whip frames: **1.88×** on the TikTok. Velocity whips also ease with power 2 instead of 3, so the last frame carries 44% of the travel, not 58%. Calm edits are unchanged: power 3 and gain 1 give exactly the old curve.

Modelled on the v2 McLaren shots (the same plan; footage motion from the source clip; camera curves without reframing; no render):

| | Before | After |
|---|---|---|
| Whip frames ÷ in-shot motion (median of 12 shots) | 10.6× | **1.9×** (TikTok 1.88×) |
| Frames with camera blur | 74% at 360° | **27%** at 180° |

Still left: shot 2.97s keeps a peak of 23× its in-shot motion. It comes from its three punches on hits, not whips. Calibration already uses the lowest move gain (0.25) there, so a lower floor would be the next step if the render still shows spikes. The three 9–10-frame shots (1.87s, 2.17s, 7.07s) are too short to measure, and their whips land at 3.4–3.6×.

### Third velocity test edit (v3, `velocity_test/mclaren_velocity_v3.mp4`, render 23 min)

**Overall 63.8%** (v2 59.1%): the best edit so far.

| Part | v2 | v3 |
|---|---|---|
| Flow (motion wave) | 21% | **74%** |
| Sharp | 85% | **100%** (smeared 62%; TikTok 69%) |
| Shake | 88% | 99% |
| Pace | 65% | 75% |
| Timing | 96% | 98% |
| Smooth | 18% | **1%** |

| Motion (px/frame) | TikTok | v2 | v3 |
|---|---|---|---|
| Fast ÷ typical | 2.6× | 7.2× | **3.8×** |
| Whip frames | 3.25 | 6.30 | 2.27 |
| Cut frames | 5.45 | 8.32 | 7.35 |
| Typical | 2.06 | 1.10 | 1.06 |
| Mean frame-to-frame jerk | 1.00 | 1.27 | **1.06** |

**The smooth score fell although the edit got smoother.** Roughness is jerk ÷ *mean* motion. Jerk dropped to almost the TikTok's level (1.27 → 1.06 vs 1.00; on whip frames 2.02 → 0.46). But mean motion dropped more (2.42 → 1.68; TikTok 2.67), because the whips no longer carry the motion. What is left:

1. **Too little motion in the long shots.** Shots 2.97s, 7.40s and 8.60s reach 0.6–0.75 px/frame against targets of about 1.9–2.2. The footage barely moves, and the drift hits its travel caps (pan 12%, zoom 1.2×), which are there to protect picture quality.
2. **Cuts carry 67% of the jerk** (5.78 per cut frame vs the TikTok's 3.13). The jump across a cut is larger than the TikTok's.
3. **Punches jump out of the low drift**: 0.1 → 2.7 px/frame on the hit frame in shot 2.97s.

**Next fix, proposed (not built):** let the drift sway (ease back and forth inside the caps) instead of walking one way until it hits them. Long shots can then keep moving at the target speed without zooming in further. Punches and cuts would sit on top of real motion instead of stillness, which fixes 1 and 3 and softens 2.

### Drift sway built (2026-11-16, not rendered yet)

The quality caps now limit **where** the camera may go, not how fast it moves (`motion.drift_path`):
- **Pan:** straight while it fits in 12% of the frame. A longer pan curves instead of stopping at the edge: the gentlest arc that fits, at most a circle 12% wide. Its speed is kept, with no stops or turnarounds.
- **Push and roll:** ease back and forth inside 1×–1.2× zoom and ±1.5° roll, with rounded turns.
- Short shots whose drift already fits are unchanged.

Modelled on the v3 McLaren shots (same plan; footage motion from the source clip; camera curves without reframing; cut frames not included; no render):

| Shot | Target | v3 drift | Swaying drift |
|---|---|---|---|
| 2.97s (59 frames) | 2.19 | 0.61 | **2.19** |
| 7.40s (36 frames) | 1.88 | 0.55 | **1.87** |
| 8.60s (37 frames) | 1.81 | 0.73 | **1.98** |
| 0.00s (35 frames) | 2.40 | 1.54 | **2.36** |

| Whole edit (modelled) | TikTok | v3 drift | Swaying drift |
|---|---|---|---|
| Typical motion | 2.06 | 1.32 | **2.34** |
| Fast ÷ typical | 2.6× | 3.2× | **2.1×** |
| Jerk ÷ mean motion | 0.376 | 0.303 | 0.266 |

**Risks to check in the render:**
- The long shots now loop about once per 1–1.5 s (7.40s: about 1.2 loops in 1.2 s), which could read as a circular wobble.
- The 9–10-frame shots are too short to calibrate and stay at drift 1.0, below their targets (e.g. 1.37 vs 2.61). The cut frames are not modelled.

### Fourth velocity test edit (v4, `velocity_test/mclaren_velocity_v4.mp4`, render 19 min)

**Overall 66.5%** (v3 63.8%): the best edit so far.

| Part | v3 | v4 |
|---|---|---|
| Smooth | 1% | **70%** (roughness 0.445; TikTok 0.378) |
| Flow (motion wave) | 74% | **89%** (fast ÷ typical 2.4×; TikTok 2.6×) |
| Pace | 75% | 80% |
| Sharp | 100% | 100% (smeared 63%; TikTok 69%) |
| Shake | 99% | 96% |
| Cuts | 88% | **76%** |
| Camera | 9% | 4% |

Calibration now reaches the target motion in every long shot (2.97s: 2.27 vs 2.19; 7.40s: 1.95 vs 1.88; 8.60s: 1.86 vs 1.81). Mean motion is 2.38 px/frame (v3 1.68; TikTok 2.67). Mean jerk stayed at 1.06 (TikTok 1.00): cuts carry 56% of it, punches 22% (up from 15%).

**New problem: punches read as cuts.** The cut detector finds 4 extra cuts in v4 (3.93s, 4.27s, 5.57s, 7.53s; v3 had 1 extra). The frames at 3.93s show a one-frame jump to a tighter, blurred zoom. That is the punch on the 3.97s hit (sync −1 frame). With real drift underneath, calibration raised the move gain in shot 2.97s from ×0.25 to ×0.55, and a punch reaches 65% of its zoom on the hit frame alone. The TikTok's punches never trigger the detector. The missed cut at 1.87s is old (v3 missed it too).

**Next fix, proposed (not built):** in velocity shots, punches attack over 3 frames instead of 2 (about 35/75/100% instead of 65/100%), so a punch flows out of the drift like the TikTok's. Calibration then sets the strength from the reference's fast ÷ typical as before. Expected: the extra cuts go away (cuts back to about 88%), punch jerk drops, and shake may lose a little.

### Punch attack over 3 frames built (2026-11-16, not rendered yet)

In velocity shots a punch now reaches 35/75/100% of its zoom over 3 frames (`VELOCITY_PUNCH_ATTACK`) instead of 65/100% over 2. The biggest step (40%) still lands on the hit frame, so timing is unchanged. Calm edits keep the old attack.

**What the 4 extra cuts in v4 are** (PySceneDetect content score, threshold 27; real cuts score 45–62):

| Time | Content score | Cause |
|---|---|---|
| 3.93s | 28.7 (frames before: ~15) | Punch on the hit: a one-frame zoom jump |
| 4.27s | 28.2 (frames before: ~19) | Punch on the hit |
| 5.57s | 27–28 for many frames in a row | Steady fast drift right at the threshold, no jump in the frames |
| 7.53s | 31–34 for many frames in a row | Same: steady fast drift |

Modelled (camera curves, no render), the punches' biggest zoom step per frame drops from 0.112 to 0.082 in shot 2.97s (0.108 → 0.077 in shot 0.00s). The shot's peak motion drops from 9.0 to 6.7 px/frame. Calibration raises the punch strength a little to keep the fast ÷ typical target (×0.70 → ×0.85 in the model), which eats part of the gain. The estimated punch scores are about 25, just under 27: a narrow margin that only a render can confirm.

The fix does not touch 5.57s and 7.53s. The detector cannot tell steady fast motion from a slow cut there. Lowering the drift would cost the smoothness and flow that v4 gained, so I would leave those two unless they look wrong when watched.

### Fifth velocity test edit (v5, `velocity_test/mclaren_velocity_v5.mp4`, render 26 min)

**Overall 70.0%** (v4 66.5%): the best edit so far.

| Part | v4 | v5 |
|---|---|---|
| Smooth | 70% | **97%** (roughness 0.397; TikTok 0.378) |
| Cuts | 76% | **82%** |
| Timing | 98% | **100%** (median −7.0 ms; TikTok −6.3 ms) |
| Shake | 96% | 99% |
| Flow | 89% | 89% (fast ÷ typical 2.3×; TikTok 2.6×) |
| Sharp | 100% | 100% |
| Pace | 80% | 79% |
| Camera | 4% | 4% |
| Color | 32% | 32% |

**The punch false cuts are gone.** The content score on the punch frames dropped from 28.7 / 28.2 to 21.5 / 19.1 (threshold 27). The extra cut at 0.6s is gone too.

**What is left on cuts:**
- 3 extra detections: 4.73s (new), 5.57s and 7.53s. All three are steady fast motion that hovers at the threshold for several frames: 4.73s scores 24.8 → 28.2 → 27.1 while the third punch eases back over the drift (v4 scored 26.8 there). The frames show no jump.
- 1 missed cut at 1.87s, as in v3 and v4.

**Where the remaining points are** (weight × what is missing):
- Camera: 4% × 0.15 = 14.4 points
- Color: 32% × 0.15 = 10.2 points
- Cuts: 82% × 0.20 = 3.6 points

Camera and color are now the biggest gaps. They have not been looked at for velocity edits yet.

### Why camera (4%) and color (32%) score low (measured on v5, no renders)

**Camera.** The score compares the frame-to-frame camera motion (zoom, roll, pan x, pan y) with the TikTok's, frame by frame and shot by shot. The TikTok's measured camera includes its own footage motion (cars driving past, orbits). Per frame it zooms 0.019 (p90 0.097) and rolls 0.0095 rad (p90 0.048), 3–10× our drift. Our motion goes the same way as the TikTok's only about 50% of the time: chance.

| What-if on v5's camera score | Score |
|---|---|
| v5 as rendered | **4%** |
| No camera motion at all | 15% |
| Our magnitudes, the TikTok's directions | 16% |
| The TikTok's path at half size | 37% |
| The TikTok's path clipped to our quality caps | 38% |

A camera that stands still scores better than ours: motion in a different direction is punished more than none. Within the quality caps (zoom ≤ 1.2×, pan 12%, roll 3°), about **38% is the ceiling** for different footage. Above that, we would have to copy the TikTok's own camera at full size, zooms of 2× and more inside one shot, which costs picture quality.

**Color.** Per shot, the grade's target matches the TikTok well (brightness and contrast within 1–2 points). An 80% transfer toward it should land close. The output misses in two ways:

1. **The grade is measured before the camera zooms in.** The colour transfer reads the footage's stats from the full frame. The camera then reframes and pushes into the subject. Here the subject is a black car in the middle of a grey street, and zooming 1.67× into the centre darkens the frame by 7–14 brightness points. The zoomed long shots come out darkest: 4.93s 20.4 (expected 41.5), 2.97s 31.3 (42.7), 0.00s 35.1 (42.7), 6.43s 34.4 (43.1). Brightness and contrast together are 62% of the colour miss.
2. **Saturation is capped.** The transfer may scale colour by at most 2×. The rainy McLaren footage has colour spread 6.5–9.3 against targets of 12–20, so most shots hit the cap. Ours lands at 8–14. Saturation is 18% of the miss.

**Proposed fixes (not built):**
1. **Grade on the final framing:** measure the colour stats on frames that already have the camera move (reframe, drift and push), then grade. Expected: the brightness misses in the zoomed shots mostly go away. This is the biggest colour gain and costs no picture quality.
2. **Camera follows the TikTok's own path:** each shot's drift takes its direction and shape from the reference camera (`seg.camera`), shrunk into the quality caps. Calibration still sets the speed to the flow target. Expected: camera 4% → about 30–38% (+4 to +5 points overall), without losing flow or smoothness. Risk: the moves would follow the TikTok's footage motion even where our footage doesn't move that way.
3. **Optional: saturation cap 2× → 2.5×.** That means more colour on grey footage, at the risk of colour noise in dark areas, so it is a quality call. I would leave it unless fix 1 leaves colour still clearly flat.

### Grade on the final framing built; reference-shaped camera tried and dropped (2026-11-17, not rendered)

**Fix 1, built: the grade measures the framed picture.** `compositor.framed()` shows sampled frames exactly as the camera will (same matrices, cover zoom and reframe). `_render_shot` now decides the camera first and grades what it shows. Modelled on the McLaren shots (Reinhard 80% on brightness, footage frames run through the planned camera curve):

| Shot | TikTok | v5 measured | Grade as before (model) | Grade on framed picture (model) |
|---|---|---|---|---|
| 0.00s | 44.7 | 35.1 | 37.2 | **41.8** |
| 2.97s | 42.5 | 31.3 | 30.8 | **40.8** |
| 4.93s | 40.2 | 20.4 | 33.7 | **40.2** |
| 6.43s | 44.9 | 34.4 | 38.2 | **42.3** |
| 8.60s | 46.6 | 40.6 | 40.6 | **44.4** |

The model of the old grade matches v5's measured brightness in most shots. It underestimates 4.93s, where the render's subject reframe pushed in further than the model's camera.

**Fix 2, tried and dropped: drift following the TikTok's camera path.** The earlier 38% estimate was wrong: it clipped each frame's motion but ignored the total travel caps. With the whole camera modelled (moves included):

| Camera layers (modelled) | Camera score |
|---|---|
| Nothing | 14.9% |
| TikTok's own path shrunk into half the caps | 15.8% |
| Moves only (whips, punches, shakes, push) | 13.0% |
| Moves + sway (v5) | 6.2% |
| Moves + shrunk TikTok path + sway | 8.2% |
| Full pipeline with calibration: v5 → shaped drift | 4.9% → 5.9% (jerk ÷ mean 0.22 → 0.33) |
| TikTok's own path at full size, uncapped | 30.8% |

On this measure, **about 15% is the ceiling for a re-created camera on different footage.** The TikTok's own path, uncapped, reaches only 31%, because the measure reads image motion and the TikTok's footage moves too. The sway costs about 9 camera points (about 1.3 overall), but it is what made smooth 97% and flow 89%. The shaped drift gained 1 camera point and made the motion rougher, so it was removed. Camera stays at about 4–6% for this footage. Only footage that moves like the TikTok's raises it.

### Sixth velocity test edit (v6, `velocity_test/mclaren_velocity_v6.mp4`, render 16 min)

**Overall 69.7%** (v5 70.0%). Color improved, but cuts lost more.

| Part | v5 | v6 |
|---|---|---|
| Color | 32% | **43%** |
| Cuts | 82% | **73%** |
| Smooth | 97% | 95% |
| Flow | 89% | 88% |
| Everything else | same | same |

**The grade fix works.** Brightness went from 30% of the colour miss to 7%. In the zoomed shots: 3.10–4.93s 31.5 → 41.4 (TikTok 42.6); 4.93s 20.5 → 35.7 (TikTok 40.3); 6.43s 34.6 → 39.7 (TikTok 45.0). Per-shot colour scores went from 9% → 41%, 1% → 59% and 27% → 56% in those shots. What's left of the colour miss: contrast (29%; ours 24–30 vs the TikTok's 27–36), saturation (30%; ours 7–12 vs 11–19, held by the 2× cap) and a slight yellow cast (26%).

**Why cuts fell: more false detections on steady motion.** The detector now finds 6 extra cuts (v5: 3): 4.10s, 4.53s, 4.73s, 6.60s, 6.87s, 7.53s. The new ones are inside the shots the fix brightened. Brighter, higher-contrast frames give the same steady motion a higher content score, so more frames hover just over the threshold of 27. Real cuts score 45–75. 1.87s is still missed, as in v3–v5 (the shot before it comes from nearly the same footage).

**A detector fix was tested, not built.** The test rule counts a cut only where the score jumps at least 1.5–1.8× above the median of the 3 frames on each side:
- On v5 and v6: every false cut gone, every real cut kept.
- On the references, run raw without the detector's flash and same-picture filters: the TikTok loses its 2.97s cut and gains detections at flashes and whips; reel_1 loses 2 of 28 cuts; reel_2 is unchanged.

The cloner reads references with the same detector, so a change has to be built into `detect_cuts` with its existing filters and checked on all three references and the benchmark before it can be trusted.

### Cut detector: steady motion no longer reads as cuts (2026-11-17, no renders)

`analysis/visual.detect_cuts` keeps PySceneDetect's content detector (threshold 27) and its existing filters, plus one rule. A detection that rises less than 1.5× above the median of the 3 frames each side, **and** shows the same picture as the frame before it (thumbnail similarity above 0.9), is motion, not a cut. It is dropped, unless a real jump (above the threshold, rising 1.5× or more) sits within one frame before or a few frames after. Then the cut moves there; the detector had fired on the motion just before a cut, or a frame late.

Measured on every detection in five videos:

| Video | Before | After |
|---|---|---|
| TikTok (reference) | 13 cuts | 13, unchanged |
| reel_1 (reference) | 28 cuts | 28, unchanged |
| reel_2 (reference) | 22 cuts | 22, unchanged |
| v5 | 3 false cuts, 2 real cuts a frame off | 0 false; 2.43s → 2.47s and 6.17s → 6.13s (the real cut frames) |
| v6 | 6 false cuts, 1 real cut a frame off | 0 false; 2.43s → 2.47s |

False cuts rose only 1.0–1.3× with similarity 0.91–0.99. Real cuts rose 1.9× or more, or changed the picture (similarity 0.12–0.86). Snapping *every* detection to its peak was tried and rejected: it moved real TikTok cuts by 2 frames (1.17s → 1.23s), which would change the timing the cloner reads.

Re-scored with the new detector (the same files, no re-render):

| Edit | Before | After | Cuts |
|---|---|---|---|
| v4 | 66.5% | 68.2% | 76% → 85% |
| v5 | 70.0% | 72.0% | 82% → 92% |
| v6 | 69.7% | **73.4%** | 73% → 92% |

v6 is the best edit so far. What is left on cuts: the 1.87s cut, still missed because the shot before uses nearly the same footage, and the TikTok's own 3.10s detection.

Benchmark with the new detector: **97.8%** overall (before: 97.7%), cuts 100% in every case. The shake case's camera part varies from run to run (88.4%, 96.0% and 93.3% in three runs). Rerun with the rule switched off, it scored 98.9% overall against 99.3% with it on, so the detector does not lower it. push_rotate (93.7%) is the same with both detectors: that is the older known issue in CHECKLIST.md. Tests: 26 pass.

### Where v6's remaining colour miss comes from (measured, no renders)

The engine's colour transfer applied to the McLaren footage as the camera frames it, in the scorer's own units (mean over shots; "miss" = distance from the TikTok in scorer tolerances, lower is better):

| | Miss | Contrast | Saturation | b (yellow +) |
|---|---|---|---|---|
| TikTok | 0 | 32.7 | 14.2 | −3.1 |
| Footage as framed, ungraded | 3.44 | 24.6 | 5.6 | +2.8 |
| v6 measured | **1.87** | 27.9 | 9.2 | −0.5 |
| Grade as set (80% strength), model | 1.42 | 29.7 | 10.4 | −0.6 |
| Grade at 100% strength, model | 1.03 | 30.5 | 11.5 | −1.3 |
| 100% strength, saturation cap 3×, model | 0.97 | 30.5 | 12.2 | −1.3 |

- **The 80% strength** (`color_strength`, which keeps 20% of the footage's own look) costs about 0.4.
- **Stages after the grade** cost about 0.45: v6 is flatter and less saturated than its own grade. The LUT itself is exact: through FFmpeg it lands within 0.4 of the formula on stills. The loss is in motion blur on fast moves, the subject reframe or the whips, and needs a one-shot test render to pin down.
- **The saturation cap** barely matters (0.06).

### Velocity edits grade at 100%; the post-grade loss found (2026-11-17)

`RenderOptions.velocity_color_strength = 1.0`: velocity edits now match the reference look fully. Calm edits keep `color_strength` 0.8.

One-shot test renders of the two long shots, colour at every stage (files in `velocity_test/shot_test/`; miss = distance from the TikTok in scorer tolerances, lower is better):

| Stage | 2.97s miss | 4.93s miss |
|---|---|---|
| Footage, retimed (before grade) | 2.77 | 2.17 |
| Graded, full frame (bright on purpose: the grade targets the framed picture) | 2.57 | 3.96 |
| Graded + camera, motion blur off | 1.53 | 0.83 |
| Final (with motion blur) | 1.55 | 0.85 |
| v6 (80% grade), same shot | 1.88 | 1.45 |

- **100% strength helps:** 1.88 → 1.55 and 1.45 → 0.85 on these shots.
- **Motion blur is not the loss** (1.53 vs 1.55).
- **The caps are not hit:** the LUTs boost contrast 1.6–1.7× and colour 1.3×, under the 2× caps.

The rest of the miss is the method. The grade (Reinhard) stretches brightness and colour linearly around the mean:
1. **Contrast:** on footage that is mostly a black car, the stretch pushes the darkest pixels below black, they clip, and contrast lands at 27.8–30.1 against the TikTok's 33.
2. **Saturation:** it matches the *spread* of colour, but the score (and the eye) reads how colourful pixels are on average: 8–10 against the TikTok's 12–13. Grey footage keeps looking grey.

**Next, proposed (not built):** match the brightness *distribution* (quantile/histogram mapping, no clipping) and the colourfulness distribution (chroma magnitude) instead of mean and spread. This can be modelled on stills before any render, and it must keep the calm benchmark's colour (95%).

### Distribution-matching grade built (2026-11-17, not rendered)

The colour profile now also stores the **distributions** of lightness and of colourfulness (distance from the mean colour) as 9 quantiles (`ColorProfile.l_quantiles`, `chroma_quantiles`; `media.color_stats`). When both footage and target have them, `grade.transfer_lut` maps them by quantiles instead of matching mean and spread:
- **Lightness:** tones keep their order. Beyond the measured range the curve eases into black and white instead of clipping. Every segment's slope stays within 0.5–2.5×.
- **Colour:** the mean colour (cast) moves to the target's. Colourfulness is mapped by quantiles, hue kept, slope at most 3×.
- Older blueprints without the distributions and black-and-white looks keep the Reinhard transfer. The TikTok blueprint was re-cloned to get them; its shots and events are identical to before.

Checked through the real code path (the engine's LUT, applied by FFmpeg to stills of every McLaren shot as the camera frames it, 100% strength):

| | Colour miss | Contrast | Saturation | b (yellow +) | Crushed to black | Blown to white |
|---|---|---|---|---|---|---|
| TikTok | 0 | 32.7 | 14.2 | −3.1 | | |
| Linear grade (v6 method) | 1.04 | 30.5 | 11.5 | −1.5 | 8.6% | 3.0% |
| **Distribution grade** | **0.88** | 30.8 | 12.8 | −3.1 | **0.7%** | **0.0%** |

The yellow cast is gone and saturation is closer. Shadows and highlights keep their detail instead of clipping: better picture quality, not only a better score. Slope limits were modelled: 2.5×/3× gives 0.83, 3× lightness gives 0.74 but risks banding on dark 8-bit footage, and 2×/2× gives 1.17, worse than linear.

Benchmark with the distribution grade: **98.0%** (before: 97.8%). Colour 95.2% → 95.7%: push_rotate, whose one unmatched shot uses the transfer, went 76.1% → 79.5% colour and 93.7% → 94.2% overall. No case went down. Tests: 27 pass.

### Seventh velocity test edit (v7, `velocity_test/mclaren_velocity_v7.mp4`, render 16 min)

**Overall 76.7%** (v6 73.4%): the best edit so far.

| Part | v6 | v7 |
|---|---|---|
| Color | 43% | **66%** |
| Cuts | 92% | 92% (no false cuts; 1.87s still missed) |
| Timing | 100% | 99% |
| Smooth | 95% | 93% |
| Flow | 88% | 87% |
| Everything else | same | same |

Per shot, colour rose in 11 of 12 measurable shots, e.g. 1.17s 20% → 68% and 2.17s 33% → 87%. Saturation is now 10–17.5 against the TikTok's 11–19; v6 had 7–12. Of what is left, 56% is contrast (ours 26–31 vs 27–36).

**One shot went backwards:** 3.10–4.93s, 41% → 8%, brightness 32.6 against the TikTok's 42.6. That TikTok shot is split in two: half very dark (median brightness 29), half very bright (80 at the 75% point). Matching it needs a steep jump in the mid-tones. The 2.5× slope limit caps the jump, and with the curve pinned at the median the whole upper half fell short. **Fixed after the render:** the limited curve is now shifted so the average brightness still matches the target. On stills through the real code path, that shot lands at 42.2 (TikTok 42.6), and the whole edit's colour miss improves from 0.88 to 0.81, with crushed blacks at 0.5%.

With the brightness fix: tests 27 pass, benchmark **98.0%** (same as before the fix; colour 95.7%).

### Eighth velocity test edit (v8, `velocity_test/mclaren_velocity_v8.mp4`, render 16 min)

**Overall 77.2%** (v7 76.7%): the best edit so far. Color 66% → **70%**. Everything else is unchanged (cuts 92%, timing 99%, smooth 93%, flow 87%, sharp 100%, camera 4%).

**The brightness fix works.** Shot 3.10–4.93s: brightness 32.6 → 43.3 (TikTok 42.6), colour score 8% → 16%. 7.40–8.60s: 41.2 → 45.7 (45.9), 40% → 52%. Brightness is now 8% of the colour miss (v7: 22%).

**What's left of colour is contrast** (68% of the miss): ours 26–31 against the TikTok's 27–36, and 20 against 33 in shot 3.10–4.93s. The brightness curve may stretch at most 2.5×. The TikTok shots are split into very dark and very bright halves, which needs steeper mid-tones. Modelled earlier: 3× gives colour miss 0.74 against 0.83, but risks banding on dark 8-bit footage. That risk can be measured on stills before deciding.

## Part 6: your review of v8 (2026-11-17)

### Colours change from shot to shot (measured on stills, no renders)

Each shot is graded toward the matching TikTok shot (75% that shot's look, 25% the edit's overall look). The TikTok's shots differ in *content*: a blue car close-up, then mostly sky, then mostly grass. Their statistics jump from shot to shot, and the score compares shot by shot, so it rewards copying those jumps. On our grey footage of a black car, copying them shows up as a blue tint on one shot and none on the next (1.17s, 2.17s and 6.13s blue; 1.87s, 2.97s and 8.60s neutral or warm).

Mean change between neighbouring shots, and colour miss (lower is better). Engine LUTs applied to stills of every shot as the camera frames it:

| Grade | Colour miss | Brightness jump | Blue-yellow jump | Saturation jump |
|---|---|---|---|---|
| TikTok itself | — | 4.9 | 8.5 | 3.8 |
| Our footage, ungraded | — | 2.3 | 2.2 | 1.0 |
| Shot's own look 75% (v8) | **0.80** | 3.6 | 6.7 | 3.9 |
| 50% | 1.11 | 2.6 | 4.6 | 2.6 |
| 35% | 1.33 | 1.9 | 3.3 | 1.8 |
| 20% | 1.56 | 1.3 | 2.0 | 1.0 |
| 0%: one look for the whole edit | 1.88 | 0.7 | 0.9 | 0.5 |
| One LUT for all shots (no per-shot brightness balance) | 1.88 | 2.5 | 4.2 | 1.2 |

The score and a consistent look pull in opposite directions. Colour miss 0.80 is v8's colour 70%; 1.87 was v6's 43%. A fully consistent look would cost about 4 overall points. A grade that respects content (sky, ground and car graded as regions) could give both, but it is a bigger build (gap 6 in CHECKLIST.md).

### The "detached" transitions in the TikTok (found frame by frame)

| Time | What happens |
|---|---|
| 1.00–1.27s | The next shot (hood close-up) slides in from the top-right corner as a moving panel and grows to full frame |
| 2.80–3.17s | **Wheel portal:** the next shot's wheel arch is a circular window showing the previous shot. The window shrinks into the arch, flashes white, then the real rim appears |
| 4.83–4.93s | The next shot's car slides up from the bottom of the frame as a cut-out over the previous shot |

None of these are detected or rendered yet. The cloner reads them as whips and flashes. A logo detaching was not found yet.

What building them takes:
- **Detection in the reference:** find frames where part of the picture already shows the next shot (a circle, a panel, a car shape), plus where it moves and grows.
- **Rendering on your footage:** the panel needs no new model. The wheel portal can find the wheel as a circle inside the car's YOLOX box. The car cut-out needs a segmentation model (e.g. rembg/U²-Net or a YOLO-seg model, a download that needs your approval).

### Region-aware grade: tried, not built (2026-11-17, stills only)

Masks with tools already installed: the car from its YOLOX box refined by OpenCV GrabCut (clean on both videos); the sky as bright, smooth areas in the upper frame; ground as the rest. Each region was graded toward the TikTok's same region over the whole edit, one mapping per region, so it stays consistent.

| Option (stills of every McLaren shot) | Colour miss | Blue-yellow / green-red jumps | Brightness jumps |
|---|---|---|---|
| v8 (per-shot look) | **0.80** | 6.7 / 2.2 | 3.6 |
| Region-aware, brightness per region | 3.54 | 3.9 / 1.2 | 3.1 |
| Region-aware colour, brightness per shot | 2.10–2.24 | 4.1 / 1.9–2.1 | 3.7 |
| Region-aware colour, one brightness curve | 2.42 | 4.1 / 2.0 | 2.5 |
| **One tint for every shot; brightness, contrast and colourfulness per shot** | 1.49 | **1.6 / 0.6** | 3.7 |
| Tint 25% per shot | 1.29 | 2.7 / 0.7 | 3.7 |
| One look for everything | 1.88 | 0.9 / 0.4 | 0.7 |

Why region-aware lost:
1. Matching each region's brightness turns our grey road, trees and houses (67% of our frames) into the TikTok's dark grass. Its frames are bright because a third of them is sky; ours are 10% sky.
2. Raising the black car's colourfulness turns the rain drops into coloured speckles and the car purple: a picture-quality defect, even at 1.3×.

What you saw jumping is mostly the **tint** (blue on one shot, warm on the next). Brightness that follows the scene is natural: the TikTok's own brightness jumps 4.9 between shots. "One tint" removes three quarters of the tint jumps (6.7 → 1.6) and keeps per-shot brightness. It costs colour miss 0.80 → 1.49, roughly colour 70% → ~52%, about −2.5 overall.

### Ninth velocity test edit (v9, `velocity_test/mclaren_velocity_v9.mp4`, render 17 min): one tint for every shot

**Overall 74.0%** (v8 77.2%). Color 70% → 48%, as predicted; everything else is the same (cuts 92%, timing 100%, smooth 93%, sharp 100%).

`RenderOptions.one_tint` (velocity edits): every shot gets the edit's overall tint; brightness, contrast and colourfulness still follow each shot. Mean change between neighbouring shots (3 frames per shot):

| | Green-red | Blue-yellow | Saturation | Brightness |
|---|---|---|---|---|
| TikTok | 3.2 | 7.1 | 2.9 | 6.7 |
| v8 | 2.5 | 6.8 | 3.4 | 3.3 |
| **v9** | **0.9** | **1.9** | **1.1** | 3.3 |

The blue casts on 1.17s, 2.17s and 6.13s are gone; all shots share one cool, rainy look.

Velocity edits only. In the benchmark's push_rotate case each shot carries a *deliberate* tint (real grading), and one tint dropped its colour from 79.5% to 48.3%. With the rule limited to velocity edits it is back at 94.2% overall / 79.5% colour. Telling a deliberate per-shot grade from a content-driven tint is still open. Tests: 28 pass.

### Your v9 review: colours still off after 4–5 s (2026-11-17, stills only)

After 4 s the frames are mostly grey road, concrete and a black car, where any tint shows. v9 moves every pixel by the difference between the TikTok's *average* colour and ours. That average is green-blue because of the TikTok's grass, blue car and sky, so it is content, not grade. It pushed our greys to a teal cast:

| Tint of grey/white areas (green-red, blue-yellow) | |
|---|---|
| TikTok | −1.7, −0.1 (almost neutral: its grade barely tints) |
| McLaren footage | 0.0, 0.0 |
| v9 | **−3.5, +0.2** (twice the TikTok's) |

Options on stills of every shot (sheet: `velocity_test/compare/tint_options_after_4s.png`; rows: TikTok, footage, v9 method, neutral tint, neutral + half the average tint):

| Tint | Colour miss | Greys tint | Overall blue-yellow | Look |
|---|---|---|---|---|
| v9: average tint | 1.48 | −3.2, −0.6 | −1.6 | road and sidewalk cold blue-teal |
| Neutral tint (greys to the TikTok's greys) | 2.45 | −1.0, −0.7 | +4.8 | greys right; sidewalk and walls go warm yellow |
| Neutral + half the average tint | 2.00 | −2.9, −0.1 | +3.0 | in between, most natural on the sheet |
| TikTok | — | −1.8, −0.2 | −3.1 | |

The TikTok's colour comes from what it shows (grass, a blue car, sky), so no tint brings the score back. This is a look decision.

### Tenth velocity test edit (v10, `velocity_test/mclaren_velocity_v10.mp4`, render 16 min): neutral + half tint (your pick)

The colour profile now also stores the **neutral point**: the tint of the greys and whites, i.e. the least colourful quarter of the mid-tones (`ColorProfile.neutral_ab`), plus colourfulness measured from it. With `one_tint` (velocity edits), greys go to the reference's greys plus half of the difference in average colour (`RenderOptions.tint_share` 0.5); colourfulness is mapped from the neutral point. The TikTok was re-cloned for the new fields; its shots and events are identical.

**Overall 72.8%** (v9 74.0%). Colour 48% → 40%; everything else is the same or slightly better (smooth 94%, flow 88%).

| | Blue-yellow jumps | Saturation jumps | After 4 s: overall blue-yellow |
|---|---|---|---|
| TikTok | 7.1 | 2.9 | −3.1 |
| v8 | 6.8 | 3.4 | |
| v9 | 1.9 | 1.1 | −1.8 (road and sidewalk blue) |
| **v10** | 4.2 | 1.4 | +2.4 (road grey, walls and yellow lines keep their warmth) |

Frames after 3 s, TikTok / v9 / v10: `velocity_test/compare/v9_v10_after_3s.png`. Half the tint follows each shot's own average, so warm shots stay a little warmer: jumps rose from v9's 1.9 to 4.2, still well under v8's 6.8. Tests: 29 pass.

### Your v10 review: blue rises at 3–5 s (2026-11-17)

**Frame by frame:** 5 frames per second (every 200 ms; frames are 33 ms apart at 30 fps), 49 per edit, from v8, v10 and the TikTok (`velocity_test/compare/frames_5fps/`). Each frame was measured for mean R/G/B, the tint of its greys, brightness, saturation, sharpness and colour noise.

- **Colour:** v10's red sits 10–15 levels below green and blue in nearly every frame (e.g. 0.0 s: R 99, G 114, B 110). Even its greys lean blue-green: a cyan cast across the whole edit, from the "half average tint" (the TikTok's average is green-blue from grass and sky). It shows most at 3–5 s, the wide street shot where sky and wet road fill the frame.
- **The footage itself is bluer in the wide street shots:** blue-green −1 to −2 at 2.47s, 2.97s, 7.07s and 8.60s, against −5 to −11 in the wall and close-up shots (sky, and wet road reflecting it). Shot by shot grading kept that difference.
- **Quality:** 3–5 s is softer because that source window is softer (sharpness 4,013 against 9,000–12,000 elsewhere); v10 keeps 80% of it. Short shots lose more to whips and punches: 2.17s keeps 20%, 1.87s 32%, 0.00s 39%. The TikTok is softer still (24–360 on the same measure).

**Fix (built, not rendered):**
1. **One colour transform for the whole edit.** Greys and colourfulness are measured over all the footage (`engine._footage_colour`), so no shot drifts bluer or warmer than the next; only lightness follows each shot.
2. **Greys go exactly to the TikTok's greys** (`tint_share` 0): no copy of its content's green-blue.
3. **Colourfulness boost at most 1.6×** for velocity edits (`velocity_chroma_cap`): at 3× the warm sidewalks turned yellow and colour noise grew. 1.6× still doubles the footage's colour (5.4 → ~10–11; TikTok 13.3).
4. **Shot matching:** each shot's overall balance moves halfway to the edit's average (`shot_balance` 0.5), so the sky-heavy street shots are not bluer than the wall shots.

On stills of every shot through the engine's path: greys almost neutral; blue between shots varies 4.7 (one transform without matching: 6.2; v10's per-shot spread was larger). Sheet: footage / one transform / one transform + shot matching, `velocity_test/compare/fix_v11_stills.png`. Tests: 29 pass.

### Eleventh velocity test edit (v11, `velocity_test/mclaren_velocity_v11.mp4`, render 17 min): one colour transform, neutral greys, shot matching

**Overall 70.2%** (v10 72.8%). Colour 40% → 29%: the score rewards copying the TikTok's green-blue, which now is deliberately not copied. Cuts 92% → 87% (see below). Everything else is the same: timing 100%, smooth 94%, sharp 100%, flow 88%.

Frame by frame, 5 frames per second, means per second, v10 → v11 (`velocity_test/compare/frames_5fps/v11`, sheet of 1–5 s: `compare/v10_v11_1to5s.png`):

| Second | Red − green | Greys: red − green | Greys: blue − green | Colourful | Sharpness |
|---|---|---|---|---|---|
| 0–1 | −14.9 → −6.0 | −10.9 → −3.9 | 2.9 → −1.3 | 11.6 → 7.4 | same |
| 1–2 | −9.5 → −2.0 | −7.7 → −1.8 | 0.9 → −1.0 | 10.9 → 8.5 | same |
| 3–4 | −9.3 → −4.0 | −7.7 → −5.9 | −1.7 → 0.2 | 10.6 → 9.0 | same |
| 4–5 | −9.7 → −4.1 | −7.5 → −5.8 | −2.3 → −0.2 | 10.7 → 9.1 | same |
| 7–8 | −13.2 → −5.0 | −11.5 → −4.4 | 1.3 → 1.6 | 11.7 → 8.8 | same |

- **The cyan cast is gone:** red is no longer 10–15 levels under green; greys are close to neutral. Brightness, sharpness and noise are unchanged (noise slightly lower).
- **3–5 s stays a little bluer than 1–3 s** (blue − green −7.8 vs −6.8). That is the footage's wet road reflecting the sky; shot matching halves it.

**Why cuts fell:** 2.17s is now missed besides 1.87s. Shots 1.57s and 2.17s use the same footage window (the same wheel close-up), and 1.87s is nearly the same. The detector deliberately ignores cuts where the picture before and after looks the same (whips, flashes), and these sit right at its similarity threshold. The real problem is the director repeating a window of a clip with few angles. To fix: don't reuse a footage window within a few shots.

### Repeated footage and the missed cuts at 1.87 s and 2.17 s (2026-11-17, no renders)

**Director: no repeated picture within a few shots.** Shots 1.57s and 2.17s used the same wheel close-up (source 14.30s and 13.80s), one shot apart. The old rule blocked an angle only twice *in a row*. Now `_repeat_penalty` costs a reuse 0.6 on the next shot, fading to nothing 3 shots later (`RECENT_SHOTS`). It applies to the same angle **or** any window whose picture looks the same (thumbnail similarity above 0.85, the cut detector's own threshold). New plan: 2.17s uses source 7.60–7.90s. No shot looks like any of the three before it (highest similarity 0.59).

**The real reason the two cuts were missed** (this corrects the earlier guess that they read as "same picture"): the pictures differ clearly (similarity 0.21 and 0.17). Between 1.57s and 2.47s the motion keeps PySceneDetect's score above its threshold for many frames (30–35 against 27). In that state PySceneDetect 0.7.3 registers no new cut, even for spikes of 68.5 (1.87s) and 64.3 (2.17s). `detect_cuts` now adds back peaks the library skipped: a local peak above the threshold, at least 1.8× the median of the 3 frames each side, not next to an existing cut, not in the opening frames, with a real picture change.

| Video | Before | After |
|---|---|---|
| TikTok | 13 cuts | 13, unchanged |
| reel_2 | 22 | 22, unchanged |
| reel_1 | 28 | 29: adds 35.77s, a real cut (the end card switches from a big glowing "AURA" to a small one) |
| v5–v11 | 1.87s missed (since v3), 2.17s missed in v11 | **all 12 cuts, no false ones** |

Re-scored with the fixed detector (the same files, no re-render): v8 77.2% → 78.1%, v10 72.8% → 73.7%, **v11 70.2% → 72.0%**; cuts are now 96% in all three (the TikTok's own 3.10s detection is the one left). The new footage plan (2.17s without the repeated wheel close-up) needs a render to show. Tests: 30 pass. Benchmark 97.7%, cuts 100%; the shake case's camera part varies from run to run as before.

### Twelfth velocity test edit (v12, `velocity_test/mclaren_velocity_v12.mp4`, render 16 min): no repeated footage

**Overall 71.8%** (v11 72.0% with the fixed detector). Shot 2.17s now shows the front of the car (source 7.60–7.90s) instead of repeating 1.57s's wheel close-up. All 12 cuts are detected, with no false ones. Colours are the same as v11 (within 0.1 every second). Smooth 94% → 90% with the new footage's motion; pace 79% → 80%. Sheet of every shot next to the TikTok: `velocity_test/compare/shots_v11_v12.png`.

The machine rebooted again at 17:05 and wiped `/tmp`. The test scripts now live next to the edits, `velocity_test/scripts/` (`render_test.py NEW PREVIOUS`, `compare_frames.py OLD NEW`).

## Part 7: your v12 review (2026-11-17)

### Blue at 3–4 s

Split into its parts, the grade's **colour** step added the cast in the street shots: at 2.97s green − red +5.7 and blue − red +2.3 (similar at 7.07s). Brightness added nothing. The colour step moved our greys to the TikTok's greys, which carry its slight teal grade (green-red −1.8). Street shots are mostly grey (road, sidewalk, overcast sky), so they took on cyan everywhere; wall shots hide it.

**Fix (built):** `RenderOptions.keep_greys` keeps the footage's own greys neutral; only colourfulness and shot matching apply. On stills of every shot, 2.97s goes from blue − red +3.8 / green − red +7.4 (v12) to +0.9 / +3.5 (footage +0.5 / +1.8). A vibrance-style protection of near-grey tints was also tried; it barely helped (+3.9 → +3.4) and stays off (`protect` 0).

### Detached transitions (built, not rendered)

Measured frame by frame in the TikTok (`velocity_test/compare/transitions/`):

| Cut | Effect | Before / after the cut |
|---|---|---|
| 1.17s | **Corner panel:** the next shot enters as a blurred window growing from the top-right corner, full frame 2 frames after the cut | 7 / 2 frames |
| 2.97s | **Wheel portal:** the next shot zoomed hugely into its wheel zooms back out; the wheel's opening shows the previous shot; then a white flash, the rim, the shot | 3 / 5 |
| 4.93s | **Car cut-out:** the next shot's car, cut out of its background, rises from below to its place; then the next shot's background appears | 4 / 0 |

**In the template:** `Transition` gains `panel`, `portal` and `cutout`, plus `Segment.transition_frames` and `transition_from` (corner / edge).

**Detection:**
- **Portal: automatic** (`analysis/transitions.py`). It looks for one circle that keeps shrinking over 5+ frames by 2.5×: 134 → 27 px over 8 frames on the TikTok. That gives 1 hit on the TikTok and 0 false alarms on its other 11 cuts and on both reels.
- **Panel and cut-out: marked in the move review**, e.g. "add 1.17 corner panel; add 4.93 cut-out". Cheap measures could not tell them from fast whips in the same palette: per-pixel "which shot is this" and per-region switch timing were both too noisy. This template is marked at 1.17s and 4.93s, as you identified them.

**Rendering** (`render/transitions.py`) composites the frames on both sides of the cut after the shots are rendered: lossless, streamed, colour-exact (the velocity path's BT.709 conversion).
- **Panel:** a soft-edged window onto the next shot grows from the corner, motion-blurred along its movement.
- **Portal:** the next shot zooms out of its wheel at a steady rate (log scale, like the TikTok). The previous shot fills the opening, with a dark arch shadow around it, then the white flash fades.
  - The wheel is found by CLIP: square patches in the lower part of the car's box, "a car wheel" against headlight / hood / windscreen / road / ..., combined around the best patch and snapped to the rim's circle when one is clear.
  - Circle shapes alone picked headlights, hood curves and the road.
- **Cut-out:** the car is cut out of the next shot's first frame (YOLOX box + GrabCut) and rises from below with vertical motion blur.
- If no wheel or car is found, the cut stays plain and the log says so.

**Director:** for a portal shot, the 20 best windows are re-ranked by the size of their wheel (CLIP says "wheel" almost everywhere, so size decides; full bonus from a radius of 12% of the frame width). The portal shot now opens on a side view with a clear wheel (source 9.70s). The catch: that footage overlaps the first shot's (9.90–11.07s), 7 shots earlier.

Stills of all three on the planned footage: `compare/transitions/ours_plan_stills.png`.

Tests: 31 pass (new: the three transitions' compositing and the portal signature). Benchmark **97.9%**, cuts 100%; no portals were detected in its synthetic edits, as expected.

### Thirteenth velocity test edit (v13, `velocity_test/mclaren_velocity_v13.mp4`, render 17 min): transitions + neutral greys

**Overall 67.0%** (v12 71.8%); 67.9% with the cut-detector fix below. Frame-by-frame comparisons with the TikTok: `compare/transitions/{panel,portal,cutout}_v13.png`.

All three transitions rendered:
- **Wheel portal:** the closest match. The opening appears at 2.90s, as in the TikTok, shrinks steadily onto the rear wheel with the previous shot inside, flashes at 3.07s and lands on the real wheel.
- **Corner panel:** grows from the top right on time, but has little contrast. Both shots show the same black car by the same house (the TikTok's: a blue hood over a sky).
- **Car cut-out:** the next shot's car rises from the bottom and lands at 4.87–4.90s, then the cut.

Why the score fell:

| Part | v12 | v13 | Cause |
|---|---|---|---|
| Cuts | 96% | 77% (82% re-scored) | The portal's biggest change came 2 frames after the cut (detected 3.03s, not 2.97s). The cut-out's big car rising read as a cut at 4.83s and blocked the real one at 4.93s. A one-frame flash of another angle at the very end (9.80s). |
| Colour | 29% | 21% | Greys now stay neutral (the TikTok's teal is no longer copied); new footage for the portal shot |
| Pace | 80% | 74% | New footage for the portal and cut-out shots |
| Flow | 88% | 92% | |

**Fixed after the render** (tests: 31 pass):
- **Portal:** starts 5 frames before the cut, like the TikTok (opening covers the frame at 2.80s), with two flashes (3.03s and 3.10s).
- **Cut-out:** starts 5 frames before the cut (4.77s), like the TikTok.
- **Cut detector:** a cut exactly 3 frames after another counts, as PySceneDetect allows (4.93s found again; TikTok, reel_1 and reel_2 unchanged).
- **Director:** a footage window ends at its angle's last frame, not the next angle's first. The last shot ended exactly on a cut in the clip (4.50s) and showed one frame of the next angle.

### Fourteenth velocity test edit (v14, `velocity_test/mclaren_velocity_v14.mp4`, render 26 min): transition timing fixes

**Overall 70.1%** (v13 67.9%). Cuts 82% → **92%**: the portal now registers on the cut (2.97s), and the one-frame flash of another angle at the end is gone. The one extra detection left is 4.80s, where our large car rises in (the TikTok's is small). Shake 100%, smooth 93%, flow 92%; colour 21% and pace 72% as in v13.

The portal now follows the TikTok's timing: the opening fills the frame at 2.83s, shrinks onto the wheel by 3.00s and flashes at 3.03s. One frame still differed: at 3.07s the TikTok shows the real rim between its two flashes, ours still the tiny opening. **Fixed after the render:** after the first flash the wheel itself shows, with the second flash glowing over it (`portal_frame(opening=False)`; stills: `compare/transitions/portal_rim_fix.png`). Frame-by-frame comparisons: `compare/transitions/{panel,portal,cutout}_v14.png`.

### Where the remaining points are (v14, 2026-11-17)

| Part | v14 | Points lost | Why |
|---|---|---|---|
| Camera | 4% | 14.4 | The measure compares frame-to-frame camera motion with the TikTok's, which includes its footage moving (driving shots, orbits). A re-created camera on this footage tops out near 15%: standing still scores 14.9%. The drift that costs about 9 of those points is what made smooth 1% → 93% and flow 74% → 92%. |
| Colour | 21% | 11.9 | Of the remaining miss, 53% is blue-yellow and 16% green-red, i.e. tint. The TikTok's tint comes from its content (green grass, a blue car and sky), and copying it is what turned your shots blue (your v9–v12 reviews). Contrast is 14%, saturation 16%. |
| Cuts | 92% | 1.6 | One extra detection at 4.80s: our cut-out car is large (a medium shot) where the TikTok's is small (a wide shot). |
| Pace | 72% | 1.1 | Changed with the footage chosen for the portal and cut-out shots (v12: 80%). |
| Smooth, flow | 93%, 92% | ~1 | |

Colour options tried on stills (engine path, every shot), none built:

| Option | Colour miss | Street shots blue − red / green − red | Colour score (est.) |
|---|---|---|---|
| v14 grade | 2.55 | +3.4 / +5.2 | 35% |
| Brightness stretch up to 3× (contrast) | 2.54 | +3.4 / +5.2 | 35% |
| Hue-selective: warm ×0.75, cool ×1.3 | 2.38 | **+6.8** / +6.7 | 38% |
| Hue-selective: warm ×0.6, cool ×1.5 | 2.31 | **+8.9** / +7.8 | 39% |

More contrast does nothing measurable. Calming yellows and lifting greens and blues gains about 0.5 overall but makes the street shots bluer again. **With this footage and a look without a blue cast, the score is close to its ceiling: about 72–75% at best.** The large remaining gaps need footage that moves the way the TikTok's does (camera), or accepting the TikTok's content tint (colour).

### Fifteenth velocity test edit (v15, `velocity_test/mclaren_velocity_v15.mp4`, render 19 min): polish

**Overall 70.1%** (v14 70.1%). Pace 72% → 76%; smooth 93% → 91%, shake 100% → 99%; cuts 92%, colour 21%, flow 92% unchanged.

- **Portal:** the previous shot now stays still, full size, while the opening closes around it like an iris, as in the TikTok. Shrinking it as well had added a second zoom. The zoomed-in next shot is blurred in proportion to the zoom. On a test clip, motion during the portal went 5.4 → 4.1 px/frame (TikTok 3.0). Starting the opening smaller (the TikTok's 60% of the width) measured worse (5.1) and was reverted. After the first flash the real wheel shows, then the second flash (`compare/transitions/portal_v15.png`).
- **Cut-out:** the car fades in over the first 40% of its rise and grows from 70% to full size. The extra cut detection at 4.80s stays: our car is a medium shot's car, much bigger in frame than the TikTok's.

### Polishing v15 (2026-11-17, not rendered)

**Cut-out at 4.80s:** while our car rises, the detector's score climbs to 28.2–28.4, just over 27. The TikTok's small, blurred car stays at 12–19. The picture really changes (similarity 0.64), so it is not a motion artefact the filter should drop: our layer is simply bigger. Tested on v15's own frames:

| Entrance (start size, fade-in share of the rise) | Peak score | Extra cut |
|---|---|---|
| 70%, 40% (v15) | 27.4 | yes |
| 55%, 60% | 24.8 | no |
| **50%, 70% (chosen)** | 24.2 | no |
| 35%, 100% | 23.7 | no |

**Frame-by-frame defect scan of v15** (all 30 frames per second; cuts and transitions excluded):
- No brightness jumps inside shots; the TikTok itself has three.
- Sudden softenings at 3.90s, 4.23s and 4.53s are the punches' motion blur, at the TikTok's own moments (3.87s, 3.97s, 4.17s, 4.57s).
- 0.57s goes from crisp to blurred in one frame on the punch; the TikTok is already soft before it. Style, not a defect.
- **Colour noise:** 1.16 median against the TikTok's 0.85. The colour boost turns rain texture into coloured speckle.

**Chroma-only noise reduction** after the grade (`RenderOptions.chroma_denoise`, FFmpeg `hqdn3d=0:4:0:3`: brightness untouched). Tested on 1 s of v15: colour noise 1.11 → 0.98, sharpness 5175 → 5162 (unchanged).

With the gentler cut-out and chroma noise reduction: tests 31 pass. Benchmark **97.7%** (before: 97.9%). Pixel match (SSIM) 93.0% → 93.2%, colour 95.7% → 95.4%. Camera 93.7% → 91.7%, all in zoom_punch and shake, the cases whose camera part varies between runs; noise reduction does not touch brightness, which is what the camera tracker reads.

### Sixteenth velocity test edit (v16, `velocity_test/mclaren_velocity_v16.mp4`, render 19 min)

**Overall 71.0%** (v15 70.1%). Cuts 92% → **96%**: all 12 real cuts, no extra detections (the cut-out no longer reads as a cut). Colour noise 1.17 → **1.06**; sharpness 4983 → 4916 (−1%, unchanged to the eye). Pace 77%, shake 100%, smooth 92%, flow 91%; colour 21% and camera 4% as before (footage-bound, see "Where the remaining points are").

## Part 8: your v16 review (2026-11-18, measured, nothing built)

**"Shines on the car on the beats":** the TikTok has warm glows. Its upper half turns orange-warm for a few frames 17 times, 11 of them within 0.2 s after a beat (e.g. 4.03s after the 3.98s beat, 5.67s and 5.83s after 5.55s and 5.83s). 85% of its frames have glowing, blown highlights (v16: 63%). v16 has 3 such surges, 1 on a beat. The cloner does not detect glows or light leaks, so the edit cannot re-create them. To build: detect beat-synced warm/bright surges in the reference, and render them as a glow on highlights plus a warm light sweep, timed to the same beats.

**"The car doesn't stand, it switches":** measured with YOLOX on every frame, v16's car is as steady as the TikTok's, or steadier:
- Within shots it moves 0.48% of the frame per frame (TikTok 0.58%).
- Across cuts its centre jumps 9% of the frame (TikTok 11%).

Some shots frame it at a different size: 0.00s TikTok 16% of the frame height vs ours 29%; 1.17s 59% vs 32%; 1.57s 37% vs 69%; 6.13s 90% vs 65%. That comes from what the footage contains, since the car can only be enlarged 1.5× before it goes soft. What you see as "switching" is not measured yet; your answer below will tell.

**What cloning captures (the template):**

| Captured | Not captured |
|---|---|
| Cuts (frame-exact), beats, music hits, BPM, drops | Glows, light leaks, light sweeps ("shines") |
| Whips (slides), zoom punches, shakes, pushes, all timed to hits | Text on screen (no OCR yet) |
| Speed ramps and the motion wave (velocity edits) | Sound effects (whooshes, impacts) |
| Motion blur amount per frame | Radial/zoom blur, chromatic aberration, grain, vignette |
| White flash transitions | Object animations other than the wheel portal (logos, parts detaching) |
| Wheel portal (automatic); corner panel and car cut-out (marked in the move review) | Corner panel and car cut-out detection |
| Per-frame camera path, per-shot colour and lightness distributions | Masks (text behind the subject), sky replacement, 3D |
| Car position and size per shot, what each shot shows (CLIP) | |

About two-thirds of what a pro editor does is captured. The rest is effects and graphics.

### Beat glows (built, not rendered)

**What the TikTok does**, frame by frame: on some beats the picture switches to a warm, golden look over the *same* footage, holds for 8–9 frames, and switches back. At 4.00s (beat 3.98s) its sky goes from cool (yellow −6.7) to golden (+6.6) and slightly darker; again at 6.77s (beat 6.79s). Its other warm moments are new footage after a cut, not an effect.

**Detection** (`analysis/glow.py`, run by the cloner, stored as `Blueprint.glows`):
- **Rule:** the upper half's yellow rises by more than 6 within a frame or two, the picture stays the same (similarity above 0.8), no cut within 3 frames, a music hit within 0.1 s, held 3–15 frames.
- **Result:** exactly 4.00s and 6.77s on the TikTok, and none on reel_1 or reel_2.

**Rendering** (`render/glow.py`, over the joined edit before the final pass, lossless):
- **Warmth:** strongest at the top, fading toward the bottom.
- **Bright areas** (the sky) take the full change, darker areas 35% of it. Spread evenly, it turned houses and trees muddy brown.
- **Sky darkening:** at most 6 L, plus a warm highlight bloom (screen blend). It switches on at the beat and eases off over its last 2 frames.
- **On v16's frames:** the top quarter's yellow goes 9.2 → 16.1 at 4.00s, 16.2 → 22.3 at 6.77s.

It is subtler than the TikTok's, because our top is mostly house and trees, not open sky.

**Move review:** glows are listed ("Glow (picture turns warm and golden on the beat)") and can be removed or added in words ("add 8.0 glow", "shine", "golden", "light leak").

Tests: 32 pass. Benchmark **97.6%** (before glows: 97.7%), cuts 100%; no glows detected in its synthetic edits.

### Seventeenth velocity test edit (v17, `velocity_test/mclaren_velocity_v17.mp4`, render 24 min): beat glows

**Overall 70.7%** (v16 71.0%). Colour 21% → 20%: the scorer compares each shot's average colour, and the glow warms two shots for 9 frames. Cuts 96% with no extra detections; everything else unchanged.

The glows land on the TikTok's own frames. The upper half's yellow jumps at 4.00s (4.9 → 9.9, held, back to 6.5 by 4.33s) and at 6.77s (12.3 → 17.4, eased off before the 7.07s cut). Frame by frame with the TikTok: `compare/glow_v17.png`. They are visibly gentler than the TikTok's golden sunset sky, because our top of frame is a house and trees, not open sky. Stronger is one setting (`render/glow.py` `MIDTONE`, `MAX_DARKEN`).

## Part 9: a harder reference: the BMW garage edit (`crazy_edit/@ttsavebot_#1.mp4`, 2026-11-18)

11.5 s, 576×1024, 30 fps: a BMW M4 in a red-lit night garage. Frame strips: `_results/edit2/frames_*.png`.

### Ground truth (read frame by frame) vs what cloning captured

| Time | What the editor did | Cloned |
|---|---|---|
| 0.63, 1.27, 1.90, 2.23, 2.60 | Hard cuts | ✓ all |
| 1.40–1.80 | Stars over the dark interior (seat shot) | ✗ |
| 3.20–3.67 | Headlights flare up bright | ✗ |
| 3.70–3.97 | **Wheel portal:** the next shot (wheel) zooms out with the previous wide shot inside the wheel | cut found (3.77), type ✗ |
| 4.40–4.63 | **Car cut-out slide:** the next shot's car (rear) slides in from the left | cut found late (4.60), type ✗ |
| 5.07–5.27 | **Car cut-out slide** from the right (front, headlights) | ✗ missed |
| 5.67–5.80 | Zoom-through into the headlight close-up | ✗ missed |
| 6.33–6.50 | **Shape portal:** the wide garage shot appears through the headlight's curve and zooms out | cut found (6.37), type ✗ |
| 7.03–7.30 | Hard cut; the previous car glows white and fades over the interior | cut ✓, glow ✗ |
| 7.33–7.63 | Hard cut; the next car appears as a white glowing silhouette and fades in | cut ✓, glow ✗ |
| 7.67, 8.30 | Hard cuts | ✓ |
| 8.87–9.03 | The next shot's car rises in from below over the logo close-up | cut found (9.03), type ✗ |
| 9.47–10.30 | **Wheel portal** (as 3.70); the inside dissolves into the real rim at 10.13–10.30 | ✗ missed |
| 10.77–10.97 | **Car cut-out slide** from the right | ✗ missed |
| everywhere | No zoom punches or shakes; motion is in the footage and the transitions | ✓ none |

Cloning found **14 of 18 shots** and **0 of 10 transition types**. It also missed the stars and the white light flares.

### Why

1. **Gradual transitions are missed.** Masked slides and portals change the picture over 3–8 frames, so neighbouring frames always look alike. Comparing frames 6 apart (thumbnail, CLIP or geometric match) does not separate them from this edit's dark, blurry camera moves either: 5% of in-shot frames match as poorly as transitions do.
2. **The transition types are not recognised.** The portal detector looks for one shrinking circle; this edit's portals open from a wheel arch and a headlight curve. Cut-out slides and glowing silhouettes have no detector at all.
3. **Overlays (stars) and white light flares** have no detector. The glow detector only knows warm glows.

### Baseline: applying the clone as it is (`_results/edit2/mclaren_v1.mp4`, render 18 min)

**Match 67.8%**: cuts 83%, camera 36%, colour 71%, pace 51%, timing 73%, shake 76%, smooth 20%, flow 56%, sharp 100%. Frames side by side: `_results/edit2/compare_v1.png`. Every transition is a plain cut (no portals, slides or glowing silhouettes), and there are no stars or flares. The footage is a daytime rainy street; the reference is a red-lit night garage with interior close-ups the footage doesn't have (the director's filming advice says so).

### Plan to upgrade cloning (proposed)

1. **Find every transition, gradual ones too.** Hand-made measures don't separate this edit's transitions from its camera moves (above). TransNetV2 is a small, open model trained for exactly this, gradual transitions included (~30 MB, from GitHub, MIT licence). It needs a download, which needs your approval.
2. **Recognise the transition type.** For each transition, work out per pixel which shot it shows, then read the shape and motion of the new shot's region:
   - shrinking with the old shot inside it → portal (any shape, not only a circle);
   - moving sideways or up → cut-out slide, with its direction;
   - white and fading → glowing silhouette;
   - growing from the centre → zoom-through.
3. **Effects:** white light flares (the glow detector extended from warm to bright), and star overlays (small bright dots appearing on dark areas).
4. **Rendering the new types:** shape portals and glowing silhouettes from the car's outline, cut-out slides from any direction, zoom-through. The wheel portal, bottom cut-out and corner panel exist already.

### TransNetV2 downloaded and running; gradual transitions found; a correction (2026-11-18)

**TransNetV2** (official weights from github.com/soCzech/TransNetV2, MIT; 36.5 MB in `tools/models/transnetv2/`, SHA-256 checked against the published values) runs in plain numpy (`analysis/transnet.py`, with `analysis/tfcheckpoint.py` reading the TensorFlow weights), so nothing else was installed. It takes 12 s for this 11.5 s edit.
- **Checked on a synthetic video:** a 12-frame dissolve scores 0.86–0.99 on every frame and a hard cut 0.81, so the port works.
- **On the BMW edit:** hard cuts 0.44–0.97, but **every masked transition (portals, cut-out slides, zoom-through) scored about 0**. It was trained on cuts and dissolves and has never seen object-masked transitions. It is kept as a module for dissolves, and not used for these.

**What did find them:** what the picture *shows* (CLIP), averaged over 6 frames just before vs just after (`analysis/transitions.gradual_cuts`):
- **On the BMW edit:** every gradual transition peaks at 0.159–0.341, in-shot moves at most 0.154 (threshold 0.155).
- **Added cuts:** BMW 5.0, 5.63, 9.6, 10.23 (the portal's inside dissolving into the rim) and 10.8; TikTok 3.60, 3.97, 4.37, 5.47, all real shot changes hidden in whips (frames: `_results/edit2/tiktok_new_cuts.png`); reel_2 none; reel_1 none (with a 10-frame guard at the start and end).

**Result:** the BMW edit is cloned with 19 shots (was 14; ground truth 18 plus the rim dissolve), the TikTok with 17 (was 13).

**Correction: the TikTok has no beat glows.** Both "glows" (4.00s and 6.77s) were whips into new shots with a warmer sky; motion blur made the thumbnail check think the picture stayed the same. The glow detector now also requires CLIP to see the same shot (change below 0.13), and finds none in the TikTok. The glow *rendering* is unchanged, and still usable from the move review. v17's glows were therefore not something the TikTok's editor did, and the velocity work (v1–v17) used 13 of its 17 shots.

### Transition types recognised (2026-11-18, not rendered)

**Per-pixel "which shot" masks:** too noisy, because both shots keep moving during the transition. Right on slides from the right, wrong on portals and the left slide (`_results/edit2/masks_all.png`).

**Motion during the transition** (optical flow, mean zoom of the 6 fastest frames) separates three types:

| Zoom | Transitions | Type |
|---|---|---|
| −0.018 to −0.023 | BMW 3.77, 9.60; TikTok 2.97 | portal (zoom out of an opening) |
| +0.016 to +0.021 | BMW 5.63, 6.37 | zoom-through |
| −0.012 to +0.012 | slides, whips, hard cuts | — |

**`analysis/transitions.classify`:**
- **Portal:** zooms out, and its next shot shows a wheel (CLIP wheel check). reel_1's cartoon camera zoom-out has none: 0.00 against 0.99–1.00 for the portals.
- **Zoom-through:** zooms in.
- **Silhouette:** on the cut frame a pure-white blob covers ≥2% of the frame, absent just before and halved by the next frames. That is the BMW's white car at 7.03s and 7.33s.

**Result:** on the BMW edit all 6 classified transitions are right, with no false ones; TikTok: its portal; reel_1 and reel_2: none.

**Not detectable with any measure tried:** cut-out slides vs whips. Sideways flow, the share of the frame moving and region masks all overlap (slides 0.50–0.55, whips 0.38–0.72 moving share). They are marked in the move review instead, which now takes directions ("add 4.6 cut-out from left", "from below", "corner panel top left"). The BMW template has its 4 marked, and the review removed a false punch and shake at 5.13s (the slide's motion).

**Stars and flares: not built.** The interior shot's camera barely moves (0.9 px), so an added star overlay cannot be told from the car's own starlight headliner. The headlight flares are the car's own lights.

**Rendering the new types** (`render/transitions.py`):
- **Zoom-through:** the previous shot rushes in with zoom blur and the next lands out of it (4 + 4 frames).
- **Silhouette:** the next shot's car (YOLOX + GrabCut) is filled white with a soft glow on the cut frame, then fades over 5 frames.
- **Cut-outs:** now from the left and right too.
- **Whips:** a shaped transition replaces the whip at its cut (cloner and review), so the two no longer stack.

Stills: `_results/edit2/new_types_stills.png`.

Tests: 33 pass. Benchmark **97.7%**, cuts 100%: no false gradual cuts or transition types on its synthetic edits.

### BMW template on the McLaren footage, v2: worse than plain cuts (57.1% vs 67.8%), and the fixes

`_results/edit2/mclaren_v2.mp4` (25 min). All 10 transitions rendered (after fixing a crash: a transition with no frames before the cut). Side by side with the BMW edit (`_results/edit2/transitions_v2_*.png`):

| Transition | BMW | v2 | Fix |
|---|---|---|---|
| Portals 3.77, 9.60 | zoom out of a wheel, **no flash** | TikTok-style white flashes; 9.60 opened on the hood of a front view | Each portal stores whether the reference flashes (white share rising >0.02: TikTok +0.05, BMW +0.001). The director's wheel preference is doubled (both portal shots now open on wheel close-ups). No clear wheel: a round opening on the car instead of a plain cut. |
| Zoom-throughs 5.63, 6.37 | gentle push (~1.2×) | 2.2× with heavy blur | Zoom size taken from the reference's measured zoom per frame (exp(10 × zoom) ≈ 1.17–1.23) |
| Silhouette 7.03 | a small, distant white car (3% of the frame) | the next close-up car all white (~40%) | The white car is whichever car (previous or next shot) is closest to the reference's white share; 10% on these stills |
| Cut-out without a car | — | — | The whole next shot slides in instead of a plain cut |

v2 lost points where the white areas read as flashes (flashes 100% → 0%) and the strong blur cost smoothness, flow and sharpness. Stills of the fixes: `_results/edit2/fixed_types_stills.png`. Tests: 34 pass.

### BMW template v3 (`_results/edit2/mclaren_v3.mp4`, 26 min): level with plain cuts, once the scorer is fixed

As first scored: **64.2%** (v1 67.8%, v2 57.1%). Not committed, as agreed.

**The scorer was penalising real cuts.** It found the reference's cuts with the hard-cut detector only: 13 for the BMW edit, while cloning now knows 18. v3, which correctly cuts at all 18, was charged 5 "extra" cuts. The scorer now finds cuts the way cloning does, hard cuts plus CLIP-found gradual transitions, for both videos (`scoring._all_cuts`). Re-scored, the same files:

| | Overall | Cuts | Colour | Shake | Sharp | Smooth | Flow |
|---|---|---|---|---|---|---|---|
| v1 (plain cuts) | 64.8% | 71% | 66% | 76% | 100% | 20% | 56% |
| v2 | 57.3% | 77% | 70% | 88% | 60% | 6% | 10% |
| v3 | 64.6% | **82%** | 70% | **93%** | 67% | 0% | 27% |

v3 wins on cuts, colour and shake, and loses everything else to blur: 47% of its frames are smeared (BMW 35%). The worst is 83% around the first portal, whose zoomed-in shot is blurred while the BMW's wheel arch stays crisp; then the cut-outs' motion streaks.

**Fixed after:** portal blur cut to a third (`PORTAL_BLUR` 0.004 → 0.0013); cut-out motion streaks halved (a 180° shutter, `STREAK` 0.5).

Tests: 34 pass. Benchmark **97.7%**, cuts 100% with the new scorer: its synthetic edits get no false gradual cuts.

### BMW template v4 (`_results/edit2/mclaren_v4.mp4`, 26 min): 63.6% (v1 64.8%, v3 64.6% on the fixed scorer). Not committed.

Halving the transition blur did not change the smeared share (v3 47%, v4 47%; v1 36%; BMW 35%). Per shot, the extra smear is footage choice, not transitions. The scorer's "smeared" is relative to the video's own sharpest frames, and the McLaren footage has very sharp close-ups (≈10,000) next to soft, rainy wide shots (1,300–3,400); the BMW edit is evenly soft (p90 ≈ 250).
- **v4's softer shots** are those whose footage changed: the second portal (the stronger wheel preference chose a softer wheel window: 7,971 → 2,250), the last shot (4,663 → 2,568), and the wide shots at 1.27s and 2.60s.
- **The transitions themselves** win on cuts (71% → 82%), colour (66% → 70%) and shake (76% → 88%), and lose on sharp, smooth and flow through those footage changes.

**Where this leaves it:** on this footage, plain cuts and the BMW's transitions score within about 1 point. The transitions match the reference's structure (cuts 82%, all 10 types present); the score difference is footage choice. Next options: let the director weigh sharpness against the wheel preference; or judge v1 vs v4 by eye, which this metric cannot settle.

### BMW template v5 (`_results/edit2/mclaren_v5.mp4`, 27 min): 64.6%, level with plain cuts (v1 64.8%)

The director's wheel preference for portal shots now scales with how much of the shot the window fills (squared) and how sharp it is. v4's second portal had a 0.20 s window slowed 3.2× into 0.63 s; now both portals get full-length, sharp wheel shots (sharpness 0.97 and 0.83, no stretch).

| | Overall | Cuts | Colour | Shake | Sharp | Smooth | Flow | Smeared frames |
|---|---|---|---|---|---|---|---|---|
| v1 (plain cuts, old clone) | 64.8% | 71% | 66% | 76% | 100% | 20% | 56% | 36% |
| v4 | 63.6% | 82% | 70% | 88% | 67% | 0% | 25% | 47% |
| **v5** | 64.6% | **82%** | **70%** | **82%** | **96%** | 0% | 24% | 39% |

Sharpness is fixed. What is left of the gap is smooth and flow, which the transitions' fast motion at the cuts costs (the scorer rewards a motion wave like the reference's own). On this footage the two versions are within 0.2 points: this metric cannot pick between them, and looking at them is the test. Tests: 34 pass.
