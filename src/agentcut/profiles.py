"""Hardware-aware render profiles and automatic resource throttling (v2.0.0).

Zero external dependencies (Python stdlib only). Provides named render presets
tailored for instant AI-agent preview loops (`proxy`), constrained CPU/RAM/HDD
machines (`low_spec`), standard social/YouTube delivery (`master`), and
archival/4K exports (`cinema`), plus live CPU/RAM auto-detection.
"""

from __future__ import annotations

import os
from typing import Any


RENDER_PROFILES: dict[str, dict[str, Any]] = {
    "proxy": {
        "name": "proxy",
        "description": "Ultra-fast 480p 15fps preview for rapid agent layout & timing checks.",
        "scale_factor": 0.45,
        "fps": 15,
        "video_preset": "ultrafast",
        "video_crf": 30,
        "grain_mode": "off",
        "max_workers": 2,
        "cache_scenes": True,
        "audio_bitrate": "96k",
    },
    "draft": {
        "name": "draft",
        "description": "Fast 720p 24fps draft for review before final master render.",
        "scale_factor": 0.67,
        "fps": 24,
        "video_preset": "veryfast",
        "video_crf": 26,
        "grain_mode": "off",
        "max_workers": 2,
        "cache_scenes": True,
        "audio_bitrate": "128k",
    },
    "low_spec": {
        "name": "low_spec",
        "description": "Memory-safe 1080p profile tuned for 4-core / 8GB RAM / HDD / CPU-only machines.",
        "scale_factor": 1.0,
        "fps": 30,
        "video_preset": "veryfast",
        "video_crf": 22,
        "grain_mode": "off",
        "max_workers": 2,
        "cache_scenes": True,
        "audio_bitrate": "160k",
    },
    "master": {
        "name": "master",
        "description": "Balanced 1080p 30fps production master for YouTube, Reels, and client delivery.",
        "scale_factor": 1.0,
        "fps": 30,
        "video_preset": "medium",
        "video_crf": 20,
        "grain_mode": "frozen",
        "max_workers": 4,
        "cache_scenes": True,
        "audio_bitrate": "192k",
    },
    "cinema": {
        "name": "cinema",
        "description": "High-bitrate archival master with slow encoding and 256k audio.",
        "scale_factor": 1.0,
        "fps": 30,
        "video_preset": "slow",
        "video_crf": 17,
        "grain_mode": "frozen",
        "max_workers": 4,
        "cache_scenes": True,
        "audio_bitrate": "256k",
    },
}


def list_render_profiles() -> list[dict[str, Any]]:
    """Return metadata for all built-in render profiles."""
    return [dict(v) for v in RENDER_PROFILES.values()]


def get_render_profile(name: str) -> dict[str, Any]:
    """Fetch a named render profile; raises ValueError on unknown name."""
    key = str(name or "").strip().lower()
    if key == "standard":
        key = "master"
    if key not in RENDER_PROFILES:
        valid = ", ".join(sorted(RENDER_PROFILES))
        raise ValueError(f"unknown render profile {name!r}; choose from: {valid}")
    return dict(RENDER_PROFILES[key])


def _detect_total_ram_gb() -> float | None:
    """Best-effort total system RAM in GiB using stdlib only."""
    # Linux /proc/meminfo
    if os.path.isfile("/proc/meminfo"):
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return round(int(parts[1]) / (1024 * 1024), 2)
        except OSError:
            pass
    # POSIX sysconf fallback
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0:
                return round((pages * page_size) / (1024 ** 3), 2)
        except (ValueError, OSError):
            pass
    return None


def detect_hardware_profile() -> dict[str, Any]:
    """Inspect local CPU count and RAM to recommend safe render concurrency."""
    cpu_count = os.cpu_count() or 2
    ram_gb = _detect_total_ram_gb()

    # Conservative memory-safe worker calculation:
    # Each concurrent FFmpeg 1080p scene worker uses ~1.2–2.0 GB RAM.
    if ram_gb is not None and ram_gb <= 9.0:
        recommended_profile = "low_spec"
        safe_workers = min(2, max(1, cpu_count // 2))
    elif cpu_count <= 4:
        recommended_profile = "low_spec"
        safe_workers = min(2, max(1, cpu_count // 2))
    elif ram_gb is not None and ram_gb >= 24.0 and cpu_count >= 8:
        recommended_profile = "master"
        safe_workers = min(6, max(2, cpu_count // 2))
    else:
        recommended_profile = "master"
        safe_workers = min(4, max(2, cpu_count // 2))

    return {
        "cpu_cores": cpu_count,
        "ram_gb": ram_gb,
        "recommended_profile": recommended_profile,
        "safe_max_workers": safe_workers,
        "profile_settings": get_render_profile(recommended_profile),
    }


def apply_profile_to_brief(brief: dict[str, Any], profile_name: str) -> dict[str, Any]:
    """Return a copy of an explainer/video brief with render profile defaults applied.

    Explicit values already set on `brief` win over profile defaults, except
    `render_profile` which is recorded for downstream telemetry.
    """
    prof = get_render_profile(profile_name)
    out = dict(brief)
    out["render_profile"] = prof["name"]
    if prof["name"] in ("proxy", "draft"):
        out.setdefault("draft", True)
        out.setdefault("scale_factor", prof["scale_factor"])
    else:
        out.setdefault("scale_factor", prof["scale_factor"])
    out.setdefault("fps", prof["fps"])
    out.setdefault("video_preset", prof["video_preset"])
    out.setdefault("grain_mode", prof["grain_mode"])
    out.setdefault("cache_scenes", prof["cache_scenes"])
    out.setdefault("max_workers", prof["max_workers"])
    return out
