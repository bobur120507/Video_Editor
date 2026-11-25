"""After Effects: an ExtendScript (.jsx) that builds the edit as a comp. After Effects imports no timeline
format, so a script is the standard route: File > Scripts > Run Script File… and pick this file.

It imports the clips and the music (next to the script first, then at their original paths), makes a comp
at the edit's size and rate, and lays one layer per piece at its speed (layer stretch), framed like the
render. The edit's moves are baked as keyframes: punches and pushes on Scale, whips and shakes on Position
and Rotation (with motion blur on), flashes as a white solid fading out, other transitions as comp markers,
and texts as text layers with their pop or fade. The agent's own render goes on top as a switched-off guide
layer to compare against. ExtendScript is ES3: the data is a plain object literal, the code avoids newer JS.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .timeline import Flat, Media, Move, cover_offset, cover_scale, punch_scale

SHAKE_PX = 28.0  # position swing of a full-strength shake, pixels at 1080 wide


def _curves(moves: list[Move], start: int, n: int, W: int, H: int, fps: int) -> dict | None:
    """Per frame of one shot: scale multiplier, position offset and rotation from its moves (None if still)."""
    if not moves:
        return None
    k = np.arange(n, dtype=np.float64)
    mult, ox, oy, rot = np.ones(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for mv in moves:
        a = mv.at - start
        if mv.kind == "punch":
            peak = punch_scale(mv.strength) - 1
            rise = np.clip((k - a + 2) / 2, 0, 1)
            fall = np.where(k > a, np.exp(-(k - a) / max(2.0, mv.frames / 2.5)), 1.0)
            mult *= 1 + peak * rise * fall * (k >= a - 2)
        elif mv.kind == "push":
            t = np.clip(k - a, 0, mv.frames) / fps
            mult *= np.exp((mv.strength or 0.2) * t)
        elif mv.kind == "shake":
            live = (k >= a) & (k < a + mv.frames)
            decay = np.clip(1 - (k - a) / max(1, mv.frames), 0, 1) * live
            amp = SHAKE_PX * (W / 1080) * (mv.strength or 0.6)
            ox += amp * decay * np.sin(k * 2.7)
            oy += amp * decay * np.cos(k * 3.4) * 0.8
            rot += (mv.rot or 1.0) * decay * np.sin(k * 2.1)
        elif mv.kind in ("whip_in", "whip_out"):
            dx, dy = (mv.dx, mv.dy) if (mv.dx or mv.dy) else (1.0, 0.0)
            u = np.clip((k - a + 1) / max(1, mv.frames), 0, 1)
            if mv.kind == "whip_out":
                s = (u**2) * (k >= a)
                ox -= dx * W * s
                oy -= dy * H * s
            else:
                s = (1 - u) ** 2 * (k < a + mv.frames)
                ox += dx * W * s
                oy += dy * H * s
    return {"mult": mult, "ox": ox, "oy": oy, "rot": rot}


def write(flat: Flat, locate=lambda m: m.path, guide: str | None = None) -> str:
    fps, W, H = flat.fps, flat.width, flat.height
    media = {m.path: i for i, m in enumerate(flat.media)}

    def entry(m: Media) -> dict:
        return {"name": m.name, "rel": "media/" + m.name, "abs": locate(m)}

    by_shot: dict[int, list[Move]] = {}
    for mv in flat.moves:
        by_shot.setdefault(mv.shot, []).append(mv)
    layers = []
    for p in flat.pieces:
        s0, n = flat.shots[p.shot]
        base = cover_scale(p.media, W, H) * 100
        bx, by = cover_offset(p.media, W, H, p.center)
        layer = {"m": media[p.media.path], "start": p.start, "frames": p.frames, "src": p.src_in, "speed": p.speed,
                 "scale": round(base, 3), "pos": [round(W / 2 + bx, 2), round(H / 2 + by, 2)], "shot": p.shot + 1}
        c = _curves(by_shot.get(p.shot, []), s0, n, W, H, fps)
        if c is not None:
            lo, hi = p.start - s0, p.start - s0 + p.frames  # only the frames this piece shows
            idx = range(lo, hi)
            layer["keys"] = {
                "t": [round((s0 + i) / fps, 5) for i in idx],
                "scale": [round(base * float(c["mult"][i]), 3) for i in idx],
                "pos": [[round(W / 2 + bx + float(c["ox"][i]), 2), round(H / 2 + by + float(c["oy"][i]), 2)] for i in idx],
                "rot": [round(float(c["rot"][i]), 3) for i in idx],
                "blur": any(mv.kind.startswith("whip") or mv.kind == "shake" for mv in by_shot.get(p.shot, [])),
            }
        layers.append(layer)

    texts = []
    for t in flat.texts:
        rgb = t.color.lstrip("#")
        col = [int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4)] if len(rgb) == 6 else [1, 1, 1]
        texts.append({"text": t.text, "in": round(t.start, 4), "out": round(t.end, 4), "size": t.size, "color": col,
                      "y": {"top": 0.2, "center": 0.5, "bottom": 0.8}[t.position] * H, "style": t.style})

    data = {
        "name": flat.name, "w": W, "h": H, "fps": fps, "frames": flat.frames,
        "media": [entry(m) for m in flat.media],
        "music": entry(flat.music) if flat.music else None,
        "guide": {"name": Path(guide).name, "rel": "media/" + Path(guide).name, "abs": guide} if guide else None,
        "layers": layers, "texts": texts,
        "flashes": [{"at": at, "frames": f} for at, kind, f in flat.transitions if kind == "flash"],
        "markers": [{"at": at, "kind": kind} for at, kind, f in flat.transitions if kind != "flash"],
    }
    return HEADER + "var DATA = " + json.dumps(data, ensure_ascii=True, separators=(",", ":")) + ";\n" + SCRIPT


HEADER = """// Edit exported by Video Editor Agent.
// After Effects: File > Scripts > Run Script File... and pick this file. It builds the edit as a comp.
// Media is looked for in a "media" folder next to this script first, then at its original path.
"""

SCRIPT = r"""
(function () {
    var here = File($.fileName).parent;
    var missing = [];
    function find(m) {
        var f = new File(here.fsName + "/" + m.rel);
        if (f.exists) { return f; }
        f = new File(m.abs);
        if (f.exists) { return f; }
        missing.push(m.name);
        return null;
    }
    function bring(m, folder) {
        var f = find(m);
        if (!f) { return null; }
        var item = app.project.importFile(new ImportOptions(f));
        item.parentFolder = folder;
        return item;
    }
    app.beginUndoGroup("Import edit: " + DATA.name);
    var fps = DATA.fps;
    var folder = app.project.items.addFolder(DATA.name + " media");
    var items = [];
    for (var i = 0; i < DATA.media.length; i++) { items.push(bring(DATA.media[i], folder)); }
    var comp = app.project.items.addComp(DATA.name, DATA.w, DATA.h, 1, DATA.frames / fps, fps);
    comp.motionBlur = true;

    if (DATA.music) {
        var music = bring(DATA.music, folder);
        if (music) {
            var ml = comp.layers.add(music);
            ml.name = "Music";
            ml.startTime = 0;
            ml.outPoint = DATA.frames / fps;
        }
    }

    for (var j = 0; j < DATA.layers.length; j++) {
        var L = DATA.layers[j];
        var item = items[L.m];
        if (!item) { continue; }
        var layer = comp.layers.add(item);
        layer.name = "Shot " + L.shot + " - " + item.name;
        if (layer.hasAudio) { layer.audioEnabled = false; }
        layer.stretch = 100 / L.speed;
        layer.startTime = L.start / fps - L.src / L.speed;
        layer.inPoint = L.start / fps;
        layer.outPoint = (L.start + L.frames) / fps;
        var tr = layer.property("ADBE Transform Group");
        tr.property("ADBE Scale").setValue([L.scale, L.scale]);
        tr.property("ADBE Position").setValue(L.pos);
        if (L.keys) {
            var sv = [], pv = [];
            for (var k = 0; k < L.keys.t.length; k++) { sv.push([L.keys.scale[k], L.keys.scale[k]]); pv.push(L.keys.pos[k]); }
            tr.property("ADBE Scale").setValuesAtTimes(L.keys.t, sv);
            tr.property("ADBE Position").setValuesAtTimes(L.keys.t, pv);
            tr.property("ADBE Rotate Z").setValuesAtTimes(L.keys.t, L.keys.rot);
            if (L.keys.blur) { layer.motionBlur = true; }
        }
    }

    for (var f = 0; f < DATA.flashes.length; f++) {
        var fl = DATA.flashes[f];
        var solid = comp.layers.addSolid([1, 1, 1], "Flash", DATA.w, DATA.h, 1, fl.frames / fps);
        solid.startTime = fl.at / fps;
        var op = solid.property("ADBE Transform Group").property("ADBE Opacity");
        op.setValueAtTime(fl.at / fps, 100);
        op.setValueAtTime((fl.at + fl.frames) / fps, 0);
    }

    for (var t = 0; t < DATA.texts.length; t++) {
        var T = DATA.texts[t];
        var tl = comp.layers.addText(T.text);
        var src = tl.property("ADBE Text Properties").property("ADBE Text Document");
        var doc = src.value;
        doc.fontSize = T.size;
        doc.fillColor = T.color;
        doc.applyFill = true;
        doc.applyStroke = false;
        doc.justification = ParagraphJustification.CENTER_JUSTIFY;
        src.setValue(doc);
        tl.inPoint = T["in"];
        tl.outPoint = T.out;
        var ttr = tl.property("ADBE Transform Group");
        ttr.property("ADBE Position").setValue([DATA.w / 2, T.y]);
        if (T.style == "pop") {
            ttr.property("ADBE Scale").setValuesAtTimes([T["in"], T["in"] + 4 / fps, T["in"] + 7 / fps], [[0, 0], [112, 112], [100, 100]]);
        } else if (T.style == "fade") {
            ttr.property("ADBE Opacity").setValuesAtTimes([T["in"], T["in"] + 8 / fps, T.out - 8 / fps, T.out], [0, 100, 100, 0]);
        }
    }

    for (var m = 0; m < DATA.markers.length; m++) {
        comp.markerProperty.setValueAtTime(DATA.markers[m].at / fps, new MarkerValue(DATA.markers[m].kind + " transition"));
    }

    if (DATA.guide) {
        var g = bring(DATA.guide, folder);
        if (g) {
            var gl = comp.layers.add(g);
            gl.name = "Agent render (guide, switched off)";
            gl.guideLayer = true;
            gl.enabled = false;
            if (gl.hasAudio) { gl.audioEnabled = false; }
        }
    }

    app.endUndoGroup();
    comp.openInViewer();
    if (missing.length) {
        alert("Built the comp, but these files weren't found (put them in a \"media\" folder next to the script, or relink):\n" + missing.join("\n"));
    }
})();
"""

