"""AgentMedia Bundle Bridge & Auto-Beat Editorial Director (v2.0.0).

Standardizes the handoff contract between AgentMedia (Whisper word timings,
voiceover narration, generated/curated visual assets) and AgentCut's editorial
documentary engine. Zero external dependencies.
"""

from __future__ import annotations

import os
from typing import Any

from .profiles import apply_profile_to_brief


def auto_split_beats(
    word_timings: list[dict[str, Any]] | None,
    total_duration: float,
    min_beat: float = 2.5,
    max_beat: float = 4.0,
    images: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Split a scene duration into natural 2.5-4.0s editorial visual beats.

    Uses inter-word pause gaps from Whisper `word_timings` when available so
    visual cuts land cleanly on natural speech pauses rather than mid-word.
    Falls back to even tiling when no word timings are provided.
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
) -> dict[str, Any]:
    """Convert an AgentMedia bundle manifest into a ready-to-render editorial brief.

    - Automatically splits long scenes into 2.5-4.0s beats using Whisper word_timings.
    - Wires scene-level highlight_card, word_timings, and full-sentence Arabic pills.
    - Applies the selected hardware render profile (`proxy`, `low_spec`, `master`, etc.).
    """
    title = str(bundle.get("title") or "Editorial Essay")
    brief_aspect = aspect or bundle.get("aspect") or "9:16"
    scenes_out: list[dict[str, Any]] = []

    for idx, sc in enumerate(bundle.get("scenes", [])):
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
        "scenes": scenes_out,
    }
    if bundle.get("music"):
        brief["music"] = bundle["music"]
    if bundle.get("music_volume") is not None:
        brief["music_volume"] = bundle["music_volume"]
    if bundle.get("font"):
        brief["font"] = bundle["font"]

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
    return brief


def produce_from_bundle(
    bundle: dict[str, Any],
    output_path: str,
    aspect: str | None = None,
    profile: str | None = None,
    parallel: bool = True,
    qc_audit: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Validate an AgentMedia bundle, build an editorial brief, render it, and optionally run QC."""
    ok_val, errs = validate_media_bundle(bundle, check_files=True)
    if not ok_val:
        return False, {"error": "invalid_media_bundle", "errors": errs}

    from .render import produce_explainer

    brief = bundle_to_editorial_brief(bundle, aspect=aspect, profile=profile, parallel=parallel)
    ok, info = produce_explainer(brief, output_path)
    result = dict(info) if isinstance(info, dict) else {"result": info}
    result["bundle_scenes"] = len(brief["scenes"])
    result["render_profile"] = brief.get("render_profile", "default")

    if ok and qc_audit:
        from .qc import audit_video_quality
        result["qc"] = audit_video_quality(output_path)

    return ok, result
