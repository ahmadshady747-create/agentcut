"""AgentCut tests: validation, real renders, preview, failure cases."""
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.render import poster_frame, preview_project, render_project
from agentcut.validate import probe_media, validate_project

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



def _crop_mean(path, t, x, y, w, h):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
           "-vf", f"crop={w}:{h}:{x}:{y}", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(cmd, capture_output=True).stdout
    n = len(data) // 3
    return tuple(sum(data[i] for i in range(k, len(data), 3)) / n for k in range(3))

def _png_px(path):
    """Pixel reader for an ffmpeg PNG frame (stdlib only)."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h, ctype = 8, b"", 0, 0, 0
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        chunk = data[pos:pos + ln]
        pos += ln + 4
        if typ == b"IHDR":
            w, h, _depth, ctype = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * ch
    rows, prev, p = [], bytearray(stride), 0
    for _y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(line)
        prev = line

    def px(x, y):
        row = rows[y]
        return tuple(row[x * ch:(x + 1) * ch])

    return w, h, px


def _frame_png(path, t, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", path,
                    "-frames:v", "1", out], check=True)
    return _png_px(out)


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
        # "dissolve" used to be the invalid example here; since
        # Loop 5 round 5 it is a real transition, so the invalid
        # example is now a name no FFmpeg xfade transition has.
        bad2["clips"][1]["transition"] = "sparkle_burst"
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

    def test_detached_job_survives_session_close(self):
        import subprocess as _sp
        import time as _time
        env = {**os.environ, "PYTHONPATH": os.path.join(BASE, "src")}

        def _start():
            return _sp.Popen([sys.executable, "-m", "agentcut", "serve"],
                             stdin=_sp.PIPE, stdout=_sp.PIPE, text=True,
                             cwd=BASE, env=env)

        def _init(proc):
            proc.stdin.write('{"jsonrpc":"2.0","id":1,"method":"initialize",'
                             '"params":{"protocolVersion":"2024-11-05","capabilities":{},'
                             '"clientInfo":{"name":"t","version":"1"}}}\n')
            proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            proc.stdin.flush()
            line = proc.stdout.readline()
            self.assertIn('"id": 1', line)

        def _call(proc, rid, name, args):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "method": "tools/call",
                                         "params": {"name": name, "arguments": args}}) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                self.assertTrue(line, "server closed stdout early")
                d = json.loads(line)
                if d.get("id") == rid:
                    return json.loads(d["result"]["content"][0]["text"])

        proc = _start()
        try:
            _init(proc)
            _call(proc, 2, "create_project", {"project": example()})
            res = _call(proc, 3, "render_project", {"id": "basic"})
            job_id = res["job_id"]
        finally:
            # Close the session hard: the detached worker must survive.
            proc.stdin.close()
            proc.wait(timeout=15)
        out_path = os.path.join(BASE, "data", "outputs", job_id, "output.mp4")
        proc2 = _start()
        try:
            _init(proc2)
            status = None
            payload = None
            deadline = _time.time() + 120
            while _time.time() < deadline:
                payload = _call(proc2, 10, "get_job", {"id": job_id})
                status = payload["status"]
                if status in ("done", "failed"):
                    break
                _time.sleep(0.5)
            self.assertEqual(status, "done", payload)
        finally:
            proc2.stdin.close()
            proc2.wait(timeout=15)
        self.assertTrue(os.path.isfile(out_path), payload)
        probe = ffprobe(out_path)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)

    def test_produce_video_montage(self):
        from agentcut.render import produce_video
        brief = {
            "id": "produced-test", "title": "Produced by an agent",
            "format": "square",
            "clips": [
                {"src": "data/test_media/poster.png"},
                {"src": "data/test_media/pattern.png"},
                {"src": "data/test_media/poster.png"},
            ],
        }
        out = os.path.join(BASE, "data", "test_produce.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info["duration"], 8.0, places=2)
        self.assertEqual((info["width"], info["height"]), (720, 720))
        self.assertEqual(info["pix_fmt"], "yuv420p")
        self.assertTrue(info["has_video"])
        self.assertTrue(len(info["review_frames"]) >= 3)
        self.assertTrue(all(os.path.isfile(f["path"]) for f in info["review_frames"]))
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 8.0, delta=0.5)

    def test_produce_video_auto_video_duration(self):
        from agentcut.render import produce_video
        brief = {
            "id": "produced-video", "format": "landscape",
            "clips": [
                {"src": "data/test_media/clip1.mp4"},
                {"src": "data/test_media/clip2.mp4"},
            ],
        }
        out = os.path.join(BASE, "data", "test_produce_video.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info["duration"], 9.5, places=2)

    def test_producer_template_montage_renders(self):
        from agentcut.render import produce_video
        for name in ("brief-montage", "brief-highlight",
                     "brief-announcement", "brief-showcase"):
            with open(os.path.join(BASE, "examples", f"{name}.json")) as fh:
                brief = json.load(fh)
            self.assertTrue(brief["clips"])
            self.assertIn(brief["format"], ("vertical", "square", "landscape"))
        with open(os.path.join(BASE, "examples", "brief-montage.json")) as fh:
            brief = json.load(fh)
        out = os.path.join(BASE, "data", "test_template_montage.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info["duration"], 5.0, delta=0.5)
        self.assertEqual(info["pix_fmt"], "yuv420p")
        self.assertEqual(len(info["review_frames"]), 5)

    def test_producer_qa_checks(self):
        from agentcut.render import produce_video
        with open(os.path.join(BASE, "examples", "brief-montage.json")) as fh:
            brief = json.load(fh)
        out = os.path.join(BASE, "data", "test_producer_qa.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        qa = info["qa"]
        self.assertAlmostEqual(qa["duration_actual"], 5.0, delta=0.5)
        self.assertEqual((qa["width_actual"], qa["height_actual"]), (720, 720))
        self.assertEqual(qa["pix_fmt"], "yuv420p")
        self.assertIsInstance(qa["black_segments"], list)
        self.assertIsInstance(qa["silence_segments"], list)
        # The montage brief has no music track, so its audio is silent
        # throughout by construction; QA must say so, not hide it.
        self.assertTrue(any("silent" in w for w in qa["warnings"]), qa["warnings"])
        self.assertFalse(any("resolution" in w for w in qa["warnings"]), qa["warnings"])

    def test_producer_sections_and_soundtrack(self):
        from agentcut.render import produce_video
        brief = {
            "id": "sectioned", "format": "square",
            "soundtrack": "data/test_media/clip1.mp4",
            "sections": [
                {"title": "Intro", "clips": [
                    {"src": "data/test_media/poster.png", "duration": 2},
                    {"src": "data/test_media/pattern.png", "duration": 2}]},
                {"title": "Finale", "clips": [
                    {"src": "data/test_media/poster.png", "duration": 2}]},
            ],
        }
        out = os.path.join(BASE, "data", "test_sections.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(info["duration"], 5.0, delta=0.5)
        self.assertEqual(info["pix_fmt"], "yuv420p")
        self.assertEqual([s["title"] for s in info["sections"]], ["Intro", "Finale"])
        self.assertAlmostEqual(info["sections"][1]["start"], 3.0, delta=0.1)
        texts = info["project"]["texts"]
        self.assertTrue(any(t["text"] == "Intro" and abs(t["start"] - 0.5) < 0.1 for t in texts))
        self.assertTrue(any(t["text"] == "Finale" and abs(t["start"] - 3.5) < 0.1 for t in texts))
        audio = info["project"]["audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["kind"], "music")
        # Soundtrack as a dict carries its own volume through.
        brief2 = {"id": "soundtrack-dict", "format": "square",
                  "soundtrack": {"src": "data/test_media/clip1.mp4", "volume": 0.5},
                  "clips": [{"src": "data/test_media/poster.png", "duration": 2}]}
        out2 = os.path.join(BASE, "data", "test_soundtrack_dict.mp4")
        ok2, info2 = produce_video(brief2, out2)
        self.assertTrue(ok2, info2)
        self.assertEqual(info2["project"]["audio"][0]["volume"], 0.5)

    def test_producer_variants_all_formats(self):
        from agentcut.render import produce_variants
        import shutil
        with open(os.path.join(BASE, "examples", "brief-montage.json")) as fh:
            brief = json.load(fh)
        out_dir = os.path.join(BASE, "data", "test_variants")
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        ok, info = produce_variants(brief, out_dir)
        self.assertTrue(ok, info)
        expected = {"vertical": (720, 1280), "square": (720, 720),
                    "landscape": (1280, 720)}
        for fmt, (w, h) in expected.items():
            v = info["variants"][fmt]
            self.assertEqual((v["width"], v["height"]), (w, h))
            self.assertAlmostEqual(v["duration"], 5.0, delta=0.5)
            self.assertEqual(v["pix_fmt"], "yuv420p")
            self.assertTrue(os.path.isfile(v["output"]))

    def test_overlay_image_renders_on_timeline(self):
        # Solid red overlay, generated in-test so its presence in a
        # frame is unambiguous (pure red where the base frame is black).
        red = os.path.join(BASE, "data", "test_red.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=200x200", "-frames:v", "1", red],
                       check=True)
        base = {"id": "ov-base", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video",
                           "src": "data/test_media/clip1.mp4",
                           "trim_in": 0, "trim_out": 5}]}
        project = dict(base)
        project["id"] = "ov-test"
        project["overlays"] = [{"id": "pip", "kind": "image", "src": red,
                                "start": 2.0, "duration": 2.0, "size": 0.5,
                                "position": "bottom_right"}]
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_overlay.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 5.0, delta=0.5)

        def crop_mean(t):
            cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", out,
                   "-vf", "crop=180:180:160:440", "-frames:v", "1",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            data = subprocess.run(cmd, capture_output=True).stdout
            n = len(data) // 3
            return tuple(sum(data[i] for i in range(k, len(data), 3)) / n
                         for k in range(3))
        r, g, b = crop_mean(3.0)
        self.assertGreater(r, 200)
        self.assertLess(g, 80)
        self.assertLess(b, 80)
        r0, _, _ = crop_mean(1.0)
        self.assertLess(r0, 80)  # overlay not on screen before its start

    def test_overlay_video_renders_and_errors_carry_fix(self):
        project = {"id": "ov-video", "version": 1,
                   "output": {"width": 360, "height": 640, "fps": 24},
                   "clips": [{"id": "a", "kind": "video",
                              "src": "data/test_media/clip1.mp4",
                              "trim_in": 0, "trim_out": 5}],
                   "overlays": [{"id": "pip", "kind": "video",
                                 "src": "data/test_media/clip2.mp4",
                                 "trim_in": 0, "trim_out": 2,
                                 "start": 1.0, "duration": 2.0,
                                 "size": 0.4, "opacity": 0.8}]}
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_overlay_video.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        bad = dict(project)
        bad["overlays"] = [{"id": "pip", "kind": "image",
                            "src": "data/test_media/poster.png",
                            "start": 99, "duration": 1.0}]
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "CLIP_OUT_OF_RANGE" for e in errors))
        self.assertTrue(all("fix" in e for e in errors if e["code"] == "CLIP_OUT_OF_RANGE"))

    def test_overlay_keyframes_position_opacity_scale(self):
        red = os.path.join(BASE, "data", "test_red.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=200x200", "-frames:v", "1", red],
                       check=True)
        clips = [{"id": "a", "kind": "video",
                  "src": "data/test_media/clip1.mp4",
                  "trim_in": 0, "trim_out": 5}]

        def render(ov, name):
            project = {"id": name, "version": 1,
                       "output": {"width": 360, "height": 640, "fps": 24},
                       "clips": clips, "overlays": [ov]}
            errors, _ = validate_project(project)
            self.assertEqual(errors, [])
            out = os.path.join(BASE, "data", name + ".mp4")
            ok, info = render_project(project, out)
            self.assertTrue(ok, info)
            return out

        # Position: x 0 -> 200 over 0..2s, y 0.
        out = render({"id": "p", "kind": "image", "src": red, "start": 0,
                      "duration": 4, "size": 0.25,
                      "keyframes": [{"time": 0, "x": 0, "y": 0},
                                    {"time": 2, "x": 200, "y": 0}]}, "test_kf_pos")
        self.assertGreater(_crop_mean(out, 0.5, 60, 10, 10, 10)[0], 200)
        self.assertLess(_crop_mean(out, 0.5, 210, 10, 10, 10)[0], 80)
        self.assertLess(_crop_mean(out, 3.0, 60, 10, 10, 10)[0], 80)
        self.assertGreater(_crop_mean(out, 3.0, 210, 10, 10, 10)[0], 200)
        # Opacity: 0.2 -> 1.0 over 0..2s, top-left, fully inside crop.
        out = render({"id": "o", "kind": "image", "src": red, "start": 0,
                      "duration": 3, "size": 0.5, "position": "top_left",
                      "keyframes": [{"time": 0, "opacity": 0.2},
                                    {"time": 2, "opacity": 1.0}]}, "test_kf_op")
        self.assertGreater(_crop_mean(out, 0.25, 30, 30, 50, 50)[0], 40)
        self.assertLess(_crop_mean(out, 0.25, 30, 30, 50, 50)[0], 120)
        self.assertGreater(_crop_mean(out, 2.5, 30, 30, 50, 50)[0], 200)
        # Scale: 0.25 -> 0.5 over 0..2s, top-left (base x/y 20,20).
        out = render({"id": "s", "kind": "image", "src": red, "start": 0,
                      "duration": 3, "size": 0.25, "position": "top_left",
                      "keyframes": [{"time": 0, "scale": 0.25},
                                    {"time": 2, "scale": 0.5}]}, "test_kf_scale")
        self.assertGreater(_crop_mean(out, 0.5, 100, 30, 10, 10)[0], 200)
        self.assertLess(_crop_mean(out, 0.5, 150, 30, 10, 10)[0], 80)
        self.assertGreater(_crop_mean(out, 2.5, 150, 30, 10, 10)[0], 200)
        # Bad keyframe time carries a fix hint.
        bad = {"id": "bad", "version": 1,
               "output": {"width": 360, "height": 640, "fps": 24},
               "clips": clips,
               "overlays": [{"id": "p", "kind": "image", "src": red,
                             "start": 0, "duration": 2,
                             "keyframes": [{"time": 0, "x": 0},
                                           {"time": 99, "x": 100}]}]}
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "CLIP_OUT_OF_RANGE" for e in errors))
        self.assertTrue(all("fix" in e for e in errors))

    def test_reverse_freeze_loop_and_split(self):
        clips = [{"id": "a", "kind": "video",
                  "src": "data/test_media/clip1.mp4",
                  "trim_in": 0, "trim_out": 5}]

        def render(project, name):
            out = os.path.join(BASE, "data", name + ".mp4")
            ok, info = render_project(project, out)
            self.assertTrue(ok, info)
            return out

        def psnr(a, b):
            import re
            out = subprocess.run(["ffmpeg", "-i", a, "-i", b,
                                  "-filter_complex", "[0:v][1:v]psnr",
                                  "-f", "null", "-"],
                                 capture_output=True, text=True)
            m = re.search(r"average:\s*(inf|[0-9.]+)", out.stderr)
            return 999.0 if m and m.group(1) == "inf" else float(m.group(1))

        def frame(mp4, t, name):
            out = os.path.join(BASE, "data", name)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t),
                            "-i", mp4, "-frames:v", "1", out], check=True)
            return out
        base = {"id": "rfl-base", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": clips}
        base_out = render(base, "test_rfl_base")
        # Loop: 5s clip x2 = 10s.
        looped = dict(base); looped["id"] = "rfl-loop"
        looped["clips"] = [{**clips[0], "loop_count": 2}]
        errors, info = validate_project(looped)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 10.0, places=2)
        loop_out = render(looped, "test_rfl_loop")
        self.assertAlmostEqual(float(ffprobe(loop_out)["format"]["duration"]), 10.0, delta=0.5)
        # Freeze: every frame equals the frame at freeze_at.
        frozen = dict(base); frozen["id"] = "rfl-freeze"
        frozen["clips"] = [{**clips[0], "freeze_at": 2.0}]
        freeze_out = render(frozen, "test_rfl_freeze")
        f1 = frame(freeze_out, 0.5, "fz05.png")
        f2 = frame(freeze_out, 3.5, "fz35.png")
        self.assertGreater(psnr(f1, f2), 40)
        # Reverse: reversing twice returns to the original.
        rev = dict(base); rev["id"] = "rfl-rev"
        rev["clips"] = [{**clips[0], "reverse": True}]
        rev_out = render(rev, "test_rfl_rev")
        rev2 = {"id": "rfl-rev2", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video", "src": rev_out,
                           "trim_in": 0, "trim_out": 5, "reverse": True}]}
        rev2_out = render(rev2, "test_rfl_rev2")
        self.assertGreater(psnr(frame(rev2_out, 2.0, "rr20.png"),
                                frame(base_out, 2.0, "base20.png")), 30)
        # Split: video clip 0..5 at 2s -> 0..2 and 2..5, total unchanged.
        from agentcut.render import split_clip
        ok, split = split_clip(base, "a", 2.0)
        self.assertTrue(ok, split)
        self.assertEqual([c["id"] for c in split["clips"]], ["a-a", "a-b"])
        self.assertEqual((split["clips"][0]["trim_out"], split["clips"][1]["trim_in"]), (2.0, 2.0))
        self.assertEqual(split["clips"][1]["transition"], "none")
        errors, info = validate_project(split)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 5.0, places=2)
        ok, bad = split_clip(base, "a", 99)
        self.assertFalse(ok)
        self.assertIn("fix", bad)

    def test_chroma_mask_and_blur_fit(self):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=green:s=200x200", "-vf",
                        "drawbox=x=50:y=50:w=100:h=100:color=red:t=fill",
                        "-frames:v", "1",
                        os.path.join(BASE, "data", "test_green.png")], check=True)
        green = os.path.join(BASE, "data", "test_green.png")
        clips = [{"id": "a", "kind": "video",
                  "src": "data/test_media/clip1.mp4",
                  "trim_in": 0, "trim_out": 5}]
        base = {"id": "r4", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": clips}
        # Chroma overlay: green removed (base black shows), red kept.
        proj = dict(base); proj["id"] = "r4-chroma"
        proj["overlays"] = [{"id": "gs", "kind": "image", "src": green,
                             "start": 0, "duration": 3, "size": 0.5,
                             "position": "top_left", "chroma_key": "green"}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_chroma.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        self.assertLess(_crop_mean(out, 1.0, 30, 30, 20, 20)[0], 80)
        self.assertGreater(_crop_mean(out, 1.0, 100, 100, 20, 20)[0], 200)
        # Rect mask on a main clip: inside shows content, outside black.
        proj = dict(base); proj["id"] = "r4-mask"
        proj["clips"] = [{**clips[0], "mask_shape": "rect",
                          "mask_x": 100, "mask_y": 300,
                          "mask_w": 160, "mask_h": 100}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_mask.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        inside = _crop_mean(out, 2.0, 180, 350, 20, 20)
        self.assertGreater(sum(inside), 30)
        self.assertLess(sum(_crop_mean(out, 2.0, 20, 20, 20, 20)), 30)
        # Blurred-background fit: corners are blurred content, not black.
        proj = dict(base); proj["id"] = "r4-blur"
        proj["clips"] = [{**clips[0], "fit": "blur"}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_blur.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        self.assertGreater(sum(_crop_mean(out, 2.0, 10, 10, 20, 20)), 30)
        # Bad mask shape carries a fix hint.
        bad = dict(base); bad["id"] = "r4-bad"
        bad["clips"] = [{**clips[0], "mask_shape": "triangle"}]
        errors, _ = validate_project(bad)
        self.assertTrue(any("fix" in e for e in errors))

    def test_continuous_keyframe_scaling(self):
        # Loop 6 round 3: scale keyframes must vary PER FRAME via
        # scale eval=frame, not in <=0.5s steps. At t=1.0 and t=1.1
        # the old segmentation would show the SAME width (both in
        # the [1.0, 1.5) segment at the segment-start scale); the
        # new path must show a wider overlay at t=1.1 than at t=1.0.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=360x640:r=24:d=4",
                        os.path.join(BASE, "data", "test_blue4.mp4")], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=200x200", "-frames:v", "1",
                        os.path.join(BASE, "data", "test_red200.png")], check=True)
        blue = os.path.join(BASE, "data", "test_blue4.mp4")
        red = os.path.join(BASE, "data", "test_red200.png")
        proj = {"id": "r6-scale", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video", "src": blue,
                           "trim_in": 0, "trim_out": 4}],
                "overlays": [{"id": "sc", "kind": "image", "src": red,
                              "start": 0, "duration": 4, "size": 0.35,
                              "position": "top_left",
                              "keyframes": [{"time": 0, "scale": 0.2,
                                             "x": 20, "y": 20},
                                            {"time": 4, "scale": 0.8,
                                             "x": 20, "y": 20}]}]}
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_scale_keys.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 4.0, delta=0.5)

        def right_edge(t):
            _w, _h, px = _frame_png(out, t, os.path.join(BASE, "data", "test_scale_frame.png"))
            edge = None
            for x in range(20, 340):
                r, g, b = px(x, 50)[:3]
                if r > 200 and b < 100:
                    edge = x
            return edge

        e10 = right_edge(1.0)
        e11 = right_edge(1.1)
        # scale(1.0) = 0.35 -> width 126 -> edge ~146;
        # scale(1.1) = 0.365 -> width 130 -> edge ~150.
        self.assertIsNotNone(e10)
        self.assertIsNotNone(e11)
        self.assertGreater(e11, e10)
        self.assertTrue(138 <= e10 <= 154, e10)
        self.assertTrue(142 <= e11 <= 158, e11)
        # Combined scale + position keyframes still work together.
        proj2 = {"id": "r6-scale-pos", "version": 1,
                 "output": {"width": 360, "height": 640, "fps": 24},
                 "clips": [{"id": "a", "kind": "video", "src": blue,
                            "trim_in": 0, "trim_out": 4}],
                 "overlays": [{"id": "sc", "kind": "image", "src": red,
                               "start": 0, "duration": 4, "size": 0.35,
                               "keyframes": [{"time": 0, "scale": 0.3,
                                              "x": 20, "y": 20},
                                             {"time": 4, "scale": 0.6,
                                              "x": 200, "y": 20}]}]}
        errors, _ = validate_project(proj2)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_scale_pos.mp4")
        ok, info = render_project(proj2, out2)
        self.assertTrue(ok, info)
        early = _crop_mean(out2, 0.1, 250, 50, 10, 10)
        late = _crop_mean(out2, 3.9, 250, 50, 10, 10)
        self.assertGreater(early[2], 150)  # blue: overlay not there yet
        self.assertGreater(late[0], 200)   # red: overlay moved there

        # Scale-varying + chroma is REJECTED with a fix, not
        # rendered broken: colorkey fails while filtering when its
        # input frame size changes every frame (verified on this
        # FFmpeg build in Loop 6 round 3).
        # Generate the green test image here: this test runs
        # alphabetically BEFORE test_mask_chroma_key_intersection,
        # which also creates it, so relying on that file made this
        # test fail in a clean copy (parent audit, Loop 6).
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=green:s=200x200", "-vf",
                        "drawbox=x=50:y=50:w=100:h=100:color=red:t=fill",
                        "-frames:v", "1",
                        os.path.join(BASE, "data", "test_green2.png")], check=True)
        proj3 = {"id": "r6-scale-chroma", "version": 1,
                 "output": {"width": 360, "height": 640, "fps": 24},
                 "clips": [{"id": "a", "kind": "video", "src": blue,
                            "trim_in": 0, "trim_out": 4}],
                 "overlays": [{"id": "sc", "kind": "image",
                               "src": os.path.join(BASE, "data", "test_green2.png"),
                               "start": 0, "duration": 4, "size": 0.35,
                               "chroma_key": "green",
                               "keyframes": [{"time": 0, "scale": 0.2,
                                              "x": 20, "y": 20},
                                             {"time": 4, "scale": 0.8,
                                              "x": 20, "y": 20}]}]}
        errors, _ = validate_project(proj3)
        self.assertTrue(errors)
        self.assertTrue(any("chroma_key" in e.get("field", "") and "fix" in e
                            for e in errors))

    def test_word_reveal(self):
        # Word reveal only: cumulative prefixes staggered by word
        # length, so early frames show only the first word and late
        # frames show the full text. Characters are never the unit
        # (Arabic letters join; splitting them would break joining).
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=360x640", "-frames:v", "1",
                        os.path.join(BASE, "data", "test_black.png")], check=True)
        black = os.path.join(BASE, "data", "test_black.png")

        def white_total(path, t):
            cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            data = subprocess.run(cmd, capture_output=True).stdout
            n = len(data) // 3
            return sum(1 for i in range(n)
                       if data[3 * i] > 200 and data[3 * i + 1] > 200 and data[3 * i + 2] > 200)
        proj = {"id": "r7-reveal", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "image", "src": black, "duration": 3}],
                "texts": [{"text": "HELLO BRAVE WORLD", "start": 0, "end": 3,
                           "size": 48, "x": "center", "y": 300,
                           "reveal": "word", "box": False}]}
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_reveal.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        early = white_total(out, 0.5)
        late = white_total(out, 2.5)
        self.assertGreater(early, 50)  # first word visible early
        self.assertGreater(late, early * 2)  # full text visible late
        # Bad reveal values and reveal+animation carry fix hints.
        bad = dict(proj); bad["id"] = "r7-reveal-bad"
        bad["texts"] = [{"text": "X Y", "start": 0, "end": 2, "reveal": "char"}]
        errors, _ = validate_project(bad)
        self.assertTrue(any("fix" in e for e in errors))
        bad2 = dict(proj); bad2["id"] = "r7-reveal-bad2"
        bad2["texts"] = [{"text": "X Y", "start": 0, "end": 2,
                          "reveal": "word", "animation": "pop"}]
        errors, _ = validate_project(bad2)
        self.assertTrue(any("fix" in e for e in errors))

    def test_arabic_font_selection(self):
        # DejaVu has no Arabic glyphs; Arabic text must resolve to a
        # Noto Arabic font when one exists on the machine.
        from agentcut.validate import (contains_arabic, find_font,
                                       find_font_for_text)
        self.assertTrue(contains_arabic("مرحبا بالعالم"))
        self.assertFalse(contains_arabic("Hello world"))
        arabic_font = find_font_for_text("مرحبا بالعالم")
        self.assertTrue(arabic_font and "Noto" in arabic_font, arabic_font)
        self.assertEqual(find_font_for_text("Hello world"), find_font())

    def test_arabic_text_renders(self):
        # Visual shaping was verified by eye on a frame (joined
        # letters, correct RTL order) with this FFmpeg build
        # (libfribidi + libharfbuzz) and Noto Naskh Arabic; this
        # test pins the render path so it cannot silently regress
        # to missing glyphs or a failed render.
        proj = {"id": "arabic-smoke", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "image",
                           "src": "data/test_media/poster.png", "duration": 3}],
                "texts": [{"text": "مرحبا بالعالم", "start": 0.5, "end": 2.5,
                           "size": 48, "x": "center", "y": 300}]}
        out = os.path.join(BASE, "data", "test_arabic.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 3.0, delta=0.5)

    def test_overlay_audio_mixing(self):
        # Loop 6 round 4: an overlay video with volume set mixes
        # its audio in exactly during its window, and nowhere else.
        import re as _re
        base = {"id": "r6-ov-audio", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "image",
                           "src": "data/test_media/poster.png", "duration": 2.5},
                          {"id": "b", "kind": "image",
                           "src": "data/test_media/pattern.png", "duration": 2.5}]}
        proj = dict(base)
        proj["overlays"] = [{"id": "snd", "kind": "video",
                             "src": "data/test_media/clip1.mp4",
                             "trim_in": 0, "trim_out": 5,
                             "start": 1.0, "duration": 2.0, "size": 0.3,
                             "position": "top_left", "volume": 0.8}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_overlay_audio.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        self.assertIn("audio", {st["codec_type"] for st in probe["streams"]})

        def mean_volume(start, end):
            proc = subprocess.run(["ffmpeg", "-v", "info", "-i", out,
                                   "-af", f"atrim=start={start}:end={end},volumedetect",
                                   "-f", "null", "-"],
                                  capture_output=True, text=True)
            m = _re.search(r"mean_volume: (-?\d+\.?\d*|-inf)", proc.stderr)
            self.assertIsNotNone(m, proc.stderr[-500:])
            return -999.0 if m.group(1) == "-inf" else float(m.group(1))

        self.assertLess(mean_volume(0, 0.9), -70)
        self.assertGreater(mean_volume(1.2, 2.8), -40)
        self.assertLess(mean_volume(3.2, 4.9), -70)
        # Validation: volume on an image overlay is rejected.
        bad = dict(base)
        bad["overlays"] = [{"id": "pic", "kind": "image",
                            "src": "data/test_media/poster.png",
                            "start": 0, "duration": 2, "volume": 0.8}]
        errors, _ = validate_project(bad)
        self.assertTrue(any(e.get("field") == "overlays.volume"
                            and "fix" in e and e.get("clip_id") == "pic"
                            for e in errors))
        # Validation: volume on a silent video is rejected too.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=360x640:r=24:d=3",
                        os.path.join(BASE, "data", "test_silent.mp4")], check=True)
        bad2 = dict(base)
        bad2["overlays"] = [{"id": "sil", "kind": "video",
                             "src": os.path.join(BASE, "data", "test_silent.mp4"),
                             "start": 0, "duration": 2, "volume": 0.8}]
        errors, _ = validate_project(bad2)
        self.assertTrue(any(e.get("field") == "overlays.volume" and "fix" in e
                            for e in errors))
        # Volume 0 (and omitted volume) on an image overlay is fine.
        ok_proj = dict(base)
        ok_proj["overlays"] = [{"id": "pic", "kind": "image",
                                "src": "data/test_media/poster.png",
                                "start": 0, "duration": 2, "volume": 0.0}]
        errors, _ = validate_project(ok_proj)
        self.assertEqual(errors, [])

    def test_mask_chroma_key_intersection(self):
        # Loop 6 round 2: mask x chroma_key must INTERSECT, not
        # replace. Main timeline is solid blue, so keyed-out green
        # and masked-out content both read as blue, kept red as red.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=blue:s=360x640:r=24:d=5",
                        os.path.join(BASE, "data", "test_blue.mp4")], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=green:s=200x200", "-vf",
                        "drawbox=x=50:y=50:w=100:h=100:color=red:t=fill",
                        "-frames:v", "1",
                        os.path.join(BASE, "data", "test_green2.png")], check=True)
        blue = os.path.join(BASE, "data", "test_blue.mp4")
        green = os.path.join(BASE, "data", "test_green2.png")
        base = {"id": "r6-mask", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video", "src": blue,
                           "trim_in": 0, "trim_out": 5}]}
        # Overlay: 200x200 source scaled to 180x180 at (20, 20);
        # circle mask centre (90, 90) r=60 in overlay coordinates.
        proj = dict(base); proj["id"] = "r6-mask-ov"
        proj["overlays"] = [{"id": "gs", "kind": "image", "src": green,
                             "start": 0, "duration": 3, "size": 0.5,
                             "position": "top_left", "chroma_key": "green",
                             "mask_shape": "circle", "mask_x": 90,
                             "mask_y": 90, "mask_r": 60}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_mask_chroma_ov.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        # Red inside the mask stays red.
        self.assertGreater(_crop_mean(out, 1.0, 91, 91, 20, 20)[0], 200)
        # Green inside the mask stays keyed out (blue shows through).
        gi = _crop_mean(out, 1.0, 91, 46, 20, 20)
        self.assertGreater(gi[2], 150)
        self.assertLess(gi[1], 100)
        # Red outside the mask is hidden (blue shows through).
        # Point chosen well outside the circle: the previous crop
        # straddled the circle edge and mixed red with blue.
        ro = _crop_mean(out, 1.0, 148, 62, 10, 10)
        self.assertGreater(ro[2], 150)
        self.assertLess(ro[0], 100)
        # Green outside the mask is hidden too.
        go = _crop_mean(out, 1.0, 28, 28, 20, 20)
        self.assertGreater(go[2], 150)
        # Same overlay with opacity keyframes: keying must survive.
        proj2 = dict(base); proj2["id"] = "r6-mask-ov-op"
        proj2["overlays"] = [{**proj["overlays"][0],
                              "keyframes": [{"time": 0, "opacity": 0.2,
                                             "x": 20, "y": 20},
                                            {"time": 3, "opacity": 1.0,
                                             "x": 20, "y": 20}]}]
        errors, _ = validate_project(proj2)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_mask_chroma_ov_op.mp4")
        ok, info = render_project(proj2, out2)
        self.assertTrue(ok, info)
        self.assertGreater(_crop_mean(out2, 2.9, 91, 91, 20, 20)[0], 200)
        gi2 = _crop_mean(out2, 2.9, 91, 46, 20, 20)
        self.assertGreater(gi2[2], 200)
        self.assertLess(gi2[1], 50)
        # Main clip: green+red video, circle mask, chroma key.
        # Masked-out red was the failing case before the fix (it
        # showed red); now it must composite over black.
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=green:s=200x200:r=24:d=3,drawbox=x=20:y=90:w=160:h=20:color=red:t=fill",
                        os.path.join(BASE, "data", "test_greenred.mp4")], check=True)
        proj3 = {"id": "r6-mask-clip", "version": 1,
                 "output": {"width": 200, "height": 200, "fps": 24},
                 "clips": [{"id": "a", "kind": "video",
                            "src": os.path.join(BASE, "data", "test_greenred.mp4"),
                            "trim_in": 0, "trim_out": 3,
                            "chroma_key": "green", "mask_shape": "circle",
                            "mask_x": 100, "mask_y": 100, "mask_r": 50}]}
        errors, _ = validate_project(proj3)
        self.assertEqual(errors, [])
        out3 = os.path.join(BASE, "data", "test_mask_chroma_clip.mp4")
        ok, info = render_project(proj3, out3)
        self.assertTrue(ok, info)
        self.assertGreater(_crop_mean(out3, 1.0, 95, 95, 10, 10)[0], 200)
        self.assertLess(sum(_crop_mean(out3, 1.0, 95, 60, 10, 10)), 30)
        self.assertLess(sum(_crop_mean(out3, 1.0, 25, 95, 10, 10)), 30)
        self.assertLess(sum(_crop_mean(out3, 1.0, 25, 25, 10, 10)), 30)

    def test_new_transitions_and_effects(self):
        proj = {"id": "r5", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [
                    {"id": "a", "kind": "video",
                     "src": "data/test_media/clip1.mp4",
                     "trim_in": 0, "trim_out": 3},
                    {"id": "b", "kind": "video",
                     "src": "data/test_media/clip2.mp4",
                     "trim_in": 0, "trim_out": 3,
                     "transition": "dissolve", "pixelize": 16,
                     "blur": 2.0, "hue": 45},
                    {"id": "c", "kind": "image",
                     "src": "data/test_media/poster.png", "duration": 2,
                     "transition": "circle_open", "vignette": True,
                     "sharpen": 1.5}]}
        errors, info = validate_project(proj)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 7.0, places=2)
        out = os.path.join(BASE, "data", "test_transitions_effects.mp4")
        ok, rinfo = render_project(proj, out)
        self.assertTrue(ok, rinfo)
        probe = ffprobe(out)
        self.assertAlmostEqual(float(probe["format"]["duration"]), 7.0, delta=0.5)
        video = [x for x in probe["streams"] if x["codec_type"] == "video"][0]
        self.assertEqual(video["pix_fmt"], "yuv420p")
        bad = dict(proj)
        bad["clips"] = [{**proj["clips"][0], "blur": 99}]
        errors, _ = validate_project(bad)
        self.assertTrue(any("fix" in e for e in errors))
        bad2 = dict(proj)
        bad2["clips"] = [{**proj["clips"][1], "transition": "sparkle_burst"}]
        errors, _ = validate_project(bad2)
        self.assertTrue(errors)

    def test_speed_curve_and_denoise(self):
        proj = {"id": "r6", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video",
                           "src": "data/test_media/clip1.mp4",
                           "trim_in": 0, "trim_out": 5,
                           "speed_curve": [{"start": 0, "end": 2.5, "speed": 2.0},
                                           {"start": 2.5, "end": 5, "speed": 0.5}]}]}
        errors, info = validate_project(proj)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 6.25, places=2)
        out = os.path.join(BASE, "data", "test_curve.mp4")
        ok, rinfo = render_project(proj, out)
        self.assertTrue(ok, rinfo)
        self.assertAlmostEqual(float(ffprobe(out)["format"]["duration"]), 6.25, delta=0.5)
        from agentcut.render import split_clip
        ok, split = split_clip(proj, "a", 3.0)
        self.assertTrue(ok, split)
        self.assertEqual(split["clips"][0]["trim_out"], 3.375)
        self.assertEqual(split["clips"][1]["trim_in"], 3.375)
        errors, info = validate_project(split)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(info["total_duration"], 6.25, places=2)
        # Denoise on a track renders; a gapped curve is rejected with fix.
        proj2 = {"id": "r6-dn", "version": 1,
                 "output": {"width": 360, "height": 640, "fps": 24},
                 "clips": [{"id": "a", "kind": "video",
                            "src": "data/test_media/clip1.mp4",
                            "trim_in": 0, "trim_out": 3}],
                 "audio": [{"src": "data/test_media/clip2.mp4",
                            "kind": "music", "volume": 0.8, "denoise": True}]}
        errors, _ = validate_project(proj2)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_denoise.mp4")
        ok, rinfo = render_project(proj2, out2)
        self.assertTrue(ok, rinfo)
        bad = dict(proj)
        bad["clips"] = [{**proj["clips"][0],
                         "speed_curve": [{"start": 0, "end": 2, "speed": 1.0},
                                         {"start": 3, "end": 5, "speed": 1.0}]}]
        errors, _ = validate_project(bad)
        self.assertTrue(any("fix" in e for e in errors))

    def test_animated_text_and_stroke(self):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=360x640", "-frames:v", "1",
                        os.path.join(BASE, "data", "test_black.png")], check=True)
        black = os.path.join(BASE, "data", "test_black.png")

        def white_total(path, t):
            cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            data = subprocess.run(cmd, capture_output=True).stdout
            n = len(data) // 3
            return sum(1 for i in range(n)
                       if data[3 * i] > 200 and data[3 * i + 1] > 200 and data[3 * i + 2] > 200)

        def white_strip(path, t, x, w):
            cmd = ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
                   "-vf", f"crop={w}:60:{x}:300", "-frames:v", "1",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
            data = subprocess.run(cmd, capture_output=True).stdout
            n = len(data) // 3
            return sum(1 for i in range(n)
                       if data[3 * i] > 200 and data[3 * i + 1] > 200 and data[3 * i + 2] > 200)
        base = {"id": "r7", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "image", "src": black, "duration": 3}]}
        # Slide-in: text at the left early, centred late.
        proj = dict(base); proj["id"] = "r7-slide"
        proj["texts"] = [{"text": "HELLO", "start": 0, "end": 3, "size": 48,
                          "x": "center", "y": 300, "animation": "slide_in",
                          "animation_duration": 1.0,
                          "stroke": 3, "stroke_color": "red"}]
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_slide.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        self.assertGreater(white_strip(out, 0.2, 0, 120), 100)
        self.assertEqual(white_strip(out, 0.2, 120, 120), 0)
        self.assertGreater(white_strip(out, 2.0, 120, 120), 500)
        # Pop: small early, full size late.
        proj = dict(base); proj["id"] = "r7-pop"
        proj["texts"] = [{"text": "HELLO", "start": 0, "end": 3, "size": 48,
                          "x": "center", "y": 300, "animation": "pop",
                          "animation_duration": 1.0}]
        out = os.path.join(BASE, "data", "test_pop.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        self.assertLess(white_total(out, 0.2), white_total(out, 2.0))
        # Bad animation carries a fix hint.
        bad = dict(base); bad["id"] = "r7-bad"
        bad["texts"] = [{"text": "X", "start": 0, "end": 2,
                         "animation": "spin"}]
        errors, _ = validate_project(bad)
        self.assertTrue(any("fix" in e for e in errors))

    def test_export_cap_3840_and_stabilize(self):
        proj = {"id": "r8-4k", "version": 1,
                "output": {"width": 3840, "height": 2160, "fps": 24},
                "clips": [{"id": "a", "kind": "video",
                           "src": "data/test_media/clip1.mp4",
                           "trim_in": 0, "trim_out": 2}]}
        errors, _ = validate_project(proj)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_4k.mp4")
        ok, info = render_project(proj, out)
        self.assertTrue(ok, info)
        probe = ffprobe(out)
        video = [x for x in probe["streams"] if x["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (3840, 2160))
        self.assertEqual(video["pix_fmt"], "yuv420p")
        bad = dict(proj)
        bad["output"] = {"width": 4000, "height": 2160, "fps": 24}
        errors, _ = validate_project(bad)
        self.assertTrue(any(e["code"] == "INVALID_DIMENSION" for e in errors))
        proj2 = {"id": "r8-stab", "version": 1,
                 "output": {"width": 360, "height": 640, "fps": 24},
                 "clips": [{"id": "a", "kind": "video",
                            "src": "data/test_media/clip1.mp4",
                            "trim_in": 0, "trim_out": 3, "stabilize": True}]}
        errors, _ = validate_project(proj2)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_stab.mp4")
        ok, info = render_project(proj2, out2)
        self.assertTrue(ok, info)
        self.assertAlmostEqual(float(ffprobe(out2)["format"]["duration"]), 3.0, delta=0.5)

    def test_agent_integration_summary_conformance_snapshots(self):
        from agentcut.render import (check_brief_conformance,
                                     produce_video, timeline_summary)
        from agentcut.server import (call_tool, _load_project,
                                     _save_project)
        # Timeline summary: segments, texts, audio, overlays.
        project = {"id": "int-sum", "version": 1,
                   "output": {"width": 360, "height": 640, "fps": 24},
                   "clips": [{"id": "a", "kind": "video",
                              "src": "data/test_media/clip1.mp4",
                              "trim_in": 0, "trim_out": 3},
                             {"id": "b", "kind": "image",
                              "src": "data/test_media/poster.png", "duration": 2,
                              "transition": "crossfade"}],
                   "texts": [{"text": "Hi", "start": 0.5, "end": 2.0}],
                   "overlays": [{"id": "pip", "kind": "image",
                                 "src": "data/test_media/poster.png",
                                 "start": 1.0, "duration": 2.0}]}
        ok, summary = timeline_summary(project)
        self.assertTrue(ok, summary)
        self.assertAlmostEqual(summary["total_duration"], 4.5, places=2)
        self.assertEqual([s["clip_id"] for s in summary["segments"]], ["a", "b"])
        self.assertAlmostEqual(summary["segments"][1]["start"], 2.5, places=2)
        self.assertEqual(len(summary["overlays"]), 1)
        # Conformance: pass case, then a missing-clip failure.
        good = {"id": "int-cb", "version": 1,
                "output": {"width": 720, "height": 720, "fps": 24},
                "clips": [{"id": "a", "kind": "image",
                           "src": "data/test_media/poster.png", "duration": 3}],
                "texts": [{"text": "T", "start": 0.5, "end": 3.0}]}
        conf = check_brief_conformance(
            {"format": "square", "title": "T",
             "clips": [{"src": "data/test_media/poster.png"}]}, good)
        self.assertTrue(conf["passed"], conf)
        conf2 = check_brief_conformance(
            {"format": "square", "title": "T",
             "clips": [{"src": "data/test_media/clip1.mp4"}]}, good)
        self.assertFalse(conf2["passed"])
        self.assertTrue(any(c["name"] == "clips_used" and not c["passed"]
                            for c in conf2["checks"]))
        # Produce carries conformance + overlay warnings.
        brief = {"id": "int-prod", "format": "square", "title": "T",
                 "target_duration": 5.5,
                 "clips": [{"src": "data/test_media/poster.png"},
                           {"src": "data/test_media/pattern.png"}],
                 "overlays": [{"id": "ghost", "kind": "image",
                               "src": "data/test_media/poster.png",
                               "start": 0.5, "duration": 1.0,
                               "opacity": 0.0}]}
        out = os.path.join(BASE, "data", "test_int_prod.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        self.assertTrue(info["conformance"]["passed"], info["conformance"])
        self.assertTrue(any("ghost" in w for w in info["qa"]["warnings"]))
        self.assertEqual(info["overlays"][0]["id"], "ghost")
        # Snapshots: patch, then roll back to the snapshot.
        proj = {"id": "int-snap", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "a", "kind": "video",
                           "src": "data/test_media/clip1.mp4",
                           "trim_in": 0, "trim_out": 3}]}
        self.assertIsNone(_save_project(proj))
        snap = call_tool("snapshot_project", {"id": "int-snap", "label": "v1"})
        self.assertNotIn("isError", snap)
        call_tool("patch_project", {"id": "int-snap", "patch": {
            "clips": [{"id": "a", "kind": "video",
                       "src": "data/test_media/clip1.mp4",
                       "trim_in": 0, "trim_out": 2}]}})
        self.assertEqual(_load_project("int-snap")["clips"][0]["trim_out"], 2)
        call_tool("rollback_project", {"id": "int-snap", "snapshot_id": "v1"})
        self.assertEqual(_load_project("int-snap")["clips"][0]["trim_out"], 3)
        # New-feature errors carry a fix hint (integration item a).
        bad = dict(project)
        bad["overlays"] = [{"id": "x", "kind": "image",
                            "src": "data/test_media/poster.png",
                            "start": 99, "duration": 1.0}]
        errors, _ = validate_project(bad)
        self.assertTrue(errors)
        self.assertTrue(all("fix" in e and "field" in e for e in errors))

    def test_probe_media(self):
        info = probe_media(os.path.join(BASE, "data", "test_media", "clip1.mp4"))
        self.assertAlmostEqual(info["duration"], 5.0, delta=0.2)
        self.assertTrue(info["has_video"])

    # ---- Pro pack 1.32.0 ----

    def _solid(self, name, color, size=160):
        path = os.path.join(BASE, "data", name)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c={color}:s={size}x{size}:d=2",
                        "-frames:v", "1", path], check=True)
        return path

    def _volumedetect(self, path, start, end):
        out = subprocess.run(
            ["ffmpeg", "-i", path, "-af",
             f"atrim=start={start}:end={end},volumedetect",
             "-vn", "-sn", "-f", "null", "-"],
            capture_output=True, text=True)
        import re as _re
        m = _re.search(r"mean_volume:\s*(-?[0-9.]+)", out.stderr)
        self.assertIsNotNone(m, out.stderr[-500:])
        return float(m.group(1))

    def test_easing_keyframes_nonlinear(self):
        # A red overlay moves x 0 -> 200 over 0..4s. At t=1 (u=0.25):
        # linear is at 50, ease_in at 12.5, ease_out at 87.5. Crops
        # separate all three: at x=30..40, only ease_in (12..102)
        # covers; at x=60..70, linear (50..140) and ease_in cover
        # but ease_out (87..177) does not; at t=3, x=160..170 is
        # covered by ease_in (112..202) and linear (150..240) but
        # not ease_out (187..277).
        red = os.path.join(BASE, "data", "test_red.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=red:s=200x200", "-frames:v", "1", red],
                       check=True)
        black = os.path.join(BASE, "data", "test_black_bg.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=360x640:d=5",
                        "-frames:v", "1", black], check=True)
        # A black still background, so every non-red crop is
        # guaranteed black regardless of any video content.
        clips = [{"id": "a", "kind": "image", "src": black, "duration": 5}]

        def render(easing, name):
            project = {"id": name, "version": 1,
                       "output": {"width": 360, "height": 640, "fps": 24},
                       "clips": clips,
                       "overlays": [{"id": "p", "kind": "image", "src": red,
                                     "start": 0, "duration": 4, "size": 0.25,
                                     "keyframes": [{"time": 0, "x": 0, "y": 0, "easing": easing},
                                                   {"time": 4, "x": 200, "y": 0, "easing": easing}]}]}
            errors, _ = validate_project(project)
            self.assertEqual(errors, [])
            out = os.path.join(BASE, "data", name + ".mp4")
            ok, info = render_project(project, out)
            self.assertTrue(ok, info)
            return out

        lin = render("linear", "test_ease_lin")
        ein = render("ease_in", "test_ease_in")
        eout = render("ease_out", "test_ease_out")
        self.assertLess(_crop_mean(lin, 1.0, 30, 10, 10, 10)[0], 80)
        self.assertGreater(_crop_mean(ein, 1.0, 30, 10, 10, 10)[0], 200)
        self.assertLess(_crop_mean(eout, 1.0, 30, 10, 10, 10)[0], 80)
        self.assertGreater(_crop_mean(lin, 1.0, 60, 10, 10, 10)[0], 200)
        self.assertGreater(_crop_mean(ein, 1.0, 60, 10, 10, 10)[0], 200)
        self.assertLess(_crop_mean(eout, 1.0, 60, 10, 10, 10)[0], 80)
        self.assertGreater(_crop_mean(ein, 3.0, 160, 10, 10, 10)[0], 200)
        self.assertLess(_crop_mean(eout, 3.0, 160, 10, 10, 10)[0], 80)
        # Unknown easing is a validation error with a fix hint.
        bad = {"id": "bad-ease", "version": 1,
               "output": {"width": 360, "height": 640, "fps": 24},
               "clips": clips,
               "overlays": [{"id": "p", "kind": "image", "src": red,
                             "start": 0, "duration": 2,
                             "keyframes": [{"time": 0, "x": 0},
                                           {"time": 2, "x": 100, "easing": "bounce"}]}]}
        errors, _ = validate_project(bad)
        self.assertTrue(errors)
        self.assertTrue(all("fix" in e for e in errors))

    def test_colour_grading_and_lut(self):
        blue = self._solid("test_blue_solid.png", "blue")
        # Dark COLOURED solid, not gray: FFmpeg colorbalance keeps
        # neutral gray neutral even under extreme shadow settings
        # (verified), so a gray probe cannot show a shadow tint.
        dark = self._solid("test_slate_dark.png", "0x28323C")
        bright = self._solid("test_gray192.png", "0xC0C0C0")

        def render(clip_extra, name, src=blue):
            project = {"id": name, "version": 1,
                       "output": {"width": 160, "height": 160, "fps": 12},
                       "clips": [{"id": "a", "kind": "image", "src": src,
                                  "duration": 2, **clip_extra}]}
            errors, _ = validate_project(project)
            self.assertEqual(errors, [])
            out = os.path.join(BASE, "data", name + ".mp4")
            ok, info = render_project(project, out)
            self.assertTrue(ok, info)
            return out

        def mean(path):
            return _crop_mean(path, 1.0, 40, 40, 80, 80)

        plain = mean(render({}, "test_grade_plain"))
        noir = mean(render({"grade": "noir"}, "test_grade_noir"))
        # Noir removes colour: channels (nearly) equal, unlike blue.
        self.assertLess(max(noir) - min(noir), 10)
        self.assertGreater(abs(plain[0] - plain[2]), 100)
        # Teal shadows: on a dark slate solid, the green-red and
        # blue-red gaps widen markedly (in: G-R 9, B-R 20).
        teal_dark = mean(render({"grade": "teal_orange"}, "test_grade_teal_dark", src=dark))
        self.assertGreater(teal_dark[1] - teal_dark[0], 20)
        self.assertGreater(teal_dark[2] - teal_dark[0], 30)
        # Orange highlights: on bright gray, red rises above blue.
        teal_bright = mean(render({"grade": "teal_orange"}, "test_grade_teal_bright", src=bright))
        self.assertGreater(teal_bright[0], teal_bright[2] + 5)
        # Warm: bright gray gains red over blue.
        warm = mean(render({"grade": "warm"}, "test_grade_warm", src=bright))
        self.assertGreater(warm[0], warm[2] + 5)
        # LUT: a red-boost .cube turns blue into magenta (R jumps).
        lut_path = os.path.join(BASE, "data", "test_redboost.cube")
        with open(lut_path, "w") as fh:
            fh.write('TITLE "redboost"\nLUT_3D_SIZE 2\n'
                     "1 0 0\n1 0 0\n1 1 0\n1 1 0\n"
                     "1 0 1\n1 0 1\n1 1 1\n1 1 1\n")
        lut_out = mean(render({"lut": lut_path}, "test_grade_lut"))
        self.assertGreater(lut_out[0], plain[0] + 100)
        # LUT validation: missing file and wrong extension, with fix.
        for bad_lut in ("data/does-not-exist.cube", "data/test_media/clip1.mp4"):
            bad = {"id": "bad-lut", "version": 1,
                   "output": {"width": 160, "height": 160, "fps": 12},
                   "clips": [{"id": "a", "kind": "image", "src": blue,
                              "duration": 2, "lut": bad_lut}]}
            errors, _ = validate_project(bad)
            self.assertTrue(errors)
            self.assertTrue(all("fix" in e for e in errors))

    def test_deterministic_ducking(self):
        music = os.path.join(BASE, "data", "test_music.wav")
        voice = os.path.join(BASE, "data", "test_voice.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=1000:duration=8", music], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=300:duration=4", voice], check=True)
        blue = self._solid("test_blue_solid.png", "blue")
        # Voice volume 0: the mix is ducked music alone, so
        # volumedetect measures the automation directly. Voice
        # window is derived: start 2.0 + 4s source = 2..6.
        project = {"id": "duck-test", "version": 1,
                   "output": {"width": 160, "height": 160, "fps": 12},
                   "clips": [{"id": "a", "kind": "image", "src": blue, "duration": 8}],
                   "audio": [{"src": music, "kind": "music", "volume": 1.0},
                             {"src": voice, "kind": "voice", "volume": 0.0, "start": 2.0}],
                   "ducking": {"voice_track": 1, "amount_db": 12, "fade": 0.25}}
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        self.assertEqual(info["ducking"]["windows"], [{"start": 2.0, "end": 6.0}])
        from agentcut.render import build_ffmpeg_args
        import shutil as _shutil
        argv, _, _td = build_ffmpeg_args(project, info, "/tmp/x.mp4")
        if _td:
            _shutil.rmtree(_td, ignore_errors=True)
        graph = " ".join(argv)
        self.assertIn("eval=frame", graph)
        self.assertNotIn("sidechaincompress", graph)
        out = os.path.join(BASE, "data", "test_ducking.mp4")
        ok, rinfo = render_project(project, out)
        self.assertTrue(ok, rinfo)
        only = self._volumedetect(out, 0.5, 1.5)
        ducked = self._volumedetect(out, 3.0, 5.0)
        after = self._volumedetect(out, 6.5, 7.5)
        self.assertAlmostEqual(only - ducked, 12.0, delta=2.0)
        self.assertAlmostEqual(only - after, 0.0, delta=2.0)
        # Explicit windows: duck 1..3 only, whatever the voice does.
        project2 = {"id": "duck-test2", "version": 1,
                    "output": {"width": 160, "height": 160, "fps": 12},
                    "clips": [{"id": "a", "kind": "image", "src": blue, "duration": 8}],
                    "audio": [{"src": music, "kind": "music", "volume": 1.0},
                              {"src": voice, "kind": "voice", "volume": 0.0, "start": 0.0}],
                    "ducking": {"voice_track": 1, "amount_db": 12, "fade": 0.25,
                                "windows": [{"start": 1, "end": 3}]}}
        errors, _ = validate_project(project2)
        self.assertEqual(errors, [])
        out2 = os.path.join(BASE, "data", "test_ducking2.mp4")
        ok, rinfo = render_project(project2, out2)
        self.assertTrue(ok, rinfo)
        self.assertAlmostEqual(
            self._volumedetect(out2, 4.0, 6.0) - self._volumedetect(out2, 1.5, 2.5),
            12.0, delta=2.0)

    def test_chapters_and_delivery_presets(self):
        from agentcut.render import produce_video, produce_variants
        blue = self._solid("test_blue_solid.png", "blue")
        project = {"id": "ch-test", "version": 1,
                   "output": {"width": 160, "height": 160, "fps": 12},
                   "clips": [{"id": "a", "kind": "image", "src": blue, "duration": 6}],
                   "chapters": [{"title": "Intro", "start": 0},
                                {"title": "End", "start": 4}]}
        errors, _ = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(BASE, "data", "test_chapters.mp4")
        ok, info = render_project(project, out)
        self.assertTrue(ok, info)
        # The shared ffprobe() helper shows format/streams only;
        # chapters need their own ffprobe call.
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_chapters", out], capture_output=True, text=True).stdout)
        chapters = probe.get("chapters", [])
        self.assertEqual([c["tags"]["title"] for c in chapters], ["Intro", "End"])
        self.assertEqual([float(c["start_time"]) for c in chapters], [0.0, 4.0])
        # Delivery presets via produce_variants, with sizes probed.
        # No explicit format here on purpose: an explicit format
        # wins over a preset (tested below), so a format in this
        # brief would make every variant square.
        brief = {"id": "preset-test",
                 "clips": [{"src": "data/test_media/poster.png", "duration": 2}],
                 "soundtrack": "data/test_media/clip1.mp4"}
        out_dir = os.path.join(BASE, "data", "test_preset_variants")
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
        ok, info = produce_variants(
            {**brief, "delivery_presets": ["shorts_720", "instagram_square"]}, out_dir)
        self.assertTrue(ok, info)
        self.assertEqual((info["variants"]["shorts_720"]["width"],
                          info["variants"]["shorts_720"]["height"]), (720, 1280))
        self.assertEqual((info["variants"]["instagram_square"]["width"],
                          info["variants"]["instagram_square"]["height"]), (1080, 1080))
        # Explicit format wins over a preset's size, and the result
        # says so instead of silently applying the preset.
        ok, info = produce_video(
            {**brief, "delivery_preset": "youtube_1080", "format": "square"},
            os.path.join(BASE, "data", "test_preset_override.mp4"))
        self.assertTrue(ok, info)
        self.assertEqual((info["width"], info["height"]), (720, 720))
        self.assertEqual(info["delivery_preset_applied"]["size_source"], "format")
        # Preset alone gives its own size, fps and bitrate target.
        ok, info = produce_video(
            {**brief, "delivery_preset": "youtube_1080"},
            os.path.join(BASE, "data", "test_preset_yt.mp4"))
        self.assertTrue(ok, info)
        self.assertEqual((info["width"], info["height"]), (1920, 1080))
        self.assertEqual(info["fps"], 30)
        self.assertEqual(info["delivery_preset_applied"]["video_bitrate"], "8M")
        # Unknown preset is a structured error, not a silent default.
        ok, info = produce_video(
            {**brief, "delivery_preset": "nope_123"},
            os.path.join(BASE, "data", "test_preset_bad.mp4"))
        self.assertFalse(ok)
        self.assertIn("delivery_preset", str(info))
        # Sections become chapters in produce results and in the file.
        brief2 = {"id": "sec-ch-test", "format": "square",
                  "sections": [{"title": "Intro", "clips": [{"src": "data/test_media/poster.png", "duration": 2}]},
                               {"title": "Finale", "clips": [{"src": "data/test_media/pattern.png", "duration": 2}]}]}
        ok, info = produce_video(brief2, os.path.join(BASE, "data", "test_sec_chapters.mp4"))
        self.assertTrue(ok, info)
        self.assertEqual([c["title"] for c in info["chapters"]], ["Intro", "Finale"])
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_chapters", info["output"]], capture_output=True, text=True).stdout)
        self.assertEqual([c["tags"]["title"] for c in probe.get("chapters", [])],
                         ["Intro", "Finale"])

    def test_producer_qa_loudness_peak_freeze(self):
        from agentcut.render import produce_video
        # Still image + music: video frozen throughout, audio loud.
        brief = {"id": "qa-pro", "format": "square",
                 "clips": [{"src": "data/test_media/poster.png", "duration": 4}],
                 "soundtrack": "data/test_media/clip1.mp4"}
        out = os.path.join(BASE, "data", "test_qa_pro.mp4")
        ok, info = produce_video(brief, out)
        self.assertTrue(ok, info)
        qa = info["qa"]
        self.assertIn("loudness", qa)
        self.assertIsInstance(qa["loudness"], dict)
        self.assertIsInstance(qa["loudness"]["input_i"], float)
        self.assertIsInstance(qa["audio_peak_db"], float)
        self.assertLess(qa["audio_peak_db"], 0.0)
        self.assertIsInstance(qa["freeze_segments"], list)
        self.assertGreaterEqual(qa["freeze_duration"], 3.0)
        self.assertTrue(any("frozen" in w for w in qa["warnings"]))
        # Black/silence QA still present alongside the new fields.
        self.assertIn("black_segments", qa)
        self.assertIn("silence_segments", qa)

    def test_review_aids_preview_only(self):
        from agentcut.render import build_ffmpeg_args, preview_project
        blue = self._solid("test_blue_solid.png", "blue")
        project = {"id": "review-test", "version": 1,
                   "output": {"width": 160, "height": 160, "fps": 12},
                   "clips": [{"id": "a", "kind": "image", "src": blue, "duration": 4}]}
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        import shutil as _shutil
        argv, _, _td = build_ffmpeg_args(project, info, "/tmp/x.mp4")
        if _td:
            _shutil.rmtree(_td, ignore_errors=True)
        self.assertNotIn("drawbox", " ".join(argv))
        self.assertNotIn("drawtext", " ".join(argv))
        argv, _, _td = build_ffmpeg_args(project, info, "/tmp/x.mp4",
                                         guides=True, review_timecode=True)
        if _td:
            _shutil.rmtree(_td, ignore_errors=True)
        graph = " ".join(argv)
        self.assertIn("drawbox", graph)
        self.assertIn("drawtext", graph)
        # Final render of the same project: no guides, no timecode.
        out = os.path.join(BASE, "data", "test_review_final.mp4")
        ok, rinfo = render_project(project, out)
        self.assertTrue(ok, rinfo)
        # Preview: guides + timecode flagged, and the guide box is
        # visible in the proxy frame where the final frame is blue.
        out_dir = os.path.join(BASE, "data", "test_review_preview")
        ok, prev = preview_project(project, out_dir, guides=True, timecode=True)
        self.assertTrue(ok, prev)
        self.assertTrue(prev["guides"])
        self.assertTrue(prev["timecode"])
        self.assertEqual(prev["safe_areas"]["action_safe"], "5% margin")
        _w, _h, px_prev = _frame_png(prev["proxy"], 1.0,
                                     os.path.join(BASE, "data", "test_review_prev.png"))
        _w2, _h2, px_fin = _frame_png(out, 1.0,
                                      os.path.join(BASE, "data", "test_review_fin.png"))
        # Proxy is half scale (80x80): action-safe border at x=4.
        self.assertGreater(px_prev(4, 40)[0], 200)  # yellow box
        self.assertLess(px_fin(8, 80)[0], 80)       # plain blue
        # Explicit project timecode DOES reach a final render.
        project_tc = dict(project)
        project_tc["timecode"] = True
        errors, info_tc = validate_project(project_tc)
        self.assertEqual(errors, [])
        argv, _, _td = build_ffmpeg_args(project_tc, info_tc, "/tmp/x.mp4")
        if _td:
            _shutil.rmtree(_td, ignore_errors=True)
        self.assertIn("drawtext", " ".join(argv))

    def test_percent_caption_renders(self):
        # Regression (found in the island-short production 2026-10-28):
        # drawtext expands %{...} in textfile content too, so a stray %
        # made the whole overlay render empty (a caption with "80%"
        # was invisible; the same caption without % was visible; %%
        # did not help). Text overlays now set expansion=none, so a
        # literal % must render like any other character.
        from agentcut.render import render_project
        srt = os.path.join(BASE, "data", "test_percent.srt")
        with open(srt, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:03,000\n"
                     "Air is 80% here\n")
        project = {"id": "percent-test", "version": 1,
                   "output": {"width": 360, "height": 640, "fps": 12},
                   "clips": [{"id": "a", "kind": "image",
                              "src": os.path.join(BASE, "data", "test_media", "pattern.png"),
                              "duration": 3}],
                   "captions_file": srt}
        out = os.path.join(BASE, "data", "test_percent.mp4")
        ok, rinfo = render_project(project, out)
        self.assertTrue(ok, rinfo)
        _w, _h, px = _frame_png(out, 1.5,
                                os.path.join(BASE, "data", "test_percent_frame.png"))
        bright = 0
        for y in range(500, 580, 2):
            for x in range(0, 360, 2):
                r, g, b = px(x, y)[:3]
                if r > 180 and g > 180 and b > 180:
                    bright += 1
        self.assertGreater(bright, 50)


    def test_bidi_display_text_transform(self):
        # v1.33.0: one function, applied at textfile-write time.
        # Arabic lines get RLE...PDF with RLM before trailing
        # punctuation; Latin-only lines are byte-identical.
        from agentcut.render import bidi_display_text
        self.assertEqual(bidi_display_text("2 دولار"),
                         "\u202b2 دولار\u202c")
        self.assertEqual(bidi_display_text("80٪ هواء!"),
                         "\u202b80٪ هواء\u200f!\u202c")
        self.assertEqual(bidi_display_text("80% هواء!"),
                         "\u202b80% هواء\u200f!\u202c")
        self.assertEqual(
            bidi_display_text("عشان تشتري جزيرة خاصة وتعيش ملك،"),
            "\u202bعشان تشتري جزيرة خاصة وتعيش ملك\u200f،\u202c")
        self.assertEqual(bidi_display_text("اقتصاد القرد 2"),
                         "\u202bاقتصاد القرد 2\u202c")
        self.assertEqual(bidi_display_text("Hello world"), "Hello world")
        self.assertEqual(bidi_display_text("80% off"), "80% off")
        # Idempotent: an already-wrapped line is not double-wrapped.
        wrapped = bidi_display_text("2 دولار")
        self.assertEqual(bidi_display_text(wrapped), wrapped)

    def test_bidi_arabic_renders(self):
        # Render smoke test: an Arabic keyword renders (frame
        # non-empty in the text band). Order correctness is proven
        # by eye on frames, recorded in STATE.md (RLE frames for
        # "2 دولار", "80٪ هواء!" and a full sentence) — the
        # transform alone is never claimed as proof.
        from agentcut.render import render_project
        project = {"id": "bidi-test", "version": 1,
                   "output": {"width": 360, "height": 640, "fps": 12},
                   "clips": [{"id": "a", "kind": "image",
                              "src": os.path.join(BASE, "data", "test_media", "pattern.png"),
                              "duration": 3}],
                   "texts": [{"text": "2 دولار", "color": "yellow",
                              "size": 54, "x": "center", "y": 200,
                              "start": 0, "end": 3}]}
        out = os.path.join(BASE, "data", "test_bidi.mp4")
        ok, rinfo = render_project(project, out)
        self.assertTrue(ok, rinfo)
        _w, _h, px = _frame_png(out, 1.5,
                                os.path.join(BASE, "data", "test_bidi_frame.png"))
        bright = 0
        for y in range(180, 280, 2):
            for x in range(0, 360, 2):
                r, g, b = px(x, y)[:3]
                if r > 180 and g > 180 and b < 120:
                    bright += 1
        self.assertGreater(bright, 20)

    def test_layer_text_overlap_warning(self):
        # v1.33.0: geometric check, warnings only (never errors).
        # Old island layout (badge gliding at y=240 across keyword
        # rows at y=340) MUST warn naming badge + keyword; the new
        # layout (badge at y=1030, below the panel, above the
        # captions at y=1160) MUST NOT warn.
        def _project(badge_y):
            badge = os.path.join(BASE, "data", "test_badge.png")
            if not os.path.isfile(badge):
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                                "-i", "color=c=0xFFCC00:s=200x200",
                                "-frames:v", "1", badge], check=True)
            return {"id": "overlap-test", "version": 1,
                    "output": {"width": 720, "height": 1280, "fps": 12},
                    "clips": [{"id": "a", "kind": "image",
                               "src": os.path.join(BASE, "data", "test_media", "pattern.png"),
                               "duration": 10}],
                    "texts": [{"text": "2 دولار", "color": "yellow",
                               "size": 54, "x": "center", "y": 340,
                               "start": 0, "end": 10}],
                    "captions": [{"text": "عبي هذا الهواء في قوارير",
                                  "start": 0, "end": 10}],
                    "overlays": [{"id": "badge", "kind": "image",
                                  "src": badge,
                                  "start": 0, "duration": 10, "size": 0.15,
                                  "keyframes": [{"time": 0, "x": 20, "y": badge_y,
                                                "scale": 1.0, "opacity": 1.0,
                                                "easing": "ease_in_out"},
                                               {"time": 10, "x": 520, "y": badge_y,
                                                "scale": 1.0, "opacity": 1.0,
                                                "easing": "ease_in_out"}]}]}
        errors, info = validate_project(_project(240))
        self.assertEqual(errors, [])  # warnings never block
        warns = [w for w in info.get("warnings", [])
                 if w.get("code") == "layer_text_overlap_warning"]
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]["overlay_id"], "badge")
        self.assertEqual(warns[0]["text"], "2 دولار")
        errors2, info2 = validate_project(_project(1030))
        self.assertEqual(errors2, [])
        warns2 = [w for w in info2.get("warnings", [])
                  if w.get("code") == "layer_text_overlap_warning"]
        self.assertEqual(warns2, [])


    def test_loop134_server_survives_non_object_json(self):
        # 1.34.0 F1: a parsed non-object JSON line and non-dict
        # params used to kill serve() with AttributeError. Each
        # malformed line must now get a JSON-RPC error and the
        # server must stay alive for a later ping.
        proc = subprocess.Popen(
            [os.path.join(BASE, "run.sh"), "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            cwd=BASE)
        try:
            def send(line):
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
                return proc.stdout.readline()
            r = send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "1"}}}))
            self.assertIn('"id": 1', r)
            r = send("[1,2,3]")
            self.assertIn("-32600", r)
            r = send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": "not-a-dict"}))
            self.assertIn("INVALID_SCHEMA", r)
            r = send("{not json")
            self.assertIn("-32700", r)
            self.assertIsNone(proc.poll())
            r = send(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}))
            self.assertIn('"id": 9', r)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    def test_loop134_id_traversal_rejected(self):
        # 1.34.0 F2: load endpoints must reject ids outside
        # [A-Za-z0-9_-]+ instead of passing them into paths
        # (list_snapshots used to makedirs() for arbitrary ids).
        from agentcut.server import call_tool
        out = call_tool("get_project", {"id": "../../etc/passwd"})
        self.assertTrue(out.get("isError"))
        self.assertIn("INVALID_SCHEMA", json.dumps(out))
        out = call_tool("get_job", {"id": "../x"})
        self.assertTrue(out.get("isError"))
        evil = os.path.join(BASE, "data", "loop134evil")
        self.assertFalse(os.path.exists(evil))
        out = call_tool("list_snapshots", {"id": "../loop134evil"})
        self.assertTrue(out.get("isError"))
        self.assertFalse(os.path.exists(evil))

    def test_loop134_text_geometry_validation(self):
        # 1.34.0 F3a: size/x/y are validated, not left to FFmpeg.
        base = {"id": "geo", "version": 1,
                "output": {"width": 160, "height": 160, "fps": 12},
                "clips": [{"id": "a", "kind": "image",
                           "src": os.path.join(BASE, "data", "test_media", "poster.png"),
                           "duration": 2}]}
        import copy
        for bad_text in ({"text": "Hi", "size": "big", "start": 0, "end": 1},
                         {"text": "Hi", "size": -5, "start": 0, "end": 1},
                         {"text": "Hi", "size": 0, "start": 0, "end": 1},
                         {"text": "Hi", "size": 241, "start": 0, "end": 1},
                         {"text": "Hi", "x": "foo", "y": 10, "start": 0, "end": 1},
                         {"text": "Hi", "x": "center", "y": -10, "start": 0, "end": 1},
                         {"text": "Hi", "x": "center", "y": "ten", "start": 0, "end": 1}):
            p = copy.deepcopy(base)
            p["texts"] = [bad_text]
            errors, _ = validate_project(p)
            self.assertTrue(errors, bad_text)
            self.assertTrue(any("fix" in e for e in errors), errors)
        p = copy.deepcopy(base)
        p["texts"] = [{"text": "Hi", "size": 48, "x": "center", "y": 80,
                       "start": 0, "end": 1}]
        errors, _ = validate_project(p)
        self.assertEqual(errors, [])
        p = copy.deepcopy(base)
        p["captions"] = [{"text": "cap", "x": 10, "y": 10, "start": 0, "end": 1}]
        errors, _ = validate_project(p)
        self.assertTrue(errors)  # captions ignore x/y in render

    def test_loop134_ignored_fields_rejected(self):
        # 1.34.0 F3b: fields render ignores must not validate clean.
        vid = os.path.join(BASE, "data", "test_media", "clip1.mp4")
        img = os.path.join(BASE, "data", "test_media", "poster.png")
        p = {"id": "ign", "version": 1,
             "output": {"width": 160, "height": 160, "fps": 12},
             "clips": [{"id": "a", "kind": "video", "src": vid,
                        "trim_in": 0, "trim_out": 3, "duration": 3}]}
        errors, _ = validate_project(p)
        self.assertTrue(errors)
        p["clips"] = [{"id": "a", "kind": "image", "src": img,
                       "duration": 2, "trim_in": 0}]
        errors, _ = validate_project(p)
        self.assertTrue(errors)

    def test_loop134_broken_media_rejected(self):
        # 1.34.0 F3c: ffprobe "succeeds" on garbage named .png
        # (0x0) and an audio-only file validates as a video clip.
        bad = os.path.join(BASE, "data", "test_broken134.png")
        with open(bad, "w") as fh:
            fh.write("not an image at all")
        voice = os.path.join(BASE, "data", "test_voice.wav")
        for src in (bad, voice):
            p = {"id": "brk", "version": 1,
                 "output": {"width": 160, "height": 160, "fps": 12},
                 "clips": [{"id": "a", "kind": "video" if src == voice else "image",
                            "src": src, "duration": 2}]}
            if src == voice:
                p["clips"][0].pop("duration")
                p["clips"][0].update({"trim_in": 0, "trim_out": 1})
            errors, _ = validate_project(p)
            self.assertTrue(errors, src)

    def test_loop134_silent_loudnorm_structured(self):
        # 1.34.0 F3d: loudnorm on digital silence used to fail
        # inside FFmpeg with a NaN/AAC error; now a structured
        # error names the track and the fix.
        silent = os.path.join(BASE, "data", "test_silent134.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=48000:cl=stereo", "-t", "2",
                        silent], check=True)
        p = {"id": "sil", "version": 1,
             "output": {"width": 160, "height": 160, "fps": 12},
             "clips": [{"id": "a", "kind": "image",
                        "src": os.path.join(BASE, "data", "test_media", "poster.png"),
                        "duration": 2}],
             "audio": [{"src": silent, "kind": "music", "loudnorm": True}]}
        ok, info = render_project(p, os.path.join(BASE, "data", "test_silent134.mp4"))
        self.assertFalse(ok)
        self.assertIn("loudnorm", json.dumps(info))
        self.assertIn("fix", info)

    def test_loop134_text_overflow_warning(self):
        # 1.34.0 F4: a caption wider than the frame warns instead
        # of clipping silently; a short caption does not warn.
        img = os.path.join(BASE, "data", "test_media", "poster.png")
        base = {"id": "ovf", "version": 1,
                "output": {"width": 160, "height": 160, "fps": 12},
                "clips": [{"id": "a", "kind": "image", "src": img, "duration": 2}]}
        import copy
        p = copy.deepcopy(base)
        p["captions"] = [{"text": "This caption is far too long for a 160 pixel wide frame",
                          "start": 0, "end": 1}]
        _, info = validate_project(p)
        self.assertTrue(any(w.get("code") == "text_overflow_warning"
                            for w in info.get("warnings", [])), info.get("warnings"))
        p = copy.deepcopy(base)
        p["captions"] = [{"text": "Short", "start": 0, "end": 1}]
        _, info = validate_project(p)
        self.assertFalse(any(w.get("code") == "text_overflow_warning"
                             for w in info.get("warnings", [])))

    def test_loop134_overlay_box_per_axis(self):
        # 1.34.0 F5: estimation must match render's per-axis x/y
        # (an x-only overlay keeps the preset y, not the preset x).
        from agentcut.validate import _overlay_box_at
        ov = {"id": "o", "kind": "image", "src": "x.png", "duration": 2,
              "start": 0, "size": 0.35, "position": "bottom_right",
              "x": 0, "probe": {"width": 100, "height": 100}}
        box = _overlay_box_at(ov, 0.5, 160, 160)
        # bw = 0.35*160 = 56; preset bottom_right y = 160-56-20 = 84.
        self.assertEqual(box, (0.0, 84.0, 56.0, 56.0))

    def test_loop134_validate_does_not_mutate_audio(self):
        # 1.34.0 F6: validation must not rewrite the caller's
        # audio tracks, even from another working directory.
        import copy
        img = os.path.join(BASE, "data", "test_media", "poster.png")
        p = {"id": "pure", "version": 1,
             "output": {"width": 160, "height": 160, "fps": 12},
             "clips": [{"id": "a", "kind": "image", "src": img, "duration": 2}],
             "audio": [{"src": "data/test_media/clip1.mp4", "kind": "music"}]}
        before = copy.deepcopy(p)
        old = os.getcwd()
        try:
            os.chdir("/tmp")
            errors, info = validate_project(p)
            self.assertEqual(errors, [])
            self.assertEqual(p, before)
            self.assertEqual(info["audio"][0]["src"],
                             os.path.join(BASE, "data", "test_media", "clip1.mp4"))
            # And a render from the other directory still resolves.
            ok, rinfo = render_project(p, "/tmp/test_pure134.mp4")
            self.assertTrue(ok, rinfo)
        finally:
            os.chdir(old)

    def test_loop134_produce_bad_numbers_structured(self):
        # 1.34.0 F7: non-numeric brief numbers are structured
        # errors, not ValueError tracebacks.
        from agentcut.render import produce_video
        img = os.path.join(BASE, "data", "test_media", "poster.png")
        ok, info = produce_video(
            {"id": "badnum", "format": "square",
             "clips": [{"src": img, "duration": "abc"}]},
            os.path.join(BASE, "data", "test_badnum134.mp4"))
        self.assertFalse(ok)
        self.assertEqual(info["code"], "INVALID_SCHEMA")
        vid = os.path.join(BASE, "data", "test_media", "clip1.mp4")
        ok, info = produce_video(
            {"id": "badnum2", "format": "square",
             "clips": [{"src": vid, "trim_in": 0, "trim_out": 3, "speed": "fast"}]},
            os.path.join(BASE, "data", "test_badnum134b.mp4"))
        self.assertFalse(ok)
        self.assertEqual(info["code"], "INVALID_SCHEMA")

    def test_loop134_cli_bad_numbers_no_traceback(self):
        # 1.34.0 F7 (CLI): bad numeric CLI arguments print an
        # error and exit non-zero, without a traceback.
        proc = subprocess.run(
            [os.path.join(BASE, "run.sh"), "split",
             os.path.join(BASE, "examples", "basic.json"), "c1", "notanumber"],
            capture_output=True, text=True, cwd=BASE, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)


if __name__ == "__main__":
    unittest.main()
