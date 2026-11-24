"""Verification test suite for AgentCut v3.4.2 (Cycle 2: Live SVG Charts, 100+ Icons & Smart Arabic Captions)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import captions, charts, flat_reels, icons, server  # noqa: E402
from agentcut.__main__ import main as cli_main  # noqa: E402


class TestIconsAndIllustrationsV342(unittest.TestCase):
    def test_over_100_icons_and_composite_illustrations(self):
        all_icons = icons.list_icons()
        self.assertGreaterEqual(len(all_icons), 100)
        for name in all_icons:
            svg = icons.icon_svg(name, 80)
            self.assertIn('viewBox="0 0 64 64"', svg, name)
            self.assertIn('class="ambient-icon"', svg, name)

        all_ills = icons.list_illustrations()
        self.assertGreaterEqual(len(all_ills), 6)
        for ill_name in all_ills:
            svg = icons.illustration_svg(ill_name)
            self.assertIn('viewBox="0 0 320 220"', svg, ill_name)
            self.assertIn('class="ambient-icon composite-ill"', svg, ill_name)


class TestVectorChartsV342(unittest.TestCase):
    def test_line_donut_and_grouped_bar_charts_build(self):
        line_svg = charts.build_line_chart_svg({
            "points": [{"label": "يناير", "value": 20}, {"label": "فبراير", "value": 55}, {"label": "مارس", "value": 90}],
            "unit": "%",
            "color": "accent",
        })
        self.assertIn("chart-line-path", line_svg)
        self.assertIn("stroke-dashoffset", line_svg)
        self.assertIn("يناير", line_svg)

        donut_html = charts.build_donut_chart_svg({
            "segments": [
                {"label": "العضلات", "value": 75, "color": "accent"},
                {"label": "الأعضاء", "value": 25, "color": "blue"},
            ],
            "center_value": "75%",
            "center_label": "إنتاج الحرارة",
        })
        self.assertIn("donut-seg", donut_html)
        self.assertIn('data-target="75"', donut_html)
        self.assertIn("إنتاج الحرارة", donut_html)

        gbars_html = charts.build_grouped_bars_svg({
            "series": ["الراحة", "الارتجاف"],
            "groups": [
                {"label": "الدقيقة 1", "values": [20, 80]},
                {"label": "الدقيقة 5", "values": [25, 100]},
            ],
            "unit": "%",
        })
        self.assertIn("gbar-rect", gbars_html)
        self.assertIn("الارتجاف", gbars_html)

    def test_chart_scene_types_in_full_html(self):
        spec = {
            "id": "charts-demo",
            "format": "landscape",
            "palette": "editorial",
            "caption_style": "highlighter_bar",
            "scenes": [
                {
                    "id": "c1",
                    "type": "chart_line",
                    "narration": "a.wav",
                    "text": "نمو متسارع عبر M1 و M2 و M3.",
                    "title": "منحنى النمو",
                    "chart": {"points": [15, 48, 92], "labels": ["M1", "M2", "M3"], "unit": "%"},
                },
                {
                    "id": "c2",
                    "type": "chart_donut",
                    "narration": "b.wav",
                    "text": "توزيع الطاقة داخل الجسم البشري.",
                    "title": "توزيع الحرارة",
                    "chart": {
                        "segments": [
                            {"label": "المركز", "value": 80, "color": "accent"},
                            {"label": "الأطراف", "value": 20, "color": "blue"},
                        ]
                    },
                },
                {
                    "id": "c3",
                    "type": "chart_bars",
                    "narration": "c.wav",
                    "text": "مقارنة الأداء قبل وبعد التحسين.",
                    "title": "مقارنة الأداء",
                    "chart": {
                        "series": ["قبل", "بعد"],
                        "groups": [{"label": "السرعة", "values": [30, 95]}],
                    },
                },
                {
                    "id": "c4",
                    "type": "concept",
                    "narration": "d.wav",
                    "text": "مشهد مركب متعدد الطبقات.",
                    "title": "تنظيم الحرارة",
                    "illustration": "thermoregulation",
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
        self.assertIn("chart-line-path", html)
        self.assertIn("donut-seg", html)
        self.assertIn("gbar-rect", html)
        self.assertIn("composite-ill", html)
        self.assertIn("cap-style-highlighter_bar", html)
        self.assertNotIn("@keyframes", html)
        self.assertNotIn("transition:", html)


class TestSmartCaptionsV342(unittest.TestCase):
    def test_balance_caption_chunks_prevents_single_word_orphans(self):
        # 8 words in one sentence -> balanced 4 + 4 (never 7 + 1 orphan)
        text = "جسمك يحافظ على درجة حرارته الداخلية طوال الشتاء."
        words = flat_reels.even_word_timings(text.split(), 0.0, 3.2)
        chunks = captions.balance_caption_chunks(words, max_words_per_chunk=7)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([len(c) for c in chunks], [4, 4])
        for c in chunks:
            self.assertGreaterEqual(len(c), 2)

    def test_keywords_and_mixed_script_latin_isolation(self):
        text = "توقيت الكلمات عبر Deepgram يصل إلى 98 بالمئة."
        timing = {
            "id": "s1",
            "start": 0.0,
            "duration": 2.5,
            "words": flat_reels.even_word_timings(text.split(), 0.0, 2.5),
        }
        scene = {"caption_style": "karaoke_pill", "keywords": ["98", "Deepgram"]}
        cap_html = captions.render_caption_html(timing, scene=scene)
        self.assertIn("cap-style-karaoke_pill", cap_html)
        self.assertIn('data-kw="1"', cap_html)
        self.assertIn('data-latin="1">Deepgram</span>', cap_html)

    def test_mcp_and_cli_v342(self):
        self.assertTrue(agentcut.__version__.startswith("3.4."))
        ds = server.dispatch_tool("list_design_system", {})
        self.assertTrue(ds["version"].startswith("3.4."))
        self.assertGreaterEqual(len(ds["icons"]), 100)
        self.assertGreaterEqual(len(ds["illustrations"]), 6)
        self.assertEqual(len(ds["caption_styles"]), 4)
        self.assertGreaterEqual(len(ds["scene_types"]), 23)
        self.assertEqual(cli_main(["illustrations"]), 0)


if __name__ == "__main__":
    unittest.main()
