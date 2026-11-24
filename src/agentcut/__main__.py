"""AgentCut v3.4.1 CLI — Browser-rendered video production with Acoustic Beat-Sync (FFmpeg for encoding only)."""
from __future__ import annotations

import json
import os
import sys

from . import __version__

_USAGE = """AgentCut v{ver} — Browser-rendered video engine (FFmpeg for encoding & audio only)

Usage:
  agentcut render <spec.json> <out.mp4> [--palette NAME] [--format vertical|landscape|square]
                                        [--motion calm|normal|energetic] [--scale N] [--keep-frames]
  agentcut frame <spec.json> <t_sec> <out.png> [--palette NAME] [--format NAME] [--scale N]
  agentcut flat-reels <spec.json> <out.mp4> [options]        (alias for render)
  agentcut flat-reels-frame <spec.json> <t_sec> <out.png>    (alias for frame)
  agentcut validate <spec.json>
  agentcut pace <spec.json> [--target-duration SEC]
  agentcut beat-sync <audio_or_spec>
  agentcut audit-audio <audio.wav> [--target-lufs -14.0]
  agentcut icons [search_query]
  agentcut illustrations
  agentcut brand-kits
  agentcut thumbnail <spec.json> <out.png|out.html> [--format landscape|vertical] [--brand-kit ID]
  agentcut multi-export <spec.json> <out_dir> [--brand-kit ID]
  agentcut patch-scene <spec.json> <scene_id> <updates_json> [out_spec.json]
  agentcut hook-ab <spec.json> [out_dir]
  agentcut contact-sheet <frames_dir_or_mp4> <out.png> [--cols 3] [--rows 2]
  agentcut visual-qc <spec.json_or_frames_dir>
  agentcut script-to-spec <script.txt_or_text> [out_spec.json] [--brand-kit ID] [--format vertical|landscape|square]
  agentcut preflight <spec.json> [--no-audio]
  agentcut studio [--host 127.0.0.1] [--port 8765]
  agentcut doctor
  agentcut sfx-list
  agentcut music-list
  agentcut mcp
  agentcut version
""".format(ver=__version__)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _apply_common_flags(spec: dict, argv: list[str], start_idx: int) -> tuple[float, bool, str]:
    scale = 1.0
    keep_frames = False
    base_dir = os.path.dirname(os.path.abspath(argv[1]))
    idx = start_idx
    while idx < len(argv):
        flag = argv[idx]
        if flag == "--keep-frames":
            keep_frames = True
            idx += 1
        elif flag == "--palette" and idx + 1 < len(argv):
            spec["palette"] = argv[idx + 1]
            idx += 2
        elif flag == "--format" and idx + 1 < len(argv):
            spec["format"] = argv[idx + 1]
            idx += 2
        elif flag == "--motion" and idx + 1 < len(argv):
            spec["motion"] = argv[idx + 1]
            idx += 2
        elif flag == "--transition" and idx + 1 < len(argv):
            spec["transition"] = argv[idx + 1]
            idx += 2
        elif flag == "--caption-style" and idx + 1 < len(argv):
            spec["caption_style"] = argv[idx + 1]
            idx += 2
        elif flag == "--brand-kit" and idx + 1 < len(argv):
            spec["brand_kit"] = argv[idx + 1]
            idx += 2
        elif flag == "--physics" and idx + 1 < len(argv):
            spec["physics"] = argv[idx + 1]
            idx += 2
        elif flag == "--camera" and idx + 1 < len(argv):
            spec["camera_move"] = argv[idx + 1]
            idx += 2
        elif flag == "--scale" and idx + 1 < len(argv):
            scale = float(argv[idx + 1])
            idx += 2
        elif flag == "--base-dir" and idx + 1 < len(argv):
            base_dir = argv[idx + 1]
            idx += 2
        else:
            idx += 1
    return scale, keep_frames, base_dir


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0

    cmd = args[0]
    if cmd in ("version", "--version", "-v"):
        print(__version__)
        return 0

    if cmd == "doctor":
        from .preflight import doctor_environment
        res = doctor_environment(probe_browser="--probe-browser" in args)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    if cmd == "studio":
        from .studio_web import run_studio_server
        host = "127.0.0.1"
        port = 8765
        if "--host" in args:
            host = args[args.index("--host") + 1]
        if "--port" in args:
            port = int(args[args.index("--port") + 1])
        run_studio_server(host=host, port=port)
        return 0

    if cmd == "preflight" and len(args) >= 2:
        from .preflight import preflight_audit
        spec = _load(args[1])
        base_dir = os.path.dirname(os.path.abspath(args[1]))
        req_audio = "--no-audio" not in args
        res = preflight_audit(spec, base_dir=base_dir, require_audio_files=req_audio)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    if cmd == "script-to-spec" and len(args) >= 2:
        from .director_browser import script_to_spec
        raw_arg = args[1]
        text = open(raw_arg, encoding="utf-8").read() if os.path.isfile(raw_arg) else raw_arg
        brand_kit = "tab_ezzay"
        fmt = "vertical"
        title = ""
        if "--brand-kit" in args:
            brand_kit = args[args.index("--brand-kit") + 1]
        if "--format" in args:
            fmt = args[args.index("--format") + 1]
        if "--title" in args:
            title = args[args.index("--title") + 1]
        spec = script_to_spec(text, title=title, brand_kit=brand_kit, format_name=fmt)
        if len(args) >= 3 and not args[2].startswith("--"):
            with open(args[2], "w", encoding="utf-8") as fh:
                json.dump(spec, fh, indent=2, ensure_ascii=False)
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    if cmd == "patch-scene" and len(args) >= 4:
        from .scene_patch import patch_scene
        spec = _load(args[1])
        scene_id = args[2]
        updates = json.loads(args[3])
        ok, info = patch_scene(spec, scene_id, updates)
        if ok and len(args) >= 5:
            with open(args[4], "w", encoding="utf-8") as fh:
                json.dump(info["patched_spec"], fh, indent=2, ensure_ascii=False)
        print(json.dumps({"ok": ok, **{k: v for k, v in info.items() if k != "patched_spec"}}, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "hook-ab" and len(args) >= 2:
        from .scene_patch import generate_hook_ab_variants
        spec = _load(args[1])
        variants = generate_hook_ab_variants(spec)
        if len(args) >= 3:
            os.makedirs(args[2], exist_ok=True)
            for v in variants:
                vp = os.path.join(args[2], f"{v['variant_id']}.json")
                with open(vp, "w", encoding="utf-8") as fh:
                    json.dump(v["hook_only_spec"], fh, indent=2, ensure_ascii=False)
        summary = [{"variant_id": v["variant_id"], "label": v["label"], "hook_fingerprint": v["hook_fingerprint"]} for v in variants]
        print(json.dumps({"ok": True, "count": len(summary), "variants": summary}, indent=2, ensure_ascii=False))
        return 0

    if cmd == "contact-sheet" and len(args) >= 3:
        from .visual_qc import render_contact_sheet
        cols = 3
        rows = 2
        if "--cols" in args:
            cols = int(args[args.index("--cols") + 1])
        if "--rows" in args:
            rows = int(args[args.index("--rows") + 1])
        ok, info = render_contact_sheet(args[1], args[2], cols=cols, rows=rows)
        print(json.dumps({"ok": ok, **info}, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "visual-qc" and len(args) >= 2:
        from .flat_reels import build_video_html, even_word_timings
        from .visual_qc import audit_html_motion_continuity, audit_rendered_frames
        target = args[1]
        if os.path.isdir(target):
            res = audit_rendered_frames(target)
        else:
            spec = _load(target)
            timings = [
                {"id": sc["id"], "start": i * 2.0, "duration": 2.0,
                 "words": even_word_timings(sc.get("text", "").split(), i * 2.0, (i + 1) * 2.0)}
                for i, sc in enumerate(spec.get("scenes") or [])
            ]
            html = build_video_html(spec, timings, embed_font=False)
            res = audit_html_motion_continuity(html, spec)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    if cmd == "brand-kits":
        from .brand_kits import list_brand_kits
        kits = list_brand_kits()
        print(json.dumps({"count": len(kits), "brand_kits": kits}, indent=2, ensure_ascii=False))
        return 0

    if cmd == "thumbnail" and len(args) >= 3:
        from .multi_export import render_thumbnail_html, render_thumbnail_png
        spec = _load(args[1])
        out_path = args[2]
        scale, _kf, _bd = _apply_common_flags(spec, args, 3)
        fmt = str(spec.get("format", "landscape"))
        if out_path.endswith(".html"):
            html = render_thumbnail_html(spec, format_name=fmt, embed_font=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(json.dumps({"ok": True, "output": out_path, "format": fmt}, indent=2))
            return 0
        ok, info = render_thumbnail_png(spec, out_path, format_name=fmt, scale=scale)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "multi-export" and len(args) >= 3:
        from .multi_export import export_multi_format
        spec = _load(args[1])
        out_dir = args[2]
        scale, _kf, base_dir = _apply_common_flags(spec, args, 3)
        ok, info = export_multi_format(spec, out_dir, base_dir=base_dir, scale=scale)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "illustrations":
        from .icons import list_illustrations
        names = list_illustrations()
        print(json.dumps({"count": len(names), "illustrations": names}, indent=2))
        return 0

    if cmd == "icons":
        from .icons import search_icons
        q = args[1] if len(args) > 1 else ""
        names = search_icons(q)
        print(json.dumps({"count": len(names), "icons": names}, indent=2))
        return 0

    if cmd == "sfx-list":
        from .soundscape import list_sfx_kinds
        print(json.dumps(list_sfx_kinds(), indent=2, ensure_ascii=False))
        return 0

    if cmd == "music-list":
        from .soundscape import list_music_presets
        print(json.dumps(list_music_presets(), indent=2, ensure_ascii=False))
        return 0

    if cmd == "validate" and len(args) >= 2:
        from .flat_reels import validate_reel_spec
        ok, errors = validate_reel_spec(_load(args[1]))
        print(json.dumps({"ok": ok, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "pace" and len(args) >= 2:
        from .flat_reels import compute_scene_timings, measure_narration_pace
        spec = _load(args[1])
        base_dir = os.path.dirname(os.path.abspath(args[1]))
        target = spec.get("target_duration")
        if "--target-duration" in args:
            i = args.index("--target-duration")
            if i + 1 < len(args):
                target = float(args[i + 1])
        timings, _tot, errs = compute_scene_timings(spec, base_dir)
        if errs:
            print(json.dumps({"ok": False, "errors": errs}, indent=2, ensure_ascii=False))
            return 1
        pace = measure_narration_pace(spec, timings, target_duration=target)
        print(json.dumps({"ok": True, **pace}, indent=2, ensure_ascii=False))
        return 0

    if cmd == "beat-sync" and len(args) >= 2:
        from .beat_sync import analyze_audio_beats, build_scene_beat_profile
        from .flat_reels import compute_scene_timings
        target_path = args[1]
        if target_path.endswith(".json"):
            spec = _load(target_path)
            base_dir = os.path.dirname(os.path.abspath(target_path))
            timings, _tot, errs = compute_scene_timings(spec, base_dir)
            if errs:
                print(json.dumps({"ok": False, "errors": errs}, indent=2, ensure_ascii=False))
                return 1
            prof = build_scene_beat_profile(spec, timings, base_dir=base_dir)
            print(json.dumps({"ok": True, "scenes": prof}, indent=2, ensure_ascii=False))
            return 0
        res = analyze_audio_beats(target_path)
        print(json.dumps({"ok": res["duration"] > 0, **res}, indent=2, ensure_ascii=False))
        return 0 if res["duration"] > 0 else 1

    if cmd == "audit-audio" and len(args) >= 2:
        from .browser_render import DEFAULT_LOUDNESS_TARGET, audit_audio_mix
        target_lufs = DEFAULT_LOUDNESS_TARGET
        if "--target-lufs" in args:
            i = args.index("--target-lufs")
            if i + 1 < len(args):
                target_lufs = float(args[i + 1])
        res = audit_audio_mix(args[1], target_lufs=target_lufs)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1

    if cmd in ("render", "flat-reels") and len(args) >= 3:
        from .flat_reels import render_video
        spec = _load(args[1])
        out_mp4 = args[2]
        scale, keep_frames, base_dir = _apply_common_flags(spec, args, 3)
        ok, info = render_video(spec, out_mp4, base_dir=base_dir,
                                scale=scale, keep_frames=keep_frames)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1

    if cmd in ("frame", "flat-reels-frame") and len(args) >= 4:
        from .flat_reels import render_video_frame
        spec = _load(args[1])
        t_sec = float(args[2])
        out_png = args[3]
        scale, _kf, base_dir = _apply_common_flags(spec, args, 4)
        ok, info = render_video_frame(spec, t_sec, out_png,
                                      base_dir=base_dir, scale=scale)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1

    if cmd == "mcp":
        from .server import run_stdio_server
        run_stdio_server()
        return 0

    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
