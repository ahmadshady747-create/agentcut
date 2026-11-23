"""Verification tests for AgentCut v3.3.8 (Cycle 8 Real Rendered Video Frame Pixel Inspection & Visual Defect Audit)."""
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
from agentcut.pipeline.auto_heal import produce_cinematic_auto_heal
from agentcut.qc import audit_video_quality, inspect_rendered_video_frames
from agentcut.render import produce_explainer, render_contact_sheet
from agentcut.server import TOOLS, call_tool

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


def _write_test_wav(path: Path, duration: float = 2.4, freq: float = 220.0) -> None:
    sr = 16000
    n = int(sr * duration)
    buf = bytearray()
    for i in range(n):
        t = i / sr
        env = 1.0 if t < duration - 0.18 else max(0.0, (duration - t) / 0.18)
        val = int(env * 0.35 * 32767 * math.sin(2.0 * math.pi * freq * t))
        buf.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


class TestCycle8RenderedFrameInspectionV338(unittest.TestCase):
    def test_end_to_end_rendered_video_frame_pixel_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            w2 = tdir / "s2.wav"
            _write_test_wav(w1, duration=2.4, freq=220.0)
            _write_test_wav(w2, duration=2.4, freq=260.0)

            srt1 = tdir / "s1.srt"
            srt2 = tdir / "s2.srt"
            srt1.write_text(
                "1\n00:00:00,080 --> 00:00:02,200\n"
                "الفصل الأول: كيف نصنع فيديو وثائقياً خالياً من العيوب البصرية.\n",
                encoding="utf-8",
            )
            srt2.write_text(
                "1\n00:00:00,080 --> 00:00:02,200\n"
                "وصلنا إلى 69 أداة برمجية مع فحص بصري للإطارات.\n",
                encoding="utf-8",
            )

            out_mp4 = tdir / "real_inspected.mp4"
            frames_dir = tdir / "frames"
            sheet_png = tdir / "contact_sheet.png"

            brief = {
                "title": "اختبار الفحص البصري للإطارات 3.3.8",
                "style": "editorial",
                "cinema_look": "chiaroscuro_doc",
                "auto_tech_cards": True,
                "j_cut": True,
                "output": {"width": 360, "height": 640, "fps": 15},
                "scenes": [
                    {
                        "id": "s1",
                        "text": "الفصل الأول: كيف نصنع فيديو وثائقياً خالياً من العيوب البصرية.",
                        "narration": str(w1),
                        "captions": str(srt1),
                        "broll": str(TEST_MEDIA / "poster.png"),
                    },
                    {
                        "id": "s2",
                        "text": "وصلنا إلى 69 أداة برمجية مع فحص بصري للإطارات.",
                        "narration": str(w2),
                        "captions": str(srt2),
                        "highlight_card": {
                            "title": "69 أداة",
                            "subtitle": "فحص بصري للإطارات",
                            "start": 0.2,
                            "duration": 1.8,
                        },
                    },
                ],
            }

            ok, info = produce_explainer(brief, str(out_mp4))
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out_mp4))

            # 1. Run pixel-level frame inspection across active subtitle & highlight card windows
            f_rep = inspect_rendered_video_frames(
                str(out_mp4),
                project_or_brief=info["project"],
                sample_count=4,
                save_frames_dir=str(frames_dir),
            )
            self.assertTrue(f_rep["passed"], f_rep)
            self.assertEqual(f_rep["grade"], "A")
            self.assertEqual(f_rep["frame_count"], 4)
            self.assertGreater(f_rep["mean_luma_std"], 8.0)
            self.assertTrue(f_rep["subtitle_ink_verified"], f_rep)
            self.assertTrue(f_rep["bottom_margin_clean"], f_rep)
            self.assertEqual(len(f_rep["issues"]), 0, f_rep["issues"])

            # Verify saved PNG frames exist and have valid headers
            saved_pngs = [f["saved_png"] for f in f_rep["frames"] if f.get("saved_png")]
            self.assertEqual(len(saved_pngs), 4)
            for p in saved_pngs:
                self.assertTrue(os.path.isfile(p))
                self.assertGreater(os.path.getsize(p), 1024)

            # 2. Generate contact sheet and verify container QC
            ok_cs, cs_info = render_contact_sheet(str(out_mp4), str(sheet_png))
            self.assertTrue(ok_cs, cs_info)
            self.assertTrue(os.path.isfile(sheet_png))

            qc = audit_video_quality(str(out_mp4))
            self.assertTrue(qc["passed"], qc)

    def test_auto_heal_includes_frame_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            _write_test_wav(w1, duration=2.0)
            srt1 = tdir / "s1.srt"
            srt1.write_text("1\n00:00:00,050 --> 00:00:01,850\nتخيل وضوح الصورة في كل إطار.\n", encoding="utf-8")
            out_mp4 = tdir / "healed_v338.mp4"
            brief = {
                "title": "Auto-Heal Visual Inspection",
                "style": "editorial",
                "scenes": [
                    {
                        "id": "s1",
                        "text": "تخيل وضوح الصورة في كل إطار.",
                        "narration": str(w1),
                        "captions": str(srt1),
                        "broll": str(TEST_MEDIA / "poster.png"),
                    }
                ],
            }
            ok, res = produce_cinematic_auto_heal(brief, str(out_mp4), profile="proxy")
            self.assertTrue(ok, res)
            self.assertIn("frame_inspection", res)
            self.assertTrue(res["frame_inspection"]["passed"], res["frame_inspection"])

    def test_69_mcp_tools_and_cli_inspect_frames(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("inspect_video_frames", tool_names)

        clip1 = str(TEST_MEDIA / "clip1.mp4")
        res = call_tool("inspect_video_frames", {"video_path": clip1, "sample_count": 3})
        self.assertFalse(res.get("isError"), res)
        rc = cli_main(["inspect-frames", clip1, "--samples", "3"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
