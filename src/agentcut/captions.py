"""Smart Arabic Kinetic Typography & Balanced Caption Engine for AgentCut (v3.4.2).

Provides:
- 4 caption display styles (`CAPTION_STYLES`):
    * `word_pop`        : classic Reel Studio scale-pop active word lighting
    * `highlighter_bar` : warm yellow marker highlight bar behind active words
    * `karaoke_pill`    : rounded pill capsule wrapping the active spoken word
    * `editorial_box`   : framed neo-brutalist card with underline sweep
- Arabic line-length balancing & orphan-word prevention (`balance_caption_chunks`):
  splits long sentences into visually balanced 3-to-7 word chunks and merges
  1-word trailing orphans into the preceding chunk so a single word never sits
  alone on a caption card.
- Keyword emphasis (`class="w kw"`) and clean mixed-script (`dir="ltr"` badge
  isolation for Latin/technical tokens inside RTL Arabic lines) without breaking
  existing `.w`, `data-s`, `data-e` span contracts.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

CAPTION_STYLES = (
    "word_pop",
    "highlighter_bar",
    "karaoke_pill",
    "editorial_box",
)

_SENTENCE_END = re.compile(r"(?<=[.؟!،؛])\s+")
_LATIN_RE = re.compile(r"^[A-Za-z0-9_./:+-]+$")
_PUNCT_STRIP = ".,،؛:!؟\"'()[]{}«»"


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=False)


def is_latin_token(token: str) -> bool:
    """True when a word token is purely Latin/ASCII technical text (e.g. Deepgram, API, v3.4)."""
    cleaned = token.strip(_PUNCT_STRIP)
    return bool(cleaned and any(ch.isalpha() for ch in cleaned) and _LATIN_RE.match(cleaned))


def balance_caption_chunks(
    words: list[dict[str, Any]],
    max_words_per_chunk: int = 7,
    min_words_per_chunk: int = 2,
) -> list[list[dict[str, Any]]]:
    """Group timed words into balanced caption chunks with zero 1-word orphans.

    1. Splits first at natural sentence boundaries (`.`, `؟`, `!`).
    2. Sub-splits any sentence longer than `max_words_per_chunk` into two or
       more evenly sized chunks (e.g. 9 words -> 5 + 4, never 7 + 2 or 8 + 1).
    3. Merges any 1-word orphan chunk into its neighbor if total words > 1.
    """
    if not words:
        return []

    # Step 1: split by sentence punctuation while keeping exact word dicts
    raw_sentences: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        cur.append(w)
        tok = str(w.get("word", "")).strip()
        if tok.endswith((".", "؟", "!")):
            raw_sentences.append(cur)
            cur = []
    if cur:
        raw_sentences.append(cur)

    # Step 2: balanced sub-splitting of long sentences
    balanced: list[list[dict[str, Any]]] = []
    for sent in raw_sentences:
        n = len(sent)
        if n <= max_words_per_chunk:
            balanced.append(sent)
        else:
            n_parts = (n + max_words_per_chunk - 1) // max_words_per_chunk
            base_size = n // n_parts
            rem = n % n_parts
            idx = 0
            for p in range(n_parts):
                sz = base_size + (1 if p < rem else 0)
                balanced.append(sent[idx : idx + sz])
                idx += sz

    # Step 3: prevent 1-word orphans when neighboring chunk exists
    if len(balanced) > 1:
        merged: list[list[dict[str, Any]]] = []
        for chunk in balanced:
            if (
                len(chunk) < min_words_per_chunk
                and merged
                and len(merged[-1]) + len(chunk) <= max_words_per_chunk + 1
            ):
                merged[-1].extend(chunk)
            else:
                merged.append(list(chunk))
        # Also check if first chunk was 1 word and can merge forward
        if len(merged) > 1 and len(merged[0]) == 1 and len(merged[1]) <= max_words_per_chunk:
            merged[1] = merged[0] + merged[1]
            merged.pop(0)
        balanced = merged

    return [c for c in balanced if c]


def render_caption_html(
    timing: dict[str, Any],
    scene: dict[str, Any] | None = None,
    spec: dict[str, Any] | None = None,
    max_words_per_chunk: int = 7,
) -> str:
    """Build balanced Arabic/mixed-script caption HTML with style & keyword support.

    Preserves `<span class="w" data-s="..." data-e="...">word</span>` backward
    compatibility while adding:
      - `cap-style-<style>` on `.caption`
      - `data-kw="1"` on matched keyword spans (from `scene['keywords']`)
      - `data-latin="1"` on Latin technical tokens for crisp mixed-script bidi isolation.
    """
    words = timing.get("words") or []
    if not words:
        return ""
    sc = scene or {}
    sp = spec or {}
    style = str(sc.get("caption_style") or sp.get("caption_style") or "word_pop").strip().lower()
    if style not in CAPTION_STYLES:
        style = "word_pop"

    raw_kws = sc.get("keywords") or sp.get("keywords") or []
    kw_set = {str(k).strip(_PUNCT_STRIP) for k in raw_kws if str(k).strip()}

    chunks = balance_caption_chunks(words, max_words_per_chunk=max_words_per_chunk)
    html_parts: list[str] = []
    for cwords in chunks:
        if not cwords:
            continue
        s = cwords[0]["start"]
        e = cwords[-1]["end"]
        spans: list[str] = []
        for w in cwords:
            tok = str(w["word"])
            clean = tok.strip(_PUNCT_STRIP)
            extra_attrs = ""
            if clean and (clean in kw_set or tok in kw_set):
                extra_attrs += ' data-kw="1"'
            if is_latin_token(tok):
                extra_attrs += ' data-latin="1"'
            spans.append(
                f'<span class="w" data-s="{w["start"]}" '
                f'data-e="{w["end"]}"{extra_attrs}>{_esc(tok)}</span> '
            )
        html_parts.append(
            f'<div class="cap-chunk" data-s="{s}" data-e="{e}">'
            f'{"".join(spans).strip()}</div>'
        )
    return f'<div class="caption cap-style-{style}">' + "".join(html_parts) + "</div>"
