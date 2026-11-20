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

Editorial mode (v1.64.0): the clean editorial theatre for
produce_explainer style "editorial" — full-bleed crisp visuals
(no heavy blur, no darkening), a 4-move alternating camera
choreography across visual beats, a bottom-32% gradient scrim
for the captions, NO top header, NO floating foreground subject,
and at most one highlight glass card per scene (middle third,
2.8s). The editorial look lives here (camera cycle, treatment,
card geometry); render.py wires it into the explainer timeline.

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

# Ken Burns drift (v1.63.0, verify fix): a pure centre-anchored
# zoom on a blurred dark B-roll moves too few pixels per second
# (MAPD 1.1 between frames 1s apart, under the 1.5 bar) — the
# centre of the frame barely moves. The classic Ken Burns move
# is a zoom AND a drift, so zoom_in/zoom_out clips also drift
# horizontally by up to this fraction of the available slack
# (the centre-to-edge distance, at most 5.4% of the width at
# 1.12x — so 0.8 means at most ~4.3% of the width, ~31px at
# 720p, over the scene; the crop's right edge stays at 98.9%
# of the frame, so no edge ever shows). The sign alternates by
# scene index so consecutive scenes drift opposite ways. Pans
# already cross the full slack, so they get no drift. render.py
# reads the clip's _kb_drift key (the _kb_start convention);
# non-cinematic clips never carry it, so their renders are
# unchanged.
KB_DRIFT_FRACTION = 0.8

# Layer 2 punch timing: a punch every PUNCH_INTERVAL seconds, peaking
# at PUNCH_PEAK x the base size, rising/falling over PUNCH_HALF_WIDTH
# seconds each side. The entrance runs ENTRANCE_DURATION seconds.
PUNCH_INTERVAL = 5.0
PUNCH_PEAK = 1.15
PUNCH_HALF_WIDTH = 0.4
ENTRANCE_DURATION = 0.6
# Layer 2 breathing (v1.63.0, Task C): a subtle continuous
# scale/float oscillation so the subject never freezes between
# its entrance and its first punch (the 1.62.0 proof was static
# 0.6–5.0s). ±2% of base size + a ±12px vertical float (at 720p,
# scaling with width), 4s period, keyframes every 1s; punch
# windows own their own times (breathing skips them). The float
# is in phase with the scale sine (the subject rises as it
# grows) and starts at the entrance landing, so the first
# breath grows out of the entrance instead of jumping.
BREATHE_AMPLITUDE = 0.02
BREATHE_FLOAT_PX = 12.0
BREATHE_PERIOD = 4.0
BREATHE_STEP = 1.0
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
# v1.63.0 (Task C): a stronger, tighter glow (was peak 30 /
# radius 0.62) so the mandatory Ken Burns drift over the
# gradient is actually VISIBLE — the 1.62.0 gradient was so
# smooth that a 1.0->1.12x zoom over 5s read as a frozen frame
# (measured MAPD 0.38 between frames 1s apart). The glow edge
# is now steep enough for the zoom to move it perceptibly,
# while the gradient stays dark and legible behind text.
GRADIENT_GLOW_PEAK = 56               # max brightness added (0..255)
GRADIENT_GLOW_RADIUS = 0.45           # fraction of max(width, height)


# Editorial mode (v1.64.0): the clean editorial theatre.
# Camera choreography: a 4-move cycle over the GLOBAL beat
# counter (beat 0 zoom_in, 1 pan_right, 2 zoom_out, 3 pan_left,
# then repeat), all smoothstep in the clip chain. Zoom moves run
# 1.0 <-> 1.18x, pans run at a fixed 1.14x frame — inside the
# 1.25x comfort max. Beats inside a scene crossfade (0.25s);
# scenes hard-cut. The editorial treatment is a light vignette
# ONLY: no blur, no darkening, no contrast/saturation change —
# the image stays crisp and full-bleed. The bottom scrim (a
# project-level scrims dict) darkens only the bottom 32% so the
# caption pill reads without dimming the scene.
EDITORIAL_CAMERA_CYCLE: Tuple[str, ...] = ("zoom_in", "pan_right",
                                           "zoom_out", "pan_left")
EDITORIAL_ZOOM_MAX = 1.18
EDITORIAL_PAN_ZOOM = 1.14
EDITORIAL_XFADE = 0.25
EDITORIAL_TREATMENT: Dict[str, float] = {"vignette": 0.5}
EDITORIAL_SCRIM: Dict[str, Dict[str, float]] = {
    "bottom": {"opacity": 0.55, "depth": 0.32}}
# Highlight card: shown in the middle third for 2.8s only, then
# gone — the screen stays clean the rest of the time.
HIGHLIGHT_CARD_DURATION = 2.8
HIGHLIGHT_CARD_WIDTH_FRAC = 0.72


def editorial_camera_for_beat(beat_index: int) -> Tuple[str, float, float]:
    """(ken_burns, kb_start, kb_end) for a global beat index.

    The cycle alternates zoom and pan so no two consecutive
    shots move the same way. The values ride the existing
    ken_burns machinery via the clip's _kb_start/_kb_end keys
    (render.py reads them; validate.py only overwrites them
    when clips share a scene_id, which explainer clips never
    carry — so explicit values survive untouched).
    """
    move = EDITORIAL_CAMERA_CYCLE[beat_index % len(EDITORIAL_CAMERA_CYCLE)]
    if move == "zoom_in":
        return (move, 1.0, EDITORIAL_ZOOM_MAX)
    if move == "zoom_out":
        return (move, EDITORIAL_ZOOM_MAX, 1.0)
    return (move, EDITORIAL_PAN_ZOOM, EDITORIAL_PAN_ZOOM)


def editorial_layer1_clip(clip: Dict[str, Any],
                          beat_index: int = 0,
                          camera_move: Optional[str] = None,
                          cinema_look: Optional[str] = None) -> Dict[str, Any]:
    """Apply the editorial Layer 1 look to one beat clip (in place).

    v2.5.0: Honors optional director-assigned `camera_move` (from
    cinematography.CAMERA_MOVES with organic handheld drift) and `cinema_look`
    when provided; otherwise falls back to the deterministic 4-move cycle for
    100% backward compatibility with v1.64.0.
    """
    clip["treatment"] = dict(EDITORIAL_TREATMENT)
    if cinema_look:
        from .cinematography import apply_cinema_look_to_clip
        apply_cinema_look_to_clip(clip, cinema_look, move_name=camera_move)
        if clip.get("kind") == "image" and not camera_move:
            kb, ks, ke = editorial_camera_for_beat(beat_index)
            clip["ken_burns"] = kb
            clip["_kb_start"] = ks
            clip["_kb_end"] = ke
        return clip

    if clip.get("kind") == "image":
        if camera_move:
            from .cinematography import CAMERA_MOVES
            spec = CAMERA_MOVES.get(camera_move)
            if spec:
                clip["camera_move"] = camera_move
                clip["ken_burns"] = (
                    spec["kind"]
                    if spec["kind"] in ("zoom_in", "zoom_out", "pan_left", "pan_right")
                    else "zoom_in"
                )
                clip["_kb_start"] = float(spec["z_start"])
                clip["_kb_end"] = float(spec["z_end"])
                clip["_kb_drift"] = bool(spec["handheld_amp"] > 0)
                return clip
        kb, ks, ke = editorial_camera_for_beat(beat_index)
        clip["ken_burns"] = kb
        clip["_kb_start"] = ks
        clip["_kb_end"] = ke
    return clip


def editorial_card_rect(width: int, height: int, card_w: int,
                        card_h: int) -> Tuple[int, int, int, int]:
    """(x, y, w, h) of the editorial highlight card: centred in
    the middle third (33%..66% of the frame height)."""
    x = int(round((width - card_w) / 2))
    third_top = int(round(0.33 * height))
    third_h = int(round(0.33 * height))
    # A too-tall card clamps into the band instead of spilling
    # past it (the middle-third contract in the docstring).
    card_h = min(int(card_h), third_h)
    y = third_top + max(0, (third_h - card_h) // 2)
    return (x, y, int(card_w), int(card_h))


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
    near-black). Zoom clips also get a _kb_drift (v1.63.0): a
    slow horizontal drift on top of the zoom, alternating
    direction by scene index, so the background never reads as
    frozen on blurred B-roll. Returns the same dict.
    """
    clip["treatment"] = dict(treatment if treatment is not None
                             else CINEMATIC_TREATMENT)
    if clip.get("kind") == "image" and not clip.get("ken_burns"):
        clip["ken_burns"] = KEN_BURNS_CYCLE[scene_index % len(KEN_BURNS_CYCLE)]
    if (clip.get("kind") == "image"
            and clip.get("ken_burns") in ("zoom_in", "zoom_out")
            and "_kb_drift" not in clip):
        clip["_kb_drift"] = (KB_DRIFT_FRACTION if scene_index % 2 == 0
                             else -KB_DRIFT_FRACTION)
    return clip


def breathe_float_y(t: float, width: int) -> float:
    """Vertical breathing offset (px) at overlay-relative time t.

    0 before the entrance landing (t < 0.6s); afterwards a sine
    at the breathing period, in phase with the scale sine, so
    the subject rises as it grows and settles as it shrinks.
    Scales with the output width (6px at 720p).
    """
    if t < ENTRANCE_DURATION:
        return 0.0
    return (BREATHE_FLOAT_PX * (float(width) / 720.0)
            * math.sin(2.0 * math.pi * (t - ENTRANCE_DURATION)
                       / BREATHE_PERIOD))


def subject_keyframes(duration: float, base_size: float,
                      cx: float, cy: float, aspect: float,
                      width: int) -> List[Dict[str, Any]]:
    """Entrance + breathing + punch-in keyframes for a Layer 2
    subject overlay.

    Times are overlay-relative seconds. The entrance (0 -> 0.6s)
    fades opacity 0 -> 1 and grows scale 0.92x -> 1.0x base with a
    spring easing. Then a measured punch every PUNCH_INTERVAL
    seconds: scale base -> 1.15x base -> base over 0.8s with
    ease_in_out. Between entrance and punches (v1.63.0, Task C)
    the subject BREATHES: a subtle ±2% scale oscillation plus a
    ±6px vertical float (4s period, 1s keyframe steps) so the
    scene never freezes — the punch windows own their own times
    and breathing skips them. Every keyframe carries x/y computed
    from its scale, so the subject grows IN PLACE around its
    centre (cx, cy): x = cx - scale*W/2, y = cy - scale*W*aspect/2
    + breathe_float_y(t). Scale values are ABSOLUTE (fraction of
    output width), never stacked on Ken Burns (the 1.37.2 rule);
    the peak is at most 0.85 * 1.15 = 0.98 of the frame width.
    """
    dur = float(duration)
    base = min(SUBJECT_SIZE_MAX, max(SUBJECT_SIZE_MIN, float(base_size)))

    def _kf(t: float, scale: float, opacity: float,
            easing: str = "linear") -> Dict[str, Any]:
        w = scale * width
        h = w * aspect
        x = max(0, int(round(cx - w / 2.0)))
        y = max(0, int(round(cy - h / 2.0 + breathe_float_y(t, width))))
        return {"time": round(float(t), 3), "x": x, "y": y,
                "scale": round(float(scale), 4),
                "opacity": round(float(opacity), 3), "easing": easing}

    if dur < 1.2:
        # Too short for punches: entrance only, landing at the end.
        return [_kf(0.0, base * 0.92, 0.0),
                _kf(dur, base, 1.0, "spring")]
    kfs = [_kf(0.0, base * 0.92, 0.0),
           _kf(ENTRANCE_DURATION, base, 1.0, "spring")]
    punch_centers = []
    center = PUNCH_INTERVAL
    while center + PUNCH_HALF_WIDTH <= dur - 0.05:
        punch_centers.append(center)
        kfs.append(_kf(center - PUNCH_HALF_WIDTH, base, 1.0))
        kfs.append(_kf(center, base * PUNCH_PEAK, 1.0, "ease_in_out"))
        kfs.append(_kf(center + PUNCH_HALF_WIDTH, base, 1.0, "ease_in_out"))
        center += PUNCH_INTERVAL
    # Breathing (v1.63.0): 1s steps from t=1.0, skipping any step
    # within a punch window (half-width + 0.1s margin). The sine
    # starts at the entrance landing so the first breath grows
    # out of the entrance instead of jumping.
    bt = 1.0
    while bt < dur - 0.05:
        if all(abs(bt - pc) > PUNCH_HALF_WIDTH + 0.1
               for pc in punch_centers):
            bs = base * (1.0 + BREATHE_AMPLITUDE * math.sin(
                2.0 * math.pi * (bt - ENTRANCE_DURATION)
                / BREATHE_PERIOD))
            kfs.append(_kf(bt, bs, 1.0))
        bt += BREATHE_STEP
    kfs.sort(key=lambda f: f["time"])
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


# Glass-card entrance/exit (v1.63.0, Task C): the hard cut is
# gone — the card slides up 18px with a spring ease and fades
# in over 0.25s, and fades out over the last 0.20s.
CARD_SLIDE_PX = 18
CARD_FADE_IN = 0.25
CARD_FADE_OUT = 0.20
# The shadow padding render_glass_card_png adds around the card
# when drop_shadow is on (vector_ui padding=16): the PNG grows
# by 2*pad on each side and the card sits pad px inside it.
CARD_SHADOW_PAD = 16


def glass_card_overlay(overlay_id: str, x: int, y: int, w: int, h: int,
                       start: float, duration: float, width: int,
                       project_dir: Optional[str] = None,
                       bg_color_top: str = "rgba(30, 41, 59, 0.82)",
                       bg_color_bottom: str = "rgba(15, 23, 42, 0.92)",
                       border_radius: float = 24.0,
                       accent_bar: bool = False,
                       accent_color: str = "#F59E0B",
                       drop_shadow: bool = False) -> Dict[str, Any]:
    """Build a Layer 3 glass-card backdrop overlay dict.

    The card PNG comes from vector_ui.get_cached_glass_card
    (content-hash cached in .agentcut_cache). drop_shadow is off
    by default, so the PNG is exactly w x h and the overlay
    rect matches the card rect; with drop_shadow on, the PNG
    grows by the 16px shadow padding on each side and the
    overlay rect shifts by that padding so the CARD still lands
    at (x, y). accent_bar draws a coloured bar on the card's
    left edge (the richer default template). The overlay's size
    fraction is w / width (or the shadowed PNG width / width);
    the caller places text items over the card rect separately.

    v1.63.0 (Task C): the card no longer hard-cuts in and out —
    it carries entrance/exit keyframes: slide_up 18px with a
    spring ease + fade in over 0.25s, fade out over 0.20s.
    Short cards (< 0.45s) skip the exit fade; the entrance
    shortens to half the duration instead of overrunning it.
    """
    w = max(2, int(w))
    h = max(2, int(h))
    dur = round(float(duration), 3)
    card_path = get_cached_glass_card(
        width=w, height=h, border_radius=border_radius,
        bg_color_top=bg_color_top, bg_color_bottom=bg_color_bottom,
        drop_shadow=drop_shadow, accent_bar=accent_bar,
        accent_color=accent_color, project_dir=project_dir)
    pad = CARD_SHADOW_PAD if drop_shadow else 0
    png_w = w + 2 * pad
    ox = max(0, int(x) - pad)
    oy = max(0, int(y) - pad)
    size = min(1.0, max(0.05, png_w / float(width)))
    size_r = round(float(size), 4)
    in_dur = min(CARD_FADE_IN, dur / 2.0)
    out_dur = min(CARD_FADE_OUT, max(0.0, dur - in_dur))
    # The entrance slides on a spring (Task C) but fades LINEARLY:
    # a spring fade completes in ~0.05s (66% at u=0.12), which is
    # a pop, not the specified 0.25s fade. opacity_easing (v1.63.0)
    # lets the fade ease differently from the move.
    kfs = [
        {"time": 0.0, "x": ox, "y": oy + CARD_SLIDE_PX,
         "scale": size_r, "opacity": 0.0, "easing": "linear",
         "opacity_easing": "linear"},
        {"time": round(in_dur, 3), "x": ox, "y": oy,
         "scale": size_r, "opacity": 1.0, "easing": "spring",
         "opacity_easing": "linear"},
    ]
    if out_dur > 0.01 and dur - out_dur > in_dur:
        kfs.append({"time": round(dur - out_dur, 3), "x": ox, "y": oy,
                    "scale": size_r, "opacity": 1.0, "easing": "linear"})
        kfs.append({"time": dur, "x": ox, "y": oy,
                    "scale": size_r, "opacity": 0.0, "easing": "linear"})
    elif kfs[-1]["time"] < dur:
        kfs.append({"time": dur, "x": ox, "y": oy,
                    "scale": size_r, "opacity": 1.0, "easing": "linear"})
    return {
        "id": overlay_id,
        "kind": "image",
        "src": card_path,
        "start": round(float(start), 3),
        "duration": dur,
        "size": size_r,
        "x": ox,
        "y": oy,
        "opacity": 1.0,
        "keyframes": kfs,
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
