"""Self-Healing Cinematic Production Loop (v3.0.0).

Closes the autonomous loop for AI agents:
  1. Pre-render craft audit (`audit_editorial_craft`).
  2. Automatic humanization (`humanize_brief`) + optional focal-point detection
     and exposure harmonization (`compile_brief_to_ir`) when craft score < 85.
  3. Render with live soundscape mixing (`room_tone` + procedural SFX).
  4. Post-render technical QC (`audit_video_quality`).
  5. Automatic repair & retry (up to `max_retries`) if QC detects clipping,
     quiet audio, or other fixable issues.
Zero external dependencies.
"""

from __future__ import annotations

from typing import Any

from ..director import humanize_brief
from ..profiles import apply_profile_to_brief
from ..qc import audit_editorial_craft, audit_video_quality, inspect_rendered_video_frames
from .ir import compile_brief_to_ir


def produce_cinematic_auto_heal(
    brief: dict[str, Any],
    output_path: str,
    cinema_look: str = "chiaroscuro_doc",
    profile: str | None = "low_spec",
    auto_focal: bool = True,
    match_exposure: bool = True,
    mix_soundscape: bool = True,
    clean_tts: bool = True,
    min_craft_score: int = 85,
    max_retries: int = 1,
) -> tuple[bool, dict[str, Any]]:
    """Run the full self-healing documentary production pipeline end-to-end."""
    from ..render import produce_explainer

    working = dict(brief)
    craft_before = audit_editorial_craft(working)
    healed_actions: list[str] = []

    # 1. Pre-render craft healing
    if craft_before["score"] < min_craft_score or not working.get("humanized"):
        working = humanize_brief(
            working,
            cinema_look=str(working.get("cinema_look") or cinema_look),
            add_soundscape=bool(mix_soundscape),
            auto_cards=True,
        )
        healed_actions.append("applied_humanize_brief")

    if profile:
        working = apply_profile_to_brief(working, profile)

    # 2. Visual intelligence compilation (focal points + exposure harmonization)
    ir = compile_brief_to_ir(working, auto_focal=auto_focal, match_exposure=match_exposure)
    if auto_focal or match_exposure:
        for sc_ir, sc_dict in zip(ir.scenes, working.get("scenes") or []):
            beats_dict = sc_dict.get("beats")
            if isinstance(beats_dict, list) and len(beats_dict) == len(sc_ir.beats):
                for b_ir, b_dict in zip(sc_ir.beats, beats_dict):
                    b_dict["focal_x"] = b_ir.focal_x
                    b_dict["focal_y"] = b_ir.focal_y
                    if b_ir.exposure_vf:
                        b_dict["exposure_vf"] = b_ir.exposure_vf
            elif sc_ir.beats:
                sc_dict["focal_x"] = sc_ir.beats[0].focal_x
                sc_dict["focal_y"] = sc_ir.beats[0].focal_y
                if sc_ir.beats[0].exposure_vf:
                    sc_dict["exposure_vf"] = sc_ir.beats[0].exposure_vf
        if auto_focal:
            healed_actions.append("detected_focal_points")
        if match_exposure and ir.exposure_target_yavg is not None:
            healed_actions.append(f"matched_exposure_yavg_{ir.exposure_target_yavg}")

    working["mix_soundscape"] = bool(mix_soundscape)
    working["clean_tts"] = bool(clean_tts)
    if clean_tts:
        healed_actions.append("enabled_auto_clean_tts")
    craft_after = audit_editorial_craft(working)

    # 3. Render + QC + Auto-Heal retry loop
    attempts = 0
    last_info: dict[str, Any] = {}
    last_qc: dict[str, Any] = {}
    last_frame_insp: dict[str, Any] = {}
    ok = False

    for attempt in range(max(1, int(max_retries) + 1)):
        attempts = attempt + 1
        ok, info = produce_explainer(working, output_path)
        last_info = dict(info) if isinstance(info, dict) else {"result": info}
        if not ok:
            break
        last_qc = audit_video_quality(output_path)
        last_frame_insp = inspect_rendered_video_frames(
            output_path,
            project_or_brief=last_info.get("project") if isinstance(last_info, dict) else working,
            sample_count=4,
        )
        if last_qc.get("passed") and last_frame_insp.get("passed", True):
            break
        # Apply deterministic repairs for fixable QC issues
        codes = {i.get("code") for i in last_qc.get("issues", [])}
        repaired = False
        if "AUDIO_CLIPPING" in codes or "AUDIO_TOO_QUIET" in codes:
            working["master_loudnorm"] = True
            working["music_volume"] = min(float(working.get("music_volume", 0.18)), 0.12)
            healed_actions.append(f"attempt_{attempts}_audio_loudnorm_fix")
            repaired = True
        if not repaired:
            break

    last_info.pop("project", None)
    return ok, {
        "output": output_path,
        "attempts": attempts,
        "healed": len(healed_actions) > 0,
        "healed_actions": healed_actions,
        "craft_before": craft_before,
        "craft_after": craft_after,
        "qc": last_qc,
        "frame_inspection": last_frame_insp,
        "ir_summary": {
            "scenes": len(ir.scenes),
            "total_duration": ir.total_duration,
            "exposure_target_yavg": ir.exposure_target_yavg,
            "cinema_look": ir.cinema_look,
        },
        "render": last_info,
    }
