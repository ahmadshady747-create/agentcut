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

# Editorial camera moves (v1.64.0, shared with compositor.py's
# EDITORIAL_CAMERA_CYCLE): zoom 1.0 <-> 1.18x, pans at a fixed
# 1.14x frame. The values live here as the filter-chain source
# of truth; compositor.py carries the cycle order.
EDITORIAL_MOVES = ("zoom_in", "pan_right", "zoom_out", "pan_left")
EDITORIAL_ZOOM_MAX = 1.18
EDITORIAL_PAN_ZOOM = 1.14


def editorial_zoompan_filter(move, duration, width, height, fps=30):
    """The zoompan filter for one editorial camera move (v1.64.0).

    Smoothstep-eased, matching render.py's clip-chain Ken Burns
    expressions exactly (same s curve, same x/y forms), so the
    B-roll pipeline and the clip chain produce the same motion
    for the same move name. move is one of EDITORIAL_MOVES.
    """
    if move not in EDITORIAL_MOVES:
        raise ValueError(
            f"editorial camera move must be one of {EDITORIAL_MOVES}, "
            f"got {move!r}")
    num_frames = max(1, int(round(duration * fps)))
    _p = f"on/{num_frames}"
    _s = f"({_p})*({_p})*(3-2*({_p}))"
    _cx = "iw/2-(iw/zoom/2)"
    if move == "zoom_in":
        z = f"1+{EDITORIAL_ZOOM_MAX - 1.0:g}*{_s}"
        x_expr, y_expr = _cx, "ih/2-(ih/zoom/2)"
    elif move == "zoom_out":
        z = f"{EDITORIAL_ZOOM_MAX:g}-{EDITORIAL_ZOOM_MAX - 1.0:g}*{_s}"
        x_expr, y_expr = _cx, "ih/2-(ih/zoom/2)"
    elif move == "pan_left":
        z = f"{EDITORIAL_PAN_ZOOM:g}"
        x_expr, y_expr = f"(iw-iw/zoom)*{_s}", "(ih-ih/zoom)/2"
    else:  # pan_right
        z = f"{EDITORIAL_PAN_ZOOM:g}"
        x_expr, y_expr = f"(iw-iw/zoom)*(1-{_s})", "(ih-ih/zoom)/2"
    return (f"zoompan=z='{z}':x='{x_expr}':y='{y_expr}':"
            f"d={num_frames}:s={width}x{height}:fps={fps}")


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
    style: str = "cinematic",
    camera_move: str | None = None,
) -> str:
    """Build the cinematic FFmpeg filter chain for B-roll background media.

    Scales and crops media to target dimensions, applies gentle Ken Burns camera
    motion if static image, and applies atmospheric darkening + depth of field
    to guarantee foreground Arabic text and diagrams remain razor-sharp.

    style "editorial" (v1.64.0): the clean editorial theatre —
    NO blur, NO darkening/contrast/saturation change, a subtle
    vignette only, and the camera move comes from the editorial
    4-move cycle (camera_move, default "zoom_in") with the
    editorial zoom values (1.18x / 1.14x). The explicit
    blur/brightness/contrast/saturation arguments are ignored
    in editorial mode (the look is fixed by the style).
    """
    if style not in ("cinematic", "editorial"):
        raise ValueError(
            f"broll style must be 'cinematic' or 'editorial', got {style!r}")
    editorial = (style == "editorial")
    if editorial:
        if camera_move is None:
            camera_move = "zoom_in"
        if camera_move not in EDITORIAL_MOVES:
            raise ValueError(
                f"editorial camera_move must be one of {EDITORIAL_MOVES}, "
                f"got {camera_move!r}")
    filters = []

    # 1. Scale and smart crop to fill target dimensions
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
    filters.append(f"crop={width}:{height}")

    # 2. Camera motion for static images (v1.63.0, Task C):
    # mandatory continuous Ken Burns over the WHOLE duration —
    # a smoothstep zoom 1.0 -> 1.12x (slow start, faster middle,
    # slow landing), centre-anchored, matching render.py's clip
    # chain exactly. The old linear ramp (min(zoom+0.0008,1.12),
    # top-left anchored) started and stopped mechanically and
    # drifted off-centre. Editorial (v1.64.0) uses the cycled
    # move instead (1.18x zooms / 1.14x pans).
    if not is_video:
        if editorial:
            filters.append(editorial_zoompan_filter(
                camera_move, duration, width, height, fps))
        else:
            num_frames = max(1, int(round(duration * fps)))
            _p = f"on/{num_frames}"
            _s = f"({_p})*({_p})*(3-2*({_p}))"
            filters.append(
                f"zoompan=z='1+0.12*{_s}':x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':d={num_frames}:"
                f"s={width}x{height}:fps={fps}")

    if editorial:
        # 3. Editorial: crisp full-bleed image, subtle vignette
        # only. No blur, no darkening — the bottom scrim (a
        # project-level scrims dict in render.py) carries the
        # caption legibility instead.
        if vignette:
            filters.append("vignette=PI/8")
        return ",".join(filters)

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
