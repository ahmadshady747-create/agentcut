"""One-Spec Multi-Format Export & Thumbnail Generator for AgentCut (v3.4.3).

From a single `spec.json`, produces:
- High-contrast flat-editorial Thumbnail HTML & PNG (`render_thumbnail_html`,
  `render_thumbnail_png`) in landscape (1280x720) or verticalcover (720x1280)
  using the spec's Brand Kit, palette, hero icon/illustration, hook title,
  series badge, and marker accent.
- Multi-platform video + thumbnail bundle (`export_multi_format`,
  `build_multi_format_html_bundle`) generating `landscape` (16:9 YouTube),
  `vertical` (9:16 Reels/Shorts/TikTok), `square` (1:1 Feed), and
  `thumbnail.png` in one deterministic pipeline.
"""
from __future__ import annotations

import copy
import html as _html
import os
import tempfile
from typing import Any

from .brand_kits import apply_brand_kit
from .browser_render import PLAYWRIGHT_AVAILABLE, capture_single_frame
from .icons import icon_svg, illustration_svg


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=False)


def _extract_thumbnail_meta(spec: dict[str, Any]) -> dict[str, str]:
    """Extract hook title, subtitle, badge, series_tag, and hero visual from a spec."""
    applied = apply_brand_kit(spec)
    scenes = applied.get("scenes") or []
    first = scenes[0] if scenes else {}
    thumb_cfg = applied.get("thumbnail") or {}

    title = str(
        thumb_cfg.get("title")
        or applied.get("title")
        or first.get("title")
        or first.get("text")
        or "طب ازاي؟"
    ).strip()
    subtitle = str(
        thumb_cfg.get("subtitle")
        or first.get("subtitle")
        or first.get("body")
        or ""
    ).strip()
    badge = str(
        thumb_cfg.get("badge")
        or first.get("badge")
        or ""
    ).strip()
    # Look for a stat value if no badge on first scene
    if not badge:
        for sc in scenes:
            st = sc.get("stat")
            if isinstance(st, dict) and st.get("value"):
                badge = str(st["value"])
                break
    series_tag = str(
        thumb_cfg.get("series_tag")
        or applied.get("series_tag")
        or "طب ازاي؟"
    ).strip()
    icon_name = str(
        thumb_cfg.get("icon")
        or first.get("icon")
        or "sparkles"
    ).strip()
    illustration_name = str(
        thumb_cfg.get("illustration")
        or first.get("illustration")
        or ""
    ).strip()
    return {
        "title": title,
        "subtitle": subtitle,
        "badge": badge,
        "series_tag": series_tag,
        "icon": icon_name,
        "illustration": illustration_name,
        "palette": str(applied.get("palette", "cream")),
    }


def render_thumbnail_html(
    spec: dict[str, Any],
    format_name: str = "landscape",
    embed_font: bool = True,
) -> str:
    """Build a self-contained HTML thumbnail poster (1280x720 or 720x1280)
    with `window.setTime(t)` for deterministic browser capture."""
    from .flat_reels import PALETTES, _font_face_css, resolve_dimensions

    applied = apply_brand_kit(spec)
    tmp_spec = dict(applied, format=format_name)
    for k in ("width", "height"):
        tmp_spec.pop(k, None)
    width, height, fmt_cls = resolve_dimensions(tmp_spec)
    meta = _extract_thumbnail_meta(applied)
    pal = PALETTES.get(meta["palette"], PALETTES["cream"])

    if meta["illustration"]:
        visual_html = illustration_svg(meta["illustration"], width=360, height=250)
    else:
        visual_html = icon_svg(meta["icon"], size=240 if fmt_cls == "landscape" else 260)

    badge_html = (
        f'<div class="th-badge">{_esc(meta["badge"])}</div>'
        if meta["badge"]
        else ""
    )
    sub_html = (
        f'<div class="th-sub">{_esc(meta["subtitle"])}</div>'
        if meta["subtitle"]
        else ""
    )

    css = f"""
:root{{--paper:{pal['paper']};--card:{pal['card']};--ink:{pal['ink']};
--accent:{pal['accent']};--blue:{pal['blue']};--muted:{pal['muted']};
--marker:{pal['marker']};--line:{pal['line']}}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;overflow:hidden;
background:var(--paper);color:var(--ink);font-family:'Cairo',sans-serif;direction:rtl}}
.th-stage{{position:relative;width:{width}px;height:{height}px;padding:48px 56px;
display:flex;flex-direction:{"row" if fmt_cls == "landscape" else "column"};
align-items:center;justify-content:space-between;gap:42px;overflow:hidden}}
.th-grid{{position:absolute;inset:0;
background-image:radial-gradient(var(--line) 2px,transparent 2px);
background-size:32px 32px;opacity:.65;pointer-events:none}}
.th-ring{{position:absolute;bottom:-120px;left:-100px;width:440px;height:440px;
border-radius:50%;border:4px dashed var(--line);opacity:.55;pointer-events:none}}
.th-copy{{position:relative;z-index:5;flex:1;display:flex;flex-direction:column;
align-items:flex-start;gap:22px}}
.th-tag{{background:var(--accent);color:var(--paper);border:3.5px solid var(--ink);
box-shadow:5px 5px 0 var(--ink);border-radius:999px;padding:10px 28px;
font-size:32px;font-weight:900}}
.th-title{{font-size:{"74px" if fmt_cls == "landscape" else "68px"};
font-weight:900;line-height:1.22;color:var(--ink)}}
.th-bar{{height:16px;width:280px;background:var(--marker);
border:3px solid var(--ink);border-radius:999px}}
.th-sub{{font-size:34px;font-weight:800;color:var(--muted);line-height:1.4}}
.th-art{{position:relative;z-index:5;width:{"420px" if fmt_cls == "landscape" else "100%"};
height:{"460px" if fmt_cls == "landscape" else "520px"};background:var(--card);
border:4px solid var(--ink);border-radius:42px;box-shadow:14px 14px 0 var(--ink);
display:flex;align-items:center;justify-content:center}}
.th-halo{{position:absolute;width:290px;height:290px;border-radius:50%;
background:var(--paper);border:3px dashed var(--line)}}
.th-art svg{{position:relative;z-index:2}}
.th-badge{{position:absolute;top:-28px;left:-22px;width:136px;height:136px;
border-radius:50%;background:var(--marker);border:4px solid var(--ink);
box-shadow:6px 6px 0 var(--ink);display:flex;align-items:center;
justify-content:center;font-size:48px;font-weight:900;transform:rotate(-10deg);z-index:6}}
"""
    js = "window.setTime = function(t){ void document.body.offsetHeight; };"
    return (
        '<!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="utf-8"><style>'
        + _font_face_css(embed_font)
        + css
        + '</style></head><body><div class="th-stage">'
        '<div class="th-grid"></div><div class="th-ring"></div>'
        '<div class="th-copy">'
        f'<div class="th-tag">{_esc(meta["series_tag"])}</div>'
        f'<h1 class="th-title">{_esc(meta["title"])}</h1>'
        '<div class="th-bar"></div>'
        + sub_html
        + "</div>"
        '<div class="th-art"><div class="th-halo"></div>'
        + badge_html
        + visual_html
        + "</div></div><script>"
        + js
        + "</script></body></html>"
    )


def render_thumbnail_png(
    spec: dict[str, Any],
    output_png: str,
    format_name: str = "landscape",
    scale: float = 1.0,
) -> tuple[bool, dict[str, Any]]:
    """Render a thumbnail poster PNG via headless Chromium."""
    from .flat_reels import resolve_dimensions, validate_reel_spec

    applied = apply_brand_kit(spec)
    ok, errors = validate_reel_spec(applied)
    if not ok:
        return False, {"code": "INVALID_SPEC", "message": "; ".join(errors), "errors": errors}
    if not PLAYWRIGHT_AVAILABLE:
        return False, {
            "code": "PLAYWRIGHT_MISSING",
            "message": "Playwright is not installed",
        }
    tmp_spec = dict(applied, format=format_name)
    for k in ("width", "height"):
        tmp_spec.pop(k, None)
    width, height, fmt_cls = resolve_dimensions(tmp_spec)
    html = render_thumbnail_html(applied, format_name=format_name, embed_font=True)
    tmp = tempfile.mkdtemp(prefix="agentcut-thumb-")
    html_path = os.path.join(tmp, "thumbnail.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)
    ok_c, info = capture_single_frame(
        html_path, 0.0, output_png, width=width, height=height, scale=scale
    )
    if not ok_c:
        return False, info
    return True, {
        "output": output_png,
        "format": fmt_cls,
        "width": width,
        "height": height,
        "brand_kit": applied.get("brand_kit"),
    }


def build_multi_format_html_bundle(
    spec: dict[str, Any],
    timings: list[dict[str, Any]],
    formats: tuple[str, ...] = ("landscape", "vertical", "square"),
    embed_font: bool = False,
    beat_profiles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic HTML pages for multiple platform aspect ratios
    plus the thumbnail HTML from a single spec (works even without Playwright)."""
    from .flat_reels import build_flat_reels_html, resolve_dimensions

    applied = apply_brand_kit(spec)
    pages: dict[str, dict[str, Any]] = {}
    for fmt in formats:
        fmt_spec = copy.deepcopy(applied)
        fmt_spec["format"] = fmt
        fmt_spec.pop("width", None)
        fmt_spec.pop("height", None)
        w, h, cls = resolve_dimensions(fmt_spec)
        html = build_flat_reels_html(
            fmt_spec, timings, embed_font=embed_font, beat_profiles=beat_profiles
        )
        pages[fmt] = {"width": w, "height": h, "format_class": cls, "html": html}

    thumb_html = render_thumbnail_html(applied, format_name="landscape", embed_font=embed_font)
    return {
        "brand_kit": applied.get("brand_kit"),
        "palette": applied.get("palette", "cream"),
        "formats": pages,
        "thumbnail_html": thumb_html,
    }


def export_multi_format(
    spec: dict[str, Any],
    output_dir: str,
    base_dir: str = ".",
    formats: tuple[str, ...] = ("landscape", "vertical", "square"),
    include_thumbnail: bool = True,
    scale: float = 1.0,
) -> tuple[bool, dict[str, Any]]:
    """Export one spec into multiple aspect-ratio MP4 files + Thumbnail PNG."""
    from .flat_reels import render_video, validate_reel_spec

    applied = apply_brand_kit(spec)
    ok, errors = validate_reel_spec(applied)
    if not ok:
        return False, {"code": "INVALID_SPEC", "message": "; ".join(errors), "errors": errors}
    if not PLAYWRIGHT_AVAILABLE:
        return False, {
            "code": "PLAYWRIGHT_MISSING",
            "message": "Playwright is not installed (pip install playwright; playwright install chromium)",
        }

    os.makedirs(output_dir, exist_ok=True)
    stem = str(applied.get("id", "video")).strip() or "video"
    outputs: dict[str, Any] = {}

    for fmt in formats:
        fmt_spec = copy.deepcopy(applied)
        fmt_spec["format"] = fmt
        fmt_spec.pop("width", None)
        fmt_spec.pop("height", None)
        out_mp4 = os.path.join(output_dir, f"{stem}_{fmt}.mp4")
        ok_r, info = render_video(fmt_spec, out_mp4, base_dir=base_dir, scale=scale)
        if not ok_r:
            return False, info
        outputs[fmt] = info

    thumb_info = None
    if include_thumbnail:
        thumb_png = os.path.join(output_dir, f"{stem}_thumbnail.png")
        ok_t, t_info = render_thumbnail_png(applied, thumb_png, format_name="landscape", scale=scale)
        if not ok_t:
            return False, t_info
        thumb_info = t_info

    return True, {
        "id": stem,
        "brand_kit": applied.get("brand_kit"),
        "output_dir": os.path.abspath(output_dir),
        "videos": outputs,
        "thumbnail": thumb_info,
    }
