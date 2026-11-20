"""Documentary Chapter Title Cards, 2.5D Parallax & Video-Beat Motion (v3.0.0).

Zero external Python dependencies (Python stdlib + FFmpeg only).
- `render_chapter_card(...)`: renders an atmospheric documentary chapter divider
  (deep dark cinema canvas + optional subtitle + HarfBuzz/BiDi Arabic text +
  smooth fade in/out + optional procedural `chapter_boom` SFX).
- `render_parallax_clip(...)`: composites a background layer and foreground
  alpha layer at different camera speeds with subtle background depth-of-field
  blur for true 2.5D documentary motion.
- `build_video_beat_motion_filter(...)`: applies subtle CPU-friendly organic
  push-in / drift motion to live video beats (not just still images).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .cinematography import CINEMA_LOOKS
from .soundscape import synthesize_sfx


_REPO_FONTS = Path(__file__).resolve().parent.parent.parent / "fonts"


def _resolve_doc_font(font: str | None = None) -> str:
    if font and os.path.isfile(font):
        return font
    for cand in (
        _REPO_FONTS / "NotoSansArabic-Bold.ttf",
        _REPO_FONTS / "Cairo.ttf",
        _REPO_FONTS / "DejaVuSans.ttf",
    ):
        if cand.is_file():
            return str(cand)
    return ""


def _esc_drawtext(val: str) -> str:
    return (
        val.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
    )


def render_chapter_card(
    title: str,
    output_path: str,
    subtitle: str | None = None,
    chapter_label: str | None = None,
    duration: float = 2.0,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    cinema_look: str = "chiaroscuro_doc",
    font: str | None = None,
    with_boom: bool = True,
    timeout: int = 60,
) -> tuple[bool, dict[str, Any]]:
    """Render a sleek documentary chapter divider MP4 with optional sub-bass boom."""
    if not title or not str(title).strip():
        return False, {"code": "INVALID_TITLE", "message": "title must be non-empty"}

    dur = max(0.8, min(8.0, float(duration)))
    w, h = int(width), int(height)
    fade_d = min(0.35, dur * 0.22)
    font_path = _resolve_doc_font(font)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    from .render import bidi_display_text
    title_disp = _esc_drawtext(bidi_display_text(str(title).strip()))
    sub_disp = _esc_drawtext(bidi_display_text(str(subtitle).strip())) if subtitle else ""
    lbl_disp = _esc_drawtext(bidi_display_text(str(chapter_label).strip())) if chapter_label else ""

    title_size = max(32, int(round(min(w, h) * 0.068)))
    sub_size = max(20, int(round(title_size * 0.52)))
    lbl_size = max(18, int(round(title_size * 0.40)))
    font_opt = f"fontfile='{_esc_drawtext(font_path)}':" if font_path else ""

    # Deep atmospheric charcoal-indigo base + subtle vignette + minimalist accent rule
    vf_parts = [
        f"color=c=#0d1117:s={w}x{h}:r={int(fps)}:d={dur:.3f}",
        "vignette=PI/4",
        f"drawbox=x=(iw-{int(w * 0.16)})/2:y=(ih/2)-{int(title_size * 1.15)}:w={int(w * 0.16)}:h=3:color=#d4a359@0.85:t=fill",
        f"drawtext={font_opt}text='{title_disp}':fontsize={title_size}:fontcolor=#f8fafc:x=(w-text_w)/2:y=(h-text_h)/2",
    ]
    if lbl_disp:
        vf_parts.append(
            f"drawtext={font_opt}text='{lbl_disp}':fontsize={lbl_size}:fontcolor=#d4a359:x=(w-text_w)/2:y=(h/2)-{int(title_size * 1.9)}"
        )
    if sub_disp:
        vf_parts.append(
            f"drawtext={font_opt}text='{sub_disp}':fontsize={sub_size}:fontcolor=#cbd5e1:x=(w-text_w)/2:y=(h/2)+{int(title_size * 0.95)}"
        )

    look_spec = CINEMA_LOOKS.get(str(cinema_look or "").strip().lower())
    if look_spec and look_spec.get("vf"):
        vf_parts.append(look_spec["vf"])

    vf_parts.append(f"fade=t=in:st=0:d={fade_d:.3f}")
    vf_parts.append(f"fade=t=out:st={max(0.0, dur - fade_d):.3f}:d={fade_d:.3f}")
    vf_parts.append("setsar=1,format=yuv420p")

    with tempfile.TemporaryDirectory(prefix="agentcut-chcard-") as td:
        audio_input: list[str] = []
        audio_map: list[str] = []
        if with_boom:
            boom_wav = os.path.join(td, "boom.wav")
            ok_b, _ = synthesize_sfx("chapter_boom", boom_wav, duration=min(dur, 1.8), volume_db=-11.0)
            if ok_b:
                audio_input = ["-i", boom_wav]
                audio_map = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
        if not audio_input:
            audio_input = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dur:.3f}"]
            audio_map = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", ",".join(vf_parts),
            *audio_input,
            *audio_map,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-t", f"{dur:.3f}",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not os.path.isfile(output_path):
            return False, {"code": "CHAPTER_CARD_FAILED", "message": proc.stderr.strip() or "ffmpeg failed"}

    return True, {
        "output": output_path,
        "title": title,
        "subtitle": subtitle,
        "duration": round(dur, 3),
        "width": w,
        "height": h,
        "cinema_look": cinema_look,
        "with_boom": with_boom,
    }


def render_parallax_clip(
    bg_path: str,
    fg_path: str,
    output_path: str,
    duration: float = 3.5,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    bg_zoom: tuple[float, float] = (1.00, 1.06),
    fg_zoom: tuple[float, float] = (1.00, 1.15),
    bg_blur: float = 2.5,
    cinema_look: str | None = "chiaroscuro_doc",
    timeout: int = 90,
) -> tuple[bool, dict[str, Any]]:
    """Render a 2.5D depth-of-field parallax shot from background + foreground RGBA layers."""
    if not os.path.isfile(bg_path):
        return False, {"code": "FILE_NOT_FOUND", "message": f"Background not found: {bg_path!r}"}
    if not os.path.isfile(fg_path):
        return False, {"code": "FILE_NOT_FOUND", "message": f"Foreground not found: {fg_path!r}"}

    dur = max(0.5, float(duration))
    w, h = int(width), int(height)
    frames = max(2, int(round(dur * fps)))
    p = f"(on/{frames - 1})"
    ease = f"(3*pow({p},2)-2*pow({p},3))"

    bg_z = f"{bg_zoom[0]:.4f}+({bg_zoom[1] - bg_zoom[0]:.4f})*{ease}"
    fg_z = f"{fg_zoom[0]:.4f}+({fg_zoom[1] - fg_zoom[0]:.4f})*{ease}"

    blur_f = f",gblur=sigma={float(bg_blur):.2f}" if bg_blur and bg_blur > 0 else ""
    look_vf = ""
    if cinema_look and cinema_look in CINEMA_LOOKS:
        look_vf = "," + CINEMA_LOOKS[cinema_look]["vf"]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fc = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"zoompan=z='{bg_z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}"
        f"{blur_f}[bg];"
        f"[1:v]format=rgba,scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
        f"zoompan=z='{fg_z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},format=rgba[fg];"
        f"[bg][fg]overlay=0:0:shortest=1{look_vf},setsar=1,format=yuv420p[vout]"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-loop", "1", "-t", f"{dur:.3f}", "-i", bg_path,
        "-loop", "1", "-t", f"{dur:.3f}", "-i", fg_path,
        "-filter_complex", fc,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-t", f"{dur:.3f}",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.isfile(output_path):
        return False, {"code": "PARALLAX_FAILED", "message": proc.stderr.strip() or "ffmpeg failed"}

    return True, {
        "output": output_path,
        "duration": round(dur, 3),
        "width": w,
        "height": h,
        "bg_zoom": list(bg_zoom),
        "fg_zoom": list(fg_zoom),
        "bg_blur": bg_blur,
        "cinema_look": cinema_look,
    }


def build_video_beat_motion_filter(
    move_name: str,
    duration: float,
    width: int,
    height: int,
) -> str:
    """Build a lightweight dynamic `scale` + `crop` expression for live video beats.

    Adds subtle organic push-in or pan drift to video clips without the heavy
    CPU cost of `zoompan` on moving video.
    """
    dur = max(0.5, float(duration))
    w, h = int(width), int(height)
    # Pre-scale by 1.12x so crop window has clean headroom to drift/push smoothly
    sw = int(round(w * 1.12))
    sh = int(round(h * 1.12))
    if sw % 2:
        sw += 1
    if sh % 2:
        sh += 1

    if move_name in ("pan_right_drift", "pan_right"):
        x_expr = f"(in_w-{w})*min(1,t/{dur:.3f})"
        y_expr = f"(in_h-{h})/2"
    elif move_name in ("pan_left_drift", "pan_left"):
        x_expr = f"(in_w-{w})*(1-min(1,t/{dur:.3f}))"
        y_expr = f"(in_h-{h})/2"
    else:
        # Subtle organic handheld breathing drift on video beats
        x_expr = f"(in_w-{w})/2+((in_w-{w})*0.22)*sin(t*1.4)"
        y_expr = f"(in_h-{h})/2+((in_h-{h})*0.18)*cos(t*1.1)"

    return (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}:x='{x_expr}':y='{y_expr}',setsar=1"
    )
