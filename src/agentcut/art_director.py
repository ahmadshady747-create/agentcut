"""Deterministic Art Director & Visual Style Bible Prompt Compiler (v3.1.0).

Eliminates inconsistent, amateur image prompts from small/free LLMs by locking
every scene & beat into a cohesive cinematic Style Bible with alternating shot
scales (establishing wide -> medium -> close-up detail -> silhouette) and strict
anti-artifact negative rules (no text, no watermarks, no floating cutouts).
Zero external dependencies.
"""

from __future__ import annotations

import re
from typing import Any

from .director import analyze_narrative_beat


STYLE_BIBLES: dict[str, dict[str, str]] = {
    "chiaroscuro_doc": {
        "name": "chiaroscuro_doc",
        "aesthetic": "crisp 35mm documentary photography, clean natural blacks, subtle warm tungsten key light, sharp architectural and object focus, zero color fog, Leica 50mm f/1.4",
        "palette": "deep charcoal, obsidian shadows, warm antique gold and amber highlights",
        "negative": "no human faces, no close-up portraits, no distorted eyes, no text, no letters, no arabic script, no documents with writing, no watermark, no logo, no split screen, no collage, no cartoon, no clipart, no floating cutout",
    },
    "kodak_warm": {
        "name": "kodak_warm",
        "aesthetic": "35mm Kodak Portra film still, golden hour natural light, soft organic grain, warm nostalgic tones, candid documentary framing, Leica 50mm f/1.4",
        "palette": "warm ochre, soft ivory, muted terracotta, gentle lifted blacks",
        "negative": "no text, no watermark, no neon oversaturation, no 3d render plastic look, no collage",
    },
    "noir_slate": {
        "name": "noir_slate",
        "aesthetic": "investigative neo-noir documentary still, moody overcast rain-slicked reflections, cool slate-teal shadows, crisp architectural geometry, 35mm cinema frame",
        "palette": "slate blue, muted steel gray, desaturated teal, crisp ivory highlights",
        "negative": "no text, no watermark, no bright pop colors, no cartoon, no cluttered graphic overlay",
    },
    "warm_archive": {
        "name": "warm_archive",
        "aesthetic": "archival historical documentary still, timeless oil-and-film chiaroscuro, aged parchment warmth, museum lighting, subtle vignette, fine photographic texture",
        "palette": "aged sepia, burnt umber, warm bronze, soft cream highlights",
        "negative": "no modern smartphones, no text, no watermark, no modern UI, no neon colors",
    },
    "midnight_tech": {
        "name": "midnight_tech",
        "aesthetic": "sleek cinematic sci-fi documentary photography, dark obsidian studio environment, subtle volumetric cyan rim light, macro hardware and optical glass details, 85mm cinema lens",
        "palette": "deep midnight indigo, matte obsidian, restrained cyan-silver accents",
        "negative": "no garish matrix code text, no watermark, no cheesy glowing brains, no cartoon icons",
    },
    "editorial_clean": {
        "name": "editorial_clean",
        "aesthetic": "high-end editorial magazine photography, clean architectural light, balanced neutral contrast, purposeful negative space, sharp subject focus, 50mm prime lens",
        "palette": "natural balanced neutrals, soft warm stone, crisp slate accents",
        "negative": "no text, no watermark, no stock-photo cheese, no cluttered background",
    },
}

_SHOT_SCALES: tuple[tuple[str, str], ...] = (
    ("wide_establishing", "wide establishing shot, grand environmental context, rule-of-thirds composition"),
    ("medium_narrative", "medium cinematic shot, balanced subject framing, natural eye-level perspective"),
    ("intimate_closeup", "intimate macro close-up detail, tactile surface texture, creamy shallow depth of field"),
    ("atmospheric_silhouette", "moody backlit silhouette shot, strong rim lighting, contemplative negative space"),
)

# Arabic & English conceptual keyword -> concrete cinematic visual motif
_VISUAL_MOTIFS: list[tuple[tuple[str, ...], str]] = [
    (("زمن", "الوقت", "ساعة", "تاريخ", "time", "clock", "history", "era"), "an antique brass hourglass and vintage mechanical watch gears illuminated by a single shaft of dust-filled light"),
    (("عقل", "فكر", "وعي", "ذاكرة", "تفكير", "mind", "memory", "thought", "brain"), "a solitary scholar's wooden desk with open leather-bound notebooks, warm lamp glow, and dramatic shadows across a quiet library"),
    (("تكنولوجيا", "ذكاء", "خوارزمية", "برمجة", "كود", "ai", "algorithm", "code", "tech", "data"), "macro optical fiber glass prisms and dark anodized server blades catching restrained volumetric light in a dim studio"),
    (("مدينة", "مجتمع", "ناس", "عالم", "حياة", "city", "society", "world", "street"), "a moody cinematic twilight boulevard with blurred passersby in motion and warm streetlamp reflections on dark pavement"),
    (("صمت", "عزلة", "تأمل", "هدوء", "silence", "solitude", "peace", "calm"), "a lone silhouette standing beside a tall arched window looking out over misty hills at dusk"),
    (("مال", "اقتصاد", "سوق", "نجاح", "ثروة", "money", "economy", "market", "finance"), "minimalist dark marble architectural hall with sharp shafts of morning sunlight and vintage brass balance scales"),
    (("سؤال", "حقيقة", "سر", "مفارقة", "question", "truth", "paradox", "mystery"), "a shadowy stone corridor opening toward a luminous doorway with dramatic light and shadow contrast"),
    (("كتابة", "كتاب", "شعر", "كلمة", "قصة", "book", "write", "poetry", "story"), "a fountain pen resting on textured cream paper beside warm candlelight in an intimate dark study"),
]


def list_style_bibles() -> list[dict[str, str]]:
    """Return all built-in visual Style Bibles."""
    return [dict(v) for v in STYLE_BIBLES.values()]


def _pick_visual_motif(text: str, beat_index: int) -> str:
    low = (text or "").lower()
    for keys, motif in _VISUAL_MOTIFS:
        if any(k in low for k in keys):
            return motif
    fallbacks = (
        "atmospheric dark architectural interior with dramatic diagonal sunbeams cutting through gentle haze",
        "contemplative human silhouette framed by moody studio rim lighting against a textured dark wall",
        "tactile close-up of weathered stone, warm brass, and glass catching soft golden documentary light",
        "vast twilight horizon over quiet landscape with deep moody clouds and subtle warm light on the edge",
    )
    return fallbacks[beat_index % len(fallbacks)]


def generate_visual_prompts(
    brief: dict[str, Any],
    style_bible: str | None = None,
    aspect: str | None = None,
) -> dict[str, Any]:
    """Deterministically compile cohesive, cinema-grade image prompts for every scene/beat.

    Guarantees style consistency, shot-scale alternation, and anti-artifact
    negative constraints regardless of how weak the calling LLM is.
    """
    bible_key = str(style_bible or brief.get("cinema_look") or "chiaroscuro_doc").strip().lower()
    bible = STYLE_BIBLES.get(bible_key, STYLE_BIBLES["chiaroscuro_doc"])
    asp = str(aspect or brief.get("aspect") or "9:16")
    framing_hint = "vertical 9:16 framing, clean lower third for subtitles" if asp == "9:16" else f"{asp} widescreen cinema framing"

    prompts: list[dict[str, Any]] = []
    global_idx = 0
    scenes = brief.get("scenes") or []

    for si, sc in enumerate(scenes):
        sid = str(sc.get("id") or f"scene_{si + 1}")
        text = str(sc.get("text") or "")
        analysis = analyze_narrative_beat(text, beat_index=si, total_beats=len(scenes), is_scene_start=True)
        beats = sc.get("beats") if isinstance(sc.get("beats"), list) and sc.get("beats") else [{"start": 0.0, "end": sc.get("duration", 3.0)}]

        for bi, beat in enumerate(beats):
            scale_name, scale_desc = _SHOT_SCALES[global_idx % len(_SHOT_SCALES)]
            motif = _pick_visual_motif(text, global_idx)
            mood_hint = (
                "high tension, crisp contrast"
                if analysis["intent"] in ("hook", "question", "climax")
                else "contemplative, serene, atmospheric"
            )
            prompt = (
                f"{scale_desc}, {motif}, {mood_hint}, "
                f"{bible['aesthetic']}, color palette: {bible['palette']}, "
                f"{framing_hint}. Avoid: {bible['negative']}."
            )
            prompts.append({
                "scene_id": sid,
                "beat_index": bi,
                "global_beat": global_idx,
                "intent": analysis["intent"],
                "shot_scale": scale_name,
                "prompt": prompt,
                "negative_prompt": bible["negative"],
            })
            global_idx += 1

    return {
        "style_bible": bible["name"],
        "aspect": asp,
        "total_prompts": len(prompts),
        "prompts": prompts,
    }
