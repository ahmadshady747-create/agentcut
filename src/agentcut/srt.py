"""Minimal SRT parsing for AgentCut.

Only what captions need: cue index (optional), a time range, and text.
Multi-line cue text is joined with a space, because drawtext renders
captions as a single centred line in v1. Malformed input raises
ValueError with a human-readable message; validation turns that into
a structured INVALID_SCHEMA error.
"""
from __future__ import annotations

import re

_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _to_seconds(h, m, s, frac):
    frac = (frac + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 1000.0


def parse_srt_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if not blocks:
        raise ValueError("SRT file has no cues")
    cues = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            raise ValueError(f"cue with no time range: {block[:80]!r}")
        m = _TIME_RE.match(lines[0])
        if not m:
            raise ValueError(f"bad SRT time range: {lines[0]!r}")
        start = _to_seconds(*m.groups()[:4])
        end = _to_seconds(*m.groups()[4:])
        cue_text = " ".join(lines[1:]).strip()
        if end <= start:
            raise ValueError(f"SRT cue ends before it starts: {lines[0]!r}")
        if not cue_text:
            raise ValueError(f"SRT cue has no text: {lines[0]!r}")
        cues.append({"text": cue_text, "start": start, "end": end})
    if not cues:
        raise ValueError("SRT file has no cues")
    return cues


def parse_srt_file(path):
    with open(path, encoding="utf-8") as fh:
        return parse_srt_text(fh.read())
