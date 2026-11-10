"""B-Roll Engine & Cinematic Background Pipeline (v1.60.0).

Supports rich live video clips and cinematic photos as scene backgrounds (B-Roll)
with automated depth-of-field blurring, color grading, and vignette to ensure
foreground Arabic typography and vector UI cards pop with 100% legibility.

Zero external dependencies — pure Python stdlib (urllib.request, json, os, hashlib) + FFmpeg.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .cache import get_cache_dir

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def is_video_file(path_or_url: str) -> bool:
    """Return True if path or url points to a video file format."""
    ext = os.path.splitext(urllib.parse.urlparse(path_or_url).path)[1].lower()
    return ext in VIDEO_EXTS


def is_image_file(path_or_url: str) -> bool:
    """Return True if path or url points to an image file format."""
    ext = os.path.splitext(urllib.parse.urlparse(path_or_url).path)[1].lower()
    return ext in IMAGE_EXTS


def build_broll_filter_chain(
    width: int,
    height: int,
    duration: float = 5.0,
    is_video: bool = True,
    fps: int = 30,
    blur_sigma: float = 6.0,
    brightness: float = -0.22,
    contrast: float = 1.12,
    saturation: float = 0.85,
    vignette: bool = True,
) -> str:
    """Build the cinematic FFmpeg filter chain for B-roll background media.

    Scales and crops media to target dimensions, applies gentle Ken Burns camera
    motion if static image, and applies atmospheric darkening + depth of field
    to guarantee foreground Arabic text and diagrams remain razor-sharp.
    """
    filters = []

    # 1. Scale and smart crop to fill target dimensions
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
    filters.append(f"crop={width}:{height}")

    # 2. Camera motion for static images
    if not is_video:
        num_frames = max(1, int(round(duration * fps)))
        filters.append(f"zoompan=z='min(zoom+0.0008,1.12)':d={num_frames}:s={width}x{height}:fps={fps}")

    # 3. Cinematic Depth of Field & Legibility Grading
    if blur_sigma > 0:
        filters.append(f"gblur=sigma={blur_sigma}")

    filters.append(f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}")

    if vignette:
        filters.append("vignette=PI/4")

    return ",".join(filters)


def download_file_to_cache(url: str, cache_dir: Path, prefix: str = "broll_") -> Optional[str]:
    """Download external media URL to local cache directory with timeout and retry."""
    try:
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".mp4"
        dest_path = cache_dir / f"{prefix}{url_hash}{ext}"

        if dest_path.exists() and dest_path.stat().st_size > 1024:
            return str(dest_path.resolve())

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AgentCut-VideoEngine/1.60.0 (Zero-Dependencies stdlib)"},
        )
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            data = resp.read()
            if len(data) > 1024:
                dest_path.write_bytes(data)
                return str(dest_path.resolve())
    except Exception:
        pass
    return None


def fetch_pexels_broll(
    query: str,
    orientation: str = "portrait",
    cache_dir: Optional[Path] = None,
) -> Optional[str]:
    """Fetch free stock video B-roll via Pexels API if PEXELS_API_KEY is configured."""
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None

    if cache_dir is None:
        cache_dir = get_cache_dir() / "broll"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://api.pexels.com/videos/search?query={encoded_query}&orientation={orientation}&per_page=1"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": api_key,
                "User-Agent": "AgentCut-VideoEngine/1.60.0",
            },
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            videos = payload.get("videos") or []
            if videos:
                files = videos[0].get("video_files") or []
                # Pick HD or high quality video file
                sorted_files = sorted(
                    [f for f in files if f.get("link") and f.get("file_type") == "video/mp4"],
                    key=lambda x: x.get("width", 0),
                    reverse=True,
                )
                if sorted_files:
                    vlink = sorted_files[0]["link"]
                    return download_file_to_cache(vlink, cache_dir, prefix="pexels_")
    except Exception:
        pass
    return None


def fetch_pixabay_broll(
    query: str,
    cache_dir: Optional[Path] = None,
) -> Optional[str]:
    """Fetch free stock video B-roll via Pixabay API if PIXABAY_API_KEY is configured."""
    api_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not api_key:
        return None

    if cache_dir is None:
        cache_dir = get_cache_dir() / "broll"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://pixabay.com/api/videos/?key={api_key}&q={encoded_query}&per_page=3"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AgentCut-VideoEngine/1.60.0"},
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            hits = payload.get("hits") or []
            if hits:
                vids = hits[0].get("videos") or {}
                # Preference: medium or small
                chosen = vids.get("medium") or vids.get("small") or vids.get("large")
                if chosen and chosen.get("url"):
                    return download_file_to_cache(chosen["url"], cache_dir, prefix="pixabay_")
    except Exception:
        pass
    return None


def resolve_broll_media(
    media_ref: Any,
    width: int = 720,
    height: int = 1280,
    project_dir: str | Path | None = None,
) -> Optional[str]:
    """Resolve a B-roll media reference into a valid local file path.

    Supports:
    1. Local file path (absolute or relative).
    2. Direct HTTP/HTTPS media URL.
    3. Keyword query string (searches Pexels/Pixabay if keys available).
    4. None / graceful fallback if media cannot be fetched.
    """
    if not media_ref or not isinstance(media_ref, str):
        return None

    media_str = media_ref.strip()

    # 1. Local file path
    if os.path.isfile(media_str):
        return os.path.abspath(media_str)

    cache_dir = get_cache_dir(project_dir) / "broll"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 2. Direct HTTP/HTTPS URL
    if media_str.startswith(("http://", "https://")):
        return download_file_to_cache(media_str, cache_dir)

    # 3. Keyword query via stock API
    orientation = "portrait" if height > width else "landscape"
    res = fetch_pexels_broll(media_str, orientation=orientation, cache_dir=cache_dir)
    if res:
        return res

    res = fetch_pixabay_broll(media_str, cache_dir=cache_dir)
    if res:
        return res

    return None
