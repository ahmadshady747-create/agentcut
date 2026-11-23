"""Single-Call Model-Agnostic Autopilot Orchestrator with Stage Approval Gates (v3.3.11).

Makes AgentCut virtually immune to weak/small LLM limitations while supporting
human-in-the-loop approval checkpoints (`stop_at`) so long renders never run
blindly on unreviewed scripts or visual layouts:
  1. `repair_brief()` — fixes fences, trailing commas, aliases, typos, missing fields.
  2. `polish_script_scenes()` — strips Markdown noise, splits run-on walls of text,
     and generates missing `.srt` subtitle files automatically.
     -> Gate `"script"`: pauses after script polishing so the user/agent can inspect,
        edit, or cancel the polished Arabic/English scene script before directing or rendering.
  3. `generate_visual_prompts()` & `humanize_brief()` — compiles Style-Bible-locked
     cinematic prompts, organic camera moves, and soundscape plan.
     -> Gate `"preview"`: renders an ultra-fast `proxy` preview MP4 + contact sheet
        (`contact_sheet.png`) + sample review frames + `inspect_rendered_video_frames()`
        so the user can inspect the exact look before committing to the full master render.
     -> Gate `"pre_render"`: pauses right before final full-resolution rendering with
        the complete directed brief, craft audit score, and hardware profile plan.
  4. `resume_autopilot_checkpoint()` — resumes any paused checkpoint (with optional
     scene text edits or brief overrides), advances to the next gate or final render,
     or cancels cleanly (`action="cancel"`).
  5. Full automatic mode (`stop_at=None` or `"none"`) remains the default for
     zero-pause one-shot execution. Zero external dependencies, zero Telegram/webhook bloat.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .art_director import generate_visual_prompts
from .brief_repair import repair_brief
from .cache import get_cache_dir
from .director import humanize_brief
from .pipeline.auto_heal import produce_cinematic_auto_heal
from .profiles import apply_profile_to_brief, detect_hardware_profile
from .qc import audit_editorial_craft, inspect_rendered_video_frames
from .render import produce_explainer, render_contact_sheet
from .script_polisher import polish_script_scenes


VALID_APPROVAL_GATES = ("none", "script", "preview", "pre_render")


def _checkpoints_dir(project_dir: str | None = None) -> Path:
    cdir = get_cache_dir(project_dir) / "checkpoints"
    cdir.mkdir(parents=True, exist_ok=True)
    return cdir


def _save_checkpoint_payload(payload: dict[str, Any], checkpoint_path: str | None = None, project_dir: str | None = None) -> str:
    cid = str(payload.get("checkpoint_id") or f"chk_{uuid.uuid4().hex[:12]}")
    payload["checkpoint_id"] = cid
    if checkpoint_path:
        target = Path(os.path.abspath(checkpoint_path))
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = _checkpoints_dir(project_dir) / f"{cid}.json"
    payload["checkpoint_path"] = str(target)
    tmp_p = target.with_suffix(".json.tmp")
    with open(tmp_p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_p, target)
    return str(target)


def load_autopilot_checkpoint(checkpoint_id_or_path: str, project_dir: str | None = None) -> dict[str, Any]:
    """Load a saved autopilot approval-gate checkpoint by path or ID."""
    cand = Path(str(checkpoint_id_or_path))
    if not cand.is_file():
        cand = _checkpoints_dir(project_dir) / f"{str(checkpoint_id_or_path).removesuffix('.json')}.json"
    if not cand.is_file():
        raise FileNotFoundError(f"Autopilot checkpoint not found: {checkpoint_id_or_path!r}")
    with open(cand, encoding="utf-8") as fh:
        data = json.load(fh)
    data["checkpoint_path"] = str(cand.resolve())
    return data


def _resolve_profile(profile: str | None) -> str:
    p = str(profile or "low_spec").strip().lower()
    if p == "auto":
        return str(detect_hardware_profile().get("recommended_profile") or "low_spec")
    return p


def _normalize_gate(stop_at: str | None) -> str:
    if not stop_at:
        return "none"
    g = str(stop_at).strip().lower().replace("-", "_")
    if g in ("script_gate", "after_script", "polish"):
        return "script"
    if g in ("preview_gate", "frames", "contact_sheet", "after_preview"):
        return "preview"
    if g in ("final", "before_render", "render_gate"):
        return "pre_render"
    if g not in VALID_APPROVAL_GATES:
        raise ValueError(f"Invalid stop_at gate {stop_at!r}; choose from: {', '.join(VALID_APPROVAL_GATES)}")
    return g


def autopilot_produce(
    raw_input: Any,
    output_path: str | None = None,
    cinema_look: str = "chiaroscuro_doc",
    aspect: str | None = None,
    profile: str = "low_spec",
    srt_dir: str | None = None,
    mix_soundscape: bool = True,
    clean_tts: bool = True,
    stop_at: str | None = None,
    checkpoint_path: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Model-proof entry point from raw text or messy JSON to approval checkpoint, blueprint, or finished MP4."""
    gate = _normalize_gate(stop_at)
    resolved_profile = _resolve_profile(profile)
    repaired, repairs_made = repair_brief(raw_input)
    if aspect:
        repaired["aspect"] = aspect
    if cinema_look and not repaired.get("cinema_look"):
        repaired["cinema_look"] = cinema_look

    project_dir = os.path.dirname(os.path.abspath(output_path)) if output_path else None

    # Use persistent srt_dir when pausing at a checkpoint so SRT paths survive across resume calls
    temp_srt_ctx = None
    active_srt_dir = srt_dir
    if not active_srt_dir:
        if gate != "none":
            srt_cache = get_cache_dir(project_dir) / "autosrt"
            srt_cache.mkdir(parents=True, exist_ok=True)
            active_srt_dir = str(srt_cache)
        elif output_path:
            temp_srt_ctx = tempfile.TemporaryDirectory(prefix="agentcut-autosrt-")
            active_srt_dir = temp_srt_ctx.name

    try:
        polished, polish_stats = polish_script_scenes(repaired, srt_dir=active_srt_dir)
        look_final = str(polished.get("cinema_look") or cinema_look or "chiaroscuro_doc")

        # --- GATE 1: Stop after polished script for reading/editing before any directing or rendering ---
        if gate == "script":
            chk_id = f"chk_script_{uuid.uuid4().hex[:10]}"
            script_review = [
                {
                    "index": idx + 1,
                    "id": sc.get("id", f"scene_{idx + 1}"),
                    "title": sc.get("title"),
                    "text": sc.get("text", ""),
                    "duration": sc.get("duration"),
                    "captions": sc.get("captions"),
                }
                for idx, sc in enumerate(polished.get("scenes") or [])
            ]
            payload = {
                "mode": "paused",
                "status": "awaiting_approval",
                "gate": "script",
                "next_gate": "preview",
                "checkpoint_id": chk_id,
                "created_at": round(time.time(), 3),
                "output_path": output_path,
                "cinema_look": look_final,
                "profile": resolved_profile,
                "srt_dir": active_srt_dir,
                "mix_soundscape": bool(mix_soundscape),
                "clean_tts": bool(clean_tts),
                "repairs_made": repairs_made,
                "polish_stats": polish_stats,
                "script_review": script_review,
                "brief": polished,
                "instructions": (
                    "Review or edit scene texts in `brief.scenes` (or pass `edits={'scenes': [...]}`), "
                    "then call `resume_autopilot` with `action='continue'` (or `stop_at='preview'` / `'none'`)."
                ),
            }
            _save_checkpoint_payload(payload, checkpoint_path=checkpoint_path, project_dir=project_dir)
            return True, payload

        directed = humanize_brief(
            polished,
            cinema_look=look_final,
            add_soundscape=bool(mix_soundscape),
            auto_cards=True,
        )
        directed["clean_tts"] = bool(clean_tts)
        directed = apply_profile_to_brief(directed, resolved_profile)
        visual_plan = generate_visual_prompts(directed, style_bible=look_final, aspect=directed.get("aspect"))
        craft = audit_editorial_craft(directed)

        scenes = directed.get("scenes") or []
        can_render = bool(
            scenes
            and all(isinstance(s.get("narration"), str) and os.path.isfile(s["narration"]) for s in scenes)
        )

        # --- GATE 2: Stop after generating proxy preview frames & contact sheet ---
        if gate == "preview":
            chk_id = f"chk_preview_{uuid.uuid4().hex[:10]}"
            preview_dir = _checkpoints_dir(project_dir) / f"{chk_id}_preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_info: dict[str, Any] = {
                "preview_dir": str(preview_dir),
                "can_render_video_preview": can_render,
            }
            if can_render:
                preview_brief = apply_profile_to_brief(dict(directed), "proxy")
                preview_mp4 = str(preview_dir / "preview_proxy.mp4")
                ok_p, p_res = produce_explainer(preview_brief, preview_mp4, review_dir=str(preview_dir / "frames"))
                if ok_p and os.path.isfile(preview_mp4):
                    sheet_png = str(preview_dir / "contact_sheet.png")
                    ok_cs, cs_res = render_contact_sheet(preview_mp4, sheet_png)
                    f_insp = inspect_rendered_video_frames(
                        preview_mp4,
                        project_or_brief=p_res.get("project"),
                        sample_count=3,
                        save_frames_dir=str(preview_dir / "inspected_frames"),
                    )
                    preview_info.update({
                        "preview_video": preview_mp4,
                        "contact_sheet": sheet_png if ok_cs else None,
                        "review_frames": p_res.get("review_frames", []),
                        "frame_inspection": f_insp,
                    })
                else:
                    preview_info["preview_error"] = p_res
            payload = {
                "mode": "paused",
                "status": "awaiting_approval",
                "gate": "preview",
                "next_gate": "pre_render",
                "checkpoint_id": chk_id,
                "created_at": round(time.time(), 3),
                "output_path": output_path,
                "cinema_look": look_final,
                "profile": resolved_profile,
                "srt_dir": active_srt_dir,
                "mix_soundscape": bool(mix_soundscape),
                "clean_tts": bool(clean_tts),
                "repairs_made": repairs_made,
                "polish_stats": polish_stats,
                "craft_audit": craft,
                "visual_prompts": visual_plan,
                "preview": preview_info,
                "brief": directed,
                "instructions": (
                    "Inspect the contact sheet and preview frames in `preview`, then call `resume_autopilot` "
                    "with `action='continue'` to proceed to final render (or `action='cancel'` to abort)."
                ),
            }
            _save_checkpoint_payload(payload, checkpoint_path=checkpoint_path, project_dir=project_dir)
            return True, payload

        # --- GATE 3: Stop immediately before final full-resolution render ---
        if gate == "pre_render":
            chk_id = f"chk_prerender_{uuid.uuid4().hex[:10]}"
            payload = {
                "mode": "paused",
                "status": "awaiting_approval",
                "gate": "pre_render",
                "next_gate": "none",
                "checkpoint_id": chk_id,
                "created_at": round(time.time(), 3),
                "output_path": output_path,
                "cinema_look": look_final,
                "profile": resolved_profile,
                "srt_dir": active_srt_dir,
                "mix_soundscape": bool(mix_soundscape),
                "clean_tts": bool(clean_tts),
                "ready_to_render": bool(can_render and output_path),
                "repairs_made": repairs_made,
                "polish_stats": polish_stats,
                "craft_audit": craft,
                "visual_prompts": visual_plan,
                "brief": directed,
                "instructions": (
                    "Final pre-render gate: verify `craft_audit` and `brief`, then call `resume_autopilot` "
                    "with `action='continue'` to execute the full render."
                ),
            }
            _save_checkpoint_payload(payload, checkpoint_path=checkpoint_path, project_dir=project_dir)
            return True, payload

        # --- FULL EXECUTION (no stop gate) ---
        if can_render and output_path:
            ok, render_res = produce_cinematic_auto_heal(
                directed,
                output_path,
                cinema_look=look_final,
                profile=resolved_profile,
                auto_focal=True,
                match_exposure=True,
                mix_soundscape=mix_soundscape,
                clean_tts=clean_tts,
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


def resume_autopilot_checkpoint(
    checkpoint_id_or_path: str,
    action: str = "continue",
    edits: dict[str, Any] | None = None,
    output_path: str | None = None,
    stop_at: str | None = None,
    project_dir: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Resume, modify, or cancel a paused autopilot approval-gate checkpoint."""
    chk = load_autopilot_checkpoint(checkpoint_id_or_path, project_dir=project_dir)
    act = str(action or "continue").strip().lower()
    if act in ("cancel", "abort", "reject"):
        chk["mode"] = "cancelled"
        chk["status"] = "cancelled"
        _save_checkpoint_payload(chk, checkpoint_path=chk.get("checkpoint_path"), project_dir=project_dir)
        return True, {
            "mode": "cancelled",
            "status": "cancelled",
            "checkpoint_id": chk.get("checkpoint_id"),
            "checkpoint_path": chk.get("checkpoint_path"),
            "cancelled_at_gate": chk.get("gate"),
        }

    brief = dict(chk.get("brief") or {})
    if isinstance(edits, dict) and edits:
        if "title" in edits:
            brief["title"] = edits["title"]
        if "cinema_look" in edits:
            brief["cinema_look"] = edits["cinema_look"]
        if "profile" in edits:
            chk["profile"] = edits["profile"]
        if isinstance(edits.get("scenes"), list):
            # Allow either full replacement or per-scene text/broll patches by id or index
            curr_scenes = list(brief.get("scenes") or [])
            for idx, patch in enumerate(edits["scenes"]):
                if not isinstance(patch, dict):
                    continue
                matched = False
                pid = patch.get("id")
                if pid:
                    for sc in curr_scenes:
                        if sc.get("id") == pid:
                            if "text" in patch and patch["text"] != sc.get("text"):
                                sc["text"] = patch["text"]
                                sc.pop("captions", None)  # Force fresh SRT regeneration for edited text
                            sc.update({k: v for k, v in patch.items() if k != "text"})
                            matched = True
                            break
                if not matched and idx < len(curr_scenes):
                    sc = curr_scenes[idx]
                    if "text" in patch and patch["text"] != sc.get("text"):
                        sc["text"] = patch["text"]
                        sc.pop("captions", None)
                    sc.update({k: v for k, v in patch.items() if k != "text"})
            brief["scenes"] = curr_scenes
        for k, v in edits.items():
            if k not in ("scenes", "title", "cinema_look", "profile"):
                brief[k] = v

    target_out = output_path or chk.get("output_path")
    next_stop = _normalize_gate(stop_at) if stop_at is not None else "none"
    return autopilot_produce(
        brief,
        output_path=target_out,
        cinema_look=str(brief.get("cinema_look") or chk.get("cinema_look") or "chiaroscuro_doc"),
        aspect=brief.get("aspect"),
        profile=str(chk.get("profile") or "low_spec"),
        srt_dir=chk.get("srt_dir"),
        mix_soundscape=bool(chk.get("mix_soundscape", True)),
        clean_tts=bool(chk.get("clean_tts", True)),
        stop_at=next_stop,
        checkpoint_path=chk.get("checkpoint_path") if next_stop != "none" else None,
    )
