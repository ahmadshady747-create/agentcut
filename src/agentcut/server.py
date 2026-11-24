"""Minimal MCP stdio server for AgentCut v3.4.1 (Browser-only production engine with Acoustic Beat-Sync)."""
from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .beat_sync import analyze_audio_beats, build_scene_beat_profile
from .browser_render import DEFAULT_LOUDNESS_TARGET, audit_audio_mix
from .flat_reels import (
    FORMATS,
    MOTION_PRESETS,
    PALETTES,
    CAMERA_MOVES,
    PHYSICS_MODES,
    SCENE_TYPES,
    TRANSITIONS,
    build_video_html,
    compute_scene_timings,
    measure_narration_pace,
    render_video,
    render_video_frame,
    validate_reel_spec,
)
from .brand_kits import BRAND_KITS, apply_brand_kit, list_brand_kits
from .captions import CAPTION_STYLES
from .director_browser import script_to_spec
from .preflight import doctor_environment, preflight_audit
from .multi_export import export_multi_format, render_thumbnail_html, render_thumbnail_png
from .scene_patch import generate_hook_ab_variants, patch_scene
from .visual_qc import audit_html_motion_continuity, audit_rendered_frames, render_contact_sheet
from .icons import list_icons, list_illustrations, search_icons
from .soundscape import (
    generate_ambient_music_bed,
    generate_room_tone,
    list_music_presets,
    list_sfx_kinds,
    synthesize_sfx,
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "script_to_spec",
        "description": "Convert raw Arabic or mixed script text into a varied multi-template AgentCut spec.json with automatic domain icon matching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_text": {"type": "string"},
                "title": {"type": "string"},
                "spec_id": {"type": "string"},
                "brand_kit": {"type": "string"},
                "format_name": {"type": "string"},
                "target_duration": {"type": "number"},
            },
            "required": ["script_text"],
        },
    },
    {
        "name": "preflight_audit",
        "description": "Run pre-render audit checking spec schema, template variety, narration pace (WPS & trim recommendations), and setTime HTML determinism.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "base_dir": {"type": "string"},
                "require_audio_files": {"type": "boolean"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "doctor_environment",
        "description": "Inspect runtime readiness (Python, FFmpeg, FFprobe, Playwright/Chromium, bundled OFL Arabic fonts, /dev/shm RAM-disk).",
        "inputSchema": {
            "type": "object",
            "properties": {"probe_browser": {"type": "boolean"}},
        },
    },
    {
        "name": "patch_scene",
        "description": "Surgically patch a single scene by ID and return the updated spec plus SHA-256 scene cache hit/invalidation breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "scene_id": {"type": "string"},
                "updates": {"type": "object"},
            },
            "required": ["spec", "scene_id", "updates"],
        },
    },
    {
        "name": "generate_hook_ab",
        "description": "Generate A/B hook variants for Scene 1 (first 3-5 seconds) while sharing cached fingerprints for Scenes 2..N.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "variants": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "visual_qc",
        "description": "Audit generated HTML or rendered PNG frames for setTime determinism, continuous motion, WCAG contrast, or build a grid contact sheet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "frames_dir": {"type": "string"},
                "contact_sheet_source": {"type": "string"},
                "contact_sheet_output": {"type": "string"},
                "cols": {"type": "integer"},
                "rows": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_brand_kits",
        "description": "List all reusable channel Brand Kits (palette, caption_style, motion, physics, camera_move, transition, music_bed).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "render_thumbnail",
        "description": "Generate a high-contrast flat-editorial Thumbnail HTML or PNG (1280x720 or 720x1280) from a video spec.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "output_path": {"type": "string"},
                "format_name": {"type": "string"},
                "html_only": {"type": "boolean"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "export_multi_format",
        "description": "Export a single video spec into multiple aspect-ratio MP4 files (16:9, 9:16, 1:1) plus a Thumbnail PNG.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "output_dir": {"type": "string"},
                "base_dir": {"type": "string"},
                "formats": {"type": "array", "items": {"type": "string"}},
                "include_thumbnail": {"type": "boolean"},
            },
            "required": ["spec", "output_dir"],
        },
    },
    {
        "name": "render_video",
        "description": "Render any AgentCut video (vertical 9:16 reel, landscape 16:9 explainer, or square 1:1) in headless Chromium and encode with FFmpeg.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "output_path": {"type": "string"},
                "base_dir": {"type": "string"},
                "scale": {"type": "number"},
                "keep_frames": {"type": "boolean"},
            },
            "required": ["spec", "output_path"],
        },
    },
    {
        "name": "render_video_frame",
        "description": "Capture a single deterministic preview frame at time t (seconds).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "t": {"type": "number"},
                "output_path": {"type": "string"},
                "base_dir": {"type": "string"},
                "scale": {"type": "number"},
            },
            "required": ["spec", "t", "output_path"],
        },
    },
    {
        "name": "validate_spec",
        "description": "Validate a video spec JSON before rendering.",
        "inputSchema": {
            "type": "object",
            "properties": {"spec": {"type": "object"}},
            "required": ["spec"],
        },
    },
    {
        "name": "measure_narration_pace",
        "description": "Measure actual words-per-second per scene from probed audio files and recommend script trimming if over target_duration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object"},
                "base_dir": {"type": "string"},
                "target_duration": {"type": "number"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "analyze_audio_beats",
        "description": "Extract physical RMS energy peaks, natural pauses, and beat-synced visual/SFX anchor timings from a narration WAV or full spec.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "spec": {"type": "object"},
                "base_dir": {"type": "string"},
            },
        },
    },
    {
        "name": "audit_audio_mix",
        "description": "Audit a WAV/MP4 audio track for EBU R128 integrated loudness (-14 LUFS) and True Peak ceiling (-1.0 dBTP).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "target_lufs": {"type": "number"},
                "tolerance_lufs": {"type": "number"},
                "max_true_peak_dbtp": {"type": "number"},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "list_design_system",
        "description": "List all palettes, formats, scene types, transitions, motion presets, icons, SFX kinds, and music bed presets.",
        "inputSchema": {"type": "object", "properties": {"icon_query": {"type": "string"}}},
    },
    {
        "name": "synthesize_sfx",
        "description": "Synthesize a procedural cinema SFX WAV using pure FFmpeg lavfi.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "output_path": {"type": "string"},
                "duration": {"type": "number"},
                "volume_db": {"type": "number"},
            },
            "required": ["kind", "output_path"],
        },
    },
    {
        "name": "generate_music_bed",
        "description": "Generate an ambient harmonic music bed WAV.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "duration": {"type": "number"},
                "preset": {"type": "string"},
                "volume_db": {"type": "number"},
            },
            "required": ["output_path", "duration"],
        },
    },
    {
        "name": "generate_room_tone",
        "description": "Generate a subtle organic room-tone audio bed WAV.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "duration": {"type": "number"},
                "preset": {"type": "string"},
                "volume_db": {"type": "number"},
            },
            "required": ["output_path", "duration"],
        },
    },
]


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "render_video":
        ok, info = render_video(
            args["spec"], args["output_path"],
            base_dir=args.get("base_dir", "."),
            scale=float(args.get("scale", 1.0)),
            keep_frames=bool(args.get("keep_frames", False)))
        return {"ok": ok, **info}
    if name == "render_video_frame":
        ok, info = render_video_frame(
            args["spec"], float(args["t"]), args["output_path"],
            base_dir=args.get("base_dir", "."),
            scale=float(args.get("scale", 1.0)))
        return {"ok": ok, **info}
    if name == "validate_spec":
        ok, errors = validate_reel_spec(args["spec"])
        return {"ok": ok, "errors": errors}
    if name == "measure_narration_pace":
        timings, _tot, errs = compute_scene_timings(args["spec"], args.get("base_dir", "."))
        if errs:
            return {"ok": False, "errors": errs}
        return {"ok": True, **measure_narration_pace(
            args["spec"], timings, target_duration=args.get("target_duration"))}
    if name == "analyze_audio_beats":
        if "spec" in args and isinstance(args["spec"], dict):
            base_dir = args.get("base_dir", ".")
            timings, _tot, errs = compute_scene_timings(args["spec"], base_dir)
            if errs:
                return {"ok": False, "errors": errs}
            prof = build_scene_beat_profile(args["spec"], timings, base_dir=base_dir)
            return {"ok": True, "scenes": prof}
        audio_path = str(args.get("audio_path", ""))
        res = analyze_audio_beats(audio_path)
        return {"ok": res["duration"] > 0, **res}
    if name == "audit_audio_mix":
        return audit_audio_mix(
            args["audio_path"],
            target_lufs=float(args.get("target_lufs", DEFAULT_LOUDNESS_TARGET)),
            tolerance_lufs=float(args.get("tolerance_lufs", 1.5)),
            max_true_peak_dbtp=float(args.get("max_true_peak_dbtp", -1.0)),
        )
    if name == "script_to_spec":
        spec = script_to_spec(
            args["script_text"],
            title=str(args.get("title", "")),
            spec_id=str(args.get("spec_id", "auto-reel")),
            brand_kit=str(args.get("brand_kit", "tab_ezzay")),
            format_name=str(args.get("format_name", "vertical")),
            target_duration=args.get("target_duration"),
        )
        return {"ok": True, "spec": spec}
    if name == "preflight_audit":
        return preflight_audit(
            args["spec"],
            base_dir=str(args.get("base_dir", ".")),
            require_audio_files=bool(args.get("require_audio_files", False)),
        )
    if name == "doctor_environment":
        return doctor_environment(probe_browser=bool(args.get("probe_browser", False)))
    if name == "patch_scene":
        ok, info = patch_scene(args["spec"], args["scene_id"], args["updates"])
        return {"ok": ok, **info}
    if name == "generate_hook_ab":
        variants = generate_hook_ab_variants(args["spec"], args.get("variants"))
        return {"ok": bool(variants), "count": len(variants), "variants": variants}
    if name == "visual_qc":
        out: dict[str, Any] = {"ok": True}
        if isinstance(args.get("spec"), dict):
            spec = args["spec"]
            fake_timings = [
                {"id": sc["id"], "start": i * 2.0, "duration": 2.0, "words": []}
                for i, sc in enumerate(spec.get("scenes") or [])
            ]
            html = build_video_html(spec, fake_timings, embed_font=False)
            ha = audit_html_motion_continuity(html, spec)
            out["html_audit"] = ha
            out["ok"] = out["ok"] and ha["ok"]
        if args.get("frames_dir"):
            fa = audit_rendered_frames(args["frames_dir"])
            out["frames_audit"] = fa
            out["ok"] = out["ok"] and fa["ok"]
        if args.get("contact_sheet_source") and args.get("contact_sheet_output"):
            ok_cs, cs_info = render_contact_sheet(
                args["contact_sheet_source"],
                args["contact_sheet_output"],
                cols=int(args.get("cols", 3)),
                rows=int(args.get("rows", 2)),
            )
            out["contact_sheet"] = cs_info
            out["ok"] = out["ok"] and ok_cs
        return out
    if name == "list_brand_kits":
        return {"ok": True, "brand_kits": list_brand_kits()}
    if name == "render_thumbnail":
        fmt = str(args.get("format_name", "landscape"))
        if args.get("html_only") or not args.get("output_path"):
            html = render_thumbnail_html(args["spec"], format_name=fmt, embed_font=False)
            return {"ok": True, "format": fmt, "html": html}
        ok, info = render_thumbnail_png(args["spec"], args["output_path"], format_name=fmt)
        return {"ok": ok, **info}
    if name == "export_multi_format":
        fmts = tuple(args.get("formats") or ("landscape", "vertical", "square"))
        ok, info = export_multi_format(
            args["spec"], args["output_dir"],
            base_dir=args.get("base_dir", "."),
            formats=fmts,
            include_thumbnail=bool(args.get("include_thumbnail", True)),
        )
        return {"ok": ok, **info}
    if name == "list_design_system":
        q = args.get("icon_query", "")
        return {
            "version": __version__,
            "palettes": sorted(PALETTES),
            "formats": {k: list(v) for k, v in FORMATS.items()},
            "scene_types": list(SCENE_TYPES),
            "transitions": list(TRANSITIONS),
            "motion_presets": sorted(MOTION_PRESETS),
            "physics_modes": list(PHYSICS_MODES),
            "camera_moves": list(CAMERA_MOVES),
            "brand_kits": sorted(BRAND_KITS),
            "caption_styles": list(CAPTION_STYLES),
            "illustrations": list_illustrations(),
            "icons": search_icons(q) if q else list_icons(),
            "sfx": list_sfx_kinds(),
            "music_beds": list_music_presets(),
        }
    if name == "synthesize_sfx":
        ok, info = synthesize_sfx(
            args["kind"], args["output_path"],
            duration=args.get("duration"), volume_db=args.get("volume_db"))
        return {"ok": ok, **info}
    if name == "generate_music_bed":
        ok, info = generate_ambient_music_bed(
            args["output_path"], float(args["duration"]),
            preset=args.get("preset", "documentary_drone"),
            volume_db=args.get("volume_db"))
        return {"ok": ok, **info}
    if name == "generate_room_tone":
        ok, info = generate_room_tone(
            args["output_path"], float(args["duration"]),
            preset=args.get("preset", "warm_studio"),
            volume_db=args.get("volume_db"))
        return {"ok": ok, **info}
    return {"ok": False, "error": f"Unknown tool: {name}"}


def run_stdio_server() -> None:
    """Minimal JSON-RPC 2.0 MCP stdio loop."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        req_id = req.get("id")
        method = req.get("method", "")
        if method == "initialize":
            res = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "agentcut", "version": __version__},
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params") or {}
            out = dispatch_tool(p.get("name", ""), p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
        else:
            res = {}
        if req_id is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": res},
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    """Main entrypoint for agentcut-mcp console script."""
    run_stdio_server()


if __name__ == "__main__":
    main()
