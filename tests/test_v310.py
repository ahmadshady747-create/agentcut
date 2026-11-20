"""Comprehensive verification suite for AgentCut v3.1.0 (Model-Agnostic Autopilot & Art Director)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.art_director import STYLE_BIBLES, generate_visual_prompts, list_style_bibles
from agentcut.autopilot import autopilot_produce
from agentcut.brief_repair import repair_brief
from agentcut.script_polisher import clean_spoken_text, generate_srt_for_scene, polish_script_scenes
from agentcut.server import TOOLS, call_tool

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


class TestBriefRepairV31(unittest.TestCase):
    def test_repairs_messy_weak_llm_json_string(self):
        messy = '''```json
        {
          "title": "فلسفة الوقت",
          "ratio": "vertical",
          "look": "chiaroscuro",
          "profile": "lowspec",
          "scenes": [
            {
              "script": "تخيل أن الوقت توقف",
              "duration": "3.5s",
              "card": "الزمن",
              "keywords": ["forbidden_in_editorial"],
            },
            "لماذا نركض دائماً؟",
          ],
        }
        ```'''
        repaired, repairs = repair_brief(messy)
        self.assertEqual(repaired["aspect"], "9:16")
        self.assertEqual(repaired["cinema_look"], "chiaroscuro_doc")
        self.assertEqual(repaired["render_profile"], "low_spec")
        self.assertEqual(repaired["style"], "editorial")
        self.assertEqual(len(repaired["scenes"]), 2)
        self.assertEqual(repaired["scenes"][0]["duration"], 3.5)
        self.assertEqual(repaired["scenes"][0]["highlight_card"]["title"], "الزمن")
        self.assertNotIn("keywords", repaired["scenes"][0])
        self.assertEqual(repaired["scenes"][1]["text"], "لماذا نركض دائماً؟")
        self.assertGreaterEqual(len(repairs), 5)


class TestArtDirectorV31(unittest.TestCase):
    def test_style_bibles_and_consistent_prompts(self):
        self.assertEqual(len(list_style_bibles()), 6)
        self.assertIn("chiaroscuro_doc", STYLE_BIBLES)
        brief = {
            "aspect": "9:16",
            "cinema_look": "chiaroscuro_doc",
            "scenes": [
                {"id": "s1", "text": "الزمن والذاكرة يصنعان تاريخ الإنسان"},
                {"id": "s2", "text": "في الصمت نجد الحقيقة العميقة"},
            ],
        }
        plan = generate_visual_prompts(brief)
        self.assertEqual(plan["style_bible"], "chiaroscuro_doc")
        self.assertEqual(plan["total_prompts"], 2)
        self.assertNotEqual(plan["prompts"][0]["shot_scale"], plan["prompts"][1]["shot_scale"])
        self.assertIn("hourglass", plan["prompts"][0]["prompt"])
        self.assertIn("no watermark", plan["prompts"][0]["negative_prompt"])


class TestScriptPolisherV31(unittest.TestCase):
    def test_clean_split_and_auto_srt(self):
        cleaned = clean_spoken_text("## **عنوان** - نص مع https://example.com رابط")
        self.assertNotIn("**", cleaned)
        self.assertNotIn("##", cleaned)
        self.assertNotIn("https://", cleaned)

        long_text = " ".join(["كلمة"] * 40)
        polished, stats = polish_script_scenes({"scenes": [{"id": "s1", "text": long_text}]}, max_words_per_scene=15)
        self.assertGreaterEqual(len(polished["scenes"]), 3)
        self.assertGreaterEqual(stats["scenes_split"], 2)

        with tempfile.TemporaryDirectory() as td:
            srt_path = str(Path(td) / "auto.srt")
            generate_srt_for_scene("تخيل عالماً هادئاً. لماذا نتعجل دائماً؟", 4.0, srt_path)
            content = Path(srt_path).read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 -->", content)
            self.assertIn("00:00:04,000", content)


class TestAutopilotV31(unittest.TestCase):
    def test_autopilot_blueprint_from_raw_arabic_essay(self):
        essay = (
            "**مقدمة:** تخيل أنك تعيش في مدينة بلا ساعات.\n\n"
            "لماذا نربط قيمتنا دائماً بالسرعة؟\n\n"
            "«التأمل هو البوابة الحقيقية للفهم العميق»"
        )
        ok, res = autopilot_produce(essay, cinema_look="kodak_warm", aspect="9:16")
        self.assertTrue(ok)
        self.assertEqual(res["mode"], "blueprint")
        self.assertTrue(res["craft_audit"]["passed"])
        self.assertEqual(res["visual_prompts"]["style_bible"], "kodak_warm")
        self.assertGreaterEqual(len(res["brief"]["scenes"]), 3)

    def test_autopilot_end_to_end_render_from_messy_json_without_srt(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "v1.wav"
            wav2 = Path(td) / "v2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=330:duration=2.2", str(wav)],
                    check=True, timeout=20,
                )
            # Weak-LLM style messy JSON: wrong keys, missing SRT captions, typo look
            messy_json = json.dumps({
                "title": "تجربة الطيار الآلي",
                "ratio": "landscape",
                "look": "chiaroscuro",
                "scenes": [
                    {
                        "script": "**تخيل** أن المحرك يصلح كل شيء تلقائياً",
                        "audio": str(wav1),
                        "image": str(TEST_MEDIA / "poster.png"),
                    },
                    {
                        "script": "لماذا نحتاج نموذجاً ضخماً للمونتاج؟",
                        "audio": str(wav2),
                        "image": str(TEST_MEDIA / "board.png"),
                    },
                ],
            }, ensure_ascii=False)
            out_mp4 = str(Path(td) / "autopilot_out.mp4")
            ok, res = autopilot_produce(f"```json\n{messy_json}\n```", output_path=out_mp4, profile="proxy")
            self.assertTrue(ok, res)
            self.assertEqual(res["mode"], "rendered")
            self.assertTrue(os.path.isfile(out_mp4))
            self.assertEqual(res["polish_stats"]["srt_generated"], 2)
            self.assertTrue(res["auto_heal"]["qc"]["passed"], res["auto_heal"]["qc"])


class TestMcpAndCliV31(unittest.TestCase):
    def test_60_mcp_tools_and_v31_cli(self):
        self.assertEqual(__version__, "3.2.0")
        self.assertEqual(len(TOOLS), 62)

        r1 = call_tool("list_style_bibles", {})
        self.assertFalse(r1.get("isError"))
        self.assertEqual(len(json.loads(r1["content"][0]["text"])["style_bibles"]), 6)

        r2 = call_tool("autopilot_produce", {"input": "تخيل البداية.\nلماذا نفكر؟"})
        self.assertFalse(r2.get("isError"))
        self.assertEqual(json.loads(r2["content"][0]["text"])["mode"], "blueprint")

        self.assertEqual(cli_main(["autopilot", "تخيل البداية.\nلماذا نفكر؟"]), 0)


if __name__ == "__main__":
    unittest.main()
