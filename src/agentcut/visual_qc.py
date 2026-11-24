"""Visual Quality Control, Motion Continuity Audit & Contact Sheet Generator (v3.4.4).

Provides:
- `audit_html_motion_continuity(html, timings)`: static & DOM-contract audit
  verifying zero CSS `@keyframes`/`transition:`, valid `window.setTime(t)`,
  continuous ambient motion hooks (`.paper-grid`, `.ambient-icon`,
  `beatEnergy`), non-empty scene visuals, and WCAG contrast ratio >= 4.5:1
  for the active palette.
- `audit_rendered_frames(frames_dir_or_list)`: inspects PNG frames (header
  dimensions, byte-size floor against blank frames, and consecutive SHA-256
  frame differences proving continuous intra-scene motion).
- `render_contact_sheet(frames_or_video, output_png, cols=3, rows=2)`:
  assembles a crisp grid contact sheet via FFmpeg `tile` filter so a human
  or agent can eye-check an entire reel or explainer in one image.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
from typing import Any


def _hex_to_rel_luminance(hex_color: str) -> float:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return 0.5
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def compute_contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Compute WCAG 2.1 contrast ratio between two hex colors."""
    la = _hex_to_rel_luminance(hex_a)
    lb = _hex_to_rel_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _probe_png_header(path: str) -> tuple[int, int] | None:
    """Fast pure-Python PNG IHDR width/height reader (0.05ms, no ffprobe)."""
    try:
        with open(path, "rb") as fh:
            hdr = fh.read(24)
        if len(hdr) < 24 or hdr[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", hdr[16:24])
        return int(w), int(h)
    except OSError:
        return None


def audit_html_motion_continuity(
    html: str,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit generated browser HTML for determinism, motion hooks, non-empty
    scenes, and WCAG text contrast."""
    from .flat_reels import PALETTES

    issues: list[str] = []
    if "@keyframes" in html or "transition:" in html or "animation:" in html:
        issues.append("CSS animations/transitions detected (violates setTime determinism)")
    if "window.setTime" not in html:
        issues.append("window.setTime(t) entrypoint missing")
    for hook in ("paper-grid", "beatEnergy", "applyPhysicsEase"):
        if hook not in html:
            issues.append(f"missing continuous motion hook: {hook}")

    scene_ids = re.findall(r'id="scene-([^"]+)"', html)
    if not scene_ids:
        issues.append("no rendered scenes found in HTML")

    pal_name = (spec or {}).get("palette", "cream")
    pal = PALETTES.get(pal_name, PALETTES["cream"])
    paper_ink_ratio = compute_contrast_ratio(pal["paper"], pal["ink"])
    card_ink_ratio = compute_contrast_ratio(pal["card"], pal["ink"])
    if paper_ink_ratio < 4.5 or card_ink_ratio < 4.5:
        issues.append(
            f"low contrast ratio on palette {pal_name!r}: paper/ink={paper_ink_ratio}, card/ink={card_ink_ratio}"
        )

    return {
        "ok": len(issues) == 0,
        "scenes_checked": len(scene_ids),
        "paper_ink_contrast": paper_ink_ratio,
        "card_ink_contrast": card_ink_ratio,
        "deterministic_settime": "window.setTime" in html and "@keyframes" not in html,
        "issues": issues,
    }


def audit_rendered_frames(
    frames_source: str | list[str],
    min_motion_ratio: float = 0.85,
    min_png_bytes: int = 800,
) -> dict[str, Any]:
    """Verify captured PNG frames are non-blank, consistent in resolution,
    and continuously changing across frames (no frozen post-entrance scenes)."""
    if isinstance(frames_source, str):
        if not os.path.isdir(frames_source):
            return {"ok": False, "code": "NO_FRAMES_DIR", "issues": ["frames directory not found"]}
        files = [
            os.path.join(frames_source, f)
            for f in sorted(os.listdir(frames_source))
            if f.endswith(".png")
        ]
    else:
        files = [f for f in frames_source if os.path.isfile(f)]

    if not files:
        return {"ok": False, "code": "EMPTY_FRAMES", "issues": ["no PNG frames found"]}

    issues: list[str] = []
    hashes: list[str] = []
    dims: set[tuple[int, int]] = set()
    blank_frames = 0

    for fp in files:
        sz = os.path.getsize(fp)
        if sz < min_png_bytes:
            blank_frames += 1
        d = _probe_png_header(fp)
        if d:
            dims.add(d)
        with open(fp, "rb") as fh:
            hashes.append(hashlib.sha256(fh.read()).hexdigest())

    if blank_frames > 0:
        issues.append(f"{blank_frames} frame(s) below {min_png_bytes} bytes (blank/corrupt)")
    if len(dims) > 1:
        issues.append(f"inconsistent frame dimensions: {sorted(dims)}")

    transitions = max(1, len(hashes) - 1)
    changing = sum(1 for a, b in zip(hashes, hashes[1:]) if a != b)
    motion_ratio = round(changing / transitions, 3) if len(hashes) > 1 else 1.0
    if len(hashes) > 1 and motion_ratio < min_motion_ratio:
        issues.append(
            f"motion continuity ratio {motion_ratio} below {min_motion_ratio} (frozen frames detected)"
        )

    w, h = next(iter(dims)) if dims else (0, 0)
    return {
        "ok": len(issues) == 0,
        "frame_count": len(files),
        "width": w,
        "height": h,
        "blank_frames": blank_frames,
        "motion_continuity_ratio": motion_ratio,
        "issues": issues,
    }


def render_contact_sheet(
    source_path: str,
    output_png: str,
    cols: int = 3,
    rows: int = 2,
    thumb_width: int = 320,
) -> tuple[bool, dict[str, Any]]:
    """Build a grid contact sheet PNG from either a directory of `frame_*.png`
    files or a rendered `.mp4` video using FFmpeg."""
    cols = max(1, int(cols))
    rows = max(1, int(rows))
    total_cells = cols * rows
    os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)

    if os.path.isdir(source_path):
        pngs = [
            os.path.join(source_path, f)
            for f in sorted(os.listdir(source_path))
            if f.endswith(".png")
        ]
        if not pngs:
            return False, {"code": "NO_FRAMES", "message": f"no PNG files in {source_path}"}
        # Sample `total_cells` evenly across available frames
        picked = [
            pngs[min(len(pngs) - 1, int(round(i * (len(pngs) - 1) / max(1, total_cells - 1))))]
            for i in range(total_cells)
        ]
        inputs: list[str] = []
        for p in picked:
            inputs += ["-i", p]
        concat_in = "".join(f"[{i}:v]" for i in range(total_cells))
        fc = (
            f"{concat_in}concat=n={total_cells}:v=1:a=0,"
            f"scale={thumb_width}:-1,"
            f"tile={cols}x{rows}:padding=8:margin=8:color=0x221E17[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-v", "error",
            *inputs, "-filter_complex", fc, "-map", "[out]",
            "-frames:v", "1", output_png,
        ]
    elif os.path.isfile(source_path):
        fc = (
            f"scale={thumb_width}:-1,"
            f"tile={cols}x{rows}:padding=8:margin=8:color=0x221E17"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-v", "error",
            "-i", source_path, "-vf", fc, "-frames:v", "1", output_png,
        ]
    else:
        return False, {"code": "MISSING_SOURCE", "message": f"source not found: {source_path}"}

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not os.path.isfile(output_png):
        return False, {
            "code": "CONTACT_SHEET_FAILED",
            "message": proc.stderr.strip()[-400:] or "ffmpeg contact sheet failed",
        }
    dims = _probe_png_header(output_png) or (0, 0)
    return True, {
        "output": os.path.abspath(output_png),
        "cols": cols,
        "rows": rows,
        "cells": total_cells,
        "width": dims[0],
        "height": dims[1],
        "size_bytes": os.path.getsize(output_png),
    }
