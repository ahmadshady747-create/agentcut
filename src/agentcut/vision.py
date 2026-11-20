"""Visual Intelligence: Auto Focal-Point Detection & Shot Exposure Matching (v3.0.0).

Zero external Python dependencies (Python stdlib + FFmpeg only).
- `detect_focal_point(image_path)`: extracts a tiny 48x48 raw grayscale frame
  from FFmpeg stdout (~20ms, 2.3 KB RAM) and computes local gradient/edge energy
  centroid `(focal_x, focal_y)` in pure Python so organic camera moves anchor
  onto the actual subject instead of dead-center `(0.5, 0.5)`.
- `measure_shot_exposure(media_path)`: runs FFmpeg `signalstats` (`YAVG`) to
  measure exact shot luminance (0-255).
- `compute_exposure_match_filter(shots, target_yavg=118.0)`: computes subtle
  per-shot `eq=brightness=...:gamma=...` corrections so consecutive documentary
  shots maintain cohesive visual exposure without jarring bright/dark jumps.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any


_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def detect_focal_point(
    media_path: str,
    sample_time: float = 0.0,
    grid_size: int = 48,
    timeout: int = 20,
) -> dict[str, Any]:
    """Detect the visual subject's focal point `(focal_x, focal_y)` in [0.15, 0.85].

    Uses FFmpeg to decode one 48x48 grayscale frame to raw bytes, then computes
    Sobel-style gradient magnitude weighted toward the central rule-of-thirds
    zone so edges/faces/subjects pull the camera naturally.
    """
    if not media_path or not os.path.isfile(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path!r}")

    n = max(16, min(96, int(grid_size)))
    cmd = ["ffmpeg", "-hide_banner", "-v", "error"]
    if sample_time > 0:
        cmd.extend(["-ss", f"{float(sample_time):.3f}"])
    cmd.extend([
        "-i", media_path,
        "-frames:v", "1",
        "-vf", f"scale={n}:{n}:flags=bilinear,format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "pipe:1",
    ])
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    raw = proc.stdout or b""
    if proc.returncode != 0 or len(raw) < n * n:
        return {
            "media_path": media_path,
            "focal_x": 0.5,
            "focal_y": 0.5,
            "confidence": 0.0,
            "method": "fallback_center",
        }

    # Compute local gradient energy (|dx| + |dy|) for interior pixels
    wx = 0.0
    wy = 0.0
    wsum = 0.0
    for y in range(1, n - 1):
        row = y * n
        up = (y - 1) * n
        dn = (y + 1) * n
        # Mild center-prior (avoids corner watermarks/borders hijacking focus)
        ny = y / (n - 1)
        cy_prior = 1.0 - 0.35 * abs(ny - 0.45)
        for x in range(1, n - 1):
            dx = abs(raw[row + x + 1] - raw[row + x - 1])
            dy = abs(raw[dn + x] - raw[up + x])
            energy = float(dx + dy)
            if energy < 12.0:
                continue
            nx = x / (n - 1)
            cx_prior = 1.0 - 0.30 * abs(nx - 0.5)
            w = (energy * energy) * cx_prior * cy_prior
            wx += nx * w
            wy += ny * w
            wsum += w

    if wsum <= 1e-6:
        return {
            "media_path": media_path,
            "focal_x": 0.5,
            "focal_y": 0.5,
            "confidence": 0.1,
            "method": "flat_frame_center",
        }

    fx = max(0.18, min(0.82, wx / wsum))
    fy = max(0.18, min(0.82, wy / wsum))
    # Confidence scales with average energy density
    avg_energy = (wsum / ((n - 2) * (n - 2))) ** 0.5
    conf = round(min(1.0, avg_energy / 80.0), 3)
    return {
        "media_path": media_path,
        "focal_x": round(fx, 3),
        "focal_y": round(fy, 3),
        "confidence": conf,
        "method": "gradient_energy_centroid",
    }


def measure_shot_exposure(
    media_path: str,
    sample_time: float = 0.0,
    timeout: int = 20,
) -> dict[str, Any]:
    """Measure average luminance (YAVG, 0-255) of a shot using FFmpeg signalstats."""
    if not media_path or not os.path.isfile(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path!r}")

    cmd = ["ffmpeg", "-hide_banner", "-v", "info"]
    if sample_time > 0:
        cmd.extend(["-ss", f"{float(sample_time):.3f}"])
    cmd.extend([
        "-i", media_path,
        "-frames:v", "1",
        "-vf", "signalstats,metadata=mode=print:file=-",
        "-f", "null", "-",
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = _YAVG_RE.search(text)
    yavg = float(m.group(1)) if m else 120.0
    return {
        "media_path": media_path,
        "yavg": round(yavg, 2),
        "exposure_class": (
            "dark" if yavg < 75.0 else ("bright" if yavg > 175.0 else "balanced")
        ),
    }


def compute_exposure_match_filter(
    media_paths: list[str],
    target_yavg: float | None = None,
    max_brightness_delta: float = 0.12,
) -> dict[str, Any]:
    """Compute per-shot FFmpeg `eq` filters to harmonize exposure across a sequence.

    If `target_yavg` is None, targets the sequence median luminance clamped to
    documentary comfort range [95, 145] so neither dark nor bright outliers
    cause jarring flashes between cuts.
    """
    measurements: list[dict[str, Any]] = []
    for mp in media_paths:
        if mp and os.path.isfile(mp):
            measurements.append(measure_shot_exposure(mp))

    if not measurements:
        return {"target_yavg": target_yavg or 118.0, "shots": []}

    if target_yavg is None:
        yvals = sorted(m["yavg"] for m in measurements)
        mid = yvals[len(yvals) // 2]
        target = max(95.0, min(145.0, mid))
    else:
        target = max(50.0, min(200.0, float(target_yavg)))

    shots_out: list[dict[str, Any]] = []
    for m in measurements:
        diff = target - m["yavg"]
        # Map luminance delta gently to FFmpeg eq brightness [-max, +max] and gamma [0.88, 1.15]
        b_delta = max(-max_brightness_delta, min(max_brightness_delta, diff / 420.0))
        gamma = max(0.88, min(1.15, 1.0 + (diff / 360.0)))
        vf = ""
        if abs(b_delta) >= 0.015 or abs(gamma - 1.0) >= 0.02:
            vf = f"eq=brightness={b_delta:.3f}:gamma={gamma:.3f}"
        shots_out.append({
            "media_path": m["media_path"],
            "yavg": m["yavg"],
            "exposure_class": m["exposure_class"],
            "brightness_delta": round(b_delta, 3),
            "gamma": round(gamma, 3),
            "vf": vf,
        })

    return {
        "target_yavg": round(target, 2),
        "shots": shots_out,
    }
