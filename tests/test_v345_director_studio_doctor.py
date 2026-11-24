"""Verification test suite for AgentCut v3.4.5 (Cycle 5: Smart Script Director, Preflight, Mobile Studio & Doctor)."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import director_browser, preflight, server, studio_web  # noqa: E402
from agentcut.__main__ import main as cli_main  # noqa: E402


class TestSmartDirectorAndPreflightV345(unittest.TestCase):
    def test_script_to_spec_generates_varied_templates_and_domain_icons(self):
        script = (
            "جسمك درجة حرارته 37 طول السنة حتى في عز البرد.\n\n"
            "طب ازاي؟\n\n"
            "الجلد فيه مستقبلات حرارة بترسل إشارة عصبية للمخ فوراً.\n\n"
            "الارتجاف يضاعف إنتاج الحرارة 5 مرات في دقائق.\n\n"
            "برودة الأطراف مقابل دفء القلب هي معادلة البقاء.\n\n"
            "إحساسك بالبرد هو أذكى نظام إنذار مبكر لحماية أعضائك."
        )
        spec = director_browser.script_to_spec(
            script, title="لغز البرد", brand_kit="tab_ezzay", format_name="vertical"
        )
        self.assertEqual(spec["palette"], "cream")
        self.assertEqual(len(spec["scenes"]), 6)
        types = [sc["type"] for sc in spec["scenes"]]
        self.assertEqual(types[0], "intro")
        self.assertEqual(types[1], "question")
        self.assertIn("statistic", types)
        self.assertIn("comparison", types)
        self.assertEqual(types[-1], "conclusion")
        # Ensure zero consecutive duplicate scene types
        for a, b in zip(types, types[1:]):
            self.assertNotEqual(a, b)

        audit = preflight.preflight_audit(spec, require_audio_files=False)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["consecutive_duplicate_types"], [])
        self.assertTrue(audit["html_audit"]["ok"])

    def test_production_examples_pass_preflight(self):
        repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
        for fname in ("reel_winter_60s.json", "explainer_landscape_16x9.json"):
            fpath = os.path.join(repo_root, "examples", fname)
            self.assertTrue(os.path.isfile(fpath), fpath)
            with open(fpath, encoding="utf-8") as fh:
                spec = json.load(fh)
            res = preflight.preflight_audit(spec, require_audio_files=False)
            self.assertTrue(res["ok"], f"{fname}: {res}")
            self.assertEqual(res["consecutive_duplicate_types"], [], fname)
            self.assertGreaterEqual(res["template_variety_score"], 80.0, fname)


class TestStudioWebAndDoctorV345(unittest.TestCase):
    def test_studio_ui_and_api_router(self):
        ui_html = studio_web.build_studio_ui_html()
        self.assertIn("استوديو أجينت كات", ui_html)
        self.assertIn("/api/script-to-spec", ui_html)

        st_code, st_data = studio_web.handle_studio_api("GET", "/api/status")
        self.assertEqual(st_code, 200)
        self.assertTrue(st_data["ok"])
        self.assertGreaterEqual(st_data["icon_count"], 100)

        code, data = studio_web.handle_studio_api(
            "POST",
            "/api/script-to-spec",
            {
                "title": "تجربة الاستوديو",
                "brand_kit": "midnight_tech",
                "format": "landscape",
                "script": "المتصفح يرسم كل إطار بدقة. طب ازاي؟ دالة setTime تحسب الحركة في 95 ثانية.",
            },
        )
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("window.setTime", data["html"])

        th_code, th_data = studio_web.handle_studio_api(
            "POST", "/api/thumbnail-html", {"spec": data["spec"]}
        )
        self.assertEqual(th_code, 200)
        self.assertIn("th-stage", th_data["html"])

        pf_code, pf_data = studio_web.handle_studio_api(
            "POST", "/api/preflight", {"spec": data["spec"]}
        )
        self.assertEqual(pf_code, 200)
        self.assertTrue(pf_data["ok"])

    def test_doctor_mcp_and_cli_v345(self):
        self.assertEqual(agentcut.__version__, "3.4.5")
        doc = preflight.doctor_environment(probe_browser=False)
        self.assertTrue(doc["ok"], doc)
        self.assertTrue(doc["fonts"]["Cairo.ttf"])

        mcp_s = server.dispatch_tool(
            "script_to_spec",
            {"script_text": "مقدمة أولى. طب ازاي؟ النتيجة 95 بالمئة. الخلاصة النهائية."},
        )
        self.assertTrue(mcp_s["ok"])
        mcp_p = server.dispatch_tool("preflight_audit", {"spec": mcp_s["spec"]})
        self.assertTrue(mcp_p["ok"])
        mcp_d = server.dispatch_tool("doctor_environment", {})
        self.assertTrue(mcp_d["ok"])
        self.assertEqual(cli_main(["doctor"]), 0)


if __name__ == "__main__":
    unittest.main()
