"""Procedural Cinema Soundscape, Room Tone, Auto-SFX, J/L-Cut & Music Bed Engine (v3.4.1).

Zero external dependencies (Python stdlib + FFmpeg `lavfi` audio synthesis).
Pure audio module — zero video filters or visual drawing.
Synthesizes organic, low-CPU sound design on demand:
- 15 procedural SFX:
    `chapter_boom`, `soft_whoosh`, `card_tick`, `shimmer_reveal`,
    `tension_riser`, `pop_in`, `counter_tick`, `bell_chime`, `bass_drop`,
    `page_flip`, `marker_swipe`, `pulse_heartbeat`, `digital_blip`,
    `success_chord`, `tape_stop`.
- 3 organic room tones: `warm_studio`, `soft_vinyl`, `night_air`.
- 6 generative ambient music beds:
    `documentary_drone`, `contemplative_pad`, `tech_pulse`,
    `curiosity_marimba`, `warm_acoustic`, `cinematic_tension`.
- Scene-aware `plan_scene_sfx(spec, timings, beat_profiles=None)` and
  `plan_jl_cut_schedule(spec, timings)` for browser-rendered videos.
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
        "description": "Minimalist glass/wood micro-click for card and step entrances.",
        "default_duration": 0.14,
        "default_volume_db": -12.0,
    },
    "shimmer_reveal": {
        "description": "Soft warm harmonic chord shimmer for key quotes and callouts.",
        "default_duration": 1.4,
        "default_volume_db": -16.0,
    },
    "tension_riser": {
        "description": "Subtle low-end rising swell leading into a hook or climax cut.",
        "default_duration": 1.2,
        "default_volume_db": -13.0,
    },
    "pop_in": {
        "description": "Crisp playful pop for question reveals, badges, and flat-editorial icons.",
        "default_duration": 0.18,
        "default_volume_db": -11.0,
    },
    "counter_tick": {
        "description": "Warm percussive tick for statistics count-ups and timeline nodes.",
        "default_duration": 0.12,
        "default_volume_db": -13.0,
    },
    "bell_chime": {
        "description": "Gentle warm harmonic bell for conclusions and key takeaways.",
        "default_duration": 1.1,
        "default_volume_db": -15.0,
    },
    "bass_drop": {
        "description": "Punchy warm low-end accent for dramatic hooks and emphasis.",
        "default_duration": 0.85,
        "default_volume_db": -11.0,
    },
    "page_flip": {
        "description": "Crisp paper rustle / flip texture for flat-paper scene turns.",
        "default_duration": 0.28,
        "default_volume_db": -14.0,
    },
    "marker_swipe": {
        "description": "Soft felt-marker underline brush sound for question & callout sweeps.",
        "default_duration": 0.35,
        "default_volume_db": -15.0,
    },
    "pulse_heartbeat": {
        "description": "Low warm double-thump biological heartbeat accent for health/body scenes.",
        "default_duration": 0.65,
        "default_volume_db": -12.0,
    },
    "digital_blip": {
        "description": "Clean gentle harmonic blip for code lines and terminal reveals.",
        "default_duration": 0.14,
        "default_volume_db": -14.0,
    },
    "success_chord": {
        "description": "Uplifting warm major triad swell for completed checklists and solutions.",
        "default_duration": 1.25,
        "default_volume_db": -15.0,
    },
    "tape_stop": {
        "description": "Short low-pass analog decelerating thump for dramatic contrast or myth-busting.",
        "default_duration": 0.45,
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

MUSIC_BED_PRESETS: dict[str, dict[str, Any]] = {
    "documentary_drone": {
        "description": "Warm low harmonic fifth pad (C2 + G2 + D3) for thoughtful explainers.",
        "freqs": [65.41, 98.0, 146.83],
        "weights": [0.55, 0.35, 0.2],
        "lowpass": 520,
        "default_volume_db": -22.0,
    },
    "contemplative_pad": {
        "description": "Soft airy minor chord bed (A2 + C3 + E3) for reflective stories.",
        "freqs": [110.0, 130.81, 164.81],
        "weights": [0.45, 0.35, 0.25],
        "lowpass": 760,
        "default_volume_db": -23.0,
    },
    "tech_pulse": {
        "description": "Clean modern electronic ambient bed (D2 + A2 + F#3) for tech/science explainers.",
        "freqs": [73.42, 110.0, 185.0],
        "weights": [0.5, 0.35, 0.2],
        "lowpass": 880,
        "default_volume_db": -22.0,
    },
    "curiosity_marimba": {
        "description": "Warm wooden harmonic intervals (F2 + C3 + G3 + A3) for 'طب ازاي؟' science reels.",
        "freqs": [87.31, 130.81, 196.0, 220.0],
        "weights": [0.45, 0.35, 0.22, 0.15],
        "lowpass": 960,
        "default_volume_db": -22.5,
    },
    "warm_acoustic": {
        "description": "Rich open fifth & major third harmonic bed (G2 + D3 + B3) for human stories.",
        "freqs": [98.0, 146.83, 246.94],
        "weights": [0.5, 0.35, 0.2],
        "lowpass": 820,
        "default_volume_db": -22.5,
    },
    "cinematic_tension": {
        "description": "Deep suspenseful sub-fifth and minor second bed (E1 + B1 + F2) for dramatic hooks.",
        "freqs": [41.20, 61.74, 87.31],
        "weights": [0.6, 0.35, 0.15],
        "lowpass": 380,
        "default_volume_db": -21.5,
    },
}

_DEFAULT_SCENE_SFX: dict[str, str] = {
    "intro": "soft_whoosh",
    "question": "pop_in",
    "concept": "page_flip",
    "process": "card_tick",
    "chapter": "chapter_boom",
    "statistic": "counter_tick",
    "callout": "shimmer_reveal",
    "conclusion": "bell_chime",
    "comparison": "card_tick",
    "timeline": "card_tick",
    "cycle": "soft_whoosh",
    "layers": "page_flip",
    "code": "digital_blip",
    "split": "card_tick",
    "checklist": "success_chord",
}


def list_sfx_kinds() -> list[dict[str, Any]]:
    """Return metadata for all 15 procedural SFX types."""
    return [{"kind": k, **v} for k, v in SFX_CATALOG.items()]


def list_music_presets() -> list[dict[str, Any]]:
    """Return metadata for all 6 ambient music bed presets."""
    return [{"preset": k, "description": v["description"],
             "default_volume_db": v["default_volume_db"]}
            for k, v in MUSIC_BED_PRESETS.items()]


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
    elif key == "pop_in":
        fc = (
            f"sine=f=520:r={sample_rate}:d={dur}[s1];"
            f"sine=f=780:r={sample_rate}:d={dur}[s2];"
            f"[s1][s2]amix=inputs=2:weights=0.65 0.45,"
            f"lowpass=f=1800,"
            f"afade=t=in:st=0:d=0.01,"
            f"afade=t=out:st=0.025:d={max(0.04, dur - 0.025):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "counter_tick":
        fc = (
            f"sine=f=1046.5:r={sample_rate}:d={dur},"
            f"highpass=f=400,lowpass=f=2200,"
            f"afade=t=in:st=0:d=0.004,"
            f"afade=t=out:st=0.012:d={max(0.03, dur - 0.012):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "bell_chime":
        fc = (
            f"sine=f=523.25:r={sample_rate}:d={dur}[c5];"
            f"sine=f=659.25:r={sample_rate}:d={dur}[e5];"
            f"sine=f=1046.5:r={sample_rate}:d={dur}[c6];"
            f"[c5][e5][c6]amix=inputs=3:weights=0.5 0.35 0.2,"
            f"lowpass=f=2200,"
            f"afade=t=in:st=0:d=0.015,"
            f"afade=t=out:st=0.1:d={max(0.1, dur - 0.1):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "bass_drop":
        fc = (
            f"sine=f=58:r={sample_rate}:d={dur}[s1];"
            f"sine=f=116:r={sample_rate}:d={dur}[s2];"
            f"[s1][s2]amix=inputs=2:weights=1.0 0.35,"
            f"lowpass=f=160,"
            f"afade=t=in:st=0:d=0.015,"
            f"afade=t=out:st=0.08:d={max(0.1, dur - 0.08):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "page_flip":
        mid = dur * 0.35
        fc = (
            f"anoisesrc=c=white:r={sample_rate}:d={dur}:a=0.45,"
            f"bandpass=f=1450:width_type=q:w=1.8,"
            f"afade=t=in:st=0:d={mid:.3f}:curve=qsin,"
            f"afade=t=out:st={mid:.3f}:d={max(0.04, dur - mid):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "marker_swipe":
        mid = dur * 0.3
        fc = (
            f"anoisesrc=c=pink:r={sample_rate}:d={dur}:a=0.5,"
            f"bandpass=f=980:width_type=q:w=2.2,"
            f"afade=t=in:st=0:d={mid:.3f},"
            f"afade=t=out:st={mid:.3f}:d={max(0.05, dur - mid):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "pulse_heartbeat":
        fc = (
            f"sine=f=55:r={sample_rate}:d={dur}[s1];"
            f"sine=f=82.5:r={sample_rate}:d={dur}[s2];"
            f"[s1][s2]amix=inputs=2:weights=0.85 0.35,"
            f"lowpass=f=140,"
            f"afade=t=in:st=0:d=0.02,"
            f"afade=t=out:st=0.08:d={max(0.08, dur - 0.08):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "digital_blip":
        fc = (
            f"sine=f=987.77:r={sample_rate}:d={dur}[b5];"
            f"sine=f=1479.98:r={sample_rate}:d={dur}[fs6];"
            f"[b5][fs6]amix=inputs=2:weights=0.6 0.4,"
            f"lowpass=f=2800,"
            f"afade=t=in:st=0:d=0.005,"
            f"afade=t=out:st=0.015:d={max(0.03, dur - 0.015):.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "success_chord":
        fc = (
            f"sine=f=293.66:r={sample_rate}:d={dur}[d4];"
            f"sine=f=369.99:r={sample_rate}:d={dur}[fs4];"
            f"sine=f=440.00:r={sample_rate}:d={dur}[a4];"
            f"sine=f=587.33:r={sample_rate}:d={dur}[d5];"
            f"[d4][fs4][a4][d5]amix=inputs=4:weights=0.45 0.35 0.3 0.2,"
            f"lowpass=f=1800,"
            f"afade=t=in:st=0:d=0.06:curve=qsin,"
            f"afade=t=out:st={dur * 0.25:.3f}:d={dur * 0.75:.3f}:curve=exp,"
            f"volume={vol_db}dB[aout]"
        )
    elif key == "tape_stop":
        fc = (
            f"sine=f=92:r={sample_rate}:d={dur}[s1];"
            f"anoisesrc=c=brown:r={sample_rate}:d={dur}:a=0.35[n1];"
            f"[s1][n1]amix=inputs=2:weights=0.8 0.4,"
            f"lowpass=f=240,"
            f"afade=t=in:st=0:d=0.01,"
            f"afade=t=out:st=0.05:d={max(0.08, dur - 0.05):.3f}:curve=exp,"
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
        "output_path": os.path.abspath(output_path),
        "duration": dur,
        "volume_db": vol_db,
        "size_bytes": os.path.getsize(output_path),
    }


def generate_room_tone(
    output_path: str,
    duration: float,
    preset: str = "warm_studio",
    volume_db: float | None = None,
    sample_rate: int = 48000,
    timeout: int = 60,
) -> tuple[bool, dict[str, Any]]:
    """Generate a subtle organic room-tone audio bed."""
    key = str(preset or "warm_studio").strip().lower()
    if key not in ROOM_TONE_PRESETS:
        valid = ", ".join(sorted(ROOM_TONE_PRESETS))
        return False, {"code": "UNKNOWN_ROOM_TONE", "message": f"Unknown room tone {preset!r}; choose from: {valid}"}
    spec = ROOM_TONE_PRESETS[key]
    dur = max(0.5, float(duration))
    vol_db = float(volume_db if volume_db is not None else spec["default_volume_db"])
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fade_out = min(0.8, dur * 0.2)
    fc = (
        f"anoisesrc=c={spec['color']}:r={sample_rate}:d={dur}:a=0.4,"
        f"{spec['filter']},"
        f"afade=t=in:st=0:d={min(0.4, dur * 0.1):.3f},"
        f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f},"
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
    return True, {"preset": key, "output_path": os.path.abspath(output_path), "duration": dur, "volume_db": vol_db}


def generate_ambient_music_bed(
    output_path: str,
    duration: float,
    preset: str = "documentary_drone",
    volume_db: float | None = None,
    sample_rate: int = 48000,
    timeout: int = 90,
) -> tuple[bool, dict[str, Any]]:
    """Generate a smooth harmonic ambient music bed using pure FFmpeg `lavfi`."""
    key = str(preset or "documentary_drone").strip().lower()
    if key not in MUSIC_BED_PRESETS:
        valid = ", ".join(sorted(MUSIC_BED_PRESETS))
        return False, {"code": "UNKNOWN_MUSIC_PRESET", "message": f"Unknown preset {preset!r}; choose from: {valid}"}
    spec = MUSIC_BED_PRESETS[key]
    dur = max(0.5, float(duration))
    vol_db = float(volume_db if volume_db is not None else spec["default_volume_db"])
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    parts = [f"sine=f={f}:r={sample_rate}:d={dur}[s{i}]" for i, f in enumerate(spec["freqs"])]
    ins = "".join(f"[s{i}]" for i in range(len(spec["freqs"])))
    weights = " ".join(str(w) for w in spec["weights"])
    fade_in = min(1.0, dur * 0.15)
    fade_out = min(1.4, dur * 0.25)
    fc = (
        ";".join(parts) + ";"
        f"{ins}amix=inputs={len(spec['freqs'])}:weights={weights},"
        f"lowpass=f={spec['lowpass']},"
        f"afade=t=in:st=0:d={fade_in:.3f}:curve=qsin,"
        f"afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}:curve=qsin,"
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
        return False, {"code": "MUSIC_BED_FAILED", "message": proc.stderr.strip() or "ffmpeg failed"}
    return True, {"preset": key, "output_path": os.path.abspath(output_path), "duration": dur, "volume_db": vol_db}


def plan_scene_sfx(
    spec: dict,
    timings: list[dict],
    beat_profiles: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Plan scene-entry SFX cues and optional acoustic beat-synced emphasis SFX."""
    scenes_by_id = {sc["id"]: sc for sc in spec.get("scenes", [])}
    auto = bool(spec.get("auto_sfx", False))
    cues: list[dict[str, Any]] = []
    for i, tm in enumerate(timings):
        sid = tm["id"]
        sc = scenes_by_id.get(sid, {})
        explicit = sc.get("sfx")
        if explicit is False or explicit == "none":
            continue
        kind = None
        if isinstance(explicit, str) and explicit.strip():
            kind = explicit.strip().lower()
        elif auto:
            kind = _DEFAULT_SCENE_SFX.get(sc.get("type", ""))
        if kind and kind in SFX_CATALOG:
            cues.append({
                "scene_id": sid,
                "kind": kind,
                "start": round(float(tm["start"]), 3),
                "volume": 0.65 if i > 0 else 0.75,
            })
        # Add beat-synced micro-accent SFX when requested
        if beat_profiles and sid in beat_profiles:
            for emp in beat_profiles[sid].get("emphasis_sfx") or []:
                ek = emp.get("kind", "")
                if ek in SFX_CATALOG:
                    cues.append({
                        "scene_id": sid,
                        "kind": ek,
                        "start": round(float(emp["start"]), 3),
                        "volume": float(emp.get("volume", 0.42)),
                    })
    return cues


def plan_jl_cut_schedule(
    spec: dict[str, Any],
    timings: list[dict[str, Any]],
    default_j_cut: float = 0.0,
    default_l_cut: float = 0.0,
) -> list[dict[str, Any]]:
    """Plan documentary J-Cut (audio leads visual cut) and L-Cut (audio lingers
    with soft fade-out) speech track parameters for `mix_audio`.

    Notice: visual scene timings stay anchored to narration durations (voice is
    never sped up or squeezed); J-Cut advances a scene's audio start by up to
    `j_cut` seconds (clamped to <=0.35s) with a smooth fade-in, while L-Cut
    applies a soft fade-out tail.
    """
    scenes_by_id = {sc["id"]: sc for sc in spec.get("scenes", [])}
    global_j = float(spec.get("j_cut", default_j_cut) or 0.0)
    global_l = float(spec.get("l_cut", default_l_cut) or 0.0)
    schedule: list[dict[str, Any]] = []

    for i, tm in enumerate(timings):
        sid = tm["id"]
        sc = scenes_by_id.get(sid, {})
        j_sec = float(sc.get("j_cut", global_j) or 0.0) if i > 0 else 0.0
        l_sec = float(sc.get("l_cut", global_l) or 0.0)
        j_sec = max(0.0, min(0.35, j_sec))
        l_sec = max(0.0, min(0.45, l_sec))
        audio_start = max(0.0, round(float(tm["start"]) - j_sec, 3))
        schedule.append({
            "scene_id": sid,
            "visual_start": round(float(tm["start"]), 3),
            "audio_start": audio_start,
            "j_cut": round(j_sec, 3),
            "l_cut": round(l_sec, 3),
            "fade_in": round(min(0.08, j_sec * 0.5), 3) if j_sec > 0 else 0.0,
            "fade_out": round(min(0.12, l_sec * 0.5), 3) if l_sec > 0 else 0.0,
        })
    return schedule
