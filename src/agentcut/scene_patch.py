"""Surgical Scene Patching, SHA-256 Scene Caching & Hook A/B Generator (v3.4.4).

Solves the "regenerate the whole video to fix one number" bottleneck on
constrained hardware (8GB RAM, CPU-only, HDD):
- `compute_scene_fingerprint(scene, spec, timing)`: deterministic SHA-256
  digest covering the scene's visual content, narration duration, word
  timings, palette, format, caption style, motion, physics, camera move,
  and transition.
- `patch_scene(spec, scene_id, updates)`: returns a patched copy of `spec`
  updating only the target scene while recording which scene fingerprints
  changed vs. stayed cached.
- `diff_scene_fingerprints(old_spec, new_spec, timings)`: lists `unchanged`
  (cache hits) and `invalidated` (needs re-capture) scene IDs.
- `generate_hook_ab_variants(spec, variants=None)`: produces 2-4 A/B hook
  variants of Scene 1 (varying hook title, badge, icon, palette, or
  caption_style) so you can preview or render only the first 3-5 seconds
  without re-rendering the rest of the video.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .brand_kits import apply_brand_kit


def compute_scene_fingerprint(
    scene: dict[str, Any],
    spec: dict[str, Any],
    timing: dict[str, Any] | None = None,
) -> str:
    """Compute a deterministic 16-hex-char SHA-256 fingerprint for one scene."""
    applied = apply_brand_kit(spec)
    tm = timing or {}
    # Normalize relative word offsets so shifting earlier scenes doesn't
    # invalidate a later scene's visual frame cache when its own duration/words match
    sc_start = float(tm.get("start", 0.0))
    rel_words = [
        {
            "w": w.get("word", ""),
            "s": round(float(w.get("start", sc_start)) - sc_start, 3),
            "e": round(float(w.get("end", sc_start)) - sc_start, 3),
        }
        for w in (tm.get("words") or [])
    ]
    payload = {
        "scene": scene,
        "duration": round(float(tm.get("duration", 0.0)), 3),
        "rel_words": rel_words,
        "global_visual": {
            "palette": applied.get("palette", "cream"),
            "format": applied.get("format", "vertical"),
            "width": applied.get("width"),
            "height": applied.get("height"),
            "fps": int(applied.get("fps", 30)),
            "series_tag": applied.get("series_tag", "طب ازاي؟"),
            "watermark": applied.get("watermark", ""),
            "caption_style": applied.get("caption_style", "word_pop"),
            "motion": applied.get("motion", "normal"),
            "physics": applied.get("physics", "spring"),
            "camera_move": applied.get("camera_move", "none"),
            "transition": applied.get("transition", "fade"),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_spec_scene_manifest(
    spec: dict[str, Any],
    timings: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build per-scene cache manifest {scene_id: {fingerprint, index, start, duration, frame_start, frame_count}}."""
    applied = apply_brand_kit(spec)
    fps = int(applied.get("fps", 30))
    tm_by_id = {t["id"]: t for t in (timings or [])}
    manifest: dict[str, dict[str, Any]] = {}

    for idx, sc in enumerate(applied.get("scenes") or []):
        sid = sc["id"]
        tm = tm_by_id.get(sid) or {"id": sid, "start": idx * 2.0, "duration": 2.0, "words": []}
        start = float(tm.get("start", 0.0))
        dur = float(tm.get("duration", 0.0))
        f_start = int(round(start * fps))
        f_end = int(round((start + dur) * fps))
        manifest[sid] = {
            "scene_id": sid,
            "index": idx,
            "fingerprint": compute_scene_fingerprint(sc, applied, tm),
            "start": round(start, 3),
            "duration": round(dur, 3),
            "frame_start": f_start,
            "frame_end": max(f_start, f_end),
            "frame_count": max(0, f_end - f_start),
        }
    return manifest


def diff_scene_fingerprints(
    old_spec: dict[str, Any],
    new_spec: dict[str, Any],
    old_timings: list[dict[str, Any]] | None = None,
    new_timings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare two specs and identify cached (unchanged) vs invalidated scenes."""
    old_m = compute_spec_scene_manifest(old_spec, old_timings)
    new_m = compute_spec_scene_manifest(new_spec, new_timings)
    unchanged: list[str] = []
    invalidated: list[str] = []
    for sid, entry in new_m.items():
        old_entry = old_m.get(sid)
        if old_entry and old_entry["fingerprint"] == entry["fingerprint"]:
            unchanged.append(sid)
        else:
            invalidated.append(sid)
    total = max(1, len(new_m))
    saved_pct = round(100.0 * len(unchanged) / total, 1)
    return {
        "unchanged": unchanged,
        "invalidated": invalidated,
        "cache_hit_ratio": round(len(unchanged) / total, 3),
        "saved_render_percent": saved_pct,
        "manifest": new_m,
    }


def patch_scene(
    spec: dict[str, Any],
    scene_id: str,
    updates: dict[str, Any],
    timings: list[dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Surgically update a single scene by ID and return the patched spec plus
    a cache diff showing that all untouched scenes retain their SHA-256 fingerprint."""
    patched = copy.deepcopy(spec)
    target = None
    for sc in patched.get("scenes") or []:
        if sc.get("id") == scene_id:
            target = sc
            break
    if target is None:
        return False, {
            "code": "SCENE_NOT_FOUND",
            "message": f"scene {scene_id!r} not found in spec",
        }

    for k, v in updates.items():
        if k == "id":
            continue
        target[k] = copy.deepcopy(v)

    diff = diff_scene_fingerprints(spec, patched, timings, timings)
    return True, {
        "scene_id": scene_id,
        "patched_spec": patched,
        "unchanged_scenes": diff["unchanged"],
        "invalidated_scenes": diff["invalidated"],
        "saved_render_percent": diff["saved_render_percent"],
        "manifest": diff["manifest"],
    }


def generate_hook_ab_variants(
    spec: dict[str, Any],
    variants: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate A/B hook variants for the opening scene (first 3-5 seconds).

    Each returned item is {variant_id, label, hook_only_spec, full_spec,
    hook_fingerprint} where `hook_only_spec` contains ONLY Scene 1 for fast
    3-second preview rendering, while `full_spec` shares the exact same
    fingerprints for Scenes 2..N.
    """
    base = apply_brand_kit(spec)
    scenes = base.get("scenes") or []
    if not scenes:
        return []

    first = scenes[0]
    default_variants = variants or [
        {
            "variant_id": "A_question_hook",
            "label": "هوك سؤال مباشر + شارة",
            "scene_updates": {
                "title": first.get("title") or "ليه ده بيحصل؟",
                "badge": first.get("badge") or "؟!",
                "caption_style": "highlighter_bar",
            },
        },
        {
            "variant_id": "B_shock_stat_hook",
            "label": "هوك رقم صادم + أيقونة",
            "scene_updates": {
                "title": first.get("subtitle") or first.get("title") or "الحقيقة المخفية",
                "badge": first.get("badge") or "×5",
                "icon": "bolt",
                "caption_style": "karaoke_pill",
            },
        },
        {
            "variant_id": "C_dark_contrast_hook",
            "label": "هوك متباين بإطار تحريري",
            "scene_updates": {
                "title": first.get("title") or "السر العلمي",
                "icon": "sparkles",
                "caption_style": "editorial_box",
            },
        },
    ]

    results: list[dict[str, Any]] = []
    for var in default_variants:
        vid = str(var.get("variant_id", f"var_{len(results) + 1}"))
        full_copy = copy.deepcopy(base)
        full_copy["id"] = f"{base.get('id', 'reel')}_{vid}"
        sc_updates = var.get("scene_updates") or {}
        for k, v in sc_updates.items():
            full_copy["scenes"][0][k] = copy.deepcopy(v)
        if "palette" in var:
            full_copy["palette"] = var["palette"]

        hook_only = copy.deepcopy(full_copy)
        hook_only["scenes"] = [copy.deepcopy(full_copy["scenes"][0])]
        fp = compute_scene_fingerprint(full_copy["scenes"][0], full_copy)
        results.append({
            "variant_id": vid,
            "label": str(var.get("label", vid)),
            "hook_fingerprint": fp,
            "hook_only_spec": hook_only,
            "full_spec": full_copy,
        })
    return results
