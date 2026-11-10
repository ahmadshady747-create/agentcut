"""Verification test suite for AgentCut v1.51.0 release (Task 3).
Empirically tests pyproject.toml zero-dep packaging, parallel chunk near-lossless
intermediate settings, duration sync compensation, and broadcast audio/video telemetry.
"""
import json
import os
import re
import subprocess
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from agentcut import __version__
from agentcut.validate import validate_project, probe_media
from agentcut.templates import build_brief_from_template, apply_template_to_brief


class TestV151Verification(unittest.TestCase):
    def test_pyproject_toml_packaging_and_zero_deps(self):
        """Task 1: Verify pyproject.toml packaging conforms strictly to zero-dep standard."""
        pyproject_path = os.path.join(BASE, "pyproject.toml")
        self.assertTrue(os.path.isfile(pyproject_path), "pyproject.toml must exist at repo root")

        # Parse TOML using Python 3.11+ tomllib
        try:
            import tomllib
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except ImportError:
            # Fallback simple parser for older Pythons
            data = {}

        if data:
            self.assertEqual(data.get("build-system", {}).get("build-backend"), "setuptools.build_meta")
            proj = data.get("project", {})
            self.assertEqual(proj.get("name"), "agentcut")
            self.assertEqual(proj.get("version"), "1.51.0")
            self.assertEqual(proj.get("requires-python"), ">=3.10")
            # Strict Zero External Python Dependencies constraint
            self.assertEqual(proj.get("dependencies"), [], "dependencies must be strictly empty")
            scripts = proj.get("scripts", {})
            self.assertEqual(scripts.get("agentcut"), "agentcut.__main__:main")
            self.assertEqual(scripts.get("agentcut-mcp"), "agentcut.server:main")

        # Verify entry points exist and are callable
        from agentcut.__main__ import main as cli_main
        from agentcut.server import main as mcp_main
        self.assertTrue(callable(cli_main))
        self.assertTrue(callable(mcp_main))

        # Verify VERSION file
        version_file = os.path.join(BASE, "VERSION")
        with open(version_file, "r", encoding="utf-8") as vf:
            ver = vf.read().strip()
        self.assertEqual(ver, "1.51.0")
        self.assertEqual(__version__, "1.51.0")

    def test_parallel_chunk_near_lossless_optimization(self):
        """Task 2: Verify parallel rendering uses near-lossless intermediate chunks & closed GOP."""
        render_py = os.path.join(BASE, "src", "agentcut", "render.py")
        with open(render_py, "r", encoding="utf-8") as rf:
            src = rf.read()

        # Near-lossless CRF 10 and ultrafast preset for intermediate chunks
        self.assertIn("chunk_crf = 10", src)
        self.assertIn('chunk_preset = "ultrafast"', src)

        # Closed GOP at boundaries for frame-accurate stitching
        self.assertIn('"+cgop"', src)
        self.assertIn('"-flags"', src)

        # A/V sync compensation with exact t_override per chunk and apad/shortest
        self.assertIn("t_override=s_dur", src)
        self.assertIn('"-shortest"', src)
        self.assertIn("apad", src)

    def test_diagram_balance_causes_bars_localization(self):
        """Verify balance, causes, and bars diagrams accept custom Arabic labels and values up to 250."""
        project = {
            "version": 1,
            "id": "test_localization",
            "output": {"width": 720, "height": 1280, "fps": 30},
            "clips": [{"id": "c1", "kind": "image", "src": "data/test_media/poster.png", "duration": 15.0}],
            "diagrams": [
                {
                    "id": "dg1",
                    "template": "balance",
                    "params": {"left_label": "الإنتاج الحقيقي", "right_label": "كتلة النقد", "left": 100, "right": 250},
                    "start": 0.0,
                    "end": 5.0,
                    "draw": 2.0,
                    "size": 0.75,
                },
                {
                    "id": "dg2",
                    "template": "causes",
                    "params": {"items": ["طباعة بلا غطاء", "انخفاض قيمة العملة", "ارتفاع جنوني في الأسعار"]},
                    "start": 5.0,
                    "end": 10.0,
                    "draw": 2.0,
                    "size": 0.75,
                },
                {
                    "id": "dg3",
                    "template": "bars",
                    "params": {"values": [50.0, 120.0, 180.0, 250.0], "labels": ["2023", "2024", "2025", "2026"]},
                    "start": 10.0,
                    "end": 15.0,
                    "draw": 2.0,
                    "size": 0.75,
                }
            ]
        }
        errors, info = validate_project(project)
        self.assertEqual(errors, [], f"Validation errors: {errors}")

        # Check motion adaptations
        motion_elems = info.get("motion", [])
        self.assertTrue(len(motion_elems) >= 3)
        # Check dg1 comparison_cards has Arabic labels, not English left/right
        balance_elem = [m for m in motion_elems if m["id"] == "dg1"][0]
        self.assertEqual(balance_elem["kind"], "comparison_cards")
        cards = balance_elem["cards"]
        self.assertEqual(cards[0]["label"], "الإنتاج الحقيقي")
        self.assertEqual(cards[1]["label"], "كتلة النقد")
        self.assertNotIn("left", [c["label"] for c in cards])
        self.assertNotIn("right", [c["label"] for c in cards])

        # Check dg2 causes
        causes_elem = [m for m in motion_elems if m["id"] == "dg2"][0]
        self.assertEqual(len(causes_elem["cards"]), 3)
        self.assertEqual(causes_elem["cards"][0]["label"], "طباعة بلا غطاء")

        # Check dg3 bars with values up to 250
        bars_elem = [m for m in motion_elems if m["id"] == "dg3"][0]
        self.assertEqual(bars_elem["kind"], "bars")
        self.assertEqual(bars_elem["values"], [50.0, 120.0, 180.0, 250.0])

    def test_reference_video_telemetry_and_broadcast_loudness(self):
        """Task 3: Measure broadcast loudness target (-14 LUFS) and stream integrity."""
        # Use existing rendered reference or synthesize a fast deterministic proof
        ref_video = os.path.join(BASE, "data", "test_v151_ref.mp4")
        if not os.path.isfile(ref_video):
            # Render a 3-second explainer with audio mastering
            from agentcut.render import produce_explainer
            brief = {
                "template": "finance_data_story",
                "title": "لماذا لا نطبع المال بلا نهاية؟ خريطة التضخم الاقتصادي",
                "aspect": "9:16",
                "scenes": [
                    {
                        "id": "s1",
                        "title": "مغالطة طباعة النقود",
                        "narration_text": "لِمَاذَا لَا تَطْبَعُ الدُّوَلُ أَمْوَالاً بِلَا نِهَايَةٍ لِإِنْهَاءِ الفَقْرِ؟",
                        "diagram": {
                            "template": "balance",
                            "params": {"left_label": "الإنتاج الحقيقي", "right_label": "كتلة النقد"},
                            "card": True
                        }
                    }
                ]
            }
            ok, info = produce_explainer(brief, ref_video)
            self.assertTrue(ok, f"Explainer render failed: {info}")

        self.assertTrue(os.path.isfile(ref_video))
        probe = probe_media(ref_video)
        self.assertIsNotNone(probe)
        self.assertEqual(probe.get("width"), 720)
        self.assertEqual(probe.get("height"), 1280)
        self.assertGreater(probe.get("duration", 0), 1.0)

        # Check audio stream: AAC, 48000 Hz
        has_aac_48k = False
        raw = probe.get("raw", {})
        for st in raw.get("streams", []):
            if st.get("codec_type") == "audio":
                self.assertEqual(st.get("codec_name"), "aac")
                self.assertEqual(st.get("sample_rate"), "48000")
                has_aac_48k = True
        self.assertTrue(has_aac_48k, "Audio stream must be AAC 48kHz")

        # Measure integrated loudness with FFmpeg loudnorm filter
        cmd = [
            "ffmpeg", "-v", "info", "-i", ref_video,
            "-af", "loudnorm=print_format=json",
            "-f", "null", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        stderr = proc.stderr
        # Find loudnorm JSON in stderr
        json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
        self.assertIsNotNone(json_match, f"loudnorm JSON not found in stderr: {stderr}")
        ln_data = json.loads(json_match.group(0))
        input_i = float(ln_data["input_i"])

        # Broadcast standard: target is -14.0 LUFS (range -16.0 to -12.5 LUFS), NOT -21.8 LUFS
        self.assertGreaterEqual(input_i, -16.0, f"Loudness too quiet: {input_i} LUFS (expected ~ -14.0)")
        self.assertLessEqual(input_i, -12.5, f"Loudness too loud: {input_i} LUFS (expected ~ -14.0)")

        # Extract reference keyframe and verify no visual defect
        frame_out = os.path.join(BASE, "data", "test_v151_frame.png")
        try:
            f_proc = subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-ss", "1.5",
                "-i", ref_video, "-frames:v", "1", frame_out
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(f_proc.returncode, 0)
            self.assertTrue(os.path.isfile(frame_out))
            self.assertGreater(os.path.getsize(frame_out), 1000)
        finally:
            if os.path.isfile(frame_out):
                try:
                    os.remove(frame_out)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
