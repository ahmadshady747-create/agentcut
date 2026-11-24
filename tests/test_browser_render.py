"""Tests for the browser renderer (Playwright + Chromium + FFmpeg).

Pure tests run everywhere. Browser-backed tests skip cleanly when
Playwright or a launchable Chromium is absent — the same gating
pattern as the OTIO tests.
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentcut import browser_render  # noqa: E402

BROWSER_OK = (browser_render.PLAYWRIGHT_AVAILABLE
              and browser_render.chromium_available())

_TINY_HTML = """<!DOCTYPE html><html><head><style>
body{margin:0;width:720px;height:1280px;background:#F7F0DC}
#box{width:200px;height:200px;background:#E4572E}
</style></head><body><div id="box"></div><script>
window.setTime = function(t){
  const b = document.getElementById('box');
  b.style.transform = 'translateX(' + (t*100) + 'px)';
};
</script></body></html>"""


def _sine_wav(path, duration=1.0, freq=440):
    # Pure-Python sine WAV (same helper as test_v164) — no ffmpeg
    # dependency for a test fixture.
    rate = 48000
    frames = int(duration * rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            v = int(12000 * math.sin(2 * math.pi * freq * i / rate))
            data += struct.pack("<h", v)
        wf.writeframes(bytes(data))


def _ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip())


class TestFrameTimes(unittest.TestCase):
    def test_sixty_seconds_at_30fps(self):
        times = browser_render.frame_times(60.0, 30)
        self.assertEqual(len(times), 1800)
        self.assertEqual(times[0], 0.0)
        self.assertAlmostEqual(times[-1], 1799 / 30)

    def test_fractional_duration_rounds_up(self):
        times = browser_render.frame_times(0.05, 30)
        self.assertEqual(len(times), 2)

    def test_bad_inputs_empty(self):
        self.assertEqual(browser_render.frame_times(0, 30), [])
        self.assertEqual(browser_render.frame_times(1.0, 0), [])


class TestLoudness(unittest.TestCase):
    def test_build_filter_from_measured(self):
        filt = browser_render.build_loudnorm_filter({
            "input_i": "-20.0", "input_tp": "-3.0",
            "input_lra": "5.0", "input_thresh": "-35.0",
            "target_offset": "6.0"}, target=-14.0)
        self.assertIn("loudnorm=I=-14.0", filt)
        self.assertIn("measured_I=-20.0", filt)
        self.assertIn("linear=true", filt)

    def test_build_filter_empty_without_measurement(self):
        self.assertEqual(browser_render.build_loudnorm_filter(None), "")
        self.assertEqual(browser_render.build_loudnorm_filter({}), "")

    def test_measure_real_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            _sine_wav(wav, 1.0)
            data = browser_render.measure_loudness(wav)
            self.assertIsNotNone(data)
            self.assertIn("input_i", data)


class TestAudio(unittest.TestCase):
    def test_probe_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            _sine_wav(wav, 2.0)
            self.assertAlmostEqual(
                browser_render.probe_audio_duration(wav), 2.0, places=1)

    def test_probe_missing_file_none(self):
        self.assertIsNone(
            browser_render.probe_audio_duration("/tmp/no-such-file.wav"))

    def test_mix_two_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.wav")
            b = os.path.join(tmp, "b.wav")
            _sine_wav(a, 1.0, 440)
            _sine_wav(b, 1.0, 880)
            out = os.path.join(tmp, "mix.wav")
            ok, info = browser_render.mix_audio(
                [{"path": a, "volume": 1.0, "start": 0.0},
                 {"path": b, "volume": 0.5, "start": 1.0}], out)
            self.assertTrue(ok, info)
            self.assertAlmostEqual(_ffprobe_duration(out), 2.0, places=1)

    def test_mix_missing_track_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, info = browser_render.mix_audio(
                [{"path": "/tmp/no-such-file.wav"}],
                os.path.join(tmp, "mix.wav"))
            self.assertFalse(ok)
            self.assertEqual(info["code"], "MISSING_AUDIO")


class TestEncode(unittest.TestCase):
    def test_encode_without_frames_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, info = browser_render.encode_frames(
                tmp, None, os.path.join(tmp, "out.mp4"))
            self.assertFalse(ok)
            self.assertEqual(info["code"], "NO_FRAMES")


@unittest.skipUnless(BROWSER_OK,
                     "Playwright/Chromium not available (browser-gated)")
class TestBrowserCapture(unittest.TestCase):
    def test_capture_half_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "page.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(_TINY_HTML)
            frames = os.path.join(tmp, "frames")
            ok, info = browser_render.capture_frames(
                html_path, 0.5, frames, fps=30)
            self.assertTrue(ok, info)
            self.assertEqual(info["frames"], 15)
            files = sorted(os.listdir(frames))
            self.assertEqual(len(files), 15)
            self.assertTrue(files[0].startswith("frame_000000"))
            # Frames actually change with t (the box moves).
            first = open(os.path.join(frames, files[0]), "rb").read()
            last = open(os.path.join(frames, files[-1]), "rb").read()
            self.assertNotEqual(first, last)

    def test_capture_single_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "page.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(_TINY_HTML)
            out = os.path.join(tmp, "frame.png")
            ok, info = browser_render.capture_single_frame(
                html_path, 0.25, out)
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 1000)

    def test_end_to_end_one_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            _sine_wav(wav, 1.0)
            out = os.path.join(tmp, "out.mp4")
            ok, info = browser_render.render_browser_page(
                _TINY_HTML, 1.0, wav, out, fps=30)
            self.assertTrue(ok, info)
            self.assertTrue(os.path.isfile(out))
            self.assertAlmostEqual(_ffprobe_duration(out), 1.0,
                                   places=1)


if __name__ == "__main__":
    unittest.main()


class TestAudioDucking(unittest.TestCase):
    def test_mix_audio_ducks_music_under_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = os.path.join(tmp, "speech.wav")
            bg = os.path.join(tmp, "music.wav")
            _sine_wav(sp, 1.5, 440)
            _sine_wav(bg, 1.5, 220)
            out = os.path.join(tmp, "mix.wav")
            ok, info = browser_render.mix_audio(
                [{"path": sp, "volume": 1.0, "start": 0.0, "role": "speech"},
                 {"path": bg, "volume": 0.3, "start": 0.0, "role": "music"}],
                out, duck_music=True)
            self.assertTrue(ok, info)
            self.assertTrue(info.get("ducked"))
            self.assertAlmostEqual(_ffprobe_duration(out), 1.5, places=1)
