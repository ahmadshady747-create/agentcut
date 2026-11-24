"""Comprehensive tests for AgentCut v3.4.0 browser-only production engine."""
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import flat_reels, icons, server, soundscape  # noqa: E402
from agentcut.__main__ import main as cli_main  # noqa: E402


def _sine_wav(path, duration=1.2, freq=440):
    rate = 48000
    frames = int(duration * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            v = int(12000 * math.sin(2 * math.pi * freq * i / rate))
            data += struct.pack("<h", v)
        wf.writeframes(bytes(data))


class TestIconsV340(unittest.TestCase):
    def test_at_least_65_icons_and_all_valid_svg(self):
        all_names = icons.list_icons()
        self.assertGreaterEqual(len(all_names), 65)
        for name in all_names:
            svg = icons.icon_svg(name, 96)
            self.assertIn('viewBox="0 0 64 64"', svg, name)
            self.assertIn('class="ambient-icon"', svg, name)

    def test_search_icons(self):
        hits = icons.search_icons("brain")
        self.assertEqual(hits, ["brain"])
        self.assertGreaterEqual(len(icons.search_icons("")), 65)


class TestFormatsAndPalettesV340(unittest.TestCase):
    def test_four_palettes_complete(self):
        self.assertEqual(set(flat_reels.PALETTES),
                         {"cream", "dark", "editorial", "midnight"})
        req = {"paper", "card", "ink", "accent", "blue", "muted", "marker", "line"}
        for name, pal in flat_reels.PALETTES.items():
            self.assertEqual(set(pal), req, name)

    def test_resolve_dimensions_formats(self):
        self.assertEqual(flat_reels.resolve_dimensions({"format": "vertical"}),
                         (720, 1280, "vertical"))
        self.assertEqual(flat_reels.resolve_dimensions({"format": "landscape"}),
                         (1280, 720, "landscape"))
        self.assertEqual(flat_reels.resolve_dimensions({"format": "explainer"}),
                         (1280, 720, "landscape"))
        self.assertEqual(flat_reels.resolve_dimensions({"format": "square"}),
                         (1080, 1080, "square"))

    def test_landscape_explainer_html_builds_with_all_new_scenes(self):
        spec = {
            "id": "long-explainer",
            "format": "landscape",
            "palette": "midnight",
            "motion": "energetic",
            "transition": "slide_up",
            "scenes": [
                {"id": "ch1", "type": "chapter", "narration": "a.wav",
                 "text": "الفصل الأول: محرك المتصفح.",
                 "badge": "الفصل 01", "title": "محرك المتصفح",
                 "subtitle": "كل الإطارات في Chromium"},
                {"id": "cd1", "type": "code", "narration": "b.wav",
                 "text": "دالة setTime تحكم كل إطار.",
                 "title": "حتمية الزمن", "language": "javascript",
                 "code": "window.setTime = (t) => {\n  render(t);\n};",
                 "highlight_lines": [2]},
                {"id": "sp1", "type": "split", "narration": "c.wav",
                 "text": "سرعة أعلى وجودة أنظف.",
                 "title": "لماذا المتصفح؟", "icon": "rocket",
                 "body": "أداء فوري",
                 "bullets": ["ريندر في دقائق", "بلا توفو عربي", "حركة مستمرة"]},
                {"id": "ck1", "type": "checklist", "narration": "d.wav",
                 "text": "كل معايير القبول متحققة.",
                 "title": "قائمة التحقق",
                 "items": ["حركة مستمرة", "صوت طبيعي", "-14 LUFS"]},
            ],
        }
        ok, errs = flat_reels.validate_reel_spec(spec)
        self.assertTrue(ok, errs)
        timings = [
            {"id": sc["id"], "start": i * 2.5, "duration": 2.5,
             "words": flat_reels.even_word_timings(
                 sc["text"].split(), i * 2.5, (i + 1) * 2.5)}
            for i, sc in enumerate(spec["scenes"])
        ]
        page = flat_reels.build_video_html(spec, timings, embed_font=False)
        self.assertIn('class="fmt-landscape"', page)
        self.assertIn("width:1280px;height:720px", page)
        self.assertIn("MOTION_SCALE = 1.45", page)
        self.assertIn('DEFAULT_TRANS = "slide_up"', page)
        self.assertIn('class="chapter-stage"', page)
        self.assertIn('class="code-card anim"', page)
        self.assertIn('class="split-grid"', page)
        self.assertIn('class="checklist"', page)
        self.assertIn('class="bg-particle"', page)
        self.assertNotIn("@keyframes", page)
        self.assertNotIn("transition:", page)
        self.assertNotIn("animation:", page)


class TestSoundscapeAndAutoMixV340(unittest.TestCase):
    def test_nine_sfx_and_synthesis(self):
        self.assertGreaterEqual(len(soundscape.SFX_CATALOG), 15)
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ("pop_in", "counter_tick", "bell_chime", "bass_drop"):
                out = os.path.join(tmp, f"{kind}.wav")
                ok, info = soundscape.synthesize_sfx(kind, out)
                self.assertTrue(ok, info)
                self.assertTrue(os.path.isfile(out))

    def test_plan_scene_sfx_and_full_audio_mix(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "s1.wav")
            b = os.path.join(tmp, "s2.wav")
            _sine_wav(a, 1.2, 440)
            _sine_wav(b, 1.2, 520)
            spec = {
                "id": "mix-test",
                "auto_sfx": True,
                "room_tone": "warm_studio",
                "music_bed": {"preset": "tech_pulse", "volume_db": -22.0},
                "scenes": [
                    {"id": "s1", "type": "intro", "narration": "s1.wav",
                     "text": "مقدمة سريعة للاختبار."},
                    {"id": "s2", "type": "conclusion", "narration": "s2.wav",
                     "text": "خاتمة واضحة جدا."},
                ],
            }
            timings, total, errs = flat_reels.compute_scene_timings(spec, tmp)
            self.assertEqual(errs, [])
            cues = soundscape.plan_scene_sfx(spec, timings)
            self.assertEqual([c["kind"] for c in cues],
                             ["soft_whoosh", "bell_chime"])
            ok, info = flat_reels.build_audio_mix(spec, timings, tmp, tmp)
            self.assertTrue(ok, info)
            self.assertTrue(info.get("ducked"))
            self.assertEqual(info.get("sfx_cues"), 2)
            self.assertAlmostEqual(info["duration"], total, places=1)


class TestCliAndMcpV340(unittest.TestCase):
    def test_version_and_mcp_dispatch(self):
        self.assertTrue(agentcut.__version__.startswith("3.4."))
        res = server.dispatch_tool("list_design_system", {})
        self.assertTrue(res["version"].startswith("3.4."))
        self.assertGreaterEqual(len(res["icons"]), 65)
        self.assertGreaterEqual(len(res["scene_types"]), 18)
        self.assertGreaterEqual(len(res["sfx"]), 15)
        self.assertEqual(cli_main(["version"]), 0)
        self.assertEqual(cli_main(["icons", "rocket"]), 0)


if __name__ == "__main__":
    unittest.main()
