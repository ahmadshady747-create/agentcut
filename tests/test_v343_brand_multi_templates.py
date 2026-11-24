"""Verification test suite for AgentCut v3.4.3 (Cycle 3: Brand Kits, Multi-Export, Thumbnails & Advanced Templates)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import brand_kits, flat_reels, multi_export, server  # noqa: E402
from agentcut.__main__ import main as cli_main  # noqa: E402


class TestBrandKitsV343(unittest.TestCase):
    def test_four_curated_brand_kits_and_apply(self):
        kits = brand_kits.list_brand_kits()
        self.assertGreaterEqual(len(kits), 4)
        ids = {k["id"] for k in kits}
        self.assertTrue(
            {"tab_ezzay", "reel_studio_dark", "arabic_documentary", "midnight_tech"}.issubset(ids)
        )

        spec = {
            "id": "kit-test",
            "brand_kit": "midnight_tech",
            "scenes": [
                {"id": "s1", "type": "intro", "narration": "a.wav", "text": "تجربة الهوية."}
            ],
        }
        applied = brand_kits.apply_brand_kit(spec)
        self.assertEqual(applied["palette"], "midnight")
        self.assertEqual(applied["transition"], "wipe_rtl")
        self.assertEqual(applied["camera_move"], "pan_rtl")
        self.assertEqual(applied["physics"], "spring")
        self.assertEqual(applied["series_tag"], "هندسة الذكاء")


class TestMultiExportAndThumbnailsV343(unittest.TestCase):
    def test_thumbnail_html_and_multi_format_bundle(self):
        spec = {
            "id": "winter-why",
            "brand_kit": "tab_ezzay",
            "title": "ليه بنحس بالبرد في الشتاء؟",
            "scenes": [
                {
                    "id": "s1",
                    "type": "intro",
                    "narration": "s1.wav",
                    "text": "جسمك حرارته 37 طول السنة.",
                    "title": "ليه بنحس بالبرد؟",
                    "subtitle": "سر الثبات الحراري",
                    "icon": "thermometer",
                    "badge": "37°",
                }
            ],
        }
        thumb_land = multi_export.render_thumbnail_html(spec, format_name="landscape", embed_font=False)
        self.assertIn("width:1280px;height:720px", thumb_land)
        self.assertIn("ليه بنحس بالبرد في الشتاء؟", thumb_land)
        self.assertIn("37°", thumb_land)
        self.assertIn("window.setTime", thumb_land)

        timings = [
            {
                "id": "s1",
                "start": 0.0,
                "duration": 2.0,
                "words": flat_reels.even_word_timings(
                    spec["scenes"][0]["text"].split(), 0.0, 2.0
                ),
            }
        ]
        bundle = multi_export.build_multi_format_html_bundle(
            spec, timings, formats=("landscape", "vertical", "square"), embed_font=False
        )
        self.assertEqual(set(bundle["formats"]), {"landscape", "vertical", "square"})
        self.assertEqual(bundle["formats"]["landscape"]["width"], 1280)
        self.assertEqual(bundle["formats"]["vertical"]["height"], 1280)
        self.assertEqual(bundle["formats"]["square"]["width"], 1080)
        self.assertIn("th-stage", bundle["thumbnail_html"])


class TestAdvancedExplainerTemplatesV343(unittest.TestCase):
    def test_five_new_explainer_templates_and_motion_physics(self):
        spec = {
            "id": "adv-explainer",
            "brand_kit": "arabic_documentary",
            "format": "landscape",
            "physics": "elastic",
            "camera_move": "push_in",
            "transition": "iris_reveal",
            "scenes": [
                {
                    "id": "sc1",
                    "type": "before_after",
                    "narration": "a.wav",
                    "text": "مقارنة شاملة قبل وبعد التحول.",
                    "title": "التحول إلى المتصفح",
                    "before": {"title": "FFmpeg القديم", "items": ["36 دقيقة ريندر", "توفو في العربي"]},
                    "after": {"title": "مسار المتصفح", "items": ["دقيقة و45 ثانية", "طباعة عربية نظيفة"]},
                },
                {
                    "id": "sc2",
                    "type": "funnel",
                    "narration": "b.wav",
                    "text": "مراحل تصفية الإشارات الحرارية.",
                    "title": "قمع الاستجابة",
                    "stages": [
                        {"label": "مستقبلات الجلد", "value": "100%"},
                        {"label": "منطقة تحت المهاد", "value": "60%"},
                        {"label": "انقباض الأوعية", "value": "25%"},
                    ],
                },
                {
                    "id": "sc3",
                    "type": "matrix_2x2",
                    "narration": "c.wav",
                    "text": "مصفوفة الأولويات الأربع.",
                    "title": "مصفوفة القرار",
                    "quadrants": [
                        {"tag": "Q1", "title": "أثر عالٍ", "body": "تنفيذ فوري"},
                        {"tag": "Q2", "title": "استراتيجي", "body": "جدولة"},
                        {"tag": "Q3", "title": "سريع", "body": "أتمتة"},
                        {"tag": "Q4", "title": "هامش", "body": "حذف"},
                    ],
                },
                {
                    "id": "sc4",
                    "type": "terminal_demo",
                    "narration": "d.wav",
                    "text": "تشغيل المحرك بأمر واحد.",
                    "title": "واجهة الأوامر",
                    "commands": [
                        {"cmd": "agentcut render spec.json out.mp4", "out": "Rendered 1800 frames in 95s"},
                    ],
                },
                {
                    "id": "sc5",
                    "type": "chapter_toc",
                    "narration": "e.wav",
                    "text": "فهرس فصول الحلقة الكاملة.",
                    "title": "فهرس الحلقة",
                    "active_chapter": 1,
                    "chapters": [
                        {"title": "لغز حرارة 37", "time": "00:00"},
                        {"title": "إنذار الجلد المبكر", "time": "01:15"},
                        {"title": "الخلاصة", "time": "02:40"},
                    ],
                },
            ],
        }
        ok, errs = flat_reels.validate_reel_spec(spec)
        self.assertTrue(ok, errs)
        timings = [
            {
                "id": sc["id"],
                "start": i * 2.0,
                "duration": 2.0,
                "words": flat_reels.even_word_timings(sc["text"].split(), i * 2.0, (i + 1) * 2.0),
            }
            for i, sc in enumerate(spec["scenes"])
        ]
        html = flat_reels.build_video_html(spec, timings, embed_font=False)
        self.assertIn('class="ba-grid"', html)
        self.assertIn('class="funnel-stack"', html)
        self.assertIn('class="matrix-grid"', html)
        self.assertIn('class="term-card anim"', html)
        self.assertIn('class="toc-list"', html)
        self.assertIn('class="brand-watermark"', html)
        self.assertIn("function easeOutSpring", html)
        self.assertIn("function easeOutElastic", html)
        self.assertIn("iris_reveal", html)
        self.assertNotIn("@keyframes", html)
        self.assertNotIn("transition:", html)

    def test_mcp_and_cli_v343(self):
        self.assertTrue(agentcut.__version__.startswith("3.4."))
        ds = server.dispatch_tool("list_design_system", {})
        self.assertTrue(ds["version"].startswith("3.4."))
        self.assertEqual(len(ds["scene_types"]), 23)
        self.assertGreaterEqual(len(ds["brand_kits"]), 4)
        self.assertIn("wipe_rtl", ds["transitions"])
        self.assertIn("iris_reveal", ds["transitions"])
        self.assertIn("spring", ds["physics_modes"])
        self.assertIn("push_in", ds["camera_moves"])

        kits_res = server.dispatch_tool("list_brand_kits", {})
        self.assertTrue(kits_res["ok"])
        self.assertGreaterEqual(len(kits_res["brand_kits"]), 4)
        self.assertEqual(cli_main(["brand-kits"]), 0)


if __name__ == "__main__":
    unittest.main()
