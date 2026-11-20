"""Deterministic Script Pacing Sculptor & Auto-SRT Generator (v3.1.0).

Protects video pacing from weak LLMs that output walls of text, stray Markdown
syntax (`**bold**`, `# headers`, `- bullets`), or forget to generate `.srt`
subtitle files. Zero external dependencies.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .text_metrics import estimate_reading_duration


_MD_HEADER_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_MD_EMPH_RE = re.compile(r"[*_`~]+")
_URL_RE = re.compile(r"https?://\S+")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!؟?؛])\s+")


def clean_spoken_text(text: str) -> str:
    """Strip Markdown noise, URLs, and stray formatting so TTS and subtitles stay clean."""
    s = str(text or "")
    s = _URL_RE.sub("", s)
    s = _MD_HEADER_RE.sub("", s)
    s = _MD_BULLET_RE.sub("", s)
    s = _MD_EMPH_RE.sub("", s)
    s = _MULTI_SPACE_RE.sub(" ", s)
    return s.strip()


def _srt_ts(seconds: float) -> str:
    ms_total = max(0, int(round(float(seconds) * 1000)))
    h, rem = divmod(ms_total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt_for_scene(
    text: str,
    duration: float,
    output_srt: str,
    max_words_per_cue: int = 10,
) -> str:
    """Generate a clean, proportionally timed UTF-8 `.srt` file from scene text + duration."""
    clean = clean_spoken_text(text)
    words = clean.split()
    dur = max(0.8, float(duration))
    os.makedirs(os.path.dirname(os.path.abspath(output_srt)), exist_ok=True)

    if not words:
        with open(output_srt, "w", encoding="utf-8") as fh:
            fh.write(f"1\n00:00:00,000 --> {_srt_ts(dur)}\n \n")
        return output_srt

    # Group words into readable full-sentence / clause cues (<= max_words_per_cue)
    cues: list[str] = []
    buf: list[str] = []
    for w in words:
        buf.append(w)
        ends_clause = w[-1] in (".", "!", "؟", "?", "،", "؛", ":")
        if len(buf) >= max_words_per_cue or (ends_clause and len(buf) >= 5):
            cues.append(" ".join(buf))
            buf = []
    if buf:
        cues.append(" ".join(buf))

    total_chars = sum(max(1, len(c)) for c in cues)
    cursor = 0.0
    lines: list[str] = []
    for idx, cue_text in enumerate(cues, start=1):
        share = (max(1, len(cue_text)) / total_chars) * dur
        end_t = dur if idx == len(cues) else min(dur, cursor + share)
        lines.append(f"{idx}\n{_srt_ts(cursor)} --> {_srt_ts(end_t)}\n{cue_text}\n")
        cursor = end_t

    with open(output_srt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return output_srt


def polish_script_scenes(
    brief: dict[str, Any],
    max_words_per_scene: int = 24,
    srt_dir: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Clean Markdown noise, split run-on scenes into rhythmic bites, and optionally build SRTs."""
    out = dict(brief)
    scenes_in = out.get("scenes") or []
    polished: list[dict[str, Any]] = []
    split_count = 0
    srt_generated = 0

    for si, sc in enumerate(scenes_in):
        sc_copy = dict(sc)
        raw_text = str(sc_copy.get("text") or "")
        clean = clean_spoken_text(raw_text)
        words = clean.split()

        # Only split scenes that have NO pre-recorded narration file (splitting
        # text that already matches a WAV would desync audio).
        if len(words) > max_words_per_scene and not sc_copy.get("narration"):
            sents = [s.strip() for s in _SENT_SPLIT_RE.split(clean) if s.strip()]
            chunks: list[str] = []
            buf = ""
            for s in sents:
                if buf and len((buf + " " + s).split()) > max_words_per_scene:
                    chunks.append(buf)
                    buf = s
                else:
                    buf = f"{buf} {s}".strip()
            if buf:
                chunks.append(buf)
            # If still one giant sentence with no punctuation, split by word budget
            if len(chunks) == 1 and len(words) > max_words_per_scene:
                chunks = [
                    " ".join(words[i:i + max_words_per_scene])
                    for i in range(0, len(words), max_words_per_scene)
                ]
            split_count += max(0, len(chunks) - 1)
            for ci, ch in enumerate(chunks):
                sub = dict(sc_copy)
                sub["id"] = f"{sc_copy.get('id', f'scene_{si + 1}')}_p{ci + 1}"
                sub["text"] = ch
                sub["duration"] = estimate_reading_duration(ch, min_duration=2.2, max_duration=12.0, pacing_slack=0.5)
                polished.append(sub)
        else:
            sc_copy["text"] = clean
            if not sc_copy.get("duration"):
                sc_copy["duration"] = estimate_reading_duration(clean, min_duration=2.2, max_duration=14.0, pacing_slack=0.5)
            polished.append(sc_copy)

    if srt_dir:
        os.makedirs(srt_dir, exist_ok=True)
        for idx, sc in enumerate(polished):
            if not sc.get("captions") and sc.get("text"):
                srt_path = os.path.join(srt_dir, f"{sc.get('id', f'scene_{idx + 1}')}.srt")
                generate_srt_for_scene(sc["text"], float(sc.get("duration", 3.0)), srt_path)
                sc["captions"] = srt_path
                srt_generated += 1

    out["scenes"] = polished
    return out, {
        "input_scenes": len(scenes_in),
        "output_scenes": len(polished),
        "scenes_split": split_count,
        "srt_generated": srt_generated,
    }
