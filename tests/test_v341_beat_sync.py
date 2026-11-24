"""Verification test suite for AgentCut v3.4.1 (Cycle 1: Acoustic Beat-Sync & Audio Engineering)."""
from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import beat_sync, browser_render, flat_reels, server, soundscape  # noqa: E402
from agentcut.__main__ import main as cli_main  # noqa: E402


def _write_pulsed_wav(path: str, duration: float = 2.0, rate: int = 16000) -> None:
    """Create a mono WAV with two clear acoustic energy bursts and a natural pause."""
    n = int(duration * rate)
    data = bytearray()
    for i in range(n):
        t = i / rate
        # Burst 1 around [0.25, 0.55], Pause around [0.60, 0.90], Burst 2 around [1.10, 1.45]
        if 0.25 <= t <= 0.55:
            amp = 22000 * math.sin(math.pi * (t - 0.25) / 0.30)
        elif 0.60 <= t <= 0.90:
            amp = 120  # natural pause
        elif 1.10 <= t <= 1.45:
            amp = 26000 * math.sin(math.pi * (t - 1.10) / 0.35)
        else:
            amp = 3200
        v = int(amp * math.sin(2 * math.pi * 320 * t))
        data += struct.pack("<h", max(-32767, min(32767, v)))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(data))


class TestBeatSyncEngineV341(unittest.TestCase):
    def test_analyze_audio_beats_detects_peaks_and_pauses(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "voice.wav")
            _write_pulsed_wav(wav, duration=2.0)
            info = beat_sync.analyze_audio_beats(wav)
            self.assertAlmostEqual(info["duration"], 2.0, places=1)
            self.assertGreaterEqual(len(info["peaks"]), 2)
            self.assertGreaterEqual(len(info["pauses"]), 1)
            # Verify pause falls inside our [0.55, 0.95] silence window
            pause = info["pauses"][0]
            self.assertGreaterEqual(pause["start"], 0.50)
            self.assertLessEqual(pause["end"], 1.00)

    def test_build_scene_beat_profile_and_html_embedding(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_pulsed_wav(os.path.join(tmp, "s1.wav"), 2.0)
            _write_pulsed_wav(os.path.join(tmp, "s2.wav"), 2.0)
            spec = {
                "id": "beat-reel",
                "palette": "cream",
                "beat_sync_sfx": True,
                "scenes": [
                    {
                        "id": "s1",
                        "type": "intro",
                        "narration": "s1.wav",
                        "text": "جسمك حرارته 37 درجة طول السنة.",
                        "title": "حرارة الجسم",
                        "keywords": ["37"],
                        "steps": ["أ", "ب", "ج"],
                    },
                    {
                        "id": "s2",
                        "type": "question",
                        "narration": "s2.wav",
                        "text": "طب ازاي؟",
                    },
                ],
            }
            timings, _tot, errs = flat_reels.compute_scene_timings(spec, tmp)
            self.assertEqual(errs, [])
            prof = beat_sync.build_scene_beat_profile(spec, timings, base_dir=tmp)
            self.assertIn("s1", prof)
            self.assertTrue(prof["s1"]["peaks"])
            self.assertTrue(prof["s1"]["anchors"])
            self.assertTrue(prof["s1"]["emphasis_sfx"])
            html = flat_reels.build_video_html(
                spec, timings, embed_font=False, beat_profiles=prof
            )
            self.assertIn("function beatEnergy", html)
            self.assertIn('"peaks":', html)
            self.assertNotIn("@keyframes", html)


class TestSoundscapeAndAudioAuditV341(unittest.TestCase):
    def test_15_sfx_and_6_music_beds(self):
        self.assertEqual(len(soundscape.SFX_CATALOG), 15)
        self.assertEqual(len(soundscape.MUSIC_BED_PRESETS), 6)
        with tempfile.TemporaryDirectory() as tmp:
            for kind in (
                "page_flip",
                "marker_swipe",
                "pulse_heartbeat",
                "digital_blip",
                "success_chord",
                "tape_stop",
            ):
                out = os.path.join(tmp, f"{kind}.wav")
                ok, info = soundscape.synthesize_sfx(kind, out)
                self.assertTrue(ok, info)
                self.assertTrue(os.path.isfile(out))
            for preset in ("curiosity_marimba", "warm_acoustic", "cinematic_tension"):
                bed = os.path.join(tmp, f"{preset}.wav")
                ok, info = soundscape.generate_ambient_music_bed(bed, 1.0, preset=preset)
                self.assertTrue(ok, info)
                self.assertTrue(os.path.isfile(bed))

    def test_jl_cut_schedule_and_mix_audio_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "s1.wav")
            b = os.path.join(tmp, "s2.wav")
            _write_pulsed_wav(a, 1.5)
            _write_pulsed_wav(b, 1.5)
            spec = {
                "id": "jl-mix",
                "auto_sfx": True,
                "beat_sync_sfx": True,
                "j_cut": 0.16,
                "l_cut": 0.14,
                "music_bed": {"preset": "curiosity_marimba", "volume_db": -22.0},
                "scenes": [
                    {"id": "s1", "type": "intro", "narration": "s1.wav", "text": "مقدمة أولى."},
                    {"id": "s2", "type": "conclusion", "narration": "s2.wav", "text": "خاتمة ثانية."},
                ],
            }
            timings, _tot, errs = flat_reels.compute_scene_timings(spec, tmp)
            self.assertEqual(errs, [])
            jl = soundscape.plan_jl_cut_schedule(spec, timings)
            self.assertEqual(jl[0]["j_cut"], 0.0)
            self.assertEqual(jl[1]["j_cut"], 0.16)
            self.assertAlmostEqual(jl[1]["audio_start"], timings[1]["start"] - 0.16, places=2)

            prof = beat_sync.build_scene_beat_profile(spec, timings, base_dir=tmp)
            ok, mix = flat_reels.build_audio_mix(spec, timings, tmp, tmp, beat_profiles=prof)
            self.assertTrue(ok, mix)
            self.assertTrue(mix.get("ducked"))
            self.assertGreaterEqual(mix.get("jl_cuts_applied", 0), 1)
            audit = mix.get("audit")
            self.assertIsNotNone(audit)
            self.assertIn("input_i", audit)
            self.assertIn("input_tp", audit)

    def test_mcp_and_cli_v341(self):
        self.assertTrue(agentcut.__version__.startswith("3.4."))
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "sample.wav")
            _write_pulsed_wav(wav, 1.6)
            r_beats = server.dispatch_tool("analyze_audio_beats", {"audio_path": wav})
            self.assertTrue(r_beats["ok"])
            self.assertTrue(r_beats["peaks"])
            r_audit = server.dispatch_tool("audit_audio_mix", {"audio_path": wav})
            self.assertIn("input_i", r_audit)
            self.assertEqual(cli_main(["beat-sync", wav]), 0)


if __name__ == "__main__":
    unittest.main()
