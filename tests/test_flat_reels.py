"""Tests for the flat-reels style (browser-rendered vertical reels).

Pure tests: no browser needed. Browser-backed behaviour lives in
tests/test_browser_render.py (gated on Playwright + Chromium).
"""
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut import flat_reels  # noqa: E402


def _spec(**overrides):
    spec = {
        "id": "test-reel",
        "title": "ليه بنحس بالبرد في الشتاء",
        "series_tag": "طب ازاي؟",
        "palette": "cream",
        "width": 720,
        "height": 1280,
        "fps": 30,
        "scenes": [
            {"id": "s1", "type": "intro", "layout": "body",
             "narration": "s1.wav", "words": "s1.words.json",
             "text": "جسمك درجة حرارته 37 طول السنة.",
             "title": "ليه بنحس بالبرد في الشتاء؟",
             "icon": "thermometer", "badge": "37°"},
            {"id": "s2", "type": "question", "layout": "body",
             "narration": "s2.wav", "words": "s2.words.json",
             "text": "طب ازاي؟"},
        ],
    }
    spec.update(overrides)
    return spec


def _sine_wav(path, duration=1.0, freq=440):
    # Pure-Python sine WAV (same helper as test_v164) — no ffmpeg
    # dependency for a test fixture.
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


class TestPalettes(unittest.TestCase):
    def test_cream_palette_documented_hexes(self):
        cream = flat_reels.PALETTES["cream"]
        self.assertEqual(cream["paper"], "#F7F0DC")
        self.assertEqual(cream["ink"], "#221E17")
        self.assertEqual(cream["accent"], "#E4572E")
        self.assertEqual(cream["blue"], "#2E86AB")
        self.assertEqual(cream["card"], "#FFFDF5")
        self.assertEqual(cream["marker"], "#FFD23F")

    def test_dark_palette_is_documented_v1(self):
        dark = flat_reels.PALETTES["dark"]
        self.assertEqual(dark["paper"], "#0C1014")
        self.assertEqual(dark["card"], "#161C22")
        self.assertEqual(dark["ink"], "#F4F3EE")
        self.assertEqual(dark["accent"], "#27B8C7")
        self.assertEqual(dark["blue"], "#5978FF")

    def test_every_palette_has_all_keys(self):
        keys = {"paper", "card", "ink", "accent", "blue",
                "muted", "marker", "line"}
        for name, pal in flat_reels.PALETTES.items():
            self.assertEqual(set(pal), keys, f"palette {name}")


class TestIcons(unittest.TestCase):
    def test_all_icons_render_svg(self):
        for name in flat_reels.ICONS:
            svg = flat_reels.icon_svg(name, 64)
            self.assertTrue(svg.startswith("<svg"),
                            f"icon {name}")
            self.assertIn('viewBox="0 0 64 64"', svg)

    def test_unknown_icon_falls_back_to_question_mark(self):
        svg = flat_reels.icon_svg("no-such-icon", 64)
        self.assertIn("؟", svg)

    def test_reel_icons_all_exist(self):
        needed = {"thermometer", "snowflake", "flame", "factory",
                  "vessel", "droplet", "bolt", "hand", "zigzag",
                  "check", "body", "wind", "door"}
        for name in needed:
            self.assertIn(name, flat_reels.ICONS, f"missing icon {name}")


class TestTextHelpers(unittest.TestCase):
    def test_split_sentences_arabic_punctuation(self):
        parts = flat_reels.split_sentences(
            "جملة أولى. جملة ثانية؟ جملة ثالثة!")
        self.assertEqual(parts, ["جملة أولى.", "جملة ثانية؟",
                                 "جملة ثالثة!"])

    def test_even_word_timings_cover_range(self):
        words = ["أ", "ب", "ج"]
        timed = flat_reels.even_word_timings(words, 2.0, 5.0)
        self.assertEqual(len(timed), 3)
        self.assertEqual(timed[0]["start"], 2.0)
        self.assertEqual(timed[-1]["end"], 5.0)
        for a, b in zip(timed, timed[1:]):
            self.assertEqual(a["end"], b["start"])

    def test_load_words_falls_back_without_file(self):
        timed = flat_reels.load_words(None, "كلمة أولى ثانية",
                                      1.0, 4.0)
        self.assertEqual([t["word"] for t in timed],
                         ["كلمة", "أولى", "ثانية"])
        self.assertEqual(timed[0]["start"], 1.0)
        self.assertEqual(timed[-1]["end"], 4.0)

    def test_load_words_accepts_matching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"word": "طب", "start": 0.1, "end": 0.4},
                           {"word": "ازاي؟", "start": 0.4, "end": 0.9}],
                          fh, ensure_ascii=False)
            timed = flat_reels.load_words(path, "طب ازاي؟", 10.0, 11.0)
            self.assertEqual(timed[0]["start"], 10.1)
            self.assertEqual(timed[1]["end"], 10.9)

    def test_load_words_rejects_mismatched_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"word": "كلام", "start": 0.0, "end": 0.5},
                           {"word": "مختلف", "start": 0.5, "end": 1.0}],
                          fh, ensure_ascii=False)
            timed = flat_reels.load_words(path, "طب ازاي؟", 0.0, 1.0)
            # Falls back to even timings over the script words.
            self.assertEqual([t["word"] for t in timed],
                             ["طب", "ازاي؟"])


class TestValidation(unittest.TestCase):
    def test_valid_spec_passes(self):
        ok, errors = flat_reels.validate_reel_spec(_spec())
        self.assertTrue(ok, errors)

    def test_missing_id_fails(self):
        ok, errors = flat_reels.validate_reel_spec(_spec(id=""))
        self.assertFalse(ok)
        self.assertTrue(any("id" in e for e in errors))

    def test_unknown_palette_fails(self):
        ok, _ = flat_reels.validate_reel_spec(_spec(palette="neon"))
        self.assertFalse(ok)

    def test_no_scenes_fails(self):
        ok, _ = flat_reels.validate_reel_spec(_spec(scenes=[]))
        self.assertFalse(ok)

    def test_unknown_scene_type_fails(self):
        spec = _spec()
        spec["scenes"][0]["type"] = "musical"
        ok, errors = flat_reels.validate_reel_spec(spec)
        self.assertFalse(ok)
        self.assertTrue(any("musical" in e for e in errors))

    def test_duplicate_scene_id_fails(self):
        spec = _spec()
        spec["scenes"][1]["id"] = "s1"
        ok, _ = flat_reels.validate_reel_spec(spec)
        self.assertFalse(ok)

    def test_missing_narration_fails(self):
        spec = _spec()
        del spec["scenes"][0]["narration"]
        ok, _ = flat_reels.validate_reel_spec(spec)
        self.assertFalse(ok)


class TestTimings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-flat-test-")
        _sine_wav(os.path.join(self.tmp, "s1.wav"), 2.0)
        _sine_wav(os.path.join(self.tmp, "s2.wav"), 1.5)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_boundaries_accumulate_from_audio(self):
        timings, total, errors = flat_reels.compute_scene_timings(
            _spec(), self.tmp)
        self.assertEqual(errors, [])
        self.assertEqual(len(timings), 2)
        self.assertAlmostEqual(timings[0]["start"], 0.0)
        self.assertAlmostEqual(timings[0]["duration"], 2.0, places=1)
        self.assertAlmostEqual(timings[1]["start"], 2.0, places=1)
        self.assertAlmostEqual(total, 3.5, places=1)

    def test_missing_audio_is_an_error(self):
        spec = _spec()
        spec["scenes"][0]["narration"] = "nope.wav"
        _, _, errors = flat_reels.compute_scene_timings(spec, self.tmp)
        self.assertTrue(errors)


class TestHtmlBuilder(unittest.TestCase):
    def _html(self, spec=None, embed_font=False):
        spec = spec or _spec()
        timings = [
            {"id": "s1", "start": 0.0, "duration": 2.0,
             "words": flat_reels.even_word_timings(
                 "جسمك درجة حرارته 37 طول السنة.".split(), 0.0, 2.0)},
            {"id": "s2", "start": 2.0, "duration": 1.0,
             "words": flat_reels.even_word_timings(
                 ["طب", "ازاي؟"], 2.0, 3.0)},
        ]
        return flat_reels.build_flat_reels_html(
            spec, timings, embed_font=embed_font)

    def test_html_contains_titles_and_tag(self):
        page = self._html()
        self.assertIn("ليه بنحس بالبرد في الشتاء؟", page)
        self.assertIn("طب ازاي؟", page)
        self.assertIn('dir="rtl"', page)

    def test_html_contains_settime_and_scenes(self):
        page = self._html()
        self.assertIn("window.setTime", page)
        self.assertIn('"id": "s1"', page)
        self.assertIn('"id": "s2"', page)

    def test_html_contains_word_spans_with_times(self):
        page = self._html()
        self.assertIn('class="w" data-s="0.0"', page)
        self.assertIn('data-s="2.0"', page)
        self.assertIn("جسمك", page)

    def test_html_uses_palette_hexes(self):
        page = self._html()
        self.assertIn("#F7F0DC", page)  # cream paper
        self.assertIn("#E4572E", page)  # red-orange accent
        dark = self._html(_spec(palette="dark"))
        self.assertIn("#0C1014", dark)
        self.assertIn("#27B8C7", dark)

    def test_html_escapes_titles(self):
        spec = _spec()
        spec["scenes"][0]["title"] = "a <b> & c"
        page = self._html(spec)
        self.assertIn("a &lt;b&gt; &amp; c", page)

    def test_no_css_animations_or_transitions(self):
        # Determinism rule: every visual depends on t only.
        page = self._html()
        self.assertNotIn("@keyframes", page)
        self.assertNotIn("transition:", page)
        self.assertNotIn("animation:", page)

    def test_mixed_script_caption_words_present(self):
        # The 2026-11-23 tofu lesson: mixed Latin+Arabic lines must
        # be representable (the visual check is the smoke test).
        spec = _spec()
        spec["scenes"][0]["text"] = "توقيت الكلمات بمطابقة Deepgram دقيق."
        timings = [{"id": "s1", "start": 0.0, "duration": 2.0,
                    "words": flat_reels.even_word_timings(
                        spec["scenes"][0]["text"].split(), 0.0, 2.0)},
                   {"id": "s2", "start": 2.0, "duration": 1.0,
                    "words": flat_reels.even_word_timings(
                        ["طب", "ازاي؟"], 2.0, 3.0)}]
        page = flat_reels.build_flat_reels_html(spec, timings,
                                                embed_font=False)
        self.assertIn("Deepgram", page)
        self.assertIn("توقيت", page)

    def test_all_scene_types_build(self):
        spec = {"id": "all", "palette": "cream", "scenes": []}
        timings = []
        t = 0.0
        for i, typ in enumerate(flat_reels.SCENE_TYPES):
            sc = {"id": f"sc{i}", "type": typ, "layout": "body",
                  "narration": "x.wav", "text": "نص تجريبي قصير",
                  "title": f"عنوان {typ}", "icon": "check",
                  "body": "سطر توضيحي",
                  "steps": [{"text": "خطوة أولى"},
                            {"text": "خطوة ثانية"}],
                  "stat": {"value": "×5", "label": "خمس مرات"},
                  "bars": [{"label": "قبل", "value": 20,
                            "color": "blue"},
                           {"label": "بعد", "value": 100,
                            "color": "accent"}],
                  "panels": [{"title": "أ", "body": "ب",
                              "icon": "bolt"},
                             {"title": "ج", "body": "د",
                              "icon": "hand"}]}
            spec["scenes"].append(sc)
            timings.append({"id": sc["id"], "start": t, "duration": 1.0,
                            "words": flat_reels.even_word_timings(
                                ["نص", "تجريبي", "قصير"], t, t + 1.0)})
            t += 1.0
        page = flat_reels.build_flat_reels_html(spec, timings,
                                                embed_font=False)
        # Every scene renders its div; each type renders its own
        # marker (question has no h1 title — the giant words ARE
        # the scene; statistic shows the big number; conclusion
        # shows the takeaway as the art label).
        for i, typ in enumerate(flat_reels.SCENE_TYPES):
            self.assertIn(f'id="scene-sc{i}"', page)
        self.assertIn("عنوان intro", page)
        self.assertIn("عنوان concept", page)
        self.assertIn("عنوان process", page)
        self.assertIn("عنوان comparison", page)
        self.assertIn('class="giant anim"', page)
        self.assertIn('class="bigstat"', page)
        self.assertIn("سطر توضيحي", page)


class TestRenderEntryPoints(unittest.TestCase):
    def test_invalid_spec_fails_without_browser(self):
        ok, info = flat_reels.render_flat_reels(
            {"id": "bad"}, "/tmp/out.mp4")
        self.assertFalse(ok)
        self.assertEqual(info["code"], "INVALID_SPEC")


if __name__ == "__main__":
    unittest.main()


class TestAmbientMotionAndExpandedDesign(unittest.TestCase):
    def test_icon_library_expanded_to_at_least_30(self):
        self.assertGreaterEqual(len(flat_reels.ICONS), 30)
        for name in ("brain", "heart", "lungs", "sun", "moon", "clock",
                     "gear", "shield", "magnet", "bulb", "cpu", "wave",
                     "eye", "scale", "sparkles"):
            self.assertIn(name, flat_reels.ICONS)
            svg = flat_reels.icon_svg(name, 120)
            self.assertIn('class="ambient-icon"', svg)
            self.assertIn('viewBox="0 0 64 64"', svg)

    def test_question_scene_has_decorative_motifs(self):
        spec = _spec()
        timings = [
            {"id": "s1", "start": 0.0, "duration": 2.0,
             "words": flat_reels.even_word_timings(
                 spec["scenes"][0]["text"].split(), 0.0, 2.0)},
            {"id": "s2", "start": 2.0, "duration": 1.2,
             "words": flat_reels.even_word_timings(
                 ["طب", "ازاي؟"], 2.0, 3.2)},
        ]
        page = flat_reels.build_flat_reels_html(spec, timings,
                                                embed_font=False)
        self.assertIn('class="q-stage"', page)
        self.assertIn('class="q-watermark"', page)
        self.assertIn('class="q-orbit"', page)
        self.assertIn('class="q-underline"', page)
        self.assertIn('class="bg-motif bg-blob-a"', page)
        self.assertIn("backgroundPosition", page)

    def test_new_scene_types_and_layout_classes(self):
        spec = {
            "id": "expanded",
            "palette": "cream",
            "scenes": [
                {"id": "t1", "type": "timeline", "layout": "timeline",
                 "narration": "a.wav", "text": "أول خطوة ثم ثانية.",
                 "title": "التسلسل",
                 "items": [{"tag": "01", "text": "البداية"},
                           {"tag": "02", "text": "النتيجة"}]},
                {"id": "c1", "type": "cycle", "layout": "cycle",
                 "narration": "b.wav", "text": "دورة متكاملة مستمرة.",
                 "title": "الدورة", "body": "الحرارة",
                 "nodes": ["إشارة", "استجابة", "توازن"]},
                {"id": "l1", "type": "layers", "layout": "layers",
                 "narration": "c.wav", "text": "ثلاث طبقات متتالية.",
                 "title": "الطبقات",
                 "layers": [{"label": "الجلد", "text": "يستشعر البرد"},
                            {"label": "الأوعية", "text": "تنقبض للحفظ"}]},
                {"id": "q1", "type": "callout", "layout": "zoom",
                 "narration": "d.wav", "text": "الخلاصة واضحة جدا.",
                 "quote": "الشعور بالبرد إنذار لا قياس.",
                 "author": "طب ازاي؟"},
            ],
        }
        ok, errs = flat_reels.validate_reel_spec(spec)
        self.assertTrue(ok, errs)
        timings = [
            {"id": sc["id"], "start": i * 2.0, "duration": 2.0,
             "words": flat_reels.even_word_timings(
                 sc["text"].split(), i * 2.0, (i + 1) * 2.0)}
            for i, sc in enumerate(spec["scenes"])
        ]
        page = flat_reels.build_flat_reels_html(spec, timings,
                                                embed_font=False)
        self.assertIn("layout-timeline", page)
        self.assertIn("layout-cycle", page)
        self.assertIn("layout-layers", page)
        self.assertIn("layout-zoom", page)
        self.assertIn('class="tl-spine-fill"', page)
        self.assertIn('class="cycle-ring"', page)
        self.assertIn('class="layers-stack"', page)
        self.assertIn('class="callout-card anim"', page)
        self.assertNotIn("@keyframes", page)
        self.assertNotIn("transition:", page)
        self.assertNotIn("animation:", page)

    def test_bigstat_numeric_countup_attributes(self):
        spec = {
            "id": "stat",
            "palette": "cream",
            "scenes": [
                {"id": "s1", "type": "statistic", "layout": "scale",
                 "narration": "a.wav", "text": "خمس مرات أسرع.",
                 "stat": {"value": "×5", "label": "خمس مرات"}},
            ],
        }
        timings = [{"id": "s1", "start": 0.0, "duration": 2.0,
                    "words": flat_reels.even_word_timings(
                        ["خمس", "مرات", "أسرع."], 0.0, 2.0)}]
        page = flat_reels.build_flat_reels_html(spec, timings,
                                                embed_font=False)
        self.assertIn('data-target="5"', page)
        self.assertIn('data-pre="×"', page)

    def test_measure_narration_pace_and_trim_recommendation(self):
        spec = _spec()
        timings = [
            {"id": "s1", "start": 0.0, "duration": 3.0, "words": []},
            {"id": "s2", "start": 3.0, "duration": 1.0, "words": []},
        ]
        pace = flat_reels.measure_narration_pace(
            spec, timings, target_duration=3.0)
        self.assertEqual(pace["total_words"], 8)
        self.assertAlmostEqual(pace["total_duration"], 4.0)
        self.assertAlmostEqual(pace["overall_wps"], 2.0)
        self.assertAlmostEqual(pace["over_target_by"], 1.0)
        self.assertEqual(pace["recommended_trim_words"], 2)
        self.assertTrue(any("trim" in w for w in pace["warnings"]))
