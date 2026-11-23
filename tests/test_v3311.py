"""Verification tests for AgentCut v3.3.11 (Cycle 10 Stage Approval Gates in autopilot_produce & Checkpoint Resume/Edit/Cancel)."""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.autopilot import (
    autopilot_produce,
    load_autopilot_checkpoint,
    resume_autopilot_checkpoint,
)
from agentcut.server import TOOLS, call_tool

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


class TestCycle10AutopilotApprovalGatesV3311(unittest.TestCase):
    def test_script_preview_and_pre_render_gates_with_edit_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            w1 = tdir / "s1.wav"
            w2 = tdir / "s2.wav"
            _write_test_wav(w1, duration=2.0)
            _write_test_wav(w2, duration=2.0)
            out_mp4 = tdir / "final_gated.mp4"
            chk_file = tdir / "checkpoint.json"

            brief_in = {
                "title": "مقال ببوابات مراجعة",
                "scenes": [
                    {
                        "id": "s1",
                        "text": "تخيل أن نراجع النص قبل أي تصيير.",
                        "narration": str(w1),
                        "broll": str(TEST_MEDIA / "poster.png"),
                    },
                    {
                        "id": "s2",
                        "text": "ثم نشاهد ورقة الاتصال قبل الرندر النهائي.",
                        "narration": str(w2),
                        "broll": str(TEST_MEDIA / "board.png"),
                    },
                ],
            }

            # 1. Stop at "script" gate
            ok1, res1 = autopilot_produce(
                brief_in,
                output_path=str(out_mp4),
                stop_at="script",
                checkpoint_path=str(chk_file),
            )
            self.assertTrue(ok1, res1)
            self.assertEqual(res1["mode"], "paused")
            self.assertEqual(res1["gate"], "script")
            self.assertFalse(out_mp4.exists(), "Video must NOT be rendered at script gate")
            self.assertEqual(len(res1["script_review"]), 2)
            self.assertTrue(chk_file.is_file())

            # 2. Edit scene 1 text and advance to "preview" gate
            ok2, res2 = resume_autopilot_checkpoint(
                str(chk_file),
                action="continue",
                edits={
                    "scenes": [
                        {"id": "s1", "text": "تخيل أن نعدّل السكريبت ونعتمد النسخة المنقحة فوراً."}
                    ]
                },
                stop_at="preview",
            )
            self.assertTrue(ok2, res2)
            self.assertEqual(res2["mode"], "paused")
            self.assertEqual(res2["gate"], "preview")
            self.assertFalse(out_mp4.exists(), "Final master must NOT exist yet at preview gate")
            self.assertEqual(
                res2["brief"]["scenes"][0]["text"],
                "تخيل أن نعدّل السكريبت ونعتمد النسخة المنقحة فوراً.",
            )
            prev = res2["preview"]
            self.assertTrue(prev["can_render_video_preview"])
            self.assertTrue(os.path.isfile(prev["preview_video"]))
            self.assertTrue(os.path.isfile(prev["contact_sheet"]))
            self.assertTrue(prev["frame_inspection"]["passed"])

            # 3. Advance to "pre_render" gate
            ok3, res3 = resume_autopilot_checkpoint(
                str(chk_file),
                action="continue",
                stop_at="pre_render",
            )
            self.assertTrue(ok3, res3)
            self.assertEqual(res3["mode"], "paused")
            self.assertEqual(res3["gate"], "pre_render")
            self.assertTrue(res3["ready_to_render"])
            self.assertFalse(out_mp4.exists())

            # 4. Resume from "pre_render" gate to final render ("none")
            ok4, res4 = resume_autopilot_checkpoint(
                str(chk_file),
                action="continue",
                stop_at="none",
            )
            self.assertTrue(ok4, res4)
            self.assertEqual(res4["mode"], "rendered")
            self.assertTrue(out_mp4.is_file())
            self.assertTrue(res4["auto_heal"]["qc"]["passed"])

    def test_cancel_checkpoint_and_71_mcp_tools(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("resume_autopilot", tool_names)

        with tempfile.TemporaryDirectory() as tmp:
            chk_p = os.path.join(tmp, "chk_cancel.json")
            r1 = call_tool(
                "autopilot_produce",
                {
                    "input": "تخيل البداية.\n\nلماذا نراجع السكريبت؟",
                    "stop_at": "script",
                    "checkpoint_path": chk_p,
                },
            )
            self.assertFalse(r1.get("isError"), r1)
            p1 = json.loads(r1["content"][0]["text"])
            self.assertEqual(p1["mode"], "paused")
            self.assertEqual(p1["gate"], "script")

            r2 = call_tool(
                "resume_autopilot",
                {
                    "checkpoint": chk_p,
                    "action": "cancel",
                },
            )
            self.assertFalse(r2.get("isError"), r2)
            p2 = json.loads(r2["content"][0]["text"])
            self.assertEqual(p2["mode"], "cancelled")
            loaded = load_autopilot_checkpoint(chk_p)
            self.assertEqual(loaded["status"], "cancelled")

            # CLI --stop-at and autopilot-resume test
            chk_cli = os.path.join(tmp, "chk_cli.json")
            rc1 = cli_main(["autopilot", "تخيل نصاً للتجربة.", "--stop-at", "script", "--checkpoint", chk_cli])
            self.assertEqual(rc1, 0)
            self.assertTrue(os.path.isfile(chk_cli))
            rc2 = cli_main(["autopilot-resume", chk_cli, "--action", "cancel"])
            self.assertEqual(rc2, 0)


if __name__ == "__main__":
    unittest.main()
