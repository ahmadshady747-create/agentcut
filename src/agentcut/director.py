"""Adaptive Human-Feel Narrative Director (v2.5.0).

Analyzes Arabic and English script text, punctuation, rhetorical cues, and
word timings to break the robotic, metronomic patterns of automated video tools:
- Detects per-scene and per-beat narrative intent (`hook`, `question`,
  `revelation`, `quote`, `data_point`, `reflection`, `climax`, `narrative`).
- Varies pacing naturally (tight cuts on hooks/questions, contemplative holds
  on quotes/revelations) instead of uniform beat durations.
- Assigns non-repetitive organic camera moves (`cinematography.py`) with
  handheld micro-drift.
- Varies transitions intelligently (hard cuts for punch, soft crossfades for
  narrative flow, chapter dips at act turns).
- Exercises HUD restraint: places `highlight_card`s sparingly on genuinely
  high-value moments rather than spamming overlays on every scene.
- Plans subtle soundscape cues (`chapter_boom`, `card_tick`, `soft_whoosh`,
  `warm_studio` room tone) and J-cut audio overlaps.
Zero external dependencies.
"""

from __future__ import annotations

import re
from typing import Any

from .cinematography import (
    CINEMA_LOOKS,
    choose_narrative_camera_move,
    get_cinema_look,
)
from .profiles import apply_profile_to_brief
from .soundscape import plan_jl_cut_audio
from .text_metrics import analyze_text, estimate_reading_duration


_ARABIC_HOOK_CUES = (
    "تخيل", "هل تعلم", "في الحقيقة", "الحقيقة أن", "السؤال الأهم",
    "المفاجأة", "انتظر", "فكّر", "تأمل", "لماذا", "كيف",
)
_ARABIC_REVELATION_CUES = (
    "لكن", "إلا أن", "المفارقة", "السر", "اكتشف", "النتيجة",
    "الخلاصة", "وهنا", "الصدمة", "لهذا السبب",
)
_ARABIC_REFLECTION_CUES = (
    "ربما", "في النهاية", "الإنسان", "الزمن", "الوعي", "الحياة",
    "المعنى", "التاريخ", "الفلسفة", "الذاكرة",
)
_ENGLISH_HOOK_CUES = (
    "imagine", "what if", "why do", "the truth is", "surprisingly",
    "wait", "think about", "have you ever",
)
_ENGLISH_REVELATION_CUES = (
    "however", "but here", "the paradox", "the secret", "the result",
    "turns out", "in reality", "actually", "that is why",
)
_ENGLISH_REFLECTION_CUES = (
    "perhaps", "ultimately", "humanity", "meaning", "memory",
    "history", "time", "silence", "philosophy",
)
_NUMBER_RE = re.compile(r"(?:\d+(?:[.,]\d+)?\s*[%٪$€£]?|\b\d{4}\b)")
_QUOTE_RE = re.compile(r"[«\"“].{6,}[»\"”]")


def analyze_narrative_beat(
    text: str,
    beat_index: int = 0,
    total_beats: int = 1,
    is_scene_start: bool = False,
) -> dict[str, Any]:
    """Classify a single beat or scene's narrative intent, energy, and editorial pacing."""
    raw = (text or "").strip()
    low = raw.lower()
    metrics = analyze_text(raw)

    intent = "narrative"
    energy = 0.55
    ideal_beat_range = (2.8, 3.8)
    # v3.3.1: Hard cuts by default (~80% of edits) to avoid PowerPoint ghosting
    transition = "none"
    transition_dur = 0.0
    sfx_cue: str | None = None

    if beat_index == 0 and is_scene_start:
        intent = "hook"
        energy = 0.85
        ideal_beat_range = (1.9, 2.8)
        transition = "none"
        transition_dur = 0.0
        sfx_cue = "chapter_boom"
    elif "؟" in raw or "?" in raw:
        intent = "question"
        energy = 0.78
        ideal_beat_range = (2.1, 3.0)
        transition = "none"  # Punchy hard cut on rhetorical questions
        transition_dur = 0.0
        sfx_cue = "soft_whoosh" if is_scene_start else None
    elif _QUOTE_RE.search(raw):
        intent = "quote"
        energy = 0.35
        ideal_beat_range = (3.8, 5.2)
        transition = "crossfade"
        transition_dur = 0.38
        sfx_cue = "shimmer_reveal"
    elif "!" in raw or any(c in raw for c in _ARABIC_REVELATION_CUES) or any(c in low for c in _ENGLISH_REVELATION_CUES):
        intent = "revelation"
        energy = 0.80
        ideal_beat_range = (2.4, 3.4)
        transition = "none" if energy >= 0.8 else "crossfade"
        transition_dur = 0.0 if transition == "none" else 0.22
        sfx_cue = "chapter_boom" if is_scene_start else "soft_whoosh"
    elif _NUMBER_RE.search(raw):
        intent = "data_point"
        energy = 0.68
        ideal_beat_range = (2.6, 3.5)
        transition = "none"
        transition_dur = 0.0
        sfx_cue = "card_tick"
    elif any(c in raw for c in _ARABIC_HOOK_CUES) or any(c in low for c in _ENGLISH_HOOK_CUES):
        intent = "hook"
        energy = 0.82
        ideal_beat_range = (2.0, 2.9)
        transition = "none"
        transition_dur = 0.0
        sfx_cue = "soft_whoosh"
    elif any(c in raw for c in _ARABIC_REFLECTION_CUES) or any(c in low for c in _ENGLISH_REFLECTION_CUES):
        intent = "reflection"
        energy = 0.32
        ideal_beat_range = (3.6, 4.8)
        transition = "crossfade"
        transition_dur = 0.35
        sfx_cue = None
    elif total_beats >= 3 and beat_index == total_beats - 1:
        intent = "climax"
        energy = 0.72
        ideal_beat_range = (3.4, 4.6)
        transition = "crossfade"
        transition_dur = 0.40
        sfx_cue = "shimmer_reveal"

    return {
        "intent": intent,
        "energy": round(energy, 2),
        "ideal_beat_range": ideal_beat_range,
        "recommended_transition": transition,
        "recommended_transition_duration": transition_dur,
        "recommended_sfx": sfx_cue,
        "is_arabic": metrics["is_arabic_predominant"],
        "word_count": metrics["words"],
    }


def _extract_card_title(text: str, intent: str) -> str | None:
    """Extract a crisp, short highlight_card title (<= 4 words) from a key sentence."""
    raw = (text or "").strip()
    if not raw:
        return None
    m_num = _NUMBER_RE.search(raw)
    if intent == "data_point" and m_num:
        # Grab the number plus 1-2 surrounding words
        words = raw.split()
        for i, w in enumerate(words):
            if m_num.group(0).strip() in w:
                lo = max(0, i - 1)
                hi = min(len(words), i + 2)
                return " ".join(words[lo:hi]).strip(".,،؛:!؟")
    m_q = _QUOTE_RE.search(raw)
    if intent == "quote" and m_q:
        inner = m_q.group(0).strip("«»\"“” ").split()
        return " ".join(inner[:5])
    if intent in ("revelation", "climax"):
        words = raw.split()
        if len(words) >= 3:
            return " ".join(words[:4]).strip(".,،؛:!؟")
    return None


def humanize_brief(
    brief: dict[str, Any],
    cinema_look: str = "chiaroscuro_doc",
    add_soundscape: bool = True,
    auto_cards: bool = True,
    jl_cut_mode: str = "j_cut",
    jl_overlap_sec: float = 0.25,
) -> dict[str, Any]:
    """Upgrade any explainer brief into a human-directed, non-robotic editorial brief.

    - Applies a cohesive built-in `cinema_look` (color science + scrim calibration).
    - Analyzes every scene's narrative intent to vary transition styles and
      camera choreography (zero back-to-back identical camera moves).
    - Spaces `highlight_card`s with human editorial restraint (at most 1 per 2-3 scenes).
    - Attaches procedural soundscape cues (`room_tone` + SFX markers) and a
      J-cut / L-cut audio overlap plan.
    """
    look = get_cinema_look(cinema_look)
    out = dict(brief)
    out["style"] = "editorial"
    out["captions_style"] = "plain"
    out["cinema_look"] = look["name"]
    out["humanized"] = True
    out.setdefault("beat_transition", "none")
    out.setdefault("clean_tts", True)
    out.setdefault("auto_tech_cards", True)
    out.setdefault("j_cut", True)
    from .subtitles_style import theme_for_cinema_look
    out.setdefault("subtitle_theme", theme_for_cinema_look(look["name"]))

    scenes_in = list(out.get("scenes") or [])
    directed_scenes: list[dict[str, Any]] = []
    director_notes: list[dict[str, Any]] = []
    sfx_events: list[dict[str, Any]] = []

    prev_move: str | None = None
    last_card_scene = -3  # Ensures cards are spaced >= 2 scenes apart
    global_beat = 0
    cursor_time = 0.0

    for si, sc in enumerate(scenes_in):
        sc_copy = dict(sc)
        text = str(sc_copy.get("text") or sc_copy.get("script") or "")
        dur = float(sc_copy.get("duration") or estimate_reading_duration(text))
        analysis = analyze_narrative_beat(
            text, beat_index=si, total_beats=len(scenes_in), is_scene_start=True
        )
        intent = analysis["intent"]

        # Vary inter-scene transitions naturally (avoid identical cuts everywhere)
        if si > 0 and "transition" not in sc_copy:
            sc_copy["transition"] = analysis["recommended_transition"]
            if analysis["recommended_transition"] != "none":
                sc_copy["transition_duration"] = analysis["recommended_transition_duration"]

        # Direct camera choreography for each beat (or whole scene)
        beats = sc_copy.get("beats")
        if isinstance(beats, list) and beats:
            directed_beats = []
            for bi, beat in enumerate(beats):
                b_copy = dict(beat)
                b_dur = max(0.5, float(b_copy.get("end", 3.0)) - float(b_copy.get("start", 0.0)))
                move = choose_narrative_camera_move(
                    intent=intent if bi == 0 else "narrative",
                    beat_index=global_beat,
                    prev_move=prev_move,
                    duration=b_dur,
                )
                b_copy.setdefault("camera_move", move)
                prev_move = b_copy["camera_move"]
                directed_beats.append(b_copy)
                global_beat += 1
            sc_copy["beats"] = directed_beats
        else:
            move = choose_narrative_camera_move(
                intent=intent,
                beat_index=global_beat,
                prev_move=prev_move,
                duration=dur,
            )
            sc_copy.setdefault("camera_move", move)
            prev_move = sc_copy["camera_move"]
            global_beat += 1

        # Editorial HUD restraint: place highlight_card only on high-value beats spaced apart
        if auto_cards and not sc_copy.get("highlight_card"):
            if intent in ("data_point", "quote", "revelation") and (si - last_card_scene) >= 2:
                card_title = _extract_card_title(text, intent)
                if card_title:
                    sc_copy["highlight_card"] = {
                        "title": card_title,
                        "duration": 2.8,
                    }
                    last_card_scene = si

        # Record soundscape cues
        if add_soundscape and analysis["recommended_sfx"]:
            sfx_events.append({
                "scene_id": sc_copy.get("id", f"scene_{si + 1}"),
                "time": round(cursor_time, 3),
                "kind": analysis["recommended_sfx"],
                "reason": intent,
            })

        sc_copy["_narrative_intent"] = intent
        sc_copy["_energy"] = analysis["energy"]
        directed_scenes.append(sc_copy)

        director_notes.append({
            "scene_id": sc_copy.get("id", f"scene_{si + 1}"),
            "intent": intent,
            "energy": analysis["energy"],
            "transition": sc_copy.get("transition", "none"),
            "camera_move": (
                sc_copy["beats"][0]["camera_move"]
                if isinstance(sc_copy.get("beats"), list) and sc_copy["beats"]
                else sc_copy.get("camera_move")
            ),
            "has_highlight_card": bool(sc_copy.get("highlight_card")),
        })
        cursor_time += dur

    out["scenes"] = directed_scenes
    if out.get("auto_chapter_cards") or any(
        isinstance(s, dict) and (s.get("chapter_card") or s.get("chapter_title") or s.get("act"))
        for s in directed_scenes
    ):
        from .parallax import auto_attach_chapter_cards
        out = auto_attach_chapter_cards(out)
    if add_soundscape:
        _mb_map = {
            "midnight_tech": "tech_pulse",
            "noir_slate": "tech_pulse",
            "warm_archive": "contemplative_pad",
            "kodak_warm": "contemplative_pad",
        }
        out["soundscape"] = {
            "room_tone": "warm_studio" if look["name"] in ("chiaroscuro_doc", "kodak_warm", "editorial_clean", "crisp_cinema") else "night_air",
            "room_tone_db": -38.0,
            "music_bed": out.get("music") or _mb_map.get(look["name"], "documentary_drone"),
            "music_bed_db": -22.0,
            "sidechain_ducking": True,
            "duck_ratio": 4.5,
            "duck_attack_ms": 35,
            "duck_release_ms": 420,
            "sfx_events": sfx_events,
            "jl_cuts": plan_jl_cut_audio(directed_scenes, mode=jl_cut_mode, overlap_sec=jl_overlap_sec),
        }
    out["director_notes"] = director_notes
    return out


def direct_from_script(
    script_text: str,
    title: str = "Editorial Essay",
    cinema_look: str = "chiaroscuro_doc",
    aspect: str = "9:16",
    profile: str | None = "low_spec",
) -> dict[str, Any]:
    """Turn a raw Arabic or English essay script into a fully directed editorial brief.

    Splits paragraphs/sentences into natural narrative scenes with reading-time
    durations, organic camera choreography, varied transitions, restrained
    highlight cards, and a cinema soundscape plan.
    """
    raw = (script_text or "").strip()
    if not raw:
        raise ValueError("script_text must be non-empty")

    # Split into paragraphs first; split overly long paragraphs by sentence punctuation
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+|\n+", raw) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para.split()) <= 28:
            chunks.append(para)
        else:
            sents = [s.strip() for s in re.split(r"(?<=[.!؟?])\s+", para) if s.strip()]
            buf = ""
            for s in sents:
                if buf and len((buf + " " + s).split()) > 24:
                    chunks.append(buf)
                    buf = s
                else:
                    buf = f"{buf} {s}".strip()
            if buf:
                chunks.append(buf)

    scenes: list[dict[str, Any]] = []
    for idx, ch in enumerate(chunks):
        dur = estimate_reading_duration(ch, min_duration=2.4, max_duration=14.0, pacing_slack=0.6)
        scenes.append({
            "id": f"scene_{idx + 1}",
            "text": ch,
            "duration": dur,
        })

    base_brief: dict[str, Any] = {
        "title": title,
        "style": "editorial",
        "aspect": aspect,
        "captions_style": "plain",
        "parallel": True,
        "cache_scenes": True,
        "scenes": scenes,
    }
    directed = humanize_brief(base_brief, cinema_look=cinema_look, add_soundscape=True, auto_cards=True)
    if profile:
        directed = apply_profile_to_brief(directed, profile)
    return directed
