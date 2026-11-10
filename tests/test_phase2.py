"""Unit tests for AgentCut Phase 2:
- Kinetic / Karaoke Typography
- New Diagram Templates (timeline & process_flow)
- Cinematic Scene Transitions
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.diagrams import build_template, TEMPLATES
from agentcut.render import produce_explainer
from agentcut.validate import validate_project


class TestPhase2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-p2-")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.poster = os.path.join(base_dir, "data", "test_media", "poster.png")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_timeline_horizontal(self):
        cw, ch, strokes = build_template("timeline", {"steps": 4, "direction": "horizontal"})
        self.assertEqual((cw, ch), (900, 560))
        self.assertGreater(len(strokes), 10)
        kinds = {s["kind"] for s in strokes}
        self.assertIn("arrow", kinds)
        self.assertIn("circle", kinds)
        self.assertIn("rect", kinds)

    def test_timeline_vertical(self):
        cw, ch, strokes = build_template("timeline", {"steps": 3, "direction": "vertical"})
        self.assertEqual((cw, ch), (600, 800))
        self.assertGreater(len(strokes), 8)

    def test_process_flow(self):
        cw, ch, strokes = build_template("process_flow", {"steps": 3})
        self.assertEqual((cw, ch), (900, 500))
        self.assertGreater(len(strokes), 6)
        kinds = {s["kind"] for s in strokes}
        self.assertIn("rect", kinds)
        self.assertIn("arrow", kinds)
        self.assertIn("circle", kinds)

    def test_templates_validation(self):
        proj = {
            "id": "p2_templates",
            "version": 1,
            "output": {"width": 720, "height": 1280, "fps": 30},
            "clips": [{"id": "c1", "kind": "image", "src": self.poster, "duration": 6.0}],
            "diagrams": [
                {"id": "d_time", "start": 0.0, "end": 3.0, "template": "timeline",
                 "params": {"steps": 3, "direction": "horizontal"}},
                {"id": "d_proc", "start": 3.0, "end": 6.0, "template": "process_flow",
                 "params": {"steps": 3}},
            ]
        }
        errors, info = validate_project(proj)
        self.assertEqual(errors, [])
        self.assertEqual(len(info["diagrams"]), 2)

    def test_explainer_with_transition_and_karaoke(self):
        out_mp4 = os.path.join(self.tmp, "p2_explainer.mp4")
        brief = {
            "id": "p2_video",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": self.poster,
            "title": "المرحلة الثانية",
            "transition": "whip_pan",
            "captions_style": "karaoke",
            "scenes": [
                {
                    "id": "s1",
                    "title": "الخط الزمني",
                    "narration_text": "هذه هي المحطة الأولى.",
                    "diagram": {"template": "timeline", "params": {"steps": 3}, "size": 0.75}
                },
                {
                    "id": "s2",
                    "title": "مسار العمليات",
                    "narration_text": "وهنا يكتمل مسار العمليات بنجاح.",
                    "diagram": {"template": "process_flow", "params": {"steps": 3}, "size": 0.75}
                }
            ]
        }
        ok, info = produce_explainer(brief, out_mp4)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out_mp4))

        # Check that scene 2 has whip_pan transition
        clips = info["project"]["clips"]
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[1]["transition"], "whip_pan")

        # Check that captions have word_timings
        captions = info["project"]["captions"]
        self.assertGreater(len(captions), 0)
        has_wt = any("word_timings" in c for c in captions)
        self.assertTrue(has_wt, "At least one caption should have word_timings for karaoke")


if __name__ == "__main__":
    unittest.main()
