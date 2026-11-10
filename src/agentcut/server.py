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
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    import fcntl
except ImportError:  # Windows / platforms without fcntl
    fcntl = None

from .render import JobStore, check_brief_conformance, poster_frame, preview_project, produce_explainer, produce_variants, produce_video, render_contact_sheet, render_project, split_clip, timeline_summary
from .validate import probe_media, validate_project

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data")
PROJECTS = os.path.join(DATA, "projects")
SNAPSHOTS = os.path.join(DATA, "snapshots")
JOBS_DIR = os.path.join(DATA, "jobs")
OUTPUTS = os.path.join(DATA, "outputs")
for _d in (PROJECTS, JOBS_DIR, OUTPUTS, SNAPSHOTS):
    os.makedirs(_d, exist_ok=True)

JOBS = JobStore(JOBS_DIR)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _project_path(pid):
    return os.path.join(PROJECTS, f"{pid}.json")


_WRITE_LOCK = threading.RLock()


def _atomic_write_json(path, data):
    """Write JSON atomically: NamedTemporaryFile in the same
    directory, then os.replace, so a reader never sees a
    half-written file (it sees the old file or the new file,
    never a torn mix). Concurrent writers are serialised with
    an flock on path + ".lock" when fcntl is available; the
    lock is held across write + replace, so two writers cannot
    interleave replaces. PLATFORM FALLBACK: where fcntl is
    unavailable (e.g. Windows), the lock is skipped — the
    replace is still atomic, so readers stay safe, but two
    simultaneous writers could race (last replace wins); that
    is documented, not hidden, and the server is normally
    single-writer per project id."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with _WRITE_LOCK:
        lock_fh = None
        if fcntl is not None:
            try:
                lock_fh = open(path + ".lock", "a")
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
            except OSError:
                lock_fh = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".agentcut-tmp-",
                                       suffix=".json", dir=directory)
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(data, fh, indent=2)
                for _attempt in range(10):
                    try:
                        os.replace(tmp, path)
                        break
                    except PermissionError:
                        if _attempt == 9:
                            raise
                        time.sleep(0.01 * (2 ** _attempt))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        finally:
            if lock_fh is not None:
                try:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
                except OSError:
                    pass
                lock_fh.close()


def _save_project(project):
    pid = project["id"]
    if not _ID_RE.match(pid):
        return {"code": "INVALID_SCHEMA", "message": "project id must match [A-Za-z0-9_-]+"}
    _atomic_write_json(_project_path(pid), project)
    return None


class _BackgroundJobs:
    """In-process background renders (ThreadPoolExecutor).

    This is SEPARATE from the detached JobStore system (get_job):
    detached jobs survive stdio close and are pollable from a new
    connection because their state lives on disk; background jobs
    live only in this server process (they die with it) and are
    polled with get_job_status. The two id namespaces do not
    collide: detached ids are bare uuid hex, background ids are
    "bg_" + uuid hex, so a bg id passed to get_job correctly
    reports JOB_NOT_FOUND instead of a wrong record, and vice
    versa. A queued background job can be cancelled (its future
    is cancelled before it starts); a RUNNING job cannot be
    interrupted — its ffmpeg child is mid-render, and killing
    the thread would leave a partial output — so cancel_job on
    a running job reports cancelled=False and the job runs to
    completion. That limit is by design, not a silent failure.
    """

    def __init__(self, max_workers=2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._jobs = {}  # job_id -> {status, future, output, result, error}

    def start(self, project):
        job_id = "bg_" + uuid.uuid4().hex[:12]
        out_dir = os.path.join(OUTPUTS, job_id)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "output.mp4")
        with self._lock:
            self._jobs[job_id] = {"status": "queued", "future": None,
                                  "output": out_path, "result": None,
                                  "error": None}
        future = self._executor.submit(self._run, job_id, project, out_path)
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["future"] = future
        return job_id

    def _run(self, job_id, project, out_path):
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None or rec["status"] == "cancelled":
                return
            rec["status"] = "running"
        try:
            ok, info = render_project(project, out_path)
        except Exception as exc:  # never leave a job stuck running
            ok, info = False, {"code": "RENDER_FAILED", "message": str(exc)}
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            if ok:
                rec["status"] = "done"
                rec["result"] = info
            else:
                rec["status"] = "failed"
                rec["error"] = info

    def status(self, job_id):
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            return {"job_id": job_id, "status": rec["status"],
                    "output": rec["output"], "result": rec["result"],
                    "error": rec["error"]}

    def cancel(self, job_id):
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            if rec["status"] == "queued":
                future = rec.get("future")
                cancelled = bool(future and future.cancel())
                if cancelled:
                    rec["status"] = "cancelled"
                return {"job_id": job_id, "status": rec["status"],
                        "cancelled": cancelled}
            if rec["status"] in ("done", "failed", "cancelled"):
                return {"job_id": job_id, "status": rec["status"],
                        "cancelled": False}
            return {"job_id": job_id, "status": "running",
                    "cancelled": False,
                    "note": "a running background render cannot be "
                            "interrupted; it runs to completion"}


BG_JOBS = _BackgroundJobs()


def _load_project(pid):
    path = _project_path(pid)
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def _snapshot_dir(pid):
    d = os.path.join(SNAPSHOTS, pid)
    os.makedirs(d, exist_ok=True)
    return d


def _snapshot_path(pid, sid):
    return os.path.join(_snapshot_dir(pid), f"{sid}.json")


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
     "description": "Validate a project JSON without rendering it. Returns errors (blocking) and info, including warnings: layer_text_overlap_warning entries (approximate overlay-vs-text box collisions, warnings only, never blocking) when an overlay path crosses a text or caption region.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}}, "required": ["project"]}},
    {"name": "preview_project",
     "description": "Render a low-resolution proxy of a stored project and extract frames plus a contact sheet. The proxy carries title-safe/action-safe guide boxes and, when the project sets timecode (or preview timecode is requested), a timecode burn-in; the result flags guides/timecode. Returns a job id; poll with get_job. The job runs as a detached process and survives the client closing stdio.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "render_project",
     "description": "Start an async final render of a stored project. Returns a job id; poll with get_job. The job runs as a detached process and survives the client closing stdio; a new connection can poll it.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "poster_frame",
     "description": "Export one full-resolution poster frame (PNG) from a stored project at a given time in seconds; omit time for the middle of the timeline.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "time": {"type": "number"}},
         "required": ["id"]}},
    {"name": "render_contact_sheet",
     "description": "Agent self-inspection: extract 12 key frames from a rendered video into one 4x3 grid PNG with an MM:SS timestamp on each thumbnail. Timestamps are chosen smartly from a stored project's timeline when project_id is given (chapter starts, diagram completions, motion/text starts), else evenly spaced. Returns the PNG path, the 12 frame times/labels, and the grid size.",
     "inputSchema": {"type": "object", "properties": {
         "video": {"type": "string"},
         "output": {"type": "string"},
         "project_id": {"type": "string"}},
         "required": ["video"]}},
    {"name": "produce_video",
     "description": "Producer one-call: take a short brief (format preset, media paths, optional title/texts/audio) and return a finished video plus review frames. Blocks until the render completes.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object"},
         "output": {"type": "string"}},
         "required": ["brief", "output"]}},
    {"name": "produce_explainer",
     "description": "One-button board explainer: one brief (id, style blackboard, output, background, title, chapters, motion gentle, music, scenes with narration/captions/diagram/keywords) in, one finished video out. Scene durations come from narration audio via ffprobe; diagrams default to their scene window; punch_in anywhere is rejected. Blocks until the render completes.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object"},
         "output": {"type": "string"}},
         "required": ["brief", "output"]}},
    {"name": "produce_variants",
     "description": "Producer multi-format export: render one brief as vertical, square and landscape videos in one call, or as named delivery presets (youtube_1080, shorts_720, instagram_square) when the brief sets delivery_presets or delivery_preset; an explicit format/width/height in the brief wins over a preset's size. Returns per-format outputs, durations, review frames and QA.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object"},
         "output_dir": {"type": "string"}},
         "required": ["brief", "output_dir"]}},
    {"name": "split_clip",
     "description": "Split one clip of a project dict into two at a time in seconds into that clip's rendered duration. Returns the new project dict; store it with create_project.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}, "clip_id": {"type": "string"},
         "at": {"type": "number"}},
         "required": ["project", "clip_id", "at"]}},
    {"name": "timeline_summary",
     "description": "Summarize a project timeline for agent reasoning: segments with start/end/kind/transition, texts, captions, audio tracks, overlays. Validates first; returns errors if invalid.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}}, "required": ["project"]}},
    {"name": "check_brief",
     "description": "Check a produced project against its brief: clips used, title present, format, duration within target_duration, captions present, audio present. Deterministic verification only; generates nothing.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object"}, "brief": {"type": "object"}},
         "required": ["project", "brief"]}},
    {"name": "snapshot_project",
     "description": "Snapshot a stored project before experimenting: returns a snapshot id. Patch freely afterwards; rollback_project restores the snapshot.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "label": {"type": "string"}},
         "required": ["id"]}},
    {"name": "rollback_project",
     "description": "Restore a stored project from one of its snapshots, discarding later patches. The snapshot itself is kept, so rollback is repeatable.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "snapshot_id": {"type": "string"}},
         "required": ["id", "snapshot_id"]}},
    {"name": "list_snapshots",
     "description": "List snapshot ids for a stored project.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "get_job",
     "description": "Poll a render or preview job by id. Job state lives on disk, so this works from a new connection for jobs launched in earlier sessions; a job whose worker died without finishing reports failed.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "start_render_job",
     "description": "Start a background render in this server process (ThreadPoolExecutor) of a stored project (by id) or a project dict (project). Returns a bg_ job id at once; poll with get_job_status. Unlike render_project/get_job (detached subprocess, disk state, survives stdio close), background jobs live only in this server process and die with it; a queued job can be cancelled with cancel_job, a running one cannot be interrupted.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "project": {"type": "object"}}}},
    {"name": "get_job_status",
     "description": "Poll a background render job started with start_render_job by its bg_ id. Returns status (queued/running/done/failed/cancelled), output path, and result or error when finished.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "cancel_job",
     "description": "Cancel a background render job by its bg_ id. A queued job is cancelled; a running job cannot be interrupted and reports cancelled=False; a finished job reports its final status.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}}, "required": ["id"]}},
    {"name": "synthesize_speech",
     "description": "Synthesize speech audio (.mp3) and matching time-aligned SRT captions (.srt) from text using neural TTS. Allows generating scene voiceovers and subtitles autonomously without human recording.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Text to synthesize into speech."},
         "output_audio": {"type": "string", "description": "Output path for the audio file (.mp3)."},
         "output_srt": {"type": "string", "description": "Optional output path for time-aligned captions (.srt)."},
         "voice": {"type": "string", "description": "Optional neural voice name (e.g. 'ar-SA-HamedNeural', 'en-US-ChristopherNeural'). Automatically detected if omitted."},
         "rate": {"type": "string", "description": "Speaking rate adjustment (default '+0%')."},
         "volume": {"type": "string", "description": "Volume adjustment (default '+0%')."}},
         "required": ["text", "output_audio"]}},
    {"name": "list_voices",
     "description": "List available neural TTS voices for speech synthesis. Optionally filter by language prefix (e.g. 'ar' for Arabic, 'en' for English).",
     "inputSchema": {"type": "object", "properties": {
         "language": {"type": "string", "description": "Language prefix filter (e.g. 'ar', 'en')."}}}},
    {"name": "reframe_video",
     "description": "Intelligently reframe an existing video to a target aspect ratio (9:16 Shorts/Reels/TikTok, 1:1 Square, 16:9 Landscape, 4:5 Portrait) with dynamic blurred padding, cropping, or letterbox fit.",
     "inputSchema": {"type": "object", "properties": {
         "input_video": {"type": "string", "description": "Path to input video."},
         "output_video": {"type": "string", "description": "Destination path for reframed video."},
         "target_aspect": {"type": "string", "description": "Target aspect ratio ('9:16', '1:1', '16:9', 'shorts', 'reels', etc.). Default '9:16'."},
         "mode": {"type": "string", "description": "Framing mode: 'blur_pad' (dynamic blurred padding), 'crop', or 'fit'. Default 'blur_pad'."},
         "draft": {"type": "boolean", "description": "Fast draft mode (half resolution, fastest preset)."}},
         "required": ["input_video", "output_video"]}},
    {"name": "generate_web_studio",
     "description": "Generate an interactive, standalone HTML Web Studio timeline inspector for a project dict or explainer brief. Includes multi-track timeline, synchronized playhead, speech viewer, aspect ratio simulator, and video preview.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "object", "description": "Project dictionary or brief object."},
         "video_path": {"type": "string", "description": "Optional path to the rendered video file."},
         "output_html": {"type": "string", "description": "Optional destination path for the HTML file."}},
         "required": ["project"]}},
    {"name": "list_plugins",
     "description": "List all registered motion element plugins and diagram templates, and optionally discover plugins in workspace or user directories.",
     "inputSchema": {"type": "object", "properties": {
         "discover": {"type": "boolean", "description": "Whether to auto-discover and load plugins from directories."}}}},
    {"name": "generate_color_palette",
     "description": "Generate harmonious color palettes and evaluate WCAG contrast ratios for video theming, diagram cards, and kinetic subtitles.",
     "inputSchema": {"type": "object", "properties": {
         "base_color": {"type": "string", "description": "Base color in HEX (#RRGGBB). Default '#3B82F6'."},
         "harmony": {"type": "string", "description": "Color harmony ('analogous', 'complementary', 'triadic', 'monochromatic'). Default 'analogous'."},
         "dark_mode": {"type": "boolean", "description": "Whether to optimize palette for dark video backgrounds. Default true."}},
         "required": ["base_color"]}},
    {"name": "analyze_text_metrics",
     "description": "Analyze text composition, count words/characters, estimate on-screen reading duration, and optionally diff against an original script.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Script or subtitle text to analyze."},
         "original_text": {"type": "string", "description": "Optional original script to diff against."},
         "wpm": {"type": "integer", "description": "Optional reading speed words-per-minute target (default 140 for Arabic, 160 for English)."}},
         "required": ["text"]}},
    {"name": "list_content_templates",
     "description": "List all 8 turnkey multi-genre content templates with their metadata, WCAG-validated palettes, aspect ratios, and visual styles.",
     "inputSchema": {"type": "object", "properties": {
         "category": {"type": "string", "description": "Optional category filter ('finance', 'documentary', 'tech', 'entertainment', 'news', 'education', 'podcast', 'marketing', or 'all')."}}}},
    {"name": "produce_from_template",
     "description": "One-call turnkey tool: expand a high-level topic title and minimal scene list using one of the 8 content templates, and render the finished studio MP4 video.",
     "inputSchema": {"type": "object", "properties": {
         "template_id": {"type": "string", "description": "One of the 8 content genre templates: 'finance_data_story', 'history_documentary', 'tech_code_explainer', 'top_list_countdown', 'news_breaking_brief', 'educational_lesson', 'podcast_audiogram_quote', 'product_promo_showcase'."},
         "title": {"type": "string", "description": "Main video topic title."},
         "scenes": {"type": "array", "description": "List of scenes with title, narration_text, role, data, etc.", "items": {"type": "object"}},
         "output_path": {"type": "string", "description": "Output path for the rendered MP4 file."},
         "aspect": {"type": "string", "description": "Target aspect ratio: '9:16', '16:9', '1:1', '4:5'. Default '9:16'."},
         "parallel": {"type": "boolean", "description": "Whether to render scenes concurrently across CPU cores. Default false."}},
         "required": ["template_id", "title", "scenes", "output_path"]}},
]


def _bad_id(value):
    """True when value is not a safe project/job/snapshot id.

    Ids are [A-Za-z0-9_-]+ by contract (see _save_project); load
    endpoints previously passed ids straight into filesystem
    paths, so a traversal id (e.g. "../snapshots/...") could read
    files outside the projects/jobs/snapshots directories, and
    list_snapshots would even create directories for arbitrary
    ids. Every id-taking tool checks its ids first (1.34.0).
    """
    return not isinstance(value, str) or not _ID_RE.match(value)


def _id_error(what="id"):
    return _tool_error({"code": "INVALID_SCHEMA",
                        "message": f"{what} must match [A-Za-z0-9_-]+"})


def call_tool(name, args):
    args = args or {}
    if name in ("get_project", "patch_project", "preview_project",
                "render_project", "poster_frame", "snapshot_project",
                "list_snapshots") and _bad_id(args.get("id")):
        return _id_error()
    if name == "rollback_project" and (_bad_id(args.get("id")) or _bad_id(args.get("snapshot_id"))):
        return _id_error("id and snapshot_id")
    if name == "get_job" and _bad_id(args.get("id")):
        return _id_error()
    if name in ("get_job_status", "cancel_job") and _bad_id(args.get("id")):
        return _id_error()
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
    if name == "render_contact_sheet":
        # v1.45.0, R9 (23rd tool): video is a file path
        # (resolved via resolve_path inside render_contact_sheet);
        # output defaults to <video>.contact.png; project_id is
        # optional and, when it names a stored project, drives
        # the smart timestamps.
        from .validate import resolve_path
        video = args.get("video")
        if not isinstance(video, str) or not video:
            return _tool_error({"code": "INVALID_SCHEMA",
                                "message": "render_contact_sheet needs a video path",
                                "fix": "pass video as a rendered video file path"})
        out = args.get("output")
        if not isinstance(out, str) or not out:
            out = resolve_path(video) + ".contact.png"
        proj = None
        pid = args.get("project_id")
        if isinstance(pid, str) and pid:
            proj = _load_project(pid)
            if proj is None:
                return _tool_error({"code": "PROJECT_NOT_FOUND",
                                    "message": pid})
        ok, info = render_contact_sheet(video, out, project=proj)
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "produce_variants":
        ok, info = produce_variants(args["brief"], args["output_dir"])
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "produce_explainer":
        ok, info = produce_explainer(args["brief"], args["output"])
        if not ok:
            return _tool_error(info)
        info = dict(info)
        info.pop("project", None)
        return _text(info)
    if name == "produce_video":
        ok, info = produce_video(args["brief"], args["output"])
        if not ok:
            return _tool_error(info)
        info = dict(info)
        info.pop("project", None)
        return _text(info)
    if name == "split_clip":
        ok, info = split_clip(args["project"], args["clip_id"], args["at"])
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "timeline_summary":
        ok, info = timeline_summary(args["project"])
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "check_brief":
        return _text(check_brief_conformance(args["brief"], args["project"]))
    if name == "snapshot_project":
        project = _load_project(args["id"])
        if project is None:
            return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        label = args.get("label")
        if label and not _ID_RE.match(label):
            return _tool_error({"code": "INVALID_SCHEMA", "message": "snapshot label must match [A-Za-z0-9_-]+"})
        import uuid as _uuid
        sid = label or _uuid.uuid4().hex[:12]
        _atomic_write_json(_snapshot_path(args["id"], sid), project)
        return _text({"id": args["id"], "snapshot_id": sid, "saved": True})
    if name == "rollback_project":
        path = _snapshot_path(args["id"], args["snapshot_id"])
        if not os.path.isfile(path):
            return _tool_error({"code": "INVALID_SCHEMA", "message": f"no snapshot {args['snapshot_id']} for project {args['id']}"})
        with open(path) as fh:
            project = json.load(fh)
        errors, _info = validate_project(project)
        if errors:
            return _tool_error({"code": "INVALID_PROJECT", "errors": errors})
        bad = _save_project(project)
        if bad:
            return _tool_error(bad)
        return _text({"id": args["id"], "snapshot_id": args["snapshot_id"], "restored": True})
    if name == "list_snapshots":
        d = _snapshot_dir(args["id"])
        ids = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
        return _text({"id": args["id"], "snapshots": ids})
    if name == "get_job":
        record = JOBS.get(args["id"])
        if record is None:
            return _tool_error({"code": "JOB_NOT_FOUND", "message": args["id"]})
        record = dict(record)
        record.pop("project", None)
        return _text(record)
    if name == "start_render_job":
        if args.get("project") is not None:
            project = args["project"]
            errors, _info = validate_project(project)
            if errors:
                return _tool_error({"code": "INVALID_PROJECT", "errors": errors})
        elif args.get("id"):
            if _bad_id(args.get("id")):
                return _id_error()
            project = _load_project(args["id"])
            if project is None:
                return _tool_error({"code": "PROJECT_NOT_FOUND", "message": args["id"]})
        else:
            return _tool_error({"code": "INVALID_SCHEMA",
                                "message": "start_render_job needs id or project"})
        job_id = BG_JOBS.start(project)
        return _text({"job_id": job_id, "status": "queued"})
    if name == "get_job_status":
        rec = BG_JOBS.status(args["id"])
        if rec is None:
            return _tool_error({"code": "JOB_NOT_FOUND", "message": args["id"]})
        return _text(rec)
    if name == "cancel_job":
        rec = BG_JOBS.cancel(args["id"])
        if rec is None:
            return _tool_error({"code": "JOB_NOT_FOUND", "message": args["id"]})
        return _text(rec)
    if name == "synthesize_speech":
        from .tts import synthesize_speech
        text = args.get("text")
        out_audio = args.get("output_audio")
        out_srt = args.get("output_srt")
        voice = args.get("voice")
        rate = args.get("rate", "+0%")
        volume = args.get("volume", "+0%")
        if not text or not isinstance(text, str):
            return _tool_error({"code": "INVALID_ARGUMENT", "message": "text must be a non-empty string"})
        if not out_audio or not isinstance(out_audio, str):
            return _tool_error({"code": "INVALID_ARGUMENT", "message": "output_audio must be a valid path"})
        try:
            res = synthesize_speech(text, out_audio, out_srt, voice=voice, rate=rate, volume=volume)
            return _text(res)
        except Exception as exc:
            return _tool_error({"code": "TTS_FAILED", "message": str(exc)})
    if name == "list_voices":
        from .tts import list_voices
        lang = args.get("language")
        try:
            voices = list_voices(language_prefix=lang)
            return _text({"voices": voices, "count": len(voices)})
        except Exception as exc:
            return _tool_error({"code": "TTS_FAILED", "message": str(exc)})
    if name == "reframe_video":
        from .reframe import reframe_video
        in_video = args.get("input_video")
        out_video = args.get("output_video")
        aspect = args.get("target_aspect", "9:16")
        mode = args.get("mode", "blur_pad")
        draft = bool(args.get("draft", False))
        if not in_video or not out_video:
            return _tool_error({"code": "INVALID_ARGUMENT", "message": "input_video and output_video are required"})
        ok, info = reframe_video(in_video, out_video, target_aspect=aspect, mode=mode, draft=draft)
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "generate_web_studio":
        from .studio import generate_studio_html
        proj = args.get("project")
        vpath = args.get("video_path")
        out_html = args.get("output_html")
        if not proj or not isinstance(proj, dict):
            return _tool_error({"code": "INVALID_ARGUMENT", "message": "project must be an object"})
        try:
            out_file = generate_studio_html(proj, video_path=vpath, output_html_path=out_html)
            return _text({"studio_html": out_file, "title": proj.get("title") or proj.get("id") or "Studio"})
        except Exception as exc:
            return _tool_error({"code": "STUDIO_FAILED", "message": str(exc)})
    if name == "list_plugins":
        from .plugins import discover_plugins, list_diagram_plugins, list_motion_plugins
        disc = bool(args.get("discover", False))
        discovered = discover_plugins() if disc else []
        return _text({
            "motion_plugins": list_motion_plugins(),
            "diagram_plugins": list_diagram_plugins(),
            "discovered_files": discovered,
        })
    if name == "generate_color_palette":
        from .colors import generate_palette
        base = args.get("base_color", "#3B82F6")
        harmony = args.get("harmony", "analogous")
        dark_mode = bool(args.get("dark_mode", True))
        try:
            pal = generate_palette(base, harmony=harmony, dark_mode=dark_mode)
            return _text(pal)
        except Exception as exc:
            return _tool_error({"code": "PALETTE_ERROR", "message": str(exc)})
    if name == "analyze_text_metrics":
        from .text_metrics import analyze_text, diff_texts, estimate_reading_duration
        text = args.get("text", "")
        orig = args.get("original_text")
        wpm = args.get("wpm")
        metrics = analyze_text(text)
        metrics["estimated_reading_duration"] = estimate_reading_duration(text, wpm=wpm)
        if orig:
            metrics["diff"] = diff_texts(orig, text)
        return _text(metrics)
    if name == "list_content_templates":
        from .templates import list_content_templates as _list_tpls
        category = args.get("category")
        return _text({"templates": _list_tpls(category)})
    if name == "produce_from_template":
        from .templates import build_brief_from_template as _build_tpl
        from .render import produce_explainer as _prod_exp
        tid = args.get("template_id")
        title = args.get("title")
        scenes = args.get("scenes")
        out_p = args.get("output_path")
        aspect = args.get("aspect", "9:16")
        parallel = bool(args.get("parallel", False))

        if not tid or not title or not scenes or not out_p:
            return _tool_error({"code": "INVALID_SCHEMA", "message": "template_id, title, scenes, and output_path are required"})
        try:
            brief = _build_tpl(tid, title, scenes, aspect=aspect)
        except Exception as e:
            return _tool_error({"code": "TEMPLATE_ERROR", "message": str(e)})

        ok, info = _prod_exp(brief, out_p)
        if not ok:
            return _tool_error(info)
        return _text(info)
    return _tool_error({"code": "INVALID_SCHEMA", "message": f"unknown tool {name}"})


def _server_version():
    """Read VERSION so tools/list callers see the real release."""
    try:
        with open(os.path.join(BASE, "VERSION")) as fh:
            return fh.read().strip()
    except Exception:
        return "1.0.0"


def _respond(resp):
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            # A line that is not JSON at all still gets a JSON-RPC
            # parse error (id null: no id could be recovered), never
            # silence — and never a crash. Found in the 1.34.0
            # audit: the old code skipped the line, which is fine,
            # but a parsed non-object (e.g. [1,2,3]) crashed the
            # server below on req.get().
            _respond({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(req, dict):
            # A parsed JSON value that is not an object (a list, a
            # string, a number) has no method to dispatch: answer
            # invalid-request and stay alive. This was a server
            # crash before 1.34.0 (AttributeError on req.get()).
            _respond({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32600,
                                "message": "invalid request: expected a JSON object"}})
            continue
        method = req.get("method", "")
        if not isinstance(method, str):
            method = ""
        req_id = req.get("id")
        if method.startswith("notifications/"):
            continue
        if method == "initialize":
            params = req.get("params")
            if not isinstance(params, dict):
                params = {}
            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentcut", "version": _server_version()},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = req.get("params")
            if not isinstance(params, dict):
                # Non-object params (e.g. a string) crashed the
                # server before 1.34.0 on params.get(); answer a
                # structured tool error instead.
                result = _tool_error({"code": "INVALID_SCHEMA",
                                      "message": "tools/call params must be an object"})
            elif params.get("arguments") is not None and not isinstance(params.get("arguments"), dict):
                result = _tool_error({"code": "INVALID_SCHEMA",
                                      "message": "tool arguments must be an object"})
            else:
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
