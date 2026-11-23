"""Tests for AgentCut v1.64.0 (editorial mode).

The editorial theatre: visual beats instead of one image per
scene, a 4-move camera cycle across beats, full-bleed crisp
visuals (light vignette only, bottom-32% scrim), NO header at
all, full-sentence pill captions only (karaoke rejected), and
one glass highlight card in the middle third for 2.8s.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.broll import build_broll_filter_chain
from agentcut.compositor import (
    CARD_SHADOW_PAD,
    EDITORIAL_PAN_ZOOM,
    EDITORIAL_XFADE,
    EDITORIAL_ZOOM_MAX,
    HIGHLIGHT_CARD_DURATION,
    editorial_camera_for_beat,
    editorial_card_rect,
    editorial_layer1_clip,
    png_dimensions,
)
from agentcut.render import (
    _validate_editorial_beats,
    produce_explainer,
)


# ---------------------------------------------------------------- helpers

def _write_sine_wav(path, duration=5.0, freq=440.0, rate=48000):
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
    return path


def _fmt_srt_time(t):
    ms = int(round(t * 1000))
    return (f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:"
            f"{(ms // 1000) % 60:02d},{ms % 1000:03d}")


def _write_srt(path, cues):
    """cues: [(start, end, text)] in scene-relative seconds."""
    with open(path, "w", encoding="utf-8") as fh:
        for i, (s, e, text) in enumerate(cues, 1):
            fh.write(f"{i}\n{_fmt_srt_time(s)} --> {_fmt_srt_time(e)}\n"
                     f"{text}\n\n")
    return path


# ------------------------------------------------------- camera + helpers

class TestEditorialCameraCycle(unittest.TestCase):
    def test_cycle_order_repeats(self):
        moves = [editorial_camera_for_beat(i)[0] for i in range(8)]
        self.assertEqual(moves, ["zoom_in", "pan_right", "zoom_out",
                                 "pan_left", "zoom_in", "pan_right",
                                 "zoom_out", "pan_left"])

    def test_zoom_values(self):
        self.assertEqual((EDITORIAL_ZOOM_MAX, EDITORIAL_PAN_ZOOM),
                         (1.18, 1.14))
        self.assertEqual(EDITORIAL_XFADE, 0.25)
        move, ks, ke = editorial_camera_for_beat(0)
        self.assertEqual((move, ks, ke), ("zoom_in", 1.0, 1.18))
        move, ks, ke = editorial_camera_for_beat(2)
        self.assertEqual((move, ks, ke), ("zoom_out", 1.18, 1.0))
        for i in (1, 3):
            move, ks, ke = editorial_camera_for_beat(i)
            self.assertEqual((ks, ke), (1.14, 1.14))
        # The cycle is a global counter, not per-scene: beat 4 is
        # zoom_in again (the 5th beat of the whole video).
        self.assertEqual(editorial_camera_for_beat(4)[0], "zoom_in")

    def test_layer1_clip_image_gets_camera_and_light_treatment(self):
        clip = {"id": "b1", "kind": "image", "src": "a.png",
                "duration": 3.0}
        out = editorial_layer1_clip(clip, 0)
        self.assertEqual(out["treatment"], {"vignette": 0.5})
        self.assertEqual(out["ken_burns"], "zoom_in")
        self.assertEqual((out["_kb_start"], out["_kb_end"]), (1.0, 1.18))
        # No blur key anywhere in the treatment.
        self.assertNotIn("blur", out["treatment"])

    def test_layer1_clip_video_gets_treatment_but_no_camera(self):
        clip = {"id": "b1", "kind": "video", "src": "a.mp4",
                "duration": 3.0}
        out = editorial_layer1_clip(clip, 1)
        self.assertEqual(out["treatment"], {"vignette": 0.5})
        self.assertNotIn("ken_burns", out)

    def test_card_rect_in_middle_third(self):
        # The band is 33%..66% of the height (0.33 fractions).
        top = int(round(0.33 * 1280))
        bottom = top + int(round(0.33 * 1280))
        x, y, w, h = editorial_card_rect(720, 1280, 500, 200)
        self.assertEqual(x, (720 - 500) // 2)
        self.assertGreaterEqual(y, top)
        self.assertLessEqual(y + h, bottom)
        # A too-tall card clamps into the band.
        x2, y2, w2, h2 = editorial_card_rect(720, 1280, 500, 2000)
        self.assertEqual(h2, bottom - top)
        self.assertEqual(y2, top)
        self.assertLessEqual(y2 + h2, bottom)


# ------------------------------------------------------- beat validation

class TestValidateEditorialBeats(unittest.TestCase):
    def test_absent_beats_means_whole_scene(self):
        beats, err = _validate_editorial_beats({}, "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual(beats, [{"start": 0.0, "end": 5.0,
                                  "broll": None}])

    def test_valid_tiling_kept(self):
        beats, err = _validate_editorial_beats(
            {"beats": [{"start": 0.0, "end": 2.5, "broll": "a.png"},
                       {"start": 2.5, "end": 5.0}]}, "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual(len(beats), 2)
        self.assertEqual(beats[0]["broll"], "a.png")

    def test_rejects_bad_beats(self):
        # (scene, expected code): a beat window outside the scene
        # (negative start, end <= start, end past the scene end)
        # is a range error, everything else is a schema error.
        bad = [
            ({"beats": []}, "INVALID_SCHEMA"),              # empty
            ({"beats": "nope"}, "INVALID_SCHEMA"),          # not a list
            ({"beats": ["not-a-dict"]}, "INVALID_SCHEMA"),  # not a dict
            ({"beats": [{"end": 2.5}]}, "INVALID_SCHEMA"),  # missing start
            ({"beats": [{"start": 0.0}]}, "INVALID_SCHEMA"),  # missing end
            ({"beats": [{"start": 2.5, "end": 2.5}]},
             "CLIP_OUT_OF_RANGE"),                           # end <= start
            ({"beats": [{"start": -0.1, "end": 2.5}]},
             "CLIP_OUT_OF_RANGE"),                           # negative
            ({"beats": [{"start": 0.0, "end": 9.9}]},
             "CLIP_OUT_OF_RANGE"),                           # past end
            ({"beats": [{"start": 0.0, "end": 2.5}]},
             "INVALID_SCHEMA"),                              # does not tile
            ({"beats": [{"start": 0.0, "end": 2.5},
                        {"start": 2.0, "end": 5.0}]},
             "INVALID_SCHEMA"),                              # overlap
            ({"beats": [{"start": 0.0, "end": 5.0,
                         "broll": 42}]}, "INVALID_SCHEMA"),  # bad broll
        ]
        for scene, code in bad:
            _, err = _validate_editorial_beats(scene, "s1", 5.0)
            self.assertIsNotNone(err, f"accepted bad beats: {scene!r}")
            self.assertEqual(err["code"], code, f"beats: {scene!r}")

    def test_rounding_epsilon_end_clamped(self):
        # The essay run-1 case: Whisper's scene duration (12.112,
        # rounded to 3 decimals) overshoots ffprobe's exact
        # narration duration (12.111958) by 42us, and the last
        # beat ends at the Whisper duration. A <=0.05s overshoot
        # clamps to dur; a larger one is still rejected.
        beats, err = _validate_editorial_beats(
            {"beats": [{"start": 0.0, "end": 4.0},
                       {"start": 4.0, "end": 8.0},
                       {"start": 8.0, "end": 12.112}]},
            "s1", 12.111958)
        self.assertIsNone(err)
        self.assertEqual(beats[-1]["end"], round(12.111958, 3))
        _, err = _validate_editorial_beats(
            {"beats": [{"start": 0.0, "end": 5.5}]}, "s1", 5.0)
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], "CLIP_OUT_OF_RANGE")


# ------------------------------------------------------- broll chain

class TestBrollEditorial(unittest.TestCase):
    def test_editorial_chain_no_blur_no_darkening(self):
        chain = build_broll_filter_chain(
            720, 1280, 3.0, False, 24, style="editorial",
            camera_move="zoom_in")
        self.assertNotIn("gblur", chain)
        self.assertNotIn("eq=", chain)
        # Subtle vignette, and a zoompan that reaches 1.18
        # (written as 1+0.18*s in the zoom expression).
        self.assertIn("vignette=PI/8", chain)
        self.assertIn("zoompan", chain)
        self.assertIn("1+0.18", chain)

    def test_editorial_pan_chain(self):
        chain = build_broll_filter_chain(
            720, 1280, 3.0, False, 24, style="editorial",
            camera_move="pan_right")
        self.assertIn("1.14", chain)
        self.assertNotIn("gblur", chain)

    def test_cinematic_chain_unchanged(self):
        chain = build_broll_filter_chain(
            720, 1280, 3.0, False, 24, style="cinematic")
        self.assertIn("gblur", chain)
        self.assertIn("eq=", chain)
        self.assertIn("vignette=PI/4", chain)

    def test_bad_style_and_move_rejected(self):
        with self.assertRaises(ValueError):
            build_broll_filter_chain(
                720, 1280, 3.0, False, 24, style="nope")
        with self.assertRaises(ValueError):
            build_broll_filter_chain(
                720, 1280, 3.0, False, 24, style="editorial",
                camera_move="spin")


# ------------------------------------------- explainer integration (1 render)

class _V164Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="agentcut-v164-")
        base = os.path.join(os.path.dirname(__file__), "..")
        img = os.path.join(base, "data", "test_media", "pattern.png")
        cls.img = os.path.abspath(img)
        scenes = []
        for sid in ("s1", "s2"):
            audio = _write_sine_wav(os.path.join(cls.tmp, f"{sid}.wav"),
                                    5.0)
            srt = _write_srt(os.path.join(cls.tmp, f"{sid}.srt"), [
                (0.2, 2.4, "الجملة الأولى"),
                (2.6, 4.8, "الجملة الثانية")])
            scene = {"id": sid, "title": f"عنوان {sid}",
                     "narration": audio, "captions": srt,
                     "beats": [{"start": 0.0, "end": 2.5,
                                "broll": cls.img},
                               {"start": 2.5, "end": 5.0,
                                "broll": cls.img}]}
            if sid == "s2":
                # Explicit timings so the card trigger is exact.
                scene["word_timings"] = [
                    {"word": "سبعين", "start": 1.0, "end": 1.6},
                    {"word": "بالمئة", "start": 1.7, "end": 2.3}]
                scene["highlight_card"] = {
                    "title": "70% من الوقت",
                    "subtitle": "يستهلك في رد الفعل",
                    "trigger_word": "سبعين"}
            scenes.append(scene)
        brief = {"id": "v164", "title": "اختبار 1.64",
                 "style": "editorial",
                 "output": {"width": 360, "height": 640, "fps": 30},
                 "chapters": False, "motion": "gentle",
                 "scenes": scenes}
        cls.out = os.path.join(cls.tmp, "editorial.mp4")
        cls.ok, cls.info = produce_explainer(brief, cls.out)
        assert cls.ok, cls.info
        cls.project = cls.info["project"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestEditorialExplainer(_V164Base):
    def test_render_ok_and_duration(self):
        self.assertTrue(self.ok, self.info)
        self.assertTrue(os.path.exists(self.out))
        # 2 scenes x 5s; the fades overlap, they do not add time.
        self.assertAlmostEqual(self.info["duration"], 10.0, places=1)

    def test_four_beat_clips_with_camera_cycle(self):
        clips = self.project["clips"]
        self.assertEqual(len(clips), 4)
        moves = [c.get("ken_burns") for c in clips]
        self.assertEqual(moves, ["zoom_in", "pan_right",
                                 "zoom_out", "pan_left"])
        kb = [(c["_kb_start"], c["_kb_end"]) for c in clips]
        self.assertEqual(kb, [(1.0, 1.18), (1.14, 1.14),
                              (1.18, 1.0), (1.14, 1.14)])

    def test_beat_durations_and_transitions(self):
        clips = self.project["clips"]
        # Non-final beats carry slot+0.25 so the fade starts
        # exactly on the slot boundary; final beats are the slot.
        self.assertEqual([c["duration"] for c in clips],
                         [2.75, 2.5, 2.75, 2.5])
        # Crossfade inside a scene, hard cut between scenes.
        # ("crossfade", not "fade": "fade" is a fade-to-black
        # that concatenates; only XFADE_TRANSITIONS overlap.)
        self.assertEqual([c["transition"] for c in clips],
                         ["none", "crossfade", "none", "crossfade"])
        for c in clips:
            if c["transition"] == "crossfade":
                self.assertEqual(c["transition_duration"], 0.25)

    def test_no_heavy_blur_light_vignette_only(self):
        for clip in self.project["clips"]:
            self.assertEqual(clip["treatment"], {"vignette": 0.5},
                             f"clip {clip['id']}: {clip['treatment']}")

    def test_bottom_scrim_only_no_top(self):
        scrims = self.project["scrims"]
        self.assertEqual(scrims, {"bottom": {"opacity": 0.55,
                                             "depth": 0.32}})
        self.assertNotIn("top", scrims)

    def test_no_header_texts(self):
        texts = self.project["texts"]
        # The only texts are the highlight card's title/subtitle.
        self.assertEqual(len(texts), 2)
        self.assertEqual({t["text"] for t in texts},
                         {"70% من الوقت", "يستهلك في رد الفعل"})
        # Nothing in the header zone (top 18%) and nothing spans
        # the whole video (the fixed title would).
        for t in texts:
            self.assertGreaterEqual(t["y"], 0.18 * 640)
            self.assertLess(t["end"] - t["start"], 10.0)
        self.assertNotIn("اختبار 1.64",
                         [t["text"] for t in texts])

    def test_no_subject_overlays(self):
        for ov in self.project["overlays"]:
            self.assertFalse(ov["id"].startswith("comp-subject"))

    def test_highlight_card_window_and_position(self):
        cards = [o for o in self.project["overlays"]
                 if o["id"] == "ed-card-s2"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        # Trigger word "سبعين" at scene-relative 1.0s; scene 2
        # starts at 5.0s absolute.
        self.assertAlmostEqual(card["start"], 6.0, places=2)
        self.assertEqual(card["duration"], HIGHLIGHT_CARD_DURATION)
        self.assertEqual(card["duration"], 2.8)
        # The card rect (the PNG minus the 16px shadow pad on
        # each side) sits in the middle third: 33%..66% of 640.
        png_w, png_h = png_dimensions(card["src"])
        top = int(round(0.33 * 640))
        bottom = top + int(round(0.33 * 640))
        card_top = card["y"] + CARD_SHADOW_PAD
        card_h = png_h - 2 * CARD_SHADOW_PAD
        self.assertGreaterEqual(card_top, top)
        self.assertLessEqual(card_top + card_h, bottom)
        # Card texts use the v3.3.1 tight 0.05s inset on both
        # sides (card 6.0..8.8, so text 6.05..8.75): the card
        # never lingers empty on screen.
        title = next(t for t in self.project["texts"]
                     if t["text"] == "70% من الوقت")
        self.assertAlmostEqual(title["start"], 6.05, places=2)
        self.assertAlmostEqual(title["end"], 8.75, places=2)

    def test_captions_are_plain_full_sentence(self):
        caps = self.project["captions"]
        self.assertEqual(len(caps), 4)
        for cap in caps:
            self.assertNotIn("word_timings", cap)
            self.assertNotIn("karaoke",
                             str(cap.get("captions_style", "")))


# ------------------------------------------------------- editorial rejections

class TestEditorialRejections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-v164-rej-")
        base = os.path.join(os.path.dirname(__file__), "..")
        self.img = os.path.abspath(
            os.path.join(base, "data", "test_media", "pattern.png"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scene(self, sid="s1", dur=5.0, **extra):
        audio = _write_sine_wav(os.path.join(self.tmp, f"{sid}.wav"),
                                dur)
        srt = _write_srt(os.path.join(self.tmp, f"{sid}.srt"), [
            (0.2, 2.4, "الجملة الأولى"),
            (2.6, 4.8, "الجملة الثانية")])
        scene = {"id": sid, "title": f"عنوان {sid}",
                 "narration": audio, "captions": srt,
                 "beats": [{"start": 0.0, "end": dur,
                            "broll": self.img}]}
        scene.update(extra)
        return scene

    def _brief(self, scenes, style="editorial", **extra):
        brief = {"id": "v164-rej", "title": "اختبار رفض",
                 "style": style,
                 "output": {"width": 360, "height": 640, "fps": 30},
                 "chapters": False, "scenes": scenes}
        brief.update(extra)
        return brief

    def _assert_rejected(self, brief):
        out = os.path.join(self.tmp, "rejected.mp4")
        ok, info = produce_explainer(brief, out)
        self.assertFalse(ok, f"accepted: {brief}")
        self.assertEqual(info["code"], "INVALID_SCHEMA")

    def test_brief_karaoke_rejected(self):
        self._assert_rejected(
            self._brief([self._scene()], captions_style="karaoke"))

    def test_scene_karaoke_rejected(self):
        self._assert_rejected(
            self._brief([self._scene(captions_style="karaoke")]))

    def test_kinetic_rejected(self):
        # Kinetic is a scene-level boolean flag (word-level
        # animation), not a captions_style value.
        self._assert_rejected(
            self._brief([self._scene(kinetic=True)]))

    def test_subject_rejected(self):
        self._assert_rejected(
            self._brief([self._scene(subject=self.img)]))

    def test_keywords_rejected(self):
        self._assert_rejected(
            self._brief([self._scene(keywords=["كلمة"])]))

    def test_diagram_rejected(self):
        self._assert_rejected(
            self._brief([self._scene(diagram={"type": "bar"})]))

    def test_highlight_card_outside_editorial_rejected(self):
        self._assert_rejected(
            self._brief(
                [self._scene(highlight_card={"title": "70%"})],
                style="cinematic"))

    def test_highlight_card_without_title_rejected(self):
        self._assert_rejected(
            self._brief([self._scene(highlight_card={"subtitle": "x"})]))

    def test_beats_not_tiling_rejected(self):
        scene = self._scene()
        scene["beats"] = [{"start": 0.0, "end": 2.5,
                           "broll": self.img}]
        self._assert_rejected(self._brief([scene]))

    def test_beat_broll_missing_file_rejected(self):
        scene = self._scene()
        scene["beats"] = [{"start": 0.0, "end": 5.0,
                           "broll": os.path.join(self.tmp, "nope.png")}]
        out = os.path.join(self.tmp, "rejected.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertFalse(ok)
        self.assertEqual(info["code"], "FILE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
