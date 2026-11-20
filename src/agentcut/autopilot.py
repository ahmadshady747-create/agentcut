"""Single-Call Model-Agnostic Autopilot Orchestrator (v3.1.0).

Makes AgentCut virtually immune to weak/small LLM limitations: a caller can
pass a raw Arabic essay, a messy/broken JSON string, or a partial brief in ONE
single call, and Autopilot deterministically runs:
  1. `repair_brief()` — fixes fences, trailing commas, aliases, typos, missing fields.
  2. `polish_script_scenes()` — strips Markdown noise, splits run-on walls of text,
     and generates missing `.srt` subtitle files automatically.
  3. `generate_visual_prompts()` — compiles Style-Bible-locked cinematic prompts
     for AgentMedia with alternating shot scales.
  4. `humanize_brief()` — applies narrative pacing, organic camera choreography,
     varied transitions, restrained highlight cards, and soundscape plan.
  5. If `output_path` is provided and scenes have narration audio, executes
     `produce_cinematic_auto_heal()` (focal points, exposure matching, live
     soundscape mixing, and QC self-healing); otherwise returns the complete
     AgentMedia-ready Production Blueprint.
Zero external dependencies.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from .art_director import generate_visual_prompts
from .brief_repair import repair_brief
from .director import humanize_brief
from .pipeline.auto_heal import produce_cinematic_auto_heal
from .profiles import apply_profile_to_brief
from .qc import audit_editorial_craft
from .script_polisher import polish_script_scenes


def autopilot_produce(
    raw_input: Any,
    output_path: str | None = None,
    cinema_look: str = "chiaroscuro_doc",
    aspect: str | None = None,
    profile: str = "low_spec",
    srt_dir: str | None = None,
    mix_soundscape: bool = True,
) -> tuple[bool, dict[str, Any]]:
    """One-shot model-proof entry point from raw text or messy JSON to blueprint or finished MP4."""
    repaired, repairs_made = repair_brief(raw_input)
    if aspect:
        repaired["aspect"] = aspect
    if cinema_look and not repaired.get("cinema_look"):
        repaired["cinema_look"] = cinema_look

    # Use persistent srt_dir if given, else temp dir when rendering
    temp_srt_ctx = None
    active_srt_dir = srt_dir
    if output_path and not active_srt_dir:
        temp_srt_ctx = tempfile.TemporaryDirectory(prefix="agentcut-autosrt-")
        active_srt_dir = temp_srt_ctx.name

    try:
        polished, polish_stats = polish_script_scenes(repaired, srt_dir=active_srt_dir)
        look_final = str(polished.get("cinema_look") or cinema_look or "chiaroscuro_doc")
        directed = humanize_brief(
            polished,
            cinema_look=look_final,
            add_soundscape=bool(mix_soundscape),
            auto_cards=True,
        )
        directed = apply_profile_to_brief(directed, profile)
        visual_plan = generate_visual_prompts(directed, style_bible=look_final, aspect=directed.get("aspect"))
        craft = audit_editorial_craft(directed)

        # Check if scenes have real local narration files ready to render
        scenes = directed.get("scenes") or []
        can_render = bool(
            output_path
            and scenes
            and all(isinstance(s.get("narration"), str) and os.path.isfile(s["narration"]) for s in scenes)
        )

        if can_render and output_path:
            ok, render_res = produce_cinematic_auto_heal(
                directed,
                output_path,
                cinema_look=look_final,
                profile=profile,
                auto_focal=True,
                match_exposure=True,
                mix_soundscape=mix_soundscape,
            )
            return ok, {
                "mode": "rendered",
                "output": output_path,
                "repairs_made": repairs_made,
                "polish_stats": polish_stats,
                "craft_audit": craft,
                "visual_prompts": visual_plan,
                "auto_heal": render_res,
            }

        return True, {
            "mode": "blueprint",
            "ready_to_render": False,
            "reason": "Provide local narration audio per scene (via AgentMedia or agentcut tts) and pass output_path to render.",
            "repairs_made": repairs_made,
            "polish_stats": polish_stats,
            "craft_audit": craft,
            "visual_prompts": visual_plan,
            "brief": directed,
        }
    finally:
        if temp_srt_ctx is not None:
            temp_srt_ctx.cleanup()
