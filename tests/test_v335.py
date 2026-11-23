"""Verification tests for AgentCut v3.3.5 (Cycle 5 Arabic Subtitle Pill Aesthetics, Contrast & Bottom Breathing Room)."""
from __future__ import annotations

import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.director import humanize_brief
from agentcut.render import produce_explainer
from agentcut.server import TOOLS, call_tool
from agentcut.subtitles_style import (
    audit_subtitle_legibility,
    compute_subtitle_layout,
    list_subtitle_themes,
    render_styled_pill_png,
    style_arabic_caption_entry,
)


def _write_test_wav(path: Path, duration: float = 2.0) -> None:
    sr = 16000
    n = int(sr * duration)
    buf = bytearray()
    for i in range(n):
        t = i / sr
        val = int(0.35 * 32767 * math.sin(2.0 * math.pi * 220.0 * t))
        buf.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


class TestCycle5ArabicSubtitlesV335(unittest.TestCase):
    def test_themes_layout_and_wcag_aaa_contrast(self) -> None:
        themes = list_subtitle_themes()
        self.assertEqual(len(themes), 3)
        names = {t["name"] for t in themes}
        self.assertEqual(names, {"editorial_cinema", "gold_documentary", "high_contrast_broadcast"})

        # Check vertical 9:16 1080x1920 layout breathing room and contrast
        lay_v = compute_subtitle_layout(1080, 1920, theme="editorial_cinema", n_lines=2)
        self.assertGreaterEqual(lay_v["bottom_clearance_frac"], 0.10)
        self.assertGreaterEqual(lay_v["bottom_clearance_px"], 190)
        self.assertGreaterEqual(lay_v["wcag_contrast_ratio"], 11.5)
        self.assertEqual(lay_v["wcag_grade"], "AAA")
        self.assertGreaterEqual(lay_v["box_padding_x"], 32)
        self.assertGreater(lay_v["card_to_pill_gap_px"], 60)

        # Render anti-aliased capsule pill PNG with subtle glass rim
        with tempfile.TemporaryDirectory() as tmp:
            pill_p = os.path.join(tmp, "pill_rim.png")
            out_p = render_styled_pill_png(
                pill_p, 320, 76, 22,
                color="#0B0F19", opacity=0.82,
                border_color="#F59E0B", border_opacity=0.28, border_width=1,
            )
            self.assertTrue(os.path.isfile(out_p))
            with open(out_p, "rb") as fh:
                hdr = fh.read(24)
            self.assertEqual(hdr[:8], b"\x89PNG\r\n\x1a\n")
            w, h = struct.unpack(">II", hdr[16:24])
            self.assertEqual((w, h), (320, 76))

    def test_produce_explainer_styles_arabic_captions_and_renders_clean_pill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            _write_test_wav(w1, duration=2.0)
            srt1 = tdir / "s1.srt"
            srt1.write_text(
                "1\n00:00:00,050 --> 00:00:01,900\n"
                "تخيل كيف يتنفس شريط الترجمة العربي بتباين سينمائي واضح.\n",
                encoding="utf-8",
            )
            out_mp4 = tdir / "sub_v335.mp4"
            brief = {
                "title": "اختبار شريط الترجمة 3.3.5",
                "style": "editorial",
                "cinema_look": "chiaroscuro_doc",
                "output": {"width": 360, "height": 640, "fps": 15},
                "scenes": [
                    {
                        "id": "s1",
                        "text": "تخيل كيف يتنفس شريط الترجمة العربي بتباين سينمائي واضح.",
                        "narration": str(w1),
                        "captions": str(srt1),
                    }
                ],
            }
            ok, info = produce_explainer(brief, str(out_mp4))
            self.assertTrue(ok, info)
            proj = info["project"]
            self.assertEqual(proj.get("subtitle_theme"), "gold_documentary")
            self.assertGreaterEqual(proj.get("caption_margin_bottom", 0), 68)
            cap0 = proj["captions"][0]
            self.assertEqual(cap0["box_color"], "#0F172A")
            self.assertGreaterEqual(float(cap0["box_opacity"]), 0.80)
            self.assertGreater(int(cap0["box_padding_x"]), int(cap0["box_padding_y"]))

            audit = audit_subtitle_legibility(proj)
            self.assertTrue(audit["passed"], audit)
            self.assertEqual(audit["grade"], "A")
            self.assertGreaterEqual(audit["layout"]["wcag_contrast_ratio"], 11.5)

    def test_66_mcp_tools_and_cli_subtitle_audit(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("audit_subtitle_legibility", tool_names)

        directed = humanize_brief(
            {"title": "مقال", "scenes": [{"id": "s1", "text": "تخيل البداية.", "duration": 2.5}]},
            cinema_look="chiaroscuro_doc",
        )
        self.assertEqual(directed["subtitle_theme"], "gold_documentary")

        res = call_tool("audit_subtitle_legibility", {"theme": "editorial_cinema", "width": 1080, "height": 1920})
        self.assertFalse(res.get("isError"), res)
        rc = cli_main(["subtitle-audit", "--theme", "gold_documentary"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
