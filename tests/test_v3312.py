"""Tests for AgentCut v3.3.12 — Cycle 11: Character-Level LCS Alignment & Optional Deepgram Bridge."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest import mock

from agentcut.__main__ import main as cli_main
from agentcut.bundle import align_bundle_word_timings, bundle_to_editorial_brief
from agentcut.deepgram_align import (
    align_scene_audio,
    align_script_with_timings,
    normalize_for_alignment,
    parse_deepgram_response,
    transcribe_audio_deepgram,
)
from agentcut.render import _validate_scene_word_timings
from agentcut.server import TOOLS, call_tool


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestV3312LCSAndDeepgram(unittest.TestCase):
    def test_arabic_normalization_handles_tashkeel_and_variants(self):
        self.assertEqual(normalize_for_alignment("أَطْلَقَتْ،"), "اطلقت")
        self.assertEqual(normalize_for_alignment("شَرِكَةٌ"), "شركه")
        self.assertEqual(normalize_for_alignment("مُوسَى"), "موسي")

    def test_lcs_preserves_original_arabic_spelling_and_passes_render_validation(self):
        script = "أَطْلَقَتْ شَرِكَةُ AgentCut إِصْدَاراً جَدِيداً لِلْمُونْتَاجِ الذَّكِيِّ"
        # Distorted Whisper ASR output (dropped tashkeel, wrong hamza/teh marbuta, split English)
        asr_words = [
            {"word": "اطلقت", "start": 0.05, "end": 0.55, "confidence": 0.94},
            {"word": "شركه", "start": 0.58, "end": 1.02, "confidence": 0.91},
            {"word": "Agent", "start": 1.05, "end": 1.35, "confidence": 0.88},
            {"word": "Cut", "start": 1.36, "end": 1.65, "confidence": 0.89},
            {"word": "اصدارا", "start": 1.70, "end": 2.25, "confidence": 0.93},
            {"word": "جديدا", "start": 2.28, "end": 2.75, "confidence": 0.92},
            {"word": "للمونتاج", "start": 2.80, "end": 3.45, "confidence": 0.87},
            {"word": "الذكي", "start": 3.48, "end": 4.00, "confidence": 0.90},
        ]
        res = align_script_with_timings(script, asr_words, audio_duration=4.2)
        aligned = res["word_timings"]

        self.assertEqual([w["word"] for w in aligned], script.split())
        self.assertTrue(res["stats"]["spelling_preserved"])
        self.assertEqual(res["stats"]["total_words"], 7)
        self.assertEqual(res["stats"]["interpolated_words"], 0)
        self.assertEqual(res["stats"]["low_confidence_count"], 0)

        # Strict monotonic non-overlapping check
        for i in range(len(aligned) - 1):
            self.assertLess(aligned[i]["start"], aligned[i]["end"])
            self.assertLessEqual(aligned[i]["end"], aligned[i + 1]["start"])

        # Must pass render.py validator
        cleaned, err = _validate_scene_word_timings(aligned, "s1", 4.2)
        self.assertIsNone(err)
        self.assertEqual(len(cleaned), 7)

    def test_unmatched_interpolation_and_low_confidence_flagging(self):
        script = "الذكاء الاصطناعي يغير صناعة الفيديو بسرعة"
        asr_words = [
            {"word": "الذكاء", "start": 0.0, "end": 0.5, "confidence": 0.95},
            {"word": "الاصطناعي", "start": 0.55, "end": 1.1, "confidence": 0.92},
            # "يغير" skipped by ASR
            {"word": "صناعة", "start": 1.8, "end": 2.3, "confidence": 0.41},  # low confidence
            {"word": "الفيديو", "start": 2.35, "end": 2.9, "confidence": 0.90},
            {"word": "بسرعة", "start": 2.95, "end": 3.5, "confidence": 0.88},
        ]
        res = align_script_with_timings(script, asr_words, low_confidence_threshold=0.72, audio_duration=3.6)
        wt = res["word_timings"]
        self.assertEqual([w["word"] for w in wt], script.split())

        # Word index 2 ("يغير") should be interpolated between 1.1 and 1.8
        self.assertTrue(wt[2]["interpolated"])
        self.assertTrue(wt[2]["low_confidence"])
        self.assertGreaterEqual(wt[2]["start"], 1.1)
        self.assertLessEqual(wt[2]["end"], 1.8)

        # Word index 3 ("صناعة") should be flagged for low ASR confidence
        self.assertFalse(wt[3]["interpolated"])
        self.assertTrue(wt[3]["low_confidence"])

        flagged_words = [x["word"] for x in res["low_confidence_words"]]
        self.assertIn("يغير", flagged_words)
        self.assertIn("صناعة", flagged_words)

    def test_deepgram_optional_bridge_urllib_and_whisper_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "narration.wav")
            with open(wav, "wb") as f:
                f.write(b"RIFF\x24\x00\x00\x00WAVEfmt ")

            # Missing API key raises clear error noting Whisper is the free default
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DEEPGRAM_API_KEY", None)
                with self.assertRaises(ValueError) as ctx:
                    transcribe_audio_deepgram(wav)
                self.assertIn("whisper", str(ctx.exception).lower())

            dg_payload = {
                "metadata": {"duration": 2.1},
                "results": {
                    "channels": [{
                        "alternatives": [{
                            "transcript": "مرحبا بالعالم اليوم",
                            "words": [
                                {"word": "مرحبا", "punctuated_word": "مرحبا", "start": 0.1, "end": 0.6, "confidence": 0.96},
                                {"word": "بالعالم", "punctuated_word": "بالعالم", "start": 0.65, "end": 1.3, "confidence": 0.62},
                                {"word": "اليوم", "punctuated_word": "اليوم", "start": 1.35, "end": 1.9, "confidence": 0.93},
                            ],
                        }]
                    }]
                },
            }
            parsed = parse_deepgram_response(dg_payload)
            self.assertEqual(len(parsed["words"]), 3)
            self.assertEqual(parsed["duration"], 2.1)

            with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(dg_payload)) as m_open:
                res = align_scene_audio(
                    script_text="مَرْحَباً بِالْعَالَمِ الْيَوْمَ",
                    audio_path=wav,
                    provider="deepgram",
                    api_key="dg_test_key_123",
                    language="ar",
                )
                self.assertEqual(m_open.call_count, 1)
                req_obj = m_open.call_args[0][0]
                self.assertEqual(req_obj.get_header("Authorization"), "Token dg_test_key_123")
                self.assertIn("model=nova-2", req_obj.full_url)
                self.assertIn("language=ar", req_obj.full_url)

            self.assertEqual(res["provider"], "deepgram")
            self.assertEqual([w["word"] for w in res["word_timings"]], ["مَرْحَباً", "بِالْعَالَمِ", "الْيَوْمَ"])
            self.assertEqual(res["stats"]["low_confidence_count"], 1)
            self.assertEqual(res["low_confidence_words"][0]["word"], "بِالْعَالَمِ")

    def test_bundle_bridge_preserves_whisper_default_and_aligns_script(self):
        bundle = {
            "title": "اختبار التوقيت",
            "aspect": "9:16",
            "scenes": [
                {
                    "id": "s1",
                    "text": "أَهْلاً بِكُمْ فِي التَّجْرِبَةِ",
                    "duration": 3.0,
                    "word_timings": [
                        {"word": "اهلا", "start": 0.0, "end": 0.6, "confidence": 0.95},
                        {"word": "بكم", "start": 0.65, "end": 1.2, "confidence": 0.94},
                        {"word": "في", "start": 1.25, "end": 1.5, "confidence": 0.91},
                        {"word": "التجربه", "start": 1.55, "end": 2.4, "confidence": 0.89},
                    ],
                }
            ],
        }
        aligned_bundle, summary = align_bundle_word_timings(bundle)
        self.assertEqual(summary["provider"], "whisper")
        self.assertEqual(summary["total_words"], 4)
        self.assertEqual(
            [w["word"] for w in aligned_bundle["scenes"][0]["word_timings"]],
            ["أَهْلاً", "بِكُمْ", "فِي", "التَّجْرِبَةِ"],
        )

        brief = bundle_to_editorial_brief(bundle)
        self.assertEqual(
            [w["word"] for w in brief["scenes"][0]["word_timings"]],
            ["أَهْلاً", "بِكُمْ", "فِي", "التَّجْرِبَةِ"],
        )

    def test_mcp_tool_72_and_cli_command(self):
        names = [t["name"] for t in TOOLS]
        self.assertIn("align_word_timings", names)
        self.assertEqual(len(TOOLS), 73)

        resp = call_tool("align_word_timings", {
            "script_text": "إِطْلَاقٌ جَدِيدٌ",
            "asr_words": [
                {"word": "اطلاق", "start": 0.0, "end": 0.6, "confidence": 0.92},
                {"word": "جديد", "start": 0.7, "end": 1.3, "confidence": 0.90},
            ],
        })
        self.assertFalse(resp.get("isError", False))
        payload = json.loads(resp["content"][0]["text"])
        self.assertEqual([w["word"] for w in payload["word_timings"]], ["إِطْلَاقٌ", "جَدِيدٌ"])
        self.assertEqual(payload["provider"], "whisper")

        with tempfile.TemporaryDirectory() as tmp:
            words_json = os.path.join(tmp, "words.json")
            with open(words_json, "w", encoding="utf-8") as f:
                json.dump([
                    {"word": "مرحبا", "start": 0.0, "end": 0.5, "confidence": 0.95},
                    {"word": "بكم", "start": 0.6, "end": 1.1, "confidence": 0.91},
                ], f)
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli_main(["align-timings", "مَرْحَباً بِكُمْ", words_json])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual([w["word"] for w in out["word_timings"]], ["مَرْحَباً", "بِكُمْ"])


if __name__ == "__main__":
    unittest.main()
