"""Verification tests for AgentCut v3.3.2 (Cycle 2 automatic in-pipeline TTS cleaning)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.autopilot import autopilot_produce
from agentcut.render import produce_explainer
from agentcut.smart_cut import clean_tts_audio

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


class TestCycle2AutoTtsCleanV332(unittest.TestCase):
    def test_preserve_duration_keeps_exact_length_and_mutes_tail(self):
        with tempfile.TemporaryDirectory() as td:
            src = str(Path(td) / "noisy_tail.wav")
            dst = str(Path(td) / "cleaned_preserved.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "sine=frequency=400:duration=2.400", src],
                check=True, timeout=20,
            )
            ok, info = clean_tts_audio(src, dst, trim_tail_sec=0.20, preserve_duration=True)
            self.assertTrue(ok, info)
            self.assertTrue(info["preserve_duration"])
            self.assertAlmostEqual(info["cleaned_duration"], 2.40, places=1)
            self.assertAlmostEqual(info["muted_tail_seconds"], 0.20, places=2)

    def test_produce_explainer_and_autopilot_auto_clean_tts_with_beats(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "s1.wav"
            wav2 = Path(td) / "s2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=320:duration=2.400", str(wav)],
                    check=True, timeout=20,
                )
            srt1 = Path(td) / "s1.srt"
            srt2 = Path(td) / "s2.srt"
            srt1.write_text("1\n00:00:00,000 --> 00:00:02,400\nالمشهد الأول مع تنظيف تلقائي للصوت\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,000 --> 00:00:02,400\nالمشهد الثاني بدون أي هلوسات في النهاية\n", encoding="utf-8")

            img1 = str(TEST_MEDIA / "poster.png")
            img2 = str(TEST_MEDIA / "board.png")
            brief = {
                "title": "Auto Clean TTS Editorial",
                "style": "editorial",
                "aspect": "16:9",
                "parallel": True,
                "render_profile": "proxy",
                "scenes": [
                    {
                        "id": "sc1",
                        "text": "المشهد الأول مع تنظيف تلقائي للصوت",
                        "narration": str(wav1),
                        "captions": str(srt1),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": img1},
                            {"start": 1.2, "end": 2.4, "broll": img2},
                        ],
                    },
                    {
                        "id": "sc2",
                        "text": "المشهد الثاني بدون أي هلوسات في النهاية",
                        "narration": str(wav2),
                        "captions": str(srt2),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": img2},
                            {"start": 1.2, "end": 2.4, "broll": img1},
                        ],
                    },
                ],
            }
            out_mp4 = str(Path(td) / "out.mp4")
            ok, info = produce_explainer(brief, out_mp4)
            self.assertTrue(ok, info)
            self.assertEqual(info.get("tts_cleaned_scenes"), 2)
            self.assertTrue(os.path.isfile(out_mp4))

            # CLI clean-tts check
            cli_out = str(Path(td) / "cli_clean.wav")
            rc = cli_main(["clean-tts", str(wav1), cli_out, "--trim-tail", "0.15", "--preserve-duration"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(cli_out))

    def test_version_is_3_3_2(self):
        self.assertEqual(__version__, "3.3.13")


if __name__ == "__main__":
    unittest.main()
