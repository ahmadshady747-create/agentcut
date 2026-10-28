"""AgentCut CLI: serve | validate | render | probe | preview | poster | produce | produce-variants."""
import json
import sys

from .render import poster_frame, preview_project, produce_variants, produce_video, render_project, run_job_worker, split_clip, timeline_summary
from .server import serve
from .validate import probe_media, validate_project


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def main(argv):
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
    if argv[0] == "produce-variants" and len(argv) == 3:
        ok, info = produce_variants(_load(argv[1]), argv[2])
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
    if argv[0] == "probe" and len(argv) == 2:
        info = probe_media(argv[1])
        print(json.dumps(info, indent=2, default=str) if info else "cannot probe")
        return 0 if info else 1
    print("usage: python3 -m agentcut [serve|validate <json>|render <json> <mp4>|preview <json> <dir>|probe <file>|poster <json> <png> [seconds]|produce <brief.json> <mp4>|produce-variants <brief.json> <dir>|split <json> <clip_id> <seconds>|worker <jobs_dir> <job_id> <out_dir>]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
