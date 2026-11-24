"""Reusable Channel Brand Kits for AgentCut (v3.4.3).

Locks channel identity across every video in a series so episodes never
drift in palette, caption style, series tag, motion physics, transitions,
music bed, room tone, or audio overlap.

Ships with 4 curated presets (`BRAND_KITS`):
- `tab_ezzay`          : «طب ازاي؟» warm cream paper, highlighter captions,
                         curiosity marimba bed, spring physics, and auto-SFX.
- `reel_studio_dark`   : Dark V1 cyan/indigo look, karaoke pill captions,
                         tech pulse bed, and energetic spring motion.
- `arabic_documentary` : Editorial clean paper, framed editorial box captions,
                         documentary drone bed, J/L-cuts, and push-in camera.
- `midnight_tech`      : Deep slate/amber developer & AI look, word-pop
                         captions, tech pulse bed, and wipe_rtl transitions.
"""
from __future__ import annotations

import copy
from typing import Any

BRAND_KITS: dict[str, dict[str, Any]] = {
    "tab_ezzay": {
        "id": "tab_ezzay",
        "name": "طب ازاي؟ (Pop-Science Paper)",
        "series_tag": "طب ازاي؟",
        "watermark": "طب ازاي؟",
        "palette": "cream",
        "caption_style": "highlighter_bar",
        "motion": "normal",
        "physics": "spring",
        "camera_move": "drift",
        "transition": "wipe_rtl",
        "auto_sfx": True,
        "beat_sync_sfx": True,
        "j_cut": 0.12,
        "l_cut": 0.10,
        "room_tone": "warm_studio",
        "music_bed": {"preset": "curiosity_marimba", "volume_db": -22.5},
    },
    "reel_studio_dark": {
        "id": "reel_studio_dark",
        "name": "Reel Studio Dark V1",
        "series_tag": "Reel Studio",
        "watermark": "REEL STUDIO",
        "palette": "dark",
        "caption_style": "karaoke_pill",
        "motion": "energetic",
        "physics": "elastic",
        "camera_move": "push_in",
        "transition": "iris_reveal",
        "auto_sfx": True,
        "beat_sync_sfx": True,
        "j_cut": 0.10,
        "l_cut": 0.08,
        "room_tone": "night_air",
        "music_bed": {"preset": "tech_pulse", "volume_db": -22.0},
    },
    "arabic_documentary": {
        "id": "arabic_documentary",
        "name": "وثائقي عربي تحريري",
        "series_tag": "تحقيق خاص",
        "watermark": "وثائقي",
        "palette": "editorial",
        "caption_style": "editorial_box",
        "motion": "calm",
        "physics": "spring",
        "camera_move": "push_in",
        "transition": "fade",
        "auto_sfx": True,
        "beat_sync_sfx": False,
        "j_cut": 0.18,
        "l_cut": 0.16,
        "room_tone": "soft_vinyl",
        "music_bed": {"preset": "documentary_drone", "volume_db": -22.0},
    },
    "midnight_tech": {
        "id": "midnight_tech",
        "name": "Midnight Tech & AI",
        "series_tag": "هندسة الذكاء",
        "watermark": "AI LAB",
        "palette": "midnight",
        "caption_style": "word_pop",
        "motion": "normal",
        "physics": "spring",
        "camera_move": "pan_rtl",
        "transition": "wipe_rtl",
        "auto_sfx": True,
        "beat_sync_sfx": True,
        "j_cut": 0.14,
        "l_cut": 0.12,
        "room_tone": "warm_studio",
        "music_bed": {"preset": "tech_pulse", "volume_db": -22.0},
    },
}


def list_brand_kits() -> list[dict[str, Any]]:
    """Return metadata for all available channel Brand Kits."""
    return [copy.deepcopy(BRAND_KITS[k]) for k in sorted(BRAND_KITS)]


def get_brand_kit(name: str) -> dict[str, Any] | None:
    """Look up a Brand Kit by ID (case-insensitive)."""
    key = str(name or "").strip().lower()
    kit = BRAND_KITS.get(key)
    return copy.deepcopy(kit) if kit else None


def register_brand_kit(kit_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Register or update a custom Brand Kit at runtime."""
    key = str(kit_id or "").strip().lower()
    if not key:
        raise ValueError("kit_id is required")
    merged = copy.deepcopy(BRAND_KITS["tab_ezzay"])
    merged.update(copy.deepcopy(config))
    merged["id"] = key
    BRAND_KITS[key] = merged
    return copy.deepcopy(merged)


def apply_brand_kit(spec: dict[str, Any], kit_name: str | None = None) -> dict[str, Any]:
    """Apply a Brand Kit's defaults to a spec dict without overwriting
    explicit user overrides already set on the spec (except when `kit_name`
    is passed explicitly as an override)."""
    out = copy.deepcopy(spec)
    chosen = kit_name or out.get("brand_kit")
    if not chosen:
        return out

    if isinstance(chosen, dict):
        kit = copy.deepcopy(BRAND_KITS["tab_ezzay"])
        kit.update(chosen)
    else:
        kit = get_brand_kit(str(chosen))
        if kit is None:
            return out

    explicit_override = kit_name is not None
    out["brand_kit"] = kit.get("id", str(chosen))
    for field in (
        "series_tag",
        "watermark",
        "palette",
        "caption_style",
        "motion",
        "physics",
        "camera_move",
        "transition",
        "auto_sfx",
        "beat_sync_sfx",
        "j_cut",
        "l_cut",
        "room_tone",
        "music_bed",
    ):
        if field in kit and (explicit_override or field not in out):
            out[field] = copy.deepcopy(kit[field])
    return out
