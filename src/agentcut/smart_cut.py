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
