"""Tests for AgentCut v1.62.0: the 3-layer cinematic compositor.

Covers compositor.py (gradient, subject keyframes, zones, Layer 1
clips, glass cards), the produce_explainer / produce_video cinematic
wiring (including its structured errors), and the real-render
acceptance tests: a multi-layer 720p project (blurred B-roll
background + transparent PNG foreground subject + vector_ui glass
card + Arabic text + ASS karaoke) with a render_contact_sheet, and
the parallel-path A/V-sync regression test (the concat-FILTER join
must not shift audio by a frame at scene boundaries).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.compositor import (
    BREATHE_AMPLITUDE,
    CARD_FADE_IN,
    CARD_FADE_OUT,
    CARD_SLIDE_PX,
    CINEMATIC_TREATMENT,
    COMPOSITOR_ZONES,
    GRADIENT_TREATMENT,
    KEN_BURNS_CYCLE,
    PUNCH_HALF_WIDTH,
    PUNCH_INTERVAL,
    PUNCH_PEAK,
    apply_compositor_to_project,
    breathe_float_y,
    cinematic_layer1_clip,
    glass_card_overlay,
    png_dimensions,
    subject_keyframes,
    subject_overlay,
    write_cinematic_gradient,
    zone_for,
    zone_rect,
)
from agentcut.render import (
    build_ffmpeg_args,
    produce_explainer,
    produce_video,
    render_contact_sheet,
    render_project,
)
from agentcut.validate import probe_media, validate_project
from agentcut.vector_ui import encode_rgba_png


def _write_transparent_subject(path, size=400):
    """A procedural transparent PNG: a solid red disc on a fully
    transparent background — the test stand-in for an
    AgentMedia remove_background result."""
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


def _frame_mean_var(video_path, time_s):
    """(mean, variance) of a frame's RGB pixels, sampled at
    180x320 — mean near 0 means a black frame, variance near 0
    means a flat/empty one. Raw rgb24 via ffmpeg (the repo's
    _crop_mean pattern); signalstats does not print YAVG on
    ffmpeg 8.1.2 without a metadata print filter."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(time_s), "-i", video_path,
           "-vf", "scale=180:320", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    assert len(data) == 180 * 320 * 3, f"short frame: {len(data)}"
    n = len(data)
    mean = sum(data) / n
    var = sum((b - mean) ** 2 for b in data) / n
    return mean, var


def _write_beep_wav(path, duration=2.0, beep_at=0.05, beep_len=0.1,
                    freq=880.0, rate=48000):
    """A WAV with one short sine beep at beep_at and silence
    elsewhere — the A/V-sync probe: the beep onset marks the
    scene's audio position exactly."""
    import math
    import struct
    import wave
    frames = int(duration * rate)
    samples = bytearray()
    for i in range(frames):
        t = i / rate
        if beep_at <= t < beep_at + beep_len:
            v = int(12000 * math.sin(2 * math.pi * freq * t))
        else:
            v = 0
        samples += struct.pack("<h", v)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(samples))
    return path


def _write_simple_srt(path, duration=2.0, text="نص تجريبي"):
    secs = int(duration)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"1\n00:00:00,000 --> 00:00:{secs:02d},000\n{text}\n")
    return path


def _beep_onsets(video_path, expected, rate=48000, window_s=0.01):
    """Beep onset times in the output audio: decode to mono f32,
    take RMS per window, and find the first loud window at or
    after each expected onset minus half a window. Returns one
    onset per expected time (or None when no beep is found)."""
    import struct
    cmd = ["ffmpeg", "-v", "error", "-i", video_path,
           "-vn", "-ac", "1", "-ar", str(rate),
           "-f", "f32le", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    samples = struct.unpack(f"<{len(data) // 4}f", data)
    win = int(rate * window_s)
    rms = []
    for i in range(0, len(samples) - win + 1, win):
        chunk = samples[i:i + win]
        rms.append((sum(x * x for x in chunk) / win) ** 0.5)
    peak = max(rms) if rms else 0.0
    assert peak > 0.01, "output audio is silent"
    thresh = peak * 0.2
    onsets = []
    for exp in expected:
        start_idx = max(0, int((exp - 0.05) / window_s))
        onset = None
        for j in range(start_idx, len(rms)):
            if rms[j] > thresh:
                onset = j * window_s
                break
        onsets.append(onset)
    return onsets


def _stream_durations(path):
    """(video_duration, audio_duration) from the raw ffprobe
    streams; None when a stream has no duration."""
    probe = probe_media(path)
    assert probe is not None
    video_dur = audio_dur = None
    for stream in probe["raw"].get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("duration"):
            video_dur = float(stream["duration"])
        elif stream.get("codec_type") == "audio" and stream.get("duration"):
            audio_dur = float(stream["duration"])
    return video_dur, audio_dur, probe


class TestCompositorUnits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-v162-")
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.media = os.path.join(base, "data", "test_media")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gradient_deterministic_and_sized(self):
        a = os.path.join(self.tmp, "g1.png")
        b = os.path.join(self.tmp, "g2.png")
        write_cinematic_gradient(a, 720, 1280)
        write_cinematic_gradient(b, 720, 1280)
        with open(a, "rb") as fh:
            ba = fh.read()
        with open(b, "rb") as fh:
            bb = fh.read()
        self.assertEqual(ba, bb, "the gradient must be byte-deterministic")
        self.assertEqual(png_dimensions(a), (720, 1280))
        # A rich gradient compresses far larger than a flat colour.
        self.assertGreater(len(ba), 5000)

    def test_zones(self):
        # zone_rect returns (x, y, w, h); zone_for takes a 0..1 fraction.
        self.assertEqual(zone_rect("header", 720, 1280), (0, 64, 720, 166))
        self.assertEqual(zone_rect("diagram", 720, 1280), (0, 282, 720, 435))
        self.assertEqual(zone_rect("callout", 720, 1280), (0, 768, 720, 128))
        self.assertEqual(zone_rect("subtitles", 720, 1280),
                         (0, 973, 720, 205))
        self.assertEqual(zone_for(0.10), "header")
        self.assertEqual(zone_for(0.39), "diagram")
        self.assertEqual(zone_for(0.65), "callout")
        self.assertEqual(zone_for(0.85), "subtitles")
        self.assertIsNone(zone_for(0.20))
        with self.assertRaises(KeyError):
            zone_rect("nowhere", 720, 1280)

    def test_subject_keyframes_entrance_and_punch(self):
        # v1.63.0: breathing keyframes join the timeline, so the
        # test locates keyframes by time instead of by index.
        kfs = subject_keyframes(8.0, 0.5, 360.0, 500.0, 1.0, 720)
        by_time = {kf["time"]: kf for kf in kfs}
        # Entrance: invisible and slightly small, landing at base.
        self.assertEqual(by_time[0.0]["opacity"], 0.0)
        self.assertAlmostEqual(by_time[0.0]["scale"], 0.46, places=3)
        self.assertEqual(by_time[0.6]["opacity"], 1.0)
        self.assertEqual(by_time[0.6]["scale"], 0.5)
        self.assertEqual(by_time[0.6]["easing"], "spring")
        # Punch at 5s: base -> 1.15x base -> base over 0.8s.
        self.assertEqual(by_time[5.0]["scale"], round(0.5 * PUNCH_PEAK, 4))
        self.assertEqual(by_time[4.6]["time"],
                         PUNCH_INTERVAL - PUNCH_HALF_WIDTH)
        self.assertEqual(by_time[5.4]["time"],
                         PUNCH_INTERVAL + PUNCH_HALF_WIDTH)
        self.assertEqual(by_time[5.0]["easing"], "ease_in_out")
        # Breathing (v1.63.0): a +/-2% scale oscillation at 1s
        # steps from t=1.0, skipping the punch window — there is
        # no breathing keyframe at 5.0 (the punch owns it).
        for t in (1.0, 2.0, 3.0, 4.0, 6.0, 7.0):
            self.assertIn(t, by_time)
            self.assertAlmostEqual(by_time[t]["scale"], 0.5,
                                   delta=0.5 * BREATHE_AMPLITUDE + 0.001)
            self.assertEqual(by_time[t]["opacity"], 1.0)
        # The peak is ABSOLUTE scale, never stacked: at most 0.85*1.15.
        self.assertLessEqual(max(kf["scale"] for kf in kfs), 0.85 * 1.15)
        # Times strictly increase and the last keyframe lands on
        # the duration at base scale.
        times = [kf["time"] for kf in kfs]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[-1], 8.0)
        self.assertEqual(by_time[8.0]["scale"], 0.5)

    def test_subject_keyframes_grow_in_place(self):
        cx, cy, width = 360.0, 500.0, 720
        kfs = subject_keyframes(12.0, 0.5, cx, cy, 1.0, width)
        for kf in kfs:
            # The subject's centre stays fixed horizontally as
            # its scale changes (within 1px of rounding):
            # x + scale*W/2 == cx. Vertically the centre follows
            # the breathing float (v1.63.0): y + scale*W/2 ==
            # cy + breathe_float_y(t) — the float is a deliberate
            # bob, not a drift off-centre.
            centre_x = kf["x"] + kf["scale"] * width / 2.0
            centre_y = kf["y"] + kf["scale"] * width * 1.0 / 2.0
            self.assertAlmostEqual(centre_x, cx, delta=1.0)
            self.assertAlmostEqual(
                centre_y, cy + breathe_float_y(kf["time"], width),
                delta=1.0)

    def test_subject_keyframes_short_duration(self):
        kfs = subject_keyframes(1.0, 0.5, 360.0, 500.0, 1.0, 720)
        self.assertEqual([kf["time"] for kf in kfs], [0.0, 1.0])
        self.assertEqual(kfs[1]["opacity"], 1.0)

    def test_subject_overlay_placements(self):
        src = _write_transparent_subject(os.path.join(self.tmp, "subj.png"))
        for placement in ("center", "left", "right"):
            ov = subject_overlay(f"ov-{placement}", src, 0.0, 8.0,
                                 720, 1280, placement=placement,
                                 base_size=0.5)
            self.assertEqual(ov["kind"], "image")
            self.assertEqual(ov["size"], 0.5)
            self.assertGreaterEqual(len(ov["keyframes"]), 2)
            self.assertEqual(ov["duration"], 8.0)
        with self.assertRaises(ValueError):
            subject_overlay("bad", src, 0.0, 8.0, 720, 1280,
                            placement="top")
        # Left and right sit on opposite thirds of the frame.
        left = subject_overlay("l", src, 0.0, 8.0, 720, 1280,
                               placement="left", base_size=0.4)
        right = subject_overlay("r", src, 0.0, 8.0, 720, 1280,
                                placement="right", base_size=0.4)
        self.assertLess(left["keyframes"][1]["x"],
                        right["keyframes"][1]["x"])

    def test_layer1_clip(self):
        clip = {"id": "c0", "kind": "image", "src": "x.png",
                "duration": 5.0}
        cinematic_layer1_clip(clip, 0)
        self.assertEqual(clip["treatment"], CINEMATIC_TREATMENT)
        self.assertEqual(clip["ken_burns"], KEN_BURNS_CYCLE[0])
        # The cycle rotates per scene index.
        for i in range(5):
            c = {"id": f"c{i}", "kind": "image", "src": "x.png",
                 "duration": 5.0}
            cinematic_layer1_clip(c, i)
            self.assertEqual(c["ken_burns"],
                             KEN_BURNS_CYCLE[i % len(KEN_BURNS_CYCLE)])
        # An explicit ken_burns always wins over the cycle.
        keep = {"id": "k", "kind": "image", "src": "x.png",
                "duration": 5.0, "ken_burns": "pan_right"}
        cinematic_layer1_clip(keep, 0)
        self.assertEqual(keep["ken_burns"], "pan_right")
        # Video clips get the treatment but no Ken Burns.
        vid = {"id": "v", "kind": "video", "src": "x.mp4",
               "duration": 5.0}
        cinematic_layer1_clip(vid, 0)
        self.assertEqual(vid["treatment"], CINEMATIC_TREATMENT)
        self.assertNotIn("ken_burns", vid)

    def test_glass_card_overlay_exact_rect(self):
        ov = glass_card_overlay("card-1", x=60, y=800, w=600, h=122,
                                start=1.0, duration=3.0, width=720,
                                project_dir=self.tmp)
        self.assertEqual(ov["kind"], "image")
        self.assertEqual(ov["x"], 60)
        self.assertEqual(ov["y"], 800)
        self.assertEqual(ov["size"], round(600 / 720, 4))
        self.assertEqual(ov["start"], 1.0)
        self.assertEqual(ov["duration"], 3.0)
        # v1.63.0: the card slides up 18px with a spring ease and
        # fades in over 0.25s, then fades out over the last 0.20s
        # — no more hard cut. Keyframe times are scene-relative.
        kfs = ov["keyframes"]
        self.assertEqual([kf["time"] for kf in kfs],
                         [0.0, CARD_FADE_IN, 3.0 - CARD_FADE_OUT, 3.0])
        self.assertEqual(kfs[0]["opacity"], 0.0)
        self.assertEqual(kfs[0]["y"], 800 + CARD_SLIDE_PX)
        self.assertEqual(kfs[1]["opacity"], 1.0)
        self.assertEqual(kfs[1]["y"], 800)
        self.assertEqual(kfs[1]["easing"], "spring")
        # The slide is spring but the fade is linear: a spring
        # fade completes in ~0.05s (a pop), not the specified
        # 0.25s fade.
        self.assertEqual(kfs[1]["opacity_easing"], "linear")
        self.assertEqual(kfs[2]["opacity"], 1.0)
        self.assertEqual(kfs[2]["y"], 800)
        self.assertEqual(kfs[3]["opacity"], 0.0)
        self.assertEqual(kfs[3]["y"], 800)
        # drop_shadow=False, so the PNG is exactly w x h and the
        # overlay rect matches the card rect.
        self.assertTrue(os.path.isfile(ov["src"]))
        self.assertEqual(png_dimensions(ov["src"]), (600, 122))

    def test_apply_compositor_validates(self):
        grad = os.path.join(self.tmp, "grad.png")
        write_cinematic_gradient(grad, 720, 1280)
        subject = _write_transparent_subject(
            os.path.join(self.tmp, "subj.png"))
        project = {
            "id": "comp", "version": 1,
            "output": {"width": 720, "height": 1280, "fps": 30},
            "clips": [
                {"id": "c1", "kind": "image", "src": grad,
                 "duration": 6.0},
                {"id": "c2", "kind": "image",
                 "src": os.path.join(self.media, "pattern.jpg"),
                 "duration": 6.0},
            ],
            "texts": [{"text": "عنوان", "start": 0.5, "end": 4.0,
                       "size": 64, "x": "center", "y": 100}],
        }
        apply_compositor_to_project(
            project,
            clip_windows=[(0.0, 6.0), (6.0, 6.0)],
            subjects=[{"clip_index": 0, "src": subject,
                       "placement": "center", "base_size": 0.5}],
            title_cards=[{"y": 100, "size": 64, "start": 0.5,
                          "end": 4.0}],
            project_dir=self.tmp)
        self.assertEqual(len(project["overlays"]), 2)
        self.assertEqual(project["overlays"][0]["id"], "comp-subject-0")
        self.assertEqual(project["overlays"][1]["id"], "comp-title-card-0")
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        argv, total, _ = build_ffmpeg_args(
            project, info, os.path.join(self.tmp, "out.mp4"))
        fc = argv[argv.index("-filter_complex") + 1]
        self.assertIn("gblur", fc)
        self.assertIn("zoompan", fc)
        self.assertEqual(total, 12.0)

    def test_apply_compositor_treatments_override(self):
        # treatments={i: t} overrides the default cinematic
        # treatment for clip i only; other clips keep it.
        project = {
            "id": "comp-t", "version": 1,
            "output": {"width": 720, "height": 1280, "fps": 30},
            "clips": [
                {"id": "c1", "kind": "image",
                 "src": os.path.join(self.media, "pattern.jpg"),
                 "duration": 6.0},
                {"id": "c2", "kind": "image",
                 "src": os.path.join(self.media, "pattern.jpg"),
                 "duration": 6.0},
            ],
        }
        apply_compositor_to_project(
            project,
            clip_windows=[(0.0, 6.0), (6.0, 6.0)],
            treatments={1: GRADIENT_TREATMENT})
        self.assertEqual(project["clips"][0]["treatment"],
                         CINEMATIC_TREATMENT)
        self.assertEqual(project["clips"][1]["treatment"],
                         GRADIENT_TREATMENT)


class TestCinematicWiringErrors(unittest.TestCase):
    """Structured errors from the cinematic producers — all fail
    before any render starts."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-v162-")
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.media = os.path.join(base, "data", "test_media")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explainer_bad_style(self):
        ok, err = produce_explainer(
            {"style": "noir", "scenes": []},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "INVALID_SCHEMA")

    def test_explainer_cinematic_nonlocal_broll(self):
        ok, err = produce_explainer(
            {"style": "cinematic", "id": "t",
             "output": {"width": 360, "height": 640, "fps": 30},
             "scenes": [{"id": "s1", "title": "t",
                         "narration_text": "كلام قصير.",
                         "broll": "mountains sunset"}]},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "FILE_NOT_FOUND")
        self.assertIn("cinematic mode", err["message"])

    def test_explainer_cinematic_missing_subject(self):
        ok, err = produce_explainer(
            {"style": "cinematic", "id": "t",
             "output": {"width": 360, "height": 640, "fps": 30},
             "scenes": [{"id": "s1", "title": "t",
                         "narration_text": "كلام قصير.",
                         "subject": "no-such-subject.png"}]},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "FILE_NOT_FOUND")

    def test_explainer_cinematic_bad_placement(self):
        subject = _write_transparent_subject(
            os.path.join(self.tmp, "subj.png"))
        ok, err = produce_explainer(
            {"style": "cinematic", "id": "t",
             "output": {"width": 360, "height": 640, "fps": 30},
             "scenes": [{"id": "s1", "title": "t",
                         "narration_text": "كلام قصير.",
                         "subject": subject,
                         "subject_placement": "top"}]},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "INVALID_SCHEMA")

    def test_produce_video_bad_style(self):
        ok, err = produce_video(
            {"style": "noir",
             "clips": [{"src": os.path.join(self.media, "pattern.jpg"),
                        "duration": 2.0}]},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "INVALID_SCHEMA")

    def test_produce_video_cinematic_missing_subject(self):
        ok, err = produce_video(
            {"style": "cinematic",
             "clips": [{"src": os.path.join(self.media, "pattern.jpg"),
                        "duration": 2.0,
                        "subject": "no-such-subject.png"}]},
            os.path.join(self.tmp, "x.mp4"))
        self.assertFalse(ok)
        self.assertEqual(err["code"], "FILE_NOT_FOUND")


class TestCompositorRender(unittest.TestCase):
    """Real renders: the multi-layer 720p compositor project and
    the parallel-path A/V-sync regression test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-v162-")
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.media = os.path.join(base, "data", "test_media")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multilayer_render_720p(self):
        grad = os.path.join(self.tmp, "grad.png")
        write_cinematic_gradient(grad, 720, 1280)
        subject = _write_transparent_subject(
            os.path.join(self.tmp, "subj.png"))
        project = {
            "id": "comp-720p", "version": 1,
            "output": {"width": 720, "height": 1280, "fps": 30},
            "clips": [
                {"id": "c1", "kind": "image",
                 "src": os.path.join(self.media, "pattern.jpg"),
                 "duration": 6.0},
                {"id": "c2", "kind": "image", "src": grad,
                 "duration": 6.0},
            ],
            "texts": [{"text": "عنوان عربي", "start": 0.5, "end": 4.0,
                       "size": 64, "x": "center", "y": 100}],
            "captions": [{
                "start": 0.0, "end": 5.0, "text": "هذا اختبار للكتابة",
                "captions_style": "karaoke",
                "word_timings": [
                    {"word": "هذا", "start": 0.0, "end": 1.6},
                    {"word": "اختبار", "start": 1.6, "end": 3.2},
                    {"word": "للكتابة", "start": 3.2, "end": 5.0},
                ],
            }],
        }
        # The generated gradient (clip 1) gets GRADIENT_TREATMENT,
        # not the default B-roll darkening: it is already dark with
        # a baked vignette, and the darkening crushed its frame to
        # a mean of 5.12 vs the 5.0 bar on FFmpeg 8.x — and 0.67
        # (black) on FFmpeg 6.1.1 in CI run 44750049307.
        apply_compositor_to_project(
            project,
            clip_windows=[(0.0, 6.0), (6.0, 6.0)],
            subjects=[{"clip_index": 0, "src": subject,
                       "placement": "center", "base_size": 0.5}],
            title_cards=[{"y": 100, "size": 64, "start": 0.5,
                          "end": 4.0}],
            project_dir=self.tmp,
            treatments={1: GRADIENT_TREATMENT})
        out = os.path.join(self.tmp, "comp-720p.mp4")
        ok, info = render_project(project, out, video_preset="veryfast",
                                  timeout=600)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out))
        probe = probe_media(out)
        self.assertIsNotNone(probe)
        self.assertAlmostEqual(probe["duration"], 12.0, delta=0.5)
        self.assertEqual(probe["width"], 720)
        self.assertEqual(probe["height"], 1280)
        self.assertTrue(probe["has_video"])
        video = [s for s in probe["raw"]["streams"]
                 if s.get("codec_type") == "video"][0]
        self.assertEqual(video["codec_name"], "h264")
        # Frames must not be black or empty (RGB mean/variance):
        # one frame per layer-1 background.
        for t in (2.0, 8.0):
            mean, var = _frame_mean_var(out, t)
            self.assertGreater(mean, 5.0, f"frame at {t}s looks black")
            self.assertGreater(var, 10.0, f"frame at {t}s looks flat")
        # The contact sheet renders from the finished video.
        sheet = os.path.join(self.tmp, "sheet.png")
        ok, sinfo = render_contact_sheet(out, sheet, project)
        self.assertTrue(ok, sinfo)
        self.assertTrue(os.path.isfile(sheet))
        self.assertGreater(os.path.getsize(sheet), 10000)

    def test_parallel_av_sync_no_frame_shift(self):
        """Regression test for the parallel-path audio shift
        (Task 2.1): three scenes through the parallel path must
        keep audio and video aligned within one frame. Each
        scene's narration is a WAV with a beep 0.05s in, so the
        beep onsets in the output mark the audio alignment
        exactly. The old concat-DEMUXER join shifted audio one
        video frame late at EVERY boundary — two boundaries here
        would delay the third beep by ~2 frames. Container
        durations alone cannot catch a shift (a late-shifted
        track is cut to the same length by -t), so the beeps are
        the assertion; the durations are a sanity check."""
        out = os.path.join(self.tmp, "parallel-sync.mp4")
        scenes = []
        for i in range(1, 4):
            wav = _write_beep_wav(os.path.join(self.tmp, f"n{i}.wav"))
            srt = _write_simple_srt(os.path.join(self.tmp, f"c{i}.srt"))
            scenes.append({"id": f"s{i}", "title": f"المشهد {i}",
                           "narration": wav, "captions": srt})
        brief = {
            "id": "parallel_sync",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": os.path.join(self.media, "poster.png"),
            "title": "مزامنة متوازية",
            "parallel": True,
            "scenes": scenes,
        }
        ok, info = produce_explainer(brief, out)
        self.assertTrue(ok, info)
        self.assertTrue(info.get("parallel"))
        self.assertEqual(len(info["scenes"]), 3)
        video_dur, audio_dur, probe = _stream_durations(out)
        self.assertIsNotNone(video_dur, "no video stream duration")
        self.assertIsNotNone(audio_dur, "no audio stream duration")
        one_frame = 1.0 / 30.0
        self.assertLess(abs(audio_dur - video_dur), one_frame,
                        f"audio {audio_dur} vs video {video_dur}")
        self.assertAlmostEqual(probe["duration"], info["duration"],
                               delta=0.5)
        expected = [0.05, 2.05, 4.05]
        onsets = _beep_onsets(out, expected)
        for exp, got in zip(expected, onsets):
            self.assertIsNotNone(got, f"no beep found near {exp}s")
            self.assertLess(abs(got - exp), one_frame,
                            f"beep at {got}s, expected {exp}s: "
                            "audio shifted by a frame or more")

    def test_scene_cache_hit_on_second_parallel_run(self):
        """The scene cache (Task 2.3): a second identical parallel
        render reuses both chunks instead of re-rendering them.
        The scenes use a STABLE narration file (not
        narration_text): the cache hash mixes in the narration
        file's mtime, and TTS rewrites its file on every run,
        which correctly invalidates the cache."""
        out = os.path.join(self.tmp, "parallel-cache.mp4")
        voice = os.path.join(self.media, "one-button-voice.wav")
        srt = _write_simple_srt(os.path.join(self.tmp, "c.srt"),
                                duration=5.0)
        brief = {
            "id": "parallel_cache",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": os.path.join(self.media, "poster.png"),
            "title": "ذاكرة متوازية",
            "parallel": True,
            "draft": True,
            "scenes": [
                {"id": "s1", "title": "المشهد 1", "narration": voice,
                 "captions": srt},
                {"id": "s2", "title": "المشهد 2", "narration": voice,
                 "captions": srt},
            ],
        }
        ok, first = produce_explainer(brief, out)
        self.assertTrue(ok, first)
        self.assertEqual(first.get("cache_hits"), 0)
        ok, second = produce_explainer(brief, out)
        self.assertTrue(ok, second)
        self.assertEqual(second.get("cache_hits"), 2)


if __name__ == "__main__":
    unittest.main()
