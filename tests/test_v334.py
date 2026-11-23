"""Verification tests for AgentCut v3.3.4 (Cycle 4 Generative Ambient Music & Sidechain Ducking)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.director import humanize_brief
from agentcut.pipeline.audio_mixer import mix_soundscape_into_video
from agentcut.qc import audit_video_quality
from agentcut.server import TOOLS, call_tool
from agentcut.soundscape import (
    MUSIC_BED_PRESETS,
    generate_ambient_music_bed,
    list_music_presets,
)


class TestCycle4MusicAndSidechainDuckingV334(unittest.TestCase):
    def test_synthesize_all_three_ambient_music_presets(self):
        self.assertEqual(len(list_music_presets()), 3)
        with tempfile.TemporaryDirectory() as td:
            for preset in ("documentary_drone", "contemplative_pad", "tech_pulse"):
                out_wav = str(Path(td) / f"{preset}.wav")
                ok, info = generate_ambient_music_bed(out_wav, duration=2.0, preset=preset)
                self.assertTrue(ok, info)
                self.assertTrue(os.path.isfile(out_wav))
                self.assertGreater(info["size_bytes"], 2048)

    def test_sidechain_ducking_mixer_with_generative_music_bed(self):
        with tempfile.TemporaryDirectory() as td:
            vid = str(Path(td) / "base.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=steelblue:s=320x240:r=15:d=2.4",
                    "-f", "lavfi", "-i", "sine=frequency=350:duration=2.4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    vid,
                ],
                check=True,
                timeout=25,
            )
            out_vid = str(Path(td) / "ducked.mp4")
            soundscape = {
                "room_tone": "warm_studio",
                "music_bed": "documentary_drone",
                "sidechain_ducking": True,
                "sfx_events": [{"kind": "chapter_boom", "time": 0.0}],
            }
            ok, res = mix_soundscape_into_video(vid, out_vid, soundscape, total_duration=2.4)
            self.assertTrue(ok, res)
            self.assertTrue(res["music_bed"])
            self.assertTrue(res["sidechain_ducking"])
            qc = audit_video_quality(out_vid)
            self.assertTrue(qc["passed"], qc)

    def test_director_attaches_mood_matched_music_bed_and_65_tools(self):
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)

        h_tech = humanize_brief({"title": "T", "scenes": [{"text": "تخيل"}]}, cinema_look="midnight_tech")
        self.assertEqual(h_tech["soundscape"]["music_bed"], "tech_pulse")
        self.assertTrue(h_tech["soundscape"]["sidechain_ducking"])

        with tempfile.TemporaryDirectory() as td:
            m_out = str(Path(td) / "mcp_bed.wav")
            r = call_tool("generate_music_bed", {"output": m_out, "duration": 1.5, "preset": "contemplative_pad"})
            self.assertFalse(r.get("isError"))
            self.assertTrue(os.path.isfile(m_out))

            c_out = str(Path(td) / "cli_bed.wav")
            self.assertEqual(cli_main(["music-bed", c_out, "1.5", "--preset", "tech_pulse"]), 0)
            self.assertTrue(os.path.isfile(c_out))


if __name__ == "__main__":
    unittest.main()
