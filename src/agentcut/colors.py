"""Color utilities, harmonious palette generation, and WCAG contrast checking.

Inspired by ProCraft's color engine, adapted for AgentCut video styling,
motion card theming, and kinetic subtitle readability. Zero external dependencies.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse a HEX color string (#RGB or #RRGGBB) to (r, g, b) 0..255."""
    clean = hex_str.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6 or not re.match(r"^[0-9a-fA-F]{6}$", clean):
        raise ValueError(f"Invalid HEX color: {hex_str!r}")
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Format (r, g, b) ints 0..255 into uppercase #RRGGBB."""
    r_clamped = max(0, min(255, int(round(r))))
    g_clamped = max(0, min(255, int(round(g))))
    b_clamped = max(0, min(255, int(round(b))))
    return f"#{r_clamped:02X}{g_clamped:02X}{b_clamped:02X}"


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert (r, g, b) 0..255 to (h: 0..360, s: 0..1, l: 0..1)."""
    rn, gn, bn = r / 255.0, g / 255.0, b / 255.0
    h_norm, l_norm, s_norm = colorsys.rgb_to_hls(rn, gn, bn)
    return (h_norm * 360.0, s_norm, l_norm)


def hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """Convert (h: 0..360, s: 0..1, l: 0..1) to (r, g, b) 0..255."""
    h_norm = (h % 360.0) / 360.0
    s_clamped = max(0.0, min(1.0, float(s)))
    l_clamped = max(0.0, min(1.0, float(l)))
    rn, gn, bn = colorsys.hls_to_rgb(h_norm, l_clamped, s_clamped)
    return (int(round(rn * 255.0)), int(round(gn * 255.0)), int(round(bn * 255.0)))


def relative_luminance(r: int, g: int, b: int) -> float:
    """Compute relative luminance according to WCAG 2.1 / ITU-R BT.709."""
    def _channel_lum(c: int) -> float:
        c_norm = c / 255.0
        return c_norm / 12.92 if c_norm <= 0.04045 else ((c_norm + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel_lum(r) + 0.7152 * _channel_lum(g) + 0.0722 * _channel_lum(b)


def contrast_ratio(color1: str | tuple[int, int, int], color2: str | tuple[int, int, int]) -> float:
    """Calculate the WCAG contrast ratio between two colors (returns 1.0 to 21.0)."""
    rgb1 = hex_to_rgb(color1) if isinstance(color1, str) else color1
    rgb2 = hex_to_rgb(color2) if isinstance(color2, str) else color2
    lum1 = relative_luminance(*rgb1)
    lum2 = relative_luminance(*rgb2)
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def is_readable(text_color: str | tuple[int, int, int], bg_color: str | tuple[int, int, int], min_ratio: float = 4.5) -> bool:
    """Check if text color is sufficiently readable on bg_color (default 4.5:1 for AA normal text)."""
    return contrast_ratio(text_color, bg_color) >= min_ratio


def ensure_readable(text_color: str, bg_color: str, min_ratio: float = 4.5) -> str:
    """Return text_color if readable against bg_color, else fallback to best of #FFFFFF or #000000."""
    if is_readable(text_color, bg_color, min_ratio):
        return text_color
    white_contrast = contrast_ratio("#FFFFFF", bg_color)
    black_contrast = contrast_ratio("#000000", bg_color)
    return "#FFFFFF" if white_contrast >= black_contrast else "#000000"


def generate_palette(
    base_color: str,
    harmony: str = "analogous",
    dark_mode: bool = True,
) -> dict[str, Any]:
    """Generate a harmonious color palette for video cards, diagrams, and motion layers.

    Supported harmonies:
      - 'monochromatic': Tints and shades of base color
      - 'complementary': Base hue + opposite hue (180 deg)
      - 'analogous': Base hue +/- 30 deg
      - 'triadic': Base hue + 120 deg and + 240 deg

    Returns a rich dictionary with primary, secondary, accent, surface, border, and text styles.
    """
    base_rgb = hex_to_rgb(base_color)
    h, s, l = rgb_to_hsl(*base_rgb)

    if harmony == "complementary":
        h_sec = (h + 180.0) % 360.0
        h_acc = (h + 150.0) % 360.0
    elif harmony == "triadic":
        h_sec = (h + 120.0) % 360.0
        h_acc = (h + 240.0) % 360.0
    elif harmony == "monochromatic":
        h_sec = h
        h_acc = h
    else:  # 'analogous' default
        h_sec = (h + 30.0) % 360.0
        h_acc = (h - 30.0 + 360.0) % 360.0

    primary_hex = rgb_to_hex(*base_rgb)
    sec_rgb = hsl_to_rgb(h_sec, max(0.4, s * 0.9), l if harmony != "monochromatic" else max(0.2, l - 0.2))
    acc_rgb = hsl_to_rgb(h_acc, min(1.0, s * 1.1), min(0.7, max(0.4, l * 1.05)))

    secondary_hex = rgb_to_hex(*sec_rgb)
    accent_hex = rgb_to_hex(*acc_rgb)

    if dark_mode:
        bg_rgb = hsl_to_rgb(h, s * 0.25, 0.08)
        bg_hex = rgb_to_hex(*bg_rgb)
        surface_rgb = hsl_to_rgb(h, s * 0.20, 0.16)
        text_primary = "#F8FAFC"
        text_muted = "#94A3B8"
        border_hex = "rgba(255, 255, 255, 0.15)"
        glass_fill = f"rgba({surface_rgb[0]}, {surface_rgb[1]}, {surface_rgb[2]}, 0.70)"
    else:
        bg_rgb = hsl_to_rgb(h, s * 0.15, 0.96)
        bg_hex = rgb_to_hex(*bg_rgb)
        surface_rgb = hsl_to_rgb(h, s * 0.15, 0.90)
        text_primary = "#0F172A"
        text_muted = "#475569"
        border_hex = "rgba(0, 0, 0, 0.10)"
        glass_fill = f"rgba({surface_rgb[0]}, {surface_rgb[1]}, {surface_rgb[2]}, 0.85)"

    c_ratio = contrast_ratio(text_primary, bg_hex)
    wcag = "AAA" if c_ratio >= 7.0 else ("AA" if c_ratio >= 4.5 else "Fail")

    return {
        "harmony": harmony,
        "base_color": primary_hex,
        "primary": primary_hex,
        "secondary": secondary_hex,
        "accent": accent_hex,
        "background": bg_hex,
        "surface": glass_fill,
        "border": border_hex,
        "text_primary": text_primary,
        "text_muted": text_muted,
        "contrast_ratio": c_ratio,
        "wcag_level": wcag,
        "swatches": [primary_hex, secondary_hex, accent_hex, bg_hex],
    }


def auto_theme_scene(scene: dict[str, Any], palette: dict[str, Any]) -> dict[str, Any]:
    """Inject harmonious palette colors into a scene's diagrams or motion cards if not explicit."""
    updated = dict(scene)
    if "diagram" in updated and isinstance(updated["diagram"], dict):
        diag = dict(updated["diagram"])
        params = dict(diag.get("params", {}))
        if "color" not in params:
            params["color"] = palette["primary"]
        if "accent" not in params:
            params["accent"] = palette["accent"]
        if "card_bg" not in params:
            params["card_bg"] = palette["surface"]
        diag["params"] = params
        updated["diagram"] = diag
    return updated
