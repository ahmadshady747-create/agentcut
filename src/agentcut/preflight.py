"""Pre-Render Preflight Auditor & Environment Doctor for AgentCut (v3.4.5).

Provides:
- `preflight_audit(spec, base_dir='.')`: comprehensive pre-render check
  verifying spec schema, scene type & icon variety (no consecutive
  duplicate templates), real narration audio pace (`measure_narration_pace`
  with script-trim recommendations instead of audio speed-up), and HTML
  `setTime(t)` determinism / WCAG contrast (`audit_html_motion_continuity`).
- `doctor_environment()`: checks Python version, FFmpeg/FFprobe availability,
  Playwright/Chromium availability, `/dev/shm` RAM-disk space, and bundled
  OFL Arabic fonts (`Cairo.ttf`, `NotoSansArabic-Bold.ttf`).
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from .brand_kits import apply_brand_kit
from .browser_render import PLAYWRIGHT_AVAILABLE, chromium_available, get_fast_scratch_dir
from .flat_reels import (
    build_video_html,
    compute_scene_timings,
    even_word_timings,
    measure_narration_pace,
    validate_reel_spec,
)
from .visual_qc import audit_html_motion_continuity


def preflight_audit(
    spec: dict[str, Any],
    base_dir: str = ".",
    require_audio_files: bool = True,
) -> dict[str, Any]:
    """Run pre-render audit on a spec dict before launching browser capture."""
    applied = apply_brand_kit(spec)
    ok_spec, spec_errors = validate_reel_spec(applied)
    warnings: list[str] = []
    errors: list[str] = list(spec_errors)

    scenes = applied.get("scenes") or []
    # Check scene template variety & icon variety
    consecutive_dup_types: list[str] = []
    used_types: list[str] = []
    used_icons: list[str] = []
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        st = str(sc.get("type", ""))
        used_types.append(st)
        if i > 0 and st and st == used_types[i - 1]:
            consecutive_dup_types.append(f"{scenes[i - 1].get('id')}->{sc.get('id')} ({st})")
        ic = str(sc.get("icon", ""))
        if ic:
            used_icons.append(ic)

    if consecutive_dup_types:
        warnings.append(
            "consecutive scenes share identical template type: "
            + ", ".join(consecutive_dup_types)
        )

    distinct_types = len(set(used_types))
    variety_score = round(100.0 * distinct_types / max(1, len(scenes)), 1)

    # Probe audio timings if files exist, otherwise synthesize 2.5s preview timings
    timings, total_dur, timing_errors = compute_scene_timings(applied, base_dir)
    audio_probed = not bool(timing_errors)
    if timing_errors:
        if require_audio_files:
            errors.extend(timing_errors)
        else:
            warnings.extend(timing_errors)
        # Build estimated timings from 1.85 wps natural Arabic pace for dry-run HTML audit
        timings = []
        cursor = 0.0
        for sc in scenes:
            if not isinstance(sc, dict):
                continue
            w_list = str(sc.get("text", "")).split()
            est_dur = round(max(1.5, len(w_list) / 1.85), 3)
            timings.append({
                "id": sc.get("id", "s"),
                "start": round(cursor, 3),
                "duration": est_dur,
                "words": even_word_timings(w_list, cursor, cursor + est_dur),
            })
            cursor += est_dur
        total_dur = round(cursor, 3)

    pace = measure_narration_pace(
        applied, timings, target_duration=applied.get("target_duration")
    )
    warnings.extend(pace.get("warnings") or [])

    html_audit = {"ok": False, "issues": ["spec invalid"]}
    if ok_spec and timings:
        html = build_video_html(applied, timings, embed_font=False)
        html_audit = audit_html_motion_continuity(html, applied)
        if not html_audit["ok"]:
            errors.extend(html_audit["issues"])

    return {
        "ok": len(errors) == 0,
        "audio_probed": audio_probed,
        "total_duration": total_dur,
        "scene_count": len(scenes),
        "distinct_scene_types": distinct_types,
        "template_variety_score": variety_score,
        "consecutive_duplicate_types": consecutive_dup_types,
        "pace": pace,
        "html_audit": html_audit,
        "warnings": warnings,
        "errors": errors,
    }


def doctor_environment(probe_browser: bool = False) -> dict[str, Any]:
    """Inspect local runtime readiness for AgentCut v3.4.5."""
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    node_path = shutil.which("node")
    chromium_bin = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )

    here = os.path.dirname(os.path.abspath(__file__))
    fonts_dir = os.path.normpath(os.path.join(here, "..", "..", "fonts"))
    cairo_ok = os.path.isfile(os.path.join(fonts_dir, "Cairo.ttf"))
    noto_ok = os.path.isfile(os.path.join(fonts_dir, "NotoSansArabic-Bold.ttf"))

    shm_free_mb = 0.0
    if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK):
        try:
            st = os.statvfs("/dev/shm")
            shm_free_mb = round((st.f_bavail * st.f_frsize) / (1024 * 1024), 1)
        except OSError:
            pass

    browser_launch_ok = chromium_available() if probe_browser else PLAYWRIGHT_AVAILABLE
    core_ok = bool(ffmpeg_path and ffprobe_path and cairo_ok)

    return {
        "ok": core_ok,
        "python_version": sys.version.split()[0],
        "ffmpeg": ffmpeg_path,
        "ffprobe": ffprobe_path,
        "node": node_path,
        "chromium_binary": chromium_bin,
        "playwright_installed": PLAYWRIGHT_AVAILABLE,
        "browser_ready": browser_launch_ok,
        "fonts": {
            "Cairo.ttf": cairo_ok,
            "NotoSansArabic-Bold.ttf": noto_ok,
        },
        "ram_disk_shm_free_mb": shm_free_mb,
        "fast_scratch_dir": get_fast_scratch_dir(),
    }
