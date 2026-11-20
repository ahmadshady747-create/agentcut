"""Comprehensive verification suite for AgentCut v2.5.0 (Human-Feel Director & Cinema Engine)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.cinematography import (
    CINEMA_LOOKS,
    build_organic_zoompan_filter,
    choose_narrative_camera_move,
    get_cinema_look,
    list_cinema_looks,
)
from agentcut.director import analyze_narrative_beat, direct_from_script, humanize_brief
from agentcut.qc import audit_editorial_craft, audit_video_quality
from agentcut.render import produce_explainer
from agentcut.server import TOOLS, call_tool
from agentcut.soundscape import (
    generate_room_tone,
    list_sfx_kinds,
    plan_jl_cut_audio,
    synthesize_sfx,
)

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


class TestCinematographyV25(unittest.TestCase):
    def test_six_cinema_looks_and_organic_zoompan(self):
        looks = list_cinema_looks()
        self.assertGreaterEqual(len(looks), 6)
        self.assertIn("chiaroscuro_doc", CINEMA_LOOKS)
        look = get_cinema_look("kodak_warm")
        self.assertIn("colorbalance=", look["vf"])

        zp = build_organic_zoompan_filter(
            "slow_push_in", duration=3.0, width=720, height=1280, fps=30,
            focal_x=0.35, focal_y=0.40, handheld=True,
        )
        self.assertIn("zoompan=", zp)
        self.assertIn("sin(", zp)
        self.assertIn("s=720x1280", zp)

    def test_narrative_camera_never_repeats_consecutively(self):
        prev = None
        for idx, intent in enumerate(["hook", "question", "revelation", "quote", "reflection", "narrative", "narrative"]):
            mv = choose_narrative_camera_move(intent, beat_index=idx, prev_move=prev, duration=3.0)
            self.assertNotEqual(mv, prev)
            prev = mv


class TestSoundscapeV25(unittest.TestCase):
    def test_synthesize_all_sfx_and_room_tone(self):
        self.assertEqual(len(list_sfx_kinds()), 5)
        with tempfile.TemporaryDirectory() as td:
            for kind in ("chapter_boom", "soft_whoosh", "card_tick", "shimmer_reveal", "tension_riser"):
                out = str(Path(td) / f"{kind}.wav")
                ok, info = synthesize_sfx(kind, out)
                self.assertTrue(ok, info)
                self.assertGreater(info["size_bytes"], 1024)

            rt = str(Path(td) / "room.wav")
            ok_r, info_r = generate_room_tone(rt, duration=2.0, preset="warm_studio")
            self.assertTrue(ok_r, info_r)
            self.assertGreater(info_r["size_bytes"], 1024)

    def test_jl_cut_planner(self):
        scenes = [{"id": "s1", "duration": 3.0}, {"id": "s2", "duration": 4.0}]
        plan = plan_jl_cut_audio(scenes, mode="j_cut", overlap_sec=0.25)
        self.assertEqual(plan[0]["audio_start"], 0.0)
        self.assertAlmostEqual(plan[1]["audio_start"], 2.75, places=2)
        self.assertEqual(plan[1]["cut_style"], "j_cut")


class TestDirectorAndCraftAuditV25(unittest.TestCase):
    def test_arabic_narrative_intent_classification(self):
        self.assertEqual(analyze_narrative_beat("تخيل أن الزمن توقف فجأة", 0, 4, True)["intent"], "hook")
        self.assertEqual(analyze_narrative_beat("لكن لماذا ننسى التفاصيل؟", 1, 4, False)["intent"], "question")
        self.assertEqual(analyze_narrative_beat("«الحكمة تبدأ من التأمل العميق»", 2, 4, False)["intent"], "quote")
        self.assertEqual(analyze_narrative_beat("أكثر من 85٪ من الناس يمرون بهذه اللحظة", 2, 4, False)["intent"], "data_point")

    def test_humanize_improves_robotic_craft_score(self):
        # Deliberately robotic brief: identical durations, uniform transitions, duplicate moves, card spam, no look/sound
        robotic = {
            "title": "Robotic Brief",
            "style": "editorial",
            "scenes": [
                {"id": "s1", "text": "تخيل عالماً بلا ذاكرة", "duration": 3.0, "camera_move": "slow_push_in", "highlight_card": {"title": "A"}},
                {"id": "s2", "text": "لماذا يهم هذا الأمر؟", "duration": 3.0, "transition": "crossfade", "camera_move": "slow_push_in", "highlight_card": {"title": "B"}},
                {"id": "s3", "text": "في الحقيقة 90٪ من قراراتنا عاطفية", "duration": 3.0, "transition": "crossfade", "camera_move": "slow_push_in", "highlight_card": {"title": "C"}},
                {"id": "s4", "text": "ربما المعنى في التأمل", "duration": 3.0, "transition": "crossfade", "camera_move": "slow_push_in"},
            ],
        }
        before = audit_editorial_craft(robotic)
        self.assertLess(before["score"], 65)

        raw_script = (
            "تخيل أنك تستيقظ في عالم بلا خرائط.\n\n"
            "لماذا نبحث دائماً عن اليقين في عالم متغير؟\n\n"
            "في الحقيقة، أكثر من 80٪ من القرارات الكبرى تُصنع في لحظات الصمت!\n\n"
            "«المعنى لا يُعطى جاهزاً، بل يُبنى بالتأمل»\n\n"
            "في النهاية، الزمن هو المحرر الحقيقي لكل قصة."
        )
        directed = direct_from_script(raw_script, title="فلسفة الزمن", cinema_look="chiaroscuro_doc")
        after = audit_editorial_craft(directed)
        self.assertGreaterEqual(after["score"], 85)
        self.assertTrue(after["passed"])
        self.assertEqual(directed["cinema_look"], "chiaroscuro_doc")
        self.assertIn("soundscape", directed)
        self.assertGreaterEqual(len(directed["director_notes"]), 4)


class TestHumanizedEditorialRenderV25(unittest.TestCase):
    def test_end_to_end_humanized_cinema_editorial_render(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "s1.wav"
            wav2 = Path(td) / "s2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=340:duration=2.4", str(wav)],
                    check=True, timeout=20,
                )
            srt1 = Path(td) / "s1.srt"
            srt2 = Path(td) / "s2.srt"
            srt1.write_text("1\n00:00:00,000 --> 00:00:02,400\nتخيل عالماً يتحرك بإيقاع سينمائي\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,000 --> 00:00:02,400\nلماذا يبدو المونتاج البشري أكثر حياة؟\n", encoding="utf-8")

            brief = {
                "title": "Humanized Cinema Proof",
                "style": "editorial",
                "humanize": True,
                "cinema_look": "chiaroscuro_doc",
                "aspect": "16:9",
                "parallel": True,
                "render_profile": "proxy",
                "scenes": [
                    {
                        "id": "sc1",
                        "text": "تخيل عالماً يتحرك بإيقاع سينمائي",
                        "narration": str(wav1),
                        "captions": str(srt1),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": str(TEST_MEDIA / "poster.png")},
                            {"start": 1.2, "end": 2.4, "broll": str(TEST_MEDIA / "board.png")},
                        ],
                    },
                    {
                        "id": "sc2",
                        "text": "لماذا يبدو المونتاج البشري أكثر حياة؟",
                        "narration": str(wav2),
                        "captions": str(srt2),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": str(TEST_MEDIA / "board.png")},
                            {"start": 1.2, "end": 2.4, "broll": str(TEST_MEDIA / "poster.png")},
                        ],
                    },
                ],
            }
            out_mp4 = str(Path(td) / "humanized.mp4")
            ok, info = produce_explainer(brief, out_mp4)
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out_mp4))
            qc = audit_video_quality(out_mp4)
            self.assertTrue(qc["passed"], qc)


class TestMcpAndCliV25(unittest.TestCase):
    def test_49_mcp_tools_and_v25_cli(self):
        self.assertEqual(__version__, "3.2.0")
        self.assertEqual(len(TOOLS), 62)

        r1 = call_tool("list_cinema_looks", {})
        self.assertFalse(r1.get("isError"))
        self.assertGreaterEqual(len(json.loads(r1["content"][0]["text"])["looks"]), 6)

        r2 = call_tool("direct_from_script", {"script": "تخيل البداية.\nلماذا نكتب؟\n«الفكرة تبقى»"})
        self.assertFalse(r2.get("isError"))
        directed = json.loads(r2["content"][0]["text"])

        r3 = call_tool("audit_editorial_craft", {"brief": directed})
        self.assertFalse(r3.get("isError"))
        self.assertTrue(json.loads(r3["content"][0]["text"])["passed"])

        self.assertEqual(cli_main(["looks"]), 0)
        self.assertEqual(cli_main(["direct", "تخيل البداية.\nلماذا نكتب؟"]), 0)


if __name__ == "__main__":
    unittest.main()
