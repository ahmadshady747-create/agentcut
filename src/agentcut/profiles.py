"""Hardware-aware render profiles, Meta Muse VM support, RAM-disk scratch routing, and CPU thread governor (v3.3.10).

Zero external dependencies (Python stdlib only). Provides named render presets
tailored for:
- `proxy`: Ultra-fast 480p 15fps preview for rapid agent layout & timing checks.
- `draft`: Fast 720p 24fps draft for review before final master render.
- `muse_vm`: Specifically tuned for Meta Muse Secure VM containers (2 vCPUs AMD EPYC Turin,
  8GB host / cgroup-capped 2-4GB container RAM, 100GB SSD, CPU-only) — uses single-worker
  720p 24fps `superfast` encoding with `threads=2` to prevent cgroup OOM kills and
  2-core thrashing.
- `low_spec`: Memory-safe 1080p profile for 4-core / 8GB RAM / HDD / CPU-only machines.
- `master` / `cinema`: Production 1080p deliverables.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
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
    "muse_vm": {
        "name": "muse_vm",
        "description": "Tuned for Meta Muse Secure VM (2 vCPUs EPYC Turin, 8GB host / cgroup-capped container RAM, 100GB SSD, CPU-only).",
        "scale_factor": 0.67,
        "fps": 24,
        "video_preset": "superfast",
        "video_crf": 23,
        "grain_mode": "off",
        "max_workers": 1,
        "ffmpeg_threads": 2,
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
    elif key in ("muse", "muse-vm", "muse_container"):
        key = "muse_vm"
    if key not in RENDER_PROFILES:
        valid = ", ".join(sorted(RENDER_PROFILES))
        raise ValueError(f"unknown render profile {name!r}; choose from: {valid}")
    return dict(RENDER_PROFILES[key])


def _detect_total_ram_gb() -> float | None:
    """Best-effort total host/VM RAM in GiB using stdlib only."""
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
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0:
                return round((pages * page_size) / (1024 ** 3), 2)
        except (ValueError, OSError):
            pass
    return None


def _detect_cgroup_ram_gb(
    cgroup_v2_path: str = "/sys/fs/cgroup/memory.max",
    cgroup_v1_path: str = "/sys/fs/cgroup/memory/memory.limit_in_bytes",
) -> float | None:
    """Detect container cgroup memory cap (e.g. systemd-nspawn / Docker) in GiB."""
    for path in (cgroup_v2_path, cgroup_v1_path):
        if os.path.isfile(path):
            try:
                raw = open(path, encoding="utf-8").read().strip()
                if raw and raw != "max":
                    val = int(raw)
                    # Ignore kernel "unlimited" sentinel values (>= 256 TiB)
                    if 64 * 1024 * 1024 <= val < (256 * 1024 ** 4):
                        return round(val / (1024 ** 3), 2)
            except (OSError, ValueError):
                pass
    return None


def detect_muse_environment() -> bool:
    """Return True when running inside a Meta Muse Secure VM / hatch runtime cell."""
    if os.environ.get("AGENTCUT_MUSE_ENV", "").strip().lower() in ("1", "true", "yes"):
        return True
    if os.path.isdir("/run/hatch") or os.path.isdir("/opt/hatch"):
        return True
    for env_k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if "hatch-egress-proxy" in os.environ.get(env_k, "").lower():
            return True
    if os.path.isfile("/etc/hosts"):
        try:
            if "hatch-egress-proxy" in open("/etc/hosts", encoding="utf-8", errors="ignore").read().lower():
                return True
        except OSError:
            pass
    return False


def get_fast_temp_root(min_free_mb: int = 384) -> str:
    """Return a fast RAM-backed scratch directory (`/dev/shm`) when available, else `tempfile.gettempdir()`."""
    env_override = os.environ.get("AGENTCUT_SCRATCH_DIR")
    if env_override and os.path.isdir(env_override) and os.access(env_override, os.W_OK):
        return env_override
    shm = "/dev/shm"
    if os.path.isdir(shm) and os.access(shm, os.W_OK) and hasattr(os, "statvfs"):
        try:
            st = os.statvfs(shm)
            free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
            if free_mb >= float(min_free_mb):
                return shm
        except OSError:
            pass
    return tempfile.gettempdir()


def compute_ffmpeg_threads_per_worker(max_workers: int = 2) -> int:
    """Compute optimal `-threads` per FFmpeg process to prevent CPU thrashing on 2-core / 4-core machines."""
    cores = max(1, int(os.cpu_count() or 2))
    workers = max(1, int(max_workers or 1))
    return max(1, min(4, cores // workers))


def detect_hardware_profile() -> dict[str, Any]:
    """Inspect local CPU count, host RAM, cgroup cap, and Muse runtime markers."""
    cpu_count = os.cpu_count() or 2
    host_ram_gb = _detect_total_ram_gb()
    cgroup_ram_gb = _detect_cgroup_ram_gb()
    is_muse = detect_muse_environment()

    if host_ram_gb is not None and cgroup_ram_gb is not None:
        effective_ram_gb = min(host_ram_gb, cgroup_ram_gb)
    else:
        effective_ram_gb = cgroup_ram_gb if cgroup_ram_gb is not None else host_ram_gb

    if is_muse or (cpu_count <= 2 and effective_ram_gb is not None and effective_ram_gb <= 4.5):
        recommended_profile = "muse_vm"
        safe_workers = 1
    elif effective_ram_gb is not None and effective_ram_gb <= 9.0:
        recommended_profile = "low_spec"
        safe_workers = min(2, max(1, cpu_count // 2))
    elif cpu_count <= 4:
        recommended_profile = "low_spec"
        safe_workers = min(2, max(1, cpu_count // 2))
    elif effective_ram_gb is not None and effective_ram_gb >= 24.0 and cpu_count >= 8:
        recommended_profile = "master"
        safe_workers = min(6, max(2, cpu_count // 2))
    else:
        recommended_profile = "master"
        safe_workers = min(4, max(2, cpu_count // 2))

    return {
        "cpu_cores": cpu_count,
        "ram_gb": effective_ram_gb,
        "host_ram_gb": host_ram_gb,
        "cgroup_ram_gb": cgroup_ram_gb,
        "cgroup_limited": bool(cgroup_ram_gb is not None and host_ram_gb is not None and cgroup_ram_gb < host_ram_gb),
        "is_muse_env": is_muse,
        "recommended_profile": recommended_profile,
        "safe_max_workers": safe_workers,
        "threads_per_worker": compute_ffmpeg_threads_per_worker(safe_workers),
        "profile_settings": get_render_profile(recommended_profile),
    }


def apply_profile_to_brief(brief: dict[str, Any], profile_name: str) -> dict[str, Any]:
    """Return a copy of an explainer/video brief with render profile defaults applied."""
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
    if prof["max_workers"] == 1:
        out["parallel"] = False
    return out


def benchmark_render_speed(width: int = 720, height: int = 1280) -> dict[str, Any]:
    """Run a fast, zero-dependency micro-benchmark of constrained-hardware optimizations."""
    from .compositor import write_cinematic_gradient
    from .validate import probe_media

    hw = detect_hardware_profile()
    scratch_root = get_fast_temp_root()
    tmpfs_active = (scratch_root == "/dev/shm")
    threads_per_worker = hw["threads_per_worker"]

    with tempfile.TemporaryDirectory(prefix="agentcut-bench-", dir=scratch_root) as td:
        g1 = os.path.join(td, "g1.png")
        g2 = os.path.join(td, "g2.png")

        t0 = time.perf_counter()
        write_cinematic_gradient(g1, width, height)
        cold_or_warm_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        t1 = time.perf_counter()
        write_cinematic_gradient(g2, width, height)
        cached_grad_ms = round((time.perf_counter() - t1) * 1000.0, 2)

        t2 = time.perf_counter()
        p_info = probe_media(g1)
        fast_probe_ms = round((time.perf_counter() - t2) * 1000.0, 3)

        out_mp4 = os.path.join(td, "bench.mp4")
        t3 = time.perf_counter()
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-threads", str(threads_per_worker),
                "-f", "lavfi", "-i", "color=c=steelblue:s=360x640:r=15:d=1.0",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                out_mp4,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        encode_1s_ms = round((time.perf_counter() - t3) * 1000.0, 2)

    return {
        "cpu_cores": hw["cpu_cores"],
        "ram_gb": hw["ram_gb"],
        "host_ram_gb": hw["host_ram_gb"],
        "cgroup_ram_gb": hw["cgroup_ram_gb"],
        "is_muse_env": hw["is_muse_env"],
        "recommended_profile": hw["recommended_profile"],
        "safe_max_workers": hw["safe_max_workers"],
        "threads_per_worker": threads_per_worker,
        "scratch_root": scratch_root,
        "tmpfs_active": tmpfs_active,
        "first_gradient_ms": cold_or_warm_ms,
        "cached_gradient_ms": cached_grad_ms,
        "fast_probe_ms": fast_probe_ms,
        "probe_valid": bool(p_info and p_info.get("width") == width),
        "encode_1s_proxy_ms": encode_1s_ms,
    }
