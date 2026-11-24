"""Verification test suite for AgentCut v3.4.4 (Cycle 4: Surgical Scene Patching, Pipe Mode & Visual QC)."""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import agentcut  # noqa: E402
from agentcut import browser_render, flat_reels, scene_patch, server, visual_qc  # noqa: E402


def _make_solid_png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Pure-Python valid PNG generator (>900 bytes with uncompressed level=0) for fast pipe/QC tests."""
    raw_rows = bytearray()
    row_pixels = bytes(rgb) * width
    for _ in range(height):
        raw_rows.append(0)  # filter type 0
        raw_rows.extend(row_pixels)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


class TestSurgicalScenePatchAndHookABV344(unittest.TestCase):
    def _sample_spec(self) -> dict:
        return {
            "id": "winter-4sc",
            "brand_kit": "tab_ezzay",
            "scenes": [
                {"id": "s1", "type": "intro", "narration": "s1.wav", "text": "حرارة جسمك 37.", "title": "الحرارة"},
                {"id": "s2", "type": "question", "narration": "s2.wav", "text": "طب ازاي؟"},
                {"id": "s3", "type": "statistic", "narration": "s3.wav", "text": "خمس مرات.", "stat": {"value": "×5"}},
                {"id": "s4", "type": "conclusion", "narration": "s4.wav", "text": "البرد إنذار ذكي."},
            ],
        }

    def test_patch_scene_preserves_untouched_scene_fingerprints(self):
        spec = self._sample_spec()
        ok, res = scene_patch.patch_scene(spec, "s3", {"stat": {"value": "×6"}})
        self.assertTrue(ok, res)
        self.assertEqual(res["invalidated_scenes"], ["s3"])
        self.assertEqual(set(res["unchanged_scenes"]), {"s1", "s2", "s4"})
        self.assertEqual(res["saved_render_percent"], 75.0)

    def test_generate_hook_ab_variants_isolates_scene_1(self):
        spec = self._sample_spec()
        base_manifest = scene_patch.compute_spec_scene_manifest(spec)
        variants = scene_patch.generate_hook_ab_variants(spec)
        self.assertEqual(len(variants), 3)
        for v in variants:
            self.assertEqual(len(v["hook_only_spec"]["scenes"]), 1)
            v_manifest = scene_patch.compute_spec_scene_manifest(v["full_spec"])
            # Scenes s2, s3, s4 keep identical fingerprints across all A/B hook variants
            for sid in ("s2", "s3", "s4"):
                self.assertEqual(v_manifest[sid]["fingerprint"], base_manifest[sid]["fingerprint"])


class TestPipeModeAndVisualQCV344(unittest.TestCase):
    def test_encode_png_stream_pipe_produces_valid_mp4_without_disk_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_mp4 = os.path.join(tmp, "piped.mp4")
            colors = [(247, 240, 220), (228, 87, 46), (46, 134, 171), (255, 210, 63), (34, 30, 23), (240, 120, 80)]
            frames_iter = (_make_solid_png_bytes(64, 64, c) for c in colors)
            ok, info = browser_render.encode_png_stream_pipe(
                frames_iter, None, out_mp4, fps=6
            )
            self.assertTrue(ok, info)
            self.assertTrue(info.get("pipe_mode"))
            self.assertEqual(info["frames"], 6)
            self.assertTrue(os.path.isfile(out_mp4))
            self.assertGreater(os.path.getsize(out_mp4), 200)

    def test_visual_qc_html_frames_and_contact_sheet(self):
        spec = {
            "id": "qc-test",
            "brand_kit": "tab_ezzay",
            "scenes": [
                {"id": "s1", "type": "intro", "narration": "a.wav", "text": "جملة اختبار أولى.", "title": "اختبار"}
            ],
        }
        timings = [
            {
                "id": "s1",
                "start": 0.0,
                "duration": 2.0,
                "words": flat_reels.even_word_timings(spec["scenes"][0]["text"].split(), 0.0, 2.0),
            }
        ]
        html = flat_reels.build_video_html(spec, timings, embed_font=False)
        ha = visual_qc.audit_html_motion_continuity(html, spec)
        self.assertTrue(ha["ok"], ha)
        self.assertGreaterEqual(ha["paper_ink_contrast"], 12.0)

        with tempfile.TemporaryDirectory() as tmp:
            colors = [(247, 240, 220), (228, 87, 46), (46, 134, 171), (255, 210, 63), (34, 30, 23), (180, 90, 60)]
            for idx, c in enumerate(colors):
                fp = os.path.join(tmp, f"frame_{idx:06d}.png")
                with open(fp, "wb") as fh:
                    fh.write(_make_solid_png_bytes(40, 40, c))

            fa = visual_qc.audit_rendered_frames(tmp)
            self.assertTrue(fa["ok"], fa)
            self.assertEqual(fa["motion_continuity_ratio"], 1.0)

            sheet_png = os.path.join(tmp, "sheet.png")
            ok_cs, cs_info = visual_qc.render_contact_sheet(tmp, sheet_png, cols=3, rows=2, thumb_width=120)
            self.assertTrue(ok_cs, cs_info)
            self.assertTrue(os.path.isfile(sheet_png))
            self.assertGreater(cs_info["width"], 300)

    def test_mcp_v344_tools(self):
        self.assertTrue(agentcut.__version__.startswith("3.4."))
        spec = {
            "id": "mcp-qc",
            "brand_kit": "midnight_tech",
            "scenes": [
                {"id": "s1", "type": "intro", "narration": "a.wav", "text": "مقدمة."},
                {"id": "s2", "type": "conclusion", "narration": "b.wav", "text": "خاتمة."},
            ],
        }
        p_res = server.dispatch_tool("patch_scene", {"spec": spec, "scene_id": "s2", "updates": {"body": "جديد"}})
        self.assertTrue(p_res["ok"])
        self.assertEqual(p_res["saved_render_percent"], 50.0)

        ab_res = server.dispatch_tool("generate_hook_ab", {"spec": spec})
        self.assertTrue(ab_res["ok"])
        self.assertEqual(ab_res["count"], 3)

        qc_res = server.dispatch_tool("visual_qc", {"spec": spec})
        self.assertTrue(qc_res["ok"])
        self.assertTrue(qc_res["html_audit"]["ok"])


if __name__ == "__main__":
    unittest.main()
