"""Character-level LCS word-timing alignment & optional Deepgram bridge.

Zero external Python dependencies (Python stdlib only: difflib, urllib, json).
Whisper remains the free, default transcription provider. When Whisper or
optional Deepgram word timings are supplied alongside an author's Arabic or
English script, this module aligns timestamps at the character level using
Longest Common Subsequence (LCS) so that:

1. Original script spelling, hamza/tashkeel forms, and word boundaries are
   100% preserved in the final ``word_timings`` list.
2. Unmatched or skipped words are smoothly interpolated between neighboring
   anchored timestamps and strictly clamped/ordered for ``render.py``.
3. Uncertain or weakly matched words are flagged with ``low_confidence=True``
   and surfaced in ``low_confidence_words`` for quick review.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Arabic diacritics (tashkeel), superscript alef, and tatweel
_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
# Punctuation stripped during character-level comparison (original tokens remain untouched)
_PUNCT_RE = re.compile(r"[\W_\u060C\u061B\u061F\u06D4]+", re.UNICODE)

_ARABIC_CHAR_MAP = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
    "ؤ": "و",
    "ئ": "ي",
})

_AUDIO_MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}


def normalize_for_alignment(text: str) -> str:
    """Normalize Arabic/Latin text for character-level LCS comparison.

    Strips tashkeel/diacritics, tatweel, and punctuation, normalizes Alef,
    Teh Marbuta, and Alef Maksura variants, and lowercases Latin letters.
    """
    if not isinstance(text, str):
        return ""
    cleaned = _ARABIC_DIACRITICS_RE.sub("", text)
    cleaned = cleaned.translate(_ARABIC_CHAR_MAP)
    cleaned = _PUNCT_RE.sub("", cleaned)
    return cleaned.lower()


def _char_lcs_pairs(a: str, b: str) -> list[tuple[int, int]]:
    """Return ordered ``(i, j)`` character index pairs of the LCS of ``a`` and ``b``.

    Uses exact O(N*M) dynamic programming when ``len(a) * len(b) <= 300_000``
    and falls back to C-accelerated ``difflib.SequenceMatcher(autojunk=False)``
    for very long inputs.
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []

    if n * m <= 300_000:
        # Standard bottom-up LCS length table
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            ai = a[i]
            row_i = dp[i]
            row_next = dp[i + 1]
            for j in range(m - 1, -1, -1):
                if ai == b[j]:
                    row_i[j] = 1 + row_next[j + 1]
                else:
                    v1 = row_next[j]
                    v2 = row_i[j + 1]
                    row_i[j] = v1 if v1 >= v2 else v2

        pairs: list[tuple[int, int]] = []
        i, j = 0, 0
        while i < n and j < m:
            if a[i] == b[j]:
                pairs.append((i, j))
                i += 1
                j += 1
            elif dp[i + 1][j] >= dp[i][j + 1]:
                i += 1
            else:
                j += 1
        return pairs

    # Fast block fallback for huge strings
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs = []
    for block in sm.get_matching_blocks():
        for offset in range(block.size):
            pairs.append((block.a + offset, block.b + offset))
    return pairs


def align_script_with_timings(
    script_text: str,
    asr_words: list[dict[str, Any]] | None,
    low_confidence_threshold: float = 0.72,
    audio_duration: float | None = None,
) -> dict[str, Any]:
    """Align ASR word timings to the author's original script via character-level LCS.

    Parameters
    ----------
    script_text:
        Original Arabic/English/mixed script text. Exact spelling and tokens
        are preserved verbatim in the output ``word_timings``.
    asr_words:
        List of ``{"word": str, "start": float, "end": float, "confidence"?: float}``
        entries from Whisper or Deepgram.
    low_confidence_threshold:
        Confidence floor below which aligned words are flagged ``low_confidence=True``.
    audio_duration:
        Optional total audio duration (seconds) used to clamp/interpolate tail words.
    """
    raw_tokens = [t for t in str(script_text or "").split() if t.strip()]
    if not raw_tokens:
        return {
            "word_timings": [],
            "low_confidence_words": [],
            "stats": {
                "total_words": 0,
                "matched_words": 0,
                "interpolated_words": 0,
                "low_confidence_count": 0,
                "mean_match_ratio": 0.0,
                "spelling_preserved": True,
            },
        }

    # Normalize ASR words safely
    clean_asr: list[dict[str, Any]] = []
    for item in (asr_words or []):
        if not isinstance(item, dict):
            continue
        w_text = str(item.get("punctuated_word") or item.get("word") or "").strip()
        if not w_text:
            continue
        try:
            ws = max(0.0, float(item["start"]))
            we = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if we <= ws:
            we = ws + 0.05
        conf_raw = item.get("confidence")
        conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else 1.0
        conf = max(0.0, min(1.0, conf))
        clean_asr.append({
            "word": w_text,
            "norm": normalize_for_alignment(w_text),
            "start": ws,
            "end": we,
            "confidence": conf,
        })
    clean_asr.sort(key=lambda x: (x["start"], x["end"]))

    # Determine effective total duration
    inferred_end = max((w["end"] for w in clean_asr), default=max(0.5, len(raw_tokens) * 0.35))
    total_dur = float(audio_duration) if audio_duration is not None and float(audio_duration) > 0 else inferred_end
    total_dur = max(total_dur, 0.2)

    # Build character streams with token index mappings
    norm_script_chars: list[str] = []
    char_to_script_idx: list[int] = []
    script_norm_lens: list[int] = []
    for s_idx, tok in enumerate(raw_tokens):
        nt = normalize_for_alignment(tok)
        script_norm_lens.append(len(nt))
        for ch in nt:
            norm_script_chars.append(ch)
            char_to_script_idx.append(s_idx)

    norm_asr_chars: list[str] = []
    char_to_asr_idx: list[int] = []
    for a_idx, aw in enumerate(clean_asr):
        na = aw["norm"]
        for ch in na:
            norm_asr_chars.append(ch)
            char_to_asr_idx.append(a_idx)

    norm_script = "".join(norm_script_chars)
    norm_asr = "".join(norm_asr_chars)
    lcs_pairs = _char_lcs_pairs(norm_script, norm_asr)

    # Map each script token -> matched ASR token hit counts
    script_hits: list[dict[int, int]] = [{} for _ in raw_tokens]
    asr_to_script_hits: list[dict[int, int]] = [{} for _ in clean_asr]
    for si, aj in lcs_pairs:
        s_tok = char_to_script_idx[si]
        a_tok = char_to_asr_idx[aj]
        script_hits[s_tok][a_tok] = script_hits[s_tok].get(a_tok, 0) + 1
        asr_to_script_hits[a_tok][s_tok] = asr_to_script_hits[a_tok].get(s_tok, 0) + 1

    # Initial anchored timings per script token
    anchors: list[dict[str, Any] | None] = [None] * len(raw_tokens)
    for s_idx, hits in enumerate(script_hits):
        norm_len = script_norm_lens[s_idx]
        if not hits or norm_len == 0:
            continue
        matched_chars = sum(hits.values())
        match_ratio = min(1.0, matched_chars / max(1, norm_len))
        # Require at least 1 character and reasonable overlap (>= 0.25 or single-char word)
        if match_ratio < 0.25 and norm_len > 2:
            continue

        matched_asr_indices = sorted(hits.keys())
        first_a = matched_asr_indices[0]
        last_a = matched_asr_indices[-1]

        # If a single ASR word spans multiple script tokens (merged transcription),
        # subdivide its [start, end] proportionally by character count.
        if first_a == last_a and len(asr_to_script_hits[first_a]) > 1:
            shared_scripts = sorted(asr_to_script_hits[first_a].keys())
            total_shared_chars = sum(max(1, script_norm_lens[k]) for k in shared_scripts)
            aw = clean_asr[first_a]
            span = max(0.05, aw["end"] - aw["start"])
            cursor_t = aw["start"]
            t_start = aw["start"]
            t_end = aw["end"]
            for k in shared_scripts:
                frac = max(1, script_norm_lens[k]) / max(1, total_shared_chars)
                seg_dur = span * frac
                if k == s_idx:
                    t_start = cursor_t
                    t_end = cursor_t + seg_dur
                    break
                cursor_t += seg_dur
        else:
            t_start = clean_asr[first_a]["start"]
            t_end = clean_asr[last_a]["end"]

        weighted_conf = sum(
            clean_asr[a_idx]["confidence"] * cnt for a_idx, cnt in hits.items()
        ) / max(1, matched_chars)

        anchors[s_idx] = {
            "start": float(t_start),
            "end": float(t_end),
            "confidence": float(weighted_conf),
            "match_ratio": float(match_ratio),
            "interpolated": False,
        }

    # Fill unmatched tokens via linear interpolation between nearest anchors
    resolved: list[dict[str, Any]] = []
    n_tok = len(raw_tokens)
    idx = 0
    while idx < n_tok:
        if anchors[idx] is not None:
            a = anchors[idx]
            resolved.append(dict(a))  # type: ignore[arg-type]
            idx += 1
            continue

        # Contiguous run of unmatched tokens [idx .. run_end - 1]
        run_end = idx
        while run_end < n_tok and anchors[run_end] is None:
            run_end += 1

        prev_end = resolved[-1]["end"] if resolved else 0.0
        next_start = anchors[run_end]["start"] if run_end < n_tok and anchors[run_end] is not None else total_dur
        if next_start <= prev_end:
            next_start = min(total_dur, prev_end + 0.25 * (run_end - idx))
            if next_start <= prev_end:
                next_start = prev_end + 0.08 * (run_end - idx)

        count = run_end - idx
        char_weights = [max(1, script_norm_lens[k]) for k in range(idx, run_end)]
        weight_sum = sum(char_weights)
        span = max(0.04 * count, next_start - prev_end)
        cursor_t = prev_end
        for offset in range(count):
            seg = span * (char_weights[offset] / max(1, weight_sum))
            resolved.append({
                "start": cursor_t,
                "end": cursor_t + seg,
                "confidence": 0.0 if not clean_asr else 0.35,
                "match_ratio": 0.0,
                "interpolated": True,
            })
            cursor_t += seg
        idx = run_end

    # Enforce strict monotonic non-overlapping timestamps inside [0, total_dur]
    word_timings: list[dict[str, Any]] = []
    low_confidence_words: list[dict[str, Any]] = []
    prev_end_t = 0.0
    matched_count = 0
    interpolated_count = 0
    match_ratio_sum = 0.0

    for s_idx, tok in enumerate(raw_tokens):
        r = resolved[s_idx]
        ws = max(prev_end_t, float(r["start"]))
        we = max(ws + 0.04, float(r["end"]))
        if audio_duration is not None and float(audio_duration) > 0:
            cap = float(audio_duration)
            if ws >= cap:
                ws = max(prev_end_t, cap - 0.04 * (n_tok - s_idx))
            we = min(cap, max(ws + 0.02, we))
            if we <= ws:
                we = ws + 0.02

        prev_end_t = we
        m_ratio = round(float(r["match_ratio"]), 3)
        conf = round(float(r["confidence"]), 3)
        interp = bool(r["interpolated"])
        is_low = interp or (m_ratio < 0.55) or (conf < float(low_confidence_threshold))

        if interp:
            interpolated_count += 1
        else:
            matched_count += 1
        match_ratio_sum += m_ratio

        entry = {
            "word": tok,
            "start": round(ws, 3),
            "end": round(we, 3),
            "confidence": conf,
            "match_ratio": m_ratio,
            "low_confidence": is_low,
            "interpolated": interp,
        }
        word_timings.append(entry)

        if is_low:
            reasons: list[str] = []
            if interp:
                reasons.append("unmatched_interpolated")
            elif m_ratio < 0.55:
                reasons.append("partial_char_match")
            if conf < float(low_confidence_threshold):
                reasons.append("low_asr_confidence")
            low_confidence_words.append({
                "index": s_idx,
                "word": tok,
                "start": entry["start"],
                "end": entry["end"],
                "confidence": conf,
                "match_ratio": m_ratio,
                "reasons": reasons,
            })

    return {
        "word_timings": word_timings,
        "low_confidence_words": low_confidence_words,
        "stats": {
            "total_words": n_tok,
            "matched_words": matched_count,
            "interpolated_words": interpolated_count,
            "low_confidence_count": len(low_confidence_words),
            "mean_match_ratio": round(match_ratio_sum / max(1, n_tok), 3),
            "spelling_preserved": True,
        },
    }


def parse_deepgram_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract normalized word timings and transcript from a Deepgram /v1/listen JSON response."""
    if not isinstance(payload, dict):
        return {"transcript": "", "words": [], "duration": None}

    results = payload.get("results") or {}
    channels = results.get("channels") or []
    alt: dict[str, Any] = {}
    if isinstance(channels, list) and channels and isinstance(channels[0], dict):
        alts = channels[0].get("alternatives") or []
        if isinstance(alts, list) and alts and isinstance(alts[0], dict):
            alt = alts[0]

    words_out: list[dict[str, Any]] = []
    for w in (alt.get("words") or []):
        if not isinstance(w, dict):
            continue
        word_str = str(w.get("punctuated_word") or w.get("word") or "").strip()
        if not word_str:
            continue
        try:
            ws = float(w["start"])
            we = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        conf = float(w.get("confidence", 1.0)) if isinstance(w.get("confidence"), (int, float)) else 1.0
        words_out.append({
            "word": word_str,
            "start": round(max(0.0, ws), 3),
            "end": round(max(ws + 0.02, we), 3),
            "confidence": round(max(0.0, min(1.0, conf)), 3),
        })

    metadata = payload.get("metadata") or {}
    dur = metadata.get("duration") if isinstance(metadata, dict) else None
    return {
        "transcript": str(alt.get("transcript") or "").strip(),
        "words": words_out,
        "duration": float(dur) if isinstance(dur, (int, float)) else None,
    }


def transcribe_audio_deepgram(
    audio_path: str,
    api_key: str | None = None,
    language: str = "ar",
    model: str = "nova-2",
    timeout: float = 60.0,
    endpoint: str = "https://api.deepgram.com/v1/listen",
) -> dict[str, Any]:
    """Transcribe an audio file using Deepgram's REST API via Python stdlib ``urllib`` only.

    This is strictly optional; Whisper remains AgentCut's free default.
    """
    key = (api_key or os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "DEEPGRAM_API_KEY is required when provider='deepgram'. "
            "Leave provider='whisper' (the free default) or pass api_key."
        )
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path!r}")

    ext = os.path.splitext(audio_path)[1].lower()
    content_type = _AUDIO_MIME_TYPES.get(ext, "application/octet-stream")
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    params = urllib.parse.urlencode({
        "model": model,
        "language": language,
        "punctuate": "true",
        "smart_format": "true",
    })
    url = f"{endpoint}?{params}"
    req = urllib.request.Request(
        url,
        data=audio_bytes,
        method="POST",
        headers={
            "Authorization": f"Token {key}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Deepgram HTTP {exc.code}: {err_detail}") from exc

    payload = json.loads(raw_body)
    parsed = parse_deepgram_response(payload)
    return {
        "provider": "deepgram",
        "model": model,
        "language": language,
        "transcript": parsed["transcript"],
        "words": parsed["words"],
        "duration": parsed["duration"],
    }


def align_scene_audio(
    script_text: str,
    asr_words: list[dict[str, Any]] | None = None,
    audio_path: str | None = None,
    provider: str = "whisper",
    api_key: str | None = None,
    language: str = "ar",
    model: str = "nova-2",
    low_confidence_threshold: float = 0.72,
    audio_duration: float | None = None,
) -> dict[str, Any]:
    """Align scene script text to word timings from Whisper (default) or optional Deepgram."""
    prov = (provider or "whisper").strip().lower()
    if prov not in ("whisper", "deepgram", "auto"):
        raise ValueError(f"Unknown timing provider {provider!r}; expected 'whisper', 'deepgram', or 'auto'.")

    resolved_words = asr_words
    resolved_dur = audio_duration
    used_provider = "whisper"

    if resolved_words is None and audio_path:
        eff_key = (api_key or os.environ.get("DEEPGRAM_API_KEY") or "").strip()
        if prov == "deepgram" or (prov == "auto" and eff_key):
            dg = transcribe_audio_deepgram(
                audio_path,
                api_key=eff_key,
                language=language,
                model=model,
            )
            resolved_words = dg["words"]
            if resolved_dur is None:
                resolved_dur = dg.get("duration")
            used_provider = "deepgram"
        else:
            # Check for Whisper sidecar JSON if present on disk
            for suffix in (".whisper.json", ".words.json"):
                sidecar = f"{audio_path}{suffix}"
                if os.path.isfile(sidecar):
                    with open(sidecar, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        resolved_words = data
                    elif isinstance(data, dict):
                        resolved_words = data.get("words") or data.get("word_timings")
                    break

    aligned = align_script_with_timings(
        script_text=script_text,
        asr_words=resolved_words,
        low_confidence_threshold=low_confidence_threshold,
        audio_duration=resolved_dur,
    )
    aligned["provider"] = used_provider
    return aligned
