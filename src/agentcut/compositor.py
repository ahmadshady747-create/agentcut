"""3-Layer Cinematic Compositor (v1.62.0).

Re-engineers the visual direction of one-button videos away from the
"black board + flat geometric shapes" house style. Three layers:

- LAYER 1 (background with depth): per-scene B-roll (local images or
  video, arriving as local files from AgentMedia) through the cinematic
  treatment — soft depth-of-field blur, darkening + contrast for text
  legibility, vignette — plus slow Ken Burns camera motion on stills
  (max total scale 1.12x, inside the 1.25x rule). When NO external
  assets are available, the black board is replaced by a rich dynamic
  gradient background (write_cinematic_gradient) — never a flat solid
  colour; the Ken Burns drift over the gradient is the subtle light
  movement.
- LAYER 2 (foreground subject): a background-removed main visual
  (transparent PNG from AgentMedia remove_background) placed at the
  centre or on a third of the screen, with a soft spring entrance and
  a measured punch-in every ~5 seconds to break visual stillness.
  Punch-ins are overlay scale KEYFRAMES whose values are ABSOLUTE
  total scale (fraction of output width, max 1.15x the base size, so
  max 0.98 of the frame) — never stacked on Ken Burns (the 1.37.2
  rule). The keyframes also move x/y to compensate, so the subject
  grows IN PLACE around its centre instead of sliding down-right.
  DESIGN CHANGE (v1.62.0, authorized): produce_explainer's 1.39.0
  punch_in rejection is superseded for these compositor-generated
  subject punch-ins ONLY. A brief-level punch_in is still rejected;
  the compositor generates the keyframes itself, and they are the
  sole exception.
- LAYER 3 (analytical layer + Arabic text): vector_ui glass cards and
  MotionCanvas counters/charts as COMPLEMENTS over the rich scene, not
  as the sole substitute for imagery. Texts keep the Arabic path
  (HarfBuzz measurement, bidi_display_text, Noto Sans Arabic Bold)
  and are zoned by the Vertical Zone Manager (COMPOSITOR_ZONES), so
  glass cards never collide with captions.

AgentMedia separation: this module NEVER fetches or generates media.
B-roll and subject PNGs ARRIVE as local files; a non-local B-roll
spec in cinematic mode is a brief error, not a fetch.

Zero dependencies: stdlib + the sibling modules broll.py, vector_ui.py
and cache.py (none of which import render.py, so render.py may import
this module at top level).
"""

from __future__ import annotations

import math
import os
import struct
from typing import Any, Dict, List, Optional, Tuple

from .vector_ui import encode_rgba_png, get_cached_glass_card

# Vertical Zone Manager bands (fractions of frame height), the same
# bands validate.py enforces for texts/captions. Layer 3 elements are
# placed inside exactly one band each.
COMPOSITOR_ZONES: Dict[str, Tuple[float, float]] = {
    "header": (0.05, 0.18),      # fixed + scene titles
    "diagram": (0.22, 0.56),     # diagrams, charts, foreground subject
    "callout": (0.60, 0.70),     # keyword callouts (+ glass cards)
    "subtitles": (0.76, 0.92),   # captions
}

# Layer 1 cinematic treatment (the 1.60.0 B-roll legibility values):
# soft depth-of-field blur, darkening + contrast so Arabic text stays
# legible over the imagery, full vignette.
CINEMATIC_TREATMENT: Dict[str, float] = {
    "blur": 6.0,
    "brightness": -0.22,
    "contrast": 1.12,
    "saturation": 0.85,
    "vignette": 1.0,
}

# Layer 1 treatment for the GENERATED gradient background: the
# gradient is already dark with a baked vignette, so it needs only
# the blur (uniformity) and a light vignette — the B-roll darkening
# (brightness -0.22) would make it near-black.
GRADIENT_TREATMENT: Dict[str, float] = {
    "blur": 6.0,
    "vignette": 0.5,
}

# Ken Burns cycle for Layer 1 stills (variety across scenes). Every
# entry is a subtle <=1.12x move (render.py: zoom_in 1.0->1.12,
# zoom_out 1.12->1.0, pans at fixed 1.12) — inside the 1.25x max.
KEN_BURNS_CYCLE: Tuple[str, ...] = ("zoom_in", "zoom_out",
                                    "pan_left", "pan_right")

# Layer 2 punch timing: a punch every PUNCH_INTERVAL seconds, peaking
# at PUNCH_PEAK x the base size, rising/falling over PUNCH_HALF_WIDTH
# seconds each side. The entrance runs ENTRANCE_DURATION seconds.
PUNCH_INTERVAL = 5.0
PUNCH_PEAK = 1.15
PUNCH_HALF_WIDTH = 0.4
ENTRANCE_DURATION = 0.6
# Base subject size (fraction of output width) is clamped so the punch
# peak never exceeds the overlay scale limit (1.0) and the entrance
# scale never drops below it (0.05): base <= 1.0/1.15, base >= 0.055.
SUBJECT_SIZE_MIN = 0.10
SUBJECT_SIZE_MAX = 0.85

# Cinematic gradient palette (deterministic): deep slate -> indigo ->
# dark sky -> deep teal, with a warm radial light source.
GRADIENT_STOPS: Tuple[Tuple[float, Tuple[int, int, int]], ...] = (
    (0.00, (13, 20, 36)),
    (0.35, (23, 25, 66)),
    (0.65, (15, 58, 88)),
    (1.00, (6, 40, 44)),
)
GRADIENT_GLOW_CENTER = (0.30, 0.14)   # fraction of (width, height)
GRADIENT_GLOW_COLOR = (255, 190, 120)
GRADIENT_GLOW_PEAK = 30               # max brightness added (0..255)
GRADIENT_GLOW_RADIUS = 0.62           # fraction of max(width, height)


def zone_rect(zone: str, width: int, height: int) -> Tuple[int, int, int, int]:
    """(x, y, w, h) pixel rect of a full-width zone band."""
    lo, hi = COMPOSITOR_ZONES[zone]
    y0 = int(round(lo * height))
    y1 = int(round(hi * height))
    return (0, y0, int(width), max(1, y1 - y0))


def zone_for(y_fraction: float) -> Optional[str]:
    """Zone name containing a vertical fraction (0..1), or None."""
    for name, (lo, hi) in COMPOSITOR_ZONES.items():
        if lo <= y_fraction < hi:
            return name
    return None


def png_dimensions(path: str) -> Optional[Tuple[int, int]]:
    """(width, height) of a PNG file from its IHDR chunk, or None.

    Stdlib-only (no ffprobe): the compositor needs a subject's
    aspect ratio to place it, and a transparent PNG subject is
    always a PNG. Non-PNG files return None (the caller falls
    back to a square assumption).
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        if header[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", header[16:24])
        if w <= 0 or h <= 0:
            return None
        return (int(w), int(h))
    except OSError:
        return None


def _gradient_row_color(y_frac: float) -> Tuple[int, int, int]:
    """Interpolated vertical-gradient RGB at a 0..1 height fraction."""
    stops = GRADIENT_STOPS
    if y_frac <= stops[0][0]:
        return stops[0][1]
    for i in range(len(stops) - 1):
        f0, c0 = stops[i]
        f1, c1 = stops[i + 1]
        if y_frac <= f1:
            u = (y_frac - f0) / (f1 - f0) if f1 > f0 else 0.0
            return tuple(int(round(c0[k] + (c1[k] - c0[k]) * u))
                         for k in range(3))
    return stops[-1][1]


def write_cinematic_gradient(path: str, width: int, height: int) -> str:
    """Write a rich cinematic gradient background PNG (deterministic).

    Vertical multi-stop gradient (slate -> indigo -> sky -> teal)
    + a warm radial light source in the upper area + a gentle
    corner vignette. Never a flat solid colour. The file is
    deterministic for (width, height): same inputs, same bytes.
    The "dynamic" part is the Ken Burns drift the clip chain
    applies over it (see cinematic_layer1_clip) — the light
    source visibly moves as the camera drifts.
    """
    width = max(2, int(width))
    height = max(2, int(height))
    gx = GRADIENT_GLOW_CENTER[0] * width
    gy = GRADIENT_GLOW_CENTER[1] * height
    radius = GRADIENT_GLOW_RADIUS * max(width, height)
    cx, cy = width / 2.0, height / 2.0
    max_corner = math.hypot(cx, cy) or 1.0
    rows = bytearray(width * height * 4)
    for y in range(height):
        y_frac = y / max(1, height - 1)
        r0, g0, b0 = _gradient_row_color(y_frac)
        dy = y + 0.5 - gy
        dy_c = y + 0.5 - cy
        base = y * width * 4
        for x in range(width):
            # Warm radial glow (smooth quadratic falloff).
            dx = x + 0.5 - gx
            dist = math.sqrt(dx * dx + dy * dy)
            glow = 0.0
            if dist < radius:
                u = 1.0 - dist / radius
                glow = GRADIENT_GLOW_PEAK * u * u
            # Gentle corner vignette (up to ~16% darker).
            dx_c = x + 0.5 - cx
            corner = math.sqrt(dx_c * dx_c + dy_c * dy_c) / max_corner
            vig = 1.0 - 0.16 * min(1.0, corner * corner)
            r = min(255, int(round((r0 + glow * GRADIENT_GLOW_COLOR[0] / 255.0) * vig)))
            g = min(255, int(round((g0 + glow * GRADIENT_GLOW_COLOR[1] / 255.0) * vig)))
            b = min(255, int(round((b0 + glow * GRADIENT_GLOW_COLOR[2] / 255.0) * vig)))
            o = base + x * 4
            rows[o] = r
            rows[o + 1] = g
            rows[o + 2] = b
            rows[o + 3] = 255
    png = encode_rgba_png(width, height, bytes(rows))
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def cinematic_layer1_clip(clip: Dict[str, Any], scene_index: int = 0,
                          treatment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Apply the Layer 1 cinematic look to one clip dict (in place).

    Sets the cinematic treatment (blur + darkening + contrast +
    vignette) and, for image clips without an explicit ken_burns,
    a slow Ken Burns move cycling zoom_in/zoom_out/pan_left/
    pan_right by scene index (max total scale 1.12x). Video clips
    get the treatment only (they already move; ken_burns is
    image-only). An explicit per-clip ken_burns always wins.
    treatment overrides the default (the generated gradient
    background uses GRADIENT_TREATMENT — it is already dark with
    a baked vignette, so the B-roll darkening would make it
    near-black). Returns the same dict.
    """
    clip["treatment"] = dict(treatment if treatment is not None
                             else CINEMATIC_TREATMENT)
    if clip.get("kind") == "image" and not clip.get("ken_burns"):
        clip["ken_burns"] = KEN_BURNS_CYCLE[scene_index % len(KEN_BURNS_CYCLE)]
    return clip


def subject_keyframes(duration: float, base_size: float,
                      cx: float, cy: float, aspect: float,
                      width: int) -> List[Dict[str, Any]]:
    """Entrance + punch-in keyframes for a Layer 2 subject overlay.

    Times are overlay-relative seconds. The entrance (0 -> 0.6s)
    fades opacity 0 -> 1 and grows scale 0.92x -> 1.0x base with a
    spring easing. Then a measured punch every PUNCH_INTERVAL
    seconds: scale base -> 1.15x base -> base over 0.8s with
    ease_in_out. Every keyframe carries x/y computed from its
    scale, so the subject grows IN PLACE around its centre
    (cx, cy): x = cx - scale*W/2, y = cy - scale*W*aspect/2.
    Scale values are ABSOLUTE (fraction of output width), never
    stacked on Ken Burns (the 1.37.2 rule); the peak is at most
    0.85 * 1.15 = 0.98 of the frame width.
    """
    dur = float(duration)
    base = min(SUBJECT_SIZE_MAX, max(SUBJECT_SIZE_MIN, float(base_size)))

    def _kf(t: float, scale: float, opacity: float,
            easing: str = "linear") -> Dict[str, Any]:
        w = scale * width
        h = w * aspect
        x = max(0, int(round(cx - w / 2.0)))
        y = max(0, int(round(cy - h / 2.0)))
        return {"time": round(float(t), 3), "x": x, "y": y,
                "scale": round(float(scale), 4),
                "opacity": round(float(opacity), 3), "easing": easing}

    if dur < 1.2:
        # Too short for punches: entrance only, landing at the end.
        return [_kf(0.0, base * 0.92, 0.0),
                _kf(dur, base, 1.0, "spring")]
    kfs = [_kf(0.0, base * 0.92, 0.0),
           _kf(ENTRANCE_DURATION, base, 1.0, "spring")]
    center = PUNCH_INTERVAL
    while center + PUNCH_HALF_WIDTH <= dur - 0.05:
        kfs.append(_kf(center - PUNCH_HALF_WIDTH, base, 1.0))
        kfs.append(_kf(center, base * PUNCH_PEAK, 1.0, "ease_in_out"))
        kfs.append(_kf(center + PUNCH_HALF_WIDTH, base, 1.0, "ease_in_out"))
        center += PUNCH_INTERVAL
    if kfs[-1]["time"] < dur:
        kfs.append(_kf(dur, base, 1.0))
    return kfs


def subject_overlay(overlay_id: str, src: str, start: float,
                    duration: float, width: int, height: int,
                    placement: str = "center",
                    base_size: float = 0.5) -> Dict[str, Any]:
    """Build a Layer 2 foreground-subject overlay dict.

    placement: "center" (frame centre), "left" (left third,
    8% margin) or "right" (right third, 8% margin). The subject
    is vertically centred on the diagram zone (0.39*H) in all
    placements. base_size is the subject width as a fraction of
    the output width (clamped to 0.10..0.85); a tall subject is
    shrunk so its height fits the diagram zone (34% of the frame
    height — 0.39*H ± 17% lands exactly on 22%..56%, so the
    subject never reaches the header titles above or the callout
    below). The overlay carries entrance + punch-in keyframes (see
    subject_keyframes). The caller must validate the src file
    exists (a structured FILE_NOT_FOUND) before rendering.
    """
    if placement not in ("center", "left", "right"):
        raise ValueError(
            f"subject placement must be 'center', 'left' or 'right', "
            f"got {placement!r}")
    dims = png_dimensions(src)
    aspect = (dims[1] / dims[0]) if dims else 1.0
    base = min(SUBJECT_SIZE_MAX, max(SUBJECT_SIZE_MIN, float(base_size)))
    w = base * width
    h = w * aspect
    max_h = 0.34 * height
    if h > max_h and aspect > 0:
        h = max_h
        w = h / aspect
        base = w / width
    cy = 0.39 * height
    if placement == "center":
        cx = width / 2.0
    elif placement == "left":
        cx = 0.08 * width + w / 2.0
    else:
        cx = 0.92 * width - w / 2.0
    x0 = max(0, int(round(cx - w / 2.0)))
    y0 = max(0, int(round(cy - h / 2.0)))
    return {
        "id": overlay_id,
        "kind": "image",
        "src": src,
        "start": round(float(start), 3),
        "duration": round(float(duration), 3),
        "size": round(float(base), 4),
        "x": x0,
        "y": y0,
        "opacity": 1.0,
        "keyframes": subject_keyframes(duration, base, cx, cy, aspect,
                                       width),
    }


def glass_card_overlay(overlay_id: str, x: int, y: int, w: int, h: int,
                       start: float, duration: float, width: int,
                       project_dir: Optional[str] = None,
                       bg_color_top: str = "rgba(30, 41, 59, 0.82)",
                       bg_color_bottom: str = "rgba(15, 23, 42, 0.92)",
                       border_radius: float = 24.0) -> Dict[str, Any]:
    """Build a Layer 3 glass-card backdrop overlay dict.

    The card PNG comes from vector_ui.get_cached_glass_card
    (content-hash cached in .agentcut_cache). drop_shadow is off
    so the PNG is exactly w x h and the overlay rect matches the
    card rect. The overlay's size fraction is w / width; the
    caller places a text item over the same rect separately.
    """
    w = max(2, int(w))
    h = max(2, int(h))
    card_path = get_cached_glass_card(
        width=w, height=h, border_radius=border_radius,
        bg_color_top=bg_color_top, bg_color_bottom=bg_color_bottom,
        drop_shadow=False, project_dir=project_dir)
    size = min(1.0, max(0.05, w / float(width)))
    return {
        "id": overlay_id,
        "kind": "image",
        "src": card_path,
        "start": round(float(start), 3),
        "duration": round(float(duration), 3),
        "size": round(float(size), 4),
        "x": max(0, int(x)),
        "y": max(0, int(y)),
        "opacity": 1.0,
    }


def apply_compositor_to_project(project: Dict[str, Any],
                                clip_windows: List[Tuple[float, float]],
                                subjects: Optional[List[Dict[str, Any]]] = None,
                                title_cards: Optional[List[Dict[str, Any]]] = None,
                                project_dir: Optional[str] = None) -> Dict[str, Any]:
    """Apply the 3-layer compositor to a built project dict (in place).

    Layer 1: every clip gets the cinematic treatment + cycled Ken
    Burns for stills (see cinematic_layer1_clip). Layer 2: each
    subject spec {clip_index, src, placement?, base_size?} becomes
    a subject overlay spanning that clip's window (clip_windows
    are (start, duration) in timeline seconds, same order as
    project["clips"]). Layer 3: each title card spec {y, size,
    start, end} gets a glass-card backdrop overlay (0.8*W wide,
    centred, size*1.4 + 32 tall) behind the title text the caller
    already placed. Returns the same project dict.
    """
    width = int(project["output"]["width"])
    height = int(project["output"]["height"])
    for ci, clip in enumerate(project.get("clips") or []):
        cinematic_layer1_clip(clip, ci)
    overlays = list(project.get("overlays") or [])
    for si, subj in enumerate(subjects or []):
        ci = int(subj.get("clip_index", 0))
        if ci < 0 or ci >= len(clip_windows):
            raise ValueError(
                f"subject {si} clip_index {ci} outside "
                f"0..{len(clip_windows) - 1}")
        cstart, cdur = clip_windows[ci]
        overlays.append(subject_overlay(
            overlay_id=f"comp-subject-{si}",
            src=subj["src"], start=cstart, duration=cdur,
            width=width, height=height,
            placement=subj.get("placement", "center"),
            base_size=float(subj.get("base_size", 0.5))))
    for ti, card in enumerate(title_cards or []):
        cy_size = float(card.get("size", 64))
        card_w = int(round(0.8 * width))
        card_h = int(round(cy_size * 1.4 + 32))
        card_x = int(round((width - card_w) / 2))
        card_y = int(round(float(card.get("y", 100)) - 16))
        overlays.append(glass_card_overlay(
            overlay_id=f"comp-title-card-{ti}",
            x=card_x, y=card_y, w=card_w, h=card_h,
            start=float(card["start"]), duration=float(card["end"] - card["start"]),
            width=width, project_dir=project_dir))
    if overlays:
        project["overlays"] = overlays
    return project
