// Web UI over the same pipeline as the Telegram bot: /clone -> move review -> template -> /apply -> score.
import { AudioGraph } from "/static/graph.js";

const view = document.getElementById("view");
const RATINGS = [
  ["timing", [["🎵 On the beat", 1], ["🎵 Off the beat", -1]]],
  ["shake", [["Shake: too little", -1], ["Shake: right", 0], ["Shake: too much", 1]]],
  ["smooth", [["〰️ Smooth", 1], ["〰️ Jerky", -1]]],
  ["quality", [["🖼 Sharp", 1], ["🖼 Blurry/low quality", -1]]],
];
const ON_SCREEN = 0.5; // seconds a move's label stays over the video, as on the bot's annotated preview

// ---------------------------------------------------------------- helpers

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = opts.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body?.error || body || res.statusText);
  return body;
}

function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(kid));
  return e;
}

function status(text, kind = "") {
  return h("p", { class: `status ${kind}`, role: kind === "error" ? "alert" : "status" }, text);
}

async function poll(jobId, onStep) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    onStep?.(job);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function page(title, ...kids) {
  view.replaceChildren(h("h1", {}, title), ...kids);
}

function setNav(name) {
  for (const a of document.querySelectorAll("[data-nav]")) a.classList.toggle("active", a.dataset.nav === name);
}

// A video with move labels drawn over it while it plays, so moves can be reviewed without rendering a preview.
function player(src, moves) {
  const video = h("video", { src, controls: true, playsinline: true, preload: "auto" });
  const labels = h("div", { class: "move-labels", "aria-hidden": "true" });
  const box = h("div", { class: "player" }, video, labels);
  let last = "";
  const tick = () => {
    if (!box.isConnected) return;
    const t = video.currentTime;
    const on = (moves || []).filter((m) => t >= m.t - 0.05 && t <= m.t + ON_SCREEN);
    const key = on.map((m) => m.n).join(",");
    if (key !== last) {
      labels.replaceChildren(...on.map((m) => h("span", {}, `#${m.n} ${m.screen}${m.detail ? " " + m.detail : ""}`)));
      last = key;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return { box, video };
}

async function graphSection(bp, video, onBackfill) {
  const root = h("section", { class: "card graph-card" }, h("h2", {}, "Sound"));
  const holder = h("div");
  root.append(holder);
  let graph = null;
  if (bp.has_graph) {
    graph = await api(`/api/blueprints/${bp.id}/graph`);
  } else if (bp.has_audio || bp.video) {
    const note = status("This template was cloned before the audio graph was stored. Only its markers are shown.");
    const btn = h("button", { class: "btn" }, "Analyse the sound once and store it");
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const { job } = await api(`/api/blueprints/${bp.id}/graph`, { method: "POST" });
        if (job) await poll(job, (j) => (note.textContent = j.step || "…"));
        onBackfill();
      } catch (e) {
        note.replaceWith(status(e.message, "error"));
      }
    };
    root.insertBefore(h("div", { class: "row" }, note, btn), holder);
  }
  new AudioGraph(holder, { graph, markers: bp.markers, duration: bp.duration, video });
  const m = bp.markers;
  root.append(h("p", { class: "hint" },
    `${m.bpm ? m.bpm.toFixed(0) + " BPM · " : ""}${m.beats.length} beats · ${m.hits.length} hits` +
    `${m.sync_to ? ` (cuts follow the ${m.sync_to})` : ""} · ${m.drops.length} drops · ${m.cuts.length} cuts · ${m.moves.length} moves`));
  return root;
}

function movesTable(bp, video) {
  if (!bp.markers.moves.length) return h("p", { class: "hint" }, "No edit moves found (only cuts).");
  const rows = bp.markers.moves.map((m) =>
    h("tr", { tabindex: 0, onclick: () => (video.currentTime = Math.max(0, m.t - 0.3)), onkeydown: (e) => e.key === "Enter" && (video.currentTime = Math.max(0, m.t - 0.3)) },
      h("td", { class: "num" }, `#${m.n}`), h("td", { class: "num" }, `${m.t.toFixed(2)}s`), h("td", {}, m.label + (m.detail ? ` (${m.detail})` : ""))));
  return h("table", { class: "moves" }, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Time"), h("th", {}, "Move"))), h("tbody", {}, rows));
}

// ---------------------------------------------------------------- clone

function cloneView() {
  setNav("clone");
  const file = h("input", { type: "file", name: "video", accept: "video/*,.json,.aep", id: "clone-file" });
  const url = h("input", { type: "url", name: "url", placeholder: "https://…", id: "clone-url" });
  const out = h("div");
  const submit = h("button", { class: "btn primary", type: "submit" }, "Clone");
  const form = h("form", { class: "card stack" },
    h("p", {}, "Send the video whose editing style you want to copy. Its cuts, beats, moves, colour and sound are saved as a template."),
    h("label", { for: "clone-file" }, "Video file, or an After Effects project (.aep) or Bodymovin export (.json)"), file,
    h("label", { for: "clone-url" }, "…or a link"), url,
    h("div", { class: "row" }, submit), out);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const data = new FormData();
    if (file.files[0]) data.append("video", file.files[0]);
    else if (url.value.trim()) data.append("url", url.value.trim());
    else return out.replaceChildren(status("Please choose a video file or paste a link to one.", "error"));
    submit.disabled = true;
    out.replaceChildren(status("⏳ Uploading…"));
    try {
      const { job } = await api("/api/clone", { method: "POST", body: data });
      const done = await poll(job, (j) => out.replaceChildren(status(j.step || "…")));
      if (done.status === "failed") throw new Error(`Could not analyze that video: ${done.error}`);
      location.hash = `#/bp/${done.result.blueprint_id}?review=1`;
    } catch (err) {
      out.replaceChildren(status(`❌ ${err.message}`, "error"));
      submit.disabled = false;
    }
  };
  page("Clone a reference", form);
}

// ---------------------------------------------------------------- blueprint (review)

async function blueprintView(id, params) {
  setNav(params.get("review") ? "clone" : "templates");
  page("Loading…");
  const [bp, templates] = await Promise.all([api(`/api/blueprints/${id}`), api("/api/templates")]);
  const tpl = templates.find((t) => t.blueprint_id === bp.id);
  const { box, video } = player(bp.video, bp.markers.moves);
  if (!bp.video) box.replaceChildren(status(bp.imported ? "Imported from After Effects: there is no reference video. The cuts, camera keyframes, time remaps and text come from the comp." : "The reference video is no longer on disk."));

  const side = h("section", { class: "card stack side" }, h("h2", {}, "Editing DNA"), h("pre", { class: "summary" }, bp.summary));
  for (const n of bp.notes.filter((n) => n.startsWith("⚠️"))) side.append(status(n, "warn"));
  side.append(h("h3", {}, "Moves"), movesTable(bp, video));

  const msg = h("div");
  const fix = h("input", { type: "text", id: "fix", placeholder: "remove 3 5 · 4 shake · add 2.4 punch" });
  const fixForm = h("form", { class: "stack" },
    h("label", { for: "fix" }, "Fix the moves in plain words"),
    h("div", { class: "row" }, fix, h("button", { class: "btn", type: "submit" }, "Apply fix")),
    h("p", { class: "hint" }, "Kinds: shake, punch, whip/slide, flash, slow zoom, slow-mo, panel, portal, cut-out, zoom-through, silhouette, glow. Times are on the graph and the video."),
    msg);
  fixForm.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/api/blueprints/${bp.id}/moves`, { json: { text: fix.value } });
      sessionStorage.setItem("fixed", "Got it: " + r.done.join("; "));
      route();
    } catch (err) {
      msg.replaceChildren(status(err.message, "error"));
    }
  };
  side.append(fixForm);
  const fixed = sessionStorage.getItem("fixed");
  if (fixed) { msg.replaceChildren(status(fixed, "ok")); sessionStorage.removeItem("fixed"); }

  if (tpl) {
    side.append(h("div", { class: "row" }, h("a", { class: "btn primary", href: `#/apply?tpl=${tpl.id}` }, "▶️ Apply to new media")));
  } else {
    const name = h("input", { type: "text", id: "tpl-name", placeholder: "car_phonk_v1", required: true });
    const saveMsg = h("div");
    const save = h("form", { class: "stack" },
      h("label", { for: "tpl-name" }, "Moves right? Name this template"),
      h("div", { class: "row" }, name, h("button", { class: "btn primary", type: "submit" }, "✅ All correct, save")), saveMsg);
    save.onsubmit = async (e) => {
      e.preventDefault();
      try {
        if (params.get("review")) await api(`/api/blueprints/${bp.id}/moves/ok`, { method: "POST" });
        const t = await api("/api/templates", { json: { blueprint_id: bp.id, name: name.value } });
        location.hash = `#/bp/${bp.id}`;
        saveMsg.replaceChildren(status(`Saved as “${t.name}”.`, "ok"));
      } catch (err) {
        saveMsg.replaceChildren(status(err.message, "error"));
      }
    };
    side.append(save);
  }

  const layout = h("div", { class: "layout" }, h("div", { class: "card video-card" }, box), side);
  page(tpl ? tpl.name : "Review the moves", layout, await graphSection(bp, video, route));
}

// ---------------------------------------------------------------- templates

async function templatesView() {
  setNav("templates");
  const rows = await api("/api/templates");
  if (!rows.length) return page("Templates", h("p", {}, "No templates yet. ", h("a", { href: "#/clone" }, "Clone a video"), " to create one."));
  const list = h("ul", { class: "templates" }, rows.map((t) => {
    const msg = h("span");
    const rename = async () => {
      const name = prompt("New name:", t.name);
      if (!name) return;
      try { await api(`/api/templates/${t.id}`, { method: "PATCH", json: { name } }); route(); } catch (e) { msg.replaceChildren(status(e.message, "error")); }
    };
    const del = async () => {
      if (!confirm(`Delete the template “${t.name}”?`)) return;
      try { await api(`/api/templates/${t.id}`, { method: "DELETE" }); route(); } catch (e) { msg.replaceChildren(status(e.message, "error")); }
    };
    return h("li", { class: "card row" },
      h("div", { class: "grow" }, h("a", { href: `#/bp/${t.blueprint_id}`, class: "tpl-name" }, t.name), h("div", { class: "hint" }, `used ${t.usage}x`)),
      h("a", { class: "btn primary", href: `#/apply?tpl=${t.id}` }, "▶️ Apply"),
      h("button", { class: "btn", onclick: rename }, "✏️ Rename"),
      h("button", { class: "btn", onclick: del }, "🗑 Delete"), msg);
  }));
  page("Templates", list);
}

// ---------------------------------------------------------------- apply

async function applyView(params) {
  setNav("apply");
  const rows = await api("/api/templates");
  if (!rows.length) return page("Apply a template", h("p", {}, "No templates yet. ", h("a", { href: "#/clone" }, "Clone a video"), " first."));
  const pick = h("select", { name: "template_id", id: "tpl" }, rows.map((t) => h("option", { value: t.id, selected: String(t.id) === params.get("tpl") }, t.name)));
  const clips = h("input", { type: "file", name: "clips", accept: "video/*", multiple: true, id: "clips", required: true });
  const text = h("textarea", { name: "text", id: "text", rows: 3, placeholder: "POV: day 1" });
  const tweak = h("input", { type: "text", name: "tweak", id: "tweak", placeholder: "darker, faster cuts, more zoom" });
  const out = h("div");
  const submit = h("button", { class: "btn primary", type: "submit" }, "🎞 Render");
  const form = h("form", { class: "card stack" },
    h("label", { for: "tpl" }, "Template"), pick,
    h("label", { for: "clips" }, "Your raw clips (several is best)"), clips,
    h("label", { for: "text" }, "On-screen text, one caption per line (the first is the hook). Optional."), text,
    h("label", { for: "tweak" }, "Changes. Optional."), tweak,
    h("div", { class: "row" }, submit), out);
  form.onsubmit = async (e) => {
    e.preventDefault();
    submit.disabled = true;
    out.replaceChildren(status("⏳ Uploading clips…"));
    try {
      const { job } = await api("/api/apply", { method: "POST", body: new FormData(form) });
      location.hash = `#/render/${job}`;
    } catch (err) {
      out.replaceChildren(status(`❌ ${err.message}`, "error"));
      submit.disabled = false;
    }
  };
  page("Apply a template", form);
}

// ---------------------------------------------------------------- render result

function scoreCard(score) {
  if (!score) return null;
  return h("section", { class: "card stack" }, h("h2", {}, `📏 Match: ${score.overall.toFixed(1)}%`),
    h("table", { class: "score" }, h("tbody", {}, Object.entries(score.parts).map(([k, v]) =>
      h("tr", {}, h("th", {}, k), h("td", { class: "bar-cell" }, h("span", { class: "bar", style: `--v:${Math.max(0, Math.min(100, v))}%` })), h("td", { class: "num" }, `${v.toFixed(0)}%`))))));
}

async function renderView(jobId) {
  setNav("apply");
  const out = h("div");
  page("Rendering", out);
  let job = await api(`/api/jobs/${jobId}`);
  while (job.status === "running" && !job.result.video) {
    out.replaceChildren(status(job.step || "…"));
    await new Promise((r) => setTimeout(r, 1000));
    job = await api(`/api/jobs/${jobId}`);
  }
  if (job.status === "failed") return out.replaceChildren(status(`❌ Render failed: ${job.error}`, "error"));
  const r = job.result;
  const bp = await api(`/api/blueprints/${r.blueprint_id}`);
  const { box, video } = player(r.video, bp.markers.moves);

  const scoreBox = h("div", {}, status(job.step || "Measuring the match…"));
  const side = h("section", { class: "card stack side" },
    h("h2", {}, `✅ v${r.version} · ${r.duration.toFixed(1)}s`),
    h("div", { class: "row" }, h("a", { class: "btn", href: r.video, download: true }, "Download master")),
    r.notes.map((n) => h("p", { class: "note" }, n)),
    h("div", { class: "thumbs" }, r.cover && h("figure", {}, h("img", { src: r.cover, alt: "Cover frame" }), h("figcaption", {}, "Cover")),
      r.grid && h("figure", {}, h("img", { src: r.grid, alt: "1:1 grid crop" }), h("figcaption", {}, "1:1 grid preview"))),
    scoreBox);

  const change = h("input", { type: "text", id: "change", placeholder: "darker, faster cuts, more zoom, bigger text" });
  const changeMsg = h("div");
  const changeForm = h("form", { class: "stack" }, h("label", { for: "change" }, "Want changes? Type them"),
    h("div", { class: "row" }, change, h("button", { class: "btn", type: "submit" }, "Re-render")), changeMsg);
  changeForm.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const res = await api(`/api/renders/${jobId}/change`, { json: { text: change.value } });
      location.hash = `#/render/${res.job}`;
    } catch (err) {
      changeMsg.replaceChildren(status(err.message, "error"));
    }
  };
  const rateMsg = h("div");
  const rate = h("div", { class: "stack" }, h("h3", {}, "How is it? One tap per row"),
    RATINGS.map(([aspect, choices]) => h("div", { class: "row" }, choices.map(([label, value]) =>
      h("button", { class: "btn small", onclick: async (e) => {
        try {
          await api(`/api/renders/${jobId}/rate`, { json: { aspect, value } });
          for (const b of e.target.parentElement.children) b.classList.toggle("chosen", b === e.target);
          rateMsg.replaceChildren(status("Thanks, noted 👍", "ok"));
        } catch (err) { rateMsg.replaceChildren(status(err.message, "error")); }
      } }, label)))), rateMsg);
  side.append(changeForm, rate);

  const layout = h("div", { class: "layout" }, h("div", { class: "card video-card" }, box), side);
  page(`Your edit`, layout, await graphSection(bp, video, route));

  const final = job.status === "running" ? await poll(jobId) : job;
  scoreBox.replaceChildren(scoreCard(final.result.score) || status("No match score (the reference video is not on disk)."));
}

// ---------------------------------------------------------------- router

async function route() {
  const [path, query = ""] = location.hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  try {
    if (parts[0] === "bp" && parts[1]) await blueprintView(parts[1], params);
    else if (parts[0] === "templates") await templatesView();
    else if (parts[0] === "apply") await applyView(params);
    else if (parts[0] === "render" && parts[1]) await renderView(parts[1]);
    else cloneView();
  } catch (e) {
    page("Something went wrong", status(e.message, "error"));
  }
}

addEventListener("hashchange", route);
route();
