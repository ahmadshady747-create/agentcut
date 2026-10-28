"""AgentCut MCP server over stdio (JSON-RPC, newline-delimited).

No third-party dependencies: the protocol surface used here is the
small, stable core every MCP client speaks — initialize, tools/list,
tools/call and ping. Notifications get no response, as the protocol
requires.
"""
from __future__ import annotations

import json
import os
import re
import sys

from .render import JobStore, poster_frame, preview_project, produce_variants, produce_video, render_project
from .validate import probe_media, validate_project

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data")
PROJECTS = os.path.join(DATA, "projects")
JOBS_DIR = os.path.join(DATA, "jobs")
OUTPUTS = os.path.join(DATA, "outputs")
for _d in (PROJECTS, JOBS_DIR, OUTPUTS):
    os.makedirs(_d, exist_ok=True)

JOBS = JobStore(JOBS_DIR)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _project_path(pid):
    return os.path.join(PROJECTS, f"{pid}.json")


def _save_project(project):
    pid = project["id"]
    if not _ID_RE.match(pid):
        return {"code": "INVALID_SCHEMA", "message": "project id must match [A-Za-z0-9_-]+"}
    with open(_project_path(pid), "w") as fh:
        json.dump(project, fh, indent=2)
    return None


def _load_project(pid):
    path = _project_path(pid)
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def _text(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _tool_error(payload):
    out = _text(payload)
    out["isError"] = True
    return out


TOOLS = [
    {"name": "probe_media",
     "description": "Probe a local media file with ffprobe: duration, size, streams.",
     "inputSchema": {"type": "object", "properties": {
         "src": {"type": "string"}}, "required": ["src"]}},
    {"name": "create_project",
     "description": "Validate and store a project JSON. Returns validation errors, if any.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}}, "required": ["project"]}},
    {"name": "get_project",
     "description": "Read a stored project by id.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "list_projects",
     "description": "List stored project ids.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "patch_project",
     "description": "Merge-patch a stored project: top-level fields replace, a clip with an existing id replaces that clip, new clips append.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "patch": {"type": "object"}},
         "required": ["id", "patch"]}},
    {"name": "validate_project",
     "description": "Validate a project JSON without rendering it.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}}, "required": ["project"]}},
    {"name": "preview_project",
     "description": "Render a low-resolution proxy of a stored project and extract frames plus a contact sheet. Returns a job id; poll with get_job.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "render_project",
     "description": "Start an async final render of a stored project. Returns a job id; poll with get_job.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "poster_frame",
     "description": "Export one full-resolution poster frame (PNG) from a stored project at a given time in seconds; omit time for the middle of the timeline.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "time": {"type": "number"}},
         "required": ["id"]}},
    {"name": "produce_video",
     "description": "Producer one-call: take a short brief (format preset, media paths, optional title/texts/audio) and return a finished video plus review frames. Blocks until the render completes.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object"},
         "output": {"type": "string"}},
         "required": ["brief", "output"]}},
    {"name": "produce_variants",
     "description": "Producer multi-format export: render one brief as vertical, square and landscape videos in one call. Returns per-format outputs, durations, review frames and QA.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object"},
         "output_dir": {"type": "string"}},
         "required": ["brief", "output_dir"]}},
    {"name": "get_job",
     "description": "Poll a render or preview job by id.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
]


def call_tool(name, args):
    args = args or {}
    if name == "probe_media":
        info = probe_media(args["src"])
        if info is None:
            return _tool_error({"code": "FILE_NOT_FOUND", "message": f"cannot probe {args['src']}"})
        info.pop("raw", None)
        return _text(info)
    if name == "create_project":
        project = args["project"]
        errors, info = validate_project(project)
        if errors:
            return _tool_error({"code": "INVALID_PROJECT", "errors": errors})
        bad = _save_project(project)
        if bad:
            return _tool_error(bad)
        return _text({"id": project["id"], "total_duration": info["total_duration"], "saved": True})
    if name == "get_project":
        project = _load_project(args["id"])
        if project is None:
            return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        return _text(project)
    if name == "list_projects":
        ids = sorted(f[:-5] for f in os.listdir(PROJECTS) if f.endswith(".json"))
        return _text({"projects": ids})
    if name == "patch_project":
        project = _load_project(args["id"])
        if project is None:
            return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        patch = args["patch"]
        for key, value in patch.items():
            if key == "clips" and isinstance(value, list):
                by_id = {c.get("id"): c for c in project.get("clips", []) if isinstance(c, dict)}
                order = [c.get("id") for c in project.get("clips", [])]
                for clip in value:
                    if clip.get("id") in by_id:
                        by_id[clip["id"]] = clip
                    else:
                        by_id[clip["id"]] = clip
                        order.append(clip["id"])
                project["clips"] = [by_id[i] for i in order]
            else:
                project[key] = value
        errors, info = validate_project(project)
        if errors:
            return _tool_error({"code": "INVALID_PROJECT", "errors": errors})
        bad = _save_project(project)
        if bad:
            return _tool_error(bad)
        return _text({"id": project["id"], "total_duration": info["total_duration"], "saved": True})
    if name == "validate_project":
        errors, info = validate_project(args["project"])
        info = {k: v for k, v in info.items() if k not in ("clips", "font")}
        return _text({"valid": not errors, "errors": errors, "info": info})
    if name in ("preview_project", "render_project"):
        project = _load_project(args["id"])
        if project is None:
            return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        kind = "preview" if name == "preview_project" else "render"
        job_id = JOBS.create(kind, project)
        out_dir = os.path.join(OUTPUTS, job_id)
        JOBS.run_async(job_id, out_dir)
        return _text({"job_id": job_id, "kind": kind, "status": "queued"})
    if name == "poster_frame":
        project = _load_project(args["id"])
        if project is None:
            return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        out_dir = os.path.join(OUTPUTS, "posters")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{args['id']}.png")
        ok, info = poster_frame(project, out_path, time=args.get("time"))
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "produce_variants":
        ok, info = produce_variants(args["brief"], args["output_dir"])
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "produce_video":
        ok, info = produce_video(args["brief"], args["output"])
        if not ok:
            return _tool_error(info)
        info = dict(info)
        info.pop("project", None)
        return _text(info)
    if name == "get_job":
        record = JOBS.get(args["id"])
        if record is None:
            return _tool_error({"code": "JOB_NOT_FOUND", "message": args["id"]})
        record = dict(record)
        record.pop("project", None)
        return _text(record)
    return _tool_error({"code": "INVALID_SCHEMA", "message": f"unknown tool {name}"})


def _server_version():
    """Read VERSION so tools/list callers see the real release."""
    try:
        with open(os.path.join(BASE, "VERSION")) as fh:
            return fh.read().strip()
    except Exception:
        return "1.0.0"


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method", "")
        req_id = req.get("id")
        if method.startswith("notifications/"):
            continue
        if method == "initialize":
            result = {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentcut", "version": _server_version()},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = req.get("params", {})
            try:
                result = call_tool(params.get("name"), params.get("arguments") or {})
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                # A malformed tool call (e.g. a missing required
                # argument) must return a structured error, never kill
                # the server with a traceback: the client may still
                # have more calls to make, and the server is long-lived.
                result = _tool_error({"code": "INVALID_SCHEMA",
                                      "message": f"missing or invalid tool argument: {exc}"})
        elif method == "ping":
            result = {}
        else:
            result = None
        if req_id is None:
            continue
        if result is None:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve()
