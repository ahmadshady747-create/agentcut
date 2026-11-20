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

from .otio import export_otio
from .render import JobStore, check_brief_conformance, poster_frame, preview_project, produce_explainer, produce_variants, produce_video, render_contact_sheet, render_project, split_clip, timeline_summary
from .validate import probe_media, validate_project

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_data_dir() -> str:
    """Resolve writable AgentCut data directory safely (v2.0.0).

    Priority:
      1. AGENTCUT_HOME environment variable (if set).
      2. Source checkout `<repo>/data` when running from a writable repo tree
         (detected by pyproject.toml next to src/).
      3. User data dir (~/.local/share/agentcut or %LOCALAPPDATA%\agentcut)
         so `pip install` into read-only site-packages never raises PermissionError.
    """
    env_home = os.environ.get("AGENTCUT_HOME", "").strip()
    if env_home:
        return os.path.abspath(os.path.expanduser(env_home))
    repo_data = os.path.join(BASE, "data")
    if os.path.isfile(os.path.join(BASE, "pyproject.toml")):
        try:
            os.makedirs(repo_data, exist_ok=True)
            if os.access(repo_data, os.W_OK):
                return repo_data
        except OSError:
            pass
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, "agentcut", "data")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, "agentcut", "data")


DATA = _resolve_data_dir()
PROJECTS = os.path.join(DATA, "projects")
SNAPSHOTS = os.path.join(DATA, "snapshots")
JOBS_DIR = os.path.join(DATA, "jobs")
OUTPUTS = os.path.join(DATA, "outputs")


def _ensure_data_dirs() -> None:
    """Create data subdirectories lazily on first use (safe on read-only installs)."""
    for _d in (PROJECTS, JOBS_DIR, OUTPUTS, SNAPSHOTS):
        try:
            os.makedirs(_d, exist_ok=True)
        except OSError:
            pass


_ensure_data_dirs()

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
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
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
    {"name": "generate_glass_card_preview",
     "description": "Generate a studio-grade pure-Python 32-bit RGBA vector glassmorphism card with anti-aliasing, linear gradient, glowing rim, and drop shadow.",
     "inputSchema": {"type": "object", "properties": {
         "width": {"type": "integer", "description": "Card width in pixels (e.g. 500)."},
         "height": {"type": "integer", "description": "Card height in pixels (e.g. 260)."},
         "border_radius": {"type": "number", "description": "Corner radius in pixels (default 24.0)."},
         "bg_color_top": {"type": "string", "description": "Top background color or RGBA (default 'rgba(30, 41, 59, 0.85)')."},
         "bg_color_bottom": {"type": "string", "description": "Bottom background color or RGBA (default 'rgba(15, 23, 42, 0.95)')."},
         "border_width": {"type": "number", "description": "Glowing rim border width (default 1.5)."},
         "border_color": {"type": "string", "description": "Rim border color (default 'rgba(255, 255, 255, 0.25)')."},
         "drop_shadow": {"type": "boolean", "description": "Whether to include soft drop shadow (default true)."}},
         "required": ["width", "height"]}},
    {"name": "export_otio",
     "description": "Export a stored project (by id) or a project dict to an OpenTimelineIO (.otio) timeline file — stdlib JSON, no dependency. Carries cuts, clip order, timing, transitions (as dissolves), chapter markers and media references; effects, grades, texts, captions, diagrams and motion are NOT carried (OTIO cannot hold them). Returns the .otio path, duration, counts and warnings. DaVinci Resolve import is unverified in this environment — open the file in Resolve to confirm.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"},
         "project": {"type": "object"},
         "output": {"type": "string"}}}},
    {"name": "list_render_profiles",
     "description": "List all built-in hardware-aware render profiles (proxy, draft, low_spec, master, cinema) with resolution scaling, FPS, preset, CRF, grain mode, and safe worker counts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "detect_hardware_profile",
     "description": "Inspect host CPU cores and available RAM to recommend the safest render profile and max_workers count without OOM or swap thrashing.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "audit_video_quality",
     "description": "Run automated FFprobe/FFmpeg quality control (QC) on a rendered video: checks SAR=1:1, A/V sync drift, black frames, silence gaps, loudness, and audio clipping, returning a 0-100 score, grade, pass verdict, and actionable fixes.",
     "inputSchema": {"type": "object", "properties": {
         "video": {"type": "string", "description": "Path to rendered MP4/MOV video file."},
         "expected_duration": {"type": "number", "description": "Optional target duration in seconds."},
         "check_black": {"type": "boolean", "description": "Detect black-frame segments (default true)."},
         "check_silence": {"type": "boolean", "description": "Detect long silence gaps (default true)."},
         "require_audio": {"type": "boolean", "description": "Warn if audio stream is missing (default true)."}},
         "required": ["video"]}},
    {"name": "validate_media_bundle",
     "description": "Validate an AgentMedia scene bundle manifest (audio, images, word_timings) before converting to an editorial explainer brief.",
     "inputSchema": {"type": "object", "properties": {
         "bundle": {"type": "object", "description": "AgentMedia bundle JSON object."},
         "check_files": {"type": "boolean", "description": "Verify local file paths exist (default true)."}},
         "required": ["bundle"]}},
    {"name": "import_media_bundle",
     "description": "Convert an AgentMedia bundle into an editorial explainer brief (auto-splitting 2.5-4.0s visual beats at natural speech pauses) and optionally render + QC audit it in one call.",
     "inputSchema": {"type": "object", "properties": {
         "bundle": {"type": "object", "description": "AgentMedia bundle JSON object."},
         "output": {"type": "string", "description": "Optional MP4 output path; if omitted, returns the generated brief without rendering."},
         "aspect": {"type": "string", "description": "Target aspect ratio (default '9:16')."},
         "profile": {"type": "string", "description": "Render profile name (proxy, draft, low_spec, master, cinema)."},
         "parallel": {"type": "boolean", "description": "Enable multi-beat parallel editorial rendering (default true)."},
         "qc_audit": {"type": "boolean", "description": "Run audit_video_quality after rendering (default false)."}},
         "required": ["bundle"]}},
    {"name": "smart_cut_silence",
     "description": "Analyze a video/audio file with FFmpeg silencedetect to remove dead-air pauses and generate either a jump-cut AgentCut project JSON or a rendered tightened MP4.",
     "inputSchema": {"type": "object", "properties": {
         "input": {"type": "string", "description": "Input video/audio file path."},
         "output": {"type": "string", "description": "Optional output MP4 path; if omitted, returns speech segments and jump-cut project JSON."},
         "noise_db": {"type": "number", "description": "Silence threshold in dB (default -35.0)."},
         "min_silence": {"type": "number", "description": "Minimum silence gap to cut in seconds (default 0.55)."}},
         "required": ["input"]}},
    {"name": "batch_produce",
     "description": "Execute a multi-project batch production manifest (explainer, project, bundle, or smart_cut jobs) with concurrency throttling, resume-if-done skipping, error isolation, and optional QC audit.",
     "inputSchema": {"type": "object", "properties": {
         "manifest": {"type": "object", "description": "Batch manifest with 'jobs' array and optional 'default_profile' and 'max_concurrency'."},
         "max_concurrency": {"type": "integer", "description": "Max concurrent jobs (auto-throttled if omitted)."},
         "resume": {"type": "boolean", "description": "Skip jobs whose output MP4 already exists (default true)."},
         "qc_audit": {"type": "boolean", "description": "Run QC audit on each completed output (default false)."},
         "report_path": {"type": "string", "description": "Optional path to write batch_report.json."}},
         "required": ["manifest"]}},
    {"name": "get_cache_stats",
     "description": "Return scene & segment render cache statistics (file counts, total MB, max LRU cap) for a project directory or default temp cache.",
     "inputSchema": {"type": "object", "properties": {
         "project_dir": {"type": "string", "description": "Optional project directory containing .agentcut_cache."}}}},
    {"name": "clear_render_cache",
     "description": "Clear cached scene MP4s, segment MKVs, and stale temp files from .agentcut_cache and return the count removed.",
     "inputSchema": {"type": "object", "properties": {
         "project_dir": {"type": "string", "description": "Optional project directory containing .agentcut_cache."}}}},
    {"name": "list_cinema_looks",
     "description": "List all built-in zero-LUT cinema color grades (chiaroscuro_doc, kodak_warm, noir_slate, editorial_clean, warm_archive, midnight_tech) with moods and FFmpeg filter chains.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "humanize_brief",
     "description": "Transform a mechanical explainer brief into a human-directed editorial documentary brief: applies a cinema_look, narrative-driven non-repetitive organic camera moves, varied transitions, restrained highlight cards, and soundscape/J-cut plan.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Explainer brief JSON object."},
         "cinema_look": {"type": "string", "description": "Built-in cinema look name (default 'chiaroscuro_doc')."},
         "add_soundscape": {"type": "boolean", "description": "Attach room tone, SFX cues, and J-cut schedule (default true)."},
         "auto_cards": {"type": "boolean", "description": "Auto-place restrained highlight cards on key moments (default true)."}},
         "required": ["brief"]}},
    {"name": "direct_from_script",
     "description": "Convert raw Arabic or English essay prose into a complete human-directed editorial brief with reading-time scene pacing, organic camera choreography, varied transitions, restrained highlight cards, and soundscape cues.",
     "inputSchema": {"type": "object", "properties": {
         "script": {"type": "string", "description": "Raw Arabic or English script text."},
         "title": {"type": "string", "description": "Video title (default 'Editorial Essay')."},
         "cinema_look": {"type": "string", "description": "Cinema look preset (default 'chiaroscuro_doc')."},
         "aspect": {"type": "string", "description": "Aspect ratio (default '9:16')."},
         "profile": {"type": "string", "description": "Render profile (default 'low_spec')."}},
         "required": ["script"]}},
    {"name": "audit_editorial_craft",
     "description": "Audit a brief BEFORE rendering for robotic/automated patterns (metronomic beat durations, repetitive camera moves, uniform transitions, card spam, flat soundscape) and return a 0-100 human-feel score with fixes.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Explainer/editorial brief JSON object."}},
         "required": ["brief"]}},
    {"name": "synthesize_sfx",
     "description": "Synthesize organic cinema SFX (chapter_boom, soft_whoosh, card_tick, shimmer_reveal, tension_riser) using pure FFmpeg lavfi with zero external assets.",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "SFX kind: chapter_boom, soft_whoosh, card_tick, shimmer_reveal, tension_riser."},
         "output": {"type": "string", "description": "Output WAV/MP3 path."},
         "duration": {"type": "number", "description": "Optional duration in seconds."},
         "volume_db": {"type": "number", "description": "Optional volume in dB."}},
         "required": ["kind", "output"]}},
    {"name": "generate_room_tone",
     "description": "Generate a subtle documentary room-tone ambience bed (warm_studio, soft_vinyl, night_air) so speech pauses never drop into dead digital silence.",
     "inputSchema": {"type": "object", "properties": {
         "output": {"type": "string", "description": "Output WAV/MP3 path."},
         "duration": {"type": "number", "description": "Duration in seconds."},
         "preset": {"type": "string", "description": "Preset: warm_studio, soft_vinyl, or night_air (default 'warm_studio')."},
         "volume_db": {"type": "number", "description": "Optional volume in dB (default -38.0)."}},
         "required": ["output", "duration"]}},
    {"name": "detect_focal_point",
     "description": "Detect the subject focal point (focal_x, focal_y) in an image or video frame using zero-dependency gradient-energy centroid analysis so camera moves anchor on the real subject.",
     "inputSchema": {"type": "object", "properties": {
         "media": {"type": "string", "description": "Path to local image or video file."},
         "sample_time": {"type": "number", "description": "Optional timestamp in seconds for video inputs (default 0.0)."}},
         "required": ["media"]}},
    {"name": "match_shot_exposure",
     "description": "Measure average luminance (YAVG) across a sequence of images/shots and compute gentle FFmpeg eq filters to harmonize exposure across cuts.",
     "inputSchema": {"type": "object", "properties": {
         "media_paths": {"type": "array", "items": {"type": "string"}, "description": "List of local image/video paths."},
         "target_yavg": {"type": "number", "description": "Optional target luminance 0-255 (defaults to clamped sequence median)."}},
         "required": ["media_paths"]}},
    {"name": "render_chapter_card",
     "description": "Render a documentary chapter title divider MP4 with Arabic/English BiDi typography, cinema look, fade in/out, and optional procedural chapter_boom SFX.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "Chapter title text."},
         "output": {"type": "string", "description": "Output MP4 path."},
         "subtitle": {"type": "string", "description": "Optional subtitle."},
         "chapter_label": {"type": "string", "description": "Optional eyebrow label (e.g. 'الفصل الأول')."},
         "duration": {"type": "number", "description": "Duration in seconds (default 2.0)."},
         "width": {"type": "integer", "description": "Video width (default 1080)."},
         "height": {"type": "integer", "description": "Video height (default 1920)."},
         "cinema_look": {"type": "string", "description": "Cinema look name (default 'chiaroscuro_doc')."},
         "with_boom": {"type": "boolean", "description": "Include procedural chapter_boom SFX (default true)."}},
         "required": ["title", "output"]}},
    {"name": "render_parallax_clip",
     "description": "Render a 2.5D depth-of-field parallax shot from a background image and foreground RGBA layer moving at different camera speeds.",
     "inputSchema": {"type": "object", "properties": {
         "background": {"type": "string", "description": "Background image path."},
         "foreground": {"type": "string", "description": "Foreground RGBA image path."},
         "output": {"type": "string", "description": "Output MP4 path."},
         "duration": {"type": "number", "description": "Duration in seconds (default 3.5)."},
         "width": {"type": "integer", "description": "Width (default 1080)."},
         "height": {"type": "integer", "description": "Height (default 1920)."},
         "bg_blur": {"type": "number", "description": "Background Gaussian blur sigma (default 2.5)."},
         "cinema_look": {"type": "string", "description": "Cinema look preset (default 'chiaroscuro_doc')."}},
         "required": ["background", "foreground", "output"]}},
    {"name": "compile_timeline_ir",
     "description": "Compile an explainer/editorial brief into a typed TimelineIR (with optional auto focal-point detection and shot exposure matching) for fast inspection before rendering.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Explainer brief JSON object."},
         "auto_focal": {"type": "boolean", "description": "Detect focal points on local beat images (default false)."},
         "match_exposure": {"type": "boolean", "description": "Compute exposure matching filters across beat images (default false)."}},
         "required": ["brief"]}},
    {"name": "produce_cinematic_auto_heal",
     "description": "Run the closed-loop self-healing documentary pipeline: craft audit -> auto-humanize -> auto focal-point & exposure match -> render with live soundscape mixing -> post-render QC -> auto-repair retry if needed.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Explainer/editorial brief JSON object."},
         "output": {"type": "string", "description": "Output MP4 path."},
         "cinema_look": {"type": "string", "description": "Cinema look (default 'chiaroscuro_doc')."},
         "profile": {"type": "string", "description": "Render profile (default 'low_spec')."},
         "auto_focal": {"type": "boolean", "description": "Auto-detect focal points (default true)."},
         "match_exposure": {"type": "boolean", "description": "Auto-harmonize exposure across shots (default true)."},
         "mix_soundscape": {"type": "boolean", "description": "Mix room tone + SFX into final video (default true)."}},
         "required": ["brief", "output"]}},
    {"name": "repair_brief",
     "description": "Deterministically repair messy or weak-LLM inputs (markdown-fenced JSON, trailing commas, wrong key aliases, typos in look/profile/aspect, string durations, plain string scenes) into a clean AgentCut brief.",
     "inputSchema": {"type": "object", "properties": {
         "input": {"description": "Dict, loose JSON string, or raw essay text."}},
         "required": ["input"]}},
    {"name": "polish_script",
     "description": "Strip Markdown/URL noise, split run-on scenes into rhythmic documentary beats, and optionally auto-generate UTF-8 .srt subtitle files.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Brief JSON object."},
         "max_words_per_scene": {"type": "integer", "description": "Max words per scene before splitting (default 24)."},
         "srt_dir": {"type": "string", "description": "Optional directory to write generated .srt files."}},
         "required": ["brief"]}},
    {"name": "list_style_bibles",
     "description": "List all 6 deterministic visual Style Bibles (chiaroscuro_doc, kodak_warm, noir_slate, warm_archive, midnight_tech, editorial_clean) for consistent image generation.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "generate_visual_prompts",
     "description": "Deterministically compile Style-Bible-locked, shot-scale-alternating image prompts for every scene/beat so even small LLMs get cohesive documentary visuals.",
     "inputSchema": {"type": "object", "properties": {
         "brief": {"type": "object", "description": "Brief JSON object."},
         "style_bible": {"type": "string", "description": "Style Bible name (default from brief.cinema_look or 'chiaroscuro_doc')."},
         "aspect": {"type": "string", "description": "Aspect ratio (default '9:16')."}},
         "required": ["brief"]}},
    {"name": "autopilot_produce",
     "description": "Single-call model-agnostic autopilot: accepts raw Arabic/English text, messy JSON, or a partial brief -> auto-repairs -> polishes script & generates missing SRTs -> compiles Style Bible visual prompts -> humanizes -> renders with auto-heal & live soundscape when narration audio is present.",
     "inputSchema": {"type": "object", "properties": {
         "input": {"description": "Raw essay text, messy JSON string, or brief dict."},
         "output": {"type": "string", "description": "Optional output MP4 path (renders automatically if scenes have local narration audio)."},
         "cinema_look": {"type": "string", "description": "Cinema look / Style Bible (default 'chiaroscuro_doc')."},
         "aspect": {"type": "string", "description": "Aspect ratio (default '9:16')."},
         "profile": {"type": "string", "description": "Render profile (default 'low_spec')."}},
         "required": ["input"]}},
    {"name": "list_agentcut_skills",
     "description": "List all modular AgentCut v3.2.0 skills bundled in skills/*/SKILL.md (autopilot, editorial-director, agentmedia-bridge, qc-healer) with descriptions.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_agentcut_skill",
     "description": "Load the full SKILL.md instructions for a bundled AgentCut v3.2.0 skill by name so any connected MCP agent can follow the exact workflow on demand.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Skill name (e.g. 'agentcut-autopilot', 'agentcut-editorial-director')."}},
         "required": ["name"]}},
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
    if name == "export_otio":
        # v1.61.0 (34th tool): stored project by id, or a project
        # dict; output defaults to data/outputs/otio/<id>.otio.
        if args.get("project") is not None:
            project = args["project"]
        elif args.get("id"):
            if _bad_id(args.get("id")):
                return _id_error()
            project = _load_project(args["id"])
            if project is None:
                return _tool_error({"code": "PROJECT_NOT_FOUND",
                                    "message": args["id"]})
        else:
            return _tool_error({"code": "INVALID_SCHEMA",
                                "message": "export_otio needs id or project",
                                "fix": "pass id of a stored project or a project dict"})
        out = args.get("output")
        if not isinstance(out, str) or not out:
            pid = project.get("id", "timeline") if isinstance(project, dict) else "timeline"
            out_dir = os.path.join(OUTPUTS, "otio")
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, f"{pid}.otio")
        ok, info = export_otio(project, out)
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
        if parallel:
            brief["parallel"] = True

        ok, info = _prod_exp(brief, out_p)
        if not ok:
            return _tool_error(info)
        return _text(info)
    if name == "generate_glass_card_preview":
        from .vector_ui import get_cached_glass_card
        w = int(args.get("width", 500))
        h = int(args.get("height", 260))
        rx = float(args.get("border_radius", 24.0))
        top = args.get("bg_color_top", "rgba(30, 41, 59, 0.85)")
        bot = args.get("bg_color_bottom", "rgba(15, 23, 42, 0.95)")
        bw = float(args.get("border_width", 1.5))
        bc = args.get("border_color", "rgba(255, 255, 255, 0.25)")
        ds = bool(args.get("drop_shadow", True))
        card_path = get_cached_glass_card(
            width=w, height=h, border_radius=rx,
            bg_color_top=top, bg_color_bottom=bot,
            border_width=bw, border_color=bc, drop_shadow=ds,
        )
        return _text({
            "status": "success",
            "path": card_path,
            "width": w,
            "height": h,
            "border_radius": rx,
            "ui_style": "glass_vector",
        })
    if name == "list_render_profiles":
        from .profiles import list_render_profiles
        return _text({"profiles": list_render_profiles()})
    if name == "detect_hardware_profile":
        from .profiles import detect_hardware_profile
        return _text(detect_hardware_profile())
    if name == "audit_video_quality":
        from .qc import audit_video_quality
        vpath = args.get("video")
        if not isinstance(vpath, str) or not vpath:
            return _tool_error({"code": "MISSING_FIELD", "message": "video is required"})
        report = audit_video_quality(
            vpath,
            expected_duration=args.get("expected_duration"),
            check_black=bool(args.get("check_black", True)),
            check_silence=bool(args.get("check_silence", True)),
            require_audio=bool(args.get("require_audio", True)),
        )
        return _text(report)
    if name == "validate_media_bundle":
        from .bundle import validate_media_bundle
        ok_b, errs_b = validate_media_bundle(
            args.get("bundle"),
            check_files=bool(args.get("check_files", True)),
        )
        return _text({"valid": ok_b, "errors": errs_b})
    if name == "import_media_bundle":
        from .bundle import bundle_to_editorial_brief, produce_from_bundle, validate_media_bundle
        bundle = args.get("bundle")
        out_mp4 = args.get("output")
        if out_mp4:
            ok_p, info_p = produce_from_bundle(
                bundle,
                out_mp4,
                aspect=args.get("aspect"),
                profile=args.get("profile"),
                parallel=bool(args.get("parallel", True)),
                qc_audit=bool(args.get("qc_audit", False)),
            )
            if not ok_p:
                return _tool_error(info_p)
            info_p = dict(info_p)
            info_p.pop("project", None)
            return _text(info_p)
        ok_b, errs_b = validate_media_bundle(bundle, check_files=False)
        if not ok_b:
            return _tool_error({"code": "INVALID_BUNDLE", "errors": errs_b})
        brief = bundle_to_editorial_brief(
            bundle,
            aspect=args.get("aspect"),
            profile=args.get("profile"),
            parallel=bool(args.get("parallel", True)),
        )
        return _text({"brief": brief})
    if name == "smart_cut_silence":
        from .smart_cut import build_jump_cut_project, smart_cut_media
        inp = args.get("input")
        if not isinstance(inp, str) or not inp:
            return _tool_error({"code": "MISSING_FIELD", "message": "input is required"})
        noise_db = float(args.get("noise_db", -35.0))
        min_silence = float(args.get("min_silence", 0.55))
        out_mp4 = args.get("output")
        try:
            if out_mp4:
                ok_s, info_s = smart_cut_media(
                    inp, out_mp4, noise_db=noise_db, min_silence=min_silence
                )
                if not ok_s:
                    return _tool_error(info_s)
                return _text(info_s)
            proj, analysis = build_jump_cut_project(
                inp, noise_db=noise_db, min_silence=min_silence
            )
            analysis = dict(analysis)
            analysis.pop("probe", None)
            return _text({"project": proj, "analysis": analysis})
        except (OSError, ValueError) as exc:
            return _tool_error({"code": "SMART_CUT_ERROR", "message": str(exc)})
    if name == "batch_produce":
        from .batch import run_batch_production
        manifest = args.get("manifest")
        try:
            summary = run_batch_production(
                manifest,
                max_concurrency=args.get("max_concurrency"),
                resume=bool(args.get("resume", True)),
                qc_audit=bool(args.get("qc_audit", False)),
                report_path=args.get("report_path"),
            )
            return _text(summary)
        except (OSError, ValueError) as exc:
            return _tool_error({"code": "BATCH_ERROR", "message": str(exc)})
    if name == "get_cache_stats":
        from .cache import get_cache_stats
        return _text(get_cache_stats(args.get("project_dir")))
    if name == "clear_render_cache":
        from .cache import clear_cache
        return _text({"removed": clear_cache(args.get("project_dir"))})
    if name == "list_cinema_looks":
        from .cinematography import list_cinema_looks
        return _text({"looks": list_cinema_looks()})
    if name == "humanize_brief":
        from .director import humanize_brief
        brief = args.get("brief")
        if not isinstance(brief, dict):
            return _tool_error({"code": "INVALID_SCHEMA", "message": "brief must be an object"})
        try:
            directed = humanize_brief(
                brief,
                cinema_look=str(args.get("cinema_look") or "chiaroscuro_doc"),
                add_soundscape=bool(args.get("add_soundscape", True)),
                auto_cards=bool(args.get("auto_cards", True)),
            )
            return _text(directed)
        except ValueError as exc:
            return _tool_error({"code": "DIRECTOR_ERROR", "message": str(exc)})
    if name == "direct_from_script":
        from .director import direct_from_script
        script = args.get("script")
        if not isinstance(script, str) or not script.strip():
            return _tool_error({"code": "MISSING_FIELD", "message": "script is required"})
        try:
            directed = direct_from_script(
                script,
                title=str(args.get("title") or "Editorial Essay"),
                cinema_look=str(args.get("cinema_look") or "chiaroscuro_doc"),
                aspect=str(args.get("aspect") or "9:16"),
                profile=args.get("profile", "low_spec"),
            )
            return _text(directed)
        except ValueError as exc:
            return _tool_error({"code": "DIRECTOR_ERROR", "message": str(exc)})
    if name == "audit_editorial_craft":
        from .qc import audit_editorial_craft
        return _text(audit_editorial_craft(args.get("brief")))
    if name == "synthesize_sfx":
        from .soundscape import synthesize_sfx
        ok_s, info_s = synthesize_sfx(
            str(args.get("kind") or ""),
            str(args.get("output") or ""),
            duration=args.get("duration"),
            volume_db=args.get("volume_db"),
        )
        if not ok_s:
            return _tool_error(info_s)
        return _text(info_s)
    if name == "generate_room_tone":
        from .soundscape import generate_room_tone
        ok_r, info_r = generate_room_tone(
            str(args.get("output") or ""),
            float(args.get("duration") or 5.0),
            preset=str(args.get("preset") or "warm_studio"),
            volume_db=args.get("volume_db"),
        )
        if not ok_r:
            return _tool_error(info_r)
        return _text(info_r)
    if name == "detect_focal_point":
        from .vision import detect_focal_point
        try:
            return _text(detect_focal_point(str(args.get("media") or ""), sample_time=float(args.get("sample_time", 0.0))))
        except (OSError, ValueError) as exc:
            return _tool_error({"code": "VISION_ERROR", "message": str(exc)})
    if name == "match_shot_exposure":
        from .vision import compute_exposure_match_filter
        paths = args.get("media_paths") or []
        return _text(compute_exposure_match_filter(paths, target_yavg=args.get("target_yavg")))
    if name == "render_chapter_card":
        from .parallax import render_chapter_card
        ok_c, info_c = render_chapter_card(
            title=str(args.get("title") or ""),
            output_path=str(args.get("output") or ""),
            subtitle=args.get("subtitle"),
            chapter_label=args.get("chapter_label"),
            duration=float(args.get("duration", 2.0)),
            width=int(args.get("width", 1080)),
            height=int(args.get("height", 1920)),
            cinema_look=str(args.get("cinema_look") or "chiaroscuro_doc"),
            with_boom=bool(args.get("with_boom", True)),
        )
        if not ok_c:
            return _tool_error(info_c)
        return _text(info_c)
    if name == "render_parallax_clip":
        from .parallax import render_parallax_clip
        ok_p, info_p = render_parallax_clip(
            bg_path=str(args.get("background") or ""),
            fg_path=str(args.get("foreground") or ""),
            output_path=str(args.get("output") or ""),
            duration=float(args.get("duration", 3.5)),
            width=int(args.get("width", 1080)),
            height=int(args.get("height", 1920)),
            bg_blur=float(args.get("bg_blur", 2.5)),
            cinema_look=args.get("cinema_look", "chiaroscuro_doc"),
        )
        if not ok_p:
            return _tool_error(info_p)
        return _text(info_p)
    if name == "compile_timeline_ir":
        from .pipeline.ir import compile_brief_to_ir
        brief = args.get("brief")
        if not isinstance(brief, dict):
            return _tool_error({"code": "INVALID_SCHEMA", "message": "brief must be an object"})
        ir = compile_brief_to_ir(
            brief,
            auto_focal=bool(args.get("auto_focal", False)),
            match_exposure=bool(args.get("match_exposure", False)),
        )
        return _text(ir.to_dict())
    if name == "produce_cinematic_auto_heal":
        from .pipeline.auto_heal import produce_cinematic_auto_heal
        brief = args.get("brief")
        out_p = args.get("output")
        if not isinstance(brief, dict) or not out_p:
            return _tool_error({"code": "INVALID_SCHEMA", "message": "brief and output are required"})
        ok_h, info_h = produce_cinematic_auto_heal(
            brief,
            str(out_p),
            cinema_look=str(args.get("cinema_look") or "chiaroscuro_doc"),
            profile=args.get("profile", "low_spec"),
            auto_focal=bool(args.get("auto_focal", True)),
            match_exposure=bool(args.get("match_exposure", True)),
            mix_soundscape=bool(args.get("mix_soundscape", True)),
        )
        if not ok_h:
            return _tool_error(info_h)
        return _text(info_h)
    if name == "repair_brief":
        from .brief_repair import repair_brief
        try:
            rep, changes = repair_brief(args.get("input"))
            return _text({"brief": rep, "repairs_made": changes})
        except ValueError as exc:
            return _tool_error({"code": "REPAIR_ERROR", "message": str(exc)})
    if name == "polish_script":
        from .script_polisher import polish_script_scenes
        brief = args.get("brief")
        if not isinstance(brief, dict):
            return _tool_error({"code": "INVALID_SCHEMA", "message": "brief must be an object"})
        pol, stats = polish_script_scenes(
            brief,
            max_words_per_scene=int(args.get("max_words_per_scene", 24)),
            srt_dir=args.get("srt_dir"),
        )
        return _text({"brief": pol, "stats": stats})
    if name == "list_style_bibles":
        from .art_director import list_style_bibles
        return _text({"style_bibles": list_style_bibles()})
    if name == "generate_visual_prompts":
        from .art_director import generate_visual_prompts
        brief = args.get("brief")
        if not isinstance(brief, dict):
            return _tool_error({"code": "INVALID_SCHEMA", "message": "brief must be an object"})
        return _text(generate_visual_prompts(brief, style_bible=args.get("style_bible"), aspect=args.get("aspect")))
    if name == "autopilot_produce":
        from .autopilot import autopilot_produce
        try:
            ok_a, res_a = autopilot_produce(
                args.get("input"),
                output_path=args.get("output"),
                cinema_look=str(args.get("cinema_look") or "chiaroscuro_doc"),
                aspect=args.get("aspect"),
                profile=str(args.get("profile") or "low_spec"),
            )
            if not ok_a:
                return _tool_error(res_a)
            return _text(res_a)
        except (OSError, ValueError) as exc:
            return _tool_error({"code": "AUTOPILOT_ERROR", "message": str(exc)})
    if name == "list_agentcut_skills":
        from .skills_registry import list_agentcut_skills
        return _text({"skills": list_agentcut_skills()})
    if name == "get_agentcut_skill":
        from .skills_registry import get_agentcut_skill
        sk = get_agentcut_skill(str(args.get("name") or ""))
        if sk is None:
            return _tool_error({"code": "SKILL_NOT_FOUND", "message": f"unknown skill {args.get('name')!r}"})
        return _text(sk)
    return _tool_error({"code": "INVALID_SCHEMA", "message": f"unknown tool {name}"})


def _server_version():
    """Read VERSION or package version so tools/list callers see the real release."""
    try:
        from . import __version__
        return __version__
    except Exception:
        pass
    try:
        with open(os.path.join(BASE, "VERSION")) as fh:
            return fh.read().strip()
    except Exception:
        return "1.51.0"


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


def main():
    """Main entrypoint for agentcut-mcp console script."""
    serve()


if __name__ == "__main__":
    main()
