"""Unit tests for ProCraft integration into AgentCut:
- Colors (Hex/RGB/HSL, Harmonies, WCAG Contrast, Readability)
- Text Metrics (Word counts, Arabic detection, Reading duration estimation, Script Diffing)
- JSON Tools (Minification, Formatting, Precision Syntax Error Reporting)
- Scene Caching (Deterministic SHA-256 Hashing, Cache Store/Lookup/Clear)
- Server MCP Tools (generate_color_palette, analyze_text_metrics)
- Web Studio Enhanced Model & UI
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.cache import clear_cache, compute_scene_hash, get_cache_dir, lookup_scene_cache, store_scene_cache
from agentcut.colors import (
    contrast_ratio,
    ensure_readable,
    generate_palette,
    hex_to_rgb,
    hsl_to_rgb,
    is_readable,
    relative_luminance,
    rgb_to_hex,
    rgb_to_hsl,
)
from agentcut.json_tools import format_json, minify_json, validate_json_syntax
from agentcut.server import call_tool
from agentcut.studio import _extract_timeline_model, generate_studio_html
from agentcut.text_metrics import analyze_text, diff_texts, estimate_reading_duration


class TestProCraftIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-procraft-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ─── Colors & Contrast ──────────────────────────────────────────
    def test_color_conversions(self):
        rgb = hex_to_rgb("#3B82F6")
        self.assertEqual(rgb, (59, 130, 246))

        hex_back = rgb_to_hex(*rgb)
        self.assertEqual(hex_back, "#3B82F6")

        h, s, l = rgb_to_hsl(*rgb)
        self.assertAlmostEqual(h, 217.2, delta=1.0)
        self.assertAlmostEqual(s, 0.91, delta=0.05)

        rgb_reconv = hsl_to_rgb(h, s, l)
        self.assertEqual(rgb_reconv, rgb)

    def test_wcag_contrast(self):
        # Black vs White should be 21:1
        ratio = contrast_ratio("#FFFFFF", "#000000")
        self.assertEqual(ratio, 21.0)

        # Same colors should be 1:1
        ratio_same = contrast_ratio("#3B82F6", "#3B82F6")
        self.assertEqual(ratio_same, 1.0)

        # White on dark blue should be readable (AA/AAA)
        self.assertTrue(is_readable("#FFFFFF", "#0F172A", min_ratio=7.0))

        # Yellow on white is not readable, ensure_readable should switch or adjust
        fixed = ensure_readable("#FFEB3B", "#FFFFFF", min_ratio=4.5)
        self.assertEqual(fixed, "#000000")

    def test_palette_generation(self):
        pal = generate_palette("#3B82F6", harmony="triadic", dark_mode=True)
        self.assertEqual(pal["primary"], "#3B82F6")
        self.assertEqual(pal["harmony"], "triadic")
        self.assertIn("secondary", pal)
        self.assertIn("accent", pal)
        self.assertIn("surface", pal)
        self.assertGreaterEqual(pal["contrast_ratio"], 4.5)
        self.assertIn(pal["wcag_level"], ("AA", "AAA"))

    # ─── Text Metrics & Diff ────────────────────────────────────────
    def test_text_analysis_and_arabic(self):
        ar_text = "الذكاء الاصطناعي يغير شكل صناعة الفيديو بالكامل."
        res = analyze_text(ar_text)
        self.assertEqual(res["words"], 7)
        self.assertEqual(res["arabic_words"], 7)
        self.assertEqual(res["latin_words"], 0)
        self.assertTrue(res["is_arabic_predominant"])

        en_text = "AgentCut is built for autonomous AI agents."
        res_en = analyze_text(en_text)
        self.assertEqual(res_en["words"], 7)
        self.assertEqual(res_en["latin_words"], 7)
        self.assertFalse(res_en["is_arabic_predominant"])

    def test_estimate_reading_duration(self):
        # 14 words Arabic: at 140 WPM -> ~6.0s + 0.5s slack = ~6.5s
        text = "هذا نص تجريبي لحساب مدة القراءة المطلوبة للمشهد المرئي بشكل متناسق ومريح."
        dur = estimate_reading_duration(text)
        self.assertGreaterEqual(dur, 4.0)
        self.assertLessEqual(dur, 12.0)

        # Empty text hits min_duration
        self.assertEqual(estimate_reading_duration("", min_duration=3.0), 3.0)

    def test_text_diff(self):
        orig = "The quick brown fox jumps over the lazy dog"
        derived = "The quick brown fox leaps over lazy dog"
        diff = diff_texts(orig, derived)

        self.assertFalse(diff["is_identical"])
        self.assertGreater(diff["similarity"], 0.7)
        self.assertIn("jumps", diff["removed_words"])
        self.assertIn("leaps", diff["added_words"])

    # ─── JSON Tools ─────────────────────────────────────────────────
    def test_json_minification_and_formatting(self):
        obj = {"id": "proj", "clips": [1, 2, 3], "nested": {"key": "value"}}
        minified = minify_json(obj)
        self.assertNotIn(" ", minified)
        self.assertNotIn("\n", minified)

        formatted = format_json(minified, indent=2)
        self.assertIn("\n  \"id\": \"proj\"", formatted)

    def test_json_syntax_validation(self):
        valid = validate_json_syntax('{"valid": true, "count": 42}')
        self.assertTrue(valid["valid"])
        self.assertIsNone(valid["error"])

        # Trailing comma
        bad_comma = '{\n  "name": "agentcut",\n  "version": 1,\n}'
        res_comma = validate_json_syntax(bad_comma)
        self.assertFalse(res_comma["valid"])
        self.assertEqual(res_comma["line"], 4)
        self.assertIn("^", res_comma["snippet"])
        self.assertIn("comma", res_comma["fix_hint"].lower())

    # ─── Scene Cache ────────────────────────────────────────────────
    def test_deterministic_scene_hash(self):
        scene1 = {"title": "Scene A", "duration": 4.5, "diagram": {"template": "timeline", "steps": 3}}
        scene2 = {"diagram": {"steps": 3, "template": "timeline"}, "duration": 4.5, "title": "Scene A"}
        output = {"width": 720, "height": 1280, "fps": 30}

        h1 = compute_scene_hash(scene1, output)
        h2 = compute_scene_hash(scene2, output)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

        # Modifying duration must change hash
        scene3 = dict(scene1, duration=5.0)
        h3 = compute_scene_hash(scene3, output)
        self.assertNotEqual(h1, h3)

    def test_scene_cache_storage(self):
        mock_mp4 = os.path.join(self.tmp, "mock_chunk.mp4")
        with open(mock_mp4, "wb") as f:
            f.write(b"0" * 2048)  # > 1024 bytes

        s_hash = "abc123def4567890"
        stored = store_scene_cache(s_hash, mock_mp4, project_dir=self.tmp)
        self.assertTrue(stored.exists())

        found = lookup_scene_cache(s_hash, project_dir=self.tmp)
        self.assertIsNotNone(found)
        self.assertEqual(found, stored)

        cleared = clear_cache(project_dir=self.tmp)
        self.assertEqual(cleared, 1)
        self.assertIsNone(lookup_scene_cache(s_hash, project_dir=self.tmp))

    # ─── MCP Tools Calling ──────────────────────────────────────────
    def test_mcp_palette_and_metrics_tools(self):
        # Call generate_color_palette
        p_res = call_tool("generate_color_palette", {"base_color": "#10B981", "harmony": "complementary"})
        self.assertNotIn("isError", p_res)
        p_data = json.loads(p_res["content"][0]["text"])
        self.assertEqual(p_data["primary"], "#10B981")
        self.assertEqual(p_data["harmony"], "complementary")

        # Call analyze_text_metrics
        m_res = call_tool("analyze_text_metrics", {
            "text": "Hello world from AgentCut MCP",
            "original_text": "Hello world from AgentCut",
        })
        self.assertNotIn("isError", m_res)
        m_data = json.loads(m_res["content"][0]["text"])
        self.assertEqual(m_data["words"], 5)
        self.assertIn("diff", m_data)
        self.assertIn("MCP", m_data["diff"]["added_words"])

    # ─── Web Studio Model Integration ───────────────────────────────
    def test_studio_model_has_palette_and_metrics(self):
        brief = {
            "id": "quantum_video",
            "title": "Quantum Physics",
            "brand_color": "#8B5CF6",
            "scenes": [
                {
                    "id": "s1",
                    "title": "Introduction",
                    "duration": 5.0,
                    "narration_text": "مرحباً بكم في شرح ظاهرة التشابك الكمي المذهلة",
                }
            ]
        }
        model = _extract_timeline_model(brief)
        self.assertIn("palette", model)
        self.assertEqual(model["palette"]["primary"], "#8B5CF6")
        self.assertIn("text_metrics", model)
        self.assertEqual(model["text_metrics"]["words"], 8)
        self.assertIn("minified_json", model)

        # Generate HTML
        out_html = os.path.join(self.tmp, "studio.html")
        path = generate_studio_html(brief, output_html_path=out_html)
        self.assertTrue(os.path.isfile(path))

        with open(path, "r", encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("Palette", html)
        self.assertIn("Metrics", html)
        self.assertIn("Tokens Saved", html)


if __name__ == "__main__":
    unittest.main()
