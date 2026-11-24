"""Browser renderer for AgentCut (v3.4.1 — flat-reels / browser render path).

Renders HTML/CSS/JS scenes in headless Chromium (Playwright) and
encodes the captured frames + audio into a video file.

Architecture (Reel Studio / HyperFrames): the BROWSER renders ALL
visuals — no FFmpeg filters, no drawtext, no zoompan, no FFmpeg
compositing anywhere in this path. FFmpeg is used ONLY as a dumb
encoder and audio mixer behind this module (mix_audio, audit_audio_mix,
and encode_frames): frames + audio -> mp4.

Everything is deterministic: the page exposes window.setTime(t)
and every visual depends on t only — no CSS animations with their
own clocks, no timers. Frame capture is screenshot-per-frame at
the target fps.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import tempfile
from typing import Any

PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None

_CHROMIUM_PROBE: bool | None = None

DEFAULT_FPS = 30
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280
DEFAULT_CRF = 18
DEFAULT_LOUDNESS_TARGET = -14.0
DEFAULT_TRUE_PEAK_CEILING = -1.0


def chromium_available() -> bool:
    """True when a headless Chromium can actually launch."""
    global _CHROMIUM_PROBE
    if _CHROMIUM_PROBE is not None:
        return _CHROMIUM_PROBE
    if not PLAYWRIGHT_AVAILABLE:
        _CHROMIUM_PROBE = False
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            browser.close()
        _CHROMIUM_PROBE = True
    except Exception:
        _CHROMIUM_PROBE = False
    return _CHROMIUM_PROBE


def frame_times(duration: float, fps: int = DEFAULT_FPS) -> list[float]:
    """Frame timestamps for a render: t = i / fps, i in [0, n)."""
    if duration <= 0 or fps <= 0:
        return []
    n = int(math.ceil(duration * fps))
    return [i / fps for i in range(n)]


def probe_audio_duration(path: str) -> float | None:
    """Duration in seconds of an audio file, via ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def mix_audio(tracks: list[dict], out_wav: str,
              duck_music: bool = True) -> tuple[bool, dict]:
    """Mix audio tracks into one WAV (dumb FFmpeg use only, never speeds up).

    tracks: [{path, volume=1.0, start=0.0, fade_in=0, fade_out=0,
              j_cut=0.0, l_cut=0.0, role='speech'|'music'|'sfx'}].
    Supports J-Cut (advances track start by up to `j_cut` seconds with a
    smooth fade-in overlap) and L-Cut (applies a soft `l_cut` fade-out tail)
    without ever altering playback speed (`atempo` is never used).
    When duck_music is True and both speech and music tracks exist,
    music tracks are automatically ducked under the combined speech
    stem via sidechaincompress so narration stays crisp.
    Returns (ok, info).
    """
    if not tracks:
        return False, {"code": "NO_TRACKS",
                       "message": "mix_audio needs at least one track"}
    for tr in tracks:
        if not os.path.isfile(tr["path"]):
            return False, {"code": "MISSING_AUDIO",
                           "message": f"audio file not found: {tr['path']}"}
    inputs: list[str] = []
    filt: list[str] = []
    speech_labels: list[str] = []
    sfx_labels: list[str] = []
    music_labels: list[str] = []
    jl_applied = 0

    for i, tr in enumerate(tracks):
        inputs += ["-i", tr["path"]]
        vol = float(tr.get("volume", 1.0))
        raw_start = float(tr.get("start", 0.0))
        j_cut = max(0.0, min(0.35, float(tr.get("j_cut", 0.0) or 0.0)))
        l_cut = max(0.0, min(0.45, float(tr.get("l_cut", 0.0) or 0.0)))
        start = max(0.0, raw_start - j_cut) if raw_start > 0 else raw_start
        fade_in = float(tr.get("fade_in", 0.0) or 0.0)
        fade_out = float(tr.get("fade_out", 0.0) or 0.0)
        if j_cut > 0 and fade_in <= 0:
            fade_in = round(min(0.08, j_cut * 0.6), 3)
        if l_cut > 0 and fade_out <= 0:
            fade_out = round(min(0.14, l_cut * 0.6), 3)
        if j_cut > 0 or l_cut > 0:
            jl_applied += 1

        parts = [f"volume={vol}"]
        if fade_in > 0:
            parts.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            dur = probe_audio_duration(tr["path"])
            if dur and dur > fade_out:
                st = max(0.0, dur - fade_out)
                parts.append(f"afade=t=out:st={st:.3f}:d={fade_out}")
        if start > 0:
            parts.append(f"adelay={int(round(start * 1000))}:all=1")
        lbl = f"[a{i}]"
        filt.append(f"[{i}:a]{','.join(parts)}{lbl}")
        role = tr.get("role")
        if role == "music":
            music_labels.append(lbl)
        elif role == "sfx":
            sfx_labels.append(lbl)
        else:
            speech_labels.append(lbl)

    ducked = False
    if duck_music and speech_labels and music_labels:
        if len(speech_labels) == 1:
            filt.append(f"{speech_labels[0]}asplit=2[sp_mix][sp_sc]")
        else:
            s_in = "".join(speech_labels)
            filt.append(
                f"{s_in}amix=inputs={len(speech_labels)}:normalize=0,"
                "asplit=2[sp_mix][sp_sc]")
        if len(music_labels) == 1:
            m_src = music_labels[0]
        else:
            m_in = "".join(music_labels)
            filt.append(
                f"{m_in}amix=inputs={len(music_labels)}:normalize=0[m_bus]")
            m_src = "[m_bus]"
        filt.append(
            f"{m_src}[sp_sc]sidechaincompress="
            "threshold=0.025:ratio=6:attack=25:release=280:makeup=1[m_duck]")
        final_ins = ["[sp_mix]", "[m_duck]"] + sfx_labels
        filt.append(
            f"{''.join(final_ins)}amix=inputs={len(final_ins)}:duration=first:normalize=0[aout]")
        ducked = True
    elif len(tracks) == 1:
        filt.append("[a0]anull[aout]")
    else:
        ins = "".join(f"[a{i}]" for i in range(len(tracks)))
        filt.append(f"{ins}amix=inputs={len(tracks)}:normalize=0[aout]")

    cmd = (["ffmpeg", "-y"] + inputs +
           ["-filter_complex", ";".join(filt),
            "-map", "[aout]", "-ac", "1", "-ar", "48000", out_wav])
    rc, _, err = _run(cmd, timeout=300)
    if rc != 0 or not os.path.isfile(out_wav):
        return False, {"code": "MIX_FAILED",
                       "message": f"ffmpeg mix failed (rc={rc}): "
                                  f"{err[-500:]}"}
    return True, {"output": out_wav,
                  "duration": probe_audio_duration(out_wav),
                  "ducked": ducked,
                  "jl_cuts_applied": jl_applied}


def measure_loudness(wav_path: str,
                     target: float = DEFAULT_LOUDNESS_TARGET
                     ) -> dict | None:
    """Measure integrated loudness with ffmpeg's loudnorm filter."""
    cmd = ["ffmpeg", "-i", wav_path, "-af",
           f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
           "-f", "null", "-"]
    rc, _, err = _run(cmd, timeout=300)
    if rc != 0:
        return None
    start = err.rfind("{")
    end = err.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(err[start:end + 1])
    except ValueError:
        return None
    if data.get("input_i") in (None, "-inf"):
        return None
    return data


def audit_audio_mix(
    wav_path: str,
    target_lufs: float = DEFAULT_LOUDNESS_TARGET,
    tolerance_lufs: float = 1.5,
    max_true_peak_dbtp: float = DEFAULT_TRUE_PEAK_CEILING,
) -> dict[str, Any]:
    """Audit an audio mix or encoded video for EBU R128 loudness (-14 LUFS)
    and True Peak compliance (<= -1.0 dBTP).

    Returns {ok, input_i, input_tp, input_lra, target_lufs, lufs_delta,
             true_peak_ok, loudness_ok, warnings}.
    """
    if not os.path.isfile(wav_path):
        return {
            "ok": False,
            "code": "MISSING_AUDIO",
            "message": f"audio file not found: {wav_path}",
            "warnings": [f"missing file: {wav_path}"],
        }
    measured = measure_loudness(wav_path, target=target_lufs)
    if not measured:
        return {
            "ok": False,
            "code": "UNMEASURABLE_AUDIO",
            "message": "audio loudness could not be measured (silence or invalid stream)",
            "warnings": ["unmeasurable or silent audio stream"],
        }
    try:
        in_i = float(measured["input_i"])
        in_tp = float(measured["input_tp"])
        in_lra = float(measured.get("input_lra", 0.0))
    except (KeyError, ValueError, TypeError):
        return {
            "ok": False,
            "code": "BAD_LOUDNORM_JSON",
            "message": "invalid loudnorm measurement fields",
            "warnings": ["invalid loudnorm fields"],
        }

    delta = round(abs(in_i - target_lufs), 2)
    loudness_ok = delta <= tolerance_lufs
    tp_ok = in_tp <= max_true_peak_dbtp
    warnings: list[str] = []
    if not loudness_ok:
        warnings.append(
            f"integrated loudness {in_i:.2f} LUFS differs from target "
            f"{target_lufs:.2f} LUFS by {delta:.2f} LU"
        )
    if not tp_ok:
        warnings.append(
            f"true peak {in_tp:.2f} dBTP exceeds ceiling {max_true_peak_dbtp:.2f} dBTP"
        )
    return {
        "ok": loudness_ok and tp_ok,
        "input_i": round(in_i, 2),
        "input_tp": round(in_tp, 2),
        "input_lra": round(in_lra, 2),
        "target_lufs": target_lufs,
        "lufs_delta": delta,
        "loudness_ok": loudness_ok,
        "true_peak_ok": tp_ok,
        "warnings": warnings,
    }


def build_loudnorm_filter(measured: dict | None,
                          target: float = DEFAULT_LOUDNESS_TARGET) -> str:
    """Linear loudnorm filter from measured values (constant gain,
    no pumping). Empty string when there is nothing to apply."""
    if not measured:
        return ""
    try:
        return (
            "loudnorm=I={t}:TP=-1.5:LRA=11:measured_I={mi}:"
            "measured_TP={mtp}:measured_LRA={mlra}:"
            "measured_thresh={mth}:offset={off}:linear=true".format(
                t=target, mi=measured["input_i"],
                mtp=measured["input_tp"], mlra=measured["input_lra"],
                mth=measured["input_thresh"],
                off=measured["target_offset"]))
    except KeyError:
        return ""


def encode_frames(frames_dir: str, audio_path: str | None,
                  output_path: str, fps: int = DEFAULT_FPS,
                  loudnorm_target: float = DEFAULT_LOUDNESS_TARGET,
                  crf: int = DEFAULT_CRF,
                  preset: str = "medium") -> tuple[bool, dict]:
    """THE encode step: frames + audio -> mp4 (dumb FFmpeg use)."""
    pattern = os.path.join(frames_dir, "frame_%06d.png")
    first = os.path.join(frames_dir, "frame_000000.png")
    if not os.path.isfile(first):
        return False, {"code": "NO_FRAMES",
                       "message": f"no frames in {frames_dir}"}
    n_frames = len([f for f in os.listdir(frames_dir)
                    if f.startswith("frame_") and f.endswith(".png")])
    cmd = ["ffmpeg", "-y", "-framerate", str(fps),
           "-i", pattern]
    filt: list[str] = []
    if audio_path:
        if not os.path.isfile(audio_path):
            return False, {"code": "MISSING_AUDIO",
                           "message": f"audio file not found: {audio_path}"}
        cmd += ["-i", audio_path]
        ln = build_loudnorm_filter(measure_loudness(audio_path,
                                                    loudnorm_target),
                                   loudnorm_target)
        if ln:
            filt = ["-filter_complex", f"[1:a]{ln}[a]",
                    "-map", "0:v", "-map", "[a]"]
        else:
            filt = ["-map", "0:v", "-map", "1:a"]
    else:
        filt = ["-map", "0:v"]
    cmd += filt + ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-r", str(fps), "-crf", str(crf), "-preset", preset]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-shortest"]
    cmd += ["-movflags", "+faststart", output_path]
    rc, _, err = _run(cmd, timeout=1800)
    if rc != 0 or not os.path.isfile(output_path):
        return False, {"code": "ENCODE_FAILED",
                       "message": f"ffmpeg encode failed (rc={rc}): "
                                  f"{err[-500:]}"}
    return True, {"output": output_path, "frames": n_frames,
                  "duration": n_frames / fps}


def capture_frames(html_path: str, duration: float,
                   frames_dir: str, fps: int = DEFAULT_FPS,
                   width: int = DEFAULT_WIDTH,
                   height: int = DEFAULT_HEIGHT,
                   scale: float = 1.0) -> tuple[bool, dict]:
    """Screenshot-per-frame capture of a browser page."""
    if not PLAYWRIGHT_AVAILABLE:
        return False, {"code": "PLAYWRIGHT_MISSING",
                       "message": "Playwright is not installed "
                                  "(pip install playwright; "
                                  "playwright install chromium)"}
    if not os.path.isfile(html_path):
        return False, {"code": "NO_HTML",
                       "message": f"HTML file not found: {html_path}"}
    times = frame_times(duration, fps)
    if not times:
        return False, {"code": "BAD_DURATION",
                       "message": f"bad duration {duration}"}
    os.makedirs(frames_dir, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--hide-scrollbars",
                "--force-color-profile=srgb",
                "--force-device-scale-factor=1"])
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=scale)
            page.goto("file://" + os.path.abspath(html_path))
            page.wait_for_function("typeof window.setTime === 'function'",
                                   timeout=15000)
            page.evaluate("document.fonts.ready.then(()=>{})")
            page.wait_for_timeout(200)
            for i, t in enumerate(times):
                page.evaluate(f"window.setTime({t!r})")
                page.screenshot(
                    path=os.path.join(frames_dir,
                                      f"frame_{i:06d}.png"))
            browser.close()
    except Exception as exc:  # noqa: BLE001 - surfaced in info
        return False, {"code": "CAPTURE_FAILED",
                       "message": f"browser capture failed: {exc}"}
    return True, {"frames": len(times), "frames_dir": frames_dir,
                  "duration": duration, "fps": fps,
                  "width": width, "height": height}


def capture_single_frame(html_path: str, t: float, out_png: str,
                         width: int = DEFAULT_WIDTH,
                         height: int = DEFAULT_HEIGHT,
                         scale: float = 1.0) -> tuple[bool, dict]:
    """Render one frame at time t — for previews and smoke tests."""
    if not PLAYWRIGHT_AVAILABLE:
        return False, {"code": "PLAYWRIGHT_MISSING",
                       "message": "Playwright is not installed"}
    if not os.path.isfile(html_path):
        return False, {"code": "NO_HTML",
                       "message": f"HTML file not found: {html_path}"}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--hide-scrollbars",
                "--force-color-profile=srgb"])
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=scale)
            page.goto("file://" + os.path.abspath(html_path))
            page.wait_for_function("typeof window.setTime === 'function'",
                                   timeout=15000)
            page.evaluate("document.fonts.ready.then(()=>{})")
            page.wait_for_timeout(200)
            page.evaluate(f"window.setTime({t!r})")
            page.screenshot(path=out_png)
            browser.close()
    except Exception as exc:  # noqa: BLE001 - surfaced in info
        return False, {"code": "CAPTURE_FAILED",
                       "message": f"browser capture failed: {exc}"}
    return True, {"output": out_png, "t": t}




def get_fast_scratch_dir(prefix: str = "agentcut-fast-") -> str:
    """Choose `/dev/shm` RAM-disk when available with >150MB free so frame
    scratch never thrashes an HDD; falls back to system temp directory."""
    shm = "/dev/shm"
    if os.path.isdir(shm) and os.access(shm, os.W_OK):
        try:
            st = os.statvfs(shm)
            if st.f_bavail * st.f_frsize > 150 * 1024 * 1024:
                return tempfile.mkdtemp(prefix=prefix, dir=shm)
        except OSError:
            pass
    return tempfile.mkdtemp(prefix=prefix)


def encode_png_stream_pipe(
    png_frames_iter,
    audio_path: str | None,
    output_path: str,
    fps: int = DEFAULT_FPS,
    loudnorm_target: float = DEFAULT_LOUDNESS_TARGET,
    crf: int = DEFAULT_CRF,
    preset: str = "medium",
) -> tuple[bool, dict]:
    """Stream raw PNG byte buffers directly into FFmpeg stdin (`image2pipe`)
    so zero `frame_XXXXXX.png` files ever hit the hard drive."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-f", "image2pipe", "-vcodec", "png", "-framerate", str(fps),
        "-i", "-",
    ]
    filt: list[str] = []
    if audio_path:
        if not os.path.isfile(audio_path):
            return False, {"code": "MISSING_AUDIO", "message": f"audio file not found: {audio_path}"}
        cmd += ["-i", audio_path]
        ln = build_loudnorm_filter(measure_loudness(audio_path, loudnorm_target), loudnorm_target)
        if ln:
            filt = ["-filter_complex", f"[1:a]{ln}[a]", "-map", "0:v", "-map", "[a]"]
        else:
            filt = ["-map", "0:v", "-map", "1:a"]
    else:
        filt = ["-map", "0:v"]
    cmd += filt + [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-crf", str(crf), "-preset", preset,
    ]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    cmd += ["-movflags", "+faststart", output_path]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    n_frames = 0
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert proc.stdin is not None
        for png_bytes in png_frames_iter:
            proc.stdin.write(png_bytes)
            n_frames += 1
        proc.stdin.close()
        proc.stdin = None
        _, err_bytes = proc.communicate(timeout=1800)
        rc = proc.returncode
        err = err_bytes.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return False, {"code": "PIPE_ENCODE_FAILED", "message": str(exc)}

    if n_frames == 0:
        return False, {"code": "NO_FRAMES", "message": "zero PNG frames streamed to pipe"}
    if rc != 0 or not os.path.isfile(output_path):
        return False, {"code": "ENCODE_FAILED", "message": f"ffmpeg pipe encode failed (rc={rc}): {err[-500:]}"}
    return True, {
        "output": output_path,
        "frames": n_frames,
        "duration": n_frames / fps,
        "pipe_mode": True,
    }


def render_browser_page(html: str, duration: float,
                        audio_path: str | None, output_path: str,
                        fps: int = DEFAULT_FPS,
                        width: int = DEFAULT_WIDTH,
                        height: int = DEFAULT_HEIGHT,
                        scale: float = 1.0,
                        loudnorm_target: float = DEFAULT_LOUDNESS_TARGET,
                        keep_frames: bool = False,
                        work_dir: str | None = None,
                        pipe_mode: bool = False
                        ) -> tuple[bool, dict]:
    """Full browser render: HTML + duration + audio -> mp4.
    When pipe_mode=True and keep_frames=False, streams PNG frames in memory
    directly to FFmpeg stdin without writing frame PNGs to disk."""
    tmp = work_dir or get_fast_scratch_dir(prefix="agentcut-browser-")
    os.makedirs(tmp, exist_ok=True)
    html_path = os.path.join(tmp, "render.html")
    frames_dir = os.path.join(tmp, "frames")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    if pipe_mode and not keep_frames:
        if not PLAYWRIGHT_AVAILABLE:
            return False, {"code": "PLAYWRIGHT_MISSING", "message": "Playwright is not installed"}
        times = frame_times(duration, fps)
        if not times:
            return False, {"code": "BAD_DURATION", "message": f"bad duration {duration}"}
        try:
            from playwright.sync_api import sync_playwright

            def _gen():
                with sync_playwright() as p:
                    browser = p.chromium.launch(args=[
                        "--no-sandbox", "--disable-dev-shm-usage",
                        "--disable-gpu", "--hide-scrollbars",
                        "--force-color-profile=srgb",
                        "--force-device-scale-factor=1"])
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=scale)
                    page.goto("file://" + os.path.abspath(html_path))
                    page.wait_for_function("typeof window.setTime === 'function'", timeout=15000)
                    page.evaluate("document.fonts.ready.then(()=>{})")
                    page.wait_for_timeout(200)
                    for t in times:
                        page.evaluate(f"window.setTime({t!r})")
                        yield page.screenshot(type="png")
                    browser.close()

            return encode_png_stream_pipe(
                _gen(), audio_path, output_path, fps=fps, loudnorm_target=loudnorm_target
            )
        except Exception as exc:  # noqa: BLE001
            return False, {"code": "CAPTURE_FAILED", "message": f"browser pipe capture failed: {exc}"}

    ok, cap = capture_frames(html_path, duration, frames_dir,
                             fps=fps, width=width, height=height,
                             scale=scale)
    if not ok:
        return False, cap
    ok, enc = encode_frames(frames_dir, audio_path, output_path,
                            fps=fps, loudnorm_target=loudnorm_target)
    if not ok:
        return False, enc
    info = dict(enc)
    info["frames_dir"] = frames_dir if keep_frames else None
    if not keep_frames:
        for name in os.listdir(frames_dir):
            try:
                os.remove(os.path.join(frames_dir, name))
            except OSError:
                pass
        try:
            os.rmdir(frames_dir)
        except OSError:
            pass
    return True, info
