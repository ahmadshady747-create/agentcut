"""Comprehensive Automated Test Suite for AgentCut v1.60.0 (The Visual Leap).

Verifies:
1. Pure-Python 32-bit RGBA PNG Generator (stdlib struct + zlib, zero external deps).
2. SDF Glassmorphism Card, Rounded Bars, and Pill Badges with Anti-aliasing & Drop Shadow.
3. B-Roll Engine with Cinematic Legibility Filter Chain (scale, crop, zoompan, gblur, eq, vignette).
4. Interactive Word Karaoke Captions (Active Word Highlight + 10% Font Enlargement + Arabic RTL safety).
5. Zero External Dependencies Compliance & Standard pyproject.toml packaging.
6. MCP Tool 'generate_glass_card_preview' integration.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from agentcut import __version__
from agentcut.broll import (
    build_broll_filter_chain,
    is_image_file,
    is_video_file,
    resolve_broll_media,
)
from agentcut.server import call_tool
from agentcut.srt import build_karaoke_ass
from agentcut.templates import get_template, list_content_templates
from agentcut.vector_ui import (
    encode_rgba_png,
    get_cached_glass_card,
    get_cached_pill_badge,
    get_cached_rounded_bar,
    parse_color_rgba,
    render_glass_card_png,
    render_pill_badge_png,
    render_rounded_bar_png,
)


class TestV160VisualLeap(unittest.TestCase):

    def test_pure_python_png_encoder(self):
        """Verify PNG encoding adheres to RFC 2083 using pure Python stdlib."""
        w, h = 16, 8
        # Create checkerboard: alternate opaque red and transparent
        pixels = bytearray()
        for y in range(h):
            for x in range(w):
                if (x + y) % 2 == 0:
                    pixels.extend([255, 0, 0, 255])
                else:
                    pixels.extend([0, 0, 0, 0])

        png_bytes = encode_rgba_png(w, h, pixels)

        # 1. Magic bytes
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        # 2. IHDR Chunk
        ihdr_len = struct.unpack(">I", png_bytes[8:12])[0]
        self.assertEqual(ihdr_len, 13)
        self.assertEqual(png_bytes[12:16], b"IHDR")
        dw, dh, bit_depth, color_type = struct.unpack(">IIBB", png_bytes[16:26])
        self.assertEqual(dw, w)
        self.assertEqual(dh, h)
        self.assertEqual(bit_depth, 8)
        self.assertEqual(color_type, 6)  # RGBA

        # 3. IEND Chunk
        self.assertTrue(png_bytes.endswith(b"IEND\xaeB`\x82"))

        # 4. Error on dimensions mismatch
        with self.assertRaises(ValueError):
            encode_rgba_png(10, 10, b"short_data")

    def test_glassmorphism_card_generation(self):
        """Verify SDF glass card rasterization, gradient, border, and drop shadow."""
        w, h = 180, 100
        png_bytes = render_glass_card_png(
            width=w,
            height=h,
            border_radius=16.0,
            bg_color_top="rgba(30, 41, 59, 0.85)",
            bg_color_bottom="rgba(15, 23, 42, 0.95)",
            border_width=1.5,
            border_color="rgba(255, 255, 255, 0.25)",
            drop_shadow=True,
            padding=12,
        )

        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        # Total width = 180 + 2*12 = 204, height = 100 + 2*12 = 124
        dw, dh = struct.unpack(">II", png_bytes[16:24])
        self.assertEqual(dw, 204)
        self.assertEqual(dh, 124)

        # Verify caching mechanism
        p1 = get_cached_glass_card(w, h, border_radius=16.0)
        self.assertTrue(os.path.isfile(p1))
        self.assertGreater(os.path.getsize(p1), 500)

        p2 = get_cached_glass_card(w, h, border_radius=16.0)
        self.assertEqual(p1, p2)

    def test_rounded_bar_and_pill_badge(self):
        """Verify rounded bar and pill badge generators with smooth corners."""
        # 1. Rounded bar
        bar_png = render_rounded_bar_png(
            width=32,
            height=96,
            fill_color="#FACC15",
            radius=12.0,
            rounded_top_only=True,
            gradient=True,
        )
        self.assertTrue(bar_png.startswith(b"\x89PNG\r\n\x1a\n"))
        bw, bh = struct.unpack(">II", bar_png[16:24])
        self.assertEqual(bw, 32)
        self.assertEqual(bh, 96)

        cached_bar = get_cached_rounded_bar(32, 96, fill_color="#FACC15")
        self.assertTrue(os.path.isfile(cached_bar))

        # 2. Pill badge
        pill_png = render_pill_badge_png(
            width=120,
            height=40,
            bg_color="#EF4444",
            border_color="rgba(255, 255, 255, 0.5)",
            border_width=2.0,
            glow=True,
        )
        self.assertTrue(pill_png.startswith(b"\x89PNG\r\n\x1a\n"))
        # With glow padding (+16px total)
        pw, ph = struct.unpack(">II", pill_png[16:24])
        self.assertEqual(pw, 136)
        self.assertEqual(ph, 56)

        cached_pill = get_cached_pill_badge(120, 40, bg_color="#EF4444")
        self.assertTrue(os.path.isfile(cached_pill))

    def test_broll_pipeline_and_cinematic_filters(self):
        """Verify B-Roll media resolution and cinematic legibility grading filters."""
        # 1. File extensions
        self.assertTrue(is_video_file("clip.mp4"))
        self.assertTrue(is_video_file("scene.mov"))
        self.assertTrue(is_image_file("background.png"))
        self.assertTrue(is_image_file("photo.jpg"))
        self.assertFalse(is_video_file("photo.jpg"))

        # 2. Local media resolution
        test_pattern = "data/test_media/pattern.png"
        resolved = resolve_broll_media(test_pattern)
        self.assertIsNotNone(resolved)
        self.assertTrue(os.path.isfile(resolved))

        # 3. Graceful fallback when no key or network
        fallback = resolve_broll_media("keyword_without_api_key_query")
        self.assertIsNone(fallback)

        # 4. Cinematic filter chain construction (image vs video)
        chain_img = build_broll_filter_chain(
            width=720, height=1280, duration=4.0, is_video=False, fps=30
        )
        self.assertIn("scale=720:1280", chain_img)
        self.assertIn("crop=720:1280", chain_img)
        self.assertIn("zoompan=", chain_img)
        self.assertIn("gblur=sigma=6.0", chain_img)
        self.assertIn("eq=brightness=-0.22:contrast=1.12:saturation=0.85", chain_img)
        self.assertIn("vignette=PI/4", chain_img)

        chain_vid = build_broll_filter_chain(
            width=1920, height=1080, duration=5.0, is_video=True
        )
        self.assertIn("scale=1920:1080", chain_vid)
        self.assertNotIn("zoompan=", chain_vid)  # No zoompan on video
        self.assertIn("gblur=sigma=6.0", chain_vid)

    def test_interactive_karaoke_pill_captions(self):
        """Verify karaoke_pill caption style with active word 10% pop and Arabic BiDi safety."""
        events = [
            {
                "kind": "karaoke_pill",
                "caption_style": "karaoke_pill",
                "start": 0.0,
                "end": 3.0,
                "font": ("Cairo", True),
                "size": 42,
                "base_color": "white",
                "hi_color": "yellow",
                "align": 8,
                "x": 360,
                "y": 1050,
                "words": [
                    {"word": "سباق", "start": 0.0, "end": 0.8},
                    {"word": "الذكاء", "start": 0.8, "end": 1.7},
                    {"word": "الاصطناعي", "start": 1.7, "end": 3.0},
                ],
            }
        ]

        ass = build_karaoke_ass(events, 720, 1280)

        # 1. Headers & Resolution
        self.assertIn("PlayResX: 720", ass)
        self.assertIn("PlayResY: 1280", ass)

        # 2. Scaled active word tags (\fscx110\fscy110)
        self.assertIn(r"\fscx110\fscy110", ass)
        self.assertIn(r"\fscx100\fscy100", ass)

        # 3. Yellow highlight color (&H0000FFFF&) and white base (&H00FFFFFF&)
        self.assertIn(r"&H0000FFFF&", ass)
        self.assertIn(r"&H00FFFFFF&", ass)

        # 4. RTL Arabic Run-Order: each window isolates the active word
        self.assertIn("سباق", ass)
        self.assertIn("الذكاء", ass)
        self.assertIn("الاصطناعي", ass)

    def test_packaging_and_zero_dependencies(self):
        """Verify strict zero-dependency packaging in pyproject.toml."""
        pyproject_path = Path("pyproject.toml")
        self.assertTrue(pyproject_path.exists())
        content = pyproject_path.read_text(encoding="utf-8")

        self.assertIn('name = "agentcut"', content)
        self.assertIn('version = "1.60.0"', content)
        self.assertIn("dependencies = []", content)
        self.assertIn('agentcut = "agentcut.__main__:main"', content)
        self.assertIn('agentcut-mcp = "agentcut.server:main"', content)

        # Verify version alignment
        version_file = Path("VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(version_file, "1.60.0")
        self.assertEqual(__version__, "1.60.0")

    def test_mcp_glass_card_preview_tool(self):
        """Verify MCP server exposes and executes generate_glass_card_preview."""
        res = call_tool(
            "generate_glass_card_preview",
            {
                "width": 480,
                "height": 240,
                "border_radius": 20.0,
                "bg_color_top": "#1E293B",
                "bg_color_bottom": "#0F172A",
                "border_color": "rgba(255,255,255,0.3)",
                "drop_shadow": True,
            },
        )
        self.assertIn("content", res)
        text_content = res["content"][0]["text"]
        self.assertIn("success", text_content)
        self.assertIn("glass_vector", text_content)

    def test_templates_support_karaoke_pill(self):
        """Verify that high-impact templates expose karaoke_pill caption preset."""
        tpl_finance = get_template("finance_data_story")
        self.assertEqual(tpl_finance["typography"]["caption_preset"], "karaoke_pill")

        tpl_countdown = get_template("top_list_countdown")
        self.assertEqual(tpl_countdown["typography"]["caption_preset"], "karaoke_pill")

        tpl_promo = get_template("product_promo_showcase")
        self.assertEqual(tpl_promo["typography"]["caption_preset"], "karaoke_pill")


if __name__ == "__main__":
    unittest.main()
