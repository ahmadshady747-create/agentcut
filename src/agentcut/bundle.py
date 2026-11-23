"""AgentMedia Bundle Bridge & Auto-Beat Editorial Director (v3.3.12).

Standardizes the handoff contract between AgentMedia (Whisper word timings,
voiceover narration, generated/curated visual assets) and AgentCut's editorial
documentary engine. Zero external dependencies.

Includes character-level LCS script alignment (preserving original Arabic
script spelling and flagging low-confidence words) with Whisper as the free
default and Deepgram as an optional timing provider.
"""

from __future__ import annotations

import os
from typing import Any

from .deepgram_align import align_scene_audio, parse_deepgram_response
from .profiles import apply_profile_to_brief


def auto_split_beats(
    word_timings: list[dict[str, Any]] | None,
    total_duration: float,
    min_beat: float = 2.5,
    max_beat: float = 4.0,
    images: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Split a scene duration into natural 2.5-4.0s editorial visual beats.

    Uses inter-word pause gaps from Whisper/Deepgram `word_timings` when
    available so visual cuts land cleanly on natural speech pauses rather than
    mid-word. Falls back to even tiling when no word timings are provided.
    """
    dur = max(0.5, float(total_duration))
    imgs = [str(x) for x in (images or []) if isinstance(x, str) and x.strip()]

    if dur <= max_beat:
        beat: dict[str, Any] = {"start": 0.0, "end": round(dur, 3)}
        if imgs:
            beat["broll"] = imgs[0]
        return [beat]

    words = sorted(
        [
            {"start": float(w["start"]), "end": float(w["end"])}
            for w in (word_timings or [])
            if isinstance(w, dict) and "start" in w and "end" in w
        ],
        key=lambda x: x["start"],
    )

    cut_points: list[float] = [0.0]
    cursor = 0.0

    while dur - cursor > max_beat + 0.15:
        win_lo = cursor + min_beat
        win_hi = min(cursor + max_beat, dur - min_beat * 0.5)
        if win_hi <= win_lo:
            # Remaining span is too small for two healthy beats; split evenly
            cut = round((cursor + dur) / 2.0, 3)
            if cut > cursor + 0.4 and dur - cut > 0.4:
                cut_points.append(cut)
            break

        # Find the widest natural pause gap between consecutive words inside [win_lo, win_hi]
        best_cut: float | None = None
        best_gap = -1.0
        for idx in range(len(words) - 1):
            gap_start = words[idx]["end"]
            gap_end = words[idx + 1]["start"]
            mid = (gap_start + gap_end) / 2.0
            if win_lo <= mid <= win_hi:
                gap = max(0.0, gap_end - gap_start)
                if gap > best_gap:
                    best_gap = gap
                    best_cut = mid

        if best_cut is None:
            # Fallback: target the middle of [min_beat, max_beat]
            best_cut = cursor + (min_beat + max_beat) / 2.0

        best_cut = round(best_cut, 3)
        cut_points.append(best_cut)
        cursor = best_cut

    cut_points.append(round(dur, 3))

    beats: list[dict[str, Any]] = []
    for i in range(len(cut_points) - 1):
        b_start = cut_points[i]
        b_end = cut_points[i + 1]
        if b_end <= b_start:
            continue
        item: dict[str, Any] = {"start": b_start, "end": b_end}
        if imgs:
            item["broll"] = imgs[i % len(imgs)]
        beats.append(item)
    return beats


def align_bundle_word_timings(
    bundle: dict[str, Any],
    provider: str = "whisper",
    api_key: str | None = None,
    language: str = "ar",
    low_confidence_threshold: float = 0.72,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Align all scenes in an AgentMedia bundle using character-level LCS.

    Preserves the author's original Arabic/English script spelling in
    ``word_timings`` and collects any low-confidence or interpolated words in
    the returned alignment summary. Whisper remains the free default.
    """
    out_bundle = dict(bundle)
    scenes_out: list[dict[str, Any]] = []
    scene_reports: list[dict[str, Any]] = []
    total_words = 0
    total_low_conf = 0

    for idx, sc in enumerate(bundle.get("scenes", [])):
        sc_copy = dict(sc)
        sid = str(sc_copy.get("id") or f"scene_{idx + 1}")
        text = str(sc_copy.get("text") or sc_copy.get("script") or "").strip()
        nar = sc_copy.get("narration") or sc_copy.get("audio")
        raw_timings = sc_copy.get("word_timings")

        if raw_timings is None and isinstance(sc_copy.get("deepgram_response"), dict):
            parsed_dg = parse_deepgram_response(sc_copy["deepgram_response"])
            raw_timings = parsed_dg.get("words")
            if sc_copy.get("duration") is None and parsed_dg.get("duration") is not None:
                sc_copy["duration"] = parsed_dg["duration"]

        should_align = bool(text) and (
            isinstance(raw_timings, list) and len(raw_timings) > 0
            or (provider == "deepgram" and isinstance(nar, str) and bool(nar))
        )
        if should_align:
            dur = sc_copy.get("duration")
            aligned = align_scene_audio(
                script_text=text,
                asr_words=raw_timings if isinstance(raw_timings, list) else None,
                audio_path=nar if isinstance(nar, str) else None,
                provider=provider,
                api_key=api_key,
                language=str(sc_copy.get("language") or language),
                low_confidence_threshold=low_confidence_threshold,
                audio_duration=float(dur) if isinstance(dur, (int, float)) else None,
            )
            sc_copy["word_timings"] = aligned["word_timings"]
            sc_copy["alignment_stats"] = aligned["stats"]
            if aligned["low_confidence_words"]:
                sc_copy["low_confidence_words"] = aligned["low_confidence_words"]
            total_words += aligned["stats"]["total_words"]
            total_low_conf += aligned["stats"]["low_confidence_count"]
            scene_reports.append({
                "scene_id": sid,
                "provider": aligned["provider"],
                "stats": aligned["stats"],
                "low_confidence_words": aligned["low_confidence_words"],
            })
        scenes_out.append(sc_copy)

    out_bundle["scenes"] = scenes_out
    summary = {
        "provider": provider or "whisper",
        "total_words": total_words,
        "low_confidence_count": total_low_conf,
        "scenes": scene_reports,
    }
    return out_bundle, summary


def validate_media_bundle(bundle: dict[str, Any], check_files: bool = True) -> tuple[bool, list[dict[str, str]]]:
    """Validate an AgentMedia bundle manifest before converting to an AgentCut brief."""
    errors: list[dict[str, str]] = []
    if not isinstance(bundle, dict):
        return False, [{"field": "bundle", "message": "Bundle must be a JSON object.", "fix": "Pass a dict with 'scenes'."}]

    scenes = bundle.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append({
            "field": "scenes",
            "message": "Bundle must include a non-empty 'scenes' list.",
            "fix": "Add at least one scene with narration/text and duration or audio file.",
        })
        return False, errors

    for idx, sc in enumerate(scenes):
        prefix = f"scenes[{idx}]"
        if not isinstance(sc, dict):
            errors.append({"field": prefix, "message": "Each scene must be an object.", "fix": "Use a dict per scene."})
            continue
        nar = sc.get("narration") or sc.get("audio")
        text = sc.get("text") or sc.get("script")
        if not nar and not text:
            errors.append({
                "field": prefix,
                "message": "Scene requires either 'narration' (audio path) or 'text'.",
                "fix": "Provide narration audio path or script text.",
            })
        if check_files and isinstance(nar, str) and nar and not os.path.isfile(nar):
            errors.append({
                "field": f"{prefix}.narration",
                "message": f"Narration file not found: {nar!r}",
                "fix": "Ensure AgentMedia generated the audio file at this path.",
            })
        for img_key in ("broll", "image"):
            val = sc.get(img_key)
            if check_files and isinstance(val, str) and val and not os.path.isfile(val):
                errors.append({
                    "field": f"{prefix}.{img_key}",
                    "message": f"Visual asset not found: {val!r}",
                    "fix": "Verify the image/video path exists on disk.",
                })
        images = sc.get("images")
        if images is not None:
            if not isinstance(images, list):
                errors.append({"field": f"{prefix}.images", "message": "'images' must be a list of paths.", "fix": "Pass a list of file paths."})
            elif check_files:
                for ii, img in enumerate(images):
                    if isinstance(img, str) and img and not os.path.isfile(img):
                        errors.append({
                            "field": f"{prefix}.images[{ii}]",
                            "message": f"Beat image not found: {img!r}",
                            "fix": "Verify all beat image paths exist on disk.",
                        })

    return (len(errors) == 0), errors


def bundle_to_editorial_brief(
    bundle: dict[str, Any],
    aspect: str | None = None,
    profile: str | None = None,
    parallel: bool = True,
    min_beat: float = 2.5,
    max_beat: float = 4.0,
    humanize: bool = False,
    cinema_look: str | None = None,
    align_script: bool = True,
    timing_provider: str = "whisper",
    deepgram_api_key: str | None = None,
    low_confidence_threshold: float = 0.72,
) -> dict[str, Any]:
    """Convert an AgentMedia bundle manifest into a ready-to-render editorial brief.

    - Aligns word_timings to original script spelling via character-level LCS
      (preserving Arabic spelling and flagging low-confidence words).
    - Automatically splits long scenes into 2.5-4.0s beats using word_timings.
    - Wires scene-level highlight_card, word_timings, and full-sentence Arabic pills.
    - Applies the selected hardware render profile (`proxy`, `low_spec`, `master`, etc.).
    """
    align_summary: dict[str, Any] | None = None
    working_bundle = bundle
    if align_script:
        working_bundle, align_summary = align_bundle_word_timings(
            bundle,
            provider=timing_provider,
            api_key=deepgram_api_key,
            language=str(bundle.get("language") or "ar"),
            low_confidence_threshold=low_confidence_threshold,
        )

    title = str(working_bundle.get("title") or "Editorial Essay")
    brief_aspect = aspect or working_bundle.get("aspect") or "9:16"
    scenes_out: list[dict[str, Any]] = []

    for idx, sc in enumerate(working_bundle.get("scenes", [])):
        nar = sc.get("narration") or sc.get("audio")
        text = sc.get("text") or sc.get("script") or ""
        wtimings = sc.get("word_timings")
        duration = sc.get("duration")

        # Infer duration from word_timings if not explicit
        if duration is None and isinstance(wtimings, list) and wtimings:
            try:
                duration = max(float(w["end"]) for w in wtimings if isinstance(w, dict) and "end" in w)
            except (ValueError, TypeError):
                duration = None

        sc_out: dict[str, Any] = {
            "id": str(sc.get("id") or f"scene_{idx + 1}"),
            "text": text,
        }
        if nar:
            sc_out["narration"] = nar
        if wtimings:
            sc_out["word_timings"] = wtimings
        if sc.get("broll") or sc.get("image"):
            sc_out["broll"] = sc.get("broll") or sc.get("image")

        # Preserve explicit beats, or auto-split if images + duration are known
        if isinstance(sc.get("beats"), list) and sc["beats"]:
            sc_out["beats"] = sc["beats"]
        elif duration is not None and float(duration) > 0:
            imgs = sc.get("images") or ([sc_out["broll"]] if sc_out.get("broll") else [])
            sc_out["beats"] = auto_split_beats(
                wtimings,
                float(duration),
                min_beat=min_beat,
                max_beat=max_beat,
                images=imgs,
            )

        if isinstance(sc.get("highlight_card"), dict) and sc["highlight_card"].get("title"):
            sc_out["highlight_card"] = dict(sc["highlight_card"])

        scenes_out.append(sc_out)

    brief: dict[str, Any] = {
        "title": title,
        "style": "editorial",
        "aspect": brief_aspect,
        "captions_style": "plain",
        "parallel": bool(parallel),
        "cache_scenes": True,
        "clean_tts": bool(working_bundle.get("clean_tts", True)),
        "scenes": scenes_out,
    }
    if working_bundle.get("music"):
        brief["music"] = working_bundle["music"]
    if working_bundle.get("music_volume") is not None:
        brief["music_volume"] = working_bundle["music_volume"]
    if working_bundle.get("font"):
        brief["font"] = working_bundle["font"]

    if humanize or cinema_look:
        from .director import humanize_brief
        brief = humanize_brief(
            brief,
            cinema_look=cinema_look or "chiaroscuro_doc",
            add_soundscape=True,
            auto_cards=True,
        )
    if profile:
        brief = apply_profile_to_brief(brief, profile)
    if align_summary and align_summary.get("total_words", 0) > 0:
        brief["_alignment_summary"] = align_summary
    return brief


def produce_from_bundle(
    bundle: dict[str, Any],
    output_path: str,
    aspect: str | None = None,
    profile: str | None = None,
    parallel: bool = True,
    qc_audit: bool = False,
    align_script: bool = True,
    timing_provider: str = "whisper",
    deepgram_api_key: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Validate an AgentMedia bundle, build an editorial brief, render it, and optionally run QC."""
    ok_val, errs = validate_media_bundle(bundle, check_files=True)
    if not ok_val:
        return False, {"error": "invalid_media_bundle", "errors": errs}

    from .render import produce_explainer

    brief = bundle_to_editorial_brief(
        bundle,
        aspect=aspect,
        profile=profile,
        parallel=parallel,
        align_script=align_script,
        timing_provider=timing_provider,
        deepgram_api_key=deepgram_api_key,
    )
    align_summary = brief.pop("_alignment_summary", None)
    ok, info = produce_explainer(brief, output_path)
    result = dict(info) if isinstance(info, dict) else {"result": info}
    result["bundle_scenes"] = len(brief["scenes"])
    result["render_profile"] = brief.get("render_profile", "default")
    if align_summary is not None:
        result["alignment_summary"] = align_summary

    if ok and qc_audit:
        from .qc import audit_video_quality
        result["qc"] = audit_video_quality(output_path)

    return ok, result
