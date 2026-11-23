"""Audio-driven Silence Trimmer / Smart Jump-Cut Engine (v2.0.0).

Analyzes talking-head or voiceover recordings using FFmpeg `silencedetect`,
removes dead-air pauses longer than `min_silence`, preserves natural breathing
padding around speech, and produces either a clean AgentCut project JSON or a
rendered jump-cut MP4. Zero external dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .validate import probe_media


_SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[0-9.]+)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<dur>[0-9.]+)"
)


def detect_speech_segments(
    media_path: str,
    noise_db: float = -35.0,
    min_silence: float = 0.55,
    pad_before: float = 0.10,
    pad_after: float = 0.12,
    min_segment: float = 0.25,
    timeout: int = 120,
) -> dict[str, Any]:
    """Detect active speech segments by inverting FFmpeg `silencedetect` spans."""
    if not media_path or not os.path.isfile(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path!r}")

    info = probe_media(media_path)
    if not info or not info.get("duration"):
        raise ValueError(f"Cannot probe media duration: {media_path!r}")
    total_dur = float(info["duration"])

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", media_path,
        "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    stderr = proc.stderr or ""

    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in stderr.splitlines():
        ms = _SILENCE_START_RE.search(line)
        if ms:
            cur_start = float(ms.group("start"))
            continue
        me = _SILENCE_END_RE.search(line)
        if me:
            end_val = float(me.group("end"))
            dur_val = float(me.group("dur"))
            start_val = cur_start if cur_start is not None else max(0.0, end_val - dur_val)
            silences.append((max(0.0, start_val), min(total_dur, end_val)))
            cur_start = None
    if cur_start is not None and cur_start < total_dur:
        silences.append((max(0.0, cur_start), total_dur))

    # Invert silence ranges into raw speech ranges
    raw_speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted(silences):
        if s_start > cursor:
            raw_speech.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < total_dur:
        raw_speech.append((cursor, total_dur))

    # Apply natural padding and merge overlapping ranges
    padded: list[list[float]] = []
    for a, b in raw_speech:
        if (b - a) < min_segment:
            continue
        pa = max(0.0, a - pad_before)
        pb = min(total_dur, b + pad_after)
        if padded and pa <= padded[-1][1] + 0.05:
            padded[-1][1] = max(padded[-1][1], pb)
        else:
            padded.append([pa, pb])

    # Fallback: if everything was classified as silence, keep the full clip
    if not padded:
        padded = [[0.0, total_dur]]

    segments = [
        {"start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3)}
        for a, b in padded
    ]
    kept_dur = round(sum(s["duration"] for s in segments), 3)
    saved = round(max(0.0, total_dur - kept_dur), 3)
    saved_pct = round((saved / total_dur) * 100.0, 1) if total_dur > 0 else 0.0

    return {
        "media_path": media_path,
        "original_duration": round(total_dur, 3),
        "trimmed_duration": kept_dur,
        "saved_seconds": saved,
        "saved_pct": saved_pct,
        "silence_count": len(silences),
        "segment_count": len(segments),
        "segments": segments,
        "probe": info,
    }


def build_jump_cut_project(
    media_path: str,
    noise_db: float = -35.0,
    min_silence: float = 0.55,
    pad_before: float = 0.10,
    pad_after: float = 0.12,
    min_segment: float = 0.25,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a valid AgentCut project dict containing jump-cut clips for active speech."""
    analysis = detect_speech_segments(
        media_path,
        noise_db=noise_db,
        min_silence=min_silence,
        pad_before=pad_before,
        pad_after=pad_after,
        min_segment=min_segment,
    )
    info = analysis["probe"]
    width = int(info.get("width") or 1920)
    height = int(info.get("height") or 1080)
    fps = int(round(float(info.get("fps") or 30)))
    abs_src = os.path.abspath(media_path)

    clips = [
        {
            "id": f"cut_{idx + 1}",
            "kind": "video",
            "src": abs_src,
            "trim_in": seg["start"],
            "trim_out": seg["end"],
            "transition": "none",
        }
        for idx, seg in enumerate(analysis["segments"])
    ]

    project = {
        "id": "smart_jump_cut",
        "version": 1,
        "output": {
            "width": width,
            "height": height,
            "fps": fps,
        },
        "clips": clips,
    }
    return project, analysis


def smart_cut_media(
    media_path: str,
    output_path: str,
    noise_db: float = -35.0,
    min_silence: float = 0.55,
    pad_before: float = 0.10,
    pad_after: float = 0.12,
    min_segment: float = 0.25,
) -> tuple[bool, dict[str, Any]]:
    """Detect silences, build jump-cut timeline, and render the tightened MP4."""
    from .render import render_project

    project, analysis = build_jump_cut_project(
        media_path,
        noise_db=noise_db,
        min_silence=min_silence,
        pad_before=pad_before,
        pad_after=pad_after,
        min_segment=min_segment,
    )
    ok, rinfo = render_project(project, output_path)
    out = dict(rinfo) if isinstance(rinfo, dict) else {"result": rinfo}
    out.update({
        "original_duration": analysis["original_duration"],
        "trimmed_duration": analysis["trimmed_duration"],
        "saved_seconds": analysis["saved_seconds"],
        "saved_pct": analysis["saved_pct"],
        "segment_count": analysis["segment_count"],
    })
    return ok, out


def clean_tts_audio(
    input_audio: str,
    output_audio: str,
    trim_tail_sec: float = 0.15,
    fade_out_sec: float = 0.07,
    highpass_hz: int = 75,
    lowpass_hz: int = 12500,
    silence_db: float = -40.0,
    preserve_duration: bool = False,
    timeout: int = 60,
) -> tuple[bool, dict[str, Any]]:
    """Remove TTS leading/trailing silence, breath/whistle hallucinations, and harsh rumble (v3.3.1).

    Specifically targets voice-cloning tail artifacts (e.g. Chatterbox snorts,
    whistles, and breath spikes at utterance endings) via reverse-silenceremove,
    band-limiting (75Hz-12.5kHz), optional safety tail trim, and smooth fade-out.
    """
    if not input_audio or not os.path.isfile(input_audio):
        return False, {"code": "FILE_NOT_FOUND", "message": f"Audio file not found: {input_audio!r}"}

    info = probe_media(input_audio)
    orig_dur = float(info.get("duration") or 0.0) if info else 0.0
    os.makedirs(os.path.dirname(os.path.abspath(output_audio)), exist_ok=True)

    # Add gentle fade-in/out to prevent clicks
    af_chain = [
        f"highpass=f={int(highpass_hz)}",
        f"lowpass=f={int(lowpass_hz)}",
    ]
    if not preserve_duration:
        af_chain.extend([
            f"silenceremove=start_periods=1:start_duration=0.05:start_threshold={silence_db}dB",
            "areverse",
            f"silenceremove=start_periods=1:start_duration=0.05:start_threshold={silence_db}dB",
            "areverse",
        ])
    af_chain.append("afade=t=in:st=0:d=0.02")
    if orig_dur > (trim_tail_sec + fade_out_sec + 0.5) and trim_tail_sec > 0:
        safe_end = max(0.3, orig_dur - trim_tail_sec)
        af_chain.append(f"atrim=end={safe_end:.3f}")
        af_chain.append(f"afade=t=out:st={max(0.0, safe_end - fade_out_sec):.3f}:d={fade_out_sec:.3f}")
        if preserve_duration:
            # Pad clean silence back to exact orig_dur so beat tiling & word_timings never desync
            af_chain.append(f"apad=whole_dur={orig_dur:.6f}")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-i", input_audio,
        "-af", ",".join(af_chain),
        output_audio,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.isfile(output_audio):
        return False, {"code": "TTS_CLEAN_FAILED", "message": (proc.stderr or "").strip() or "ffmpeg failed"}

    new_info = probe_media(output_audio)
    new_dur = float(new_info.get("duration") or 0.0) if new_info else 0.0
    return True, {
        "input": input_audio,
        "output": output_audio,
        "original_duration": round(orig_dur, 3),
        "cleaned_duration": round(new_dur, 3),
        "trimmed_seconds": round(max(0.0, orig_dur - new_dur), 3),
        "preserve_duration": bool(preserve_duration),
        "muted_tail_seconds": round(trim_tail_sec if (preserve_duration and orig_dur > (trim_tail_sec + fade_out_sec + 0.5)) else max(0.0, orig_dur - new_dur), 3),
    }
