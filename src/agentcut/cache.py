"""Deterministic scene hashing and incremental rendering cache.

Inspired by ProCraft's cryptographic hashing tools, adapted for AgentCut multi-scene
timeline caching to skip re-rendering unchanged scenes during iterative edits.
Zero external dependencies.

v2.0.0 upgrades:
- Recursive source-asset file stat hashing (covers nested clips, beats, overlays,
  audio tracks, fonts, and LUTs — not just top-level keys).
- Includes AgentCut version in every scene hash so engine upgrades auto-invalidate
  stale cached chunks.
- Atomic writes via temporary file + os.replace() to prevent corrupt partial MP4s
  under parallel rendering or unexpected termination.
- Automatic LRU cache eviction (configurable via AGENTCUT_CACHE_MAX_MB, default
  2048 MB) and get_cache_stats() telemetry for disk-constrained HDD systems.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


_MEDIA_PATH_KEYS = frozenset({
    "narration", "background", "video", "image", "src", "path",
    "broll", "font", "file", "audio", "lut", "mask", "overlay",
})


def _agentcut_version() -> str:
    try:
        from . import __version__
        return str(__version__)
    except Exception:
        return "0.0.0"


def _collect_existing_paths(obj: Any, found: set[str], parent_key: str = "") -> None:
    """Recursively collect existing local file paths referenced in a scene structure."""
    if isinstance(obj, dict):
        for k, v in sorted(obj.items()):
            _collect_existing_paths(v, found, parent_key=str(k).lower())
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_existing_paths(item, found, parent_key=parent_key)
    elif isinstance(obj, str) and obj and len(obj) < 1024:
        # Check known media/font keys or strings that look like real file paths
        if (parent_key in _MEDIA_PATH_KEYS or "/" in obj or "\\" in obj) and os.path.isfile(obj):
            found.add(os.path.abspath(obj))


def normalize_for_hash(obj: Any) -> Any:
    """Recursively normalize dicts, lists, and numbers for stable hashing.

    Dicts sort by key, floats round to 4 decimals, so two
    equal-valued structures hash identically regardless of
    key order or float noise. (v1.62.0: public — the shared
    normalization for every AgentCut hash.)
    """
    if isinstance(obj, dict):
        return {k: normalize_for_hash(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, (list, tuple)):
        return [normalize_for_hash(x) for x in obj]
    elif isinstance(obj, float):
        return round(obj, 4)
    return obj


# Back-compat alias (the pre-1.62.0 private name).
_normalize_obj = normalize_for_hash


def compute_scene_hash(scene: dict[str, Any], output_params: dict[str, Any] | None = None) -> str:
    """Compute an invariant, deterministic SHA-256 hash for a scene definition.

    Incorporates output parameters (width, height, fps), the current AgentCut
    version, and file metadata (size + mtime_ns) of ALL referenced media/font
    files recursively to guarantee cache invalidation on any source modification.
    Returns a 16-character hexadecimal string.
    """
    hasher = hashlib.sha256()

    normalized_scene = normalize_for_hash(scene)
    normalized_output = normalize_for_hash(output_params or {})

    # Hash structural JSON + AgentCut engine version
    payload = json.dumps(
        {
            "agentcut_version": _agentcut_version(),
            "scene": normalized_scene,
            "output": normalized_output,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    hasher.update(payload.encode("utf-8"))

    # Recursively mix in file stats for all referenced external media/font files
    referenced_paths: set[str] = set()
    _collect_existing_paths(scene, referenced_paths)
    for fpath in sorted(referenced_paths):
        try:
            stat = os.stat(fpath)
            hasher.update(f"{fpath}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
        except OSError:
            continue

    return hasher.hexdigest()[:16]


def get_cache_dir(project_dir: str | Path | None = None) -> Path:
    """Get or create the local AgentCut cache directory."""
    if project_dir:
        cache_dir = Path(project_dir) / ".agentcut_cache"
    else:
        cache_dir = Path(tempfile.gettempdir()) / "agentcut_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def lookup_scene_cache(scene_hash: str, project_dir: str | Path | None = None) -> Path | None:
    """Return path to cached scene MP4 if present and non-empty, else None.

    Touches mtime on hit so LRU eviction preserves actively used chunks.
    """
    cache_dir = get_cache_dir(project_dir)
    target = cache_dir / f"scene_{scene_hash}.mp4"
    if target.exists() and target.is_file() and target.stat().st_size > 1024:
        try:
            os.utime(target, None)
        except OSError:
            pass
        return target
    return None


def _default_max_cache_bytes() -> int:
    raw = os.environ.get("AGENTCUT_CACHE_MAX_MB", "2048").strip()
    try:
        mb = float(raw)
        if mb <= 0:
            return 0
        return int(mb * 1024 * 1024)
    except ValueError:
        return 2048 * 1024 * 1024


def evict_cache(
    project_dir: str | Path | None = None,
    max_bytes: int | None = None,
) -> dict[str, int]:
    """Enforce an LRU size cap on the cache directory.

    Deletes oldest-accessed `scene_*.mp4` and `seg_*.mkv` files until total
    cache size is within `max_bytes`. Returns {"removed": N, "freed_bytes": B}.
    """
    limit = _default_max_cache_bytes() if max_bytes is None else int(max_bytes)
    if limit <= 0:
        return {"removed": 0, "freed_bytes": 0}

    cache_dir = get_cache_dir(project_dir)
    entries: list[tuple[float, int, Path]] = []
    total_size = 0
    for pattern in ("scene_*.mp4", "seg_*.mkv"):
        for item in cache_dir.glob(pattern):
            try:
                st = item.stat()
                entries.append((st.st_mtime, st.st_size, item))
                total_size += st.st_size
            except OSError:
                continue

    if total_size <= limit:
        return {"removed": 0, "freed_bytes": 0}

    # Evict oldest mtime first
    entries.sort(key=lambda e: e[0])
    removed = 0
    freed = 0
    for _, size, item in entries:
        if total_size <= limit:
            break
        try:
            item.unlink()
            total_size -= size
            removed += 1
            freed += size
        except OSError:
            continue
    return {"removed": removed, "freed_bytes": freed}


def store_scene_cache(
    scene_hash: str,
    rendered_mp4: str | Path,
    project_dir: str | Path | None = None,
    max_bytes: int | None = None,
) -> Path:
    """Atomically store a rendered scene MP4 in the cache and enforce LRU cap."""
    cache_dir = get_cache_dir(project_dir)
    src_path = Path(rendered_mp4)
    dst_path = cache_dir / f"scene_{scene_hash}.mp4"
    if src_path.resolve() != dst_path.resolve():
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".scene_{scene_hash}_",
            suffix=".tmp",
            dir=str(cache_dir),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            shutil.copy2(src_path, tmp_path)
            os.replace(tmp_path, dst_path)
            try:
                os.utime(dst_path, None)
            except OSError:
                pass
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    evict_cache(project_dir=project_dir, max_bytes=max_bytes)
    return dst_path


def get_cache_stats(project_dir: str | Path | None = None) -> dict[str, Any]:
    """Return structured statistics about the cache directory."""
    cache_dir = get_cache_dir(project_dir)
    scene_files = 0
    segment_files = 0
    total_bytes = 0
    for item in cache_dir.glob("scene_*.mp4"):
        try:
            total_bytes += item.stat().st_size
            scene_files += 1
        except OSError:
            pass
    for item in cache_dir.glob("seg_*.mkv"):
        try:
            total_bytes += item.stat().st_size
            segment_files += 1
        except OSError:
            pass
    return {
        "cache_dir": str(cache_dir),
        "scene_chunks": scene_files,
        "segment_chunks": segment_files,
        "total_files": scene_files + segment_files,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "max_mb": round(_default_max_cache_bytes() / (1024 * 1024), 2),
    }


def clear_cache(project_dir: str | Path | None = None) -> int:
    """Clear all cached files and return the number removed.

    Removes scene MP4s (scene_*.mp4, this module), segment files
    (seg_*.mkv, render_project_cached), and any stale `.tmp` files.
    """
    cache_dir = get_cache_dir(project_dir)
    removed = 0
    if cache_dir.exists():
        for pattern in ("scene_*.mp4", "seg_*.mkv", ".scene_*.tmp"):
            for item in cache_dir.glob(pattern):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed
