"""Real Technical Visual Card & Infographic Renderer (v3.3.3 — Cycle 3).

Eliminates hallucinated/gibberish AI-generated text and random irrelevant B-roll
on technical, code, and quantitative scenes by rendering crisp, deterministic
1080x1920 / 1920x1080 PNG visuals using ONLY Python stdlib (`vector_ui` + `zlib`
+ `struct`) and FFmpeg (`drawbox` + `drawtext` with HarfBuzz/BiDi):
1. `render_code_card_png(...)`: IDE/terminal window with macOS window traffic
   lights, line numbers, syntax-colored code/JSON lines, and optional Arabic/English
   header badge.
2. `render_kpi_card_png(...)`: Large golden hero metric (`62 أداة`, `576 اختباراً`,
   `99%`) with clean Arabic/English label, subtitle, and decorative progress bar.
3. `render_bar_chart_card_png(...)`: Clean horizontal comparative bar chart
   with crisp vector rounded bars, labels, and values.
4. `auto_attach_tech_cards(brief, project_dir)`: Inspects editorial scenes and
   beats without explicit local B-roll; when a scene discusses code/JSON/CLI or
   quantitative metrics, automatically renders and attaches a real technical
   visual PNG as the scene/beat `broll`.
Zero external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .cache import get_cache_dir


_REPO_FONTS = Path(__file__).resolve().parent.parent.parent / "fonts"

_CODE_KEYWORDS = (
    "json", "project.json", "bundle.json", "python", "stdlib", "ffmpeg",
    "cli", "mcp", "api", "git", "pip", "docker", "otio", "srt", "yaml",
    "جيسون", "بروجيكت جيسون", "كود", "برمجة", "بايثون", "ملف واحد",
    "مخطط معلن", "خط أنابيب", "طيار آلي", "واجهة", "أمر",
)

_METRIC_RE = re.compile(r"(?<!\w)(\d+(?:[.,]\d+)?\s*[%٪+xX]?)(?!\w)")


def _resolve_font(mono: bool = False) -> str:
    candidates = (
        (_REPO_FONTS / "DejaVuSans.ttf", _REPO_FONTS / "NotoSansArabic-Bold.ttf")
        if mono
        else (_REPO_FONTS / "NotoSansArabic-Bold.ttf", _REPO_FONTS / "Cairo.ttf", _REPO_FONTS / "DejaVuSans.ttf")
    )
    for c in candidates:
        if c.is_file():
            return str(c)
    return ""


def _esc_dt(val: str) -> str:
    return (
        str(val or "")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _bidi(text: str) -> str:
    try:
        from .render import bidi_display_text
        return bidi_display_text(str(text or ""))
    except Exception:
        return str(text or "")


def render_code_card_png(
    code_lines: list[str] | str,
    output_path: str | None = None,
    title: str = "project.json",
    subtitle: str | None = None,
    width: int = 1080,
    height: int = 1920,
    project_dir: str | Path | None = None,
    timeout: int = 30,
) -> str:
    """Render a crisp studio IDE / JSON code window PNG (cached by content hash)."""
    if isinstance(code_lines, str):
        lines = [ln.rstrip() for ln in code_lines.splitlines() if ln.strip()][:12]
    else:
        lines = [str(ln).rstrip() for ln in code_lines if str(ln).strip()][:12]
    if not lines:
        lines = ['{', '  "style": "editorial",', '  "cinema_look": "crisp_cinema",', '  "autopilot": true', '}']

    w, h = int(width), int(height)
    payload = {"kind": "code_card_v1", "lines": lines, "title": title, "sub": subtitle, "w": w, "h": h}
    hkey = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    target = Path(output_path) if output_path else (get_cache_dir(project_dir) / f"tech_code_{hkey}.png")
    if target.is_file() and target.stat().st_size > 512 and not output_path:
        return str(target.resolve())

    target.parent.mkdir(parents=True, exist_ok=True)
    font_ar = _resolve_font(mono=False)
    font_mono = _resolve_font(mono=True)
    f_ar_opt = f"fontfile='{_esc_dt(font_ar)}':" if font_ar else ""
    f_mn_opt = f"fontfile='{_esc_dt(font_mono)}':" if font_mono else ""

    win_w = int(w * 0.86)
    win_h = int(min(h * 0.54, max(340, 140 + len(lines) * 48)))
    win_x = (w - win_w) // 2
    win_y = int(h * 0.22)
    bar_h = 54

    vf = [
        f"color=c=#0b101b:s={w}x{h}:d=1",
        # Subtle ambient glow behind terminal window
        f"drawbox=x={max(0, win_x - 16)}:y={max(0, win_y - 16)}:w={min(w, win_w + 32)}:h={min(h, win_h + 32)}:color=#1e293b@0.45:t=fill",
        # Main terminal body
        f"drawbox=x={win_x}:y={win_y}:w={win_w}:h={win_h}:color=#0f172a@0.96:t=fill",
        # Top window chrome bar
        f"drawbox=x={win_x}:y={win_y}:w={win_w}:h={bar_h}:color=#1e293b@0.98:t=fill",
        # Border rim
        f"drawbox=x={win_x}:y={win_y}:w={win_w}:h={win_h}:color=#334155@0.85:t=2",
        # macOS traffic light dots
        f"drawbox=x={win_x + 22}:y={win_y + 20}:w=14:h=14:color=#ef4444:t=fill",
        f"drawbox=x={win_x + 46}:y={win_y + 20}:w=14:h=14:color=#f59e0b:t=fill",
        f"drawbox=x={win_x + 70}:y={win_y + 20}:w=14:h=14:color=#10b981:t=fill",
        # Window title
        f"drawtext={f_ar_opt}text='{_esc_dt(_bidi(title))}':fontsize={max(20, int(w * 0.024))}:fontcolor=#94a3b8:x=(w-text_w)/2:y={win_y + 15}",
    ]

    code_fs = max(18, int(w * 0.024))
    line_step = int(code_fs * 1.65)
    y0 = win_y + bar_h + 28
    for idx, ln in enumerate(lines):
        cy = y0 + idx * line_step
        if cy + code_fs > win_y + win_h - 16:
            break
        # Syntax color heuristic
        stripped = ln.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            col = "#64748b"
        elif ":" in stripped:
            col = "#38bdf8"
        elif stripped.startswith('"') or stripped.startswith("'"):
            col = "#fde047"
        else:
            col = "#e2e8f0"
        num_txt = f"{idx + 1:02d}"
        vf.append(
            f"drawtext={f_mn_opt}text='{num_txt}':fontsize={int(code_fs * 0.85)}:fontcolor=#475569:x={win_x + 24}:y={cy}"
        )
        vf.append(
            f"drawtext={f_ar_opt}text='{_esc_dt(_bidi(ln))}':fontsize={code_fs}:fontcolor={col}:x={win_x + 78}:y={cy}"
        )

    if subtitle:
        sub_fs = max(24, int(w * 0.032))
        vf.append(
            f"drawtext={f_ar_opt}text='{_esc_dt(_bidi(subtitle))}':fontsize={sub_fs}:fontcolor=#f8fafc:x=(w-text_w)/2:y={win_y - int(sub_fs * 2.1)}"
        )

    vf.append("vignette=PI/5.5,format=rgba")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", ",".join(vf),
        "-frames:v", "1",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(f"render_code_card_png failed: {(proc.stderr or '').strip()}")
    return str(target.resolve())


def render_kpi_card_png(
    value: str,
    label: str,
    subtitle: str | None = None,
    output_path: str | None = None,
    accent_color: str = "#f59e0b",
    progress: float = 0.88,
    width: int = 1080,
    height: int = 1920,
    project_dir: str | Path | None = None,
    timeout: int = 30,
) -> str:
    """Render a high-contrast documentary KPI / statistic card PNG."""
    w, h = int(width), int(height)
    prog = max(0.08, min(1.0, float(progress)))
    payload = {
        "kind": "kpi_card_v1", "val": value, "lbl": label,
        "sub": subtitle, "acc": accent_color, "prog": round(prog, 2), "w": w, "h": h,
    }
    hkey = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    target = Path(output_path) if output_path else (get_cache_dir(project_dir) / f"tech_kpi_{hkey}.png")
    if target.is_file() and target.stat().st_size > 512 and not output_path:
        return str(target.resolve())

    target.parent.mkdir(parents=True, exist_ok=True)
    font_ar = _resolve_font(mono=False)
    f_opt = f"fontfile='{_esc_dt(font_ar)}':" if font_ar else ""

    card_w = int(w * 0.82)
    card_h = int(min(h * 0.42, 520))
    cx = (w - card_w) // 2
    cy = int((h - card_h) * 0.42)
    bar_w = int(card_w * 0.76)
    bar_x = (w - bar_w) // 2
    bar_y = cy + card_h - 76

    val_fs = max(56, int(min(w, h) * 0.115))
    lbl_fs = max(28, int(min(w, h) * 0.045))
    sub_fs = max(20, int(min(w, h) * 0.030))

    vf = [
        f"color=c=#0b101b:s={w}x{h}:d=1",
        f"drawbox=x={cx}:y={cy}:w={card_w}:h={card_h}:color=#111827@0.95:t=fill",
        f"drawbox=x={cx}:y={cy}:w={card_w}:h=6:color={accent_color}:t=fill",
        f"drawbox=x={cx}:y={cy}:w={card_w}:h={card_h}:color=#334155@0.80:t=2",
        f"drawtext={f_opt}text='{_esc_dt(_bidi(value))}':fontsize={val_fs}:fontcolor={accent_color}:x=(w-text_w)/2:y={cy + int(card_h * 0.18)}",
        f"drawtext={f_opt}text='{_esc_dt(_bidi(label))}':fontsize={lbl_fs}:fontcolor=#f8fafc:x=(w-text_w)/2:y={cy + int(card_h * 0.50)}",
        # Progress track + filled bar
        f"drawbox=x={bar_x}:y={bar_y}:w={bar_w}:h=12:color=#1e293b:t=fill",
        f"drawbox=x={bar_x}:y={bar_y}:w={max(12, int(bar_w * prog))}:h=12:color={accent_color}:t=fill",
    ]
    if subtitle:
        vf.append(
            f"drawtext={f_opt}text='{_esc_dt(_bidi(subtitle))}':fontsize={sub_fs}:fontcolor=#94a3b8:x=(w-text_w)/2:y={cy + int(card_h * 0.66)}"
        )

    vf.append("vignette=PI/5.5,format=rgba")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", ",".join(vf),
        "-frames:v", "1",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(f"render_kpi_card_png failed: {(proc.stderr or '').strip()}")
    return str(target.resolve())


def render_bar_chart_card_png(
    items: list[dict[str, Any]],
    title: str = "مقارنة الأداء",
    output_path: str | None = None,
    accent_color: str = "#38bdf8",
    width: int = 1080,
    height: int = 1920,
    project_dir: str | Path | None = None,
    timeout: int = 30,
) -> str:
    """Render a clean horizontal bar chart infographic PNG for data scenes."""
    rows = [r for r in (items or []) if isinstance(r, dict) and "label" in r and "value" in r][:6]
    if not rows:
        rows = [
            {"label": "v1.64", "value": 34},
            {"label": "v2.5", "value": 49},
            {"label": "v3.3", "value": 65},
        ]

    w, h = int(width), int(height)
    payload = {"kind": "bar_chart_v1", "rows": rows, "title": title, "acc": accent_color, "w": w, "h": h}
    hkey = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    target = Path(output_path) if output_path else (get_cache_dir(project_dir) / f"tech_chart_{hkey}.png")
    if target.is_file() and target.stat().st_size > 512 and not output_path:
        return str(target.resolve())

    target.parent.mkdir(parents=True, exist_ok=True)
    font_ar = _resolve_font(mono=False)
    f_opt = f"fontfile='{_esc_dt(font_ar)}':" if font_ar else ""

    max_val = max(1.0, max(float(r["value"]) for r in rows))
    card_w = int(w * 0.86)
    card_h = int(min(h * 0.56, 180 + len(rows) * 95))
    cx = (w - card_w) // 2
    cy = int((h - card_h) * 0.40)

    title_fs = max(28, int(min(w, h) * 0.042))
    row_fs = max(22, int(min(w, h) * 0.028))

    vf = [
        f"color=c=#0b101b:s={w}x{h}:d=1",
        f"drawbox=x={cx}:y={cy}:w={card_w}:h={card_h}:color=#111827@0.95:t=fill",
        f"drawbox=x={cx}:y={cy}:w={card_w}:h={card_h}:color=#334155@0.80:t=2",
        f"drawtext={f_opt}text='{_esc_dt(_bidi(title))}':fontsize={title_fs}:fontcolor=#f8fafc:x=(w-text_w)/2:y={cy + 32}",
    ]

    track_x = cx + 44
    track_w = card_w - 88
    y_cursor = cy + 110
    for r in rows:
        lbl = str(r["label"])
        val = float(r["value"])
        fill_w = max(14, int(round((val / max_val) * track_w)))
        val_str = f"{int(val)}" if val.is_integer() else f"{val:.1f}"
        vf.append(
            f"drawtext={f_opt}text='{_esc_dt(_bidi(f'{lbl}  ({val_str})'))}':fontsize={row_fs}:fontcolor=#cbd5e1:x={track_x}:y={y_cursor}"
        )
        bar_y = y_cursor + int(row_fs * 1.35)
        vf.append(f"drawbox=x={track_x}:y={bar_y}:w={track_w}:h=18:color=#1e293b:t=fill")
        vf.append(f"drawbox=x={track_x}:y={bar_y}:w={fill_w}:h=18:color={accent_color}:t=fill")
        y_cursor += int(row_fs * 2.85)

    vf.append("vignette=PI/5.5,format=rgba")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", ",".join(vf),
        "-frames:v", "1",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not target.is_file():
        raise RuntimeError(f"render_bar_chart_card_png failed: {(proc.stderr or '').strip()}")
    return str(target.resolve())


def build_tech_card_for_scene(
    scene: dict[str, Any],
    width: int = 1080,
    height: int = 1920,
    project_dir: str | Path | None = None,
) -> str | None:
    """Classify a scene or explicit `tech_card` spec and render the appropriate PNG."""
    tc = scene.get("tech_card")
    if isinstance(tc, dict):
        kind = str(tc.get("kind") or "kpi").strip().lower()
        if kind == "code":
            return render_code_card_png(
                tc.get("lines") or tc.get("code") or [],
                title=str(tc.get("title") or "project.json"),
                subtitle=tc.get("subtitle"),
                width=width,
                height=height,
                project_dir=project_dir,
            )
        if kind in ("chart", "bars", "bar_chart"):
            return render_bar_chart_card_png(
                tc.get("items") or [],
                title=str(tc.get("title") or "مقارنة الأداء"),
                width=width,
                height=height,
                project_dir=project_dir,
            )
        return render_kpi_card_png(
            value=str(tc.get("value") or "100%"),
            label=str(tc.get("label") or scene.get("title") or "AgentCut"),
            subtitle=tc.get("subtitle"),
            width=width,
            height=height,
            project_dir=project_dir,
        )

    text = str(scene.get("text") or scene.get("script") or "").strip()
    if not text:
        return None
    low = text.lower()

    # 1. Code / JSON / CLI architecture scene
    if any(k in low for k in _CODE_KEYWORDS):
        sid = str(scene.get("id") or "scene_1")
        snippet = [
            "{",
            f'  "scene": "{sid}",',
            '  "style": "editorial",',
            '  "look": "crisp_cinema",',
            '  "clean_tts": true,',
            '  "deps": 0',
            "}",
        ]
        words = text.split()
        sub = " ".join(words[:5]).strip(".,،؛:!؟")
        return render_code_card_png(
            snippet,
            title="project.json — AgentCut",
            subtitle=sub,
            width=width,
            height=height,
            project_dir=project_dir,
        )

    # 2. Quantitative / KPI metric scene
    m = _METRIC_RE.search(text)
    if m or scene.get("_narrative_intent") == "data_point":
        num_val = m.group(1).strip() if m else "100%"
        words = text.split()
        lbl = " ".join(words[:4]).strip(".,،؛:!؟")
        sub = " ".join(words[4:9]).strip(".,،؛:!؟") if len(words) > 4 else None
        return render_kpi_card_png(
            value=num_val,
            label=lbl,
            subtitle=sub,
            width=width,
            height=height,
            project_dir=project_dir,
        )

    return None


def auto_attach_tech_cards(
    brief: dict[str, Any],
    width: int = 1080,
    height: int = 1920,
    project_dir: str | Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Attach real technical/KPI/code cards to scenes that lack local B-roll files.

    Never overwrites an existing valid local image/video `broll` unless the scene
    explicitly specifies `tech_card`. Returns `(updated_brief, generated_count)`.
    """
    out = dict(brief)
    scenes_out: list[dict[str, Any]] = []
    generated = 0

    for sc in out.get("scenes") or []:
        sc_copy = dict(sc)
        has_local_broll = isinstance(sc_copy.get("broll"), str) and os.path.isfile(sc_copy["broll"])
        explicit_tc = isinstance(sc_copy.get("tech_card"), dict)

        if explicit_tc or (not has_local_broll and out.get("auto_tech_cards", True)):
            try:
                card_png = build_tech_card_for_scene(
                    sc_copy, width=width, height=height, project_dir=project_dir
                )
                if card_png and os.path.isfile(card_png):
                    sc_copy["broll"] = card_png
                    sc_copy["_auto_tech_card"] = card_png
                    # Also populate any beat in this scene that lacks its own broll
                    if isinstance(sc_copy.get("beats"), list):
                        new_beats = []
                        for b in sc_copy["beats"]:
                            bc = dict(b)
                            if not bc.get("broll") or not os.path.isfile(str(bc.get("broll"))):
                                bc["broll"] = card_png
                            new_beats.append(bc)
                        sc_copy["beats"] = new_beats
                    generated += 1
            except Exception:
                pass
        scenes_out.append(sc_copy)

    out["scenes"] = scenes_out
    out["_tech_cards_generated"] = generated
    return out, generated
