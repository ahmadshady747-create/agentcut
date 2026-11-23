"""Comprehensive verification suite for AgentCut v2.0.0 upgrades & fixes."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.batch import run_batch_production
from agentcut.bundle import auto_split_beats, bundle_to_editorial_brief, validate_media_bundle
from agentcut.cache import (
    clear_cache,
    compute_scene_hash,
    evict_cache,
    get_cache_stats,
    lookup_scene_cache,
    store_scene_cache,
)
from agentcut.profiles import (
    apply_profile_to_brief,
    detect_hardware_profile,
    get_render_profile,
    list_render_profiles,
)
from agentcut.qc import audit_video_quality
from agentcut.render import produce_explainer
from agentcut.server import TOOLS, call_tool
from agentcut.smart_cut import build_jump_cut_project, detect_speech_segments
from agentcut.srt import batch_captions_by_window, measure_text_width, wrap_lines
from agentcut.text_metrics import analyze_text

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"
FONTS = REPO / "fonts"


class TestCacheV2(unittest.TestCase):
    def test_recursive_nested_asset_hashing(self):
        with tempfile.TemporaryDirectory() as td:
            asset = Path(td) / "beat1.png"
            asset.write_bytes(b"PNG-DATA-V1" * 100)
            scene = {
                "id": "s1",
                "clips": [{"kind": "image", "src": str(asset)}],
            }
            h1 = compute_scene_hash(scene, {"width": 1080, "height": 1920})
            time.sleep(0.02)
            asset.write_bytes(b"PNG-DATA-V2-MODIFIED" * 200)
            h2 = compute_scene_hash(scene, {"width": 1080, "height": 1920})
            self.assertNotEqual(h1, h2)

    def test_atomic_store_lru_eviction_and_stats(self):
        with tempfile.TemporaryDirectory() as td:
            src1 = Path(td) / "c1.mp4"
            src2 = Path(td) / "c2.mp4"
            src1.write_bytes(b"A" * 4096)
            src2.write_bytes(b"B" * 4096)

            store_scene_cache("hash_old", src1, project_dir=td, max_bytes=0)
            time.sleep(0.02)
            store_scene_cache("hash_new", src2, project_dir=td, max_bytes=0)

            stats = get_cache_stats(td)
            self.assertEqual(stats["scene_chunks"], 2)
            self.assertEqual(stats["total_bytes"], 8192)

            ev = evict_cache(project_dir=td, max_bytes=5000)
            self.assertEqual(ev["removed"], 1)
            self.assertIsNone(lookup_scene_cache("hash_old", project_dir=td))
            self.assertIsNotNone(lookup_scene_cache("hash_new", project_dir=td))
            self.assertEqual(clear_cache(td), 1)


class TestSrtAndTextMetricsV2(unittest.TestCase):
    def test_wrap_lines_preserves_explicit_newlines(self):
        lines = wrap_lines("السطر الأول\nالسطر الثاني", 48, 900)
        self.assertEqual(lines, ["السطر الأول", "السطر الثاني"])

    def test_batch_captions_negative_and_huge_timestamps(self):
        cues = [
            {"start": -2.5, "end": 1.0, "text": "neg"},
            {"start": 86400.0, "end": 86402.0, "text": "far"},
        ]
        windows = batch_captions_by_window(cues, window_seconds=30)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["index"], 0)
        self.assertEqual(windows[1]["index"], 2880)

    def test_measure_text_width_thread_safe_cached(self):
        font = str(FONTS / "NotoSansArabic-Bold.ttf")
        results = []

        def worker():
            for _ in range(5):
                w = measure_text_width("أجينت كات محرك مونتاج", font, 48)
                results.append(w)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 20)
        self.assertTrue(all(w > 50.0 for w in results))
        self.assertEqual(len(set(round(w, 3) for w in results)), 1)

    def test_arabic_diacritics_and_pure_digits(self):
        ar = analyze_text("مَرْحَباً بِالعَالَمِ")
        self.assertEqual(ar["words"], 2)
        self.assertEqual(ar["arabic_words"], 2)
        self.assertTrue(ar["is_arabic_predominant"])

        nums = analyze_text("2026 - 100%")
        self.assertFalse(nums["is_arabic_predominant"])
        self.assertEqual(nums["arabic_words"], 0)


class TestProfilesV2(unittest.TestCase):
    def test_profiles_and_hardware_detection(self):
        profs = list_render_profiles()
        names = {p["name"] for p in profs}
        self.assertTrue({"proxy", "draft", "low_spec", "master", "cinema"}.issubset(names))
        self.assertEqual(get_render_profile("standard")["name"], "master")

        hw = detect_hardware_profile()
        self.assertGreaterEqual(hw["cpu_cores"], 1)
        self.assertGreaterEqual(hw["safe_max_workers"], 1)
        self.assertIn(hw["recommended_profile"], names)

        brief = apply_profile_to_brief({"title": "Demo", "scenes": []}, "low_spec")
        self.assertEqual(brief["render_profile"], "low_spec")
        self.assertEqual(brief["grain_mode"], "off")
        self.assertEqual(brief["max_workers"], 2)


class TestQcAuditV2(unittest.TestCase):
    def test_qc_missing_file_returns_grade_f(self):
        rep = audit_video_quality("/tmp/does_not_exist_agentcut_v200.mp4")
        self.assertFalse(rep["passed"])
        self.assertEqual(rep["grade"], "F")
        self.assertEqual(rep["issues"][0]["code"], "FILE_NOT_FOUND")

    def test_qc_real_test_clip(self):
        clip = str(TEST_MEDIA / "clip1.mp4")
        rep = audit_video_quality(clip, require_audio=False)
        self.assertGreaterEqual(rep["score"], 60)
        self.assertIn("duration", rep["metrics"])
        self.assertGreater(rep["metrics"]["duration"], 0)

        rep_drift = audit_video_quality(clip, expected_duration=99.0, require_audio=False)
        codes = [i["code"] for i in rep_drift["issues"]]
        self.assertIn("DURATION_DRIFT", codes)


class TestAgentMediaBundleV2(unittest.TestCase):
    def test_pause_aware_auto_split_beats(self):
        timings = [
            {"word": "w1", "start": 0.0, "end": 1.2},
            {"word": "w2", "start": 1.3, "end": 2.6},
            # Big natural pause between 2.6 and 3.3 (mid = 2.95)
            {"word": "w3", "start": 3.3, "end": 5.0},
            {"word": "w4", "start": 5.1, "end": 7.0},
        ]
        beats = auto_split_beats(timings, 7.0, min_beat=2.5, max_beat=4.0, images=["a.png", "b.png"])
        self.assertEqual(len(beats), 2)
        self.assertAlmostEqual(beats[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(beats[0]["end"], 2.95, places=2)
        self.assertAlmostEqual(beats[1]["end"], 7.0, places=2)
        self.assertEqual(beats[0]["broll"], "a.png")
        self.assertEqual(beats[1]["broll"], "b.png")

    def test_validate_and_convert_bundle(self):
        bad_ok, bad_errs = validate_media_bundle({"scenes": []})
        self.assertFalse(bad_ok)
        self.assertTrue(len(bad_errs) >= 1)

        bundle = {
            "title": "مقال مرئي تجريبي",
            "aspect": "9:16",
            "scenes": [{
                "id": "s1",
                "text": "أهلا بكم في هذا المقال المرئي",
                "duration": 6.5,
                "images": ["img1.png", "img2.png"],
                "highlight_card": {"title": "الفكرة المحورية"},
            }],
        }
        ok, errs = validate_media_bundle(bundle, check_files=False)
        self.assertTrue(ok, errs)
        brief = bundle_to_editorial_brief(bundle, profile="low_spec", parallel=True)
        self.assertEqual(brief["style"], "editorial")
        self.assertEqual(brief["render_profile"], "low_spec")
        self.assertTrue(brief["parallel"])
        self.assertEqual(len(brief["scenes"][0]["beats"]), 2)


class TestSmartCutV2(unittest.TestCase):
    def test_detect_speech_segments_synthetic_media(self):
        with tempfile.TemporaryDirectory() as td:
            sample = Path(td) / "talk_with_silence.mp4"
            # 1s tone + 1.2s silence + 1s tone = 3.2s total
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=navy:s=320x240:r=15:d=3.2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.2",
                "-f", "lavfi", "-i", "sine=frequency=520:duration=1.0",
                "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(sample),
            ]
            subprocess.run(cmd, check=True, timeout=30)
            proj, analysis = build_jump_cut_project(str(sample), noise_db=-35.0, min_silence=0.5)
            self.assertGreaterEqual(analysis["segment_count"], 2)
            self.assertGreater(analysis["saved_seconds"], 0.4)
            self.assertEqual(len(proj["clips"]), analysis["segment_count"])


class TestBatchOrchestratorV2(unittest.TestCase):
    def test_batch_resume_and_error_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            existing_mp4 = Path(td) / "already_done.mp4"
            existing_mp4.write_bytes(b"0" * 4096)
            report_file = Path(td) / "batch_report.json"
            manifest = {
                "jobs": [
                    {"id": "job_skip", "kind": "project", "input": {}, "output": str(existing_mp4)},
                    {"id": "job_bad", "kind": "unknown_kind", "input": {}, "output": str(Path(td) / "bad.mp4")},
                ]
            }
            summary = run_batch_production(manifest, max_concurrency=1, resume=True, report_path=str(report_file))
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertFalse(summary["all_ok"])
            self.assertTrue(report_file.is_file())


class TestParallelEditorialRenderV2(unittest.TestCase):
    def test_parallel_multibeat_editorial_render_and_cache(self):
        with tempfile.TemporaryDirectory() as td:
            wav1 = Path(td) / "n1.wav"
            wav2 = Path(td) / "n2.wav"
            for wav in (wav1, wav2):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "sine=frequency=330:duration=2.4", str(wav)],
                    check=True, timeout=20,
                )
            srt1 = Path(td) / "s1.srt"
            srt2 = Path(td) / "s2.srt"
            srt1.write_text("1\n00:00:00,000 --> 00:00:02,400\nالمشهد الأول بإيقاعين متوازيين\n", encoding="utf-8")
            srt2.write_text("1\n00:00:00,000 --> 00:00:02,400\nالمشهد الثاني مع تخزين مؤقت ذري\n", encoding="utf-8")
            img1 = str(TEST_MEDIA / "poster.png")
            img2 = str(TEST_MEDIA / "board.png")
            brief = {
                "title": "Parallel Editorial Proof",
                "style": "editorial",
                "aspect": "16:9",
                "parallel": True,
                "render_profile": "proxy",
                "scenes": [
                    {
                        "id": "sc1",
                        "text": "المشهد الأول بإيقاعين متوازيين",
                        "narration": str(wav1),
                        "captions": str(srt1),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": img1},
                            {"start": 1.2, "end": 2.4, "broll": img2},
                        ],
                    },
                    {
                        "id": "sc2",
                        "text": "المشهد الثاني مع تخزين مؤقت ذري",
                        "narration": str(wav2),
                        "captions": str(srt2),
                        "beats": [
                            {"start": 0.0, "end": 1.2, "broll": img2},
                            {"start": 1.2, "end": 2.4, "broll": img1},
                        ],
                    },
                ],
            }
            out1 = str(Path(td) / "out1.mp4")
            ok1, info1 = produce_explainer(brief, out1)
            self.assertTrue(ok1, info1)
            self.assertEqual((info1.get("render") or info1).get("parallel_chunks"), 2)
            self.assertTrue(os.path.isfile(out1))

            # Second render reuses atomic scene cache
            out2 = str(Path(td) / "out2.mp4")
            ok2, info2 = produce_explainer(brief, out2)
            self.assertTrue(ok2, info2)
            self.assertEqual((info2.get("render") or info2).get("cache_hits"), 2)


class TestMcpAndCliV2(unittest.TestCase):
    def test_mcp_43_tools_and_dispatch(self):
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)

        r1 = call_tool("list_render_profiles", {})
        self.assertFalse(r1.get("isError"))
        payload1 = json.loads(r1["content"][0]["text"])
        self.assertGreaterEqual(len(payload1["profiles"]), 5)

        r2 = call_tool("detect_hardware_profile", {})
        self.assertFalse(r2.get("isError"))

        r3 = call_tool("get_cache_stats", {})
        self.assertFalse(r3.get("isError"))

        r4 = call_tool("validate_media_bundle", {"bundle": {"scenes": [{"text": "hello"}]}, "check_files": False})
        self.assertFalse(r4.get("isError"))
        self.assertTrue(json.loads(r4["content"][0]["text"])["valid"])

    def test_cli_version_help_and_profiles(self):
        self.assertEqual(cli_main(["--version"]), 0)
        self.assertEqual(cli_main(["--help"]), 0)
        self.assertEqual(cli_main(["profiles", "--detect"]), 0)
        self.assertEqual(cli_main(["cache-stats"]), 0)


if __name__ == "__main__":
    unittest.main()
