"""Procedural Cinema Soundscape, Room Tone & J/L-Cut Engine (v2.5.0).

Zero external dependencies (Python stdlib + FFmpeg `lavfi` audio synthesis).
Eliminates the sterile, "silent-gap" feel of automated edits by synthesizing
organic, low-CPU documentary sound design on demand:
- Procedural SFX: `chapter_boom`, `soft_whoosh`, `card_tick`, `shimmer_reveal`,
  `tension_riser`.
- Organic room tone: `warm_studio`, `soft_vinyl`, `night_air` at subtle -38 dB
  so pauses between speech never drop into dead digital silence.
- Documentary J-Cut / L-Cut overlap planner for fluid scene transitions.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


SFX_CATALOG: dict[str, dict[str, Any]] = {
    "chapter_boom": {
        "description": "Deep warm cinematic sub-bass swell and decay for chapter/act openings.",
        "default_duration": 1.6,
        "default_volume_db": -10.0,
    },
    "soft_whoosh": {
        "description": "Airy bandpass-filtered transition swoosh — organic, never harsh.",
        "default_duration": 0.55,
        "default_volume_db": -14.0,
    },
    "card_tick": {
        "description": "Minimalist glass/wood micro-click for highlight_card entrance.",
        "default_duration": 0.14,
        "default_volume_db": -12.0,
    },
    "shimmer_reveal": {
        "description": "Soft warm harmonic chord shimmer for key quotes and revelations.",
        "default_duration": 1.4,
        "default_volume_db": -16.0,
    },
    "tension_riser": {
        "description": "Subtle low-end rising swell leading into a hook or climax cut.",
        "default_duration": 1.2,
        "default_volume_db": -13.0,
    },
}

ROOM_TONE_PRESETS: dict[str, dict[str, Any]] = {
    "warm_studio": {
        "description": "Subtle warm brown-noise studio floor (-38 dB) eliminating dead digital silence.",
        "color": "brown",
        "filter": "lowpass=f=420,highpass=f=45",
        "default_volume_db": -38.0,
    },
    "soft_vinyl": {
        "description": "Gentle archival pink-noise warmth for philosophical/historical essays.",
        "color": "pink",
        "filter": "lowpass=f=900,highpass=f=60",
        "default_volume_db": -40.0,
    },
    "night_air": {
        "description": "Deep atmospheric low-passed air bed for nocturnal/noir documentaries.",
        "color": "brown",
        "filter": "lowpass=f=240,highpass=f=30",
        "default_volume_db": -36.0,
    },
}


def list_sfx_kinds() -> list[dict[str, Any]]:
    """Return metadata for all procedural SFX types."""
    return [{"kind": k, **v} for k, v in SFX_CATALOG.items()]


def synthesize_sfx(
    kind: str,
    output_path: str,
    duration: float | None = None,
    volume_db: float | None = None,
    sample_rate: int = 48000,
    timeout: int = 30,
) -> tuple[bool, dict[str, Any]]:
    """Synthesize a clean, organic cinema SFX WAV/MP3 using pure FFmpeg `lavfi`."""
    key = str(kind or "").strip().lower()
    if key not in SFX_CATALOG:
        valid = ", ".join(sorted(SFX_CATALOG))
        return False, {"code": "UNKNOWN_SFX", "message": f"Unknown SFX kind {kind!r}; choose from: {valid}"}

    spec = SFX_CATALOG[key]
    dur = float(duration if duration is not None else spec["default_duration"])
    dur = max(0.08, min(10.0, dur))
    vol_db = float(volume_db if volume_db is not None else spec["default_volume_db"])

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if key == "chapter_boom":
        fc = (
            f"sine=f=52:r={sample_rate}:d={dur}[s1];"
            f"sine=f=104:r={sample_rate}:d={dur}[s2];"
            f"anoisesrc=c=brown:r={sample_rate}:d={dur}:a=0.15[n1];"
            f"[s1][s2][n1]amix=inputs=3:weights=1.0 0.45 0.25,"
            f"lowpass=f=180,"
            f"afade=t=in:st=0:d=0.04,"
            f"afade=t=out:st={max(0.05, dur * 0.15):.3f}:d={max(0.1, dur * 0.85):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "soft_whoosh":
        mid = dur * 0.42
        fc = (
            f"anoisesrc=c=pink:r={sample_rate}:d={dur}:a=0.6,"
            f"bandpass=f=650:width_type=q:w=1.2,"
            f"afade=t=in:st=0:d={mid:.3f}:curve=qsin,"
            f"afade=t=out:st={mid:.3f}:d={max(0.05, dur - mid):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "card_tick":
        fc = (
            f"sine=f=880:r={sample_rate}:d={dur}[s1];"
            f"sine=f=1320:r={sample_rate}:d={dur}[s2];"
            f"[s1][s2]amix=inputs=2:weights=0.7 0.3,"
            f"highpass=f=300,lowpass=f=2400,"
            f"afade=t=in:st=0:d=0.005,"
            f"afade=t=out:st=0.01:d={max(0.03, dur - 0.01):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "shimmer_reveal":
        fc = (
            f"sine=f=261.63:r={sample_rate}:d={dur}[c4];"
            f"sine=f=329.63:r={sample_rate}:d={dur}[e4];"
            f"sine=f=392.00:r={sample_rate}:d={dur}[g4];"
            f"[c4][e4][g4]amix=inputs=3:weights=0.5 0.35 0.3,"
            f"lowpass=f=1400,"
            f"afade=t=in:st=0:d={min(0.25, dur * 0.25):.3f}:curve=qsin,"
            f"afade=t=out:st={dur * 0.3:.3f}:d={dur * 0.7:.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    else:  # tension_riser
        fc = (
            f"anoisesrc=c=brown:r={sample_rate}:d={dur}:a=0.5[n1];"
            f"sine=f=75:r={sample_rate}:d={dur}[s1];"
            f"[n1][s1]amix=inputs=2:weights=0.6 0.5,"
            f"lowpass=f=360,"
            f"afade=t=in:st=0:d={max(0.1, dur * 0.82):.3f}:curve=exp,"
            f"afade=t=out:st={max(0.05, dur - 0.12):.3f}:d=0.12,"
            f"volume={vol_db}dB[aout]"
        )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-filter_complex", fc,
        "-map", "[aout]",
        "-ar", str(sample_rate),
        "-ac", "2",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.isfile(output_path):
        return False, {"code": "SFX_SYNTH_FAILED", "message": proc.stderr.strip() or "ffmpeg failed"}

    return True, {
        "kind": key,
        "output": output_path,
        "duration": round(dur, 3),
        "volume_db": vol_db,
        "size_bytes": os.path.getsize(output_path),
    }


def generate_room_tone(
    output_path: str,
    duration: float,
    preset: str = "warm_studio",
    volume_db: float | None = None,
    sample_rate: int = 48000,
    timeout: int = 45,
) -> tuple[bool, dict[str, Any]]:
    """Generate a subtle documentary room-tone bed so pauses feel organic, never dead."""
    key = str(preset or "warm_studio").strip().lower()
    if key not in ROOM_TONE_PRESETS:
        valid = ", ".join(sorted(ROOM_TONE_PRESETS))
        return False, {"code": "UNKNOWN_PRESET", "message": f"Unknown room tone preset {preset!r}; choose from: {valid}"}

    spec = ROOM_TONE_PRESETS[key]
    dur = max(0.5, float(duration))
    vol_db = float(volume_db if volume_db is not None else spec["default_volume_db"])
    fade = min(0.5, dur * 0.15)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    fc = (
        f"anoisesrc=c={spec['color']}:r={sample_rate}:d={dur:.3f}:a=0.35,"
        f"{spec['filter']},"
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade:.3f},"
        f"volume={vol_db}dB[aout]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-filter_complex", fc,
        "-map", "[aout]",
        "-ar", str(sample_rate),
        "-ac", "2",
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.isfile(output_path):
        return False, {"code": "ROOM_TONE_FAILED", "message": proc.stderr.strip() or "ffmpeg failed"}

    return True, {
        "preset": key,
        "output": output_path,
        "duration": round(dur, 3),
        "volume_db": vol_db,
        "size_bytes": os.path.getsize(output_path),
    }


def plan_jl_cut_audio(
    scenes: list[dict[str, Any]],
    mode: str = "j_cut",
    overlap_sec: float = 0.25,
) -> list[dict[str, Any]]:
    """Compute documentary J-cut (audio leads picture) or L-cut (audio trails picture) offsets.

    Returns a list of audio schedule hints per scene so editors/renderers can
    stitch scene transitions smoothly without abrupt audio walls.
    """
    m = str(mode or "j_cut").strip().lower()
    ov = max(0.0, min(0.6, float(overlap_sec)))
    plan: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, sc in enumerate(scenes):
        dur = float(sc.get("duration") or 3.0)
        if m == "j_cut" and idx > 0:
            audio_start = max(0.0, round(cursor - ov, 3))
            audio_end = round(cursor + dur, 3)
        elif m == "l_cut" and idx < len(scenes) - 1:
            audio_start = round(cursor, 3)
            audio_end = round(cursor + dur + ov, 3)
        else:
            audio_start = round(cursor, 3)
            audio_end = round(cursor + dur, 3)
        plan.append({
            "scene_id": sc.get("id", f"scene_{idx + 1}"),
            "visual_start": round(cursor, 3),
            "visual_end": round(cursor + dur, 3),
            "audio_start": audio_start,
            "audio_end": audio_end,
            "cut_style": m if (idx > 0 or m == "l_cut") else "straight",
        })
        cursor += dur
    return plan
