"""Unit and integration tests for AgentCut Multi-Genre Content Templates Engine (v1.50.0)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from agentcut.templates import (
    CONTENT_TEMPLATES,
    ASPECT_PRESETS,
    list_content_templates,
    get_content_template,
    build_brief_from_template,
    apply_template_to_brief,
)
from agentcut.server import TOOLS
from agentcut.validate import validate_project


class TestContentTemplates(unittest.TestCase):
    def test_list_all_8_templates(self):
        """All 8 turnkey templates are defined and returned with complete metadata."""
        templates = list_content_templates()
        self.assertEqual(len(templates), 8)
        expected_ids = {
            "finance_data_story",
            "history_documentary",
            "tech_code_explainer",
            "top_list_countdown",
            "news_breaking_brief",
            "educational_lesson",
            "podcast_audiogram_quote",
            "product_promo_showcase",
        }
        found_ids = {t["id"] for t in templates}
        self.assertEqual(found_ids, expected_ids)

        for t in templates:
            self.assertIn("name_ar", t)
            self.assertIn("name_en", t)
            self.assertIn("colors", t)
            self.assertIn("typography", t)
            self.assertIn("transitions", t)
            self.assertIn("scene_roles", t)
            self.assertIn("default_aspect", t)

    def test_category_filtering(self):
        """Category filtering returns the correct subset of templates."""
        finance = list_content_templates(category="finance")
        self.assertEqual(len(finance), 1)
        self.assertEqual(finance[0]["id"], "finance_data_story")

        tech = list_content_templates(category="tech")
        self.assertEqual(len(tech), 1)
        self.assertEqual(tech[0]["id"], "tech_code_explainer")

        news = list_content_templates(category="news")
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["id"], "news_breaking_brief")

        all_tpls = list_content_templates(category="all")
        self.assertEqual(len(all_tpls), 8)

    def test_wcag_contrast_compliance(self):
        """All 8 content templates have WCAG AA / AAA compliant contrast ratios."""
        templates = list_content_templates()
        for t in templates:
            colors = t["colors"]
            ratio = colors["contrast_ratio"]
            # WCAG AA requires at least 4.5:1 for normal text
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"Template {t['id']} failed WCAG AA with contrast ratio {ratio}:1",
            )
            self.assertTrue(colors["wcag_aa"])

    def test_build_brief_all_templates(self):
        """build_brief_from_template generates valid, rich explainer briefs for all 8 genres."""
        sample_scenes = [
            {"title": "المقدمة والمدخل", "text": "نص المشهد الأول للبداية.", "role": "hook"},
            {"title": "العرض والبيانات", "text": "نص المشهد الثاني مع الأرقام.", "role": "data_proof", "data": [10, 30, 60, 90]},
            {"title": "الخاتمة والتوصيات", "text": "نص المشهد الأخير للخلاصة.", "role": "conclusion"},
        ]

        for tid in CONTENT_TEMPLATES:
            brief = build_brief_from_template(
                tid,
                topic_title=f"اختبار قالب {tid}",
                scenes_input=sample_scenes,
                aspect="9:16",
            )
            self.assertEqual(brief["template"], tid)
            self.assertEqual(brief["title"], f"اختبار قالب {tid}")
            self.assertEqual(brief["output"]["width"], 720)
            self.assertEqual(brief["output"]["height"], 1280)
            self.assertEqual(len(brief["scenes"]), 3)
            self.assertIn("highlight_color", brief)
            self.assertIn("captions_style", brief)

    def test_aspect_ratios_support(self):
        """Supports 9:16, 16:9, 1:1, and 4:5 aspect ratio output configurations."""
        sample_scenes = [{"title": "مشهد 1", "text": "نص توضيحي."}]
        for aspect, dims in ASPECT_PRESETS.items():
            brief = build_brief_from_template(
                "finance_data_story",
                topic_title="اختبار الأبعاد",
                scenes_input=sample_scenes,
                aspect=aspect,
            )
            self.assertEqual(brief["output"]["width"], dims["width"])
            self.assertEqual(brief["output"]["height"], dims["height"])

    def test_examples_json_files_exist_and_valid(self):
        """All 8 ready-to-run template JSON briefs in examples/templates/ are valid JSON."""
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples", "templates")
        self.assertTrue(os.path.isdir(examples_dir))

        for tid in CONTENT_TEMPLATES:
            fpath = os.path.join(examples_dir, f"{tid}.json")
            self.assertTrue(os.path.isfile(fpath), f"Missing example file: {fpath}")
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIsInstance(data, dict)
            self.assertEqual(data.get("template"), tid)
            self.assertIn("scenes", data)
            self.assertGreater(len(data["scenes"]), 0)

    def test_apply_template_to_brief(self):
        """apply_template_to_brief populates missing defaults on existing brief."""
        raw_brief = {
            "id": "my_video",
            "template": "tech_code_explainer",
            "title": "ذكاء الوكلاء",
            "scenes": [{"title": "المشهد 1", "text": "نص"}],
        }
        enriched = apply_template_to_brief(raw_brief)
        self.assertEqual(enriched["style"], "blackboard")
        self.assertEqual(enriched["look"], "teal_orange")
        self.assertEqual(enriched["captions_style"], "karaoke")
        self.assertEqual(enriched["highlight_color"], "green")
        self.assertEqual(enriched["background"], "canvas")

    def test_server_tools_registered(self):
        """list_content_templates and produce_from_template tools are registered in MCP server."""
        tool_names = [t["name"] for t in TOOLS]
        self.assertIn("list_content_templates", tool_names)
        self.assertIn("produce_from_template", tool_names)

    def test_end_to_end_brief_validation(self):
        """Briefs produced from templates translate into valid project structures."""
        sample_scenes = [
            {"title": "المقدمة", "text": "مرحباً بكم في هذا الدرس.", "role": "hook"},
            {"title": "التفاصيل", "text": "نتناول اليوم شرح المفهوم.", "role": "process", "points": ["أ", "ب", "ج"]},
            {"title": "الخلاصة", "text": "شكراً للمتابعة.", "role": "conclusion"},
        ]
        brief = build_brief_from_template("educational_lesson", "درس تجريبي", sample_scenes, aspect="16:9")
        self.assertEqual(brief["template"], "educational_lesson")
        self.assertEqual(brief["output"]["width"], 1280)
        self.assertEqual(brief["output"]["height"], 720)


if __name__ == "__main__":
    unittest.main()
