"""Unit tests for AgentCut Phase 1: TTS and Procedural Audio Engine."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.tts import (
    detect_voice,
    generate_ambient_audio,
    is_arabic,
    list_voices,
    synthesize_speech,
)
from agentcut.render import produce_explainer
from agentcut.validate import probe_media


class TestTTS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-tts-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_arabic_detection(self):
        self.assertTrue(is_arabic("مرحبا بكم"))
        self.assertTrue(is_arabic("Hello مرحبا"))
        self.assertFalse(is_arabic("Hello world"))
        self.assertFalse(is_arabic("12345"))

    def test_detect_voice(self):
        self.assertEqual(detect_voice("مرحبا بالعالم"), "ar-SA-HamedNeural")
        self.assertEqual(detect_voice("Welcome to AgentCut"), "en-US-ChristopherNeural")
        self.assertEqual(detect_voice("مرحبا", "ar-EG-SalmaNeural"), "ar-EG-SalmaNeural")

    def test_empty_text_raises(self):
        audio_out = os.path.join(self.tmp, "empty.mp3")
        with self.assertRaises(ValueError):
            synthesize_speech("", audio_out)

    def test_list_voices_arabic(self):
        voices = list_voices("ar")
        self.assertGreater(len(voices), 0)
        first = voices[0]
        self.assertIn("name", first)
        self.assertIn("locale", first)
        self.assertTrue(first["locale"].startswith("ar"))

    def test_generate_ambient_audio(self):
        amb_path = os.path.join(self.tmp, "ambient.mp3")
        res = generate_ambient_audio(amb_path, duration=2.0)
        self.assertTrue(os.path.isfile(res))
        probe = probe_media(res)
        self.assertIsNotNone(probe)
        self.assertTrue(probe.get("has_audio"))
        self.assertAlmostEqual(probe["duration"], 2.0, delta=0.5)

    def test_speech_synthesis(self):
        audio_path = os.path.join(self.tmp, "speech.mp3")
        srt_path = os.path.join(self.tmp, "speech.srt")
        res = synthesize_speech("اختبار التوليد الصوتي", audio_path, srt_path)
        self.assertTrue(os.path.isfile(audio_path))
        self.assertTrue(os.path.isfile(srt_path))
        self.assertGreater(res["duration"], 0.5)
        self.assertGreater(res["cues_count"], 0)

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("-->", content)
        self.assertIn("اختبار", content)

    def test_produce_explainer_with_narration_text(self):
        # Full end-to-end: autonomous explainer from text alone
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        poster_bg = os.path.join(base_dir, "data", "test_media", "poster.png")
        if not os.path.isfile(poster_bg):
            self.skipTest("poster.png not found")

        out_mp4 = os.path.join(self.tmp, "auto_explainer.mp4")
        brief = {
            "id": "auto_tts_brief",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": poster_bg,
            "title": "شرح تجريبي",
            "music": "ambient",
            "sfx_density": "rich",
            "scenes": [
                {
                    "id": "s1",
                    "title": "مقدمة",
                    "narration_text": "مرحباً بكم في هذا الفيديو الذي تم إنتاجه بالكامل تلقائياً.",
                }
            ],
        }

        ok, info = produce_explainer(brief, out_mp4)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out_mp4))
        self.assertGreater(info["duration"], 1.0)
        self.assertEqual(len(info["scenes"]), 1)
        self.assertTrue(os.path.isfile(info["scenes"][0]["narration"]))
        self.assertTrue(os.path.isfile(info["scenes"][0]["captions"]))
        self.assertGreater(len(info["review_frames"]), 0)


if __name__ == "__main__":
    unittest.main()
