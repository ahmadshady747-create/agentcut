"""Unified browser-rendered video engine for AgentCut (v3.4.0).

Renders ALL AgentCut video formats — vertical 9:16 reels, landscape 16:9
long-form explainers, and square 1:1 social videos — entirely in headless
Chromium (HTML/CSS/SVG/JS) driven deterministically by window.setTime(t).
FFmpeg is used ONLY as a dumb encoder (encode_frames) and audio mixer
(mix_audio) — zero FFmpeg video filters, drawtext, or zoompan anywhere.

Features:
- 4 palettes: cream, dark, editorial, midnight
- 3 format presets: vertical (720x1280), landscape (1280x720), square (1080x1080)
- 15 scene templates: intro, question, concept, process, statistic,
  comparison, conclusion, timeline, cycle, layers, callout, chapter,
  code, split, checklist
- 66 hand-drawn flat-editorial SVG icons with deterministic inner motion
- Continuous setTime-driven ambient motion (drifting paper grid, floating
  geometric particles, breathing icons, rocking badges, numeric count-ups,
  narration-coupled sequential highlighting, smooth scene transitions)
- Audio discipline: real-audio pace measurement (measure_narration_pace),
  never speeds up voice, optional room tone + auto-SFX + sidechain-ducked
  ambient music bed + -14 LUFS linear loudnorm.
"""
from __future__ import annotations

import base64
import html as _html
import json
import os
import re

from .beat_sync import build_scene_beat_profile
from .brand_kits import BRAND_KITS, apply_brand_kit
from .browser_render import (
    PLAYWRIGHT_AVAILABLE,
    audit_audio_mix,
    get_fast_scratch_dir,
    mix_audio,
    probe_audio_duration,
    render_browser_page,
)
from .scene_patch import compute_spec_scene_manifest
from .visual_qc import audit_html_motion_continuity
from .captions import CAPTION_STYLES, balance_caption_chunks, render_caption_html
from .charts import build_donut_chart_svg, build_grouped_bars_svg, build_line_chart_svg
from .icons import (
    ICONS,
    ILLUSTRATIONS,
    icon_svg,
    illustration_svg,
    list_icons,
    list_illustrations,
    search_icons,
)

PALETTES: dict[str, dict[str, str]] = {
    "cream": {
        "paper": "#F7F0DC",
        "card": "#FFFDF5",
        "ink": "#221E17",
        "accent": "#E4572E",
        "blue": "#2E86AB",
        "muted": "#8A8171",
        "marker": "#FFD23F",
        "line": "#E3D9BE",
    },
    "dark": {
        "paper": "#0C1014",
        "card": "#161C22",
        "ink": "#F4F3EE",
        "accent": "#27B8C7",
        "blue": "#5978FF",
        "muted": "#8A94A6",
        "marker": "#FFD23F",
        "line": "#232B33",
    },
    "editorial": {
        "paper": "#FAF8F2",
        "card": "#FFFFFF",
        "ink": "#18181B",
        "accent": "#D62828",
        "blue": "#1D4ED8",
        "muted": "#71717A",
        "marker": "#FACC15",
        "line": "#E4E2D8",
    },
    "midnight": {
        "paper": "#0B1120",
        "card": "#131C31",
        "ink": "#F1F5F9",
        "accent": "#F59E0B",
        "blue": "#38BDF8",
        "muted": "#94A3B8",
        "marker": "#FDE047",
        "line": "#1E293B",
    },
}

FORMATS: dict[str, tuple[int, int]] = {
    "vertical": (720, 1280),
    "reel": (720, 1280),
    "landscape": (1280, 720),
    "explainer": (1280, 720),
    "youtube": (1280, 720),
    "square": (1080, 1080),
}

SCENE_TYPES = (
    "intro", "question", "concept", "process",
    "statistic", "comparison", "conclusion",
    "timeline", "cycle", "layers", "callout",
    "chapter", "code", "split", "checklist",
    "chart_line", "chart_donut", "chart_bars",
    "before_after", "funnel", "matrix_2x2", "terminal_demo", "chapter_toc",
)
LAYOUTS = ("body", "zoom", "flow", "cycle", "timeline", "chart",
           "layers", "cards", "scale")
TRANSITIONS = ("cut", "fade", "slide_up", "zoom_in", "wipe_rtl", "iris_reveal")
PHYSICS_MODES = ("back", "spring", "elastic")
CAMERA_MOVES = ("none", "push_in", "pull_out", "pan_rtl", "pan_ltr", "drift")
MOTION_PRESETS: dict[str, float] = {
    "calm": 0.55,
    "normal": 1.0,
    "energetic": 1.45,
}

_SENTENCE_END = re.compile(r"(?<=[.؟!])\s+")
_NUM_RE = re.compile(r"^([^0-9\u0660-\u0669]*)([0-9]+(?:\.[0-9]+)?)(.*)$")


def resolve_dimensions(spec: dict) -> tuple[int, int, str]:
    """Resolve (width, height, format_class) from spec."""
    fmt = str(spec.get("format", "")).strip().lower()
    if fmt in FORMATS:
        default_w, default_h = FORMATS[fmt]
    else:
        default_w, default_h = 720, 1280
    w = int(spec.get("width", default_w))
    h = int(spec.get("height", default_h))
    if w > h:
        cls = "landscape"
    elif w == h:
        cls = "square"
    else:
        cls = "vertical"
    return w, h, cls


def split_sentences(text: str) -> list[str]:
    """Split caption text into sentence chunks (for chunked captions)."""
    parts = [p.strip() for p in _SENTENCE_END.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


def even_word_timings(words: list[str], start: float,
                      end: float) -> list[dict]:
    """Fallback word timings: even split across [start, end)."""
    if not words or end <= start:
        return []
    step = (end - start) / len(words)
    return [{"word": w, "start": round(start + i * step, 3),
             "end": round(start + (i + 1) * step, 3)}
            for i, w in enumerate(words)]


def load_words(path: str | None, text: str, start: float,
               end: float) -> list[dict]:
    """Load per-scene word timings, aligned to the script words."""
    script_words = text.split()
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            items = data if isinstance(data, list) else data.get("words", [])
            timed = [{"word": str(it["word"]),
                      "start": round(start + float(it["start"]), 3),
                      "end": round(start + float(it["end"]), 3)}
                     for it in items
                     if "word" in it and "start" in it and "end" in it]
            if [t["word"] for t in timed] == script_words and timed:
                return timed
        except (ValueError, KeyError, TypeError, OSError):
            pass
    return even_word_timings(script_words, start, end)


def measure_narration_pace(spec: dict, timings: list[dict],
                           min_wps: float = 1.4,
                           max_wps: float = 2.4,
                           target_duration: float | None = None
                           ) -> dict:
    """Measure real narration pace (words/sec) per scene and overall.

    Never assumes a fixed speech rate: measures actual audio durations
    against script word counts. When total duration exceeds
    target_duration, recommends trimming the script (never speeding up
    audio).
    """
    scenes_by_id = {sc["id"]: sc for sc in spec.get("scenes", [])}
    per_scene: list[dict] = []
    total_words = 0
    total_dur = 0.0
    warnings: list[str] = []

    for tm in timings:
        sc = scenes_by_id.get(tm["id"], {})
        words = len(str(sc.get("text", "")).split())
        dur = float(tm.get("duration", 0.0))
        wps = round(words / dur, 2) if dur > 0 else 0.0
        total_words += words
        total_dur += dur
        status = "ok"
        if dur > 0 and wps < min_wps:
            status = "slow"
            warnings.append(
                f"scene {tm['id']}: slow pace ({wps} wps < {min_wps})")
        elif dur > 0 and wps > max_wps:
            status = "fast"
            warnings.append(
                f"scene {tm['id']}: fast pace ({wps} wps > {max_wps})")
        per_scene.append({
            "id": tm["id"],
            "words": words,
            "duration": round(dur, 3),
            "wps": wps,
            "status": status,
        })

    overall_wps = round(total_words / total_dur, 2) if total_dur > 0 else 0.0
    over_by = None
    recommended_trim_words = 0
    if target_duration is not None and total_dur > target_duration:
        over_by = round(total_dur - target_duration, 2)
        if overall_wps > 0:
            recommended_trim_words = max(1, int(round(over_by * overall_wps)))
        warnings.append(
            f"total duration {round(total_dur, 2)}s exceeds target "
            f"{target_duration}s by {over_by}s — trim ~{recommended_trim_words} "
            "words from script instead of speeding up audio")

    return {
        "total_words": total_words,
        "total_duration": round(total_dur, 3),
        "overall_wps": overall_wps,
        "per_scene": per_scene,
        "over_target_by": over_by,
        "recommended_trim_words": recommended_trim_words,
        "warnings": warnings,
    }


def validate_reel_spec(spec: dict) -> tuple[bool, list[str]]:
    """Validate a reel / explainer spec dict. Returns (ok, errors)."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return False, ["reel spec must be a JSON object"]
    if not str(spec.get("id", "")).strip():
        errors.append("reel id is required")
    palette = spec.get("palette", "cream")
    if palette not in PALETTES:
        errors.append(f"unknown palette {palette!r}; "
                      f"choose from: {', '.join(sorted(PALETTES))}")
    fmt = spec.get("format")
    if fmt is not None and str(fmt).strip().lower() not in FORMATS:
        errors.append(f"unknown format {fmt!r}; "
                      f"choose from: {', '.join(sorted(FORMATS))}")
    motion = spec.get("motion", "normal")
    if motion not in MOTION_PRESETS:
        errors.append(f"unknown motion preset {motion!r}; "
                      f"choose from: {', '.join(sorted(MOTION_PRESETS))}")
    transition = spec.get("transition", "fade")
    if transition not in TRANSITIONS:
        errors.append(f"unknown transition {transition!r}; "
                      f"choose from: {', '.join(TRANSITIONS)}")
    bkit = spec.get("brand_kit")
    if isinstance(bkit, str) and bkit.strip().lower() not in BRAND_KITS:
        errors.append(f"unknown brand_kit {bkit!r}; "
                      f"choose from: {', '.join(sorted(BRAND_KITS))}")
    spec = apply_brand_kit(spec)
    cap_style = spec.get("caption_style", "word_pop")
    if cap_style not in CAPTION_STYLES:
        errors.append(f"unknown caption_style {cap_style!r}; "
                      f"choose from: {', '.join(CAPTION_STYLES)}")
    phys = spec.get("physics", "spring")
    if phys not in PHYSICS_MODES:
        errors.append(f"unknown physics {phys!r}; "
                      f"choose from: {', '.join(PHYSICS_MODES)}")
    cam = spec.get("camera_move", "none")
    if cam not in CAMERA_MOVES:
        errors.append(f"unknown camera_move {cam!r}; "
                      f"choose from: {', '.join(CAMERA_MOVES)}")
    scenes = spec.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("reel needs at least one scene")
        return False, errors
    seen: set[str] = set()
    for i, sc in enumerate(scenes):
        where = f"scene {i}"
        if not isinstance(sc, dict):
            errors.append(f"{where}: not an object")
            continue
        sid = str(sc.get("id", "")).strip()
        if not sid:
            errors.append(f"{where}: id is required")
        elif sid in seen:
            errors.append(f"duplicate scene id {sid!r}")
        seen.add(sid)
        if sc.get("type") not in SCENE_TYPES:
            errors.append(f"scene {sid or i}: unknown type "
                          f"{sc.get('type')!r}")
        if sc.get("layout", "body") not in LAYOUTS:
            errors.append(f"scene {sid or i}: unknown layout "
                          f"{sc.get('layout')!r}")
        tr = sc.get("transition")
        if tr is not None and tr not in TRANSITIONS:
            errors.append(f"scene {sid or i}: unknown transition {tr!r}")
        sc_cam = sc.get("camera_move")
        if sc_cam is not None and sc_cam not in CAMERA_MOVES:
            errors.append(f"scene {sid or i}: unknown camera_move {sc_cam!r}")
        if not str(sc.get("narration", "")).strip():
            errors.append(f"scene {sid or i}: narration is required")
        if not str(sc.get("text", "")).strip():
            errors.append(f"scene {sid or i}: text is required")
    return (not errors), errors


def compute_scene_timings(spec: dict, base_dir: str
                          ) -> tuple[list[dict], float, list[str]]:
    """Scene boundaries from narration audio durations."""
    timings: list[dict] = []
    errors: list[str] = []
    cursor = 0.0
    for sc in spec.get("scenes", []):
        narration = os.path.join(base_dir, sc["narration"])
        dur = probe_audio_duration(narration)
        if dur is None:
            errors.append(f"scene {sc['id']}: cannot probe "
                          f"narration {sc['narration']!r}")
            return [], 0.0, errors
        words_path = sc.get("words")
        words_full = (os.path.join(base_dir, words_path)
                      if words_path else None)
        words = load_words(words_full, sc["text"], cursor, cursor + dur)
        timings.append({"id": sc["id"], "start": round(cursor, 3),
                        "duration": round(dur, 3), "words": words})
        cursor += dur
    return timings, round(cursor, 3), errors


# ---------------------------------------------------------------------------
# HTML / CSS / JS builders
# ---------------------------------------------------------------------------

def _font_face_css(embed_font: bool = True) -> str:
    """@font-face for Cairo (OFL), embedded as base64 when asked."""
    if not embed_font:
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "fonts", "Cairo.ttf")
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return ("@font-face{font-family:'Cairo';font-style:normal;"
            "font-weight:400 900;font-display:swap;"
            "src:url(data:font/ttf;base64," + b64 + ") format('truetype');}")


def _css(pal: dict[str, str], width: int, height: int) -> str:
    """Stylesheet for vertical, landscape, and square browser renders.

    Zero CSS animations or transitions anywhere: every visual depends
    on t only, driven deterministically by window.setTime(t).
    """
    raw = """
:root{--paper:PAPER;--card:CARD;--ink:INK;--accent:ACCENT;--blue:BLUE;
--muted:MUTED;--marker:MARKER;--line:LINE}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{background:var(--paper);color:var(--ink);
font-family:'Cairo',sans-serif;direction:rtl}
#stage{position:relative;width:__W__px;height:__H__px;overflow:hidden}
.paper-grid{position:absolute;inset:-40px;
background-image:radial-gradient(var(--line) 1.7px,transparent 1.7px);
background-size:30px 30px;opacity:.6;pointer-events:none}
.bg-motif{position:absolute;pointer-events:none;z-index:1}
.bg-blob-a{top:12%;right:-55px;width:240px;height:240px;border-radius:50%;
border:3px dashed var(--line);opacity:.55}
.bg-blob-b{bottom:14%;left:-45px;width:210px;height:210px;border-radius:36px;
border:3px solid var(--line);opacity:.45}
.bg-dots{top:22%;left:28px;width:96px;height:96px;opacity:.35}
.bg-particle{position:absolute;pointer-events:none;z-index:2;opacity:.45}
.bg-corner-tl{position:absolute;top:110px;left:28px;width:36px;height:36px;
border-top:3px solid var(--line);border-left:3px solid var(--line);
border-radius:8px 0 0 0;opacity:.65;pointer-events:none;z-index:2}
.bg-corner-br{position:absolute;bottom:140px;right:28px;width:36px;height:36px;
border-bottom:3px solid var(--line);border-right:3px solid var(--line);
border-radius:0 0 8px 0;opacity:.65;pointer-events:none;z-index:2}
#progress{position:absolute;top:0;left:0;right:0;height:10px;
background:var(--line);z-index:50}
#progressbar{height:100%;background:var(--accent);width:0}
.scene{position:absolute;inset:0;padding:44px 42px 38px;display:none;
flex-direction:column;z-index:10}
.scene.active{display:flex}
.tagrow{display:flex;justify-content:space-between;align-items:center;
position:relative;z-index:5}
.tag{background:var(--accent);color:var(--paper);padding:9px 22px;
border-radius:999px;font-size:26px;font-weight:800;border:3px solid var(--ink);
box-shadow:4px 4px 0 var(--ink)}
.scenenum{color:var(--muted);font-size:23px;font-weight:700;
direction:ltr;unicode-bidi:embed}
h1.title{font-size:52px;line-height:1.3;font-weight:900;margin-top:20px;
position:relative;z-index:5}
.subtitle{font-size:30px;color:var(--muted);line-height:1.45;margin-top:8px;
position:relative;z-index:5}
.visual{flex:1;display:flex;align-items:center;justify-content:center;
min-height:0;padding:28px 0 18px;position:relative;z-index:5}
.art-card{position:relative;width:100%;background:var(--card);
border:3px solid var(--ink);border-radius:34px;
box-shadow:10px 10px 0 var(--ink);padding:38px 28px;
display:flex;flex-direction:column;align-items:center;gap:18px;
text-align:center}
.icon-stage{position:relative;display:flex;align-items:center;
justify-content:center;padding:12px}
.icon-halo{position:absolute;width:206px;height:206px;border-radius:50%;
background:var(--paper);border:2.5px dashed var(--line);z-index:0}
.icon-stage svg{position:relative;z-index:2}
.kb-wrap{width:100%;max-height:360px;border-radius:22px;overflow:hidden;
border:3px solid var(--ink)}
.kb-img{width:100%;height:100%;object-fit:cover;display:block}
.art-card .art-label{font-size:34px;font-weight:900;line-height:1.38}
.art-card .art-sub{font-size:28px;color:var(--muted);line-height:1.48}
.badge{position:absolute;top:-32px;left:-14px;width:118px;height:118px;
border-radius:50%;background:var(--marker);border:3px solid var(--ink);
box-shadow:4px 4px 0 var(--ink);display:flex;align-items:center;
justify-content:center;font-size:42px;font-weight:900;z-index:8}
.caption{margin-top:18px;min-height:136px;position:relative;z-index:6}
.cap-chunk{display:none;font-size:42px;font-weight:700;line-height:1.5;
text-align:center}
.cap-chunk.on{display:block}
.w{opacity:.38}
.w.done{opacity:1}
.w.lit{opacity:1;color:var(--accent);display:inline-block;
transform:scale(1.13);font-weight:900}
.w[data-latin="1"]{direction:ltr;unicode-bidi:isolate;font-weight:800}
.w[data-kw="1"].done{color:var(--accent);font-weight:900}
.cap-style-highlighter_bar .w.lit{color:var(--ink);background:var(--marker);
padding:1px 12px;border-radius:10px;border:2.5px solid var(--ink);
box-shadow:3px 3px 0 var(--ink);transform:scale(1.08)}
.cap-style-karaoke_pill .w.lit{color:var(--paper);background:var(--accent);
padding:2px 14px;border-radius:999px;border:2.5px solid var(--ink);
transform:scale(1.09)}
.cap-style-editorial_box{background:var(--card);border:3px solid var(--ink);
border-radius:24px;padding:14px 20px;box-shadow:6px 6px 0 var(--ink)}
/* Vector SVG charts (line, donut, grouped bars) */
.vector-chart{overflow:visible;display:block}
.donut-layout{width:100%;display:flex;flex-direction:column;
align-items:center;gap:20px}
.donut-ring-wrap{position:relative;display:flex;align-items:center;
justify-content:center}
.donut-center{position:absolute;inset:0;display:flex;flex-direction:column;
align-items:center;justify-content:center;text-align:center;pointer-events:none}
.donut-center-val.bigstat{font-size:74px;line-height:1}
.donut-center-lbl{font-size:23px;font-weight:800;color:var(--muted);margin-top:4px}
.donut-legend{width:100%;display:flex;flex-wrap:wrap;justify-content:center;gap:12px}
.donut-leg-item{display:flex;align-items:center;gap:8px;background:var(--paper);
border:2.5px solid var(--ink);border-radius:999px;padding:6px 16px;
font-size:23px;font-weight:800}
.donut-swatch{width:16px;height:16px;border-radius:50%;
border:2px solid var(--ink);display:inline-block}
.donut-leg-val{color:var(--accent);font-weight:900}
.gbar-legend{display:flex;gap:16px;justify-content:center;margin-bottom:6px}
.gbar-pill{display:inline-flex;align-items:center;gap:8px;font-size:22px;
font-weight:800;background:var(--paper);border:2px solid var(--ink);
border-radius:999px;padding:4px 14px}
/* Question scene decorative backdrop */
.q-stage{position:relative;width:100%;display:flex;flex-direction:column;
align-items:center;justify-content:center;padding:38px 20px}
.q-watermark{position:absolute;font-size:440px;font-weight:900;line-height:1;
color:var(--line);opacity:.48;user-select:none;pointer-events:none;z-index:1}
.q-orbit{position:absolute;width:420px;height:420px;pointer-events:none;z-index:2}
.q-accent-dot{position:absolute;width:24px;height:24px;border-radius:50%;
border:3px solid var(--ink);z-index:3}
.q-dot-1{top:18px;right:48px;background:var(--marker)}
.q-dot-2{bottom:26px;left:56px;background:var(--blue)}
.q-dot-3{top:64px;left:40px;width:18px;height:18px;background:var(--accent)}
.q-underline{margin-top:24px;height:16px;width:0;max-width:340px;
background:var(--marker);border:3px solid var(--ink);border-radius:999px;
position:relative;z-index:4}
.giant{font-size:100px;font-weight:900;line-height:1.32;text-align:center;
position:relative;z-index:4}
.giant .w{opacity:.55}
.giant .w.done{opacity:1}
.giant .w.lit{color:var(--accent)}
/* Statistic & bars */
.bigstat{font-size:200px;font-weight:900;color:var(--accent);line-height:1;
direction:ltr;unicode-bidi:embed}
.statlabel{font-size:34px;font-weight:700;line-height:1.42;text-align:center}
.bars{width:100%;display:flex;flex-direction:column;gap:16px;margin-top:8px}
.bar-row{display:flex;flex-direction:column;gap:6px}
.bar-label{font-size:27px;font-weight:700;text-align:right;
display:flex;justify-content:space-between;align-items:center}
.bar-track{height:32px;background:var(--line);border:2.5px solid var(--ink);
border-radius:999px;overflow:hidden}
.bar-fill{height:100%;border-radius:999px;width:0}
.bar-fill.accent{background:var(--accent)}
.bar-fill.blue{background:var(--blue)}
.bar-fill.marker{background:var(--marker)}
/* Process steps */
.steps{display:flex;flex-direction:column;gap:16px;width:100%;
flex:1;justify-content:space-evenly;align-self:stretch}
.step{display:flex;gap:16px;align-items:center;background:var(--card);
border:3px solid var(--ink);border-radius:22px;padding:16px 20px;
box-shadow:6px 6px 0 var(--ink)}
.step.is-active{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.stepnum{width:54px;height:54px;border-radius:50%;background:var(--accent);
color:var(--paper);font-size:28px;font-weight:900;flex:none;
display:flex;align-items:center;justify-content:center;
border:3px solid var(--ink)}
.steptext{font-size:30px;font-weight:700;line-height:1.42}
/* Comparison panels */
.panels{display:flex;gap:18px;width:100%;align-items:stretch}
.panel{flex:1;background:var(--card);border:3px solid var(--ink);
border-radius:28px;padding:26px 16px;text-align:center;
box-shadow:6px 6px 0 var(--ink);display:flex;flex-direction:column;
align-items:center}
.panel.is-active{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.panel .ptitle{font-size:32px;font-weight:900;margin-top:12px;line-height:1.38}
.panel .pbody{font-size:25px;color:var(--muted);line-height:1.48;margin-top:6px}
.eq{font-size:60px;font-weight:900;color:var(--accent);align-self:center}
.endline{text-align:center;color:var(--muted);font-size:28px;margin-top:12px}
/* Timeline scene */
.timeline{position:relative;width:100%;display:flex;flex-direction:column;
gap:18px;padding-right:42px}
.tl-spine{position:absolute;top:22px;bottom:22px;right:18px;width:6px;
background:var(--line);border-radius:999px;overflow:hidden}
.tl-spine-fill{width:100%;height:0;background:var(--accent);border-radius:999px}
.tl-item{position:relative;background:var(--card);border:3px solid var(--ink);
border-radius:22px;padding:16px 20px;box-shadow:6px 6px 0 var(--ink)}
.tl-item.is-active{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.tl-dot{position:absolute;top:50%;right:-35px;width:22px;height:22px;
margin-top:-11px;border-radius:50%;background:var(--card);
border:4px solid var(--ink)}
.tl-item.is-active .tl-dot{background:var(--accent)}
.tl-tag{display:inline-block;background:var(--marker);color:var(--ink);
border:2px solid var(--ink);border-radius:999px;padding:2px 14px;
font-size:21px;font-weight:800;margin-bottom:5px}
.tl-text{font-size:28px;font-weight:700;line-height:1.4}
/* Cycle scene */
.cycle-wrap{position:relative;width:100%;height:520px;display:flex;
align-items:center;justify-content:center}
.cycle-ring{position:absolute;width:360px;height:360px;pointer-events:none}
.cycle-center{position:relative;z-index:2;width:146px;height:146px;
border-radius:50%;background:var(--marker);border:3px solid var(--ink);
box-shadow:5px 5px 0 var(--ink);display:flex;align-items:center;
justify-content:center;text-align:center;font-size:28px;font-weight:900;
padding:10px}
.cycle-node{position:absolute;width:224px;background:var(--card);
border:3px solid var(--ink);border-radius:20px;padding:12px 14px;
box-shadow:5px 5px 0 var(--ink);text-align:center;z-index:3}
.cycle-node.is-active{border-color:var(--accent);
box-shadow:7px 7px 0 var(--accent)}
.cycle-node .cnum{display:inline-flex;width:34px;height:34px;border-radius:50%;
background:var(--blue);color:var(--paper);border:2.5px solid var(--ink);
align-items:center;justify-content:center;font-size:19px;font-weight:900;
margin-bottom:5px}
.cycle-node.is-active .cnum{background:var(--accent)}
.cycle-node .ctext{font-size:24px;font-weight:800;line-height:1.32}
/* Layers scene */
.layers-stack{width:100%;display:flex;flex-direction:column;gap:14px}
.layer-row{display:flex;align-items:center;gap:16px;background:var(--card);
border:3px solid var(--ink);border-radius:22px;padding:18px 20px;
box-shadow:6px 6px 0 var(--ink)}
.layer-row.is-active{border-color:var(--accent);
box-shadow:8px 8px 0 var(--accent)}
.layer-chip{min-width:92px;padding:7px 14px;border-radius:14px;
background:var(--blue);color:var(--paper);border:2.5px solid var(--ink);
font-size:23px;font-weight:900;text-align:center}
.layer-row.is-active .layer-chip{background:var(--accent)}
.layer-body{font-size:28px;font-weight:700;line-height:1.38}
/* Callout / Quote scene */
.callout-card{position:relative;width:100%;background:var(--card);
border:3px solid var(--ink);border-radius:34px;
box-shadow:10px 10px 0 var(--ink);padding:52px 34px 40px;text-align:center}
.callout-mark{position:absolute;top:-32px;right:32px;width:78px;height:78px;
border-radius:20px;background:var(--accent);color:var(--paper);
border:3px solid var(--ink);box-shadow:4px 4px 0 var(--ink);
display:flex;align-items:center;justify-content:center;font-size:54px;
font-weight:900;line-height:1}
.callout-text{font-size:42px;font-weight:900;line-height:1.48;position:relative;
z-index:2}
.callout-sweep{margin:22px auto 0;height:14px;width:0;max-width:300px;
background:var(--marker);border:2.5px solid var(--ink);border-radius:999px}
.callout-author{margin-top:16px;font-size:26px;font-weight:700;
color:var(--muted)}
/* Chapter divider scene (long-form explainers) */
.chapter-stage{position:relative;width:100%;display:flex;flex-direction:column;
align-items:center;justify-content:center;text-align:center;gap:20px}
.chapter-pill{background:var(--marker);color:var(--ink);
border:3px solid var(--ink);border-radius:999px;padding:8px 28px;
font-size:28px;font-weight:900;box-shadow:5px 5px 0 var(--ink);z-index:4}
.chapter-title{font-size:66px;font-weight:900;line-height:1.25;z-index:4}
.chapter-bar{height:14px;width:0;max-width:360px;background:var(--accent);
border:3px solid var(--ink);border-radius:999px;z-index:4}
.chapter-sub{font-size:32px;color:var(--muted);font-weight:700;z-index:4}
/* Code block scene (technical explainers) */
.code-card{width:100%;background:#111827;color:#F8FAFC;
border:3px solid var(--ink);border-radius:26px;overflow:hidden;
box-shadow:10px 10px 0 var(--ink);direction:ltr;text-align:left}
.code-hdr{display:flex;align-items:center;justify-content:space-between;
padding:14px 20px;background:#1F2937;border-bottom:2px solid #374151}
.code-dots{display:flex;gap:8px}
.code-dot{width:14px;height:14px;border-radius:50%}
.code-lang{font-size:20px;font-weight:800;color:#93C5FD;
font-family:monospace}
.code-body{padding:20px 24px;font-family:monospace;font-size:24px;
line-height:1.65;white-space:pre-wrap}
.code-line{padding:2px 8px;border-radius:6px;opacity:0}
.code-line.hl{background:rgba(245,158,11,.22);border-left:4px solid var(--marker)}
/* Split scene (2-column explainer layout) */
.split-grid{width:100%;display:flex;gap:22px;align-items:stretch}
.split-visual{flex:0 0 38%;background:var(--card);border:3px solid var(--ink);
border-radius:28px;padding:24px;box-shadow:7px 7px 0 var(--ink);
display:flex;flex-direction:column;align-items:center;justify-content:center;
gap:14px;text-align:center}
.split-list{flex:1;display:flex;flex-direction:column;gap:14px;
justify-content:center}
.split-item{background:var(--card);border:3px solid var(--ink);
border-radius:20px;padding:16px 20px;box-shadow:5px 5px 0 var(--ink);
font-size:28px;font-weight:700;line-height:1.4}
.split-item.is-active{border-color:var(--accent);
box-shadow:7px 7px 0 var(--accent)}
/* Checklist scene */
.checklist{width:100%;display:flex;flex-direction:column;gap:16px}
.chk-item{display:flex;align-items:center;gap:16px;background:var(--card);
border:3px solid var(--ink);border-radius:22px;padding:18px 22px;
box-shadow:6px 6px 0 var(--ink)}
.chk-box{width:46px;height:46px;border-radius:14px;background:var(--paper);
border:3px solid var(--ink);display:flex;align-items:center;
justify-content:center;font-size:28px;font-weight:900;color:var(--paper);
flex:none}
.chk-item.checked .chk-box{background:var(--accent)}
.chk-item.checked{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.chk-text{font-size:30px;font-weight:700;line-height:1.4}
/* Before / After template */
.ba-grid{width:100%;display:flex;gap:18px;align-items:stretch}
.ba-card{flex:1;background:var(--card);border:3px solid var(--ink);
border-radius:28px;padding:24px 18px;box-shadow:7px 7px 0 var(--ink);
display:flex;flex-direction:column;gap:12px}
.ba-card.ba-after{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.ba-card.is-active{transform:translateY(-3px)}
.ba-badge{align-self:flex-start;padding:4px 16px;border-radius:999px;
border:2.5px solid var(--ink);font-size:22px;font-weight:900}
.ba-before .ba-badge{background:var(--line);color:var(--ink)}
.ba-after .ba-badge{background:var(--accent);color:var(--paper)}
.ba-title{font-size:30px;font-weight:900}
.ba-item{font-size:25px;font-weight:700;line-height:1.4;padding-right:10px;
border-right:4px solid var(--blue)}
.ba-after .ba-item{border-right-color:var(--accent)}
/* Funnel template */
.funnel-stack{width:100%;display:flex;flex-direction:column;
align-items:center;gap:14px}
.funnel-stage{background:var(--card);border:3px solid var(--ink);
border-radius:20px;padding:14px 20px;box-shadow:6px 6px 0 var(--ink);
display:flex;justify-content:space-between;align-items:center}
.funnel-stage.is-active{background:var(--marker);border-color:var(--ink)}
.funnel-lbl{font-size:28px;font-weight:900}
.funnel-val{font-size:26px;font-weight:900;color:var(--accent);
background:var(--paper);border:2px solid var(--ink);border-radius:999px;
padding:2px 14px}
/* Matrix 2x2 template */
.matrix-grid{width:100%;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.matrix-cell{background:var(--card);border:3px solid var(--ink);
border-radius:24px;padding:20px 18px;box-shadow:6px 6px 0 var(--ink);
display:flex;flex-direction:column;gap:8px}
.matrix-cell.is-active{border-color:var(--accent);box-shadow:8px 8px 0 var(--accent)}
.matrix-qtag{align-self:flex-start;background:var(--blue);color:var(--paper);
border:2px solid var(--ink);border-radius:10px;padding:2px 12px;
font-size:20px;font-weight:900}
.matrix-cell.is-active .matrix-qtag{background:var(--accent)}
.matrix-title{font-size:28px;font-weight:900}
.matrix-body{font-size:23px;color:var(--muted);font-weight:700;line-height:1.38}
/* Terminal demo template */
.term-card{width:100%;background:#0F172A;color:#F8FAFC;
border:3px solid var(--ink);border-radius:26px;overflow:hidden;
box-shadow:10px 10px 0 var(--ink);direction:ltr;text-align:left}
.term-body{padding:18px 22px;font-family:monospace;font-size:23px;
line-height:1.6;display:flex;flex-direction:column;gap:8px}
.term-row{opacity:0}
.term-cmd{color:#38BDF8;font-weight:800}
.term-out{color:#A7F3D0;padding-left:16px}
/* Chapter TOC template */
.toc-list{width:100%;display:flex;flex-direction:column;gap:14px}
.toc-row{display:flex;align-items:center;gap:16px;background:var(--card);
border:3px solid var(--ink);border-radius:22px;padding:16px 20px;
box-shadow:6px 6px 0 var(--ink)}
.toc-row.is-active{background:var(--marker);border-color:var(--ink);
box-shadow:8px 8px 0 var(--accent)}
.toc-num{width:50px;height:50px;border-radius:14px;background:var(--ink);
color:var(--paper);font-size:24px;font-weight:900;display:flex;
align-items:center;justify-content:center;flex:none}
.toc-row.is-active .toc-num{background:var(--accent)}
.toc-title{font-size:29px;font-weight:900;flex:1}
.toc-time{font-size:22px;font-weight:800;color:var(--muted);direction:ltr}
.brand-watermark{position:absolute;bottom:14px;left:22px;font-size:19px;
font-weight:900;color:var(--muted);opacity:.65;z-index:45;pointer-events:none}
/* Layout modifiers */
.layout-zoom .art-card{padding:48px 32px}
.layout-zoom .icon-halo{width:232px;height:232px}
.layout-scale .bigstat{font-size:224px}
.layout-cards .step,.layout-cards .panel{border-radius:30px}
/* Landscape 16:9 responsive adjustments (long-form explainers) */
.fmt-landscape .scene{padding:28px 54px 24px}
.fmt-landscape h1.title{font-size:44px;margin-top:10px}
.fmt-landscape .subtitle{font-size:24px;margin-top:4px}
.fmt-landscape .visual{padding:14px 0 10px}
.fmt-landscape .art-card{flex-direction:row;justify-content:center;gap:38px;
padding:28px 40px;text-align:right}
.fmt-landscape .icon-halo{width:168px;height:168px}
.fmt-landscape .bigstat{font-size:150px}
.fmt-landscape .caption{margin-top:8px;min-height:74px;
background:var(--card);border:3px solid var(--ink);border-radius:18px;
padding:10px 24px;box-shadow:5px 5px 0 var(--ink)}
.fmt-landscape .cap-chunk{font-size:30px;line-height:1.4}
.fmt-landscape .giant{font-size:84px}
.fmt-landscape .q-watermark{font-size:320px}
.fmt-landscape .q-orbit{width:310px;height:310px}
.fmt-landscape .steps,.fmt-landscape .timeline,.fmt-landscape .layers-stack,
.fmt-landscape .checklist{gap:10px}
.fmt-landscape .step,.fmt-landscape .tl-item,.fmt-landscape .layer-row,
.fmt-landscape .chk-item{padding:11px 16px}
.fmt-landscape .steptext,.fmt-landscape .tl-text,.fmt-landscape .layer-body,
.fmt-landscape .chk-text{font-size:24px}
.fmt-landscape .cycle-wrap{height:380px}
.fmt-landscape .cycle-ring{width:280px;height:280px}
.fmt-landscape .cycle-node{width:210px;padding:9px 12px}
.fmt-landscape .callout-card{padding:36px 40px 28px}
.fmt-landscape .callout-text{font-size:34px}
.fmt-landscape .code-body{font-size:20px;line-height:1.5;padding:14px 20px}
.fmt-landscape .chapter-title{font-size:58px}
/* Square 1:1 adjustments */
.fmt-square .scene{padding:36px 44px 30px}
.fmt-square h1.title{font-size:48px;margin-top:14px}
.fmt-square .caption{min-height:105px}
.fmt-square .cap-chunk{font-size:36px}
"""
    return (
        raw.replace("__W__", str(width)).replace("__H__", str(height))
        .replace("PAPER", pal["paper"]).replace("CARD", pal["card"])
        .replace("INK", pal["ink"]).replace("ACCENT", pal["accent"])
        .replace("BLUE", pal["blue"]).replace("MUTED", pal["muted"])
        .replace("MARKER", pal["marker"]).replace("LINE", pal["line"])
    )


_JS = """
const DUR = __DUR__;
const MOTION_SCALE = __MOTION__;
const DEFAULT_TRANS = __TRANS__;
const PHYSICS_MODE = __PHYSICS__;
const DEFAULT_CAM = __CAMERA__;
function easeOutCubic(x){return 1-Math.pow(1-x,3);}
function easeOutBack(x){const c=1.70158;return 1+((c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2));}
function easeOutSpring(x){
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return 1 - Math.exp(-6.5 * x) * Math.cos(11.5 * x);
}
function easeOutElastic(x){
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return Math.pow(2, -9 * x) * Math.sin((x * 9 - 0.75) * (2 * Math.PI / 3)) + 1;
}
function applyPhysicsEase(x){
  if (PHYSICS_MODE === 'elastic') return easeOutElastic(x);
  if (PHYSICS_MODE === 'spring') return easeOutSpring(x);
  return easeOutBack(x);
}
function beatEnergy(local, peaks){
  if (!peaks || !peaks.length) return 0;
  let boost = 0;
  for (let i = 0; i < peaks.length; i++){
    const dt = local - peaks[i].t;
    if (dt >= -0.03 && dt <= 0.19){
      const w = Math.exp(-(dt * dt) / 0.0045) * (peaks[i].i || 0.8);
      if (w > boost) boost = w;
    }
  }
  return boost;
}
let CURRENT = null;
window.setTime = function(t){
  const clamped = Math.max(0, t);
  const ms = MOTION_SCALE;
  document.getElementById('progressbar').style.width =
    (Math.min(100, DUR > 0 ? 100*clamped/DUR : 0)) + '%';

  // Continuous background paper-grid drift (deterministic from t).
  const grid = document.querySelector('.paper-grid');
  if (grid) {
    grid.style.backgroundPosition =
      (clamped * 14 * ms).toFixed(2) + 'px ' + (clamped * 9 * ms).toFixed(2) + 'px';
  }
  // Global subtle ambient background motifs.
  document.querySelectorAll('#stage > .bg-motif').forEach((el, idx) => {
    const phase = idx * 1.9;
    const dx = Math.sin(clamped * 0.65 * ms + phase) * 14 * ms;
    const dy = Math.cos(clamped * 0.52 * ms + phase) * 12 * ms;
    const rot = Math.sin(clamped * 0.4 * ms + phase) * 12 * ms + idx * 15;
    el.style.transform =
      'translate(' + dx.toFixed(2) + 'px,' + dy.toFixed(2) + 'px) rotate(' +
      rot.toFixed(2) + 'deg)';
  });
  // Floating ambient geometric particles.
  document.querySelectorAll('.bg-particle').forEach((el, idx) => {
    const ph = idx * 1.37;
    const dx = Math.sin(clamped * (0.7 + (idx % 3) * 0.22) * ms + ph) * (16 + idx * 2) * ms;
    const dy = Math.cos(clamped * (0.55 + (idx % 2) * 0.28) * ms + ph) * (14 + idx * 2) * ms;
    const rot = ((clamped * (18 + idx * 7) * ms) % 360);
    el.style.transform =
      'translate(' + dx.toFixed(2) + 'px,' + dy.toFixed(2) + 'px) rotate(' +
      rot.toFixed(1) + 'deg)';
  });

  let sc = SCENES[SCENES.length-1];
  for (const s of SCENES){ if (clamped < s.end){ sc = s; break; } }
  if (CURRENT !== sc.id){
    document.querySelectorAll('.scene').forEach(
      el => el.classList.remove('active'));
    document.getElementById('scene-'+sc.id).classList.add('active');
    CURRENT = sc.id;
  }
  const root = document.getElementById('scene-'+sc.id);
  const local = Math.max(0, clamped - sc.start);
  const scDur = Math.max(0.001, sc.end - sc.start);
  const scProg = Math.min(1, local / scDur);
  const beatBoost = beatEnergy(local, sc.peaks || []);

  // Scene-level deterministic transition (first 0.26s) + browser camera move.
  // The first scene opens fully formed: frame 0 is the video cover, so it
  // must not be an empty fade-in.
  const isFirst = sc.start === 0;
  const tr = sc.transition || DEFAULT_TRANS;
  const cam = sc.camera_move || DEFAULT_CAM;
  const tp = isFirst ? 1 : Math.min(1, local / 0.26);
  const te = easeOutCubic(tp);
  root.style.clipPath = '';
  let rootTx = 0, rootTy = 0, rootScale = 1;
  if (tr === 'fade') {
    root.style.opacity = te.toFixed(3);
  } else if (tr === 'slide_up') {
    root.style.opacity = te.toFixed(3);
    rootTy += (1 - te) * 28;
  } else if (tr === 'zoom_in') {
    root.style.opacity = te.toFixed(3);
    rootScale *= (0.95 + 0.05 * te);
  } else if (tr === 'wipe_rtl') {
    root.style.opacity = '1';
    const leftClip = ((1 - te) * 100).toFixed(2);
    root.style.clipPath = 'inset(0 0 0 ' + leftClip + '%)';
  } else if (tr === 'iris_reveal') {
    root.style.opacity = '1';
    const rad = (te * 115).toFixed(2);
    root.style.clipPath = 'circle(' + rad + '% at 50% 50%)';
  } else {
    root.style.opacity = '1';
  }
  const visBox = root.querySelector('.visual');
  if (visBox) {
    let camScale = 1 + beatBoost * 0.012 * ms, camX = 0, camY = 0;
    if (cam === 'push_in') {
      camScale += 0.045 * scProg * ms;
    } else if (cam === 'pull_out') {
      camScale += 0.045 * (1 - scProg) * ms;
    } else if (cam === 'pan_rtl') {
      camX = (0.5 - scProg) * 18 * ms;
    } else if (cam === 'pan_ltr') {
      camX = (scProg - 0.5) * 18 * ms;
    } else if (cam === 'drift') {
      camX = Math.sin(local * 0.9 * ms) * 5 * ms;
      camY = Math.cos(local * 0.75 * ms) * 4 * ms;
    }
    visBox.style.transform =
      'translate(' + camX.toFixed(2) + 'px,' + camY.toFixed(2) + 'px) scale(' +
      camScale.toFixed(4) + ')';
  }
  root.style.transform =
    (rootTx !== 0 || rootTy !== 0 || rootScale !== 1)
      ? ('translate(' + rootTx.toFixed(2) + 'px,' + rootTy.toFixed(2) + 'px) scale(' + rootScale.toFixed(4) + ')')
      : '';

  // Entrance + continuous living micro-float for .anim elements.
  root.querySelectorAll('.anim').forEach((el, idx) => {
    const d = parseFloat(el.dataset.delay || '0');
    const p = isFirst ? 1 : Math.min(1, Math.max(0, (local - d) / 0.45));
    const e = applyPhysicsEase(p);
    el.style.opacity = p;
    let ty = (1 - p) * 18;
    let sx = 0.92 + 0.08 * e;
    let rot = 0;
    if (p >= 1) {
      const aft = local - d - 0.45;
      if (el.classList.contains('badge')) {
        rot = -8 + Math.sin(aft * 2.4 * ms) * 4.0 * ms;
        sx = 1 + 0.03 * ms * Math.sin(aft * 2.1 * ms);
      } else if (el.classList.contains('art-card') || el.classList.contains('callout-card') || el.classList.contains('code-card')) {
        ty = Math.sin(aft * 1.45 * ms + idx) * 3.5 * ms;
      } else if (el.classList.contains('panel') || el.classList.contains('split-visual')) {
        ty = Math.sin(aft * 1.6 * ms + idx * 1.4) * 3.0 * ms;
      } else if (el.classList.contains('giant') || el.classList.contains('chapter-title')) {
        sx = 1 + 0.022 * ms * Math.sin(aft * 2.0 * ms);
        ty = Math.sin(aft * 1.5 * ms) * 4.0 * ms;
      }
    } else if (el.classList.contains('badge')) {
      rot = -8;
    }
    el.style.transform =
      'translateY(' + ty.toFixed(2) + 'px) scale(' + sx.toFixed(4) + ')' +
      (rot !== 0 ? ' rotate(' + rot.toFixed(2) + 'deg)' : '');
  });

  // Hero SVG icons: breathing + subtle vertical float + tilt + inner motions.
  root.querySelectorAll('.ambient-icon').forEach((svg, idx) => {
    const ph = idx * 1.3;
    const bob = Math.sin(local * 1.85 * ms + ph) * 5.5 * ms - beatBoost * 3.2 * ms;
    const scIcon = 1 + 0.032 * ms * Math.sin(local * 2.2 * ms + ph) + beatBoost * 0.045 * ms;
    const tilt = Math.sin(local * 1.15 * ms + ph) * 2.4 * ms;
    svg.style.transform =
      'translateY(' + bob.toFixed(2) + 'px) scale(' + scIcon.toFixed(4) +
      ') rotate(' + tilt.toFixed(2) + 'deg)';
    svg.querySelectorAll('.svg-spin').forEach(g => {
      g.style.transformOrigin = '32px 32px';
      g.style.transform = 'rotate(' + ((local * 28 * ms) % 360).toFixed(2) + 'deg)';
    });
    svg.querySelectorAll('.svg-pulse').forEach((g, gi) => {
      g.style.transformOrigin = '32px 32px';
      const s = 1 + 0.065 * ms * Math.sin(local * 3.1 * ms + gi);
      g.style.transform = 'scale(' + s.toFixed(4) + ')';
    });
    svg.querySelectorAll('.svg-wave').forEach((g, gi) => {
      const dx = Math.sin(local * 2.8 * ms + gi) * 3.2 * ms;
      g.style.transform = 'translateX(' + dx.toFixed(2) + 'px)';
    });
    svg.querySelectorAll('.svg-rise').forEach((g, gi) => {
      const dy = Math.sin(local * 2.4 * ms + gi) * -2.8 * ms;
      g.style.transform = 'translateY(' + dy.toFixed(2) + 'px)';
    });
    svg.querySelectorAll('.svg-tick').forEach((g, gi) => {
      g.style.transformOrigin = '32px 32px';
      const ang = Math.sin(local * 2.6 * ms + gi) * 9.0 * ms;
      g.style.transform = 'rotate(' + ang.toFixed(2) + 'deg)';
    });
  });

  // Ken Burns smooth browser drift for embedded scene images (no FFmpeg zoompan).
  root.querySelectorAll('.kb-img').forEach(img => {
    const z = 1.0 + 0.08 * scProg;
    const dx = (scProg - 0.5) * 16 * ms;
    img.style.transform = 'scale(' + z.toFixed(4) + ') translateX(' + dx.toFixed(2) + 'px)';
  });

  // Icon halo ring gentle rotation.
  root.querySelectorAll('.icon-halo').forEach((el, idx) => {
    el.style.transform = 'rotate(' + ((local * 16 * ms + idx * 30) % 360).toFixed(2) + 'deg)';
  });

  // Question / Cycle / Chapter orbital rings & watermark breathing.
  root.querySelectorAll('.q-orbit, .cycle-ring').forEach(el => {
    el.style.transform = 'rotate(' + ((local * 18 * ms) % 360).toFixed(2) + 'deg)';
  });
  root.querySelectorAll('.q-watermark').forEach(el => {
    const s = 1 + 0.03 * ms * Math.sin(local * 1.4 * ms);
    el.style.transform = 'scale(' + s.toFixed(4) + ') rotate(' +
      (Math.sin(local * 0.9 * ms) * 4 * ms).toFixed(2) + 'deg)';
  });
  root.querySelectorAll('.q-accent-dot').forEach((el, idx) => {
    const dx = Math.sin(local * 1.9 * ms + idx * 2.1) * 9 * ms;
    const dy = Math.cos(local * 1.5 * ms + idx * 1.7) * 9 * ms;
    el.style.transform = 'translate(' + dx.toFixed(2) + 'px,' + dy.toFixed(2) + 'px)';
  });

  // Marker sweep underlines (question, callout, chapter scenes).
  root.querySelectorAll('.q-underline, .callout-sweep, .chapter-bar').forEach(el => {
    const p = Math.min(1, Math.max(0, (local - 0.16) / 0.65));
    const maxW = parseFloat(el.dataset.maxw || '320');
    el.style.width = (easeOutCubic(p) * maxW).toFixed(1) + 'px';
  });

  // Big statistic numeric count-up + breathing.
  root.querySelectorAll('.bigstat[data-target]').forEach(el => {
    const target = parseFloat(el.dataset.target);
    const dec = parseInt(el.dataset.dec || '0', 10);
    const pre = el.dataset.pre || '';
    const suf = el.dataset.suf || '';
    const p = Math.min(1, Math.max(0, (local - 0.1) / 0.85));
    const val = target * easeOutCubic(p);
    el.textContent = pre + (dec > 0 ? val.toFixed(dec) : String(Math.round(val))) + suf;
    const pulse = p >= 1 ? (1 + 0.025 * ms * Math.sin((local - 0.95) * 2.4 * ms)) : 1;
    el.style.transform = 'scale(' + pulse.toFixed(4) + ')';
  });

  // SVG Line chart progressive stroke-dashoffset draw + node pop-in.
  root.querySelectorAll('.chart-line-path').forEach(el => {
    const len = parseFloat(el.dataset.len || '500');
    const d = parseFloat(el.dataset.delay || '0.12');
    const p = Math.min(1, Math.max(0, (local - d) / 0.95));
    const e = easeOutCubic(p);
    el.style.strokeDashoffset = ((1 - e) * len).toFixed(2);
  });
  root.querySelectorAll('.chart-area').forEach(el => {
    const p = Math.min(1, Math.max(0, (local - 0.35) / 0.75));
    el.style.opacity = (easeOutCubic(p) * 0.18).toFixed(3);
  });
  root.querySelectorAll('.chart-dot').forEach(el => {
    const d = parseFloat(el.dataset.delay || '0.2');
    const p = Math.min(1, Math.max(0, (local - d) / 0.35));
    el.style.opacity = p.toFixed(3);
  });
  // SVG Donut chart arc sweep.
  root.querySelectorAll('.donut-seg').forEach(el => {
    const circ = parseFloat(el.dataset.circ || '600');
    const segLen = parseFloat(el.dataset.seglen || '200');
    const d = parseFloat(el.dataset.delay || '0.14');
    const p = Math.min(1, Math.max(0, (local - d) / 0.85));
    const cur = (easeOutCubic(p) * segLen).toFixed(2);
    el.setAttribute('stroke-dasharray', cur + ' ' + circ);
  });
  // SVG Grouped vertical bars growth from baseline.
  root.querySelectorAll('.gbar-rect').forEach((el, idx) => {
    const baseY = parseFloat(el.dataset.basey || '240');
    const th = parseFloat(el.dataset.th || '100');
    const d = parseFloat(el.dataset.delay || '0.15');
    const p = Math.min(1, Math.max(0, (local - d) / 0.78));
    const h = easeOutCubic(p) * th;
    el.setAttribute('y', (baseY - h).toFixed(2));
    el.setAttribute('height', Math.max(0, h).toFixed(2));
  });
  root.querySelectorAll('.gbar-val').forEach(el => {
    const d = parseFloat(el.dataset.delay || '0.15');
    const p = Math.min(1, Math.max(0, (local - d - 0.25) / 0.35));
    el.style.opacity = p.toFixed(3);
  });

  // Bars growth + subtle living highlight.
  root.querySelectorAll('.bar-fill').forEach((el, idx) => {
    const d = parseFloat(el.dataset.delay || '0');
    const p = Math.min(1, Math.max(0, (local - d) / 0.8));
    const baseW = easeOutCubic(p) * parseFloat(el.dataset.w);
    const breath = p >= 1 ? (0.6 * ms * Math.sin((local - d) * 2.5 * ms + idx)) : 0;
    el.style.width = Math.max(0, Math.min(100, baseW + breath)).toFixed(2) + '%';
  });

  // Timeline vertical spine fill.
  root.querySelectorAll('.tl-spine-fill').forEach(el => {
    el.style.height = (Math.min(1, scProg * 1.15) * 100).toFixed(2) + '%';
  });

  // Progressive code line reveal.
  const codeLines = root.querySelectorAll('.code-line');
  if (codeLines.length > 0) {
    codeLines.forEach((el, idx) => {
      const d = 0.12 + idx * 0.16;
      const p = Math.min(1, Math.max(0, (local - d) / 0.25));
      el.style.opacity = p.toFixed(3);
      el.style.transform = 'translateX(' + ((1 - p) * -14).toFixed(1) + 'px)';
    });
  }

  // Animated checklist tick-off synced to narration progress.
  const chkItems = root.querySelectorAll('.chk-item');
  if (chkItems.length > 0) {
    chkItems.forEach((el, i) => {
      const thresh = (i + 0.35) / chkItems.length;
      const checked = scProg >= thresh;
      el.classList.toggle('checked', checked);
      const box = el.querySelector('.chk-box');
      if (box) box.textContent = checked ? '✓' : '';
    });
  }

  // Terminal demo progressive row reveal.
  const termRows = root.querySelectorAll('.term-row');
  if (termRows.length > 0) {
    termRows.forEach((el, idx) => {
      const d = 0.14 + idx * 0.22;
      const p = Math.min(1, Math.max(0, (local - d) / 0.24));
      el.style.opacity = p.toFixed(3);
      el.style.transform = 'translateX(' + ((1 - p) * -12).toFixed(1) + 'px)';
    });
  }

  // Narration-coupled sequential highlight across steps/items/layers/cycle/panels/split/advanced templates.
  ['.step', '.tl-item', '.layer-row', '.cycle-node', '.panel', '.split-item', '.ba-card', '.funnel-stage', '.matrix-cell', '.toc-row'].forEach(sel => {
    const items = root.querySelectorAll(sel);
    if (items.length > 1) {
      const activeIdx = Math.min(items.length - 1, Math.floor(scProg * items.length));
      items.forEach((el, i) => {
        el.classList.toggle('is-active', i === activeIdx);
      });
    }
  });

  // Caption chunks & word lighting.
  root.querySelectorAll('.cap-chunk').forEach(el => {
    const s = parseFloat(el.dataset.s), e = parseFloat(el.dataset.e);
    el.classList.toggle('on', clamped >= s && clamped < e);
  });
  root.querySelectorAll('.w').forEach(el => {
    const s = parseFloat(el.dataset.s), e = parseFloat(el.dataset.e);
    el.classList.toggle('lit', clamped >= s && clamped < e);
    el.classList.toggle('done', clamped >= e);
  });
  void document.body.offsetHeight;
};
"""


def _esc(text: str) -> str:
    return _html.escape(text, quote=False)


def _attr(text: str) -> str:
    return _html.escape(text, quote=True)


def _icon_stage(name: str, size: int = 190) -> str:
    """Render an icon inside its animated editorial halo stage."""
    return (
        '<div class="icon-stage">'
        '<div class="icon-halo"></div>'
        + icon_svg(name, size)
        + "</div>"
    )


def _image_or_icon(scene: dict, default_icon: str, size: int = 185) -> str:
    img = scene.get("image")
    if isinstance(img, str) and img.strip():
        return f'<div class="kb-wrap"><img class="kb-img" src="{_attr(img.strip())}" alt=""/></div>'
    ill = scene.get("illustration")
    if isinstance(ill, str) and ill.strip():
        return illustration_svg(ill.strip())
    return _icon_stage(str(scene.get("icon", default_icon)), size)


def _caption_chunks_html(
    timing: dict,
    scene: dict | None = None,
    spec: dict | None = None,
) -> str:
    """Smart balanced Arabic caption chunks with style & keyword support."""
    return render_caption_html(timing, scene=scene, spec=spec)


def _giant_words_html(timing: dict) -> str:
    spans = "".join(
        f'<span class="w" data-s="{w["start"]}" data-e="{w["end"]}">'
        f"{_esc(w['word'])}</span> " for w in timing["words"])
    return f'<div class="giant anim">{spans.strip()}</div>'


def _tagrow(spec: dict, scene: dict, index: int, total: int) -> str:
    tag = _esc(str(scene.get("tag") or spec.get("series_tag", "طب ازاي؟")))
    return (f'<div class="tagrow anim" data-delay="0">'
            f'<div class="tag">{tag}</div>'
            f'<div class="scenenum">{index + 1} / {total}</div></div>')


def _scene_intro(scene: dict, timing: dict, spec: dict,
                 index: int, total: int) -> str:
    visual = _image_or_icon(scene, "thermometer", 190)
    badge = scene.get("badge")
    badge_html = (f'<div class="badge anim" data-delay="0.35">'
                  f"{_esc(str(badge))}</div>" if badge else "")
    label = _esc(str(scene.get("body", "")))
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.2">'
        + badge_html + visual
        + (f'<div class="art-label">{label}</div>' if label else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_question(scene: dict, timing: dict, spec: dict,
                    index: int, total: int) -> str:
    orbit = (
        '<svg class="q-orbit" viewBox="0 0 200 200">'
        '<circle cx="100" cy="100" r="88" fill="none" stroke="var(--line)" '
        'stroke-width="3" stroke-dasharray="12 10"/>'
        '<circle cx="100" cy="12" r="7" fill="var(--accent)" '
        'stroke="var(--ink)" stroke-width="2.5"/>'
        '<circle cx="188" cy="100" r="6" fill="var(--blue)" '
        'stroke="var(--ink)" stroke-width="2.5"/>'
        '</svg>'
    )
    return (
        _tagrow(spec, scene, index, total)
        + '<div class="visual"><div class="q-stage">'
        + '<div class="q-watermark">؟</div>'
        + orbit
        + '<div class="q-accent-dot q-dot-1"></div>'
        + '<div class="q-accent-dot q-dot-2"></div>'
        + '<div class="q-accent-dot q-dot-3"></div>'
        + _giant_words_html(timing)
        + '<div class="q-underline" data-maxw="320"></div>'
        + "</div></div>"
        + '<div class="caption"></div>')


def _scene_concept(scene: dict, timing: dict, spec: dict,
                   index: int, total: int) -> str:
    visual = _image_or_icon(scene, "factory", 180)
    label = _esc(str(scene.get("body", "")))
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.2">'
        + visual
        + (f'<div class="art-label">{label}</div>' if label else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_process(scene: dict, timing: dict, spec: dict,
                   index: int, total: int) -> str:
    steps = scene.get("steps") or []
    rows: list[str] = []
    for i, st in enumerate(steps):
        if isinstance(st, str):
            st = {"text": st}
        rows.append(
            f'<div class="step anim" data-delay="{0.15 + 0.22 * i:.2f}">'
            f'<div class="stepnum">{i + 1}</div>'
            f'<div class="steptext">{_esc(str(st.get("text", "")))}</div>'
            "</div>")
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="steps">'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _bigstat_html(raw_value: str) -> str:
    m = _NUM_RE.match(raw_value.strip())
    if m:
        pre, num_str, suf = m.group(1), m.group(2), m.group(3)
        dec = len(num_str.split(".")[1]) if "." in num_str else 0
        return (
            f'<div class="bigstat" data-target="{_attr(num_str)}" '
            f'data-dec="{dec}" data-pre="{_attr(pre)}" '
            f'data-suf="{_attr(suf)}">{_esc(raw_value)}</div>'
        )
    return f'<div class="bigstat">{_esc(raw_value)}</div>'


def _scene_statistic(scene: dict, timing: dict, spec: dict,
                     index: int, total: int) -> str:
    stat = scene.get("stat") or {}
    raw_value = str(stat.get("value", ""))
    label = _esc(str(stat.get("label", "")))
    bars = scene.get("bars") or []
    bar_rows: list[str] = []
    for i, bar in enumerate(bars):
        bar_rows.append(
            f'<div class="bar-row anim" data-delay="{0.28 + 0.18 * i:.2f}">'
            f'<div class="bar-label">{_esc(str(bar.get("label", "")))}</div>'
            f'<div class="bar-track"><div class="bar-fill '
            f'{_esc(str(bar.get("color", "accent")))}" '
            f'data-w="{float(bar.get("value", 0))}" '
            f'data-delay="{0.28 + 0.18 * i:.2f}"></div></div></div>')
    return (
        _tagrow(spec, scene, index, total)
        + '<div class="visual"><div class="art-card anim" data-delay="0.1">'
        + _bigstat_html(raw_value)
        + (f'<div class="statlabel">{label}</div>' if label else "")
        + ('<div class="bars">' + "".join(bar_rows) + "</div>"
           if bar_rows else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_comparison(scene: dict, timing: dict, spec: dict,
                      index: int, total: int) -> str:
    panels = scene.get("panels") or []
    parts: list[str] = []
    for i, pn in enumerate(panels):
        parts.append(
            f'<div class="panel anim" data-delay="{0.15 + 0.22 * i:.2f}">'
            + icon_svg(str(pn.get("icon", "check")), 104)
            + f'<div class="ptitle">{_esc(str(pn.get("title", "")))}</div>'
            + (f'<div class="pbody">{_esc(str(pn.get("body", "")))}</div>'
               if pn.get("body") else "")
            + "</div>")
        if i == 0 and len(panels) > 1:
            sep = _esc(str(scene.get("separator", "=")))
            parts.append(f'<div class="eq anim" data-delay="0.4">{sep}</div>')
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="panels">'
        + "".join(parts) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_conclusion(scene: dict, timing: dict, spec: dict,
                      index: int, total: int) -> str:
    icon = _icon_stage(str(scene.get("icon", "check")), 165)
    title = _esc(str(scene.get("title", "")))
    takeaway = _esc(str(scene.get("body", "")))
    endline = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + (f'<h1 class="title anim" data-delay="0.05">{title}</h1>'
           if title else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.1">'
        + icon
        + (f'<div class="art-label">{takeaway}</div>' if takeaway else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec)
        + (f'<div class="endline anim" data-delay="0.3">{endline}</div>'
           if endline else ""))


def _scene_timeline(scene: dict, timing: dict, spec: dict,
                    index: int, total: int) -> str:
    items = scene.get("items") or scene.get("steps") or []
    rows: list[str] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            item = {"text": item}
        tag = item.get("tag") or item.get("label") or f"{i + 1}"
        rows.append(
            f'<div class="tl-item anim" data-delay="{0.15 + 0.2 * i:.2f}">'
            '<div class="tl-dot"></div>'
            f'<div class="tl-tag">{_esc(str(tag))}</div>'
            f'<div class="tl-text">{_esc(str(item.get("text", "")))}</div>'
            "</div>")
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="timeline">'
        + '<div class="tl-spine"><div class="tl-spine-fill"></div></div>'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_cycle(scene: dict, timing: dict, spec: dict,
                 index: int, total: int) -> str:
    nodes = (scene.get("nodes") or scene.get("steps") or [])[:4]
    positions = [
        "top:8px;left:50%;margin-left:-112px",
        "top:50%;left:0;margin-top:-54px",
        "bottom:8px;left:50%;margin-left:-112px",
        "top:50%;right:0;margin-top:-54px",
    ]
    node_html: list[str] = []
    for i, nd in enumerate(nodes):
        if isinstance(nd, str):
            nd = {"text": nd}
        pos = positions[i % len(positions)]
        node_html.append(
            f'<div class="cycle-node anim" style="{pos}" '
            f'data-delay="{0.15 + 0.18 * i:.2f}">'
            f'<div class="cnum">{i + 1}</div>'
            f'<div class="ctext">{_esc(str(nd.get("text", "")))}</div>'
            "</div>")
    center_label = _esc(str(scene.get("body") or scene.get("center") or "دورة"))
    ring = (
        '<svg class="cycle-ring" viewBox="0 0 200 200">'
        '<circle cx="100" cy="100" r="82" fill="none" stroke="var(--blue)" '
        'stroke-width="4" stroke-dasharray="14 10"/>'
        '<polygon points="100,10 112,24 88,24" fill="var(--accent)"/>'
        '<polygon points="100,190 112,176 88,176" fill="var(--accent)"/>'
        '</svg>'
    )
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + '<div class="visual"><div class="cycle-wrap">'
        + ring
        + f'<div class="cycle-center anim" data-delay="0.1">{center_label}</div>'
        + "".join(node_html) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_layers(scene: dict, timing: dict, spec: dict,
                  index: int, total: int) -> str:
    layers = scene.get("layers") or scene.get("steps") or []
    rows: list[str] = []
    for i, ly in enumerate(layers):
        if isinstance(ly, str):
            ly = {"text": ly}
        chip = ly.get("label") or ly.get("badge") or f"طبقة {i + 1}"
        rows.append(
            f'<div class="layer-row anim" data-delay="{0.14 + 0.18 * i:.2f}">'
            f'<div class="layer-chip">{_esc(str(chip))}</div>'
            f'<div class="layer-body">{_esc(str(ly.get("text", "")))}</div>'
            "</div>")
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="layers-stack">'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_callout(scene: dict, timing: dict, spec: dict,
                   index: int, total: int) -> str:
    quote = _esc(str(scene.get("quote") or scene.get("body")
                     or scene.get("title") or ""))
    author = _esc(str(scene.get("author") or scene.get("subtitle") or ""))
    return (
        _tagrow(spec, scene, index, total)
        + '<div class="visual"><div class="callout-card anim" data-delay="0.12">'
        + '<div class="callout-mark">“</div>'
        + f'<div class="callout-text">{quote}</div>'
        + '<div class="callout-sweep" data-maxw="280"></div>'
        + (f'<div class="callout-author">{author}</div>' if author else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_chapter(scene: dict, timing: dict, spec: dict,
                   index: int, total: int) -> str:
    ch_label = _esc(str(scene.get("badge") or f"الفصل {index + 1}"))
    title = _esc(str(scene.get("title") or scene.get("text") or ""))
    sub = _esc(str(scene.get("subtitle") or scene.get("body") or ""))
    orbit = (
        '<svg class="q-orbit" viewBox="0 0 200 200">'
        '<circle cx="100" cy="100" r="90" fill="none" stroke="var(--line)" '
        'stroke-width="3" stroke-dasharray="10 8"/>'
        '<circle cx="100" cy="10" r="6" fill="var(--accent)"/>'
        '</svg>'
    )
    return (
        _tagrow(spec, scene, index, total)
        + '<div class="visual"><div class="chapter-stage">'
        + orbit
        + f'<div class="chapter-pill anim" data-delay="0.05">{ch_label}</div>'
        + f'<div class="chapter-title anim" data-delay="0.14">{title}</div>'
        + '<div class="chapter-bar" data-maxw="340"></div>'
        + (f'<div class="chapter-sub anim" data-delay="0.25">{sub}</div>'
           if sub else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_code(scene: dict, timing: dict, spec: dict,
                index: int, total: int) -> str:
    lang = _esc(str(scene.get("language") or "python"))
    raw_code = str(scene.get("code") or scene.get("body") or "")
    hl_set = set(scene.get("highlight_lines") or [])
    lines_html: list[str] = []
    for idx_l, line in enumerate(raw_code.splitlines() or [""], start=1):
        hl_cls = " hl" if idx_l in hl_set else ""
        lines_html.append(
            f'<div class="code-line{hl_cls}">{_esc(line) or " "}</div>')
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="code-card anim" data-delay="0.18">'
        + '<div class="code-hdr"><div class="code-dots">'
        + '<span class="code-dot" style="background:#EF4444"></span>'
        + '<span class="code-dot" style="background:#F59E0B"></span>'
        + '<span class="code-dot" style="background:#10B981"></span></div>'
        + f'<div class="code-lang">{lang}</div></div>'
        + '<div class="code-body">' + "".join(lines_html) + "</div>"
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_split(scene: dict, timing: dict, spec: dict,
                 index: int, total: int) -> str:
    visual = _image_or_icon(scene, "bulb", 150)
    bullets = scene.get("bullets") or scene.get("items") or scene.get("steps") or []
    rows: list[str] = []
    for i, b in enumerate(bullets):
        txt = b.get("text", "") if isinstance(b, dict) else str(b)
        rows.append(
            f'<div class="split-item anim" data-delay="{0.18 + 0.18 * i:.2f}">'
            f"{_esc(txt)}</div>")
    label = _esc(str(scene.get("body", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + '<div class="visual"><div class="split-grid">'
        + '<div class="split-visual anim" data-delay="0.12">'
        + visual
        + (f'<div class="art-label">{label}</div>' if label else "")
        + '</div><div class="split-list">' + "".join(rows)
        + "</div></div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_checklist(scene: dict, timing: dict, spec: dict,
                     index: int, total: int) -> str:
    items = scene.get("items") or scene.get("steps") or []
    rows: list[str] = []
    for i, it in enumerate(items):
        txt = it.get("text", "") if isinstance(it, dict) else str(it)
        rows.append(
            f'<div class="chk-item anim" data-delay="{0.14 + 0.18 * i:.2f}">'
            '<div class="chk-box"></div>'
            f'<div class="chk-text">{_esc(txt)}</div></div>')
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="checklist">'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))




def _scene_chart_line(scene: dict, timing: dict, spec: dict,
                      index: int, total: int) -> str:
    chart = scene.get("chart") or scene
    sub = _esc(str(scene.get("subtitle", "")))
    label = _esc(str(scene.get("body", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.14">'
        + build_line_chart_svg(chart)
        + (f'<div class="art-label">{label}</div>' if label else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_chart_donut(scene: dict, timing: dict, spec: dict,
                       index: int, total: int) -> str:
    chart = scene.get("chart") or scene
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.14">'
        + build_donut_chart_svg(chart)
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_chart_bars(scene: dict, timing: dict, spec: dict,
                      index: int, total: int) -> str:
    chart = scene.get("chart") or scene
    sub = _esc(str(scene.get("subtitle", "")))
    label = _esc(str(scene.get("body", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>'
           if sub else "")
        + '<div class="visual"><div class="art-card anim" data-delay="0.14">'
        + build_grouped_bars_svg(chart)
        + (f'<div class="art-label">{label}</div>' if label else "")
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))




def _scene_before_after(scene: dict, timing: dict, spec: dict,
                        index: int, total: int) -> str:
    before = scene.get("before") or {"title": "قبل", "items": ["الحالة الأولى"]}
    after = scene.get("after") or {"title": "بعد", "items": ["النتيجة المحسّنة"]}
    b_items = "".join(
        f'<div class="ba-item">{_esc(x)}</div>'
        for x in (before.get("items") or [before.get("body", "")]) if x
    )
    a_items = "".join(
        f'<div class="ba-item">{_esc(x)}</div>'
        for x in (after.get("items") or [after.get("body", "")]) if x
    )
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>' if sub else "")
        + '<div class="visual"><div class="ba-grid">'
        + '<div class="ba-card ba-before anim" data-delay="0.14">'
        + f'<span class="ba-badge">{_esc(before.get("badge", "قبل"))}</span>'
        + f'<div class="ba-title">{_esc(before.get("title", ""))}</div>'
        + b_items + "</div>"
        + '<div class="ba-card ba-after anim" data-delay="0.28">'
        + f'<span class="ba-badge">{_esc(after.get("badge", "بعد"))}</span>'
        + f'<div class="ba-title">{_esc(after.get("title", ""))}</div>'
        + a_items + "</div>"
        + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_funnel(scene: dict, timing: dict, spec: dict,
                  index: int, total: int) -> str:
    stages = scene.get("stages") or scene.get("steps") or []
    rows: list[str] = []
    n = max(1, len(stages))
    for i, st in enumerate(stages):
        if isinstance(st, str):
            st = {"label": st, "value": f"{100 - i * 20}%"}
        lbl = st.get("label") or st.get("text") or f"مرحلة {i + 1}"
        val = st.get("value") or ""
        w_pct = max(48, int(round(100 - (i * 42 / max(1, n - 1))))) if n > 1 else 100
        rows.append(
            f'<div class="funnel-stage anim" style="width:{w_pct}%" '
            f'data-delay="{0.14 + 0.16 * i:.2f}">'
            f'<span class="funnel-lbl">{_esc(lbl)}</span>'
            + (f'<span class="funnel-val">{_esc(val)}</span>' if val != "" else "")
            + "</div>"
        )
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>' if sub else "")
        + '<div class="visual"><div class="funnel-stack">'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_matrix_2x2(scene: dict, timing: dict, spec: dict,
                      index: int, total: int) -> str:
    quads = (scene.get("quadrants") or scene.get("panels") or [])[:4]
    cells: list[str] = []
    for i, q in enumerate(quads):
        if isinstance(q, str):
            q = {"title": q}
        qtag = q.get("tag") or f"Q{i + 1}"
        cells.append(
            f'<div class="matrix-cell anim" data-delay="{0.14 + 0.14 * i:.2f}">'
            f'<span class="matrix-qtag">{_esc(qtag)}</span>'
            f'<div class="matrix-title">{_esc(q.get("title", ""))}</div>'
            + (f'<div class="matrix-body">{_esc(q.get("body", ""))}</div>'
               if q.get("body") else "")
            + "</div>"
        )
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>' if sub else "")
        + '<div class="visual"><div class="matrix-grid">'
        + "".join(cells) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_terminal_demo(scene: dict, timing: dict, spec: dict,
                         index: int, total: int) -> str:
    cmds = scene.get("commands") or scene.get("items") or []
    rows: list[str] = []
    for c in cmds:
        if isinstance(c, str):
            c = {"cmd": c, "out": ""}
        cmd_txt = c.get("cmd") or c.get("command") or ""
        out_txt = c.get("out") or c.get("output") or ""
        rows.append(
            '<div class="term-row">'
            f'<div class="term-cmd">$ {_esc(cmd_txt)}</div>'
            + (f'<div class="term-out">{_esc(out_txt)}</div>' if out_txt else "")
            + "</div>"
        )
    shell = _esc(str(scene.get("shell") or "bash"))
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', '')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>' if sub else "")
        + '<div class="visual"><div class="term-card anim" data-delay="0.14">'
        + '<div class="code-hdr"><div class="code-dots">'
        + '<span class="code-dot" style="background:#EF4444"></span>'
        + '<span class="code-dot" style="background:#F59E0B"></span>'
        + '<span class="code-dot" style="background:#10B981"></span></div>'
        + f'<div class="code-lang">{shell}</div></div>'
        + '<div class="term-body">' + "".join(rows) + "</div></div></div>"
        + _caption_chunks_html(timing, scene, spec))


def _scene_chapter_toc(scene: dict, timing: dict, spec: dict,
                       index: int, total: int) -> str:
    chapters = scene.get("chapters") or scene.get("items") or scene.get("steps") or []
    active_ch = scene.get("active_chapter")
    rows: list[str] = []
    for i, ch in enumerate(chapters):
        if isinstance(ch, str):
            ch = {"title": ch}
        title = ch.get("title") or ch.get("text") or f"الفصل {i + 1}"
        time_lbl = ch.get("time") or ""
        act_cls = " is-active" if (active_ch is not None and i == int(active_ch)) else ""
        rows.append(
            f'<div class="toc-row{act_cls} anim" data-delay="{0.12 + 0.14 * i:.2f}">'
            f'<div class="toc-num">{i + 1:02d}</div>'
            f'<div class="toc-title">{_esc(title)}</div>'
            + (f'<div class="toc-time">{_esc(time_lbl)}</div>' if time_lbl else "")
            + "</div>"
        )
    sub = _esc(str(scene.get("subtitle", "")))
    return (
        _tagrow(spec, scene, index, total)
        + f'<h1 class="title anim" data-delay="0.05">'
          f"{_esc(str(scene.get('title', 'فهرس الفصول')))}</h1>"
        + (f'<div class="subtitle anim" data-delay="0.12">{sub}</div>' if sub else "")
        + '<div class="visual"><div class="toc-list">'
        + "".join(rows) + "</div></div>"
        + _caption_chunks_html(timing, scene, spec))


_BUILDERS = {
    "intro": _scene_intro,
    "question": _scene_question,
    "concept": _scene_concept,
    "process": _scene_process,
    "statistic": _scene_statistic,
    "comparison": _scene_comparison,
    "conclusion": _scene_conclusion,
    "timeline": _scene_timeline,
    "cycle": _scene_cycle,
    "layers": _scene_layers,
    "callout": _scene_callout,
    "chapter": _scene_chapter,
    "code": _scene_code,
    "split": _scene_split,
    "checklist": _scene_checklist,
    "chart_line": _scene_chart_line,
    "chart_donut": _scene_chart_donut,
    "chart_bars": _scene_chart_bars,
    "before_after": _scene_before_after,
    "funnel": _scene_funnel,
    "matrix_2x2": _scene_matrix_2x2,
    "terminal_demo": _scene_terminal_demo,
    "chapter_toc": _scene_chapter_toc,
}


def build_flat_reels_html(spec: dict, timings: list[dict],
                          embed_font: bool = True,
                          beat_profiles: dict | None = None) -> str:
    """Build the full self-contained browser render page for any format."""
    spec = apply_brand_kit(spec)
    pal = PALETTES[spec.get("palette", "cream")]
    width, height, fmt_cls = resolve_dimensions(spec)
    motion_scale = MOTION_PRESETS[spec.get("motion", "normal")]
    default_trans = str(spec.get("transition", "fade"))
    total = timings[-1]["start"] + timings[-1]["duration"] if timings else 0
    scenes_by_id = {sc["id"]: sc for sc in spec["scenes"]}
    total_scenes = len(spec["scenes"])
    scene_divs: list[str] = []
    js_scenes: list[dict] = []
    for i, tm in enumerate(timings):
        sc = scenes_by_id[tm["id"]]
        builder = _BUILDERS[sc["type"]]
        layout = str(sc.get("layout", "body"))
        inner = builder(sc, tm, spec, i, total_scenes)
        scene_divs.append(
            f'<div class="scene layout-{_attr(layout)}" '
            f'id="scene-{_attr(tm["id"])}">{inner}</div>')
        bp = (beat_profiles or {}).get(tm["id"]) or {}
        peaks = bp.get("peaks") if bp else (tm.get("peaks") or sc.get("peaks") or [])
        js_scenes.append({
            "id": tm["id"],
            "start": tm["start"],
            "end": round(tm["start"] + tm["duration"], 3),
            "transition": sc.get("transition") or default_trans,
            "camera_move": sc.get("camera_move") or spec.get("camera_move", "none"),
            "peaks": peaks,
        })
    physics_mode = str(spec.get("physics", "spring"))
    camera_move = str(spec.get("camera_move", "none"))
    js = (
        _JS.replace("__DUR__", repr(round(total, 3)))
        .replace("__MOTION__", repr(motion_scale))
        .replace("__TRANS__", json.dumps(default_trans))
        .replace("__PHYSICS__", json.dumps(physics_mode))
        .replace("__CAMERA__", json.dumps(camera_move))
    )
    js = ("const SCENES = " + json.dumps(js_scenes, ensure_ascii=False)
          + ";\n" + js)
    bg_motifs = (
        '<div class="bg-motif bg-blob-a"></div>'
        '<div class="bg-motif bg-blob-b"></div>'
        '<svg class="bg-motif bg-dots" viewBox="0 0 80 80">'
        '<circle cx="12" cy="12" r="4" fill="var(--line)"/>'
        '<circle cx="36" cy="12" r="4" fill="var(--line)"/>'
        '<circle cx="60" cy="12" r="4" fill="var(--line)"/>'
        '<circle cx="12" cy="36" r="4" fill="var(--line)"/>'
        '<circle cx="36" cy="36" r="4" fill="var(--accent)"/>'
        '<circle cx="60" cy="36" r="4" fill="var(--line)"/>'
        '<circle cx="12" cy="60" r="4" fill="var(--line)"/>'
        '<circle cx="36" cy="60" r="4" fill="var(--line)"/>'
        '<circle cx="60" cy="60" r="4" fill="var(--blue)"/>'
        '</svg>'
        '<svg class="bg-particle" style="top:16%;left:14%" width="20" height="20" viewBox="0 0 20 20">'
        '<circle cx="10" cy="10" r="6" fill="none" stroke="var(--accent)" stroke-width="2.5"/></svg>'
        '<svg class="bg-particle" style="top:34%;right:10%" width="22" height="22" viewBox="0 0 22 22">'
        '<line x1="11" y1="3" x2="11" y2="19" stroke="var(--blue)" stroke-width="2.8" stroke-linecap="round"/>'
        '<line x1="3" y1="11" x2="19" y2="11" stroke="var(--blue)" stroke-width="2.8" stroke-linecap="round"/></svg>'
        '<svg class="bg-particle" style="bottom:28%;right:14%" width="18" height="18" viewBox="0 0 18 18">'
        '<polygon points="9,2 16,9 9,16 2,9" fill="var(--marker)" stroke="var(--ink)" stroke-width="1.5"/></svg>'
        '<svg class="bg-particle" style="bottom:36%;left:11%" width="18" height="18" viewBox="0 0 18 18">'
        '<circle cx="9" cy="9" r="5" fill="var(--blue)"/></svg>'
        '<div class="bg-corner-tl"></div>'
        '<div class="bg-corner-br"></div>'
    )
    return (
        "<!DOCTYPE html><html dir=\"rtl\" lang=\"ar\"><head>"
        "<meta charset=\"utf-8\"><style>"
        + _font_face_css(embed_font) + _css(pal, width, height)
        + f"</style></head><body class=\"fmt-{fmt_cls}\"><div id=\"stage\">"
        "<div class=\"paper-grid\"></div>"
        + bg_motifs
        + "<div id=\"progress\"><div id=\"progressbar\"></div></div>"
        + "".join(scene_divs)
        + (f'<div class="brand-watermark">{_esc(str(spec["watermark"]))}</div>' if spec.get("watermark") else "")
        + "</div><script>" + js + "</script></body></html>")


# Unified alias for both reels and long-form explainers
build_video_html = build_flat_reels_html


# ---------------------------------------------------------------------------
# Render orchestration (Audio mixing + Browser capture + FFmpeg encode)
# ---------------------------------------------------------------------------

def build_audio_mix(spec: dict, timings: list[dict], base_dir: str,
                    work_dir: str,
                    beat_profiles: dict | None = None) -> tuple[bool, dict]:
    """Mix scene narrations (with J/L-cuts), optional room tone, auto-SFX, and ducked music bed."""
    from .soundscape import plan_jl_cut_schedule

    jl_sched = {row["scene_id"]: row for row in plan_jl_cut_schedule(spec, timings)}
    tracks: list[dict] = []
    for tm in timings:
        sc = next(s for s in spec["scenes"] if s["id"] == tm["id"])
        jl = jl_sched.get(tm["id"], {})
        tracks.append({"path": os.path.join(base_dir, sc["narration"]),
                       "volume": 1.0, "start": tm["start"],
                       "j_cut": jl.get("j_cut", 0.0),
                       "l_cut": jl.get("l_cut", 0.0),
                       "fade_in": jl.get("fade_in", 0.0),
                       "fade_out": jl.get("fade_out", 0.0),
                       "role": "speech"})

    total = timings[-1]["start"] + timings[-1]["duration"] if timings else 0.0

    # Optional subtle room tone so pauses never drop into dead digital silence.
    room = spec.get("room_tone")
    if room and total > 0:
        try:
            from .soundscape import generate_room_tone

            preset = room if isinstance(room, str) else str(room.get("preset", "warm_studio"))
            vol_db = None if isinstance(room, str) else room.get("volume_db")
            rt_path = os.path.join(work_dir, "room_tone.wav")
            ok_rt, _ = generate_room_tone(rt_path, total, preset=preset, volume_db=vol_db)
            if ok_rt:
                tracks.append({"path": rt_path, "volume": 1.0, "start": 0.0, "role": "music"})
        except Exception:  # noqa: BLE001
            pass

    # Optional auto-SFX or explicit per-scene SFX cues.
    sfx_count = 0
    try:
        from .soundscape import plan_scene_sfx, synthesize_sfx

        cues = plan_scene_sfx(spec, timings, beat_profiles=beat_profiles)
        synth_cache: dict[str, str] = {}
        for cue in cues:
            kind = cue["kind"]
            if kind not in synth_cache:
                sfx_path = os.path.join(work_dir, f"sfx_{kind}.wav")
                ok_s, _ = synthesize_sfx(kind, sfx_path)
                if ok_s:
                    synth_cache[kind] = sfx_path
            if kind in synth_cache:
                tracks.append({
                    "path": synth_cache[kind],
                    "volume": float(cue.get("volume", 0.65)),
                    "start": float(cue["start"]),
                    "role": "sfx",
                })
                sfx_count += 1
    except Exception:  # noqa: BLE001
        pass

    # Optional ambient music bed (automatically sidechain-ducked under speech).
    bed = spec.get("music_bed")
    if bed and total > 0:
        try:
            from .soundscape import generate_ambient_music_bed

            preset = bed if isinstance(bed, str) else str(bed.get("preset", "documentary_drone"))
            vol_db = -20.0 if isinstance(bed, str) else float(bed.get("volume_db", -20.0))
            bed_path = os.path.join(work_dir, "bed.wav")
            ok_b, _ = generate_ambient_music_bed(
                bed_path, total, preset=preset, volume_db=vol_db)
            if ok_b:
                tracks.append({"path": bed_path, "volume": 1.0, "start": 0.0, "role": "music"})
        except Exception:  # noqa: BLE001
            pass

    out_wav = os.path.join(work_dir, "mix.wav")
    ok, info = mix_audio(tracks, out_wav, duck_music=True)
    if ok:
        info["sfx_cues"] = sfx_count
        info["audit"] = audit_audio_mix(out_wav)
    return ok, info


def render_flat_reels(spec: dict, output_path: str,
                      base_dir: str = ".",
                      scale: float = 1.0,
                      keep_frames: bool = False,
                      work_dir: str | None = None,
                      pipe_mode: bool = False
                      ) -> tuple[bool, dict]:
    """Render any reel or long-form explainer spec to mp4 via the browser engine."""
    spec = apply_brand_kit(spec)
    ok, errors = validate_reel_spec(spec)
    if not ok:
        return False, {"code": "INVALID_SPEC", "message": "; ".join(errors),
                       "errors": errors}
    if not PLAYWRIGHT_AVAILABLE:
        return False, {"code": "PLAYWRIGHT_MISSING",
                       "message": "Playwright is not installed "
                                  "(pip install playwright; "
                                  "playwright install chromium)"}
    timings, total, terrors = compute_scene_timings(spec, base_dir)
    if terrors:
        return False, {"code": "TIMING_FAILED",
                       "message": "; ".join(terrors)}
    pace = measure_narration_pace(
        spec, timings, target_duration=spec.get("target_duration"))
    width, height, fmt_cls = resolve_dimensions(spec)
    import tempfile

    tmp = work_dir or get_fast_scratch_dir(prefix="agentcut-browser-render-")
    os.makedirs(tmp, exist_ok=True)
    beat_profiles = build_scene_beat_profile(spec, timings, base_dir=base_dir)
    ok, mix = build_audio_mix(spec, timings, base_dir, tmp,
                              beat_profiles=beat_profiles)
    if not ok:
        return False, mix
    html = build_flat_reels_html(spec, timings, beat_profiles=beat_profiles)
    ok, info = render_browser_page(
        html, total, mix["output"], output_path,
        fps=int(spec.get("fps", 30)),
        width=width, height=height,
        scale=scale, keep_frames=keep_frames, work_dir=tmp, pipe_mode=pipe_mode)
    if not ok:
        return False, info
    info["duration"] = total
    info["scenes"] = len(timings)
    info["palette"] = spec.get("palette", "cream")
    info["format"] = fmt_cls
    info["width"] = width
    info["height"] = height
    info["pace"] = pace
    info["music_ducked"] = bool(mix.get("ducked", False))
    info["sfx_cues"] = int(mix.get("sfx_cues", 0))
    info["jl_cuts_applied"] = int(mix.get("jl_cuts_applied", 0))
    info["audio_audit"] = mix.get("audit")
    info["beat_profiles"] = beat_profiles
    info["scene_manifest"] = compute_spec_scene_manifest(spec, timings)
    info["visual_audit"] = audit_html_motion_continuity(html, spec)
    return True, info


# Unified alias for long-form explainers & vertical reels alike
render_video = render_flat_reels


def render_flat_reels_frame(spec: dict, t: float, out_png: str,
                            base_dir: str = ".",
                            scale: float = 1.0) -> tuple[bool, dict]:
    """Render a single frame at time t — previews and smoke tests."""
    from .browser_render import capture_single_frame

    ok, errors = validate_reel_spec(spec)
    if not ok:
        return False, {"code": "INVALID_SPEC", "message": "; ".join(errors)}
    timings, _total, terrors = compute_scene_timings(spec, base_dir)
    if terrors:
        return False, {"code": "TIMING_FAILED",
                       "message": "; ".join(terrors)}
    width, height, _ = resolve_dimensions(spec)
    beat_profiles = build_scene_beat_profile(spec, timings, base_dir=base_dir)
    html = build_flat_reels_html(spec, timings, beat_profiles=beat_profiles)
    import tempfile

    tmp = tempfile.mkdtemp(prefix="agentcut-flat-frame-")
    html_path = os.path.join(tmp, "render.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return capture_single_frame(html_path, t, out_png,
                                width=width, height=height, scale=scale)


render_video_frame = render_flat_reels_frame
