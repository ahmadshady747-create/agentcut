"""Text metrics, on-screen reading duration estimation, and script-to-caption diffing.

Inspired by ProCraft's text tools, adapted for AgentCut automated scene pacing,
kinetic caption timing, and script verification. Zero external dependencies.

v2.0.0 fixes:
- Preserves Arabic diacritics/tashkeel (Unicode combining marks U+064B..U+065F,
  U+0670, U+06D6..U+06ED) inside word tokens so vocalized Arabic words are not
  split into fragments.
- Fixes `is_arabic_predominant` so pure numeric/punctuation strings (0 Arabic
  words, 0 Latin words) return False instead of True.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# Word token regex that keeps Arabic diacritics (tashkeel) attached to their base word
_WORD_RE = re.compile(r"[\w\u064B-\u065F\u0670\u06D6-\u06ED]+", re.UNICODE)
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_CHAR_RE = re.compile(r"[a-zA-Z]")


def analyze_text(text: str) -> dict[str, Any]:
    """Analyze text composition, word counts, and language distribution."""
    if not text:
        return {
            "words": 0,
            "characters": 0,
            "characters_no_spaces": 0,
            "lines": 0,
            "arabic_words": 0,
            "latin_words": 0,
            "digits": 0,
            "is_arabic_predominant": False,
        }

    lines = [ln for ln in text.splitlines() if ln.strip()]
    tokens = _WORD_RE.findall(text)
    arabic_tokens = [tok for tok in tokens if _ARABIC_CHAR_RE.search(tok)]
    latin_tokens = [tok for tok in tokens if _LATIN_CHAR_RE.search(tok)]
    digits = re.findall(r"\d+", text)

    return {
        "words": len(tokens),
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s+", "", text)),
        "lines": max(1, len(lines)),
        "arabic_words": len(arabic_tokens),
        "latin_words": len(latin_tokens),
        "digits": len(digits),
        "is_arabic_predominant": len(arabic_tokens) > 0 and len(arabic_tokens) >= len(latin_tokens),
    }


def estimate_reading_duration(
    text: str,
    wpm: int | None = None,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    pacing_slack: float = 0.5,
) -> float:
    """Estimate optimal on-screen display duration (in seconds) for a text card or subtitle.

    Uses language-aware reading speeds:
      - Arabic text: default ~140 WPM (for comfortable visual absorption).
      - Latin/English: default ~160 WPM.
    Adds a minimum visual comprehension slack (default +0.5s).
    Clamped between min_duration and max_duration.
    """
    metrics = analyze_text(text)
    word_count = metrics["words"]
    if word_count == 0:
        return min_duration

    # Determine words per minute based on predominant language if not specified
    if wpm is None:
        effective_wpm = 140 if metrics["is_arabic_predominant"] else 160
    else:
        effective_wpm = max(30, wpm)

    # Base duration in seconds
    raw_seconds = (word_count / effective_wpm) * 60.0

    # Add pacing slack for eye-tracking and layout absorption
    total_seconds = raw_seconds + pacing_slack

    # Clamp to boundaries
    clamped = max(min_duration, min(max_duration, total_seconds))
    return round(clamped, 2)


def diff_texts(original_text: str, derived_text: str) -> dict[str, Any]:
    """Compare an original script against derived captions or transcription.

    Returns similarity ratio (0.0 to 1.0), additions, deletions, and structural diff.
    Useful in Agent Verify Loops to ensure no script lines were omitted.
    """
    orig_words = re.findall(r"\S+", original_text.strip())
    der_words = re.findall(r"\S+", derived_text.strip())

    matcher = difflib.SequenceMatcher(None, orig_words, der_words)
    similarity = round(matcher.ratio(), 3)
    is_identical = (similarity == 1.0)

    added = []
    removed = []
    changes = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "delete":
            deleted_words = orig_words[i1:i2]
            removed.extend(deleted_words)
            changes.append({"type": "removed", "words": deleted_words, "orig_index": i1})
        elif tag == "insert":
            inserted_words = der_words[j1:j2]
            added.extend(inserted_words)
            changes.append({"type": "added", "words": inserted_words, "derived_index": j1})
        elif tag == "replace":
            del_words = orig_words[i1:i2]
            ins_words = der_words[j1:j2]
            removed.extend(del_words)
            added.extend(ins_words)
            changes.append({
                "type": "replaced",
                "removed": del_words,
                "added": ins_words,
                "orig_index": i1,
            })

    return {
        "similarity": similarity,
        "is_identical": is_identical,
        "original_word_count": len(orig_words),
        "derived_word_count": len(der_words),
        "added_words": added,
        "removed_words": removed,
        "change_count": len(changes),
        "changes": changes,
    }
