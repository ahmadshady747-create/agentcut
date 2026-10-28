"""AgentCut tests: validation, real renders, preview, failure cases."""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.render import poster_frame, preview_project, render_project
from agentcut.validate import probe_media, validate_project

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def example():
    with open(os.path.join(BASE, "examples", "basic.json")) as fh:
        return json.load(fh)


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout)


class TestAgentCut(unittest.TestCase):
    def test_example_validates(self):
        errors, info = validate_project(example())
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 8.0, places=2)

    def test_example_renders_real_mp4(self):
        out = os.path.join(BASE, "data", "test_output.mp4")
        ok, info = render_project(example(), out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        streams = {s["codec_type"] for s in probe["streams"]}
        self.assertIn("video", streams)
        self.assertIn("audio", streams)
        duration = float(probe["format"]["duration"])
        self.assertAlmostEqual(duration, 8.0, delta=0.5)
        video = [s for s in probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (360, 640))

    def test_preview_produces_frames(self):
        out_dir = os.path.join(BASE, "data", "test_preview")
        ok, info = preview_project(example(), out_dir)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(info["proxy"]))
        self.assertGreaterEqual(len(info["frames"]), 3)
        for frame in info["frames"]:
            self.assertTrue(os.path.isfile(frame["path"]))
        self.assertTrue(os.path.isfile(info["contact_sheet"]))

    def test_missing_file_is_structured_error(self):
        project = example()
        project["clips"] = [{"id": "x", "kind": "video",
                             "src": "data/test_media/does-not-exist.mp4",
                             "trim_in": 0, "trim_out": 1}]
        errors, _ = validate_project(project)
        self.assertTrue(any(e["code"] == "FILE_NOT_FOUND" for e in errors))

    def test_out_of_range_clip_is_structured_error(self):
        project = example()
        project["clips"] = [{"id": "x", "kind": "video",
                             "src": "data/test_media/clip1.mp4",
                             "trim_in": 0, "trim_out": 999}]
        errors, _ = validate_project(project)
        self.assertTrue(any(e["code"] == "CLIP_OUT_OF_RANGE" for e in errors))

    def test_empty_timeline_is_structured_error(self):
        project = example()
        project["clips"] = []
        errors, _ = validate_project(project)
        self.assertTrue(any(e["code"] == "EMPTY_TIMELINE" for e in errors))

    def test_crossfade_renders_with_overlap(self):
        with open(os.path.join(BASE, "examples", "crossfade.json")) as fh:
            project = json.load(fh)
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 5.5, places=2)
        out = os.path.join(BASE, "data", "test_crossfade.mp4")
        ok, render_info = render_project(project, out)
        self.assertTrue(ok, render_info)
        probe = ffprobe(out)
        duration = float(probe["format"]["duration"])
        self.assertAlmostEqual(duration, 5.5, delta=0.5)
        video = [s for s in probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (360, 640))

    def test_audio_start_offset_renders_and_validates(self):
        project = example()
        project["audio"] = [{"src": "data/test_media/clip1.mp4",
                             "kind": "music", "volume": 0.3, "start": 2.0}]
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_offset.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)
        bad = example()
        bad["audio"] = [{"src": "data/test_media/clip1.mp4",
                         "kind": "music", "start": 99}]
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "CLIP_OUT_OF_RANGE" for e in errors))

    def test_sidechain_ducking_renders(self):
        project = example()
        project["audio"] = [
            {"src": "data/test_media/clip1.mp4", "kind": "music", "volume": 0.8},
            {"src": "data/test_media/clip2.mp4", "kind": "voice", "volume": 1.0, "start": 2.0},
        ]
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        from agentcut.render import build_ffmpeg_args
        import shutil
        argv, _, _text_dir = build_ffmpeg_args(project, info, "/tmp/x.mp4")
        if _text_dir:
            shutil.rmtree(_text_dir, ignore_errors=True)
        self.assertIn("sidechaincompress", " ".join(argv))
        out = os.path.join(BASE, "data", "test_duck.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)
        self.assertIn("audio", {s_["codec_type"] for s_ in probe["streams"]})

    def test_srt_import_renders(self):
        with open(os.path.join(BASE, "examples", "srt.json")) as fh:
            project = json.load(fh)
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertEqual(len(info["captions"]), 2)
        out = os.path.join(BASE, "data", "test_srt.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)
        bad = dict(project)
        bad["captions_file"] = "examples/does-not-exist.srt"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "FILE_NOT_FOUND" for e in errors))

    def test_landscape_and_square_examples_render(self):
        for name, (w, h) in (("landscape", (1280, 720)), ("square", (720, 720))):
            with open(os.path.join(BASE, "examples", f"{name}.json")) as fh:
                project = json.load(fh)
            errors, _ = validate_project(project)
            self.assertEqual(errors, [])
            out = os.path.join(BASE, "data", f"test_{name}.mp4")
            ok, info = render_project(project, out)
            self.assertTrue(ok, info)
            probe = ffprobe(out)
            video = [s_ for s_ in probe["streams"] if s_["codec_type"] == "video"][0]
            self.assertEqual((video["width"], video["height"]), (w, h))
            self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)

    def test_image_crossfade_is_yuv420p(self):
        project = {
            "id": "img-xfade", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [
                {"id": "i1", "kind": "image", "src": "data/test_media/poster.png", "duration": 1},
                {"id": "i2", "kind": "image", "src": "data/test_media/poster.png", "duration": 1, "transition": "crossfade"},
            ],
        }
        out = os.path.join(BASE, "data", "test_img_xfade.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        video = [st for st in info["probe"]["raw"]["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")

    def test_apostrophe_text_renders(self):
        project = {
            "id": "apostrophe", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": "data/test_media/poster.png", "duration": 2}],
            "texts": [{"text": "Ballon d'Or 2026: 100%", "start": 0.2, "end": 1.8, "size": 36, "x": "center", "y": 80}],
        }
        out = os.path.join(BASE, "data", "test_apostrophe.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)

    def test_ken_burns_renders_and_moves(self):
        with open(os.path.join(BASE, "examples", "kenburns.json")) as fh:
            project = json.load(fh)
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_kenburns.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 3.0, delta=0.5)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        # First and last frames must differ: a static image would not.
        for t, name in ((0.1, "kb_first.png"), (2.9, "kb_last.png")):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", out,
                            "-frames:v", "1", os.path.join(BASE, "data", name)],
                           check=True, timeout=60)
        with open(os.path.join(BASE, "data", "kb_first.png"), "rb") as fh:
            first = fh.read()
        with open(os.path.join(BASE, "data", "kb_last.png"), "rb") as fh:
            last = fh.read()
        self.assertNotEqual(first, last)
        bad = json.loads(json.dumps(project))
        bad["clips"][0]["ken_burns"] = "spin"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        bad2 = example()
        bad2["clips"][0]["ken_burns"] = "zoom_in"
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_crop_fill_differs_from_contain(self):
        with open(os.path.join(BASE, "examples", "cover.json")) as fh:
            project = json.load(fh)
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_cover.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        contain = json.loads(json.dumps(project))
        contain["clips"][0]["fit"] = "contain"
        out2 = os.path.join(BASE, "data", "test_contain.mp4")
        ok, _ = render_project(contain, out2)
        self.assertTrue(ok)
        for t, name, src in ((1.0, "cover_frame.png", out), (1.0, "contain_frame.png", out2)):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", src,
                            "-frames:v", "1", os.path.join(BASE, "data", name)],
                           check=True, timeout=60)
        with open(os.path.join(BASE, "data", "cover_frame.png"), "rb") as fh:
            cover_bytes = fh.read()
        with open(os.path.join(BASE, "data", "contain_frame.png"), "rb") as fh:
            contain_bytes = fh.read()
        self.assertNotEqual(cover_bytes, contain_bytes)
        bad = json.loads(json.dumps(project))
        bad["clips"][0]["fit"] = "stretch"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_wipe_transition_renders_with_overlap(self):
        with open(os.path.join(BASE, "examples", "wipe.json")) as fh:
            project = json.load(fh)
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 5.5, places=2)
        out = os.path.join(BASE, "data", "test_wipe.mp4")
        ok, render_info = render_project(project, out)
        self.assertTrue(ok, render_info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 5.5, delta=0.5)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        bad = json.loads(json.dumps(project))
        bad["clips"][0]["transition"] = "slide_right"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        bad2 = json.loads(json.dumps(project))
        bad2["clips"][1]["transition"] = "dissolve"
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_text_styling_renders(self):
        project = {
            "id": "textstyle", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": "data/test_media/pattern.png", "duration": 2}],
            "texts": [{"text": "Styled", "start": 0.2, "end": 1.8, "size": 48,
                       "color": "#FFFF00", "box": False, "position": "center"}],
        }
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_textstyle.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)
        bad = json.loads(json.dumps(project))
        bad["texts"][0]["color"] = "notacolor"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        bad2 = json.loads(json.dumps(project))
        bad2["texts"][0]["position"] = "middle"
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        bad3 = json.loads(json.dumps(project))
        bad3["texts"][0]["box"] = "yes"
        errors, _ = validate_project(bad3)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_clip_speed_and_audio_fades(self):
        project = {
            "id": "speed", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "v1", "kind": "video", "src": "data/test_media/clip1.mp4",
                       "trim_in": 0, "trim_out": 4, "speed": 2.0}],
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 2.0, places=2)
        out = os.path.join(BASE, "data", "test_speed.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)
        self.assertIn("audio", {st["codec_type"] for st in probe["streams"]})
        bad = json.loads(json.dumps(project))
        bad["clips"][0]["speed"] = 0.1
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        bad2 = example()
        bad2["clips"][1]["speed"] = 2.0
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        faded = example()
        faded["audio"] = [{"src": "data/test_media/clip1.mp4", "kind": "music",
                           "volume": 0.8, "fade_in": 1.0, "fade_out": 1.0}]
        errors, _ = validate_project(faded)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_fades.mp4")
        ok, _ = render_project(faded, out2)
        self.assertTrue(ok)
        probe = ffprobe(out2)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)
        bad3 = example()
        bad3["audio"] = [{"src": "data/test_media/clip1.mp4", "kind": "music",
                          "fade_in": 5.0, "fade_out": 5.0}]
        errors, _ = validate_project(bad3)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_poster_frame_export(self):
        project = example()
        out = os.path.join(BASE, "data", "test_poster.png")
        ok, info = poster_frame(project, out, time=1.0)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual((info["width"], info["height"]), (360, 640))
        probe = ffprobe(out)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (360, 640))
        ok, info = poster_frame(project, os.path.join(BASE, "data", "test_poster2.png"))
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info["time"], 4.0, places=2)
        ok, info = poster_frame(project, os.path.join(BASE, "data", "test_poster3.png"), time=99)
        self.assertFalse(ok)
        self.assertEqual(info["code"], "CLIP_OUT_OF_RANGE")

    def test_robustness_large_image_and_bad_inputs(self):
        # A 4000x3000 still must render (downscaled internally first).
        large = os.path.join(BASE, "data", "test_large.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc2=size=4000x3000:rate=1",
                        "-frames:v", "1", large], check=True, timeout=120)
        project = {
            "id": "large", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "L", "kind": "image", "src": large, "duration": 1}],
        }
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_large.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 1.0, delta=0.5)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        # A video file is not a still image.
        bad = json.loads(json.dumps(project))
        bad["clips"][0]["src"] = "data/test_media/clip1.mp4"
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))
        # Boolean / float dimensions are rejected, with a clear message.
        bad2 = example()
        bad2["output"]["width"] = True
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e["code"] == "INVALID_DIMENSION" for e in errors))
        # Empty text is rejected.
        bad3 = example()
        bad3["texts"] = [{"text": "   ", "start": 0.5, "end": 1.5}]
        errors, _ = validate_project(bad3)
        self.assertTrue(any(e["code"] == "INVALID_SCHEMA" for e in errors))

    def test_jpeg_image_clip_validates_and_renders(self):
        # Regression: JPEGs probe with duration 0.04 via image2, so a
        # duration-based still/video check wrongly rejects real photos.
        project = {
            "id": "jpeg", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "j1", "kind": "image", "src": "data/test_media/pattern.jpg", "duration": 1}],
        }
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_jpeg.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 1.0, delta=0.5)

    def test_atomic_render_failure_preserves_existing(self):
        import subprocess as _sp
        from unittest import mock
        from agentcut.render import render_project as _rp
        out = os.path.join(BASE, "data", "test_atomic.mp4")
        with open(out, "wb") as fh:
            fh.write(b"previous good output")
        real_run = _sp.run

        def _fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
                return _sp.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            return real_run(cmd, **kwargs)

        project = {
            "id": "atomic", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": "data/test_media/poster.png", "duration": 1}],
        }
        with mock.patch("agentcut.render.subprocess.run", side_effect=_fake_run):
            ok, info = _rp(project, out)
        self.assertFalse(ok)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(), b"previous good output")
        leftovers = [f for f in os.listdir(os.path.join(BASE, "data"))
                     if f.startswith(".agentcut-tmp-")]
        self.assertEqual(leftovers, [])

    def test_transition_duration_and_text_fade(self):
        import subprocess as _sp
        project = {
            "id": "tdur", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [
                {"id": "v1", "kind": "video", "src": "data/test_media/clip1.mp4", "trim_in": 0, "trim_out": 3},
                {"id": "v2", "kind": "video", "src": "data/test_media/clip2.mp4", "trim_in": 0, "trim_out": 3,
                 "transition": "crossfade", "transition_duration": 1.0},
            ],
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 5.0, places=2)
        out = os.path.join(BASE, "data", "test_tdur.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 5.0, delta=0.5)
        # Text fade: a fading text must change pixels between mid-fade
        # and full opacity, and an over-long fade must be rejected.
        tproject = {
            "id": "tfade", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": "data/test_media/poster.png", "duration": 3}],
            "texts": [{"text": "FADING TEXT HERE", "start": 0.2, "end": 2.8, "size": 64,
                       "x": "center", "y": 300, "fade_in": 1.0, "fade_out": 1.0}],
        }
        errors, _ = validate_project(tproject)
        self.assertEqual(errors, [])
        tout = os.path.join(BASE, "data", "test_tfade.mp4")
        ok, info = render_project(tproject, tout)
        self.assertTrue(ok, info)
        frames = []
        for k, t in enumerate((0.7, 1.5)):
            fp = os.path.join(BASE, "data", f"test_tfade_{k}.png")
            _sp.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", tout,
                     "-frames:v", "1", fp], capture_output=True, timeout=60)
            with open(fp, "rb") as fh:
                frames.append(fh.read())
        self.assertNotEqual(frames[0], frames[1])
        bad = dict(tproject)
        bad["texts"] = [{"text": "X", "start": 0, "end": 1, "fade_in": 0.6, "fade_out": 0.6}]
        errors, _ = validate_project(bad)
        self.assertTrue(errors)

    def test_per_clip_filters(self):
        import subprocess as _sp
        base = {
            "id": "filt", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": "data/test_media/poster.png", "duration": 2}],
        }
        filt = dict(base)
        filt["clips"] = [dict(base["clips"][0], grayscale=True, brightness=-0.3)]
        errors, _ = validate_project(filt)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_filt.mp4")
        ok, info = render_project(filt, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)
        # Grayscale of the blue poster must differ from the plain frame.
        plain = os.path.join(BASE, "data", "test_plain.mp4")
        ok, _ = render_project(base, plain)
        self.assertTrue(ok)
        frames = []
        for src, k in ((plain, 0), (out, 1)):
            fp = os.path.join(BASE, "data", f"test_filt_{k}.png")
            _sp.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", src,
                     "-frames:v", "1", fp], capture_output=True, timeout=60)
            with open(fp, "rb") as fh:
                frames.append(fh.read())
        self.assertNotEqual(frames[0], frames[1])
        bad = dict(base)
        bad["clips"] = [dict(base["clips"][0], brightness=2.0)]
        errors, _ = validate_project(bad)
        self.assertTrue(errors)

    def test_loudnorm_track(self):
        project = {
            "id": "loud", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "v1", "kind": "video", "src": "data/test_media/clip1.mp4", "trim_in": 0, "trim_out": 3}],
            "audio": [{"src": "data/test_media/clip1.mp4", "kind": "music", "volume": 1.0, "loudnorm": True}],
        }
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_loud.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertIn("audio", {st["codec_type"] for st in probe["streams"]})
        bad = dict(project)
        bad["audio"] = [{"src": "data/test_media/clip1.mp4", "kind": "music", "loudnorm": "yes"}]
        errors, _ = validate_project(bad)
        self.assertTrue(errors)

    def test_caption_styling(self):
        with open(os.path.join(BASE, "examples", "srt.json")) as fh:
            project = json.load(fh)
        project["caption_color"] = "yellow"
        project["caption_size"] = 48
        project["caption_position"] = "top_center"
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertTrue(info["captions"])
        self.assertEqual(info["captions"][0]["color"], "yellow")
        self.assertEqual(info["captions"][0]["size"], 48)
        self.assertEqual(info["captions"][0]["position"], "top_center")
        out = os.path.join(BASE, "data", "test_srt_style.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)
        bad = dict(project)
        bad["caption_color"] = "notacolor"
        errors, _ = validate_project(bad)
        self.assertTrue(errors)

    def test_prescaled_large_image(self):
        import subprocess as _sp
        import tempfile as _tf
        tmpdir = _tf.mkdtemp(prefix="agentcut-large-")
        large = os.path.join(tmpdir, "large.png")
        _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=blue:s=4000x3000", "-frames:v", "1", large],
                capture_output=True, timeout=120)
        project = {
            "id": "large", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 24},
            "clips": [{"id": "i1", "kind": "image", "src": large, "duration": 2, "fit": "cover"}],
        }
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_large.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.5)
        video = [st for st in probe["streams"] if st["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_server_survives_bad_tool_call(self):
        import subprocess as _sp
        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_job","arguments":{"job_id":"wrong-key"}}}',
            '{"jsonrpc":"2.0","id":3,"method":"ping"}',
        ]
        proc = _sp.run([sys.executable, "-m", "agentcut", "serve"],
                       input="\n".join(lines) + "\n", capture_output=True, text=True,
                       cwd=BASE, env={**os.environ, "PYTHONPATH": os.path.join(BASE, "src")},
                       timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn('"id": 3', proc.stdout)
        self.assertIn("INVALID_SCHEMA", proc.stdout)

    def test_probe_media(self):
        info = probe_media(os.path.join(BASE, "data", "test_media", "clip1.mp4"))
        self.assertAlmostEqual(info["duration"], 5.0, delta=0.2)
        self.assertTrue(info["has_video"])


if __name__ == "__main__":
    unittest.main()
