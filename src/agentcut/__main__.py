"""AgentCut CLI (v2.0.0): serve | validate | render | preview | probe | poster |
contact-sheet | produce | produce-explainer | produce-variants | split | package |
tts | voices | reframe | studio | plugins | templates | from-template | otio |
qc | profiles | bundle | smart-cut | batch | cache-stats | cache-clear.
"""
from __future__ import annotations

import json
import os
import sys

from . import __version__
from .otio import export_otio
from .package import package_project
from .render import (
    poster_frame,
    preview_project,
    produce_explainer,
    produce_variants,
    produce_video,
    render_contact_sheet,
    render_project,
    run_job_worker,
    split_clip,
    timeline_summary,
)
from .server import serve
from .validate import probe_media, validate_project


_USAGE = (
    "usage: agentcut [--version | --help] <command> [args...]\n"
    "commands:\n"
    "  serve | validate <json> | render <json> <mp4> | preview <json> <dir>\n"
    "  probe <file> | poster <json> <png> [seconds] | contact-sheet <video> <out.png> [project.json]\n"
    "  produce <brief.json> <mp4> | produce-explainer <brief.json> <mp4> | produce-variants <brief.json> <dir>\n"
    "  summary <json> | split <json> <clip_id> <seconds> | package <json> <outdir> [--trim-clips]\n"
    "  tts <text> <mp3> [srt] [voice] | voices [lang] | reframe <in.mp4> <out.mp4> [--aspect 9:16] [--mode blur_pad] [--draft]\n"
    "  studio <json> [video.mp4] [-o out.html] [--open] | plugins [list|discover] | palette <hex> [--harmony analogous]\n"
    "  text-metrics <text|file> | minify-json <json|file> | templates [category] | from-template <id> <input.json> <out.mp4> [--aspect 9:16] [--parallel] [--profile low_spec]\n"
    "  otio <project.json> <out.otio>\n"
    "  qc <video.mp4> [--expected-duration SEC] [--no-black] [--no-silence]\n"
    "  profiles [--detect]\n"
    "  bundle <bundle.json> <out.mp4> [--aspect 9:16] [--profile low_spec] [--no-parallel] [--qc]\n"
    "  smart-cut <in.mp4> <out.mp4> [--noise-db -35] [--min-silence 0.55]\n"
    "  batch <manifest.json> [--concurrency N] [--no-resume] [--profile low_spec] [--qc] [--report report.json]\n"
    "  cache-stats [project_dir] | cache-clear [project_dir]\n"
    "  looks | humanize <brief.json> [out.json] [--look chiaroscuro_doc] | direct <script.txt> [out.json] [--title T] [--look L]\n"
    "  craft-audit <brief.json> | sfx <kind> <out.wav> [--duration SEC] | room-tone <out.wav> <dur> [--preset warm_studio]\n"
    "  focal-point <media> | exposure-match <img1> <img2...> | chapter-card <title> <out.mp4> | parallax <bg> <fg> <out.mp4> | compile-ir <brief.json> | auto-heal <brief.json> <out.mp4>\n"
    "  repair-brief <file|text> | art-prompts <brief.json> [--look L] | autopilot <file|text> [out.mp4] [--look L] [--profile P] | skills [list|show <name>|export <dir>]"
)


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    """CLI entry point: file/argument errors report to stderr with non-zero exit."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _main(argv)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _main(argv):
    if not argv or argv[0] == "serve":
        serve()
        return 0
    if argv[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(f"agentcut {__version__}")
        return 0
    if argv[0] == "validate" and len(argv) == 2:
        errors, info = validate_project(_load(argv[1]))
        print(json.dumps({"valid": not errors, "errors": errors,
                          "total_duration": info.get("total_duration")}, indent=2, ensure_ascii=False))
        return 1 if errors else 0
    if argv[0] == "render" and len(argv) == 3:
        ok, info = render_project(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "preview" and len(argv) == 3:
        ok, info = preview_project(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "poster" and len(argv) in (3, 4):
        t = float(argv[3]) if len(argv) == 4 else None
        ok, info = poster_frame(_load(argv[1]), argv[2], time=t)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "contact-sheet" and len(argv) in (3, 4):
        proj = _load(argv[3]) if len(argv) == 4 else None
        ok, info = render_contact_sheet(argv[1], argv[2], project=proj)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "otio" and len(argv) == 3:
        ok, info = export_otio(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "produce-variants" and len(argv) == 3:
        ok, info = produce_variants(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "produce-explainer" and len(argv) == 3:
        ok, info = produce_explainer(_load(argv[1]), argv[2])
        info = dict(info)
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "produce" and len(argv) == 3:
        ok, info = produce_video(_load(argv[1]), argv[2])
        info = dict(info)
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "summary" and len(argv) == 2:
        ok, info = timeline_summary(_load(argv[1]))
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "split" and len(argv) == 4:
        ok, info = split_clip(_load(argv[1]), argv[2], float(argv[3]))
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "worker" and len(argv) == 4:
        return 0 if run_job_worker(argv[1], argv[2], argv[3]) else 1
    if argv[0] == "tts" and len(argv) in (3, 4, 5):
        from .tts import synthesize_speech
        text = argv[1]
        out_mp3 = argv[2]
        out_srt = argv[3] if len(argv) >= 4 and not argv[3].startswith("-") else None
        voice = argv[4] if len(argv) >= 5 else None
        res = synthesize_speech(text, out_mp3, out_srt, voice=voice)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "voices" and len(argv) in (1, 2):
        from .tts import list_voices
        lang = argv[1] if len(argv) == 2 else None
        vlist = list_voices(lang)
        print(json.dumps(vlist, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "reframe" and len(argv) >= 3:
        from .reframe import reframe_video
        in_path = argv[1]
        out_path = argv[2]
        aspect = "9:16"
        mode = "blur_pad"
        draft = "--draft" in argv
        for idx in range(3, len(argv)):
            if argv[idx] == "--aspect" and idx + 1 < len(argv):
                aspect = argv[idx + 1]
            elif argv[idx] == "--mode" and idx + 1 < len(argv):
                mode = argv[idx + 1]
        ok, info = reframe_video(in_path, out_path, target_aspect=aspect, mode=mode, draft=draft)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "studio" and len(argv) >= 2:
        from .studio import generate_studio_html
        target_json = argv[1]
        video_arg = None
        out_html = None
        auto_open = "--open" in argv
        idx = 2
        while idx < len(argv):
            if argv[idx] in ("-o", "--output") and idx + 1 < len(argv):
                out_html = argv[idx + 1]
                idx += 2
            elif argv[idx] != "--open" and video_arg is None:
                video_arg = argv[idx]
                idx += 1
            else:
                idx += 1
        path = generate_studio_html(target_json, video_path=video_arg, output_html_path=out_html)
        print(json.dumps({"studio_html": path}, indent=2, ensure_ascii=False))
        if auto_open:
            import webbrowser
            webbrowser.open(f"file://{path}")
        return 0
    if argv[0] == "plugins" and len(argv) in (1, 2):
        from .plugins import discover_plugins, list_diagram_plugins, list_motion_plugins
        sub = argv[1] if len(argv) == 2 else "list"
        if sub == "discover":
            loaded = discover_plugins()
            print(json.dumps({"discovered_files": loaded, "motion_plugins": list_motion_plugins(), "diagram_plugins": list_diagram_plugins()}, indent=2))
        else:
            print(json.dumps({"motion_plugins": list_motion_plugins(), "diagram_plugins": list_diagram_plugins()}, indent=2))
        return 0
    if argv[0] == "palette" and len(argv) >= 2:
        from .colors import generate_palette
        base_color = argv[1]
        harmony = "analogous"
        if len(argv) >= 4 and argv[2] == "--harmony":
            harmony = argv[3]
        pal = generate_palette(base_color, harmony=harmony)
        print(json.dumps(pal, indent=2))
        return 0
    if argv[0] == "text-metrics" and len(argv) >= 2:
        from .text_metrics import analyze_text, estimate_reading_duration
        arg = argv[1]
        raw_text = open(arg, encoding="utf-8").read() if os.path.isfile(arg) else arg
        metrics = analyze_text(raw_text)
        metrics["estimated_reading_duration"] = estimate_reading_duration(raw_text)
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "minify-json" and len(argv) == 2:
        from .json_tools import minify_json
        arg = argv[1]
        raw = open(arg, encoding="utf-8").read() if os.path.isfile(arg) else arg
        print(minify_json(raw))
        return 0
    if argv[0] == "probe" and len(argv) == 2:
        info = probe_media(argv[1])
        print(json.dumps(info, indent=2, default=str) if info else "cannot probe")
        return 0 if info else 1
    if argv[0] == "templates" and len(argv) in (1, 2):
        from .templates import list_content_templates
        cat = argv[1] if len(argv) == 2 else None
        tpls = list_content_templates(cat)
        print(json.dumps(tpls, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "from-template" and len(argv) >= 4:
        from .templates import build_brief_from_template
        tid = argv[1]
        in_json = _load(argv[2])
        out_mp4 = argv[3]
        aspect = "9:16"
        parallel = False
        profile = None
        for idx in range(4, len(argv)):
            if argv[idx] == "--aspect" and idx + 1 < len(argv):
                aspect = argv[idx + 1]
            elif argv[idx] == "--parallel":
                parallel = True
            elif argv[idx] == "--profile" and idx + 1 < len(argv):
                profile = argv[idx + 1]
        title = in_json.get("title", "Untitled Video")
        scenes = in_json.get("scenes", [])
        brief = build_brief_from_template(tid, title, scenes, aspect=aspect)
        if parallel:
            brief["parallel"] = True
        if profile:
            brief["render_profile"] = profile
        ok, info = produce_explainer(brief, out_mp4)
        info = dict(info) if isinstance(info, dict) else {"result": info}
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "package" and len(argv) in (3, 4):
        trim = len(argv) == 4 and argv[3] == "--trim-clips"
        if len(argv) == 4 and not trim:
            print(_USAGE, file=sys.stderr)
            return 2
        summary = package_project(_load(argv[1]), argv[2], trim_clips=trim)
        print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
        return 0
    # --- v2.0.0 CLI commands ---
    if argv[0] == "qc" and len(argv) >= 2:
        from .qc import audit_video_quality
        vpath = argv[1]
        exp_dur = None
        check_black = "--no-black" not in argv
        check_silence = "--no-silence" not in argv
        for idx in range(2, len(argv)):
            if argv[idx] == "--expected-duration" and idx + 1 < len(argv):
                exp_dur = float(argv[idx + 1])
        report = audit_video_quality(
            vpath,
            expected_duration=exp_dur,
            check_black=check_black,
            check_silence=check_silence,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("passed") else 1
    if argv[0] == "profiles":
        from .profiles import detect_hardware_profile, list_render_profiles
        if "--detect" in argv:
            print(json.dumps(detect_hardware_profile(), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(list_render_profiles(), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "bundle" and len(argv) >= 3:
        from .bundle import produce_from_bundle
        bdata = _load(argv[1])
        out_mp4 = argv[2]
        aspect = None
        profile = None
        parallel = "--no-parallel" not in argv
        qc_flag = "--qc" in argv
        for idx in range(3, len(argv)):
            if argv[idx] == "--aspect" and idx + 1 < len(argv):
                aspect = argv[idx + 1]
            elif argv[idx] == "--profile" and idx + 1 < len(argv):
                profile = argv[idx + 1]
        ok, info = produce_from_bundle(
            bdata, out_mp4, aspect=aspect, profile=profile,
            parallel=parallel, qc_audit=qc_flag,
        )
        info = dict(info) if isinstance(info, dict) else {"result": info}
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "smart-cut" and len(argv) >= 3:
        from .smart_cut import smart_cut_media
        in_media = argv[1]
        out_mp4 = argv[2]
        noise_db = -35.0
        min_silence = 0.55
        for idx in range(3, len(argv)):
            if argv[idx] == "--noise-db" and idx + 1 < len(argv):
                noise_db = float(argv[idx + 1])
            elif argv[idx] == "--min-silence" and idx + 1 < len(argv):
                min_silence = float(argv[idx + 1])
        ok, info = smart_cut_media(
            in_media, out_mp4, noise_db=noise_db, min_silence=min_silence
        )
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "batch" and len(argv) >= 2:
        from .batch import run_batch_production
        manifest = _load(argv[1])
        conc = None
        resume = "--no-resume" not in argv
        profile = None
        qc_flag = "--qc" in argv
        report_path = None
        for idx in range(2, len(argv)):
            if argv[idx] == "--concurrency" and idx + 1 < len(argv):
                conc = int(argv[idx + 1])
            elif argv[idx] == "--profile" and idx + 1 < len(argv):
                profile = argv[idx + 1]
            elif argv[idx] == "--report" and idx + 1 < len(argv):
                report_path = argv[idx + 1]
        summary = run_batch_production(
            manifest, max_concurrency=conc, resume=resume,
            default_profile=profile, qc_audit=qc_flag, report_path=report_path,
        )
        print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
        return 0 if summary.get("all_ok") else 1
    if argv[0] == "cache-stats" and len(argv) in (1, 2):
        from .cache import get_cache_stats
        pdir = argv[1] if len(argv) == 2 else None
        print(json.dumps(get_cache_stats(pdir), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "cache-clear" and len(argv) in (1, 2):
        from .cache import clear_cache
        pdir = argv[1] if len(argv) == 2 else None
        removed = clear_cache(pdir)
        print(json.dumps({"removed": removed}, indent=2))
        return 0
    # --- v2.5.0 Human-Feel Director & Cinema CLI commands ---
    if argv[0] == "looks":
        from .cinematography import list_cinema_looks
        print(json.dumps(list_cinema_looks(), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "humanize" and len(argv) >= 2:
        from .director import humanize_brief
        brief = _load(argv[1])
        look = "chiaroscuro_doc"
        out_json = None
        for idx in range(2, len(argv)):
            if argv[idx] == "--look" and idx + 1 < len(argv):
                look = argv[idx + 1]
            elif not argv[idx].startswith("-") and (idx == 2 or argv[idx - 1] != "--look"):
                out_json = argv[idx]
        directed = humanize_brief(brief, cinema_look=look)
        if out_json:
            with open(out_json, "w", encoding="utf-8") as fh:
                json.dump(directed, fh, indent=2, ensure_ascii=False)
            print(json.dumps({"written": out_json, "cinema_look": look, "scenes": len(directed.get("scenes", []))}, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(directed, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "direct" and len(argv) >= 2:
        from .director import direct_from_script
        raw_arg = argv[1]
        script = open(raw_arg, encoding="utf-8").read() if os.path.isfile(raw_arg) else raw_arg
        title = "Editorial Essay"
        look = "chiaroscuro_doc"
        aspect = "9:16"
        out_json = None
        for idx in range(2, len(argv)):
            if argv[idx] == "--title" and idx + 1 < len(argv):
                title = argv[idx + 1]
            elif argv[idx] == "--look" and idx + 1 < len(argv):
                look = argv[idx + 1]
            elif argv[idx] == "--aspect" and idx + 1 < len(argv):
                aspect = argv[idx + 1]
            elif not argv[idx].startswith("-") and argv[idx - 1] not in ("--title", "--look", "--aspect"):
                out_json = argv[idx]
        directed = direct_from_script(script, title=title, cinema_look=look, aspect=aspect)
        if out_json:
            with open(out_json, "w", encoding="utf-8") as fh:
                json.dump(directed, fh, indent=2, ensure_ascii=False)
            print(json.dumps({"written": out_json, "scenes": len(directed["scenes"]), "cinema_look": look}, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(directed, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "craft-audit" and len(argv) == 2:
        from .qc import audit_editorial_craft
        rep = audit_editorial_craft(_load(argv[1]))
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0 if rep.get("passed") else 1
    if argv[0] == "sfx" and len(argv) >= 3:
        from .soundscape import synthesize_sfx
        dur = None
        for idx in range(3, len(argv)):
            if argv[idx] == "--duration" and idx + 1 < len(argv):
                dur = float(argv[idx + 1])
        ok, info = synthesize_sfx(argv[1], argv[2], duration=dur)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "room-tone" and len(argv) >= 3:
        from .soundscape import generate_room_tone
        preset = "warm_studio"
        for idx in range(3, len(argv)):
            if argv[idx] == "--preset" and idx + 1 < len(argv):
                preset = argv[idx + 1]
        ok, info = generate_room_tone(argv[1], float(argv[2]), preset=preset)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    # --- v3.0.0 Cinema Documentary Engine CLI commands ---
    if argv[0] == "focal-point" and len(argv) >= 2:
        from .vision import detect_focal_point
        print(json.dumps(detect_focal_point(argv[1]), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "exposure-match" and len(argv) >= 2:
        from .vision import compute_exposure_match_filter
        print(json.dumps(compute_exposure_match_filter(argv[1:]), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "chapter-card" and len(argv) >= 3:
        from .parallax import render_chapter_card
        sub = None
        dur = 2.0
        for idx in range(3, len(argv)):
            if argv[idx] == "--subtitle" and idx + 1 < len(argv):
                sub = argv[idx + 1]
            elif argv[idx] == "--duration" and idx + 1 < len(argv):
                dur = float(argv[idx + 1])
        ok, info = render_chapter_card(argv[1], argv[2], subtitle=sub, duration=dur)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "parallax" and len(argv) >= 4:
        from .parallax import render_parallax_clip
        ok, info = render_parallax_clip(argv[1], argv[2], argv[3])
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if ok else 1
    if argv[0] == "compile-ir" and len(argv) >= 2:
        from .pipeline.ir import compile_brief_to_ir
        ir = compile_brief_to_ir(_load(argv[1]), auto_focal="--focal" in argv, match_exposure="--exposure" in argv)
        print(json.dumps(ir.to_dict(), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "auto-heal" and len(argv) >= 3:
        from .pipeline.auto_heal import produce_cinematic_auto_heal
        prof = "low_spec"
        look = "chiaroscuro_doc"
        for idx in range(3, len(argv)):
            if argv[idx] == "--profile" and idx + 1 < len(argv):
                prof = argv[idx + 1]
            elif argv[idx] == "--look" and idx + 1 < len(argv):
                look = argv[idx + 1]
        ok, info = produce_cinematic_auto_heal(_load(argv[1]), argv[2], cinema_look=look, profile=prof)
        print(json.dumps(info, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    # --- v3.1.0 Model-Agnostic Autopilot CLI commands ---
    if argv[0] == "repair-brief" and len(argv) >= 2:
        from .brief_repair import repair_brief
        raw_arg = argv[1]
        raw_val = open(raw_arg, encoding="utf-8").read() if os.path.isfile(raw_arg) else raw_arg
        rep, changes = repair_brief(raw_val)
        print(json.dumps({"brief": rep, "repairs_made": changes}, indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "art-prompts" and len(argv) >= 2:
        from .art_director import generate_visual_prompts
        look = None
        for idx in range(2, len(argv)):
            if argv[idx] == "--look" and idx + 1 < len(argv):
                look = argv[idx + 1]
        print(json.dumps(generate_visual_prompts(_load(argv[1]), style_bible=look), indent=2, ensure_ascii=False))
        return 0
    if argv[0] == "autopilot" and len(argv) >= 2:
        from .autopilot import autopilot_produce
        raw_arg = argv[1]
        raw_val = open(raw_arg, encoding="utf-8").read() if os.path.isfile(raw_arg) else raw_arg
        out_mp4 = None
        look = "chiaroscuro_doc"
        aspect = None
        prof = "low_spec"
        for idx in range(2, len(argv)):
            if argv[idx] == "--look" and idx + 1 < len(argv):
                look = argv[idx + 1]
            elif argv[idx] == "--aspect" and idx + 1 < len(argv):
                aspect = argv[idx + 1]
            elif argv[idx] == "--profile" and idx + 1 < len(argv):
                prof = argv[idx + 1]
            elif not argv[idx].startswith("-") and argv[idx - 1] not in ("--look", "--aspect", "--profile"):
                out_mp4 = argv[idx]
        ok, res = autopilot_produce(raw_val, output_path=out_mp4, cinema_look=look, aspect=aspect, profile=prof)
        print(json.dumps(res, indent=2, default=str, ensure_ascii=False))
        return 0 if ok else 1
    # --- v3.2.0 Skills Pack CLI command ---
    if argv[0] == "skills":
        from .skills_registry import export_agentcut_skills, get_agentcut_skill, list_agentcut_skills
        sub = argv[1] if len(argv) >= 2 else "list"
        if sub == "list":
            print(json.dumps(list_agentcut_skills(), indent=2, ensure_ascii=False))
            return 0
        if sub == "show" and len(argv) >= 3:
            sk = get_agentcut_skill(argv[2])
            if sk is None:
                print(f"error: skill not found: {argv[2]}", file=sys.stderr)
                return 1
            print(sk["content"])
            return 0
        if sub == "export" and len(argv) >= 3:
            print(json.dumps(export_agentcut_skills(argv[2]), indent=2, ensure_ascii=False))
            return 0
        print("usage: agentcut skills [list | show <name> | export <dest_dir>]", file=sys.stderr)
        return 2

    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
