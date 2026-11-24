"""Acoustic Beat-Sync Engine for AgentCut (v3.4.1).

Analyzes real narration audio files (WAV via pure Python `wave` module or
other formats via `ffmpeg` PCM extraction) to detect:
- RMS energy envelope (dBFS over short analysis windows)
- Acoustic energy peaks (syllable / emphasis hits with local prominence)
- Natural speech pauses (breath gaps where energy drops below floor)
- Anchor timestamps combining word timings (Whisper / Deepgram) with
  detected acoustic peaks so visual entrances, camera micro-pulses, and
  procedural SFX land on the exact millisecond of vocal emphasis.

Strictly zero external Python dependencies (stdlib `wave`, `struct`, `math`
only) and deterministic output for `window.setTime(t)`.
"""
from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import tempfile
import wave
from typing import Any

_AR_NUM_RE = re.compile(r"[0-9\u0660-\u0669]+|[%٪×]|[؟!]")


def _read_mono_pcm16(audio_path: str, target_rate: int = 16000) -> tuple[list[int], int]:
    """Read mono 16-bit signed PCM samples from a WAV file (pure Python fast path)
    or decode via FFmpeg when non-PCM/compressed."""
    if not os.path.isfile(audio_path):
        return [], target_rate
    try:
        with wave.open(audio_path, "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            rate = wf.getframerate()
            n_frames = wf.getnframes()
            if sw == 2 and n_frames > 0:
                raw = wf.readframes(n_frames)
                total_samples = len(raw) // 2
                ints = struct.unpack(f"<{total_samples}h", raw[: total_samples * 2])
                if n_ch > 1:
                    mono = [
                        int(sum(ints[i : i + n_ch]) / n_ch)
                        for i in range(0, len(ints), n_ch)
                    ]
                else:
                    mono = list(ints)
                return mono, rate
    except (wave.Error, EOFError, struct.error, OSError):
        pass

    # Fallback: decode via ffmpeg to temporary 16kHz mono PCM WAV
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-i", audio_path, "-ac", "1", "-ar", str(target_rate),
                "-c:a", "pcm_s16le", tmp_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0 and os.path.isfile(tmp_path):
            with wave.open(tmp_path, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                n = len(raw) // 2
                return list(struct.unpack(f"<{n}h", raw[: n * 2])), wf.getframerate()
    except (OSError, subprocess.SubprocessError, wave.Error):
        pass
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return [], target_rate


def compute_rms_envelope(
    audio_path: str,
    window_ms: int = 25,
) -> tuple[list[dict[str, float]], float]:
    """Compute time-bucketed RMS energy envelope [{t, rms, db}] and duration.

    Each bucket covers `window_ms` milliseconds (default 25ms = 40 Hz
    resolution, finer than 30fps frame intervals).
    """
    samples, rate = _read_mono_pcm16(audio_path)
    if not samples or rate <= 0:
        return [], 0.0
    win = max(1, int(round(rate * (window_ms / 1000.0))))
    envelope: list[dict[str, float]] = []
    for idx in range(0, len(samples), win):
        chunk = samples[idx : idx + win]
        if not chunk:
            continue
        sq = sum(s * s for s in chunk) / len(chunk)
        rms = math.sqrt(sq) / 32768.0
        db = 20.0 * math.log10(max(rms, 1e-6))
        t_mid = (idx + 0.5 * len(chunk)) / rate
        envelope.append({
            "t": round(t_mid, 4),
            "rms": round(rms, 5),
            "db": round(db, 2),
        })
    duration = round(len(samples) / rate, 4)
    return envelope, duration


def detect_energy_peaks(
    envelope: list[dict[str, float]],
    min_spacing_sec: float = 0.22,
    prominence_ratio: float = 1.18,
    max_peaks: int = 24,
) -> list[dict[str, float]]:
    """Detect vocal emphasis peaks from an RMS envelope.

    Returns [{t, intensity, rms, db}] sorted by time `t`, where
    `intensity` is normalized in [0.0, 1.0].
    """
    if not envelope:
        return []
    rms_vals = [b["rms"] for b in envelope]
    max_rms = max(rms_vals)
    if max_rms <= 1e-4:
        return []
    mean_rms = sum(rms_vals) / len(rms_vals)
    floor_rms = max(mean_rms * prominence_ratio, max_rms * 0.25)

    candidates: list[dict[str, float]] = []
    n = len(envelope)
    for i, b in enumerate(envelope):
        val = b["rms"]
        if val < floor_rms:
            continue
        left = envelope[i - 1]["rms"] if i > 0 else 0.0
        right = envelope[i + 1]["rms"] if i + 1 < n else 0.0
        if val >= left and val >= right and (val > left or val > right):
            # Local neighborhood average (approx +-150ms)
            lo = max(0, i - 6)
            hi = min(n, i + 7)
            local_mean = sum(envelope[j]["rms"] for j in range(lo, hi)) / max(1, hi - lo)
            if val >= local_mean * 1.08:
                candidates.append({
                    "t": b["t"],
                    "rms": val,
                    "db": b["db"],
                    "intensity": round(min(1.0, val / max_rms), 3),
                })

    # Greedy non-maximum suppression by min_spacing_sec
    by_strength = sorted(candidates, key=lambda c: c["rms"], reverse=True)
    selected: list[dict[str, float]] = []
    for cand in by_strength:
        if len(selected) >= max_peaks:
            break
        if all(abs(cand["t"] - s["t"]) >= min_spacing_sec for s in selected):
            selected.append(cand)
    selected.sort(key=lambda c: c["t"])
    return selected


def detect_natural_pauses(
    envelope: list[dict[str, float]],
    min_pause_sec: float = 0.14,
    silence_ratio: float = 0.22,
) -> list[dict[str, float]]:
    """Detect natural breath pauses [{start, end, duration}] in narration audio."""
    if not envelope:
        return []
    rms_vals = [b["rms"] for b in envelope]
    max_rms = max(rms_vals)
    if max_rms <= 1e-4:
        return []
    mean_rms = sum(rms_vals) / len(rms_vals)
    thresh = min(mean_rms * 0.45, max_rms * silence_ratio)
    step = envelope[1]["t"] - envelope[0]["t"] if len(envelope) > 1 else 0.025

    pauses: list[dict[str, float]] = []
    run_start: float | None = None
    last_t = 0.0
    for b in envelope:
        t = b["t"]
        if b["rms"] <= thresh:
            if run_start is None:
                run_start = max(0.0, round(t - 0.5 * step, 3))
            last_t = round(t + 0.5 * step, 3)
        else:
            if run_start is not None:
                dur = round(last_t - run_start, 3)
                if dur >= min_pause_sec:
                    pauses.append({
                        "start": run_start,
                        "end": last_t,
                        "duration": dur,
                    })
                run_start = None
    if run_start is not None:
        dur = round(last_t - run_start, 3)
        if dur >= min_pause_sec:
            pauses.append({"start": run_start, "end": last_t, "duration": dur})
    return pauses


def analyze_audio_beats(
    audio_path: str,
    window_ms: int = 25,
    min_peak_spacing: float = 0.22,
    min_pause_sec: float = 0.14,
) -> dict[str, Any]:
    """Full acoustic analysis of a single narration audio file.

    Returns {duration, mean_db, peak_db, peaks, pauses, dynamic_range_db}.
    """
    envelope, dur = compute_rms_envelope(audio_path, window_ms=window_ms)
    if not envelope:
        return {
            "duration": 0.0,
            "mean_db": -96.0,
            "peak_db": -96.0,
            "dynamic_range_db": 0.0,
            "peaks": [],
            "pauses": [],
        }
    peaks = detect_energy_peaks(envelope, min_spacing_sec=min_peak_spacing)
    pauses = detect_natural_pauses(envelope, min_pause_sec=min_pause_sec)
    dbs = [b["db"] for b in envelope]
    peak_db = round(max(dbs), 2)
    mean_db = round(sum(dbs) / len(dbs), 2)
    active_dbs = [d for d in dbs if d > -55.0] or dbs
    dyn_range = round(max(active_dbs) - min(active_dbs), 2)
    return {
        "duration": dur,
        "mean_db": mean_db,
        "peak_db": peak_db,
        "dynamic_range_db": dyn_range,
        "peaks": peaks,
        "pauses": pauses,
    }


def _find_keyword_anchors(
    words: list[dict[str, Any]],
    scene_start: float,
    explicit_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Find emphasis word timestamps (numbers, question words, explicit keywords)
    relative to `scene_start`."""
    kw_set = {k.strip() for k in (explicit_keywords or []) if k and str(k).strip()}
    anchors: list[dict[str, Any]] = []
    for w in words:
        token = str(w.get("word", "")).strip()
        clean = token.strip(".,،؛:!؟\"'()[]")
        rel_t = round(max(0.0, float(w.get("start", scene_start)) - scene_start), 3)
        is_kw = clean in kw_set or token in kw_set
        is_num_or_q = bool(_AR_NUM_RE.search(token))
        if is_kw or is_num_or_q:
            anchors.append({
                "word": token,
                "t": rel_t,
                "end": round(max(rel_t, float(w.get("end", scene_start)) - scene_start), 3),
                "kind": "keyword" if is_kw else "emphasis",
            })
    return anchors


def align_visual_anchors_to_beats(
    scene: dict[str, Any],
    timing: dict[str, Any],
    beats: dict[str, Any],
    n_elements: int,
) -> list[float]:
    """Compute `n_elements` deterministic entrance delays (in seconds, relative
    to scene start) snapped to real vocal energy peaks, pause boundaries, or
    keyword timings instead of rigid constant offsets.
    """
    if n_elements <= 0:
        return []
    dur = max(0.4, float(timing.get("duration", 2.0)))
    scene_start = float(timing.get("start", 0.0))
    words = timing.get("words") or []
    peaks = beats.get("peaks") or []
    pauses = beats.get("pauses") or []
    kw_anchors = _find_keyword_anchors(
        words, scene_start, explicit_keywords=scene.get("keywords")
    )

    # Candidate anchor times inside [0.08, 0.78 * dur]
    max_delay = max(0.25, min(dur * 0.78, dur - 0.35))
    cand_times: list[float] = []
    for p in pauses:
        # Just after a natural breath pause is an ideal editorial reveal point
        t_after = round(float(p["end"]) + 0.02, 3)
        if 0.12 <= t_after <= max_delay:
            cand_times.append(t_after)
    for kw in kw_anchors:
        if 0.08 <= kw["t"] <= max_delay:
            cand_times.append(round(float(kw["t"]), 3))
    for pk in peaks:
        if 0.08 <= pk["t"] <= max_delay:
            cand_times.append(round(float(pk["t"]), 3))

    cand_times = sorted(set(cand_times))

    delays: list[float] = []
    for idx in range(n_elements):
        if idx == 0:
            delays.append(0.10)
            continue
        target = min(max_delay, 0.14 + 0.22 * idx)
        # Snap to nearest acoustic candidate within +-0.14s that is > previous delay + 0.10s
        prev = delays[-1]
        best = None
        best_dist = 0.15
        for ct in cand_times:
            if ct <= prev + 0.10:
                continue
            dist = abs(ct - target)
            if dist < best_dist:
                best = ct
                best_dist = dist
        chosen = best if best is not None else max(prev + 0.12, target)
        delays.append(round(min(max_delay, chosen), 3))
    return delays


def build_scene_beat_profile(
    spec: dict[str, Any],
    timings: list[dict[str, Any]],
    base_dir: str = ".",
) -> dict[str, dict[str, Any]]:
    """Build per-scene acoustic beat profiles keyed by scene `id`.

    For each scene, returns:
      - `peaks`: [{t, intensity}] (relative to scene start, for setTime camera/icon pulse)
      - `pauses`: [{start, end, duration}] (relative to scene start)
      - `anchors`: [{word, t, kind}] (relative to scene start)
      - `element_delays`: list[float] (beat-snapped entrance delays for steps/items/bars)
      - `emphasis_sfx`: list[{kind, start, volume}] (absolute timeline timestamps for micro-accents)
    """
    scenes_by_id = {sc["id"]: sc for sc in spec.get("scenes", [])}
    profiles: dict[str, dict[str, Any]] = {}

    for tm in timings:
        sid = tm["id"]
        sc = scenes_by_id.get(sid, {})
        narration_rel = sc.get("narration", "")
        narration_path = os.path.join(base_dir, narration_rel) if narration_rel else ""
        beats = analyze_audio_beats(narration_path)
        sc_start = float(tm.get("start", 0.0))
        words = tm.get("words") or []
        anchors = _find_keyword_anchors(
            words, sc_start, explicit_keywords=sc.get("keywords")
        )

        # Count sequential visual items in this scene
        items = (
            sc.get("steps")
            or sc.get("items")
            or sc.get("layers")
            or sc.get("nodes")
            or sc.get("panels")
            or sc.get("bullets")
            or sc.get("bars")
            or []
        )
        n_items = max(len(items), 4)
        delays = align_visual_anchors_to_beats(sc, tm, beats, n_items)

        # Compact peaks payload for browser JS setTime(t)
        js_peaks = [
            {"t": round(float(p["t"]), 3), "i": round(float(p["intensity"]), 3)}
            for p in (beats.get("peaks") or [])[:12]
        ]
        # Snap keyword anchors to nearest peak if within 0.12s for extra punch
        for anc in anchors:
            if not any(abs(p["t"] - anc["t"]) < 0.09 for p in js_peaks):
                js_peaks.append({"t": round(float(anc["t"]), 3), "i": 0.92})
        js_peaks.sort(key=lambda x: x["t"])

        # Optional micro-SFX accents on top vocal/keyword peak inside the scene
        emphasis_sfx: list[dict[str, Any]] = []
        if spec.get("beat_sync_sfx") and js_peaks:
            strongest = max(js_peaks, key=lambda p: p["i"])
            if strongest["t"] >= 0.22 and strongest["i"] >= 0.75:
                emphasis_sfx.append({
                    "scene_id": sid,
                    "kind": "pop_in" if sc.get("type") == "question" else "card_tick",
                    "start": round(sc_start + strongest["t"], 3),
                    "volume": 0.42,
                })

        profiles[sid] = {
            "duration": beats["duration"] or tm.get("duration", 0.0),
            "mean_db": beats["mean_db"],
            "peak_db": beats["peak_db"],
            "dynamic_range_db": beats["dynamic_range_db"],
            "peaks": js_peaks,
            "pauses": beats["pauses"],
            "anchors": anchors,
            "element_delays": delays,
            "emphasis_sfx": emphasis_sfx,
        }
    return profiles
