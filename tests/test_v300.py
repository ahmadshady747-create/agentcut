"""Comprehensive verification suite for AgentCut v3.0.0 (Full Cinematic Documentary Engine)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.parallax import (
    build_video_beat_motion_filter,
    render_chapter_card,
    render_parallax_clip,
)
from agentcut.pipeline import (
    compile_brief_to_ir,
    materialize_soundscape_audio,
    mix_soundscape_into_video,
    produce_cinematic_auto_heal,
    write_filter_script,
)
from agentcut.qc import audit_video_quality
from agentcut.server import TOOLS, call_tool
from agentcut.vision import (
    compute_exposure_match_filter,
    detect_focal_point,
    measure_shot_exposure,
)

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


class TestVisionV3(unittest.TestCase):
    def test_detect_focal_point_and_exposure_matching(self):
        poster = str(TEST_MEDIA / "poster.png")
        board = str(TEST_MEDIA / "board.png")

        fp = detect_focal_point(poster)
        self.assertGreaterEqual(fp["focal_x"], 0.15)
        self.assertLessEqual(fp["focal_x"], 0.85)
        self.assertGreaterEqual(fp["focal_y"], 0.15)
        self.assertLessEqual(fp["focal_y"], 0.85)

        exp = measure_shot_exposure(poster)
        self.assertGreaterEqual(exp["yavg"], 0.0)
        self.assertLessEqual(exp["yavg"], 255.0)

        matched = compute_exposure_match_filter([poster, board])
        self.assertEqual(len(matched["shots"]), 2)
        self.assertGreater(matched["target_yavg"], 0)


class TestParallaxAndChapterCardV3(unittest.TestCase):
    def test_render_chapter_card_and_parallax(self):
        with tempfile.TemporaryDirectory() as td:
            ch_out = str(Path(td) / "chapter1.mp4")
            ok_c, info_c = render_chapter_card(
                title="الفصل الأول: فلسفة الزمن",
                output_path=ch_out,
                subtitle="كيف نصنع المعنى",
                chapter_label="01",
                duration=1.4,
                width=480,
                height=854,
                fps=15,
                with_boom=True,
            )
            self.assertTrue(ok_c, info_c)
            self.assertTrue(os.path.isfile(ch_out))
            qc_c = audit_video_quality(ch_out)
            self.assertTrue(qc_c["passed"], qc_c)

            px_out = str(Path(td) / "parallax.mp4")
            ok_p, info_p = render_parallax_clip(
                bg_path=str(TEST_MEDIA / "poster.png"),
                fg_path=str(TEST_MEDIA / "board.png"),
                output_path=px_out,
                duration=1.2,
                width=360,
                height=640,
                fps=15,
            )
            self.assertTrue(ok_p, info_p)
            self.assertTrue(os.path.isfile(px_out))

        vf_vid = build_video_beat_motion_filter("pan_right_drift", 3.0, 720, 1280)
        self.assertIn("crop=720:1280", vf_vid)


class TestModularPipelineV3(unittest.TestCase):
    def test_ir_compiler_and_filter_script(self):
        brief = {
            "title": "IR Test",
            "style": "editorial",
            "cinema_look": "chiaroscuro_doc",
            "scenes": [{
                "id": "s1",
                "text": "مشهد اختباري",
                "duration": 3.0,
                "broll": str(TEST_MEDIA / "poster.png"),
            }],
        }
        ir = compile_brief_to_ir(brief, auto_focal=True, match_exposure=True)
        self.assertEqual(len(ir.scenes), 1)
        self.assertEqual(ir.total_duration, 3.0)
        self.assertIsNotNone(ir.exposure_target_yavg)

        with tempfile.TemporaryDirectory() as td:
            sp = write_filter_script("[0:v]setsar=1[vout]", directory=td)
            self.assertTrue(os.path.isfile(sp))
            self.assertIn("setsar=1", Path(sp).read_text(encoding="utf-8"))

            mat = materialize_soundscape_audio(
                {"room_tone": "warm_studio", "sfx_events": [{"kind": "chapter_boom", "time": 0.0}]},
                total_duration=2.0,
                work_dir=td,
            )
            self.assertTrue(os.path.isfile(mat["room_tone_path"]))
            self.assertEqual(len(mat["sfx_files"]), 1)


class TestAutoHealPipelineV3(unittest.TestCase):
    def test_end_to_end_produce_cinematic_auto_heal(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "s1.wav"
            wav2 = Path(td) / "s2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=320:duration=2.4", str(wav)],
                    check=True, timeout=20,
                )
            srt1 = Path(td) / "s1.srt"
            srt2 = Path(td) / "s2.srt"
            srt1.write_text("1\n00:00:00,000 --> 00:00:02,400\nتخيل عالماً وثائقياً متكامل الصوت والصورة\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,000 --> 00:00:02,400\nلماذا يبدو الإخراج الذكي أقرب للسينما؟\n", encoding="utf-8")

            brief = {
                "title": "Auto-Heal Documentary Proof",
                "style": "editorial",
                "aspect": "16:9",
                "parallel": True,
                "scenes": [
                    {
                        "id": "sc1",
                        "text": "تخيل عالماً وثائقياً متكامل الصوت والصورة",
                        "narration": str(wav1),
                        "captions": str(srt1),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": str(TEST_MEDIA / "poster.png")},
                            {"start": 1.2, "end": 2.4, "broll": str(TEST_MEDIA / "board.png")},
                        ],
                    },
                    {
                        "id": "sc2",
                        "text": "لماذا يبدو الإخراج الذكي أقرب للسينما؟",
                        "narration": str(wav2),
                        "captions": str(srt2),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": str(TEST_MEDIA / "board.png")},
                            {"start": 1.2, "end": 2.4, "broll": str(TEST_MEDIA / "poster.png")},
                        ],
                    },
                ],
            }
            out_mp4 = str(Path(td) / "auto_healed.mp4")
            ok, res = produce_cinematic_auto_heal(
                brief,
                out_mp4,
                cinema_look="chiaroscuro_doc",
                profile="proxy",
                auto_focal=True,
                match_exposure=True,
                mix_soundscape=True,
            )
            self.assertTrue(ok, res)
            self.assertTrue(res["healed"])
            self.assertTrue(os.path.isfile(out_mp4))
            self.assertGreaterEqual(res["craft_after"]["score"], res["craft_before"]["score"])
            self.assertTrue(res["qc"]["passed"], res["qc"])
            self.assertIsNotNone(res["render"].get("soundscape_mix"))
            self.assertTrue(res["render"]["soundscape_mix"]["room_tone"])


class TestMcpAndCliV3(unittest.TestCase):
    def test_55_mcp_tools_and_v3_cli(self):
        self.assertEqual(__version__, "3.2.0")
        self.assertEqual(len(TOOLS), 62)

        r1 = call_tool("detect_focal_point", {"media": str(TEST_MEDIA / "poster.png")})
        self.assertFalse(r1.get("isError"))
        self.assertIn("focal_x", json.loads(r1["content"][0]["text"]))

        r2 = call_tool("match_shot_exposure", {"media_paths": [str(TEST_MEDIA / "poster.png"), str(TEST_MEDIA / "board.png")]})
        self.assertFalse(r2.get("isError"))
        self.assertEqual(len(json.loads(r2["content"][0]["text"])["shots"]), 2)

        self.assertEqual(cli_main(["focal-point", str(TEST_MEDIA / "poster.png")]), 0)
        self.assertEqual(cli_main(["exposure-match", str(TEST_MEDIA / "poster.png"), str(TEST_MEDIA / "board.png")]), 0)


if __name__ == "__main__":
    unittest.main()
