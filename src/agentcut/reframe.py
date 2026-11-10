"""AgentCut Smart Multi-Aspect Auto-Reframe (Phase 4).

Enables intelligent aspect ratio transformation between landscape (16:9),
vertical (9:16 Shorts/Reels/TikTok), square (1:1 Instagram/LinkedIn),
and portrait (4:5) formats.

Modes:
- blur_pad: Centers the original sharp video over an expanded, blurred,
  and softly darkened background of itself (industry standard for vertical adaptation).
- crop: Center-crops or focal-crops the source video to completely fill the target frame.
- fit: Letterboxes or pillarboxes the video with black or custom colored padding.
"""

import json
import math
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple


ASPECT_PRESETS: Dict[str, Dict[str, Any]] = {
    "9:16": {"width": 1080, "height": 1920, "name": "vertical_shorts"},
    "16:9": {"width": 1920, "height": 1080, "name": "landscape_standard"},
    "1:1": {"width": 1080, "height": 1080, "name": "square_feed"},
    "4:5": {"width": 1080, "height": 1350, "name": "portrait_social"},
    # Aliases
    "vertical": {"width": 1080, "height": 1920, "name": "vertical_shorts"},
    "shorts": {"width": 1080, "height": 1920, "name": "vertical_shorts"},
    "reels": {"width": 1080, "height": 1920, "name": "vertical_shorts"},
    "tiktok": {"width": 1080, "height": 1920, "name": "vertical_shorts"},
    "landscape": {"width": 1920, "height": 1080, "name": "landscape_standard"},
    "youtube": {"width": 1920, "height": 1080, "name": "landscape_standard"},
    "square": {"width": 1080, "height": 1080, "name": "square_feed"},
    "feed": {"width": 1080, "height": 1080, "name": "square_feed"},
}


def probe_video_geometry(video_path: str) -> Dict[str, Any]:
    """Probe video file for dimensions, fps, duration, and audio presence."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json", video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr}")

    try:
        data = json.loads(res.stdout)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse ffprobe JSON output: {exc}")

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not video_stream:
        raise RuntimeError(f"No video stream found in {video_path}")

    width = int(video_stream.get("width", 1920))
    height = int(video_stream.get("height", 1080))

    # Frame rate
    r_fps = video_stream.get("r_frame_rate", "30/1")
    if "/" in str(r_fps):
        num, den = str(r_fps).split("/")
        fps = float(num) / max(1.0, float(den))
    else:
        fps = float(r_fps or 30.0)

    # Duration
    duration = None
    if video_stream.get("duration"):
        try:
            duration = float(video_stream["duration"])
        except ValueError:
            pass
    if duration is None and data.get("format", {}).get("duration"):
        try:
            duration = float(data["format"]["duration"])
        except ValueError:
            pass
    if duration is None:
        duration = 0.0

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "aspect_ratio": round(width / max(1, height), 4),
        "has_audio": has_audio,
    }


def parse_target_dimensions(
    target_aspect: str = "9:16",
    width: Optional[int] = None,
    height: Optional[int] = None,
    scale_factor: float = 1.0,
) -> Tuple[int, int]:
    """Resolve target width and height from aspect name or custom bounds.

    Guarantees even dimensions for H.264/yuv420p compatibility.
    """
    if width is not None and height is not None:
        w, h = int(width), int(height)
    elif target_aspect.lower() in ASPECT_PRESETS:
        preset = ASPECT_PRESETS[target_aspect.lower()]
        w, h = preset["width"], preset["height"]
    elif ":" in target_aspect:
        parts = target_aspect.split(":")
        try:
            ar_w, ar_h = float(parts[0]), float(parts[1])
            if ar_w <= ar_h:
                # Vertical or square-ish: anchor to 1080 width
                w = 1080
                h = int(round(1080.0 * (ar_h / ar_w)))
            else:
                # Landscape: anchor to 1920 width
                w = 1920
                h = int(round(1920.0 * (ar_h / ar_w)))
        except (ValueError, ZeroDivisionError):
            w, h = 1080, 1920
    else:
        w, h = 1080, 1920

    if scale_factor != 1.0:
        w = int(round(w * scale_factor))
        h = int(round(h * scale_factor))

    # Even numbers required by yuv420p
    w = (w // 2) * 2
    h = (h // 2) * 2
    return max(2, w), max(2, h)


def build_reframe_filter(
    in_w: int,
    in_h: int,
    out_w: int,
    out_h: int,
    mode: str = "blur_pad",
    blur_sigma: int = 25,
    blur_steps: int = 5,
) -> str:
    """Generate FFmpeg filtergraph for the requested reframe mode."""
    if mode == "blur_pad":
        # Background: scale to completely fill out_w x out_h, crop center, blur and slightly darken
        # Foreground: scale to fit inside out_w x out_h preserving original aspect
        # Overlay: place foreground centered over background
        return (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},"
            f"boxblur={blur_sigma}:{blur_steps},"
            f"eq=brightness=-0.08:saturation=1.15[bg];"
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
    elif mode == "crop":
        # Scale to fill completely, then center crop
        return (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}:(in_w-out_w)/2:(in_h-out_h)/2[v]"
        )
    elif mode == "fit":
        # Letterbox/pillarbox with black padding
        return (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black[v]"
        )
    else:
        raise ValueError(f"Unknown reframe mode '{mode}'. Choose 'blur_pad', 'crop', or 'fit'.")


def reframe_video(
    input_path: str,
    output_path: str,
    target_aspect: str = "9:16",
    mode: str = "blur_pad",
    width: Optional[int] = None,
    height: Optional[int] = None,
    draft: bool = False,
    encoder: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Intelligently transform video to target aspect ratio and resolution.

    Args:
        input_path: Path to source MP4 or media file.
        output_path: Destination path for reframed MP4.
        target_aspect: Aspect ratio preset ('9:16', '16:9', '1:1', '4:5', 'shorts', etc.).
        mode: Framing strategy: 'blur_pad' (dynamic blurred padding), 'crop', or 'fit'.
        width: Optional explicit target width in pixels.
        height: Optional explicit target height in pixels.
        draft: Fast draft render (half resolution, fastest encoder preset).
        encoder: Optional specific video encoder (e.g. 'h264_qsv', 'h264_nvenc', 'libx264').

    Returns:
        (ok, info_dict)
    """
    start_time = time.time()
    try:
        geo = probe_video_geometry(input_path)
    except Exception as exc:
        return False, {"code": "PROBE_FAILED", "message": str(exc)}

    in_w = geo["width"]
    in_h = geo["height"]
    scale_factor = 0.5 if draft else 1.0
    out_w, out_h = parse_target_dimensions(target_aspect, width, height, scale_factor=scale_factor)

    # Pick encoder
    if encoder is None:
        try:
            from .render import _pick_encoder
            encoder = _pick_encoder(out_w, out_h)
        except Exception:
            encoder = "libx264"

    filtergraph = build_reframe_filter(in_w, in_h, out_w, out_h, mode=mode)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", input_path,
        "-filter_complex", filtergraph,
        "-map", "[v]",
    ]

    if geo["has_audio"]:
        cmd.extend(["-map", "0:a", "-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-an"])

    # Video encoding params
    cmd.extend(["-c:v", encoder, "-pix_fmt", "yuv420p"])

    if encoder == "libx264":
        preset = "ultrafast" if draft else "veryfast"
        cmd.extend(["-preset", preset, "-crf", "22"])
    elif "nvenc" in encoder:
        preset = "p1" if draft else "p4"
        cmd.extend(["-preset", preset])
    elif "qsv" in encoder:
        preset = "veryfast" if draft else "balanced"
        cmd.extend(["-preset", preset])

    cmd.append(output_path)

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        # Fallback to libx264 if hardware encoder failed
        if encoder != "libx264":
            cmd_fallback = [c if c != encoder else "libx264" for c in cmd]
            res = subprocess.run(cmd_fallback, capture_output=True, text=True, check=False)
            encoder = "libx264"

    if res.returncode != 0:
        return False, {
            "code": "REFRAME_FAILED",
            "message": f"FFmpeg reframe failed: {res.stderr}",
            "command": " ".join(cmd),
        }

    elapsed = round(time.time() - start_time, 2)
    return True, {
        "output_path": output_path,
        "input_resolution": f"{in_w}x{in_h}",
        "output_resolution": f"{out_w}x{out_h}",
        "target_aspect": target_aspect,
        "mode": mode,
        "encoder": encoder,
        "duration": geo["duration"],
        "elapsed_seconds": elapsed,
    }


def reframe_to_variants(
    input_path: str,
    output_dir: str,
    aspects: Tuple[str, ...] = ("9:16", "1:1", "16:9"),
    mode: str = "blur_pad",
    draft: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Produce multi-platform aspect variants from a single source video."""
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    variants = {}
    for aspect in aspects:
        aspect_clean = aspect.replace(":", "x")
        out_name = f"{base_name}_{aspect_clean}.mp4"
        out_path = os.path.join(output_dir, out_name)

        ok, info = reframe_video(
            input_path=input_path,
            output_path=out_path,
            target_aspect=aspect,
            mode=mode,
            draft=draft,
        )
        if not ok:
            return False, {
                "code": "VARIANT_FAILED",
                "message": f"Failed to generate variant for {aspect}: {info.get('message')}",
                "partial_variants": variants,
            }
        variants[aspect] = info

    return True, {
        "source": input_path,
        "output_dir": output_dir,
        "mode": mode,
        "variants": variants,
    }
