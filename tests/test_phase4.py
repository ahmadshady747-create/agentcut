"""Tests for Phase 4: Platforms & Extensibility (Auto-Reframe, Plugins, Web Studio)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.motion import MotionCanvas
from agentcut.plugins import (
    MotionPlugin,
    get_all_motion_kinds,
    get_diagram_plugin,
    get_motion_plugin,
    is_known_motion_kind,
    list_diagram_plugins,
    list_motion_plugins,
    register_diagram_plugin,
    register_motion_plugin,
)
from agentcut.reframe import (
    ASPECT_PRESETS,
    build_reframe_filter,
    parse_target_dimensions,
    probe_video_geometry,
    reframe_to_variants,
    reframe_video,
)
from agentcut.studio import _extract_timeline_model, generate_studio_html
from agentcut.validate import validate_project


class TestPhase4(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut_p4_")
        # Generate a small 1-second 640x360 16:9 test video with audio
        self.src_video = os.path.join(self.tmp, "src_16_9.mp4")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:duration=1.0:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            self.src_video
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to create test video: {res.stderr}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Reframe Tests
    # ------------------------------------------------------------------

    def test_probe_video_geometry(self):
        geo = probe_video_geometry(self.src_video)
        self.assertEqual(geo["width"], 640)
        self.assertEqual(geo["height"], 360)
        self.assertEqual(geo["fps"], 30.0)
        self.assertTrue(geo["has_audio"])
        self.assertAlmostEqual(geo["duration"], 1.0, delta=0.2)

    def test_parse_target_dimensions(self):
        # 9:16 vertical
        w, h = parse_target_dimensions("9:16")
        self.assertEqual((w, h), (1080, 1920))

        # 9:16 draft (0.5x)
        w, h = parse_target_dimensions("9:16", scale_factor=0.5)
        self.assertEqual((w, h), (540, 960))

        # 1:1 square
        w, h = parse_target_dimensions("1:1")
        self.assertEqual((w, h), (1080, 1080))

        # 16:9 landscape
        w, h = parse_target_dimensions("16:9")
        self.assertEqual((w, h), (1920, 1080))

    def test_reframe_blur_pad(self):
        out = os.path.join(self.tmp, "reframed_916_blur.mp4")
        ok, info = reframe_video(
            self.src_video,
            out,
            target_aspect="9:16",
            mode="blur_pad",
            draft=True,  # 540x960 for fast test execution
        )
        self.assertTrue(ok, f"Reframe failed: {info}")
        self.assertTrue(os.path.isfile(out))
        self.assertGreater(os.path.getsize(out), 1000)

        geo = probe_video_geometry(out)
        self.assertEqual(geo["width"], 540)
        self.assertEqual(geo["height"], 960)
        self.assertTrue(geo["has_audio"])

    def test_reframe_crop(self):
        out = os.path.join(self.tmp, "reframed_11_crop.mp4")
        ok, info = reframe_video(
            self.src_video,
            out,
            target_aspect="1:1",
            mode="crop",
            draft=True,  # 540x540
        )
        self.assertTrue(ok, f"Crop failed: {info}")
        self.assertTrue(os.path.isfile(out))

        geo = probe_video_geometry(out)
        self.assertEqual(geo["width"], 540)
        self.assertEqual(geo["height"], 540)

    def test_reframe_fit(self):
        out = os.path.join(self.tmp, "reframed_916_fit.mp4")
        ok, info = reframe_video(
            self.src_video,
            out,
            target_aspect="9:16",
            mode="fit",
            draft=True,
        )
        self.assertTrue(ok, f"Fit failed: {info}")
        self.assertTrue(os.path.isfile(out))

        geo = probe_video_geometry(out)
        self.assertEqual(geo["width"], 540)
        self.assertEqual(geo["height"], 960)

    def test_reframe_to_variants(self):
        var_dir = os.path.join(self.tmp, "variants")
        ok, info = reframe_to_variants(
            self.src_video,
            var_dir,
            aspects=("9:16", "1:1"),
            mode="blur_pad",
            draft=True,
        )
        self.assertTrue(ok, f"Variants failed: {info}")
        self.assertIn("9:16", info["variants"])
        self.assertIn("1:1", info["variants"])
        self.assertTrue(os.path.isfile(info["variants"]["9:16"]["output_path"]))
        self.assertTrue(os.path.isfile(info["variants"]["1:1"]["output_path"]))

    # ------------------------------------------------------------------
    # 2. Plugin Architecture Tests
    # ------------------------------------------------------------------

    def test_built_in_motion_plugins(self):
        plugins = list_motion_plugins()
        self.assertIn("progress_bar", plugins)
        self.assertIn("badge", plugins)

        self.assertTrue(is_known_motion_kind("progress_bar"))
        self.assertTrue(is_known_motion_kind("badge"))

        # Test rendering a frame of progress_bar
        pb_plugin = get_motion_plugin("progress_bar")
        self.assertIsNotNone(pb_plugin)
        canvas = MotionCanvas(640, 100)
        el = {
            "kind": "progress_bar",
            "start": 0.0,
            "end": 2.0,
            "x": 50,
            "y": 30,
            "w": 500,
            "h": 24,
            "progress_start": 0.0,
            "progress_end": 1.0,
        }
        pb_plugin.render(canvas, el, t=1.0, ox=0, oy=0)
        raw_bytes = canvas.bytes()
        self.assertEqual(len(raw_bytes), 640 * 100 * 4)

    def test_custom_motion_plugin_registration(self):
        class SparklePlugin(MotionPlugin):
            name = "sparkle_box"

            def bbox(self, el):
                return (10, 10, 80, 80)

            def render(self, canvas, el, t, ox, oy):
                canvas.fill_circle(50 - ox, 50 - oy, 20, (255, 220, 50), 0.9)

        register_motion_plugin("sparkle_box", SparklePlugin())
        self.assertIn("sparkle_box", list_motion_plugins())
        self.assertTrue(is_known_motion_kind("sparkle_box"))

        # Project validation with custom motion plugin
        project = {
            "version": 1,
            "id": "test_plugin_proj",
            "clips": [
                {"id": "c1", "kind": "video", "src": self.src_video, "trim_in": 0.0, "trim_out": 1.0}
            ],
            "motion": [
                {"id": "sp1", "kind": "sparkle_box", "start": 0.1, "end": 0.8}
            ]
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [], f"Plugin validation should succeed: {errors}")
        self.assertEqual(info["motion"][0]["kind"], "sparkle_box")

    def test_custom_diagram_plugin_registration(self):
        def custom_radar_builder(params):
            strokes = [
                {"kind": "circle", "center": [300, 300], "radius": 150, "color": "cyan", "width": 4, "alpha": 0.9}
            ]
            return 600, 600, strokes

        register_diagram_plugin("radar_chart", custom_radar_builder)
        self.assertIn("radar_chart", list_diagram_plugins())

        from agentcut.diagrams import build_template
        cw, ch, strokes = build_template("radar_chart", {})
        self.assertEqual((cw, ch), (600, 600))
        self.assertEqual(len(strokes), 1)

    # ------------------------------------------------------------------
    # 3. Interactive Web Studio Tests
    # ------------------------------------------------------------------

    def test_web_studio_generator(self):
        brief = {
            "id": "test_brief_studio",
            "title": "Quantum Computing 101",
            "scenes": [
                {
                    "id": "intro",
                    "title": "Introduction",
                    "duration": 4.0,
                    "narration_text": "مرحباً بكم في عالم الحوسبة الكمية",
                    "kinetic": True,
                    "transition": "whip_pan",
                },
                {
                    "id": "concept",
                    "title": "Superposition",
                    "duration": 6.0,
                    "narration_text": "البت الكمي يمكن أن يكون صفراً وواحداً في نفس اللحظة",
                    "diagram": {"template": "timeline", "steps": 3},
                    "transition": "glitch",
                }
            ]
        }

        # Model extraction
        model = _extract_timeline_model(brief, self.src_video)
        self.assertEqual(model["title"], "Quantum Computing 101")
        self.assertEqual(len(model["scenes"]), 2)
        self.assertEqual(model["total_duration"], 10.0)
        self.assertTrue(model["scenes"][0]["kinetic"])

        # Studio HTML generation
        out_html = os.path.join(self.tmp, "studio.html")
        path = generate_studio_html(brief, video_path=self.src_video, output_html_path=out_html)
        self.assertTrue(os.path.isfile(path))

        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()

        self.assertIn("AGENTCUT", content)
        self.assertIn("Quantum Computing 101", content)
        self.assertIn("SCENES", content)
        self.assertIn("NARRATION", content)
        self.assertIn("playhead", content)
        self.assertIn("مرحباً بكم في عالم الحوسبة الكمية", content)


if __name__ == "__main__":
    unittest.main()
