// Editor: open a finished edit, change it like in CapCut (cuts, footage, speed, framing, moves, text, look,
// music), render it again, or export it to CapCut, After Effects, Premiere, Resolve and Final Cut.
//
// Every change is one request to /api/projects/<id>/edit; the server re-cuts the shot data (moves, speed,
// camera curves) and keeps an undo step. The preview here is quick: it plays your clips at their cut points,
// speeds and framing with the text and simple versions of the moves. The grade, glows and smear come from
// the real render ("Render again").

const SNAP = 0.08; // seconds: a dragged cut snaps to a beat or drum hit this close (hold Alt to place freely)
const PX_PER_S = 90; // timeline scale at zoom 1
const LOOK = [["brightness", "Brightness"], ["contrast", "Contrast"], ["saturation", "Saturation"], ["warmth", "Warmth"], ["effects", "Move strength"]];
const TRANSITION_WORDS = { cut: "Cut", flash: "Flash", zoom: "Zoom-through", panel: "Corner panel", portal: "Wheel portal", cutout: "Car cut-out", silhouette: "White silhouette" };
const EXPORT_HINTS = {
  capcut: "Ready-cut clips, one per shot, plus the music and the text. Drop them on CapCut's timeline in order. CapCut imports no timeline file.",
  aftereffects: "File > Scripts > Run Script File… builds the comp: shots, speed, moves as keyframes, flashes, text, music.",
  premiere: "Premiere Pro: File > Import. DaVinci Resolve: File > Import > Timeline.",
  fcpxml: "Final Cut Pro: File > Import > XML. DaVinci Resolve reads it too.",
  otio: "DaVinci Resolve 18.5+, Premiere Pro 26+, Kdenlive 25.04+, Avid.",
  edl: "Cuts and speeds only, for any editor.",
  bundle: "Every timeline format above, plus your clips, the music and the agent's render, in one zip.",
};

export async function editorView(pid, ui, params = new URLSearchParams()) {
  const { h, api, page, status, progressBox, poll, setNav, AudioGraph } = ui;
  setNav("edits");
  page("Loading…");
  let p = await api(`/api/projects/${pid}`);

  // ------------------------------------------------------------ state
  const st = { sel: { kind: "shot", i: 0 }, zoom: 1, mode: "quick", playing: false, t: Math.max(0, +params.get("t") || 0), started: 0, startT: 0 };
  const msg = h("div", { class: "editor-msg" });
  const say = (text, kind = "") => msg.replaceChildren(text ? status(text, kind) : "");

  async function change(body) {
    try {
      const next = await api(`/api/projects/${pid}/edit`, { json: body });
      p = next;
      if (next.done?.length) say("Got it: " + next.done.join("; "), "ok");
      else say("");
      redraw();
    } catch (e) {
      say(e.message, "error");
    }
  }

  async function step(way) {
    try { p = await api(`/api/projects/${pid}/${way}`, { method: "POST" }); say(""); redraw(); } catch (e) { say(e.message, "error"); }
  }

  // ------------------------------------------------------------ preview
  const W = p.width, H = p.height;
  const shape = `aspect-ratio:${W} / ${H};width:min(100%, calc(min(64vh, 560px) * ${(W / H).toFixed(4)}))`;
  const canvas = h("canvas", { class: "edit-canvas", style: shape, width: Math.round(W / 3), height: Math.round(H / 3), "aria-label": "Quick preview of the edit" });
  const ctx = canvas.getContext("2d");
  const pool = new Map(); // clip url -> hidden <video>
  const videoFor = (url) => {
    if (!pool.has(url)) {
      const v = h("video", { src: url, muted: true, playsinline: true, preload: "auto" });
      v.muted = true;
      v.addEventListener("seeked", () => !st.playing && draw());
      v.addEventListener("loadeddata", () => !st.playing && draw());
      pool.set(url, v);
    }
    return pool.get(url);
  };
  const music = h("audio", { preload: "auto" });
  const rendered = h("video", { controls: true, playsinline: true, preload: "metadata", class: "edit-rendered", style: shape, hidden: true });
  const clock = {
    get currentTime() { return st.mode === "render" ? rendered.currentTime : st.t; },
    set currentTime(t) { seek(t); },
  };

  function shotAt(t) {
    const i = p.shots.findIndex((s) => t >= s.start && t < s.end);
    return i < 0 ? p.shots.length - 1 : i;
  }

  function sourceTime(s, t) { // where in its clip a shot is at edit time t, following its speed points
    const dur = s.end - s.start, frac = (t - s.start) / dur;
    const pts = [...s.speed].sort((a, b) => a.at - b.at);
    let src = 0;
    pts.forEach((q, k) => {
      const next = k + 1 < pts.length ? pts[k + 1].at : 1;
      src += Math.max(0, Math.min(frac, next) - q.at) * dur * q.speed;
    });
    return s.src_start + src;
  }

  function speedAt(s, t) {
    const frac = (t - s.start) / (s.end - s.start);
    let v = s.speed[0]?.speed || 1;
    for (const q of s.speed) if (q.at <= frac + 1e-4) v = q.speed;
    return v;
  }

  function moveTransform(s, t) { // simple stand-ins for the renderer's moves: zoom, offset, rotation, white
    let zoom = 1, dx = 0, dy = 0, rot = 0, white = 0;
    const local = t - s.start, dur = s.end - s.start;
    for (const m of s.moves) {
      const at = m.t - s.start;
      if (m.kind === "punch" && local >= at - 0.05) zoom *= 1 + 0.15 * Math.exp(-(local - at) / 0.12) * (local >= at ? 1 : 0.5);
      if (m.kind === "push") zoom *= 1 + 0.12 * (local / dur);
      if (m.kind === "shake" && local >= at && local < at + 0.3) {
        const k = (1 - (local - at) / 0.3) * 0.02;
        dx += Math.sin(local * 90) * k; dy += Math.cos(local * 77) * k; rot += Math.sin(local * 60) * k * 1.5;
      }
      if (m.kind === "whip") {
        const into = local, out = dur - local;
        if (into < 0.13) dx += (1 - into / 0.13) ** 2 * 0.6;
        if (out < 0.13) dx -= (1 - out / 0.13) ** 2 * 0.6;
      }
    }
    if (s.transition === "flash" && local < 0.15) white = 1 - local / 0.15;
    return { zoom, dx, dy, rot, white };
  }

  function draw() {
    const t = st.t, cw = canvas.width, ch = canvas.height;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, cw, ch);
    if (!p.shots.length) return;
    const i = shotAt(t), s = p.shots[i], clip = p.clips[s.clip];
    const v = clip?.url && videoFor(clip.url);
    const m = moveTransform(s, t);
    if (v && v.readyState >= 2) {
      const scale = Math.max(cw / v.videoWidth, ch / v.videoHeight) * m.zoom;
      const vw = v.videoWidth * scale, vh = v.videoHeight * scale;
      let x = cw / 2 - s.center[0] * vw, y = ch / 2 - s.center[1] * vh;
      x = Math.min(0, Math.max(cw - vw, x)); y = Math.min(0, Math.max(ch - vh, y));
      ctx.save();
      ctx.translate(cw / 2, ch / 2); ctx.rotate(m.rot); ctx.translate(-cw / 2 + m.dx * cw, -ch / 2 + m.dy * ch);
      ctx.drawImage(v, x, y, vw, vh);
      ctx.restore();
    }
    if (m.white) { ctx.fillStyle = `rgba(255,255,255,${m.white})`; ctx.fillRect(0, 0, cw, ch); }
    for (const tx of p.texts) {
      if (t < tx.start || t >= tx.end) continue;
      const pop = tx.style === "pop" ? Math.min(1, (t - tx.start) / 0.12) : 1;
      const size = tx.size * (ch / H) * (0.7 + 0.3 * pop);
      ctx.font = `800 ${size}px system-ui, sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineWidth = Math.max(2, size / 10); ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.fillStyle = tx.color;
      ctx.globalAlpha = tx.style === "fade" ? Math.min(1, (t - tx.start) / 0.25, (tx.end - t) / 0.25) : 1;
      const y = ch * { top: 0.2, center: 0.5, bottom: 0.8 }[tx.position];
      tx.text.split("\n").forEach((line, k, all) => {
        const ly = y + (k - (all.length - 1) / 2) * size * 1.15;
        ctx.strokeText(line, cw / 2, ly); ctx.fillText(line, cw / 2, ly);
      });
      ctx.globalAlpha = 1;
    }
  }

  function syncVideo(play) {
    const i = shotAt(st.t), s = p.shots[i], clip = p.clips[s.clip];
    for (const [url, v] of pool) if (url !== clip?.url && !v.paused) v.pause();
    if (!clip?.url) return;
    const v = videoFor(clip.url), want = sourceTime(s, st.t);
    if (Math.abs(v.currentTime - want) > (play ? 0.15 : 0.01)) v.currentTime = Math.min(want, (v.duration || want) - 0.01);
    if (play) {
      v.playbackRate = Math.min(16, Math.max(0.0625, speedAt(s, st.t)));
      if (v.paused) v.play().catch(() => {});
    } else if (!v.paused) v.pause();
  }

  function seek(t) {
    st.t = Math.max(0, Math.min(p.duration - 1e-3, t));
    if (st.mode === "render") rendered.currentTime = st.t;
    if (st.playing) { st.started = performance.now(); st.startT = st.t; if (p.music) music.currentTime = st.t; }
    syncVideo(st.playing);
    draw();
    playhead();
  }

  function frame() {
    if (!canvas.isConnected) { for (const v of pool.values()) v.pause(); music.pause(); return; }
    if (st.playing) {
      st.t = p.music && !music.paused ? music.currentTime : st.startT + (performance.now() - st.started) / 1000;
      if (st.t >= p.duration) { st.t = 0; togglePlay(false); }
      syncVideo(true);
      draw();
      playhead();
    }
    requestAnimationFrame(frame);
  }

  function togglePlay(on = !st.playing) {
    if (st.mode === "render") { on ? rendered.play() : rendered.pause(); return; }
    st.playing = on;
    playBtn.textContent = on ? "⏸ Pause" : "▶️ Play";
    if (on) {
      st.started = performance.now(); st.startT = st.t;
      if (p.music) { music.src.endsWith(p.music) || (music.src = p.music); music.currentTime = st.t; music.play().catch(() => {}); }
    } else music.pause();
    syncVideo(on);
  }

  const playBtn = h("button", { class: "btn", type: "button", onclick: () => togglePlay() }, "▶️ Play");
  const timeText = h("span", { class: "hint edit-time" });
  const modeQuick = h("button", { class: "btn small chosen", type: "button", "aria-pressed": "true" }, "Quick preview");
  const modeRender = h("button", { class: "btn small", type: "button", "aria-pressed": "false" }, "Last render");
  function setMode(mode) {
    if (mode === "render" && !p.renders.length) return;
    togglePlay(false);
    st.mode = mode;
    modeQuick.classList.toggle("chosen", mode === "quick"); modeQuick.setAttribute("aria-pressed", String(mode === "quick"));
    modeRender.classList.toggle("chosen", mode === "render"); modeRender.setAttribute("aria-pressed", String(mode === "render"));
    canvas.hidden = mode === "render"; rendered.hidden = mode !== "render"; playBtn.hidden = mode === "render";
    if (mode === "render") { const last = p.renders[p.renders.length - 1].url; if (!rendered.src.endsWith(last)) rendered.src = last; rendered.currentTime = st.t; }
  }
  modeQuick.onclick = () => setMode("quick");
  modeRender.onclick = () => setMode("render");
  rendered.addEventListener("timeupdate", () => { st.t = rendered.currentTime; playhead(); });

  const previewCard = h("div", { class: "card video-card stack" },
    h("div", { class: "edit-screen" }, canvas, rendered),
    h("div", { class: "row" }, playBtn, timeText, h("span", { class: "grow" }), modeQuick, modeRender),
    h("p", { class: "hint" }, "The quick preview plays your clips at their cuts, speed and framing, with the text and simple versions of the moves. The grade, glows and motion smear are in the real render."));

  // ------------------------------------------------------------ toolbar
  const renderBox = h("div");
  const fast = h("input", { type: "checkbox", id: "edit-fast" });
  const renderBtn = h("button", { class: "btn primary", type: "button" }, "🎞 Render again");
  renderBtn.onclick = async () => {
    renderBtn.disabled = true;
    try {
      if (fast.checked === p.settings.upscale) await change({ op: "settings", settings: { upscale: !fast.checked } });
      const { job } = await api(`/api/projects/${pid}/render`, { method: "POST" });
      const done = await poll(job, (j) => renderBox.replaceChildren(progressBox(j)));
      if (done.status === "failed") throw new Error(`Render failed: ${done.error}`);
      renderBox.replaceChildren(status("Rendered. Showing it under “Last render”.", "ok"), ...done.result.notes.map((n) => h("p", { class: "note" }, n)));
      p = await api(`/api/projects/${pid}`);
      redraw();
      setMode("render");
    } catch (e) {
      renderBox.replaceChildren(status(e.message, "error"));
    }
    renderBtn.disabled = false;
  };
  const undoBtn = h("button", { class: "btn", type: "button", onclick: () => step("undo") }, "↶ Undo");
  const redoBtn = h("button", { class: "btn", type: "button", onclick: () => step("redo") }, "↷ Redo");
  const splitHere = async () => { const i = shotAt(st.t); await change({ op: "split", shot: i, t: st.t }); st.sel = { kind: "shot", i: shotAt(st.t) }; drawTimeline(); drawInspector(); };
  const splitBtn = h("button", { class: "btn", type: "button", onclick: splitHere }, "✂️ Split at playhead");
  const textBtn = h("button", { class: "btn", type: "button" }, "➕ Text at playhead");
  textBtn.onclick = async () => {
    const texts = [...p.texts, { text: "New text", start: st.t, end: Math.min(p.duration, st.t + 1.5), position: "center", size: 72, color: "#FFFFFF", style: "pop" }];
    await change({ op: "texts", texts });
    st.sel = { kind: "text", i: p.texts.findIndex((x) => x.text === "New text" && Math.abs(x.start - st.t) < 0.01) };
    redraw();
  };
  const toolbar = h("div", { class: "card stack" },
    h("div", { class: "row" }, undoBtn, redoBtn, splitBtn, textBtn, h("span", { class: "grow" }),
      h("label", { class: "toggle", for: "edit-fast" }, fast, "Fast render (no AI upscaling)"), renderBtn),
    msg, renderBox);

  // ------------------------------------------------------------ timeline
  const ruler = h("div", { class: "tl-ruler", "aria-hidden": "true" });
  const vTrack = h("div", { class: "tl-track tl-video", role: "list", "aria-label": "Shots" });
  const tTrack = h("div", { class: "tl-track tl-text", role: "list", "aria-label": "Texts" });
  const aTrack = h("div", { class: "tl-track tl-audio", "aria-hidden": "true" });
  const head = h("div", { class: "tl-head", "aria-hidden": "true" });
  const lanes = h("div", { class: "tl-lanes" }, ruler, vTrack, tTrack, aTrack, head);
  const scroller = h("div", { class: "tl-scroll" }, lanes);
  const zoom = h("input", { type: "range", min: 0.5, max: 6, step: 0.25, value: 1, id: "tl-zoom" });
  zoom.oninput = () => { st.zoom = +zoom.value; drawTimeline(); };
  const timelineCard = h("section", { class: "card stack" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Timeline"), h("label", { for: "tl-zoom", class: "toggle" }, "Zoom"), zoom),
    scroller,
    h("p", { class: "hint" }, "Click a shot or text to edit it · drag a cut to move it (it snaps to beats and hits; hold Alt to place it freely) · drag a text's ends to retime it · Space plays · S splits · Delete removes the shot · Ctrl+Z undoes"));

  const px = (t) => t * PX_PER_S * st.zoom;
  const tAt = (clientX) => (clientX - lanes.getBoundingClientRect().left) / (PX_PER_S * st.zoom);
  const snap = (t, free) => {
    if (free) return t;
    const near = [...p.beats, ...p.hits].reduce((best, b) => (Math.abs(b - t) < Math.abs(best - t) ? b : best), Infinity);
    return Math.abs(near - t) <= SNAP ? near : Math.round(t * p.fps) / p.fps;
  };

  function playhead() {
    head.style.left = `${px(st.t)}px`;
    timeText.textContent = `${st.t.toFixed(2)} s / ${p.duration.toFixed(2)} s`;
  }

  function drag(e, onMove, onDone) {
    e.preventDefault(); e.stopPropagation();
    const move = (ev) => onMove(ev);
    const up = (ev) => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); onDone(ev); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  }

  function drawTimeline() {
    const width = px(p.duration);
    lanes.style.width = `${width + 40}px`;
    ruler.replaceChildren(...Array.from({ length: Math.floor(p.duration) + 1 }, (_, s) => h("span", { style: `left:${px(s)}px` }, `${s}s`)),
      ...p.beats.map((b) => h("i", { style: `left:${px(b)}px` })));
    vTrack.replaceChildren(...p.shots.map((s, i) => {
      const clip = p.clips[s.clip];
      const on = st.sel.kind === "shot" && st.sel.i === i;
      const block = h("div", {
        class: `tl-shot${on ? " on" : ""}`, role: "listitem", tabindex: 0, style: `left:${px(s.start)}px;width:${px(s.end - s.start)}px`,
        "aria-label": `Shot ${i + 1}, ${s.start.toFixed(2)} to ${s.end.toFixed(2)} seconds, ${clip?.name || "missing clip"}`,
        onclick: () => { st.sel = { kind: "shot", i }; seek(s.start + 0.01); drawTimeline(); drawInspector(); },
        onkeydown: (e) => e.key === "Enter" && e.currentTarget.click(),
      }, h("img", { src: `/api/projects/${pid}/thumb?clip=${s.clip}&t=${(s.src_start + 0.04).toFixed(2)}`, alt: "", loading: "lazy" }),
        h("span", {}, `${i + 1}${s.transition !== "cut" ? " · " + TRANSITION_WORDS[s.transition] : ""}${s.speed.some((q) => q.speed !== 1) ? " · " + s.speed.map((q) => q.speed + "×").join("→") : ""}`));
      if (i > 0) {
        const handle = h("div", { class: "tl-cut", title: "Drag to move this cut", "aria-hidden": "true" });
        handle.onpointerdown = (e) => {
          const lo = p.shots[i - 1].start + 2 / p.fps, hi = s.end - 2 / p.fps;
          let t = s.start;
          drag(e, (ev) => { t = Math.min(hi, Math.max(lo, snap(tAt(ev.clientX), ev.altKey))); handle.style.transform = `translateX(${px(t - s.start)}px)`; seek(t); },
            () => Math.abs(t - s.start) > 1e-3 && change({ op: "cut", cut: i, t }));
        };
        block.append(handle);
      }
      return block;
    }));
    tTrack.replaceChildren(...p.texts.map((tx, i) => {
      const on = st.sel.kind === "text" && st.sel.i === i;
      const block = h("div", { class: `tl-textblock${on ? " on" : ""}`, role: "listitem", tabindex: 0, style: `left:${px(tx.start)}px;width:${px(tx.end - tx.start)}px`,
        "aria-label": `Text “${tx.text}”, ${tx.start.toFixed(2)} to ${tx.end.toFixed(2)} seconds`,
        onclick: () => { st.sel = { kind: "text", i }; seek(tx.start + 0.01); drawTimeline(); drawInspector(); },
        onkeydown: (e) => e.key === "Enter" && e.currentTarget.click() }, h("span", {}, tx.text));
      for (const edge of ["start", "end"]) {
        const grip = h("div", { class: `tl-grip ${edge}`, "aria-hidden": "true" });
        grip.onpointerdown = (e) => {
          const next = { ...tx };
          drag(e, (ev) => {
            const t = snap(tAt(ev.clientX), ev.altKey);
            if (edge === "start") next.start = Math.min(t, next.end - 0.1); else next.end = Math.max(t, next.start + 0.1);
            block.style.left = `${px(next.start)}px`; block.style.width = `${px(next.end - next.start)}px`;
          }, () => change({ op: "texts", texts: p.texts.map((o, k) => (k === i ? next : o)) }));
        };
        block.append(grip);
      }
      return block;
    }));
    aTrack.replaceChildren(h("span", {}, p.music ? (p.custom_music ? "🎵 Your music" : "🎵 Template music") : "No music"),
      ...p.hits.map((t) => h("i", { style: `left:${px(t)}px` })));
    playhead();
  }
  lanes.onpointerdown = (e) => { if (e.target === lanes || e.target === ruler || e.target.parentElement === ruler || e.target === aTrack) seek(tAt(e.clientX)); };

  // ------------------------------------------------------------ inspector
  const inspector = h("section", { class: "card stack side" });

  function field(label, input, id) { input.id = id; return [h("label", { for: id }, label), input]; }

  function shotInspector(i) {
    const s = p.shots[i], clip = p.clips[s.clip] || {}, n = p.shots.length;
    const dur = s.end - s.start;
    const pick = h("select", {}, p.clips.map((c, k) => h("option", { value: k, selected: k === s.clip }, `${c.name} (${c.duration.toFixed(1)}s)`)));
    pick.onchange = () => change({ op: "shot", shot: i, clip: +pick.value });
    const need = s.speed.reduce((a, q, k, all) => a + ((k + 1 < all.length ? all[k + 1].at : 1) - q.at) * dur * q.speed, 0);
    const maxIn = Math.max(0, (clip.duration || 0) - need);
    const inRange = h("input", { type: "range", min: 0, max: maxIn.toFixed(2), step: 0.01, value: Math.min(s.src_start, maxIn) });
    const inNum = h("input", { type: "number", min: 0, step: 0.05, value: s.src_start.toFixed(2), class: "num-input", "aria-label": "In-point, seconds" });
    const peek = h("img", { class: "edit-peek", alt: "The frame the shot starts on", src: `/api/projects/${pid}/thumb?clip=${s.clip}&t=${(s.src_start + 0.04).toFixed(2)}` });
    let timer;
    inRange.oninput = () => {
      inNum.value = (+inRange.value).toFixed(2);
      clearTimeout(timer);
      timer = setTimeout(() => (peek.src = `/api/projects/${pid}/thumb?clip=${s.clip}&t=${(+inRange.value + 0.04).toFixed(2)}`), 150);
    };
    inRange.onchange = () => change({ op: "shot", shot: i, src_start: +inRange.value });
    inNum.onchange = () => change({ op: "shot", shot: i, src_start: +inNum.value });
    const cx = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: s.center[0] });
    const cy = h("input", { type: "range", min: 0, max: 1, step: 0.01, value: s.center[1] });
    for (const r of [cx, cy]) {
      r.oninput = () => { s.center = [+cx.value, +cy.value]; draw(); };
      r.onchange = () => change({ op: "shot", shot: i, center: [+cx.value, +cy.value] });
    }
    const ramp = s.speed.length > 1;
    const speed = h("input", { type: "number", min: 0.1, max: 8, step: 0.05, value: ramp ? 1 : s.speed[0].speed, class: "num-input" });
    const setSpeed = h("button", { class: "btn small", type: "button", onclick: () => change({ op: "shot", shot: i, speed: +speed.value }) }, "Set");
    const trans = h("select", { disabled: i === 0 }, p.transitions.map((k) => h("option", { value: k, selected: k === s.transition }, TRANSITION_WORDS[k])));
    trans.onchange = () => change({ op: "shot", shot: i, transition: trans.value });
    const moveKind = h("select", { "aria-label": "Move to add" }, Object.entries(p.move_kinds).map(([k, label]) => h("option", { value: k }, label)));
    const at = Math.min(Math.max(st.t, s.start), s.end - 0.01);
    return [
      h("h2", {}, `Shot ${i + 1} of ${n}`),
      h("p", { class: "hint" }, `${s.start.toFixed(2)}–${s.end.toFixed(2)} s · ${dur.toFixed(2)} s${s.own_footage ? " · the reference's own footage" : ""}`),
      ...field("Footage", pick, "edit-clip"),
      h("label", { for: "edit-in" }, "Starts at, in the clip (seconds)"),
      h("div", { class: "row" }, Object.assign(inRange, { id: "edit-in", className: "grow" }), inNum),
      peek,
      h("label", { for: "edit-cx" }, "Framing: left ↔ right"), Object.assign(cx, { id: "edit-cx" }),
      h("label", { for: "edit-cy" }, "Framing: up ↕ down"), Object.assign(cy, { id: "edit-cy" }),
      h("label", { for: "edit-speed" }, ramp ? `Speed: a ramp (${s.speed.map((q) => q.speed + "×").join(" → ")}); set one speed instead` : "Speed"),
      h("div", { class: "row" }, Object.assign(speed, { id: "edit-speed" }), h("span", { class: "hint" }, "×"), setSpeed),
      ...field("Transition into this shot", trans, "edit-trans"),
      h("h3", {}, "Moves in this shot"),
      s.moves.length ? h("ul", { class: "edit-moves" }, s.moves.map((m) => h("li", { class: "row" },
        h("span", { class: "grow" }, `#${m.n} ${m.label} · ${m.t.toFixed(2)} s`),
        h("button", { class: "btn small", type: "button", onclick: () => change({ op: "moves", text: `remove ${m.n}` }) }, "Remove")))) : h("p", { class: "hint" }, "No moves in this shot."),
      h("div", { class: "row" }, moveKind, h("button", { class: "btn small", type: "button", onclick: () => change({ op: "moves", text: `add ${at.toFixed(2)} ${moveKind.value}` }) }, `Add at ${at.toFixed(2)} s`)),
      h("div", { class: "row" },
        h("button", { class: "btn small", type: "button", disabled: i === 0, onclick: () => { st.sel.i = i - 1; change({ op: "swap", shot: i, with: i - 1 }); } }, "◀ Footage earlier"),
        h("button", { class: "btn small", type: "button", disabled: i === n - 1, onclick: () => { st.sel.i = i + 1; change({ op: "swap", shot: i, with: i + 1 }); } }, "Footage later ▶")),
      h("div", { class: "row" },
        h("button", { class: "btn small", type: "button", disabled: n < 2, onclick: () => { st.sel.i = Math.max(0, i - 1); change({ op: "remove", shot: i }); } }, "🗑 Remove shot")),
      h("p", { class: "hint" }, "Removing a shot lets the one before take its time, so the later cuts stay on the beat. Moving footage swaps it with the neighbour; each slot keeps its timing and moves."),
    ];
  }

  function textInspector(i) {
    const tx = p.texts[i];
    if (!tx) { st.sel = { kind: "shot", i: 0 }; return shotInspector(0); }
    const save = (patch) => change({ op: "texts", texts: p.texts.map((o, k) => (k === i ? { ...o, ...patch } : o)) });
    const words = h("textarea", { rows: 2 }, tx.text);
    words.onchange = () => save({ text: words.value });
    const start = h("input", { type: "number", step: 0.05, min: 0, value: tx.start.toFixed(2), class: "num-input" });
    const end = h("input", { type: "number", step: 0.05, min: 0, value: tx.end.toFixed(2), class: "num-input" });
    start.onchange = () => save({ start: +start.value });
    end.onchange = () => save({ end: +end.value });
    const pos = h("select", {}, ["top", "center", "bottom"].map((k) => h("option", { value: k, selected: k === tx.position }, k[0].toUpperCase() + k.slice(1))));
    pos.onchange = () => save({ position: pos.value });
    const size = h("input", { type: "number", min: 24, max: 200, step: 2, value: tx.size, class: "num-input" });
    size.onchange = () => save({ size: +size.value });
    const color = h("input", { type: "color", value: tx.color });
    color.onchange = () => save({ color: color.value.toUpperCase() });
    const style = h("select", {}, [["pop", "Pop in"], ["fade", "Fade"], ["static", "Still"]].map(([k, l]) => h("option", { value: k, selected: k === tx.style }, l)));
    style.onchange = () => save({ style: style.value });
    return [
      h("h2", {}, "Text"),
      ...field("Words", words, "edit-words"),
      h("div", { class: "row" }, h("label", { for: "edit-ts" }, "From"), Object.assign(start, { id: "edit-ts" }), h("label", { for: "edit-te" }, "to"), Object.assign(end, { id: "edit-te" }), h("span", { class: "hint" }, "s")),
      ...field("Position", pos, "edit-pos"),
      h("div", { class: "row" }, h("label", { for: "edit-size" }, "Size"), Object.assign(size, { id: "edit-size" }), h("label", { for: "edit-color" }, "Colour"), Object.assign(color, { id: "edit-color" })),
      ...field("Animation", style, "edit-style"),
      h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => { st.sel = { kind: "shot", i: shotAt(st.t) }; change({ op: "texts", texts: p.texts.filter((_, k) => k !== i) }); } }, "🗑 Remove text")),
    ];
  }

  function lookSection() {
    const rows = LOOK.map(([k, label]) => {
      const r = h("input", { type: "range", min: -2, max: 2, step: 0.5, value: p.look[k] || 0, id: `look-${k}` });
      r.onchange = () => change({ op: "look", look: { ...p.look, [k]: +r.value } });
      return [h("label", { for: `look-${k}` }, label), r];
    });
    const s = p.settings;
    const glows = h("input", { type: "checkbox", id: "set-glows", checked: s.glows });
    glows.onchange = () => change({ op: "settings", settings: { glows: glows.checked } });
    const trans = h("input", { type: "checkbox", id: "set-trans", checked: s.transitions });
    trans.onchange = () => change({ op: "settings", settings: { transitions: trans.checked } });
    const blur = h("select", { id: "set-blur" }, [["auto", "Motion blur like the reference"], ["fast_only", "Blur only on whips and punches"], ["off", "No motion blur"]]
      .map(([k, l]) => h("option", { value: k, selected: k === s.blur }, l)));
    blur.onchange = () => change({ op: "settings", settings: { blur: blur.value } });
    return h("details", { class: "edit-more" }, h("summary", {}, "Look"),
      h("div", { class: "stack" }, ...rows.flat(),
        h("label", { class: "toggle", for: "set-glows" }, glows, "Beat glows"),
        h("label", { class: "toggle", for: "set-trans" }, trans, "Shaped transitions (portal, panel, cut-out…)"),
        h("label", { for: "set-blur" }, "Motion blur"), blur,
        h("p", { class: "hint" }, "Shown in the real render, not the quick preview.")));
  }

  function mediaSection() {
    const add = h("input", { type: "file", accept: "video/*", multiple: true, id: "edit-add" });
    add.onchange = async () => {
      const data = new FormData();
      for (const f of add.files) data.append("clips", f);
      say("⏳ Uploading…");
      try { p = await api(`/api/projects/${pid}/clips`, { method: "POST", body: data }); say("Added. Pick it under Footage on any shot.", "ok"); redraw(); } catch (e) { say(e.message, "error"); }
    };
    const song = h("input", { type: "file", accept: "audio/*,video/*", id: "edit-music" });
    song.onchange = async () => {
      const data = new FormData();
      data.append("music", song.files[0]);
      say("⏳ Uploading…");
      try { p = await api(`/api/projects/${pid}/music`, { method: "POST", body: data }); say("Music replaced. The cuts stay where they are; drag them onto the new beats if needed.", "ok"); redraw(); } catch (e) { say(e.message, "error"); }
    };
    return h("details", { class: "edit-more" }, h("summary", {}, `Media · ${p.clips.length} clip${p.clips.length === 1 ? "" : "s"}`),
      h("div", { class: "stack" },
        h("ul", { class: "edit-moves" }, p.clips.map((c) => h("li", {}, `${c.name} · ${c.duration.toFixed(1)} s${c.missing ? " · missing" : ""}`))),
        h("label", { for: "edit-add" }, "Add clips"), add,
        h("label", { for: "edit-music" }, p.custom_music ? "Music: yours. Replace it" : "Music: the template's. Replace it"), song,
        p.custom_music && h("button", { class: "btn small", type: "button", onclick: async () => { p = await api(`/api/projects/${pid}/music`, { json: { reset: true } }); redraw(); } }, "Use the template's music")));
  }

  function exportSection() {
    const fmt = h("select", { id: "exp-fmt" }, Object.entries(p.formats).map(([k, label]) => h("option", { value: k }, label)));
    const base = h("input", { type: "text", id: "exp-base", placeholder: "C:\\Users\\you\\Desktop\\my_edit (optional)" });
    const hint = h("p", { class: "hint" });
    const link = h("a", { class: "btn primary", download: "" }, "⬇️ Download");
    const update = () => {
      hint.textContent = EXPORT_HINTS[fmt.value] || "";
      const q = base.value.trim() ? `?base=${encodeURIComponent(base.value.trim())}` : "";
      link.href = `/api/projects/${pid}/export/${fmt.value}${q}`;
    };
    fmt.onchange = update; base.oninput = update;
    update();
    return h("details", { class: "edit-more" }, h("summary", {}, "Export to another editor"),
      h("div", { class: "stack" }, h("label", { for: "exp-fmt" }, "Format"), fmt, hint,
        h("label", { for: "exp-base" }, "Folder you'll unzip the media to on the other computer"), base,
        h("p", { class: "hint" }, "Fill it in and the files open there without relinking. Leave it empty to open them on this computer."),
        h("div", { class: "row" }, link)));
  }

  function drawInspector() {
    const main = st.sel.kind === "text" ? textInspector(st.sel.i) : shotInspector(Math.min(st.sel.i, p.shots.length - 1));
    inspector.replaceChildren(...main, lookSection(), mediaSection(), exportSection());
  }

  // ------------------------------------------------------------ sound
  let graphHolder = h("div"), graphData;
  const soundCard = h("section", { class: "card graph-card" }, h("h2", {}, "Sound"), graphHolder);
  async function drawGraph() { // a fresh holder each time: the old graph stops drawing once it is detached
    if (graphData === undefined) graphData = p.has_graph ? await api(`/api/projects/${pid}/graph`).catch(() => null) : null;
    const fresh = h("div");
    graphHolder.replaceWith(fresh);
    graphHolder = fresh;
    new AudioGraph(fresh, { graph: graphData, markers: p.markers, duration: p.duration, video: clock });
  }

  function redraw() {
    undoBtn.disabled = !p.undo; redoBtn.disabled = !p.redo;
    modeRender.disabled = !p.renders.length;
    fast.checked = !p.settings.upscale;
    if (st.sel.kind === "shot") st.sel.i = Math.min(st.sel.i, p.shots.length - 1);
    view_title.textContent = p.name;
    drawTimeline();
    drawInspector();
    seek(st.t);
    drawGraph();
  }

  // ------------------------------------------------------------ keys
  const keys = (e) => {
    if (!canvas.isConnected) return removeEventListener("keydown", keys);
    if (e.target.closest("input, textarea, select")) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); step(e.shiftKey ? "redo" : "undo"); }
    else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); step("redo"); }
    else if (e.key === "s" && !mod) splitHere();
    else if ((e.key === "Delete" || e.key === "Backspace") && st.sel.kind === "shot" && p.shots.length > 1) change({ op: "remove", shot: st.sel.i });
    else if (e.key === "ArrowRight") seek(st.t + (e.shiftKey ? 1 : 1 / p.fps));
    else if (e.key === "ArrowLeft") seek(st.t - (e.shiftKey ? 1 : 1 / p.fps));
  };
  addEventListener("keydown", keys);

  const rename = h("button", { class: "btn small", type: "button" }, "✏️ Rename");
  rename.onclick = () => { const name = prompt("Name:", p.name); if (name) change({ op: "rename", name }); };
  const view_title = h("span", {}, p.name);
  page("", h("div", { class: "row" }, h("a", { href: "#/edits", class: "hint" }, "← All edits"), h("span", { class: "grow" }), rename),
    toolbar, h("div", { class: "layout" }, previewCard, inspector), timelineCard, soundCard);
  const h1 = document.querySelector("#view h1");
  h1.replaceChildren(view_title);
  st.sel.i = shotAt(st.t);
  redraw();
  requestAnimationFrame(frame);
}

export async function editsView(ui) {
  const { h, api, page, setNav } = ui;
  setNav("edits");
  const rows = await api("/api/projects");
  if (!rows.length) return page("Your edits", h("p", {}, "No edits yet. ", h("a", { href: "#/apply" }, "Apply a template"), " to make one; it opens here for changes."));
  page("Your edits", h("ul", { class: "templates" }, rows.map((r) => h("li", { class: "card row" },
    r.cover && h("img", { class: "edit-cover", src: r.cover, alt: "" }),
    h("div", { class: "grow" }, h("a", { href: `#/edit/${r.id}`, class: "tpl-name" }, r.name),
      h("div", { class: "hint" }, `${r.duration.toFixed(1)} s · ${r.shots} shots · ${r.renders} render${r.renders === 1 ? "" : "s"} · changed ${new Date(r.updated * 1000).toLocaleString()}`)),
    h("a", { class: "btn primary", href: `#/edit/${r.id}` }, "✂️ Edit")))));
}
