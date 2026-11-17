"""Tests for AgentCut v1.63.0.

Task A: the scene-level word_timings key in produce_explainer
(AgentMedia bundle timings feed the karaoke path; the explicit
key wins over in-tool TTS timings; malformed timings rejected).
Task B: the subtitle pill on EVERY cue, with a 0.12s fade.
Task C: continuous micro-motion (smoothstep Ken Burns on the
gradient too, breathing subject) + the glass-card upgrade
(slide_up + fade entrance/exit, accent bar, richer template).
"""

from __future__ import annotations

import math
import os
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.broll import build_broll_filter_chain
from agentcut.compositor import (
    CARD_SHADOW_PAD,
    KB_DRIFT_FRACTION,
    cinematic_layer1_clip,
    glass_card_overlay,
    png_dimensions,
)
from agentcut.render import (
    _validate_scene_word_timings,
    build_ffmpeg_args,
    produce_explainer,
)
from agentcut.validate import validate_project
from agentcut.vector_ui import (
    get_cached_glass_card,
    render_glass_card_png,
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


def _write_transparent_subject(path, size=400):
    from agentcut.vector_ui import encode_rgba_png
    pixels = bytearray()
    cx = cy = size / 2.0
    r = size * 0.38
    for y in range(size):
        for x in range(size):
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            if dx * dx + dy * dy <= r * r:
                pixels.extend([220, 40, 40, 255])
            else:
                pixels.extend([0, 0, 0, 0])
    with open(path, "wb") as fh:
        fh.write(encode_rgba_png(size, size, pixels))
    return path


def _grab_frame(video, t, width, height):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", video,
           "-vf", f"scale={width}:{height}", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    assert len(data) == width * height * 3, f"short frame: {len(data)}"
    return data


def _band_median(frame, width, height, y0, y1, x0=0.15, x1=0.85):
    vals = []
    for y in range(int(y0 * height), int(y1 * height)):
        row = y * width * 3
        for x in range(int(x0 * width), int(x1 * width)):
            o = row + x * 3
            vals.append(0.299 * frame[o] + 0.587 * frame[o + 1]
                        + 0.114 * frame[o + 2])
    return statistics.median(vals)


def _mapd(f1, f2):
    return sum(abs(a - b) for a, b in zip(f1, f2)) / len(f1)


def _count_yellow(frame, width, height, y0=0.80, y1=0.92):
    n = 0
    for y in range(int(y0 * height), int(y1 * height)):
        row = y * width * 3
        for x in range(int(0.05 * width), int(0.95 * width)):
            o = row + x * 3
            r, g, b = frame[o], frame[o + 1], frame[o + 2]
            if r > 180 and g > 140 and b < 110 and r - b > 80:
                n += 1
    return n


def _region_mean_r(frame, width, height, cx, cy, hw=2, hh=10):
    """Mean red channel in a small rect around (cx, cy)."""
    vals = []
    for y in range(max(0, cy - hh), min(height, cy + hh)):
        row = y * width * 3
        for x in range(max(0, cx - hw), min(width, cx + hw)):
            vals.append(frame[row + x * 3])
    return sum(vals) / len(vals)


class _V163Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-v163-")
        self.subject = _write_transparent_subject(
            os.path.join(self.tmp, "subject.png"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scene(self, sid, dur=5.0, cues=None, title=None, **extra):
        audio = _write_sine_wav(os.path.join(self.tmp, f"{sid}.wav"), dur)
        srt = _write_srt(os.path.join(self.tmp, f"{sid}.srt"), cues or [
            (0.2, 2.4, "الجملة الأولى"),
            (2.6, 4.8, "الجملة الثانية")])
        scene = {"id": sid, "title": title or f"عنوان {sid}",
                 "narration": audio, "captions": srt,
                 "subject": self.subject}
        scene.update(extra)
        return scene

    def _brief(self, scenes, width=360, height=640, **extra):
        brief = {"id": "v163", "title": "اختبار 1.63",
                 "style": "cinematic",
                 "output": {"width": width, "height": height, "fps": 30},
                 "chapters": False, "motion": "gentle",
                 "scenes": scenes}
        brief.update(extra)
        return brief


# ------------------------------------------------------- Task A: validator

class TestSceneWordTimingsValidation(unittest.TestCase):
    def test_valid_list_sorted_and_kept(self):
        wt = [{"word": "ب", "start": 1.0, "end": 2.0},
              {"word": "أ", "start": 0.1, "end": 0.9}]
        cleaned, err = _validate_scene_word_timings(wt, "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual([w["word"] for w in cleaned], ["أ", "ب"])

    def test_empty_list_means_absent(self):
        cleaned, err = _validate_scene_word_timings([], "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual(cleaned, [])

    def test_not_a_list_rejected(self):
        _, err = _validate_scene_word_timings("nope", "s1", 5.0)
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], "INVALID_SCHEMA")

    def test_malformed_entries_rejected(self):
        bad = [
            [{"word": "أ", "start": 0.1}],                    # missing end
            [{"word": "أ", "end": 1.0}],                      # missing start
            [{"start": 0.1, "end": 1.0}],                     # missing word
            [{"word": "", "start": 0.1, "end": 1.0}],          # empty word
            [{"word": "أ", "start": "x", "end": 1.0}],         # non-numeric
            [{"word": "أ", "start": 1.0, "end": 1.0}],         # end <= start
            [{"word": "أ", "start": 2.0, "end": 1.0}],         # end < start
            [{"word": "أ", "start": -0.5, "end": 1.0}],        # negative
            ["not-a-dict"],                                    # not a dict
        ]
        for wt in bad:
            _, err = _validate_scene_word_timings(wt, "s1", 5.0)
            self.assertIsNotNone(err, f"accepted malformed: {wt!r}")
            self.assertEqual(err["code"], "INVALID_SCHEMA")

    def test_clamp_and_drop_out_of_range(self):
        wt = [{"word": "أ", "start": 0.1, "end": 0.9},
              {"word": "ب", "start": 4.0, "end": 9.9},   # end clamped
              {"word": "ج", "start": 5.0, "end": 6.0}]   # dropped
        cleaned, err = _validate_scene_word_timings(wt, "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual([w["word"] for w in cleaned], ["أ", "ب"])
        self.assertEqual(cleaned[1]["end"], 5.0)

    def test_overlap_first_wins(self):
        wt = [{"word": "أ", "start": 0.1, "end": 1.0},
              {"word": "ب", "start": 0.5, "end": 1.5},   # overlaps: dropped
              {"word": "ج", "start": 1.5, "end": 2.0}]   # kept
        cleaned, err = _validate_scene_word_timings(wt, "s1", 5.0)
        self.assertIsNone(err)
        self.assertEqual([w["word"] for w in cleaned], ["أ", "ج"])


# ------------------------------------------- Task A: explainer integration

class TestSceneWordTimingsExplainer(_V163Base):
    def test_karaoke_from_explicit_timings(self):
        wt = [{"word": "الجملة", "start": 0.3, "end": 1.0},
              {"word": "الأولى", "start": 1.2, "end": 2.2},
              {"word": "الجملة", "start": 2.7, "end": 3.4},
              {"word": "الثانية", "start": 3.6, "end": 4.6}]
        scene = self._scene("s1", word_timings=wt,
                            captions_style="karaoke")
        out = os.path.join(self.tmp, "karaoke.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        timed = [c for c in info["project"]["captions"]
                 if c.get("word_timings")]
        self.assertTrue(timed, "no caption carries word_timings")
        # The explicit windows reached the built project.
        first = timed[0]["word_timings"]
        self.assertAlmostEqual(first[0]["start"], 0.3)
        self.assertAlmostEqual(first[0]["end"], 1.0)
        # And the highlight is visible on screen: yellow pixels
        # in the caption band at the first word's midpoint.
        frame = _grab_frame(out, 0.65, 360, 640)
        self.assertGreater(_count_yellow(frame, 360, 640), 10)

    def test_explicit_timings_win_over_tts(self):
        # The scene has narration_text, so in-tool TTS runs (in
        # this VM Edge fails and the sine fallback supplies its
        # own syllable-weighted timings). The explicit key must
        # win: the built captions carry OUR windows, not the
        # TTS ones. Engine-independent: whichever TTS runs, the
        # explicit windows are distinct from its tiling.
        scene = {"id": "s1", "title": "أسبقية",
                 "narration_text": "مرحبا بالعالم",
                 "captions_style": "karaoke",
                 "subject": self.subject,
                 "word_timings": [
                     {"word": "مرحبا", "start": 0.20, "end": 0.60},
                     {"word": "بالعالم", "start": 1.00, "end": 1.60}]}
        out = os.path.join(self.tmp, "precedence.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        timed = [c for c in info["project"]["captions"]
                 if c.get("word_timings")]
        self.assertTrue(timed, "no caption carries word_timings")
        got = [(w["start"], w["end"]) for w in timed[0]["word_timings"]]
        self.assertEqual(got, [(0.20, 0.60), (1.00, 1.60)])

    def test_explicit_empty_list_keeps_tts_timings(self):
        scene = {"id": "s1", "title": "قائمة فارغة",
                 "narration_text": "مرحبا بالعالم",
                 "captions_style": "karaoke",
                 "subject": self.subject,
                 "word_timings": []}
        out = os.path.join(self.tmp, "empty.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        timed = [c for c in info["project"]["captions"]
                 if c.get("word_timings")]
        self.assertTrue(
            timed, "explicit [] wiped the TTS timings instead of "
                   "keeping them")

    def test_all_words_dropped_means_no_karaoke(self):
        wt = [{"word": "بعيد", "start": 100.0, "end": 101.0}]
        scene = self._scene("s1", word_timings=wt,
                            captions_style="karaoke")
        out = os.path.join(self.tmp, "dropped.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        timed = [c for c in info["project"]["captions"]
                 if c.get("word_timings")]
        self.assertEqual(
            timed, [],
            "a non-empty explicit list whose words were all "
            "dropped must yield no karaoke, not TTS timings")

    def test_count_mismatch_reconciled_not_degraded(self):
        # Whisper's tiny model splits and joins words
        # ("السينمائية" -> "سنما" + "ايتي"), so the timing
        # count rarely equals the cue's word count. The cue
        # must still get karaoke: the split halves merge
        # (smallest gap first) and a joined word splits at
        # its midpoint — never a silent downgrade to plain.
        cues = [(0.2, 4.1, "هذا اختبار حقيقي للمشاهد السينمائية"),
                (4.3, 4.9, "نص قصير")]
        wt = [{"word": "هذا", "start": 0.3, "end": 0.7},
              {"word": "اختبار", "start": 0.8, "end": 1.4},
              {"word": "حقيقي", "start": 1.5, "end": 2.1},
              {"word": "للمشاهد", "start": 2.2, "end": 2.8},
              {"word": "سنما", "start": 2.9, "end": 3.3},
              {"word": "ايتي", "start": 3.3, "end": 3.7},
              {"word": "نصقصير", "start": 4.3, "end": 4.9}]
        scene = self._scene("s1", cues=cues, word_timings=wt,
                            captions_style="karaoke")
        out = os.path.join(self.tmp, "reconciled.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        timed = [c for c in info["project"]["captions"]
                 if c.get("word_timings")]
        self.assertEqual(len(timed), 2,
                         "both cues must keep karaoke")
        # Cue 1: 6 timings -> 5 words; the split halves merge
        # into the split word's full span.
        first = timed[0]["word_timings"]
        self.assertEqual(len(first), 5)
        self.assertEqual(first[4]["word"], "السينمائية")
        self.assertAlmostEqual(first[4]["start"], 2.9)
        self.assertAlmostEqual(first[4]["end"], 3.7)
        # Cue 2: 1 timing -> 2 words; the window splits at
        # its midpoint.
        second = timed[1]["word_timings"]
        self.assertEqual(len(second), 2)
        self.assertEqual([w["word"] for w in second], ["نص", "قصير"])
        self.assertAlmostEqual(second[0]["start"], 4.3)
        self.assertAlmostEqual(second[0]["end"], 4.6)
        self.assertAlmostEqual(second[1]["start"], 4.6)
        self.assertAlmostEqual(second[1]["end"], 4.9)

    def test_malformed_timings_rejected(self):
        scene = self._scene(
            "s1", word_timings=[{"word": "أ", "start": 2.0, "end": 1.0}],
            captions_style="karaoke")
        out = os.path.join(self.tmp, "bad.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertFalse(ok)
        self.assertEqual(info["code"], "INVALID_SCHEMA")
        self.assertFalse(os.path.exists(out))


# ------------------------------------------------------------- Task B: pill

class TestPillEveryCue(_V163Base):
    def test_pill_on_all_four_cues_with_fade(self):
        # The 1.62.0 defect: the pill showed on cue 1 only. The
        # brief has a 0.2s gap between cues, so t=2.5 / t=7.5
        # are caption-free reference frames.
        s1 = self._scene("s1", title="المشهد الأول")
        s2 = self._scene("s2", title="المشهد الثاني")
        out = os.path.join(self.tmp, "pill.mp4")
        ok, info = produce_explainer(self._brief([s1, s2]), out)
        self.assertTrue(ok, info)
        gap1 = _band_median(_grab_frame(out, 2.5, 360, 640),
                            360, 640, 0.76, 0.92)
        gap2 = _band_median(_grab_frame(out, 7.5, 360, 640),
                            360, 640, 0.76, 0.92)
        for label, t, gap in (("S1 cue1", 1.3, gap1),
                              ("S1 cue2", 3.7, gap1),
                              ("S2 cue1", 6.3, gap2),
                              ("S2 cue2", 8.7, gap2)):
            mid = _band_median(_grab_frame(out, t, 360, 640),
                               360, 640, 0.76, 0.92)
            # A RATIO, not an absolute drop: the pill darkens
            # multiplicatively, and the background moves (Task C
            # drift), so the gap frame's brightness differs from
            # the cue frame's by a few levels — an absolute bar
            # fails on a brighter background even with the pill
            # present (S1 cue2 measured 13.0 vs gap 20.7, a 7.7
            # drop, ratio 0.63). A missing pill gives ~1.0.
            self.assertLess(
                mid, gap * 0.75,
                f"{label}: no pill (band median {mid:.1f} vs "
                f"gap {gap:.1f})")
        # The 0.12s fade: halfway through the fade-in the pill
        # is visibly present but not yet at full darkness. The
        # pre-cue frame (t=0.15, no caption yet) is the reference
        # — it is 0.11s from the fade sample, so the background
        # has barely moved, unlike the t=2.5 gap frame.
        pre = _band_median(_grab_frame(out, 0.15, 360, 640),
                           360, 640, 0.76, 0.92)
        full = _band_median(_grab_frame(out, 1.3, 360, 640),
                            360, 640, 0.76, 0.92)
        mid_fade = _band_median(_grab_frame(out, 0.26, 360, 640),
                                360, 640, 0.76, 0.92)
        self.assertLess(mid_fade, pre * 0.95,
                        "pill fade-in has not started at +0.06s")
        self.assertGreater(mid_fade, full * 1.1,
                           "pill is already at full darkness "
                           "halfway through its fade-in")


# ---------------------------------------------------------- Task C: motion

class TestContinuousMotion(_V163Base):
    def test_motion_inside_one_scene(self):
        # No keywords: no card entrance can inflate the MAPD —
        # this is pure background Ken Burns + subject breathing.
        # The bar is 0.45, NOT 1.5: this render uses the
        # generated gradient, which is smooth by design (a soft
        # radial glow), so its 1s MAPD is ~0.55 — the 1.5 bar
        # belongs to real B-roll at 720p and is verified by
        # scripts/verify_real_cinematic.py (measured 1.531).
        # The frozen 1.62.0 baseline was 0.386, so 0.45 catches
        # a motion regression with margin; the 3s-apart MAPD on
        # this render is 4.1, confirming substantial motion.
        scene = self._scene("s1")
        out = os.path.join(self.tmp, "motion.mp4")
        ok, info = produce_explainer(self._brief([scene]), out)
        self.assertTrue(ok, info)
        f1 = _grab_frame(out, 1.0, 180, 320)
        f2 = _grab_frame(out, 2.0, 180, 320)
        self.assertGreater(
            _mapd(f1, f2), 0.45,
            "frames at t=1.0 and t=2.0 are frozen (MAPD <= 0.45)")
        f3 = _grab_frame(out, 3.0, 180, 320)
        f4 = _grab_frame(out, 4.0, 180, 320)
        self.assertGreater(
            _mapd(f3, f4), 0.45,
            "frames at t=3.0 and t=4.0 are frozen (MAPD <= 0.45)")


# --------------------------------------------- Task C: broll + glass card

class TestBrollSmoothstep(unittest.TestCase):
    def test_zoompan_is_smoothstep_centre_anchored(self):
        chain = build_broll_filter_chain(720, 1280, duration=5.0,
                                         is_video=False)
        self.assertIn("zoompan=", chain)
        # Smoothstep 1.0 -> 1.12x, not the old linear ramp.
        self.assertIn("1+0.12*", chain)
        self.assertIn("3-2*", chain)
        self.assertNotIn("min(zoom+0.0008", chain)
        # Centre-anchored (the old ramp drifted top-left).
        self.assertIn("x='iw/2-(iw/zoom/2)'", chain)
        self.assertIn("y='ih/2-(ih/zoom/2)'", chain)


class TestKenBurnsDrift(unittest.TestCase):
    def test_cinematic_layer1_sets_drift_on_zooms(self):
        c0 = {"id": "c0", "kind": "image", "src": "x.png",
              "duration": 5.0}
        cinematic_layer1_clip(c0, 0)
        self.assertEqual(c0["ken_burns"], "zoom_in")
        self.assertEqual(c0["_kb_drift"], KB_DRIFT_FRACTION)
        c1 = {"id": "c1", "kind": "image", "src": "x.png",
              "duration": 5.0}
        cinematic_layer1_clip(c1, 1)
        self.assertEqual(c1["ken_burns"], "zoom_out")
        # Consecutive scenes drift opposite ways.
        self.assertEqual(c1["_kb_drift"], -KB_DRIFT_FRACTION)
        # Pans already cross the full slack: no drift.
        c2 = {"id": "c2", "kind": "image", "src": "x.png",
              "duration": 5.0}
        cinematic_layer1_clip(c2, 2)
        self.assertEqual(c2["ken_burns"], "pan_left")
        self.assertNotIn("_kb_drift", c2)
        # Video clips: no drift either.
        v = {"id": "v", "kind": "video", "src": "x.mp4",
             "duration": 5.0}
        cinematic_layer1_clip(v, 0)
        self.assertNotIn("_kb_drift", v)

    def test_drift_in_filter_graph(self):
        base = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        media = os.path.join(base, "data", "test_media", "pattern.png")

        def _graph(clip):
            project = {"id": "drift", "version": 1,
                       "output": {"width": 720, "height": 1280,
                                  "fps": 30},
                       "clips": [clip]}
            errors, info = validate_project(project)
            self.assertEqual(errors, [])
            argv, _, _ = build_ffmpeg_args(
                project, info,
                os.path.join(tempfile.gettempdir(), "drift-out.mp4"))
            return argv[argv.index("-filter_complex") + 1]

        drifted = _graph({"id": "a", "kind": "image", "src": media,
                          "duration": 2, "ken_burns": "zoom_in",
                          "_kb_drift": 0.8})
        # The drift multiplies the centre anchor by a CONSTANT
        # (1 + k) — the slack itself grows with the zoom, so an
        # s-weighted drift was too weak early in a zoom_in.
        self.assertIn("(1+(0.8))", drifted)
        # zoom_out carries the same constant drift (the slack
        # closes instead of opening, so no s factor either).
        drifted_out = _graph({"id": "a", "kind": "image", "src": media,
                              "duration": 2, "ken_burns": "zoom_out",
                              "_kb_drift": -0.8})
        self.assertIn("(1+(-0.8))", drifted_out)
        plain = _graph({"id": "a", "kind": "image", "src": media,
                        "duration": 2, "ken_burns": "zoom_in"})
        self.assertIn("x='iw/2-(iw/zoom/2)'", plain)
        self.assertNotIn("(1+(0.8))", plain)


class TestGlassCardTemplate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-v163-card-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accent_bar_pixel(self):
        png = render_glass_card_png(400, 160, accent_bar=True,
                                    accent_color="#F59E0B",
                                    drop_shadow=False)
        path = os.path.join(self.tmp, "accent.png")
        with open(path, "wb") as fh:
            fh.write(png)
        # The bar is a solid amber strip on the card's left edge.
        px = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", "crop=1:1:3:80", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        self.assertEqual(list(px), [245, 158, 11])
        # And the card centre is the dark slate, not amber.
        px2 = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", "crop=1:1:200:80", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        self.assertNotEqual(list(px2), [245, 158, 11])

    def test_card_cache_keys_accent(self):
        plain = get_cached_glass_card(400, 160, project_dir=self.tmp)
        accented = get_cached_glass_card(
            400, 160, accent_bar=True, project_dir=self.tmp)
        self.assertNotEqual(plain, accented)
        again = get_cached_glass_card(
            400, 160, accent_bar=True, project_dir=self.tmp)
        self.assertEqual(accented, again)

    def test_card_shadow_shifts_rect(self):
        ov = glass_card_overlay("card-s", x=60, y=800, w=600, h=122,
                                start=1.0, duration=3.0, width=720,
                                project_dir=self.tmp, drop_shadow=True)
        # The PNG grows by the shadow padding on each side...
        self.assertEqual(png_dimensions(ov["src"]),
                         (600 + 2 * CARD_SHADOW_PAD,
                          122 + 2 * CARD_SHADOW_PAD))
        # ...and the overlay rect shifts by the padding so the
        # CARD still lands at (60, 800).
        self.assertEqual(ov["x"], 60 - CARD_SHADOW_PAD)
        self.assertEqual(ov["y"], 800 - CARD_SHADOW_PAD)
        self.assertEqual(ov["size"],
                         round((600 + 2 * CARD_SHADOW_PAD) / 720, 4))

    def test_short_card_skips_exit_fade(self):
        ov = glass_card_overlay("card-short", x=60, y=800, w=600, h=122,
                                start=1.0, duration=0.3, width=720,
                                project_dir=self.tmp)
        times = [kf["time"] for kf in ov["keyframes"]]
        self.assertEqual(times, [0.0, 0.15, 0.3])
        # The entrance shortens to half the duration and the
        # card stays fully visible to the end (no exit fade).
        self.assertEqual([kf["opacity"] for kf in ov["keyframes"]],
                         [0.0, 1.0, 1.0])


class TestRicherKeywordCard(_V163Base):
    def test_card_structure_and_entrance(self):
        # 720x1280: the two-line card (title + subtitle) only
        # fits at full height; at 360x640 it falls back to one
        # line by design.
        scene = self._scene("s1", keywords=[
            {"text": "سينمائي", "subtitle": "مشهد حقيقي",
             "start": 1.0, "end": 4.0}])
        out = os.path.join(self.tmp, "card.mp4")
        ok, info = produce_explainer(
            self._brief([scene], width=720, height=1280), out)
        self.assertTrue(ok, info)
        cards = [o for o in info["project"]["overlays"]
                 if o["id"].startswith("comp-card-")]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        # The card is at least 60% of the frame wide (the 1.62.0
        # card hugged the measured text, so one word read as a
        # button). size is the PNG fraction, which includes the
        # shadow padding, so it is >= 0.6 by construction.
        self.assertGreaterEqual(card["size"], 0.6)
        # The subtitle line is in the project texts.
        self.assertTrue(
            any(t.get("text") == "مشهد حقيقي"
                for t in info["project"]["texts"]),
            "subtitle line missing from the project texts")
        # The accent bar is in the card PNG: amber at the bar's
        # column, card-relative x=3, vertical centre.
        png_w, png_h = png_dimensions(card["src"])
        # The PNG is the >= 0.6W card plus the shadow padding.
        self.assertGreaterEqual(png_w, 432 + 2 * CARD_SHADOW_PAD)
        bar_x = card["x"] + CARD_SHADOW_PAD + 3
        bar_y = card["y"] + CARD_SHADOW_PAD + (png_h - 2 * CARD_SHADOW_PAD) // 2
        px = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", card["src"],
             "-vf", f"crop=1:1:{CARD_SHADOW_PAD + 3}:"
                    f"{CARD_SHADOW_PAD + (png_h - 2 * CARD_SHADOW_PAD) // 2}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True).stdout
        # Amber #F59E0B (245, 158, 11) within a per-channel
        # tolerance: ffmpeg's PNG->rgb24 path shifts each channel
        # by a few levels (measured 240, 155, 11), while the PNG
        # itself holds the exact value (verified live). A
        # tolerance of 10 still distinguishes amber from the dark
        # card background (~22, 32, 50).
        for got, want in zip(px, (245, 158, 11)):
            self.assertLess(abs(got - want), 10,
                            f"accent bar pixel {list(px)} != amber")
        # Entrance smoothness: the bar's red channel ramps from
        # the background (card absent) through the mid-fade blend
        # to full amber (card settled).
        r_before = _region_mean_r(_grab_frame(out, 0.9, 720, 1280),
                                  720, 1280, bar_x, bar_y)
        r_mid = _region_mean_r(_grab_frame(out, 1.12, 720, 1280),
                               720, 1280, bar_x, bar_y)
        r_settled = _region_mean_r(_grab_frame(out, 1.5, 720, 1280),
                                   720, 1280, bar_x, bar_y)
        self.assertGreater(r_mid, r_before + 15,
                           f"card has not started fading in "
                           f"(R {r_before:.0f} -> {r_mid:.0f})")
        self.assertGreater(r_settled, r_mid + 15,
                           f"card fade-in is not progressive "
                           f"(R {r_mid:.0f} -> {r_settled:.0f})")


if __name__ == "__main__":
    unittest.main()
