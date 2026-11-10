"""Deterministic scene hashing and incremental rendering cache.

Inspired by ProCraft's cryptographic hashing tools, adapted for AgentCut multi-scene
timeline caching to skip re-rendering unchanged scenes during iterative edits.
Zero external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _normalize_obj(obj: Any) -> Any:
    """Recursively normalize dicts, lists, and numbers for stable hashing."""
    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, (list, tuple)):
        return [_normalize_obj(x) for x in obj]
    elif isinstance(obj, float):
        return round(obj, 4)
    return obj


def compute_scene_hash(scene: dict[str, Any], output_params: dict[str, Any] | None = None) -> str:
    """Compute an invariant, deterministic SHA-256 hash for a scene definition.

    Incorporates output parameters (width, height, fps) and referenced audio file metadata
    (size + mtime) to ensure cache invalidation on any source modification.
    Returns a 16-character hexadecimal string.
    """
    hasher = hashlib.sha256()

    normalized_scene = _normalize_obj(scene)
    normalized_output = _normalize_obj(output_params or {})

    # Hash structural JSON
    payload = json.dumps({"scene": normalized_scene, "output": normalized_output}, sort_keys=True, ensure_ascii=False)
    hasher.update(payload.encode("utf-8"))

    # If the scene references external media files, mix in their file stats
    for key in ("narration", "background", "video", "image"):
        val = scene.get(key)
        if isinstance(val, str) and os.path.exists(val):
            stat = os.stat(val)
            hasher.update(f"{val}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))

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
    """Return path to cached scene MP4 if present and non-empty, else None."""
    cache_dir = get_cache_dir(project_dir)
    target = cache_dir / f"scene_{scene_hash}.mp4"
    if target.exists() and target.is_file() and target.stat().st_size > 1024:
        return target
    return None


def store_scene_cache(scene_hash: str, rendered_mp4: str | Path, project_dir: str | Path | None = None) -> Path:
    """Store a successfully rendered scene MP4 in the cache."""
    cache_dir = get_cache_dir(project_dir)
    src_path = Path(rendered_mp4)
    dst_path = cache_dir / f"scene_{scene_hash}.mp4"
    if src_path.resolve() != dst_path.resolve():
        shutil.copy2(src_path, dst_path)
    return dst_path


def clear_cache(project_dir: str | Path | None = None) -> int:
    """Clear all cached scene MP4 files and return the number of removed files."""
    cache_dir = get_cache_dir(project_dir)
    removed = 0
    if cache_dir.exists():
        for item in cache_dir.glob("scene_*.mp4"):
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
    return removed
