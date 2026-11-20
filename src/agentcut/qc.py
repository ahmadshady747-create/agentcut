"""Automated Media Quality Control & Self-Critique Engine (v2.0.0).

Zero external dependencies (Python stdlib + FFmpeg/FFprobe). Inspects rendered
videos for structural, visual, and audio defects so AI agents can run closed-loop
self-healing renders without human eye-checking every draft:
- Video integrity: stream presence, SAR=1:1 check, FPS, expected duration drift.
- Audio integrity: stream presence, A/V sync drift (ms), clipping (max_volume),
  loudness compliance (mean_volume vs target), and unexpected silence gaps.
- Visual defects: black-frame segments via FFmpeg `blackdetect`.
- Structured 0-100 quality score, letter grade, pass/fail verdict, and
  machine-actionable fix suggestions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any


_BLACK_RE = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<dur>[0-9.]+)"
)
_SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[0-9.]+)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<dur>[0-9.]+)"
)
_MEAN_VOL_RE = re.compile(r"mean_volume:\s*(?P<val>-?[0-9.]+)\s*dB")
_MAX_VOL_RE = re.compile(r"max_volume:\s*(?P<val>-?[0-9.]+)\s*dB")


def _run_ffprobe_json(video_path: str, timeout: int = 30) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json",
        video_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip() or proc.returncode}")
    return json.loads(proc.stdout or "{}")


def _parse_fps(rate_str: str | None) -> float | None:
    if not rate_str or rate_str in ("0/0", "N/A"):
        return None
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        try:
            d = float(den)
            return round(float(num) / d, 3) if d > 0 else None
        except ValueError:
            return None
    try:
        return round(float(rate_str), 3)
    except ValueError:
        return None


def _run_ffmpeg_analysis(
    video_path: str,
    has_video: bool,
    has_audio: bool,
    check_black: bool = True,
    check_silence: bool = True,
    black_min_dur: float = 0.4,
    silence_noise_db: float = -45.0,
    silence_min_dur: float = 1.5,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run a single fast FFmpeg null-sink pass for blackdetect + silencedetect + volumedetect."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", video_path]
    if has_video and check_black:
        cmd.extend(["-vf", f"blackdetect=d={black_min_dur}:pix_th=0.10"])
    else:
        cmd.append("-vn")

    if has_audio:
        afilters = ["volumedetect"]
        if check_silence:
            afilters.append(f"silencedetect=noise={silence_noise_db}dB:d={silence_min_dur}")
        cmd.extend(["-af", ",".join(afilters)])
    else:
        cmd.append("-an")

    cmd.extend(["-f", "null", "-"])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    stderr = proc.stderr or ""

    black_segments = [
        {
            "start": round(float(m.group("start")), 3),
            "end": round(float(m.group("end")), 3),
            "duration": round(float(m.group("dur")), 3),
        }
        for m in _BLACK_RE.finditer(stderr)
    ]

    silence_segments: list[dict[str, float]] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        ms = _SILENCE_START_RE.search(line)
        if ms:
            pending_start = float(ms.group("start"))
            continue
        me = _SILENCE_END_RE.search(line)
        if me:
            end_val = float(me.group("end"))
            dur_val = float(me.group("dur"))
            start_val = pending_start if pending_start is not None else max(0.0, end_val - dur_val)
            silence_segments.append({
                "start": round(start_val, 3),
                "end": round(end_val, 3),
                "duration": round(dur_val, 3),
            })
            pending_start = None

    mean_m = _MEAN_VOL_RE.search(stderr)
    max_m = _MAX_VOL_RE.search(stderr)
    return {
        "black_segments": black_segments,
        "silence_segments": silence_segments,
        "mean_volume_db": float(mean_m.group("val")) if mean_m else None,
        "max_volume_db": float(max_m.group("val")) if max_m else None,
    }


def audit_video_quality(
    video_path: str,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.5,
    max_av_drift_ms: float = 150.0,
    check_black: bool = True,
    check_silence: bool = True,
    silence_min_dur: float = 1.5,
    require_audio: bool = True,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run an automated technical & perceptual QC audit on a rendered media file.

    Returns a structured report with score (0-100), grade (A-F), passed bool,
    metrics, and actionable issues list for AI self-healing loops.
    """
    if not video_path or not os.path.isfile(video_path):
        return {
            "passed": False,
            "score": 0,
            "grade": "F",
            "video_path": video_path,
            "issues": [{
                "severity": "error",
                "code": "FILE_NOT_FOUND",
                "message": f"Media file does not exist: {video_path!r}",
                "fix": "Render the project before running QC audit.",
            }],
            "metrics": {},
        }

    file_size = os.path.getsize(video_path)
    issues: list[dict[str, Any]] = []
    score = 100

    try:
        probe = _run_ffprobe_json(video_path, timeout=min(30, timeout))
    except Exception as exc:
        return {
            "passed": False,
            "score": 0,
            "grade": "F",
            "video_path": video_path,
            "issues": [{
                "severity": "error",
                "code": "CORRUPT_CONTAINER",
                "message": f"FFprobe could not read container: {exc}",
                "fix": "Re-render the file; the container header/moov atom may be truncated.",
            }],
            "metrics": {"file_size_bytes": file_size},
        }

    streams = probe.get("streams", [])
    fmt = probe.get("format", {})
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fmt_dur = float(fmt["duration"]) if fmt.get("duration") not in (None, "N/A") else 0.0
    v_dur = float(vstream["duration"]) if (vstream and vstream.get("duration") not in (None, "N/A")) else fmt_dur
    a_dur = float(astream["duration"]) if (astream and astream.get("duration") not in (None, "N/A")) else 0.0

    if vstream is None:
        issues.append({
            "severity": "error",
            "code": "NO_VIDEO_STREAM",
            "message": "No video stream found in output file.",
            "fix": "Ensure clips are mapped to the video output stream.",
        })
        score -= 50

    if require_audio and astream is None:
        issues.append({
            "severity": "warning",
            "code": "NO_AUDIO_STREAM",
            "message": "No audio stream present in output file.",
            "fix": "Attach narration or background audio track, or set require_audio=False for silent visuals.",
        })
        score -= 15

    # Check SAR (Sample Aspect Ratio) — non-1:1 causes stretched frames after concat
    sar = vstream.get("sample_aspect_ratio") if vstream else None
    if sar and sar not in ("1:1", "0:1", "N/A"):
        issues.append({
            "severity": "warning",
            "code": "NON_SQUARE_SAR",
            "message": f"Non-square sample aspect ratio detected: {sar}.",
            "fix": "Normalize clip chains with setsar=1 before concat/xfade.",
        })
        score -= 10

    # Expected duration drift
    if expected_duration is not None and fmt_dur > 0:
        drift = abs(fmt_dur - float(expected_duration))
        if drift > duration_tolerance:
            issues.append({
                "severity": "error" if drift > max(2.0, duration_tolerance * 3) else "warning",
                "code": "DURATION_DRIFT",
                "message": f"Duration {fmt_dur:.2f}s differs from expected {expected_duration:.2f}s by {drift:.2f}s.",
                "fix": "Check xfade overlap math or pass explicit trim_out / beat durations.",
            })
            score -= 20 if drift > 2.0 else 10

    # A/V sync drift
    av_drift_ms = None
    if vstream is not None and astream is not None and v_dur > 0 and a_dur > 0:
        av_drift_ms = round(abs(v_dur - a_dur) * 1000.0, 1)
        if av_drift_ms > max_av_drift_ms:
            issues.append({
                "severity": "error" if av_drift_ms > 500.0 else "warning",
                "code": "AV_SYNC_DRIFT",
                "message": f"Video ({v_dur:.3f}s) and audio ({a_dur:.3f}s) differ by {av_drift_ms:.1f}ms.",
                "fix": "Use concat filter (not concat demuxer) and verify audio padding/trimming.",
            })
            score -= 20 if av_drift_ms > 500.0 else 10

    analysis = _run_ffmpeg_analysis(
        video_path,
        has_video=vstream is not None,
        has_audio=astream is not None,
        check_black=check_black,
        check_silence=check_silence,
        silence_min_dur=silence_min_dur,
        timeout=timeout,
    )

    # Black frames
    black_segs = analysis["black_segments"]
    if black_segs:
        total_black = round(sum(b["duration"] for b in black_segs), 2)
        issues.append({
            "severity": "error" if total_black > max(1.5, fmt_dur * 0.2) else "warning",
            "code": "BLACK_FRAMES_DETECTED",
            "message": f"Detected {len(black_segs)} black segment(s) totaling {total_black}s.",
            "timestamp": black_segs[0]["start"],
            "fix": "Verify clip src paths, start offsets, and xfade offsets to remove black gaps.",
        })
        score -= min(25, 8 * len(black_segs))

    # Audio loudness & clipping
    max_vol = analysis["max_volume_db"]
    mean_vol = analysis["mean_volume_db"]
    if max_vol is not None and max_vol >= -0.2:
        issues.append({
            "severity": "warning",
            "code": "AUDIO_CLIPPING",
            "message": f"Audio peak reaches {max_vol:.1f} dB (clipping risk).",
            "fix": "Enable master_loudnorm=True with loudnorm_i=-14.0 or lower narration/music volume.",
        })
        score -= 10
    if mean_vol is not None and mean_vol < -32.0:
        issues.append({
            "severity": "warning",
            "code": "AUDIO_TOO_QUIET",
            "message": f"Mean audio level is very quiet ({mean_vol:.1f} dB).",
            "fix": "Enable master_loudnorm=True or boost voice track volume.",
        })
        score -= 10

    # Long silence gaps
    silence_segs = analysis["silence_segments"]
    if silence_segs:
        longest = max(s["duration"] for s in silence_segs)
        issues.append({
            "severity": "warning",
            "code": "SILENCE_GAPS",
            "message": f"Found {len(silence_segs)} silence gap(s) >= {silence_min_dur}s (longest {longest:.2f}s).",
            "timestamp": silence_segs[0]["start"],
            "fix": "Trim dead air with smart_cut_silence or check narration alignment.",
        })
        score -= min(15, 5 * len(silence_segs))

    score = max(0, min(100, score))
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    has_errors = any(i["severity"] == "error" for i in issues)
    return {
        "passed": (not has_errors) and score >= 75,
        "score": score,
        "grade": grade,
        "video_path": video_path,
        "issues": issues,
        "metrics": {
            "file_size_bytes": file_size,
            "duration": round(fmt_dur, 3),
            "video_duration": round(v_dur, 3) if vstream else None,
            "audio_duration": round(a_dur, 3) if astream else None,
            "av_drift_ms": av_drift_ms,
            "width": vstream.get("width") if vstream else None,
            "height": vstream.get("height") if vstream else None,
            "fps": _parse_fps(vstream.get("r_frame_rate")) if vstream else None,
            "sar": sar,
            "video_codec": vstream.get("codec_name") if vstream else None,
            "audio_codec": astream.get("codec_name") if astream else None,
            "mean_volume_db": mean_vol,
            "max_volume_db": max_vol,
            "black_segments": black_segs,
            "silence_segments": silence_segs,
        },
    }


def audit_editorial_craft(brief: dict[str, Any]) -> dict[str, Any]:
    """Audit an explainer/editorial brief for robotic patterns BEFORE rendering (v2.5.0).

    Scores editorial human-feel (0-100) across 5 craft pillars:
      1. Rhythm variance (flags metronomic identical beat durations or holds > 6s).
      2. Camera diversity (flags consecutive identical camera moves).
      3. Transition variety (flags 100% identical transitions across >= 3 scenes).
      4. HUD restraint (flags highlight_card spam on back-to-back scenes).
      5. Cinema & soundscape craft (checks cinema_look + room_tone / SFX cues).
    """
    if not isinstance(brief, dict):
        return {"passed": False, "score": 0, "grade": "F", "issues": [{"code": "INVALID_BRIEF", "message": "Brief must be a dict", "fix": "Pass a valid brief JSON."}]}

    scenes = brief.get("scenes") or []
    if not scenes:
        return {"passed": False, "score": 0, "grade": "F", "issues": [{"code": "EMPTY_SCENES", "message": "Brief has no scenes.", "fix": "Add scenes to the brief."}]}

    score = 100
    issues: list[dict[str, Any]] = []

    beat_durs: list[float] = []
    cam_moves: list[str] = []
    transitions: list[str] = []
    card_indices: list[int] = []

    for si, sc in enumerate(scenes):
        if si > 0:
            transitions.append(str(sc.get("transition", "none")))
        if sc.get("highlight_card"):
            card_indices.append(si)
        beats = sc.get("beats")
        if isinstance(beats, list) and beats:
            for b in beats:
                bd = float(b.get("end", 0)) - float(b.get("start", 0))
                if bd > 0:
                    beat_durs.append(round(bd, 2))
                if b.get("camera_move"):
                    cam_moves.append(str(b["camera_move"]))
        else:
            if sc.get("duration"):
                beat_durs.append(round(float(sc["duration"]), 2))
            if sc.get("camera_move"):
                cam_moves.append(str(sc["camera_move"]))

    # 1. Rhythm variance
    if len(beat_durs) >= 3:
        spread = max(beat_durs) - min(beat_durs)
        if spread < 0.25:
            score -= 15
            issues.append({
                "severity": "warning",
                "code": "METRONOMIC_RHYTHM",
                "message": f"All {len(beat_durs)} beats have nearly identical duration (spread {spread:.2f}s).",
                "fix": "Run humanize_brief() or vary beat lengths (1.9-2.8s hooks, 3.5-5.0s reflections).",
            })
        long_holds = [d for d in beat_durs if d > 6.5]
        if long_holds:
            score -= min(15, 5 * len(long_holds))
            issues.append({
                "severity": "warning",
                "code": "OVERLONG_STATIC_HOLD",
                "message": f"Found {len(long_holds)} beat(s) longer than 6.5s (max {max(long_holds):.1f}s).",
                "fix": "Split long scenes with auto_split_beats() into 2.5-4.0s visual beats.",
            })

    # 2. Camera diversity
    consec_dups = sum(1 for i in range(1, len(cam_moves)) if cam_moves[i] == cam_moves[i - 1])
    if consec_dups > 0:
        score -= min(20, 8 * consec_dups)
        issues.append({
            "severity": "warning",
            "code": "REPETITIVE_CAMERA_MOVES",
            "message": f"Found {consec_dups} consecutive identical camera move(s).",
            "fix": "Use humanize_brief() / choose_narrative_camera_move() so consecutive beats never repeat.",
        })

    # 3. Transition variety
    if len(transitions) >= 3 and len(set(transitions)) == 1:
        score -= 10
        issues.append({
            "severity": "warning",
            "code": "UNIFORM_TRANSITIONS",
            "message": f"Every scene cut uses the exact same transition ({transitions[0]!r}).",
            "fix": "Mix hard cuts on punchy questions/revelations with soft crossfades on narrative shifts.",
        })

    # 4. HUD restraint
    back_to_back_cards = sum(1 for i in range(1, len(card_indices)) if card_indices[i] - card_indices[i - 1] == 1)
    if back_to_back_cards > 0:
        score -= min(15, 7 * back_to_back_cards)
        issues.append({
            "severity": "warning",
            "code": "HUD_CARD_SPAM",
            "message": f"Found {back_to_back_cards} back-to-back highlight_card(s).",
            "fix": "Space highlight_card overlays at least 2 scenes apart for a clean editorial feel.",
        })

    # 5. Cinema look & soundscape
    if not brief.get("cinema_look"):
        score -= 10
        issues.append({
            "severity": "info",
            "code": "NO_CINEMA_LOOK",
            "message": "No cohesive cinema_look color grade is set.",
            "fix": "Set cinema_look to 'chiaroscuro_doc', 'kodak_warm', or 'noir_slate'.",
        })
    if not brief.get("soundscape") and not brief.get("music"):
        score -= 10
        issues.append({
            "severity": "info",
            "code": "FLAT_SOUNDSCAPE",
            "message": "No soundscape (room_tone/SFX) or music bed attached.",
            "fix": "Run humanize_brief(brief, add_soundscape=True) to add subtle warm room tone and editorial SFX cues.",
        })

    score = max(0, min(100, score))
    grade = "A" if score >= 90 else ("B" if score >= 80 else ("C" if score >= 70 else ("D" if score >= 60 else "F")))
    return {
        "passed": score >= 75,
        "score": score,
        "grade": grade,
        "issues": issues,
        "stats": {
            "scene_count": len(scenes),
            "beat_count": len(beat_durs),
            "unique_camera_moves": len(set(cam_moves)),
            "unique_transitions": len(set(transitions)),
            "highlight_cards": len(card_indices),
            "cinema_look": brief.get("cinema_look"),
            "has_soundscape": bool(brief.get("soundscape")),
        },
    }
