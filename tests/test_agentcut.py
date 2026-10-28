"""AgentCut tests: validation, real renders, preview, failure cases."""
import json
import os
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

    def test_probe_media(self):
        info = probe_media(os.path.join(BASE, "data", "test_media", "clip1.mp4"))
        self.assertAlmostEqual(info["duration"], 5.0, delta=0.2)
        self.assertTrue(info["has_video"])


if __name__ == "__main__":
    unittest.main()
