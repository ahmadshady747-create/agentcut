"""Verification tests for AgentCut v3.3.6 (Cycle 6 Automatic Chapter Cards, 2.5D Parallax & J-Cut Render Integration)."""
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
from agentcut.parallax import (
    auto_attach_chapter_cards,
    render_cached_parallax_beat,
    render_chapter_card_png,
    synthesize_depth_foreground_png,
)
from agentcut.qc import audit_video_quality
from agentcut.render import produce_explainer
from agentcut.server import TOOLS, call_tool

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


def _write_test_wav(path: Path, duration: float = 2.2) -> None:
    sr = 16000
    n = int(sr * duration)
    buf = bytearray()
    for i in range(n):
        t = i / sr
        env = 1.0 if t < duration - 0.2 else max(0.0, (duration - t) / 0.2)
        val = int(env * 0.35 * 32767 * math.sin(2.0 * math.pi * 240.0 * t))
        buf.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


class TestCycle6ChapterCardsParallaxJCutV336(unittest.TestCase):
    def test_chapter_card_png_and_auto_attach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok, res = render_chapter_card_png(
                title="الفصل الأول: البداية",
                subtitle="رحلة في عمق الصورة",
                chapter_label="الفصل 01",
                width=360,
                height=640,
                project_dir=tmp,
            )
            self.assertTrue(ok, res)
            self.assertTrue(os.path.isfile(res["output"]))

        brief = {
            "title": "مقال",
            "auto_chapter_cards": True,
            "scenes": [
                {"id": "s1", "title": "مدخل الحكاية", "text": "تخيل البداية."},
                {"id": "s2", "text": "مشهد ثانٍ."},
                {"id": "s3", "chapter_title": "المحور الثاني: التحول", "text": "هنا يتغير كل شيء."},
            ],
        }
        enriched = auto_attach_chapter_cards(brief, interval_scenes=3)
        self.assertIn("chapter_card", enriched["scenes"][0])
        self.assertIn("chapter_card", enriched["scenes"][2])
        self.assertEqual(enriched["scenes"][2]["chapter_card"]["title"], "المحور الثاني: التحول")

    def test_produce_explainer_integrates_chapter_cards_parallax_and_jcut(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            w2 = tdir / "s2.wav"
            _write_test_wav(w1, duration=2.2)
            _write_test_wav(w2, duration=2.2)

            srt1 = tdir / "s1.srt"
            srt2 = tdir / "s2.srt"
            srt1.write_text("1\n00:00:00,050 --> 00:00:02,000\nالفصل الأول: البداية في عمق المشهد.\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,050 --> 00:00:02,000\nلماذا يسبق الصوت الصورة في المونتاج السينمائي؟\n", encoding="utf-8")

            out_mp4 = tdir / "cycle6.mp4"
            brief = {
                "title": "اختبار الدورة السادسة 3.3.6",
                "style": "editorial",
                "cinema_look": "chiaroscuro_doc",
                "auto_chapter_cards": True,
                "j_cut": True,
                "j_cut_lead": 0.18,
                "output": {"width": 360, "height": 640, "fps": 15},
                "scenes": [
                    {
                        "id": "s1",
                        "chapter_card": {"title": "الفصل الأول: البداية", "label": "الفصل 01"},
                        "text": "الفصل الأول: البداية في عمق المشهد.",
                        "narration": str(w1),
                        "captions": str(srt1),
                    },
                    {
                        "id": "s2",
                        "text": "لماذا يسبق الصوت الصورة في المونتاج السينمائي؟",
                        "narration": str(w2),
                        "captions": str(srt2),
                        "broll": str(TEST_MEDIA / "poster.png"),
                        "fg_broll": str(TEST_MEDIA / "board.png"),
                        "parallax": True,
                    },
                ],
            }
            ok, info = produce_explainer(brief, str(out_mp4))
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out_mp4))
            self.assertGreaterEqual(info.get("chapter_cards_rendered", 0), 1)
            self.assertGreaterEqual(info.get("parallax_clips_rendered", 0), 1)
            self.assertGreaterEqual(info.get("j_cuts_applied", 0), 1)

            # Verify scene 2 audio_start leads visual start (J-Cut)
            sc2 = info["scenes"][1]
            self.assertLess(sc2["audio_start"], sc2["start"])
            self.assertGreater(sc2["j_cut_lead_sec"], 0.10)

            qc = audit_video_quality(str(out_mp4))
            self.assertTrue(qc["passed"], qc)

    def test_67_mcp_tools_and_cli_auto_chapters(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("auto_chapter_cards", tool_names)

        res = call_tool(
            "auto_chapter_cards",
            {
                "brief": {
                    "title": "مقال",
                    "auto_chapter_cards": True,
                    "scenes": [
                        {"id": "s1", "title": "الفصل الأول", "text": "تخيل البداية."},
                        {"id": "s2", "text": "تفصيل."},
                    ],
                }
            },
        )
        self.assertFalse(res.get("isError"), res)


if __name__ == "__main__":
    unittest.main()
