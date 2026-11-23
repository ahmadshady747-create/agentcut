"""Verification tests for AgentCut v3.3.9 (Cycle 9 Open-Licensed Arabic/Dual-Script Font Migration to Cairo OFL & Removal of Proprietary Fonts)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.brief_repair import repair_brief
from agentcut.render import _ass_font_for, render_project
from agentcut.server import TOOLS, call_tool
from agentcut.srt import measure_text_width
from agentcut.templates import CONTENT_TEMPLATES, list_content_templates
from agentcut.validate import (
    BUNDLED_FONTS,
    DUAL_SCRIPT_FONT_FILES,
    audit_font_compliance,
    bundled_fonts_dir,
    find_bundled_font,
    find_font_for_text,
)


class TestCycle9OpenLicensedFontsV339(unittest.TestCase):
    def test_proprietary_fonts_removed_and_cairo_is_default_dual_script(self) -> None:
        fdir = Path(bundled_fonts_dir() or "")
        self.assertTrue(fdir.is_dir())

        # Proprietary Microsoft fonts must not exist in bundled fonts/
        self.assertFalse((fdir / "Arial.ttf").exists())
        self.assertFalse((fdir / "SegoeUI.ttf").exists())
        self.assertNotIn("segoe-ui", BUNDLED_FONTS)
        self.assertNotIn("arial", BUNDLED_FONTS)

        # Open-licensed Cairo OFL must be bundled and primary dual-script default
        self.assertIn("cairo", BUNDLED_FONTS)
        self.assertTrue((fdir / "Cairo.ttf").is_file())
        self.assertEqual(DUAL_SCRIPT_FONT_FILES[0], "Cairo.ttf")

        # Mixed Arabic + Latin / ASCII symbols must resolve to Cairo.ttf
        mixed_text = "مقارنة الأداء: AgentCut v3.3.9 يحقق 100% توافق (OFL)!"
        resolved_mixed = find_font_for_text(mixed_text)
        self.assertIsNotNone(resolved_mixed)
        self.assertEqual(os.path.basename(resolved_mixed), "Cairo.ttf")

        # Pure Arabic still resolves to NotoSansArabic-Bold.ttf
        pure_ar = "تخيل بداية الحكاية في عمق المشهد"
        resolved_ar = find_font_for_text(pure_ar)
        self.assertIsNotNone(resolved_ar)
        self.assertEqual(os.path.basename(resolved_ar), "NotoSansArabic-Bold.ttf")

        # Pure Latin still resolves to DejaVuSans.ttf
        pure_lat = "Pure Latin documentary subtitle"
        resolved_lat = find_font_for_text(pure_lat)
        self.assertIsNotNone(resolved_lat)
        self.assertEqual(os.path.basename(resolved_lat), "DejaVuSans.ttf")

    def test_all_templates_and_glyph_smoke_render_with_open_fonts(self) -> None:
        for tid, tpl in CONTENT_TEMPLATES.items():
            font_key = tpl.get("typography", {}).get("font")
            self.assertIn(font_key, BUNDLED_FONTS, f"Template {tid} uses invalid font {font_key!r}")
            self.assertEqual(font_key, "cairo")

        # HarfBuzz measurement and ASS font mapping for all 4 bundled open fonts
        for fkey in ("cairo", "noto-sans-bold", "noto-naskh", "dejavu"):
            fpath = find_bundled_font(fkey)
            self.assertIsNotNone(fpath, fkey)
            sample = "اختبار الخط العربي Cairo 2026" if fkey != "dejavu" else "AgentCut Open Font 2026"
            w = measure_text_width(sample, fpath, 42)
            self.assertGreater(w, 80.0)

        self.assertEqual(_ass_font_for({"font": "cairo"}, "نص عربي"), ("Cairo", -1))

        # Smoke render a real video frame with mixed Arabic+Latin text using Cairo.ttf
        with tempfile.TemporaryDirectory() as tmp:
            out_mp4 = os.path.join(tmp, "cairo_smoke.mp4")
            proj = {
                "id": "cairo_smoke",
                "version": 1,
                "output": {"width": 360, "height": 640, "fps": 15},
                "clips": [
                    {
                        "id": "c1",
                        "kind": "image",
                        "src": "data/test_media/poster.png",
                        "duration": 1.2,
                    }
                ],
                "texts": [
                    {
                        "text": "خط القاهرة المفتوح: AgentCut (100% OFL)",
                        "font": "cairo",
                        "start": 0.0,
                        "end": 1.1,
                        "size": 30,
                        "x": "center",
                        "y": 280,
                        "color": "white",
                        "box": True,
                    }
                ],
            }
            ok, info = render_project(proj, out_mp4)
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out_mp4))

        # Verify brief_repair coerces legacy segoe-ui / arial to cairo
        repaired, notes = repair_brief({
            "title": "T",
            "font": "segoe-ui",
            "scenes": [{"id": "s1", "text": "مرحبا", "font": "arial"}],
        })
        self.assertEqual(repaired.get("font"), "cairo")
        self.assertEqual(repaired["scenes"][0].get("font"), "cairo")
        self.assertIn("replaced_proprietary_font_with_cairo", notes)

    def test_70_mcp_tools_and_font_compliance_audit(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("audit_font_compliance", tool_names)

        audit = audit_font_compliance()
        self.assertTrue(audit["passed"], audit)
        self.assertEqual(audit["proprietary_fonts_present"], [])
        self.assertEqual(audit["default_dual_script_font"], "Cairo.ttf")

        res = call_tool("audit_font_compliance", {})
        self.assertFalse(res.get("isError"), res)
        rc = cli_main(["fonts-audit"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
