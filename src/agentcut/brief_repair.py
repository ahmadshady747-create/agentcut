"""Forgiving Model-Proof Brief Coercer & Repair Engine (v3.1.0).

Small, free, or local LLMs frequently emit malformed JSON (markdown fences,
trailing commas), wrong field aliases (`script` vs `text`, `audio` vs `narration`,
`image` vs `broll`, `ratio` vs `aspect`), typos in enum names (`chiaroscuro`
instead of `chiaroscuro_doc`), stringified numbers (`"3.5s"`), or plain string
scenes. This module deterministically repairs messy inputs so weak models
produce valid, high-craft AgentCut briefs on the first call. Zero dependencies.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from .cinematography import CINEMA_LOOKS
from .profiles import RENDER_PROFILES
from .text_metrics import estimate_reading_duration


_VALID_STYLES = ("editorial", "cinematic", "blackboard")
_VALID_ASPECTS = ("9:16", "16:9", "1:1", "4:5")

_TOP_ALIASES = {
    "ratio": "aspect",
    "aspect_ratio": "aspect",
    "look": "cinema_look",
    "grade": "cinema_look",
    "color_grade": "cinema_look",
    "profile": "render_profile",
    "quality": "render_profile",
    "mode": "style",
    "bg_music": "music",
    "bgm": "music",
}

_SCENE_ALIASES = {
    "script": "text",
    "line": "text",
    "narration_text": "text",
    "caption": "text",
    "content": "text",
    "audio": "narration",
    "voice": "narration",
    "voiceover": "narration",
    "audio_path": "narration",
    "image": "broll",
    "img": "broll",
    "visual": "broll",
    "media": "broll",
    "video": "broll",
    "srt": "captions",
    "subtitle": "captions",
    "subtitles": "captions",
    "card": "highlight_card",
}

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_NUM_STR_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def _parse_number(val: Any, default: float | None = None) -> float | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if isinstance(val, str):
        m = _NUM_STR_RE.search(val.strip())
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return default


def _fuzzy_match(value: str, choices: tuple[str, ...] | list[str], default: str) -> tuple[str, bool]:
    low = str(value or "").strip().lower()
    if low in choices:
        return low, False
    # Substring match first (e.g. "chiaroscuro" -> "chiaroscuro_doc")
    for ch in choices:
        if low and (low in ch or ch in low):
            return ch, True
    matches = difflib.get_close_matches(low, list(choices), n=1, cutoff=0.55)
    if matches:
        return matches[0], True
    return default, True


def parse_loose_json(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Parse a dict, a markdown-fenced/trailing-comma JSON string, or raw essay text."""
    repairs: list[str] = []
    if isinstance(raw, dict):
        return dict(raw), repairs
    if not isinstance(raw, str):
        raise ValueError("input must be a dict or string")

    cleaned = _FENCE_RE.sub("", raw).strip()
    if cleaned != raw.strip():
        repairs.append("stripped_markdown_code_fences")

    # Attempt strict JSON first, then trailing-comma repair
    if cleaned.startswith("{") or cleaned.startswith("["):
        for attempt_str, tag in (
            (cleaned, None),
            (_TRAILING_COMMA_RE.sub(r"\1", cleaned), "removed_trailing_commas"),
        ):
            try:
                parsed = json.loads(attempt_str)
                if tag:
                    repairs.append(tag)
                if isinstance(parsed, list):
                    repairs.append("wrapped_scene_list_into_brief")
                    return {"scenes": parsed}, repairs
                if isinstance(parsed, dict):
                    return parsed, repairs
            except json.JSONDecodeError:
                continue

    # Fallback: treat raw string as an Arabic/English essay script
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+|\n+", cleaned) if p.strip()]
    repairs.append("converted_raw_text_to_scenes")
    return {
        "title": (paragraphs[0][:40] if paragraphs else "Editorial Essay"),
        "scenes": [{"text": p} for p in paragraphs],
    }, repairs


def repair_brief(raw_input: Any) -> tuple[dict[str, Any], list[str]]:
    """Deterministically coerce and repair a messy LLM-generated brief into a clean v3.1 brief.

    Returns `(repaired_brief, repairs_made)`.
    """
    brief, repairs = parse_loose_json(raw_input)

    # 1. Normalize top-level aliases
    for old_k, new_k in _TOP_ALIASES.items():
        if old_k in brief and new_k not in brief:
            brief[new_k] = brief.pop(old_k)
            repairs.append(f"aliased_{old_k}_to_{new_k}")

    brief.setdefault("title", "Editorial Essay")
    brief.setdefault("parallel", True)
    brief.setdefault("cache_scenes", True)

    # 2. Fuzzy-repair style, aspect, cinema_look, render_profile
    if "style" in brief:
        fixed_style, changed = _fuzzy_match(str(brief["style"]), _VALID_STYLES, "editorial")
        if changed:
            repairs.append(f"fixed_style_{brief['style']}_to_{fixed_style}")
            brief["style"] = fixed_style
    else:
        brief["style"] = "editorial"
        repairs.append("defaulted_style_to_editorial")

    if brief["style"] == "editorial":
        if brief.get("captions_style") not in (None, "plain"):
            repairs.append(f"forced_editorial_captions_style_plain")
        brief["captions_style"] = "plain"

    if "aspect" in brief:
        raw_asp = str(brief["aspect"]).strip().lower()
        asp_map = {"vertical": "9:16", "portrait": "9:16", "reels": "9:16", "shorts": "9:16",
                   "horizontal": "16:9", "landscape": "16:9", "youtube": "16:9", "square": "1:1"}
        if raw_asp in asp_map:
            brief["aspect"] = asp_map[raw_asp]
            repairs.append(f"mapped_aspect_{raw_asp}_to_{brief['aspect']}")
        else:
            fixed_asp, changed = _fuzzy_match(raw_asp, _VALID_ASPECTS, "9:16")
            if changed:
                repairs.append(f"fixed_aspect_{raw_asp}_to_{fixed_asp}")
                brief["aspect"] = fixed_asp
    else:
        brief["aspect"] = "9:16"

    if brief.get("cinema_look"):
        fixed_look, changed = _fuzzy_match(str(brief["cinema_look"]), list(CINEMA_LOOKS.keys()), "chiaroscuro_doc")
        if changed:
            repairs.append(f"fixed_cinema_look_{brief['cinema_look']}_to_{fixed_look}")
            brief["cinema_look"] = fixed_look

    if brief.get("render_profile"):
        fixed_prof, changed = _fuzzy_match(str(brief["render_profile"]), list(RENDER_PROFILES.keys()), "low_spec")
        if changed:
            repairs.append(f"fixed_profile_{brief['render_profile']}_to_{fixed_prof}")
            brief["render_profile"] = fixed_prof

    # 3. Repair scenes list (supports plain strings, alias keys, string numbers)
    raw_scenes = brief.get("scenes")
    if isinstance(raw_scenes, str):
        raw_scenes = [p.strip() for p in re.split(r"\n+", raw_scenes) if p.strip()]
        repairs.append("split_string_scenes")
    if not isinstance(raw_scenes, list):
        raw_scenes = []

    fixed_scenes: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_scenes):
        if isinstance(item, str):
            sc: dict[str, Any] = {"text": item.strip()}
            repairs.append(f"coerced_string_scene_{idx + 1}")
        elif isinstance(item, dict):
            sc = dict(item)
        else:
            continue

        for old_k, new_k in _SCENE_ALIASES.items():
            if old_k in sc and new_k not in sc:
                sc[new_k] = sc.pop(old_k)
                repairs.append(f"scene_{idx + 1}_aliased_{old_k}_to_{new_k}")

        sc.setdefault("id", f"scene_{idx + 1}")
        sc.setdefault("text", "")

        # Coerce stringified duration (e.g. "4.2s" -> 4.2)
        if "duration" in sc:
            parsed_d = _parse_number(sc["duration"])
            if parsed_d is not None and parsed_d > 0:
                if not isinstance(sc["duration"], (int, float)):
                    repairs.append(f"scene_{idx + 1}_parsed_duration")
                sc["duration"] = round(parsed_d, 3)
            else:
                sc["duration"] = estimate_reading_duration(sc["text"])
                repairs.append(f"scene_{idx + 1}_estimated_duration")
        elif sc["text"]:
            sc["duration"] = estimate_reading_duration(sc["text"])

        # Coerce string highlight_card ("الفكرة" -> {"title": "الفكرة"})
        if isinstance(sc.get("highlight_card"), str) and sc["highlight_card"].strip():
            sc["highlight_card"] = {"title": sc["highlight_card"].strip(), "duration": 2.8}
            repairs.append(f"scene_{idx + 1}_coerced_highlight_card")

        # Strip forbidden editorial keys cleanly instead of crashing later
        if brief["style"] == "editorial":
            for forbidden in ("keywords", "diagram", "subject"):
                if forbidden in sc:
                    sc.pop(forbidden, None)
                    repairs.append(f"scene_{idx + 1}_removed_editorial_forbidden_{forbidden}")

        fixed_scenes.append(sc)

    brief["scenes"] = fixed_scenes
    return brief, repairs
