"""Cinema Color Science & Organic Camera Choreography (v2.5.0).

Zero external dependencies (Python stdlib + native FFmpeg filter expressions).
Eliminates the "automated slideshow" feel by replacing robotic linear Ken Burns
and flat default color with:
1. Built-in documentary & cinema color grades (pure FFmpeg `eq`, `colorbalance`,
   `curves`, `vignette`, `unsharp` — no external `.cube` LUTs required, CPU-safe
   on 8GB RAM / no-GPU hardware).
2. Organic handheld micro-drift & focal-point camera moves (rule-of-thirds
   anchoring + subtle dual-frequency breathing so still frames feel shot on a
   real camera rather than a rigid slider).
3. Narrative-aware camera choreography that adapts motion type and intensity to
   each beat's emotional intent (hook, question, revelation, reflection, quote,
   data_point, climax) while avoiding repetitive consecutive moves.
"""

from __future__ import annotations

from typing import Any


# Built-in CPU-safe cinema looks expressed as pure FFmpeg filter chains
CINEMA_LOOKS: dict[str, dict[str, Any]] = {
    "chiaroscuro_doc": {
        "name": "chiaroscuro_doc",
        "description": "Crisp documentary chiaroscuro: deep neutral blacks, subtle warm golden highlights, high clarity (no muddy teal cast).",
        "mood": "philosophical, historical, investigative",
        "vf": (
            "eq=contrast=1.12:brightness=-0.01:saturation=1.00,"
            "colorbalance=rs=0.01:gs=0.0:bs=0.0:rm=0.02:gm=0.01:bm=-0.01:rh=0.04:gh=0.02:bh=-0.02,"
            "unsharp=3:3:0.45:3:3:0.0,"
            "vignette=PI/5.5"
        ),
        "scrim_opacity": 0.50,
        "scrim_depth": 0.30,
    },
    "crisp_cinema": {
        "name": "crisp_cinema",
        "description": "Zero-cast high-contrast natural cinema grade with clean blacks and micro-sharpening.",
        "mood": "modern, tech, clean documentary",
        "vf": (
            "eq=contrast=1.10:brightness=0.0:saturation=1.04,"
            "unsharp=3:3:0.40:3:3:0.0,"
            "vignette=PI/6"
        ),
        "scrim_opacity": 0.48,
        "scrim_depth": 0.30,
    },
    "kodak_warm": {
        "name": "kodak_warm",
        "description": "Warm 35mm Kodak film look: lifted soft blacks, golden midtones, gentle organic warmth.",
        "mood": "storytelling, essay, biography",
        "vf": (
            "eq=contrast=1.05:brightness=0.01:saturation=1.04,"
            "colorbalance=rs=0.02:gs=0.01:bs=-0.02:rm=0.05:gm=0.02:bm=-0.04:rh=0.06:gh=0.03:bh=-0.03,"
            "vignette=PI/5.5"
        ),
        "scrim_opacity": 0.48,
        "scrim_depth": 0.30,
    },
    "noir_slate": {
        "name": "noir_slate",
        "description": "Cool desaturated slate-teal shadows with crisp ivory highlights for tech & investigative essays.",
        "mood": "technical, analytical, thriller",
        "vf": (
            "eq=contrast=1.12:brightness=-0.03:saturation=0.78,"
            "colorbalance=rs=-0.02:gs=0.0:bs=0.03:rm=-0.01:gm=0.0:bm=0.01:rh=0.02:gh=0.02:bh=0.01,"
            "unsharp=3:3:0.45:3:3:0.0,"
            "vignette=PI/4.8"
        ),
        "scrim_opacity": 0.56,
        "scrim_depth": 0.34,
    },
    "editorial_clean": {
        "name": "editorial_clean",
        "description": "Crisp modern magazine/documentary grade: balanced neutral color, subtle micro-contrast, light vignette.",
        "mood": "explainer, journalism, modern",
        "vf": (
            "eq=contrast=1.04:brightness=0.0:saturation=1.02,"
            "unsharp=3:3:0.25:3:3:0.0,"
            "vignette=PI/6"
        ),
        "scrim_opacity": 0.50,
        "scrim_depth": 0.32,
    },
    "warm_archive": {
        "name": "warm_archive",
        "description": "Vintage sepia-kissed archival documentary tone with soft contrast and aged warmth.",
        "mood": "history, poetry, heritage",
        "vf": (
            "eq=contrast=1.06:brightness=-0.01:saturation=0.72,"
            "colorbalance=rs=0.05:gs=0.02:bs=-0.04:rm=0.06:gm=0.03:bm=-0.05:rh=0.04:gh=0.02:bh=-0.04,"
            "vignette=PI/4.5"
        ),
        "scrim_opacity": 0.54,
        "scrim_depth": 0.33,
    },
    "midnight_tech": {
        "name": "midnight_tech",
        "description": "Deep indigo-midnight shadows with clean cyan-silver highlights for AI, code, and futuristic topics.",
        "mood": "ai, software, future",
        "vf": (
            "eq=contrast=1.10:brightness=-0.02:saturation=0.96,"
            "colorbalance=rs=-0.04:gs=-0.01:bs=0.08:rm=-0.01:gm=0.02:bm=0.05:rh=0.01:gh=0.04:bh=0.06,"
            "unsharp=3:3:0.4:3:3:0.0,"
            "vignette=PI/5"
        ),
        "scrim_opacity": 0.55,
        "scrim_depth": 0.32,
    },
}


# Narrative camera moves with organic motion parameters
CAMERA_MOVES: dict[str, dict[str, Any]] = {
    "slow_push_in": {
        "kind": "zoom_in",
        "z_start": 1.00,
        "z_end": 1.14,
        "pan_x": (0.50, 0.50),
        "pan_y": (0.50, 0.47),
        "handheld_amp": 0.004,
        "feel": "focused intimacy, drawing viewer into the core idea",
    },
    "dramatic_push_in": {
        "kind": "zoom_in",
        "z_start": 1.00,
        "z_end": 1.22,
        "pan_x": (0.50, 0.50),
        "pan_y": (0.52, 0.45),
        "handheld_amp": 0.005,
        "feel": "hook, climax, or urgent question",
    },
    "pull_back_reveal": {
        "kind": "zoom_out",
        "z_start": 1.18,
        "z_end": 1.00,
        "pan_x": (0.50, 0.50),
        "pan_y": (0.46, 0.50),
        "handheld_amp": 0.003,
        "feel": "widening perspective, resolution, or big-picture context",
    },
    "pan_right_drift": {
        "kind": "pan_right",
        "z_start": 1.12,
        "z_end": 1.14,
        "pan_x": (0.36, 0.64),
        "pan_y": (0.50, 0.48),
        "handheld_amp": 0.004,
        "feel": "narrative progression, unfolding timeline",
    },
    "pan_left_drift": {
        "kind": "pan_left",
        "z_start": 1.14,
        "z_end": 1.12,
        "pan_x": (0.64, 0.36),
        "pan_y": (0.48, 0.51),
        "handheld_amp": 0.004,
        "feel": "contrast, counterpoint, or Arabic RTL visual sweep",
    },
    "contemplative_hold": {
        "kind": "hold",
        "z_start": 1.04,
        "z_end": 1.06,
        "pan_x": (0.50, 0.50),
        "pan_y": (0.50, 0.50),
        "handheld_amp": 0.0025,
        "feel": "deliberate stillness for quotes, poetry, or heavy realizations",
    },
    "handheld_breathe": {
        "kind": "breathe",
        "z_start": 1.06,
        "z_end": 1.09,
        "pan_x": (0.48, 0.52),
        "pan_y": (0.51, 0.49),
        "handheld_amp": 0.006,
        "feel": "organic documentary lens breathing",
    },
}


# Mapping from narrative beat intent -> preferred camera move candidates
_INTENT_TO_MOVES: dict[str, tuple[str, ...]] = {
    "hook": ("dramatic_push_in", "pan_left_drift", "slow_push_in"),
    "question": ("dramatic_push_in", "handheld_breathe", "slow_push_in"),
    "revelation": ("pull_back_reveal", "slow_push_in", "contemplative_hold"),
    "quote": ("contemplative_hold", "slow_push_in", "pull_back_reveal"),
    "data_point": ("pan_right_drift", "slow_push_in", "pan_left_drift"),
    "reflection": ("contemplative_hold", "pull_back_reveal", "handheld_breathe"),
    "climax": ("dramatic_push_in", "pull_back_reveal", "handheld_breathe"),
    "narrative": ("pan_left_drift", "slow_push_in", "pan_right_drift", "handheld_breathe"),
}


def list_cinema_looks() -> list[dict[str, Any]]:
    """Return metadata for all built-in cinema color looks."""
    return [dict(v) for v in CINEMA_LOOKS.values()]


def get_cinema_look(name: str) -> dict[str, Any]:
    """Return a built-in cinema look by name; raises ValueError if unknown."""
    key = str(name or "").strip().lower()
    if key not in CINEMA_LOOKS:
        valid = ", ".join(sorted(CINEMA_LOOKS))
        raise ValueError(f"unknown cinema look {name!r}; choose from: {valid}")
    return dict(CINEMA_LOOKS[key])


def choose_narrative_camera_move(
    intent: str,
    beat_index: int,
    prev_move: str | None = None,
    duration: float = 3.2,
) -> str:
    """Select an organic, non-repetitive camera move tailored to narrative intent & duration.

    - Never repeats `prev_move` back-to-back.
    - Avoids fast pans on very short beats (< 2.0s) so motion stays legible.
    - Prefers `contemplative_hold` or `slow_push_in` on longer reflective beats.
    """
    candidates = list(_INTENT_TO_MOVES.get(intent, _INTENT_TO_MOVES["narrative"]))
    if duration < 2.0:
        # Short punchy beats read cleaner with push/breathe than wide pans
        candidates = [m for m in candidates if not m.startswith("pan_")] or candidates
    if prev_move in candidates and len(candidates) > 1:
        candidates = [m for m in candidates if m != prev_move]
    return candidates[beat_index % len(candidates)]


def build_organic_zoompan_filter(
    move_name: str,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    focal_x: float = 0.5,
    focal_y: float = 0.5,
    handheld: bool = True,
) -> str:
    """Build an FFmpeg `zoompan` filter string with smoothstep easing + organic handheld drift.

    Uses dual-frequency low-amplitude sinusoidal breathing (`sin(p*PI*2.3) + 0.45*sin(p*PI*5.1)`)
    when `handheld=True` so still images feel alive like real documentary footage.
    """
    spec = CAMERA_MOVES.get(move_name, CAMERA_MOVES["slow_push_in"])
    frames = max(2, int(round(max(0.2, float(duration)) * fps)))
    p = f"(on/{frames - 1})"
    # Cubic smoothstep: 3p^2 - 2p^3
    ease = f"(3*pow({p},2)-2*pow({p},3))"

    z0 = float(spec["z_start"])
    z1 = float(spec["z_end"])
    z_expr = f"{z0:.4f}+({z1 - z0:.4f})*{ease}"

    # Blend move pan endpoints with custom focal point
    fx = max(0.15, min(0.85, float(focal_x)))
    fy = max(0.15, min(0.85, float(focal_y)))
    px0, px1 = spec["pan_x"]
    py0, py1 = spec["pan_y"]
    # Shift pan center toward focal point without exceeding frame bounds
    shift_x = (fx - 0.5) * 0.45
    shift_y = (fy - 0.5) * 0.45
    ax0 = max(0.05, min(0.95, px0 + shift_x))
    ax1 = max(0.05, min(0.95, px1 + shift_x))
    ay0 = max(0.05, min(0.95, py0 + shift_y))
    ay1 = max(0.05, min(0.95, py1 + shift_y))

    amp = float(spec["handheld_amp"]) if handheld else 0.0
    if amp > 0:
        drift_x = f"+{amp:.4f}*iw*(sin({p}*6.283*1.15)+0.4*sin({p}*6.283*2.7))"
        drift_y = f"+{amp:.4f}*ih*(cos({p}*6.283*0.95)+0.4*sin({p}*6.283*2.3))"
    else:
        drift_x = ""
        drift_y = ""

    x_expr = f"max(0,min(iw-iw/zoom,(iw-iw/zoom)*({ax0:.3f}+({ax1 - ax0:.3f})*{ease}){drift_x}))"
    y_expr = f"max(0,min(ih-ih/zoom,(ih-ih/zoom)*({ay0:.3f}+({ay1 - ay0:.3f})*{ease}){drift_y}))"

    return f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={int(width)}x{int(height)}:fps={int(fps)}"


def apply_cinema_look_to_clip(
    clip: dict[str, Any],
    look_name: str,
    move_name: str | None = None,
    focal_x: float = 0.5,
    focal_y: float = 0.5,
) -> dict[str, Any]:
    """Attach a cinema look metadata + organic camera settings onto a clip dict."""
    look = get_cinema_look(look_name)
    clip["cinema_look"] = look["name"]
    if move_name and move_name in CAMERA_MOVES:
        spec = CAMERA_MOVES[move_name]
        clip["camera_move"] = move_name
        clip["focal_x"] = round(max(0.15, min(0.85, float(focal_x))), 3)
        clip["focal_y"] = round(max(0.15, min(0.85, float(focal_y))), 3)
        if clip.get("kind") == "image":
            # Map smoothly onto existing ken_burns machinery so render.py stays 100% compatible
            clip["ken_burns"] = spec["kind"] if spec["kind"] in ("zoom_in", "zoom_out", "pan_left", "pan_right") else "zoom_in"
            clip["_kb_start"] = float(spec["z_start"])
            clip["_kb_end"] = float(spec["z_end"])
            clip["_kb_drift"] = bool(spec["handheld_amp"] > 0)
    return clip
