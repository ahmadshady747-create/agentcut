"""Verification tests for AgentCut v3.3.7 (Cycle 7 Render Speed Optimization on Constrained 8GB RAM / HDD / CPU-Only Hardware)."""
from __future__ import annotations

import math
import os
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from agentcut import __version__
from agentcut.__main__ import main as cli_main
from agentcut.compositor import write_cinematic_gradient
from agentcut.profiles import (
    benchmark_render_speed,
    compute_ffmpeg_threads_per_worker,
    get_fast_temp_root,
)
from agentcut.server import TOOLS, call_tool
from agentcut.validate import _fast_probe_header, probe_media


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


class TestCycle7ConstrainedHardwareSpeedV337(unittest.TestCase):
    def test_fast_probe_header_and_cached_gradient_speedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            g1 = tdir / "g1.png"
            g2 = tdir / "g2.png"

            write_cinematic_gradient(str(g1), 720, 1280)
            t0 = time.perf_counter()
            write_cinematic_gradient(str(g2), 720, 1280)
            cached_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(cached_ms, 25.0)
            self.assertEqual(g1.read_bytes(), g2.read_bytes())

            # Pure-Python header probe for PNG and WAV (< 2ms, zero subprocesses)
            w1 = tdir / "test.wav"
            _write_test_wav(w1, duration=2.5)

            t1 = time.perf_counter()
            p_png = _fast_probe_header(str(g1))
            p_wav = _fast_probe_header(str(w1))
            probe_ms = (time.perf_counter() - t1) * 1000.0

            self.assertIsNotNone(p_png)
            self.assertEqual((p_png["width"], p_png["height"]), (720, 1280))
            self.assertIsNotNone(p_wav)
            self.assertAlmostEqual(p_wav["duration"], 2.5, places=2)
            self.assertLess(probe_ms, 10.0)

    def test_ram_disk_scratch_and_thread_governor(self) -> None:
        scratch = get_fast_temp_root()
        self.assertTrue(os.path.isdir(scratch))
        threads = compute_ffmpeg_threads_per_worker(max_workers=2)
        self.assertGreaterEqual(threads, 1)
        self.assertLessEqual(threads, 4)

        bench = benchmark_render_speed(width=360, height=640)
        self.assertTrue(bench["probe_valid"])
        self.assertLess(bench["cached_gradient_ms"], 25.0)
        self.assertLess(bench["fast_probe_ms"], 10.0)

    def test_68_mcp_tools_and_cli_benchmark(self) -> None:
        self.assertEqual(__version__, "3.3.13")
        self.assertEqual(len(TOOLS), 73)
        tool_names = {t["name"] for t in TOOLS}
        self.assertIn("benchmark_render_speed", tool_names)

        res = call_tool("benchmark_render_speed", {"width": 360, "height": 640})
        self.assertFalse(res.get("isError"), res)
        rc = cli_main(["benchmark", "--width", "360", "--height", "640"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
