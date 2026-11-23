"""Verification tests for AgentCut v3.3.1 (Cycle 1 real-video quality fixes)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.art_director import STYLE_BIBLES
from agentcut.cinematography import CINEMA_LOOKS
from agentcut.director import analyze_narrative_beat, humanize_brief
from agentcut.server import TOOLS, call_tool
from agentcut.smart_cut import clean_tts_audio


class TestCycle1FixesV331(unittest.TestCase):
    def test_crisp_cinema_and_clean_chiaroscuro(self):
        self.assertIn("crisp_cinema", CINEMA_LOOKS)
        self.assertNotIn("bs=0.04", CINEMA_LOOKS["chiaroscuro_doc"]["vf"])

    def test_hard_cut_default_in_director(self):
        self.assertEqual(analyze_narrative_beat("هذه جملة سردية عادية", 1, 4)["recommended_transition"], "none")
        self.assertEqual(analyze_narrative_beat("«الحكمة تبدأ من الصمت»", 2, 4)["recommended_transition"], "crossfade")
        h = humanize_brief({"title": "T", "scenes": [{"text": "تخيل"}, {"text": "النتيجة 90%"}]})
        self.assertEqual(h["beat_transition"], "none")

    def test_art_director_bans_faces_and_text(self):
        neg = STYLE_BIBLES["chiaroscuro_doc"]["negative"]
        self.assertIn("no human faces", neg)
        self.assertIn("no arabic script", neg)

    def test_clean_tts_audio_trims_tail_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            src = str(Path(td) / "raw_tts.wav")
            dst = str(Path(td) / "clean_tts.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "sine=frequency=280:duration=2.0", src],
                check=True, timeout=20,
            )
            ok, info = clean_tts_audio(src, dst, trim_tail_sec=0.18)
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(dst))
            self.assertLess(info["cleaned_duration"], info["original_duration"])

    def test_63_mcp_tools(self):
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        r = call_tool("clean_tts_audio", {"input": "/tmp/missing.wav", "output": "/tmp/out.wav"})
        self.assertTrue(r.get("isError"))


if __name__ == "__main__":
    unittest.main()
