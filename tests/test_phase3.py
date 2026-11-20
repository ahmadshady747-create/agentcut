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

    def test_parallel_rebase_rounding_sliver(self):
        # CI run 44769612850: scene 1's true end (2.9776s)
        # rounds UP to 2.978 in the scenes list, so scene 2's
        # title (starting at unrounded 2.9776) fell inside
        # scene 1's rounded range and was rebased into chunk 0,
        # where the +0.1s minimum-duration floor pushed its end
        # to 3.078 — past the chunk's 2.98s timeline
        # (CLIP_OUT_OF_RANGE). The rebase must compare rounded
        # starts and clamp ends to the chunk duration. The
        # render itself is mocked out (validate-only): the
        # regression is in the chunk projects, not the encoder.
        from unittest import mock
        from agentcut.render import _render_explainer_parallel
        from agentcut.validate import validate_project

        base_dir = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        wav = os.path.join(base_dir, "data", "test_media",
                           "one-button-voice.wav")
        project = {
            "id": "rounding_sliver",
            "version": 1,
            "output": {"width": 360, "height": 640, "fps": 30},
            "clips": [
                {"id": "c1", "kind": "image", "src": self.poster,
                 "duration": 2.9776},
                {"id": "c2", "kind": "image", "src": self.poster,
                 "duration": 1.5},
            ],
            "texts": [
                {"text": "عنوان ثابت", "start": 0.0, "end": 4.478,
                 "size": 60, "x": "center", "y": 80,
                 "color": "white", "box": False},
                {"text": "المشهد 1", "start": 0.0, "end": 2.9776,
                 "size": 52, "x": "center", "y": 190,
                 "color": "yellow", "box": False},
                {"text": "المشهد 2", "start": 2.9776, "end": 4.4776,
                 "size": 52, "x": "center", "y": 190,
                 "color": "yellow", "box": False},
            ],
            "captions": [],
            "audio": [],
            "diagrams": [],
            "motion": [],
            "overlays": [],
        }
        scenes = [
            {"id": "s1", "title": "المشهد 1", "start": 0.0,
             "end": 2.978, "duration": 2.978, "narration": wav,
             "clip_start": 0, "clip_end": 1},
            {"id": "s2", "title": "المشهد 2", "start": 2.978,
             "end": 4.478, "duration": 1.5, "narration": wav,
             "clip_start": 1, "clip_end": 2},
        ]
        seen = []

        def fake_render(proj, out, **kw):
            with open(out, "wb"):
                pass
            if proj["id"].endswith("_parallel_assembly"):
                return True, {}
            errors, _info = validate_project(proj)
            seen.append((proj["id"], proj, errors))
            if errors:
                return False, {"code": "INVALID_PROJECT",
                               "errors": errors}
            return True, {}

        out_mp4 = os.path.join(self.tmp, "sliver.mp4")
        with mock.patch("agentcut.render.render_project",
                        side_effect=fake_render):
            ok, rinfo = _render_explainer_parallel(
                project, scenes, out_mp4, use_cache=False)
        self.assertTrue(ok, rinfo)
        self.assertEqual(rinfo.get("parallel_chunks"), 2)
        self.assertEqual(len(seen), 2, seen)
        by_id = {pid: (proj, errs) for pid, proj, errs in seen}
        for pid, (_proj, errs) in by_id.items():
            self.assertEqual(errs, [], f"{pid}: {errs}")
        chunk0 = by_id["rounding_sliver_chunk_0"][0]
        chunk1 = by_id["rounding_sliver_chunk_1"][0]
        texts0 = {t["text"] for t in chunk0["texts"]}
        texts1 = {t["text"] for t in chunk1["texts"]}
        # Scene 2's title belongs to chunk 1 only — the old
        # code put it in chunk 0 with an out-of-range end.
        self.assertNotIn("المشهد 2", texts0)
        self.assertIn("المشهد 2", texts1)
        self.assertIn("المشهد 1", texts0)
        self.assertIn("عنوان ثابت", texts0)
        self.assertIn("عنوان ثابت", texts1)


if __name__ == "__main__":
    unittest.main()
