"""AgentCut CLI: serve | validate | render | probe | preview | poster | produce | produce-variants | package."""
import json
import sys

from .package import package_project
from .render import poster_frame, preview_project, produce_explainer, produce_variants, produce_video, render_contact_sheet, render_project, run_job_worker, split_clip, timeline_summary
from .server import serve
from .validate import probe_media, validate_project


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def main(argv):
    """CLI entry point: file/argument errors are reported as
    `error: ...` with a non-zero exit, never as a raw traceback
    (1.34.0 — the MCP path already structured these, the CLI did
    not). Internal bugs still raise, by design."""
    try:
        return _main(argv)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1


def _main(argv):
    if not argv or argv[0] == "serve":
        serve()
        return 0
    if argv[0] == "validate" and len(argv) == 2:
        errors, info = validate_project(_load(argv[1]))
        print(json.dumps({"valid": not errors, "errors": errors,
                          "total_duration": info.get("total_duration")}, indent=2))
        return 1 if errors else 0
    if argv[0] == "render" and len(argv) == 3:
        ok, info = render_project(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "preview" and len(argv) == 3:
        ok, info = preview_project(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "poster" and len(argv) in (3, 4):
        t = float(argv[3]) if len(argv) == 4 else None
        ok, info = poster_frame(_load(argv[1]), argv[2], time=t)
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "contact-sheet" and len(argv) in (3, 4):
        # v1.45.0, R9: contact-sheet <video> <out.png> [project.json]
        # — 12 key frames in a 4x3 grid PNG with MM:SS labels.
        # The optional project JSON drives the smart timestamps.
        proj = _load(argv[3]) if len(argv) == 4 else None
        ok, info = render_contact_sheet(argv[1], argv[2], project=proj)
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "produce-variants" and len(argv) == 3:
        ok, info = produce_variants(_load(argv[1]), argv[2])
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "produce-explainer" and len(argv) == 3:
        ok, info = produce_explainer(_load(argv[1]), argv[2])
        info = dict(info)
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "produce" and len(argv) == 3:
        ok, info = produce_video(_load(argv[1]), argv[2])
        info = dict(info)
        info.pop("project", None)
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "summary" and len(argv) == 2:
        ok, info = timeline_summary(_load(argv[1]))
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "split" and len(argv) == 4:
        ok, info = split_clip(_load(argv[1]), argv[2], float(argv[3]))
        print(json.dumps(info, indent=2, default=str))
        return 0 if ok else 1
    if argv[0] == "worker" and len(argv) == 4:
        # Detached job worker (internal): run one queued job from its
        # disk state file and write the final state back to disk.
        # Started by JobStore.run_async, never by hand in normal use.
        return 0 if run_job_worker(argv[1], argv[2], argv[3]) else 1
    if argv[0] == "tts" and len(argv) in (3, 4, 5):
        from .tts import synthesize_speech
        text = argv[1]
        out_mp3 = argv[2]
        out_srt = argv[3] if len(argv) >= 4 and not argv[3].startswith("-") else None
        voice = argv[4] if len(argv) >= 5 else None
        res = synthesize_speech(text, out_mp3, out_srt, voice=voice)
        print(json.dumps(res, indent=2))
        return 0
    if argv[0] == "voices" and len(argv) in (1, 2):
        from .tts import list_voices
        lang = argv[1] if len(argv) == 2 else None
        vlist = list_voices(lang)
        print(json.dumps(vlist, indent=2))
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
        print(json.dumps(info, indent=2, default=str))
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
        print(json.dumps({"studio_html": path}, indent=2))
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
    if argv[0] == "probe" and len(argv) == 2:
        info = probe_media(argv[1])
        print(json.dumps(info, indent=2, default=str) if info else "cannot probe")
        return 0 if info else 1
    if argv[0] == "package" and len(argv) in (3, 4):
        trim = len(argv) == 4 and argv[3] == "--trim-clips"
        if len(argv) == 4 and not trim:
            print("usage: python3 -m agentcut [serve|validate <json>|render <json> <mp4>|preview <json> <dir>|probe <file>|poster <json> <png> [seconds]|contact-sheet <video> <out.png> [project.json]|produce <brief.json> <mp4>|produce-explainer <brief.json> <mp4>|produce-variants <brief.json> <dir>|split <json> <clip_id> <seconds>|package <json> <outdir> [--trim-clips]|worker <jobs_dir> <job_id> <out_dir>|tts <text> <mp3> [srt] [voice]|voices [lang]|reframe <in.mp4> <out.mp4> [--aspect 9:16] [--mode blur_pad]|studio <json> [video.mp4] [-o out.html]|plugins [list|discover]]")
            return 2
        summary = package_project(_load(argv[1]), argv[2], trim_clips=trim)
        print(json.dumps(summary, indent=2, default=str))
        return 0
    print("usage: python3 -m agentcut [serve|validate <json>|render <json> <mp4>|preview <json> <dir>|probe <file>|poster <json> <png> [seconds]|contact-sheet <video> <out.png> [project.json]|produce <brief.json> <mp4>|produce-explainer <brief.json> <mp4>|produce-variants <brief.json> <dir>|split <json> <clip_id> <seconds>|package <json> <outdir> [--trim-clips]|worker <jobs_dir> <job_id> <out_dir>|tts <text> <mp3> [srt] [voice]|voices [lang]|reframe <in.mp4> <out.mp4> [--aspect 9:16] [--mode blur_pad]|studio <json> [video.mp4] [-o out.html]|plugins [list|discover]]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
