"""Verification tests for AgentCut v3.3.10 (Meta Muse Secure VM Hardware Profile & Cgroup Detection)."""
from __future__ import annotations

import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from agentcut import __version__
from agentcut.profiles import (
    _detect_cgroup_ram_gb,
    apply_profile_to_brief,
    detect_hardware_profile,
    detect_muse_environment,
    get_render_profile,
    list_render_profiles,
)
from agentcut.qc import audit_video_quality
from agentcut.render import produce_explainer

REPO = Path(__file__).resolve().parent.parent
TEST_MEDIA = REPO / "data" / "test_media"


def _write_test_wav(path: Path, duration: float = 2.0) -> None:
    sr = 16000
    n = int(sr * duration)
    buf = bytearray()
    for i in range(n):
        t = i / sr
        val = int(0.35 * 32767 * math.sin(2.0 * math.pi * 220.0 * t))
        buf.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(buf))


class TestMuseVmSupportV3310(unittest.TestCase):
    def test_muse_vm_profile_and_cgroup_detection(self) -> None:
        names = {p["name"] for p in list_render_profiles()}
        self.assertIn("muse_vm", names)

        prof = get_render_profile("muse_vm")
        self.assertEqual(prof["max_workers"], 1)
        self.assertEqual(prof["fps"], 24)
        self.assertEqual(prof["video_preset"], "superfast")
        self.assertAlmostEqual(prof["scale_factor"], 0.67, places=2)
        self.assertEqual(get_render_profile("muse")["name"], "muse_vm")

        with tempfile.TemporaryDirectory() as tmp:
            cg_v2 = Path(tmp) / "memory.max"
            cg_v2.write_text(str(2 * 1024 ** 3), encoding="utf-8")
            self.assertAlmostEqual(_detect_cgroup_ram_gb(str(cg_v2), str(Path(tmp) / "missing")), 2.0, places=2)

        old_env = os.environ.get("AGENTCUT_MUSE_ENV")
        try:
            os.environ["AGENTCUT_MUSE_ENV"] = "1"
            self.assertTrue(detect_muse_environment())
            hw = detect_hardware_profile()
            self.assertTrue(hw["is_muse_env"])
            self.assertEqual(hw["recommended_profile"], "muse_vm")
            self.assertEqual(hw["safe_max_workers"], 1)
        finally:
            if old_env is None:
                os.environ.pop("AGENTCUT_MUSE_ENV", None)
            else:
                os.environ["AGENTCUT_MUSE_ENV"] = old_env

    def test_apply_muse_vm_and_render_single_worker(self) -> None:
        applied = apply_profile_to_brief({"title": "Muse Test", "parallel": True, "scenes": []}, "muse_vm")
        self.assertEqual(applied["render_profile"], "muse_vm")
        self.assertEqual(applied["max_workers"], 1)
        self.assertFalse(applied["parallel"])

        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            _write_test_wav(w1, duration=2.0)
            srt1 = tdir / "s1.srt"
            srt1.write_text("1\n00:00:00,050 --> 00:00:01,850\nتجهيز بيئة ميوس للتصيير السريع.\n", encoding="utf-8")

            out_mp4 = tdir / "muse_out.mp4"
            brief = {
                "title": "Muse Render",
                "style": "editorial",
                "render_profile": "muse_vm",
                "output": {"width": 720, "height": 1280, "fps": 24},
                "scenes": [
                    {
                        "id": "s1",
                        "text": "تجهيز بيئة ميوس للتصيير السريع.",
                        "narration": str(w1),
                        "captions": str(srt1),
                        "broll": str(TEST_MEDIA / "poster.png"),
                    }
                ],
            }
            ok, info = produce_explainer(brief, str(out_mp4))
            self.assertTrue(ok, info)
            self.assertFalse(info["parallel"])
            self.assertEqual(info["width"], 482)
            self.assertEqual(info["height"], 857)
            qc = audit_video_quality(str(out_mp4))
            self.assertTrue(qc["passed"], qc)

        self.assertEqual(__version__, "3.3.13")


if __name__ == "__main__":
    unittest.main()
