"""Arabic subtitle pill aesthetics, high-contrast themes, and bottom breathing-room layout for AgentCut v3.3.5.

Solves four visual defects observed in vertical and horizontal Arabic documentary videos:
1. Bottom-edge crowding: lifts the subtitle capsule out of the bottom 4-6% dead zone
   into a comfortable 11.5% vertical / 9.5% landscape lower-third breathing zone.
2. Weak pill contrast: replaces flat 50% black boxes (`~3.8:1` contrast over bright B-roll)
   with deep obsidian glass capsules (`0.82..0.88` opacity + bottom scrim -> `14.2:1+` WCAG AAA).
3. Cramped Arabic script padding: uses asymmetric capsule padding (`box_padding_x=24`,
   `box_padding_y=14`, `box_radius=22` at 720p base) and a subtle translucent glass rim
   so Arabic terminal glyphs (`ن، ي، ر، و`), dots, and punctuation breathe naturally.
4. Inter-line collision on wrapped Arabic lines: sets `caption_line_spacing=1.42` and
   `caption_max_width=0.82` with resolution-scaled typography so line-1 descenders
   never collide with line-2 ascenders.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

from .vector_ui import encode_rgba_png


SUBTITLE_THEMES: Dict[str, Dict[str, Any]] = {
    "editorial_cinema": {
        "name": "editorial_cinema",
        "description": "Deep obsidian glass capsule with subtle white rim and crisp white Arabic typography (WCAG AAA).",
        "text_color": "#FFFFFF",
        "box_color": "#0B0F19",
        "box_opacity": 0.82,
        "box_padding_x": 24,
        "box_padding_y": 14,
        "box_radius": 22,
        "box_border_color": "#FFFFFF",
        "box_border_opacity": 0.18,
        "box_border_width": 1,
        "shadow_x": 2,
        "shadow_y": 2,
        "shadow_color": "#000000",
        "font_size_720p": 38,
        "max_width_frac": 0.82,
        "line_spacing": 1.42,
        "margin_bottom_vertical_frac": 0.115,
        "margin_bottom_landscape_frac": 0.095,
    },
    "gold_documentary": {
        "name": "gold_documentary",
        "description": "Warm ivory Arabic typography on charcoal-obsidian capsule with subtle antique-gold rim for historical/philosophical essays.",
        "text_color": "#FFFBEB",
        "box_color": "#0F172A",
        "box_opacity": 0.84,
        "box_padding_x": 24,
        "box_padding_y": 14,
        "box_radius": 22,
        "box_border_color": "#F59E0B",
        "box_border_opacity": 0.28,
        "box_border_width": 1,
        "shadow_x": 2,
        "shadow_y": 2,
        "shadow_color": "#000000",
        "font_size_720p": 38,
        "max_width_frac": 0.82,
        "line_spacing": 1.42,
        "margin_bottom_vertical_frac": 0.115,
        "margin_bottom_landscape_frac": 0.095,
    },
    "high_contrast_broadcast": {
        "name": "high_contrast_broadcast",
        "description": "Ultra-deep broadcast capsule with cyan-silver accent rim and maximum daylight legibility.",
        "text_color": "#FFFFFF",
        "box_color": "#050811",
        "box_opacity": 0.88,
        "box_padding_x": 26,
        "box_padding_y": 15,
        "box_radius": 24,
        "box_border_color": "#38BDF8",
        "box_border_opacity": 0.24,
        "box_border_width": 1,
        "shadow_x": 2,
        "shadow_y": 2,
        "shadow_color": "#000000",
        "font_size_720p": 40,
        "max_width_frac": 0.82,
        "line_spacing": 1.44,
        "margin_bottom_vertical_frac": 0.120,
        "margin_bottom_landscape_frac": 0.100,
    },
}


def list_subtitle_themes() -> List[Dict[str, Any]]:
    """Return all available Arabic subtitle pill themes."""
    return [dict(v) for v in SUBTITLE_THEMES.values()]


def theme_for_cinema_look(cinema_look: Optional[str] = None) -> str:
    """Pick the most fitting subtitle theme for a given cinema_look."""
    if cinema_look in ("chiaroscuro_doc", "kodak_warm", "warm_archive"):
        return "gold_documentary"
    if cinema_look in ("midnight_tech", "noir_slate"):
        return "editorial_cinema"
    return "editorial_cinema"


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    s = str(hex_color).strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = "#" + s[2:]
    named = {
        "white": (255, 255, 255),
        "black": (0, 0, 0),
        "yellow": (255, 235, 59),
        "cyan": (56, 189, 248),
        "orange": (245, 158, 11),
    }
    if s.lower() in named:
        return named[s.lower()]
    if s.startswith("#") and len(s) == 7:
        try:
            return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
        except ValueError:
            pass
    return (0, 0, 0)


def _relative_luminance(rgb: Tuple[int, int, int]) -> float:
    def _chan(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def compute_pill_contrast_ratio(
    text_color: str = "#FFFFFF",
    box_color: str = "#0B0F19",
    box_opacity: float = 0.82,
    scrim_opacity: float = 0.55,
    scene_luma: int = 210,
) -> float:
    """Compute effective WCAG contrast ratio of subtitle text over pill + bottom scrim on a bright scene."""
    # Scene background after bottom gradient scrim
    scrim_factor = 1.0 - max(0.0, min(1.0, float(scrim_opacity))) * 0.75
    bg_under_pill = (
        int(round(scene_luma * scrim_factor)),
        int(round(scene_luma * scrim_factor)),
        int(round(scene_luma * scrim_factor)),
    )
    box_rgb = _hex_to_rgb(box_color)
    alpha = max(0.0, min(1.0, float(box_opacity)))
    eff_bg = (
        int(round(box_rgb[0] * alpha + bg_under_pill[0] * (1.0 - alpha))),
        int(round(box_rgb[1] * alpha + bg_under_pill[1] * (1.0 - alpha))),
        int(round(box_rgb[2] * alpha + bg_under_pill[2] * (1.0 - alpha))),
    )
    l_text = _relative_luminance(_hex_to_rgb(text_color))
    l_bg = _relative_luminance(eff_bg)
    lighter = max(l_text, l_bg)
    darker = min(l_text, l_bg)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def compute_subtitle_layout(
    width: int = 720,
    height: int = 1280,
    theme: str = "editorial_cinema",
    n_lines: int = 1,
    margin_bottom_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute resolution-adaptive Arabic subtitle capsule layout and breathing-room metrics."""
    width = max(160, int(width))
    height = max(160, int(height))
    spec = SUBTITLE_THEMES.get(theme) or SUBTITLE_THEMES["editorial_cinema"]
    short_dim = min(width, height)
    res_scale = short_dim / 720.0

    font_size = max(20, min(76, int(round(float(spec["font_size_720p"]) * res_scale))))
    pad_x = max(12, min(40, int(round(float(spec["box_padding_x"]) * res_scale))))
    pad_y = max(8, min(32, int(round(float(spec["box_padding_y"]) * res_scale))))
    radius = max(10, min(44, int(round(float(spec["box_radius"]) * res_scale))))
    line_spacing = float(spec["line_spacing"])
    max_width_frac = float(spec["max_width_frac"])

    is_vertical = height >= width
    mb_frac = (
        float(spec["margin_bottom_vertical_frac"])
        if is_vertical
        else float(spec["margin_bottom_landscape_frac"])
    )
    if margin_bottom_override is not None:
        margin_bottom = max(24, int(round(float(margin_bottom_override))))
    else:
        margin_bottom = max(36, int(round(height * mb_frac)))

    n_lines = max(1, int(n_lines))
    block_text_h = (n_lines - 1) * font_size * line_spacing + font_size * 1.4
    block_top_y = float(height - margin_bottom - pad_y) - block_text_h
    pill_top_y = max(0, int(round(block_top_y - pad_y)))
    pill_bottom_y = min(height, int(round(block_top_y + block_text_h + pad_y)))
    bottom_clearance_px = max(0, height - pill_bottom_y)
    bottom_clearance_frac = round(bottom_clearance_px / float(height), 4)

    middle_card_bottom_y = int(round(0.66 * height))
    card_to_pill_gap_px = pill_top_y - middle_card_bottom_y

    contrast = compute_pill_contrast_ratio(
        text_color=spec["text_color"],
        box_color=spec["box_color"],
        box_opacity=float(spec["box_opacity"]),
    )
    wcag_grade = "AAA" if contrast >= 7.0 else ("AA" if contrast >= 4.5 else "FAIL")

    return {
        "theme": spec["name"],
        "width": width,
        "height": height,
        "is_vertical": is_vertical,
        "font_size": font_size,
        "text_color": spec["text_color"],
        "box_color": spec["box_color"],
        "box_opacity": float(spec["box_opacity"]),
        "box_padding_x": pad_x,
        "box_padding_y": pad_y,
        "box_radius": radius,
        "box_border_color": spec["box_border_color"],
        "box_border_opacity": float(spec["box_border_opacity"]),
        "box_border_width": int(spec["box_border_width"]),
        "shadow_x": int(spec["shadow_x"]),
        "shadow_y": int(spec["shadow_y"]),
        "shadow_color": spec["shadow_color"],
        "line_spacing": line_spacing,
        "max_width_frac": max_width_frac,
        "margin_bottom": margin_bottom,
        "block_top_y": round(block_top_y, 2),
        "pill_top_y": pill_top_y,
        "pill_bottom_y": pill_bottom_y,
        "bottom_clearance_px": bottom_clearance_px,
        "bottom_clearance_frac": bottom_clearance_frac,
        "middle_card_bottom_y": middle_card_bottom_y,
        "card_to_pill_gap_px": card_to_pill_gap_px,
        "wcag_contrast_ratio": contrast,
        "wcag_grade": wcag_grade,
    }


def style_arabic_caption_entry(
    cap_entry: Dict[str, Any],
    width: int = 720,
    height: int = 1280,
    theme: str = "editorial_cinema",
    margin_bottom_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Apply high-contrast Arabic subtitle capsule styling to a caption item (preserving explicit fields)."""
    layout = compute_subtitle_layout(
        width=width,
        height=height,
        theme=theme,
        n_lines=1,
        margin_bottom_override=margin_bottom_override,
    )
    out = dict(cap_entry)
    out.setdefault("size", layout["font_size"])
    out.setdefault("color", layout["text_color"])
    out.setdefault("box", True)
    out.setdefault("box_color", layout["box_color"])
    out.setdefault("box_opacity", layout["box_opacity"])
    out.setdefault("box_radius", layout["box_radius"])
    out.setdefault("box_padding", layout["box_padding_y"])
    out.setdefault("box_padding_x", layout["box_padding_x"])
    out.setdefault("box_padding_y", layout["box_padding_y"])
    out.setdefault("box_border_color", layout["box_border_color"])
    out.setdefault("box_border_opacity", layout["box_border_opacity"])
    out.setdefault("box_border_width", layout["box_border_width"])
    out.setdefault("shadow_x", layout["shadow_x"])
    out.setdefault("shadow_y", layout["shadow_y"])
    out.setdefault("shadow_color", layout["shadow_color"])
    out.setdefault("margin_bottom", layout["margin_bottom"])
    return out


def render_styled_pill_png(
    path: str,
    bw: int,
    bh: int,
    radius: int,
    color: str = "#0B0F19",
    opacity: float = 0.82,
    border_color: str = "#FFFFFF",
    border_opacity: float = 0.18,
    border_width: int = 1,
) -> str:
    """Render an anti-aliased rounded subtitle capsule PNG with a subtle translucent glass rim."""
    bw = max(2, int(bw))
    bh = max(2, int(bh))
    br = max(0.0, min(float(radius), bw / 2.0, bh / 2.0))
    fill_r, fill_g, fill_b = _hex_to_rgb(color)
    fill_a = max(0.0, min(1.0, float(opacity)))
    rim_r, rim_g, rim_b = _hex_to_rgb(border_color)
    rim_a = max(0.0, min(1.0, float(border_opacity)))
    bw_rim = max(0.0, float(border_width))

    half_w = bw / 2.0
    half_h = bh / 2.0
    inner_w = half_w - br
    inner_h = half_h - br

    buf = bytearray(bw * bh * 4)
    import math

    for y in range(bh):
        dy = abs((y + 0.5) - half_h) - inner_h
        row_off = y * bw * 4
        for x in range(bw):
            dx = abs((x + 0.5) - half_w) - inner_w
            if dx <= 0.0 and dy <= 0.0:
                dist = max(dx, dy) - br
            else:
                qx = max(dx, 0.0)
                qy = max(dy, 0.0)
                dist = math.hypot(qx, qy) - br

            if dist >= 0.5:
                continue
            outer_cov = 1.0 if dist <= -0.5 else (0.5 - dist)
            if rim_a > 0.0 and bw_rim > 0.0 and dist >= -(bw_rim + 0.5):
                # Smooth transition between inner fill and outer glass rim
                rim_mix = 1.0 if dist >= -(bw_rim - 0.5) else (dist + bw_rim + 0.5)
                rim_mix = max(0.0, min(1.0, rim_mix))
                r = int(round(fill_r * (1.0 - rim_mix) + rim_r * rim_mix))
                g = int(round(fill_g * (1.0 - rim_mix) + rim_g * rim_mix))
                b = int(round(fill_b * (1.0 - rim_mix) + rim_b * rim_mix))
                a_norm = fill_a * (1.0 - rim_mix) + max(fill_a, rim_a) * rim_mix
            else:
                r, g, b = fill_r, fill_g, fill_b
                a_norm = fill_a

            a = max(0, min(255, int(round(outer_cov * a_norm * 255.0))))
            idx = row_off + x * 4
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
            buf[idx + 3] = a

    png_bytes = encode_rgba_png(bw, bh, bytes(buf))
    with open(path, "wb") as fh:
        fh.write(png_bytes)
    return path


def audit_subtitle_legibility(
    project_or_brief: Dict[str, Any],
    theme: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit Arabic subtitle placement, WCAG contrast, padding, and bottom breathing room."""
    out_cfg = project_or_brief.get("output") or {}
    aspect = str(project_or_brief.get("aspect", "9:16")).strip()
    if "width" in out_cfg and "height" in out_cfg:
        width = int(out_cfg["width"])
        height = int(out_cfg["height"])
    elif aspect == "16:9":
        width, height = 1280, 720
    elif aspect == "1:1":
        width, height = 1080, 1080
    elif aspect == "4:5":
        width, height = 1080, 1350
    else:
        width, height = 720, 1280

    resolved_theme = (
        theme
        or project_or_brief.get("subtitle_theme")
        or theme_for_cinema_look(project_or_brief.get("cinema_look"))
    )
    mb_override = project_or_brief.get("caption_margin_bottom")
    layout = compute_subtitle_layout(
        width=width,
        height=height,
        theme=resolved_theme,
        n_lines=2,
        margin_bottom_override=mb_override,
    )

    caps = list(project_or_brief.get("captions") or [])
    if caps:
        sample = caps[0]
        box_color = sample.get("box_color", layout["box_color"])
        box_opacity = float(sample.get("box_opacity", layout["box_opacity"]))
        text_color = sample.get("color", layout["text_color"])
        pad_x = int(sample.get("box_padding_x", sample.get("box_padding", layout["box_padding_x"])))
        pad_y = int(sample.get("box_padding_y", sample.get("box_padding", layout["box_padding_y"])))
        mb = int(sample.get("margin_bottom", mb_override if mb_override is not None else 120))
        layout = compute_subtitle_layout(
            width=width,
            height=height,
            theme=resolved_theme,
            n_lines=2,
            margin_bottom_override=mb,
        )
        layout["box_color"] = box_color
        layout["box_opacity"] = box_opacity
        layout["text_color"] = text_color
        layout["box_padding_x"] = pad_x
        layout["box_padding_y"] = pad_y
        layout["wcag_contrast_ratio"] = compute_pill_contrast_ratio(
            text_color=text_color,
            box_color=box_color,
            box_opacity=box_opacity,
        )
        layout["wcag_grade"] = (
            "AAA"
            if layout["wcag_contrast_ratio"] >= 7.0
            else ("AA" if layout["wcag_contrast_ratio"] >= 4.5 else "FAIL")
        )

    issues: List[Dict[str, Any]] = []
    score = 100

    if layout["bottom_clearance_frac"] < 0.08:
        score -= 25
        issues.append({
            "code": "SUBTITLE_TOO_CLOSE_TO_BOTTOM",
            "severity": "warning",
            "message": (
                f"Subtitle bottom clearance is {layout['bottom_clearance_frac']*100:.1f}% "
                f"({layout['bottom_clearance_px']}px), below the 8% safe breathing threshold."
            ),
            "fix": "Set subtitle_theme='editorial_cinema' or increase caption_margin_bottom.",
        })

    if layout["wcag_contrast_ratio"] < 7.0:
        penalty = 30 if layout["wcag_contrast_ratio"] < 4.5 else 15
        score -= penalty
        issues.append({
            "code": "SUBTITLE_LOW_CONTRAST",
            "severity": "error" if layout["wcag_contrast_ratio"] < 4.5 else "warning",
            "message": (
                f"Subtitle WCAG contrast ratio is {layout['wcag_contrast_ratio']}:1 "
                f"(target >= 7.0:1 AAA for documentary Arabic pills)."
            ),
            "fix": "Increase box_opacity to >= 0.80 and use deep obsidian box_color='#0B0F19'.",
        })

    min_pad_x = max(10, int(round(16 * (min(width, height) / 720.0))))
    if layout["box_padding_x"] < min_pad_x:
        score -= 15
        issues.append({
            "code": "SUBTITLE_CRAMPED_HORIZONTAL_PADDING",
            "severity": "warning",
            "message": f"Horizontal pill padding is {layout['box_padding_x']}px (target >= {min_pad_x}px for Arabic script).",
            "fix": "Set box_padding_x >= 20 so terminal Arabic letters have horizontal breathing room.",
        })

    min_gap = max(8, int(round(12 * (height / 1280.0))))
    if layout["card_to_pill_gap_px"] < min_gap:
        score -= 20
        issues.append({
            "code": "SUBTITLE_COLLIDES_WITH_MIDDLE_CARD",
            "severity": "warning",
            "message": f"Subtitle top is too close to middle-third card band (gap {layout['card_to_pill_gap_px']}px).",
            "fix": "Reduce caption_margin_bottom slightly so a 16px+ gap remains below the 66% card zone.",
        })

    score = max(0, min(100, score))
    grade = "A" if score >= 90 else ("B" if score >= 80 else ("C" if score >= 70 else "D"))
    return {
        "passed": score >= 85 and all(i["severity"] != "error" for i in issues),
        "score": score,
        "grade": grade,
        "caption_count": len(caps),
        "layout": layout,
        "issues": issues,
    }
