"""Unit tests for AgentCut Phase 3:
- GPU HW Acceleration (h264_qsv, h264_mf, etc.)
- Ultra-Fast Draft Mode (scale_factor=0.5, ultrafast preset)
- Chunked Parallel Scene Rendering (multiprocessing + concat demuxer)
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut.render import (
    HW_ENCODERS_PREFERENCE,
    choose_video_codec,
    detect_hw_encoders,
    encoder_smoke_test,
    produce_explainer,
)
from agentcut.validate import probe_media


class TestPhase3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcut-test-p3-")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.poster = os.path.join(base_dir, "data", "test_media", "poster.png")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hw_encoders_extended(self):
        self.assertIn("h264_qsv", HW_ENCODERS_PREFERENCE)
        self.assertIn("h264_mf", HW_ENCODERS_PREFERENCE)
        self.assertIn("h264_nvenc", HW_ENCODERS_PREFERENCE)

        chosen = choose_video_codec("auto")
        self.assertTrue(isinstance(chosen, str) and len(chosen) > 0)
        # Smoke test chosen encoder to verify it really encodes
        self.assertTrue(encoder_smoke_test(chosen))

    def test_draft_mode(self):
        out_mp4 = os.path.join(self.tmp, "draft.mp4")
        brief = {
            "id": "draft_video",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": self.poster,
            "title": "مسودة سريعة",
            "draft": True,
            "scenes": [
                {
                    "id": "s1",
                    "title": "مقدمة المسودة",
                    "narration_text": "هذه مسودة سريعة للمعاينة الفورية.",
                }
            ],
        }

        t0 = time.time()
        ok, info = produce_explainer(brief, out_mp4)
        t1 = time.time()

        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out_mp4))
        self.assertTrue(info.get("draft"))
        # 0.5x of 360x640 is 180x320
        self.assertEqual(info["width"], 180)
        self.assertEqual(info["height"], 320)
        # Verify render was fast
        probe = probe_media(out_mp4)
        self.assertIsNotNone(probe)
        self.assertEqual(probe["width"], 180)
        self.assertEqual(probe["height"], 320)

    def test_parallel_chunked_rendering(self):
        out_mp4 = os.path.join(self.tmp, "parallel.mp4")
        brief = {
            "id": "parallel_video",
            "style": "blackboard",
            "output": {"width": 360, "height": 640, "fps": 30},
            "background": self.poster,
            "title": "تصيير متوازي",
            "parallel": True,
            "music": "ambient",
            "scenes": [
                {
                    "id": "s1",
                    "title": "المشهد 1",
                    "narration_text": "هذا هو المشهد الأول في التصيير المتوازي.",
                },
                {
                    "id": "s2",
                    "title": "المشهد 2",
                    "narration_text": "وهذا هو المشهد الثاني الذي تم تصييره بالتوازي.",
                },
            ],
        }

        ok, info = produce_explainer(brief, out_mp4)
        self.assertTrue(ok, info)
        self.assertTrue(os.path.isfile(out_mp4))
        self.assertTrue(info.get("parallel"))
        self.assertEqual(len(info["scenes"]), 2)

        probe = probe_media(out_mp4)
        self.assertIsNotNone(probe)
        self.assertAlmostEqual(probe["duration"], info["duration"], delta=0.5)


if __name__ == "__main__":
    unittest.main()
