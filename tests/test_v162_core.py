"""Tests for AgentCut v1.62.0 core changes (Task 2):

- treatment.grain_mode (off | frozen | temporal) + the frozen
  default, and the 1.60.0 treatment-key whitelist fix (blur /
  brightness / contrast / saturation).
- Draft-mode text scaling (scale_factor < 1.0 halves text; 1.0
  is unchanged).
- Cache unification (scene cache + segment cache share one
  .agentcut_cache dir; clear_cache removes both).
- Reframe unification (one windows-crop builder, one "W:H"
  parser, one track-interp expression).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut import cache as cache_mod
from agentcut.reframe import (
    build_windows_crop_filter,
    parse_aspect_ratio,
    track_interp_expr,
)
from agentcut.render import (
    DEFAULT_GRAIN_MODE,
    _track_interp_expr,
    _treatment_filters,
    build_ffmpeg_args,
)
from agentcut.validate import validate_project


def _project_with_treatment(treatment, level="clip"):
    project = {
        "id": "t", "version": 1,
        "output": {"width": 360, "height": 640, "fps": 30},
        "clips": [{"id": "c1", "kind": "image",
                   "src": "data/test_media/pattern.jpg",
                   "duration": 2.0}],
    }
    if level == "clip":
        project["clips"][0]["treatment"] = treatment
    else:
        project["treatment"] = treatment
    return project


class TestGrainMode(unittest.TestCase):
    def test_default_is_frozen(self):
        self.assertEqual(DEFAULT_GRAIN_MODE, "frozen")
        # No grain_mode key -> the frozen filter (noise without
        # allf=t: one static pattern, verified byte-identical
        # frames on this build).
        filt = _treatment_filters({"grain": 10})
        self.assertIn("noise=alls=10", filt)
        self.assertNotIn("allf=t", filt)

    def test_grain_modes(self):
        temporal = _treatment_filters({"grain": 10,
                                       "grain_mode": "temporal"})
        self.assertIn("noise=alls=10:allf=t", temporal)
        frozen = _treatment_filters({"grain": 10,
                                     "grain_mode": "frozen"})
        self.assertIn("noise=alls=10", frozen)
        self.assertNotIn("allf=t", frozen)
        off = _treatment_filters({"grain": 10, "grain_mode": "off"})
        self.assertNotIn("noise=", off)
        # Draft renders force grain off entirely.
        draft = _treatment_filters({"grain": 10}, grain_off=True)
        self.assertNotIn("noise=", draft)

    def test_grain_mode_validation(self):
        for mode in ("off", "frozen", "temporal"):
            errors, _ = validate_project(
                _project_with_treatment({"grain": 10,
                                         "grain_mode": mode}))
            self.assertEqual(errors, [], mode)
        errors, _ = validate_project(
            _project_with_treatment({"grain": 10,
                                     "grain_mode": "shimmer"}))
        self.assertEqual(len(errors), 1)
        self.assertIn("grain_mode", errors[0]["message"])
        # Project-level treatment validates too.
        errors, _ = validate_project(
            _project_with_treatment({"grain": 8, "grain_mode": "off"},
                                    level="project"))
        self.assertEqual(errors, [])

    def test_treatment_cinematic_keys_valid(self):
        """The 1.60.0 B-roll treatment (blur/brightness/contrast/
        saturation) validates: the renderer supported these keys
        all along, but the validator rejected them, so every
        B-roll explainer brief failed validation."""
        errors, _ = validate_project(_project_with_treatment(
            {"blur": 6.0, "brightness": -0.22, "contrast": 1.12,
             "saturation": 0.85, "vignette": 1.0}))
        self.assertEqual(errors, [])
        filt = _treatment_filters(
            {"blur": 6.0, "brightness": -0.22, "contrast": 1.12,
             "saturation": 0.85, "vignette": 1.0})
        self.assertIn("gblur=sigma=6.0", filt)
        self.assertIn("eq=brightness=-0.22:contrast=1.12"
                      ":saturation=0.85", filt)
        self.assertIn("vignette", filt)

    def test_treatment_cinematic_keys_ranges(self):
        errors, _ = validate_project(
            _project_with_treatment({"brightness": -2.0}))
        self.assertEqual(len(errors), 1)
        self.assertIn("brightness", errors[0]["message"])
        errors, _ = validate_project(
            _project_with_treatment({"blur": 31.0}))
        self.assertEqual(len(errors), 1)
        errors, _ = validate_project(
            _project_with_treatment({"contrast": 3.5}))
        self.assertEqual(len(errors), 1)
        errors, _ = validate_project(
            _project_with_treatment({"saturation": -0.5}))
        self.assertEqual(len(errors), 1)
        errors, _ = validate_project(
            _project_with_treatment({"sparkle": 1.0}))
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown treatment key", errors[0]["message"])


class TestDraftScaling(unittest.TestCase):
    """Filter-level proof (Task 2.2): at scale_factor 0.5 every
    text quantity halves; at 1.0 nothing changes. The render-level
    proof (0.5 frames eye-checked, 1.0 byte-identical vs the
    pre-1.62.0 tree) lives in the 1.62.0 build log, not the suite."""

    def setUp(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.base = base
        with open(os.path.join(base, "examples", "basic.json")) as fh:
            self.project = json.load(fh)
        errors, self.info = validate_project(self.project)
        self.assertEqual(errors, [])

    def _graph(self, scale_factor):
        argv, _, _ = build_ffmpeg_args(
            self.project, self.info,
            os.path.join(tempfile.gettempdir(), "draft-scale.mp4"),
            scale_factor=scale_factor)
        return argv[argv.index("-filter_complex") + 1]

    def test_scale_1_unchanged(self):
        fc = self._graph(1.0)
        self.assertIn("fontsize='48'", fc)
        self.assertIn("y='80'", fc)
        self.assertIn("fontsize='36'", fc)

    def test_scale_half_halves_text(self):
        fc = self._graph(0.5)
        self.assertIn("fontsize='24.0'", fc)
        self.assertIn("y='40'", fc)
        self.assertIn("fontsize='18.0'", fc)
        # The 1.0 values must not survive anywhere in the graph.
        self.assertNotIn("fontsize='48'", fc)
        self.assertNotIn("fontsize='36'", fc)

    def test_overlay_draft_scaling(self):
        """Overlays scale like text (R7 fix): explicit x/y and
        keyframe x/y halve at 0.5 and are unchanged at 1.0. Before
        the fix the size scaled (it uses the render width) but x/y
        stayed full-res, so a centred subject landed half off the
        right edge of a draft frame."""
        import re
        project = {
            "id": "ov-draft", "version": 1,
            "output": {"width": 360, "height": 640, "fps": 30},
            "clips": [{"id": "c1", "kind": "image",
                       "src": "data/test_media/pattern.jpg",
                       "duration": 3.0}],
            "overlays": [
                {"id": "static", "kind": "image",
                 "src": "data/test_media/pattern.png",
                 "start": 0.0, "duration": 3.0, "size": 0.5,
                 "x": 90, "y": 160},
                {"id": "keyed", "kind": "image",
                 "src": "data/test_media/pattern.png",
                 "start": 0.0, "duration": 3.0, "size": 0.5,
                 "x": 90, "y": 160,
                 "keyframes": [{"time": 0.0, "x": 100, "y": 170,
                                 "scale": 0.5, "opacity": 1.0},
                                {"time": 3.0, "x": 90, "y": 160,
                                 "scale": 0.5, "opacity": 1.0}]},
            ],
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [])
        out = os.path.join(tempfile.gettempdir(), "ov-draft.mp4")

        def _overlay_xy(scale_factor):
            argv, _, _ = build_ffmpeg_args(project, info, out,
                                           scale_factor=scale_factor)
            fc = argv[argv.index("-filter_complex") + 1]
            # The overlay filters: [v][ov]overlay=x='..':y='..'
            return re.findall(r"overlay=x='([^']*)':y='([^']*)'", fc)

        full = _overlay_xy(1.0)
        self.assertEqual(len(full), 2)
        # Static overlay keeps its exact full-res coords.
        self.assertEqual(full[0], ("90", "160"))
        # Keyframed overlay embeds the full-res keyframe values.
        self.assertIn("100", full[1][0])
        self.assertIn("170", full[1][1])
        half = _overlay_xy(0.5)
        self.assertEqual(len(half), 2)
        self.assertEqual(half[0], ("45", "80"))
        self.assertIn("50", half[1][0])
        self.assertIn("85", half[1][1])
        self.assertNotIn("100", half[1][0])
        self.assertNotIn("170", half[1][1])


class TestCacheUnification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-v162-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalize_for_hash(self):
        a = cache_mod.normalize_for_hash({"b": 1, "a": [1.0, {"x": 2}]})
        b = cache_mod.normalize_for_hash({"a": [1.0, {"x": 2}], "b": 1})
        self.assertEqual(a, b)
        # The old private name is still an alias.
        self.assertIs(cache_mod._normalize_obj,
                      cache_mod.normalize_for_hash)

    def test_scene_cache_roundtrip(self):
        scene = {"id": "s1", "duration": 5.0}
        h = cache_mod.compute_scene_hash(scene, {"width": 360})
        self.assertEqual(len(h), 16)
        self.assertIsNone(
            cache_mod.lookup_scene_cache(h, project_dir=self.tmp))
        src = os.path.join(self.tmp, "chunk.mp4")
        with open(src, "wb") as fh:
            fh.write(b"\x00" * 2048)
        self.assertTrue(
            cache_mod.store_scene_cache(h, src, project_dir=self.tmp))
        found = cache_mod.lookup_scene_cache(h, project_dir=self.tmp)
        self.assertIsNotNone(found)
        self.assertTrue(str(found).endswith(f"scene_{h}.mp4"))

    def test_clear_cache_removes_segments_too(self):
        cache_dir = cache_mod.get_cache_dir(self.tmp)
        seg = cache_dir / "seg_deadbeef.mkv"
        seg.write_bytes(b"\x00" * 2048)
        scene = cache_dir / "scene_deadbeef.mp4"
        scene.write_bytes(b"\x00" * 2048)
        removed = cache_mod.clear_cache(project_dir=self.tmp)
        self.assertEqual(removed, 2)
        self.assertFalse(seg.exists())
        self.assertFalse(scene.exists())

    def test_cache_dir_shared(self):
        # Scene cache and segment cache share one .agentcut_cache
        # dir (the 1.62.0 unification).
        self.assertEqual(str(cache_mod.get_cache_dir(self.tmp)),
                         os.path.join(self.tmp, ".agentcut_cache"))


class TestReframeUnification(unittest.TestCase):
    def test_build_windows_crop_filter(self):
        windows = [{"t": 0.0, "x": 10.0, "y": 20.0, "w": 100.0,
                    "h": 200.0},
                   {"t": 5.0, "x": 30.0, "y": 40.0, "w": 100.0,
                    "h": 200.0}]
        filt = build_windows_crop_filter(windows)
        self.assertTrue(filt.startswith("crop=100:200:"))
        self.assertIn("x=", filt)
        self.assertIn("y=", filt)
        # Offsets are even-rounded (2*trunc(.../2)).
        self.assertIn("2*trunc(", filt)

    def test_parse_aspect_ratio(self):
        self.assertEqual(parse_aspect_ratio("9:16"), (9.0, 16.0))
        self.assertEqual(parse_aspect_ratio("16:9"), (16.0, 9.0))
        for bad in ("9x16", "9", "0:16", "9:-16", "", None):
            with self.assertRaises(ValueError, msg=bad):
                parse_aspect_ratio(bad)

    def test_track_interp_expr_is_the_reframe_one(self):
        # render.py keeps only an alias now; the implementation
        # lives in reframe.py.
        self.assertIs(_track_interp_expr, track_interp_expr)
        expr = track_interp_expr(
            [{"t": 0.0, "x": 10.0, "y": 20.0, "w": 100.0, "h": 200.0},
             {"t": 5.0, "x": 30.0, "y": 40.0, "w": 100.0, "h": 200.0}],
            "t", "x")
        self.assertIn("t", expr)

    def test_clip_reframe_validation_uses_parser(self):
        tmp = tempfile.mkdtemp(prefix="agentcut-test-v162-")
        try:
            windows_path = os.path.join(tmp, "reframe.json")
            with open(windows_path, "w") as fh:
                json.dump({"frame_size": {"width": 640, "height": 360},
                           "windows": [{"t": 0.0, "x": 100.0, "y": 0.0,
                                        "w": 200.0, "h": 360.0}]}, fh)

            def _proj(aspect):
                return {
                    "id": "r", "version": 1,
                    "output": {"width": 360, "height": 640, "fps": 30},
                    "clips": [{"id": "c1", "kind": "video",
                               "src": "data/test_media/clip1.mp4",
                               "reframe": {"windows_file": windows_path,
                                           "target_aspect": aspect}}],
                }

            errors, _ = validate_project(_proj("9x16"))
            self.assertTrue(any("aspect" in e["message"].lower()
                                for e in errors), errors)
            errors, _ = validate_project(_proj("9:16"))
            self.assertEqual(errors, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
