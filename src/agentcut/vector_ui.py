"""Pure-Python Vector Graphics and Glassmorphism Cards Engine (v1.60.0).

Delivers studio-grade vector UI elements (glassmorphism cards, rounded bars,
pill badges, and soft drop shadows) using STRICTLY the Python standard library
(struct, zlib, math, hashlib, os) with ZERO external dependencies.

Every generated UI element is output as a valid 32-bit RGBA PNG, cached
deterministically by content hash to ensure zero rendering overhead on repeated runs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any, Tuple

from .cache import get_cache_dir

# -----------------------------------------------------------------------------
# Color parsing & manipulation helpers
# -----------------------------------------------------------------------------

NAMED_COLORS: dict[str, Tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (239, 68, 68),
    "yellow": (250, 204, 21),
    "gold": (245, 158, 11),
    "green": (16, 185, 129),
    "emerald": (16, 185, 129),
    "blue": (59, 130, 246),
    "sky": (56, 189, 248),
    "cyan": (34, 211, 238),
    "indigo": (99, 102, 241),
    "purple": (168, 85, 247),
    "magenta": (236, 72, 153),
    "pink": (244, 63, 94),
    "rose": (244, 63, 94),
    "orange": (249, 115, 22),
    "slate": (15, 23, 42),
    "gray": (107, 114, 128),
}


def parse_color_rgba(c: Any, default_alpha: int = 255) -> Tuple[int, int, int, int]:
    """Parse color into an (R, G, B, A) tuple with 0..255 integer ranges."""
    if isinstance(c, (list, tuple)):
        if len(c) == 3:
            return (int(c[0]), int(c[1]), int(c[2]), int(default_alpha))
        elif len(c) >= 4:
            a = c[3]
            if isinstance(a, float) and 0.0 <= a <= 1.0:
                a = int(round(a * 255.0))
            return (int(c[0]), int(c[1]), int(c[2]), max(0, min(255, int(a))))

    if isinstance(c, str):
        c = c.strip()
        if c.lower() in NAMED_COLORS:
            r, g, b = NAMED_COLORS[c.lower()]
            return (r, g, b, int(default_alpha))
        if c.startswith("#"):
            h = c[1:]
            if len(h) == 3:
                r = int(h[0] * 2, 16)
                g = int(h[1] * 2, 16)
                b = int(h[2] * 2, 16)
                return (r, g, b, int(default_alpha))
            if len(h) == 6:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(default_alpha))
            if len(h) == 8:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
        if c.startswith("rgba(") and c.endswith(")"):
            parts = [x.strip() for x in c[5:-1].split(",")]
            if len(parts) >= 4:
                r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
                a_f = float(parts[3])
                a = int(round(a_f * 255.0 if a_f <= 1.0 else a_f))
                return (r, g, b, max(0, min(255, a)))
        if c.startswith("rgb(") and c.endswith(")"):
            parts = [x.strip() for x in c[4:-1].split(",")]
            if len(parts) >= 3:
                r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
                return (r, g, b, int(default_alpha))

    return (255, 255, 255, int(default_alpha))


# -----------------------------------------------------------------------------
# Pure-Python 32-bit RGBA PNG Encoder
# -----------------------------------------------------------------------------

def encode_rgba_png(width: int, height: int, raw_rows: bytes | bytearray) -> bytes:
    """Encode raw 32-bit RGBA pixel bytes into a standard PNG file.

    Zero external dependencies — uses only Python stdlib struct and zlib.
    Supports full alpha transparency (Color Type 6, bit depth 8).
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid PNG dimensions: {width}x{height}")
    expected_len = width * height * 4
    if len(raw_rows) != expected_len:
        raise ValueError(f"Buffer size mismatch: expected {expected_len} bytes, got {len(raw_rows)}")

    stride = width * 4
    # Scanlines start with filter byte 0 (None)
    scanlines = bytearray((stride + 1) * height)
    for y in range(height):
        dest_off = y * (stride + 1)
        src_off = y * stride
        scanlines[dest_off] = 0
        scanlines[dest_off + 1 : dest_off + 1 + stride] = raw_rows[src_off : src_off + stride]

    compressed = zlib.compress(bytes(scanlines), level=6)

    # IHDR chunk: width (4B), height (4B), bit_depth 8, color_type 6 (RGBA), comp 0, filter 0, interlace 0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + ihdr_crc

    # IDAT chunk
    idat_crc = struct.pack(">I", zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF)
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + idat_crc

    # IEND chunk
    iend_crc = struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + iend_crc

    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# -----------------------------------------------------------------------------
# Vector SDF Glassmorphism Card Generator
# -----------------------------------------------------------------------------

def render_glass_card_png(
    width: int,
    height: int,
    border_radius: float = 24.0,
    bg_color_top: Any = "rgba(30, 41, 59, 0.82)",
    bg_color_bottom: Any = "rgba(15, 23, 42, 0.92)",
    border_width: float = 1.5,
    border_color: Any = "rgba(255, 255, 255, 0.22)",
    drop_shadow: bool = True,
    shadow_blur: float = 12.0,
    shadow_opacity: float = 0.35,
    shadow_offset_y: float = 6.0,
    padding: int = 16,
) -> bytes:
    """Render a premium Glassmorphism Card with sub-pixel anti-aliasing and drop shadow.

    Returns raw PNG bytes.
    """
    pad = int(padding if drop_shadow else 0)
    total_w = width + 2 * pad
    total_h = height + 2 * pad

    top_r, top_g, top_b, top_a = parse_color_rgba(bg_color_top, 210)
    bot_r, bot_g, bot_b, bot_a = parse_color_rgba(bg_color_bottom, 235)
    bord_r, bord_g, bord_b, bord_a = parse_color_rgba(border_color, 60)

    cx = total_w / 2.0
    cy = total_h / 2.0
    bx = max(0.0, width / 2.0 - border_radius)
    by = max(0.0, height / 2.0 - border_radius)

    buf = bytearray(total_w * total_h * 4)

    for y in range(total_h):
        py = abs(y + 0.5 - cy)
        is_y_cor = py > by
        qy = py - by if is_y_cor else 0.0

        spy = abs(y + 0.5 - (cy + shadow_offset_y))
        is_sy_cor = spy > by
        sqy = spy - by if is_sy_cor else 0.0

        # Vertical linear gradient fraction
        ty = max(0.0, min(1.0, (y - pad) / float(max(1, height))))
        bg_r = top_r + ty * (bot_r - top_r)
        bg_g = top_g + ty * (bot_g - top_g)
        bg_b = top_b + ty * (bot_b - top_b)
        bg_a = top_a + ty * (bot_a - top_a)

        row_off = y * total_w * 4

        for x in range(total_w):
            px = abs(x + 0.5 - cx)
            is_x_cor = px > bx

            # Card distance field
            if is_x_cor and is_y_cor:
                dist = math.hypot(px - bx, qy) - border_radius
            elif is_x_cor:
                dist = (px - bx) - border_radius
            elif is_y_cor:
                dist = qy - border_radius
            else:
                dist = -border_radius

            # Drop shadow calculation
            s_alpha = 0.0
            if drop_shadow and shadow_opacity > 0:
                spx = abs(x + 0.5 - cx)
                if spx > bx and is_sy_cor:
                    sdist = math.hypot(spx - bx, sqy) - border_radius
                elif spx > bx:
                    sdist = (spx - bx) - border_radius
                elif is_sy_cor:
                    sdist = sqy - border_radius
                else:
                    sdist = -border_radius

                if sdist > 0 and sdist < shadow_blur * 2.0:
                    falloff = (1.0 - sdist / (shadow_blur * 2.0)) ** 2
                    s_alpha = shadow_opacity * falloff * 255.0
                elif sdist <= 0:
                    s_alpha = shadow_opacity * 255.0

            # Sub-pixel anti-aliasing for card boundary
            if dist >= 0.5:
                card_mask = 0.0
            elif dist <= -0.5:
                card_mask = 1.0
            else:
                card_mask = 0.5 - dist

            if card_mask > 0:
                # Glowing border / rim calculation
                if border_width > 0 and bord_a > 0:
                    b_in = max(0.0, min(1.0, dist + border_width + 0.5))
                    b_out = max(0.0, min(1.0, 0.5 - dist))
                    b_factor = min(b_in, b_out)
                    b_alpha_f = (bord_a / 255.0) * b_factor
                    cr = bg_r * (1.0 - b_alpha_f) + bord_r * b_alpha_f
                    cg = bg_g * (1.0 - b_alpha_f) + bord_g * b_alpha_f
                    cb = bg_b * (1.0 - b_alpha_f) + bord_b * b_alpha_f
                    ca = (bg_a * (1.0 - b_alpha_f) + bord_a * b_alpha_f) * card_mask
                else:
                    cr, cg, cb, ca = bg_r, bg_g, bg_b, bg_a * card_mask
            else:
                cr, cg, cb, ca = 0.0, 0.0, 0.0, 0.0

            # Source-over composite card over shadow
            ac = ca / 255.0
            as_ = s_alpha / 255.0
            out_a = ac + as_ * (1.0 - ac)
            if out_a > 0.001:
                # Shadow color is dark neutral black/slate
                out_r = (cr * ac) / out_a
                out_g = (cg * ac) / out_a
                out_b = (cb * ac) / out_a
            else:
                out_r, out_g, out_b, out_a = 0.0, 0.0, 0.0, 0.0

            idx = row_off + x * 4
            buf[idx] = int(round(out_r))
            buf[idx + 1] = int(round(out_g))
            buf[idx + 2] = int(round(out_b))
            buf[idx + 3] = int(round(out_a * 255.0))

    return encode_rgba_png(total_w, total_h, buf)


# -----------------------------------------------------------------------------
# Rounded Bar & Pill Badge Generators
# -----------------------------------------------------------------------------

def render_rounded_bar_png(
    width: int,
    height: int,
    fill_color: Any = "#FACC15",
    radius: float | None = None,
    rounded_top_only: bool = True,
    gradient: bool = True,
    border_color: Any = None,
    border_width: float = 1.0,
) -> bytes:
    """Render a glossy rounded bar for bar charts with anti-aliasing."""
    if radius is None:
        radius = min(width / 2.0, 16.0)
    radius = max(0.0, min(radius, min(width / 2.0, height / 2.0)))

    r_base, g_base, b_base, a_base = parse_color_rgba(fill_color, 255)
    # Gradient: top is brighter (+25%), bottom is rich
    top_r = min(255, int(r_base * 1.25)) if gradient else r_base
    top_g = min(255, int(g_base * 1.25)) if gradient else g_base
    top_b = min(255, int(b_base * 1.25)) if gradient else b_base

    bord_r, bord_g, bord_b, bord_a = parse_color_rgba(border_color, 0) if border_color else (0, 0, 0, 0)

    cx = width / 2.0
    bx = max(0.0, width / 2.0 - radius)
    by_top = radius

    buf = bytearray(width * height * 4)

    for y in range(height):
        # In rounded_top_only, only the top part (y < radius) evaluates corner distance
        is_top_corner = rounded_top_only and (y < by_top)
        qy = (by_top - y) if is_top_corner else 0.0

        ty = y / float(max(1, height))
        bg_r = top_r + ty * (r_base - top_r)
        bg_g = top_g + ty * (g_base - top_g)
        bg_b = top_b + ty * (b_base - top_b)

        row_off = y * width * 4

        for x in range(width):
            px = abs(x + 0.5 - cx)
            is_x_cor = px > bx

            if is_x_cor and is_top_corner:
                dist = math.hypot(px - bx, qy) - radius
            elif is_x_cor:
                dist = (px - bx) - radius
            elif is_top_corner:
                dist = qy - radius
            else:
                dist = -radius

            if dist >= 0.5:
                mask = 0.0
            elif dist <= -0.5:
                mask = 1.0
            else:
                mask = 0.5 - dist

            if mask > 0:
                if border_color and border_width > 0 and bord_a > 0:
                    b_in = max(0.0, min(1.0, dist + border_width + 0.5))
                    b_out = max(0.0, min(1.0, 0.5 - dist))
                    b_f = min(b_in, b_out) * (bord_a / 255.0)
                    cr = bg_r * (1.0 - b_f) + bord_r * b_f
                    cg = bg_g * (1.0 - b_f) + bord_g * b_f
                    cb = bg_b * (1.0 - b_f) + bord_b * b_f
                    ca = a_base * mask
                else:
                    cr, cg, cb, ca = bg_r, bg_g, bg_b, a_base * mask
            else:
                cr, cg, cb, ca = 0.0, 0.0, 0.0, 0.0

            idx = row_off + x * 4
            buf[idx] = int(round(cr))
            buf[idx + 1] = int(round(cg))
            buf[idx + 2] = int(round(cb))
            buf[idx + 3] = int(round(ca))

    return encode_rgba_png(width, height, buf)


def render_pill_badge_png(
    width: int,
    height: int,
    bg_color: Any = "#FACC15",
    radius: float | None = None,
    border_color: Any = "rgba(255, 255, 255, 0.4)",
    border_width: float = 1.5,
    glow: bool = False,
    glow_color: Any = None,
) -> bytes:
    """Render a pill badge with full circular ends (radius = height / 2) and anti-aliasing."""
    if radius is None:
        radius = height / 2.0
    radius = max(0.0, min(radius, min(width / 2.0, height / 2.0)))

    r_bg, g_bg, b_bg, a_bg = parse_color_rgba(bg_color, 255)
    bord_r, bord_g, bord_b, bord_a = parse_color_rgba(border_color, 100) if border_color else (0, 0, 0, 0)
    glow_r, glow_g, glow_b, _ = parse_color_rgba(glow_color or bg_color, 120)

    pad = 8 if glow else 0
    total_w = width + 2 * pad
    total_h = height + 2 * pad

    cx = total_w / 2.0
    cy = total_h / 2.0
    bx = max(0.0, width / 2.0 - radius)
    by = max(0.0, height / 2.0 - radius)

    buf = bytearray(total_w * total_h * 4)

    for y in range(total_h):
        py = abs(y + 0.5 - cy)
        is_y_cor = py > by
        qy = py - by if is_y_cor else 0.0
        row_off = y * total_w * 4

        for x in range(total_w):
            px = abs(x + 0.5 - cx)
            is_x_cor = px > bx

            if is_x_cor and is_y_cor:
                dist = math.hypot(px - bx, qy) - radius
            elif is_x_cor:
                dist = (px - bx) - radius
            elif is_y_cor:
                dist = qy - radius
            else:
                dist = -radius

            glow_a = 0.0
            if glow and dist > 0 and dist < 12.0:
                glow_a = (1.0 - dist / 12.0) ** 2 * 120.0

            if dist >= 0.5:
                card_mask = 0.0
            elif dist <= -0.5:
                card_mask = 1.0
            else:
                card_mask = 0.5 - dist

            if card_mask > 0:
                if border_width > 0 and bord_a > 0:
                    b_in = max(0.0, min(1.0, dist + border_width + 0.5))
                    b_out = max(0.0, min(1.0, 0.5 - dist))
                    b_f = min(b_in, b_out) * (bord_a / 255.0)
                    cr = r_bg * (1.0 - b_f) + bord_r * b_f
                    cg = g_bg * (1.0 - b_f) + bord_g * b_f
                    cb = b_bg * (1.0 - b_f) + bord_b * b_f
                    ca = a_bg * card_mask
                else:
                    cr, cg, cb, ca = r_bg, g_bg, b_bg, a_bg * card_mask
            else:
                cr, cg, cb, ca = 0.0, 0.0, 0.0, 0.0

            # Composite over glow if any
            ac = ca / 255.0
            ag = glow_a / 255.0
            out_a = ac + ag * (1.0 - ac)
            if out_a > 0.001:
                out_r = (cr * ac + glow_r * ag * (1.0 - ac)) / out_a
                out_g = (cg * ac + glow_g * ag * (1.0 - ac)) / out_a
                out_b = (cb * ac + glow_b * ag * (1.0 - ac)) / out_a
            else:
                out_r, out_g, out_b, out_a = 0.0, 0.0, 0.0, 0.0

            idx = row_off + x * 4
            buf[idx] = int(round(out_r))
            buf[idx + 1] = int(round(out_g))
            buf[idx + 2] = int(round(out_b))
            buf[idx + 3] = int(round(out_a * 255.0))

    return encode_rgba_png(total_w, total_h, buf)


# -----------------------------------------------------------------------------
# Disk Cache Helpers (Zero Runtime Overhead)
# -----------------------------------------------------------------------------

def get_cached_glass_card(
    width: int,
    height: int,
    border_radius: float = 24.0,
    bg_color_top: Any = "rgba(30, 41, 59, 0.82)",
    bg_color_bottom: Any = "rgba(15, 23, 42, 0.92)",
    border_width: float = 1.5,
    border_color: Any = "rgba(255, 255, 255, 0.22)",
    drop_shadow: bool = True,
    project_dir: str | Path | None = None,
) -> str:
    """Return absolute path to cached glass card PNG, rendering it if not cached."""
    cache_dir = get_cache_dir(project_dir)
    params = {
        "kind": "glass_card",
        "w": width,
        "h": height,
        "rx": border_radius,
        "top": str(bg_color_top),
        "bot": str(bg_color_bottom),
        "bw": border_width,
        "bc": str(border_color),
        "ds": drop_shadow,
    }
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    file_path = cache_dir / f"glass_card_{h}.png"

    if not file_path.exists() or file_path.stat().st_size < 100:
        png_bytes = render_glass_card_png(
            width=width,
            height=height,
            border_radius=border_radius,
            bg_color_top=bg_color_top,
            bg_color_bottom=bg_color_bottom,
            border_width=border_width,
            border_color=border_color,
            drop_shadow=drop_shadow,
        )
        file_path.write_bytes(png_bytes)

    return str(file_path.resolve())


def get_cached_rounded_bar(
    width: int,
    height: int,
    fill_color: Any = "#FACC15",
    radius: float | None = None,
    rounded_top_only: bool = True,
    project_dir: str | Path | None = None,
) -> str:
    """Return absolute path to cached rounded bar PNG."""
    cache_dir = get_cache_dir(project_dir)
    params = {
        "kind": "rounded_bar",
        "w": width,
        "h": height,
        "color": str(fill_color),
        "rx": radius,
        "top_only": rounded_top_only,
    }
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    file_path = cache_dir / f"rounded_bar_{h}.png"

    if not file_path.exists() or file_path.stat().st_size < 100:
        png_bytes = render_rounded_bar_png(
            width=width,
            height=height,
            fill_color=fill_color,
            radius=radius,
            rounded_top_only=rounded_top_only,
        )
        file_path.write_bytes(png_bytes)

    return str(file_path.resolve())


def get_cached_pill_badge(
    width: int,
    height: int,
    bg_color: Any = "#FACC15",
    border_color: Any = "rgba(255, 255, 255, 0.4)",
    glow: bool = False,
    project_dir: str | Path | None = None,
) -> str:
    """Return absolute path to cached pill badge PNG."""
    cache_dir = get_cache_dir(project_dir)
    params = {
        "kind": "pill_badge",
        "w": width,
        "h": height,
        "color": str(bg_color),
        "bc": str(border_color),
        "glow": glow,
    }
    h = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    file_path = cache_dir / f"pill_badge_{h}.png"

    if not file_path.exists() or file_path.stat().st_size < 100:
        png_bytes = render_pill_badge_png(
            width=width,
            height=height,
            bg_color=bg_color,
            border_color=border_color,
            glow=glow,
        )
        file_path.write_bytes(png_bytes)

    return str(file_path.resolve())
