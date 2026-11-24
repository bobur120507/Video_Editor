// Audio graph: the template's stored sound (waveform, dB mel spectrogram, band energy, onset strength)
// with beats, kick/snare hits, drops, cuts and moves on one time axis. Click seeks the video.
// Nothing is analysed here: every number comes from Blueprint.audio.graph and the blueprint's markers.

const GUTTER = 64, PAD_R = 14, GAP = 8, AXIS = 24;
const LANES = [
  { key: "events", title: "Moves", h: 52 },
  { key: "wave", title: "Waveform", h: 72 },
  { key: "spec", title: "Spectrum", h: 160 },
  { key: "bands", title: "Bands dB", h: 96 },
  { key: "onset", title: "Onsets", h: 84 },
];
const HIT_SLOT = { kick: "--series-low", snare: "--series-high", hit: "--series-mid" };

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  const v = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

export class AudioGraph {
  constructor(root, { graph, markers, duration, video }) {
    this.root = root;
    this.graph = graph;
    this.markers = markers;
    this.duration = Math.max(duration || 0, graph ? graph.onset.length / graph.fps : 0, 0.1);
    this.video = video;
    this.view = [0, this.duration];
    this.show = { beats: true, hits: true, drops: true, cuts: true, moves: true, low: true, mid: true, high: true, boost: false };
    this.hover = null;
    this.lanes = graph ? LANES : LANES.filter((l) => l.key === "events");
    this.height = this.lanes.reduce((s, l) => s + l.h + GAP, 0) + AXIS;
    if (graph) this.spec = Uint8Array.from(atob(graph.spec), (c) => c.charCodeAt(0));
    this.build();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(this.wrap);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.restyle());
    const tick = () => {
      if (!this.root.isConnected) return;
      this.drawOverlay();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  // ------------------------------------------------------------ DOM

  build() {
    this.root.innerHTML = "";
    this.root.classList.add("graph");
    const bar = el("div", "graph-bar");
    const toggles = el("div", "graph-toggles");
    const item = (key, label, swatch) => {
      const lab = el("label", "toggle");
      const box = el("input");
      box.type = "checkbox";
      box.checked = this.show[key];
      box.addEventListener("change", () => { this.show[key] = box.checked; this.drawStatic(); });
      lab.append(box);
      if (swatch) lab.append(swatch);
      lab.append(document.createTextNode(label));
      toggles.append(lab);
    };
    const sw = (v, shape = "dot") => { const s = el("span", `swatch ${shape}`); s.style.setProperty("--c", `var(${v})`); return s; };
    item("moves", "Moves", sw("--ink-1"));
    item("cuts", "Cuts", sw("--ink-2", "bar"));
    item("beats", "Beats", sw("--ink-3", "bar"));
    item("drops", "Drops", sw("--ink-1", "dash"));
    item("hits", "Hits: kick · snare · both", null);
    toggles.lastChild.prepend(sw("--series-low"), sw("--series-high"), sw("--series-mid"));
    toggles.lastChild.prepend(toggles.lastChild.querySelector("input"));
    if (this.graph) {
      item("low", "Low < 150 Hz", sw("--series-low", "line"));
      item("mid", "Mid", sw("--series-mid", "line"));
      item("high", "High > 1.5 kHz", sw("--series-high", "line"));
      item("boost", "Boost quiet onsets", null);
    }
    const zoom = el("div", "graph-zoom");
    const button = (text, title, fn) => { const b = el("button", "btn small"); b.textContent = text; b.title = title; b.onclick = fn; zoom.append(b); };
    button("−", "Zoom out", () => this.zoomBy(1.6));
    button("+", "Zoom in", () => this.zoomBy(1 / 1.6));
    button("Fit", "Show the whole video", () => { this.view = [0, this.duration]; this.drawStatic(); });
    bar.append(toggles, zoom);

    this.wrap = el("div", "graph-canvas");
    this.base = el("canvas");
    this.top = el("canvas");
    this.tip = el("div", "graph-tip");
    this.tip.hidden = true;
    this.wrap.append(this.base, this.top, this.tip);
    this.wrap.style.height = `${this.height}px`;
    const hint = el("p", "hint");
    hint.textContent = "Click to seek · drag to pan · Ctrl/⌘ + scroll to zoom";
    this.root.append(bar, this.wrap, hint);
    this.bindPointer();
  }

  resize() {
    const w = this.wrap.clientWidth;
    if (!w) return;
    const dpr = window.devicePixelRatio || 1;
    for (const c of [this.base, this.top]) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(this.height * dpr);
      c.style.width = `${w}px`;
      c.style.height = `${this.height}px`;
      c.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    this.w = w;
    this.restyle();
  }

  restyle() {
    this.colors = {
      surface: css("--surface-1"), ink1: css("--ink-1"), ink2: css("--ink-2"), ink3: css("--ink-3"), grid: css("--grid"),
      low: css("--series-low"), mid: css("--series-mid"), high: css("--series-high"),
      heat0: css("--heat-0"), heat1: css("--heat-1"),
    };
    if (this.graph) this.specImage = this.buildSpecImage();
    this.drawStatic();
  }

  // ------------------------------------------------------------ geometry

  laneTop(key) {
    let y = 0;
    for (const l of this.lanes) {
      if (l.key === key) return y;
      y += l.h + GAP;
    }
    return -1;
  }
  lane(key) { return this.lanes.find((l) => l.key === key); }
  get plotW() { return this.w - GUTTER - PAD_R; }
  x(t) { return GUTTER + ((t - this.view[0]) / (this.view[1] - this.view[0])) * this.plotW; }
  t(x) { return this.view[0] + ((x - GUTTER) / this.plotW) * (this.view[1] - this.view[0]); }
  col(t) { return Math.round(t * this.graph.fps); }

  zoomBy(f, at) {
    const [a, b] = this.view;
    const c = at ?? (a + b) / 2;
    let span = Math.min(this.duration, Math.max(0.25, (b - a) * f));
    let na = c - (c - a) * (span / (b - a));
    na = Math.max(0, Math.min(this.duration - span, na));
    this.view = [na, na + span];
    this.drawStatic();
  }

  // ------------------------------------------------------------ static layer

  buildSpecImage() {
    const g = this.graph, rows = g.mel_hz.length, cols = g.onset.length;
    const img = new ImageData(cols, rows);
    const a = hexToRgb(this.colors.heat0), b = hexToRgb(this.colors.heat1);
    for (let r = 0; r < rows; r++) {
      const y = rows - 1 - r; // low frequencies at the bottom
      for (let c = 0; c < cols; c++) {
        const v = this.spec[r * cols + c] / 255;
        const k = (y * cols + c) * 4;
        img.data[k] = a[0] + (b[0] - a[0]) * v;
        img.data[k + 1] = a[1] + (b[1] - a[1]) * v;
        img.data[k + 2] = a[2] + (b[2] - a[2]) * v;
        img.data[k + 3] = 255;
      }
    }
    const off = document.createElement("canvas");
    off.width = cols;
    off.height = rows;
    off.getContext("2d").putImageData(img, 0, 0);
    return off;
  }

  drawStatic() {
    if (!this.w) return;
    const ctx = this.base.getContext("2d");
    const C = this.colors;
    ctx.clearRect(0, 0, this.w, this.height);
    ctx.font = "11px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const l of this.lanes) {
      const y = this.laneTop(l.key);
      ctx.fillStyle = C.ink2;
      ctx.textAlign = "left";
      ctx.fillText(l.title, 4, y + 8);
      ctx.strokeStyle = C.grid;
      ctx.lineWidth = 1;
      ctx.strokeRect(GUTTER + 0.5, y + 0.5, this.plotW - 1, l.h - 1);
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(GUTTER, 0, this.plotW, this.height);
    ctx.clip();
    if (this.graph) {
      this.drawWave(ctx);
      this.drawSpec(ctx);
      this.drawBands(ctx);
      this.drawOnset(ctx);
    }
    this.drawVerticals(ctx);
    this.drawEvents(ctx);
    ctx.restore();
    this.drawAxis(ctx);
    this.drawYTicks(ctx);
  }

  span(key) {
    const top = this.laneTop(key);
    return [top + 4, top + this.lane(key).h - 4];
  }

  drawWave(ctx) {
    const g = this.graph, [y0, y1] = this.span("wave"), mid = (y0 + y1) / 2, half = (y1 - y0) / 2;
    ctx.strokeStyle = this.colors.grid;
    ctx.beginPath(); ctx.moveTo(GUTTER, mid + 0.5); ctx.lineTo(GUTTER + this.plotW, mid + 0.5); ctx.stroke();
    ctx.fillStyle = this.colors.ink2;
    const n = g.wave_min.length;
    for (let px = 0; px < this.plotW; px++) {
      let c0 = Math.floor(this.t(GUTTER + px) * g.fps), c1 = Math.max(c0 + 1, Math.floor(this.t(GUTTER + px + 1) * g.fps));
      c0 = Math.max(0, c0); c1 = Math.min(n, c1);
      if (c0 >= c1) continue;
      let lo = 1, hi = -1;
      for (let c = c0; c < c1; c++) { lo = Math.min(lo, g.wave_min[c]); hi = Math.max(hi, g.wave_max[c]); }
      const top = mid - hi * half, bot = mid - lo * half;
      ctx.fillRect(GUTTER + px, top, 1, Math.max(1, bot - top));
    }
  }

  drawSpec(ctx) {
    const g = this.graph, [y0, y1] = this.span("spec");
    const cols = g.onset.length, sx = this.view[0] * g.fps, sw = (this.view[1] - this.view[0]) * g.fps;
    ctx.imageSmoothingEnabled = sw > this.plotW;
    ctx.drawImage(this.specImage, sx, 0, Math.min(sw, cols - sx), g.mel_hz.length, GUTTER, y0, (Math.min(sw, cols - sx) / sw) * this.plotW, y1 - y0);
  }

  freqY(hz) {
    const m = this.graph.mel_hz, [y0, y1] = this.span("spec");
    let i = m.findIndex((f) => f >= hz);
    if (i <= 0) i = Math.max(1, i);
    const r = i - 1 + (hz - m[i - 1]) / (m[i] - m[i - 1]); // fractional row
    return y1 - ((r + 0.5) / m.length) * (y1 - y0);
  }

  dbY(db, key) {
    const [y0, y1] = this.span(key), floor = this.graph.floor_db;
    return y1 - ((Math.max(db, floor) - floor) / -floor) * (y1 - y0);
  }

  line(ctx, values, yOf, color, width = 2) {
    const g = this.graph, c0 = Math.max(0, this.col(this.view[0]) - 1), c1 = Math.min(values.length, this.col(this.view[1]) + 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (let c = c0; c < c1; c++) {
      const x = this.x(c / g.fps), y = yOf(values[c]);
      c === c0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  drawBands(ctx) {
    for (const k of ["mid", "high", "low"]) {
      if (this.show[k]) this.line(ctx, this.graph[k], (v) => this.dbY(v, "bands"), this.colors[k], 1.5);
    }
  }

  onsetY(v) {
    const [y0, y1] = this.span("onset");
    const s = this.show.boost ? Math.sqrt(v) : v; // square root lifts quiet ticks without hiding loud hits
    return y1 - s * (y1 - y0 - 12);
  }

  drawOnset(ctx) {
    const g = this.graph, [y0, y1] = this.span("onset");
    const c0 = Math.max(0, this.col(this.view[0]) - 1), c1 = Math.min(g.onset.length, this.col(this.view[1]) + 2);
    ctx.fillStyle = this.colors.grid;
    ctx.beginPath();
    ctx.moveTo(this.x(c0 / g.fps), y1);
    for (let c = c0; c < c1; c++) ctx.lineTo(this.x(c / g.fps), this.onsetY(g.onset[c]));
    ctx.lineTo(this.x((c1 - 1) / g.fps), y1);
    ctx.fill();
    this.line(ctx, g.onset, (v) => this.onsetY(v), this.colors.ink2, 1.5);
    if (this.show.beats) {
      ctx.strokeStyle = this.colors.ink1;
      ctx.lineWidth = 2;
      for (const b of this.markers.beats) {
        const x = this.x(b);
        ctx.beginPath(); ctx.moveTo(x, y1); ctx.lineTo(x, y1 - 8); ctx.stroke();
      }
    }
    if (this.show.hits) {
      for (const h of this.markers.hits) {
        const x = this.x(h.t), c = Math.min(g.onset.length - 1, this.col(h.t));
        const y = Math.max(y0 + 5, this.onsetY(Math.max(...g.onset.slice(c, c + 4))) - 6);
        this.dot(ctx, x, y, 4, css(HIT_SLOT[h.kind] || HIT_SLOT.hit));
      }
    }
  }

  dot(ctx, x, y, r, fill) {
    ctx.beginPath();
    ctx.arc(x, y, r + 2, 0, Math.PI * 2);
    ctx.fillStyle = this.colors.surface; // surface ring keeps overlapping marks apart
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
  }

  drawVerticals(ctx) {
    const top = 0, bottom = this.height - AXIS;
    const vline = (t, color, width, dash = []) => {
      const x = Math.round(this.x(t)) + 0.5;
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.setLineDash([]);
    };
    if (this.show.beats) {
      ctx.globalAlpha = 0.35;
      for (const b of this.markers.beats) vline(b, this.colors.ink3, 1);
      ctx.globalAlpha = 1;
    }
    if (this.show.cuts) for (const c of this.markers.cuts) vline(c.t, this.colors.ink2, 1);
    if (this.show.drops) for (const d of this.markers.drops) vline(d, this.colors.ink1, 1.5, [5, 4]);
  }

  movesLayout() {
    // Stack move badges that would overlap into a second row, like labels stacked on the annotated preview.
    const rows = [[], []];
    return this.markers.moves.map((m) => {
      const x = this.x(m.t);
      const row = rows[0].some((px) => Math.abs(px - x) < 20) ? 1 : 0;
      rows[row].push(x);
      return { m, x, y: this.laneTop("events") + 16 + row * 20 };
    });
  }

  drawEvents(ctx) {
    const y = this.laneTop("events");
    ctx.textAlign = "left";
    if (this.show.cuts) {
      ctx.fillStyle = this.colors.ink2;
      for (const c of this.markers.cuts) {
        if (c.transition !== "cut") ctx.fillText(c.transition, this.x(c.t) + 3, y + this.lane("events").h - 9);
      }
    }
    if (this.show.drops) {
      ctx.fillStyle = this.colors.ink1;
      for (const d of this.markers.drops) ctx.fillText("drop", this.x(d) + 4, y + 8);
    }
    if (!this.show.moves) return;
    ctx.textAlign = "center";
    ctx.font = "600 10px system-ui, sans-serif";
    for (const { m, x, y: my } of this.movesLayout()) {
      this.dot(ctx, x, my, 8, this.colors.ink1);
      ctx.fillStyle = this.colors.surface;
      ctx.fillText(String(m.n), x, my + 0.5);
    }
    ctx.font = "11px system-ui, sans-serif";
  }

  drawAxis(ctx) {
    const y = this.height - AXIS, [a, b] = this.view, span = b - a;
    const step = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30].find((s) => span / s <= Math.max(4, this.plotW / 80)) || 60;
    ctx.fillStyle = this.colors.ink2;
    ctx.strokeStyle = this.colors.grid;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let t = Math.ceil(a / step) * step; t <= b + 1e-9; t += step) {
      const x = this.x(t);
      ctx.beginPath(); ctx.moveTo(x + 0.5, y); ctx.lineTo(x + 0.5, y + 4); ctx.stroke();
      ctx.fillText(`${+t.toFixed(2)}s`, x, y + 6);
    }
    ctx.textBaseline = "middle";
  }

  drawYTicks(ctx) {
    if (!this.graph) return;
    ctx.fillStyle = this.colors.ink3;
    ctx.textAlign = "right";
    // Below each lane's title, so they never collide.
    for (const [hz, label] of [[100, "100 Hz"], [1000, "1 kHz"], [5000, "5 kHz"]]) ctx.fillText(label, GUTTER - 6, this.freqY(hz));
    for (const db of [-40, -80]) ctx.fillText(`${db}`, GUTTER - 6, this.dbY(db, "bands"));
  }

  // ------------------------------------------------------------ overlay: playhead + hover

  drawOverlay() {
    if (!this.w) return;
    const ctx = this.top.getContext("2d"), bottom = this.height - AXIS;
    ctx.clearRect(0, 0, this.w, this.height);
    const now = this.video && !isNaN(this.video.currentTime) ? this.video.currentTime : null;
    if (now !== null && now >= this.view[0] && now <= this.view[1]) {
      const x = this.x(now);
      ctx.strokeStyle = this.colors.ink1;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, bottom); ctx.stroke();
    }
    if (this.hover !== null && !this.dragging) {
      const x = this.x(this.hover);
      ctx.strokeStyle = this.colors.ink3;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, bottom); ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  nearest(x, y) {
    const near = (t) => Math.abs(this.x(t) - x) <= 6;
    const ev = this.laneTop("events");
    if (this.show.moves && y >= ev && y <= ev + this.lane("events").h) {
      const hit = this.movesLayout().find((p) => Math.abs(p.x - x) <= 10 && Math.abs(p.y - y) <= 10);
      if (hit) return `#${hit.m.n} ${hit.m.label}${hit.m.detail ? ` (${hit.m.detail})` : ""}`;
    }
    const out = [];
    if (this.show.hits) for (const h of this.markers.hits) if (near(h.t)) out.push(`${h.kind === "hit" ? "kick + snare" : h.kind} hit ${h.t.toFixed(2)}s`);
    if (this.show.cuts) for (const c of this.markers.cuts) if (near(c.t)) out.push(`${c.transition === "cut" ? "cut" : c.transition + " transition"} ${c.t.toFixed(2)}s`);
    if (this.show.drops) for (const d of this.markers.drops) if (near(d)) out.push(`drop ${d.toFixed(2)}s`);
    if (this.show.beats) for (const b of this.markers.beats) if (near(b)) { out.push(`beat ${b.toFixed(2)}s`); break; }
    return out.join("<br>");
  }

  tooltip(x, y) {
    const t = this.t(x);
    const rows = [`<b>${t.toFixed(2)} s</b>`];
    if (this.graph) {
      const g = this.graph, c = Math.max(0, Math.min(g.onset.length - 1, this.col(t)));
      rows.push(`Loudness ${g.loudness[c].toFixed(1)} dB`);
      rows.push(`<span class="k" style="--c:var(--series-low)"></span>Low ${g.low[c].toFixed(0)} · <span class="k" style="--c:var(--series-mid)"></span>Mid ${g.mid[c].toFixed(0)} · <span class="k" style="--c:var(--series-high)"></span>High ${g.high[c].toFixed(0)} dB`);
      rows.push(`Onset ${(g.onset[c] * 100).toFixed(0)}%`);
      const [s0, s1] = this.span("spec");
      if (y >= s0 && y <= s1) {
        const rowsN = g.mel_hz.length, r = Math.max(0, Math.min(rowsN - 1, Math.floor(((s1 - y) / (s1 - s0)) * rowsN)));
        const db = g.floor_db + (this.spec[r * g.onset.length + c] / 255) * -g.floor_db;
        rows.push(`${g.mel_hz[r] >= 1000 ? (g.mel_hz[r] / 1000).toFixed(1) + " kHz" : g.mel_hz[r].toFixed(0) + " Hz"}: ${db.toFixed(0)} dB`);
      }
    }
    const marks = this.nearest(x, y);
    if (marks) rows.push(`<span class="tip-marks">${marks}</span>`);
    this.tip.innerHTML = rows.join("<br>");
    this.tip.hidden = false;
    const tw = this.tip.offsetWidth;
    this.tip.style.left = `${x + 14 + tw > this.w ? x - tw - 14 : x + 14}px`;
    this.tip.style.top = `${Math.min(y + 10, this.height - this.tip.offsetHeight - 4)}px`;
  }

  bindPointer() {
    const pos = (e) => { const r = this.top.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let down = null;
    this.top.addEventListener("pointerdown", (e) => {
      down = { x: pos(e)[0], view: [...this.view] };
      this.top.setPointerCapture(e.pointerId);
    });
    this.top.addEventListener("pointermove", (e) => {
      const [x, y] = pos(e);
      if (down && Math.abs(x - down.x) > 3) {
        this.dragging = true;
        const dt = ((down.x - x) / this.plotW) * (down.view[1] - down.view[0]);
        const span = down.view[1] - down.view[0];
        const a = Math.max(0, Math.min(this.duration - span, down.view[0] + dt));
        this.view = [a, a + span];
        this.drawStatic();
      }
      if (x < GUTTER || x > GUTTER + this.plotW) { this.hover = null; this.tip.hidden = true; return; }
      this.hover = this.t(x);
      this.tooltip(x, y);
    });
    this.top.addEventListener("pointerup", (e) => {
      const [x] = pos(e);
      if (down && !this.dragging && x >= GUTTER && this.video) this.video.currentTime = Math.max(0, Math.min(this.duration, this.t(x)));
      down = null;
      this.dragging = false;
    });
    this.top.addEventListener("pointerleave", () => { this.hover = null; this.tip.hidden = true; });
    this.top.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        this.zoomBy(e.deltaY > 0 ? 1.25 : 0.8, this.t(pos(e)[0]));
      } else if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && this.view[1] - this.view[0] < this.duration) {
        e.preventDefault();
        const span = this.view[1] - this.view[0];
        const a = Math.max(0, Math.min(this.duration - span, this.view[0] + (e.deltaX / this.plotW) * span));
        this.view = [a, a + span];
        this.drawStatic();
      }
    }, { passive: false });
  }
}

function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}
