"""Project schema, probing and validation for AgentCut v1.

Validation runs before any render and returns structured errors, never
a bare exception, so an agent can fix the project and retry.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .srt import parse_srt_file

MAX_DURATION = 600.0
MAX_DIMENSION = 3840
MAX_FPS = 60

# Incoming-clip transitions that overlap the previous clip by 0.5s
# via FFmpeg xfade (video) + acrossfade (audio). The value is the
# xfade transition name; audio always acrossfades, since xfade-style
# wipes/slides have no audio equivalent.
XFADE_TRANSITIONS = {
    "crossfade": "fade",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "wipe_up": "wipeup",
    "wipe_down": "wipedown",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "slide_up": "slideup",
    "slide_down": "slidedown",
    "dissolve": "dissolve",
    "fade_black": "fadeblack",
    "fade_white": "fadewhite",
    "pixelize": "pixelize",
    "radial": "radial",
    "circle_open": "circleopen",
    "circle_close": "circleclose",
    "smooth_left": "smoothleft",
    "smooth_right": "smoothright",
    "smooth_up": "smoothup",
    "smooth_down": "smoothdown",
    "diag_tl": "diagtl",
    "diag_tr": "diagtr",
    "diag_bl": "diagbl",
    "diag_br": "diagbr",
    "cover_left": "coverleft",
    "cover_right": "coverright",
    "cover_up": "coverup",
    "cover_down": "coverdown",
    "reveal_left": "revealleft",
    "reveal_right": "revealright",
    "reveal_up": "revealup",
    "reveal_down": "revealdown",
    "distance": "distance",
    "rect_crop": "rectcrop",
    # v1.40.0: zoom (scale-through), diagonal top-left wipe and
    # horizontal squeeze — all present in this build's xfade
    # list, none previously exposed. circlecrop was tried for an
    # iris transition and REJECTED: on this build its midpoint
    # renders essentially black (a 1-2px dot) with real videos
    # and with solid colours alike — do not expose it.
    "zoom": "zoomin",
    "wipe_tl": "wipetl",
    "squeeze_h": "squeezeh",
}

TEXT_COLORS = {"white", "black", "yellow", "red", "green", "blue", "cyan",
               "magenta", "orange", "purple"}

# Keyframe easing values (pro pack). "linear" is the default and the
# old behaviour; the others reshape the normalised segment time
# before interpolation (see _interp_expr_eased in render.py).
# "spring" (v1.45.0, R6) is the underdamped response (k=180,
# c=14, m=1; ~15% overshoot); "cubic_bezier" is the CSS-ease
# curve (degree-4 polynomial fit, clamped). Both are in the
# shared easing system (motion.apply_easing /
# render._easing_factor).
EASING_VALUES = {"linear", "ease_in", "ease_out", "ease_in_out",
                 "ease_out_back", "spring", "cubic_bezier"}

# Colour-grade presets (pro pack). These are honest, documented
# filter combinations — eq/colorbalance/saturation settings, NOT
# film-stock emulation. Each value is the FFmpeg filter chain
# appended after a clip's other colour filters.
# Custom transitions (v1.44.0, F5): NOT xfade names — they are
# post-join effect passes that straddle a plain concat cut, so the
# joined video is built with concat and the effect is applied
# over the cut window afterwards (see render.py
# _apply_custom_transition). The old xfade "zoom" (zoomin) is
# untouched; zoom_out here is the cut-straddling pulse.
CUSTOM_TRANSITIONS = {"zoom_out", "whip_pan", "glitch", "light_leak"}

# Animated text presets (v1.44.0, F4): named entrance styles on
# texts/captions. typewriter reveals WHOLE WORDS only (never a
# mid-word split, which breaks Arabic joining) via the existing
# reveal-word expansion; kinetic_words staggers per-word
# slide+fade; highlight_sweep sweeps a bar behind the whole line;
# underline_swipe sweeps a thin bar under the line. reveal_mask
# was DROPPED (v1.44.0, R7): the animated crop it needs is not
# feasible -- crop w/h are fixed at config time on this FFmpeg
# 8.1 build (proven: w stayed 500 past frame 15 with an n-driven
# expression), so a crop cannot reveal over time; the geq-alpha
# alternative works in a probe but needs a new looped-PNG input
# whose frame-number timing depends on the input framerate rather
# than the render timeline (fps-fragile), a whole new render path
# for one preset. The other seven presets cover the entrance
# vocabulary. A preset and an explicit animation/reveal/
# word_timings on the same item conflict and are rejected.
TEXT_PRESETS = ("typewriter", "slide_in", "pop_in", "fade_up",
                "kinetic_words", "highlight_sweep", "underline_swipe")

GRADE_PRESETS = {
    # Slightly lifted contrast, pulled-back saturation, a touch of
    # blue in the shadows and warmth in the highlights.
    "cinematic": ("eq=contrast=1.10:brightness=-0.03:saturation=0.85,"
                  "colorbalance=rs=-0.05:bs=0.10:rh=0.05:bh=-0.05"),
    # Shadows pushed toward teal (green+blue up, red down),
    # highlights pushed toward orange (red up, blue down).
    "teal_orange": ("colorbalance=rs=-0.20:gs=0.20:bs=0.20:"
                    "rh=0.30:gh=0.05:bh=-0.30,eq=saturation=1.10"),
    # Warm highlights, slightly brighter and more saturated.
    "warm": ("colorbalance=rh=0.20:gh=0.05:bh=-0.20,"
             "eq=brightness=0.02:saturation=1.10"),
    # Cool highlights (blue up, red down), slightly more saturated.
    "cool": ("colorbalance=rh=-0.20:bh=0.20,eq=saturation=1.05"),
    # No colour at all, with raised contrast: black-and-white.
    "noir": "eq=saturation=0:contrast=1.15:brightness=-0.02",
    # Delta: faded, warm, muted with a soft vignette — an aged
    # film feel. Settings, not a film stock.
    "vintage": ("eq=contrast=0.92:brightness=0.04:saturation=0.70,"
                "colorbalance=rh=0.12:gh=0.02:bh=-0.12,"
                "vignette=angle=PI/4"),
    # Delta: punchy contrast/saturation plus luma sharpening.
    "crisp": ("eq=contrast=1.12:saturation=1.15,"
              "unsharp=5:5:0.8:5:5:0.0"),
}

# Blurred-background fits (delta): "blur" is the original gblur
# fill; "blur_pad" is the same fill with a boxblur kernel
# (split + scale + boxblur + overlay). Every blur exclusion
# (Ken Burns, chroma, stabilize, speed_curve, prescale skip,
# default-motion exclusion) applies to both names.
BLUR_FITS = ("blur", "blur_pad")
TEXT_POSITIONS = {"top_left", "top_center", "top_right", "center",
                  "bottom_left", "bottom_center", "bottom_right"}

# Text style presets (v1.40.0): size ratios, colour, shadow and
# box defaults usable on ANY text or caption, not tied to a
# look or style. Render fills only fields the item does not set
# (see _resolve_text_style in render.py), so an explicit size,
# colour, box or shadow always wins and old projects render
# identically. Size fractions are of the output height: at the
# 720x1280 default they give title 70, heading 56, label 38,
# caption 36 (the caption preset matches the old fixed caption
# default size only at that height — presets scale with the
# output by design, since they are ratios).
TEXT_STYLES = {
    "title": {"size_frac": 0.055, "color": "yellow",
              "shadow": (2, 2), "box": False},
    "heading": {"size_frac": 0.044, "color": "white",
                "shadow": (2, 2), "box": False},
    "label": {"size_frac": 0.030, "color": "yellow",
              "shadow": (2, 2), "box": False},
    "caption": {"size_frac": 0.028, "color": "white",
                "shadow": (2, 2), "box": True},
    # Rounded translucent backdrop card (v1.41.0, Fix 6 title
    # treatment): legibility from the card + shadow, never
    # from a black box behind every line.
    "card": {"size_frac": 0.055, "color": "white",
             "shadow": (2, 2), "box": True, "box_radius": 18,
             "box_color": "black", "box_opacity": 0.45},
    # Modern display styles (v1.44.0, F4 typography system):
    # opt-in designed looks — size, colour, font, shadow and
    # box resolved together. Existing title/heading/label/
    # caption/card values are FROZEN (never change resolved
    # values), so old projects render byte-identically.
    "display": {"size_frac": 0.075, "color": "white",
                "font": "noto-sans-bold", "shadow": (3, 3),
                "box": True, "box_radius": 24,
                "box_color": "black", "box_opacity": 0.55},
    "subtitle": {"size_frac": 0.038, "color": "white",
                 "font": "noto-sans-bold", "shadow": (2, 2),
                 "box": False},
    "quote": {"size_frac": 0.042, "color": "yellow",
              "font": "noto-sans-bold", "shadow": (2, 2),
              "box": True, "box_radius": 18,
              "box_color": "black", "box_opacity": 0.35},
}

# Built-in procedural SFX (v1.37.0): generated deterministically at
# render time from FFmpeg lavfi sources (see SFX_SOURCES in
# render.py). No pre-generated files, no new dependencies. "cash"
# and "chime" deliberately map to the same clean metallic ring:
# both names are kept because agents use both words for money
# figures and key numbers, and they should sound identical.
SFX_NAMES = ("pop", "whoosh", "cash", "chime", "click", "ding", "thud")

# Font files by script (searched in every font directory, in order).
# DejaVu has no Arabic glyphs, so Arabic texts previously rendered
# as missing glyphs. Noto Sans Arabic (Bold first, then Regular) is
# the default Arabic font everywhere — drawtext captions, ASS
# subtitles, and motion labels. Noto Naskh Arabic is deliberately
# NOT in this chain: this FFmpeg build's drawtext mis-shapes it,
# forming a false lam-alef ligature from a logical alef+lam
# sequence ("بالتجربة" renders as "بلا تجربة", "بالبايت" as
# "بلا بايت" — reproduced 2026-11-07 with the exact sentence,
# plain drawtext, no RLE/RLM, no wrapping; direct HarfBuzz
# shaping of the same logical string is correct in both fonts,
# and libass renders Naskh correctly, so the defect is specific
# to drawtext + Naskh on this build). Naskh stays available only
# as the explicit `font: "noto-naskh"` registry choice.
LATIN_FONT_FILES = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]
ARABIC_FONT_FILES = ["NotoSansArabic-Bold.ttf",
                     "NotoSansArabic-Regular.ttf"]

# Bundled fonts selectable by name (v1.41.0): the `font`
# option on texts/captions takes one of these keys, never a
# path, so a project stays portable across machines. Files
# are searched in the usual font dirs (bundled fonts/ first
# among the repo paths); noto-sans-bold is the Arabic DEFAULT
# face (Bold, modern, geometric) and the only Arabic face the
# default chain selects. noto-naskh is kept for explicit
# opt-in only: it is NOT drawtext-safe on this FFmpeg build
# (false lam-alef ligature, see ARABIC_FONT_FILES above), so
# no default path ever resolves to it. Licence files ship
# beside the fonts.
BUNDLED_FONTS = {"noto-naskh": "NotoNaskhArabic-Regular.ttf",
                 "noto-sans-bold": "NotoSansArabic-Bold.ttf",
                 "dejavu": "DejaVuSans.ttf"}


def find_bundled_font(name):
    """Resolve a BUNDLED_FONTS key to a file path, or None."""
    filename = BUNDLED_FONTS.get(name)
    if filename is None:
        return None
    return _find_font_file([filename])


def bundled_fonts_dir():
    """First font dir holding a bundled font file, or None.

    The render path passes this as the ass filter's fontsdir so
    libass resolves bundled families (Noto Sans Arabic / Noto
    Naskh / DejaVu) without depending on system fontconfig
    alone; system dirs still apply on top of it."""
    for d in _font_dirs():
        for filename in BUNDLED_FONTS.values():
            if os.path.isfile(os.path.join(d, filename)):
                return d
    return None


def _font_dirs():
    """Directories searched for fonts, in priority order (v1.35.0,
    on-device portability). The old code only knew absolute Linux
    paths under /usr/share/fonts, which do not exist on Android:
    Termux keeps its prefix under $PREFIX (e.g.
    /data/data/com.termux/files/usr) and ships no fonts by default.
    Order: an explicit $AGENTCUT_FONT_DIR first, then the bundled
    fonts/ directory shipped in the repo (and in render packs),
    then Termux $PREFIX font dirs, then system paths."""
    dirs = []
    env_dir = os.environ.get("AGENTCUT_FONT_DIR")
    if env_dir:
        dirs.append(env_dir)
    dirs.append(os.path.join(BASE_DIR, "fonts"))
    prefix = os.environ.get("PREFIX")
    if prefix:
        dirs.append(os.path.join(prefix, "share/fonts/truetype/dejavu"))
        dirs.append(os.path.join(prefix, "share/fonts/truetype/noto"))
        dirs.append(os.path.join(prefix, "share/fonts"))
    dirs.append("/usr/share/fonts/truetype/dejavu")
    dirs.append("/usr/share/fonts/truetype/noto")
    dirs.append("/usr/share/fonts")
    return dirs


def _find_font_file(files):
    for directory in _font_dirs():
        for name in files:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                return path
    return None


def contains_arabic(text):
    """True when text contains any Arabic Unicode character."""
    for ch in str(text):
        code = ord(ch)
        if (0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F
                or 0x08A0 <= code <= 0x08FF or 0xFB50 <= code <= 0xFDFF
                or 0xFE70 <= code <= 0xFEFF):
            return True
    return False


def find_font_for_text(text):
    """Font for one text: an Arabic font for Arabic text, else the
    Latin font. Deliberately NO fallback from Arabic to Latin (v1.35.0):
    the old code fell back to DejaVu when no Arabic font existed, and
    DejaVu has no Arabic glyphs, so Arabic text rendered as silent
    tofu boxes. Returning None here lets validation fail loudly with
    FONT_NOT_FOUND instead."""
    if contains_arabic(text):
        return _find_font_file(ARABIC_FONT_FILES)
    return find_font()


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_path(path):
    """Resolve a media path: absolute, cwd-relative, or agentcut-relative."""
    if os.path.isabs(path):
        return os.path.normpath(path)
    if os.path.isfile(path):
        return path
    candidate = os.path.normpath(os.path.join(BASE_DIR, path))
    if os.path.isfile(candidate):
        return candidate
    return path


def _load_json_data_file(path, field, errors, oid=None, fix_hint=None):
    """Load a JSON data file produced by an AgentMedia analysis
    tool (v1.44.0). resolve_path + read + json.loads, with a
    structured error (and None) on any failure. The resolved
    absolute path is returned alongside the data so the caller
    can stash it in info["data_files"] for the renderer.
    """
    if not isinstance(path, str) or not path:
        errors.append(err("INVALID_SCHEMA", f"{field} must be a non-empty file path", field=field, clip_id=oid,
                          fix=fix_hint or f"point {field} at a JSON file produced by an AgentMedia tool"))
        return None, None
    resolved = resolve_path(path)
    if not os.path.isfile(resolved):
        errors.append(err("FILE_NOT_FOUND", f"data file not found: {resolved}", field=field, clip_id=oid,
                          fix=fix_hint or "fix the path, or run the AgentMedia tool that produces this file"))
        return None, None
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append(err("INVALID_SCHEMA", f"data file is not valid JSON: {resolved} ({exc})", field=field, clip_id=oid,
                          fix="replace it with the JSON file the AgentMedia tool wrote"))
        return None, None
    if not isinstance(data, dict):
        errors.append(err("INVALID_SCHEMA", "data file must hold a JSON object: {resolved}", field=field, clip_id=oid,
                          fix="replace it with the JSON file the AgentMedia tool wrote"))
        return None, None
    return data, resolved


def _load_beats_data(spec, errors, oid=None):
    """Validate a beat_cut spec's beats file (v1.44.0, F1).
    Returns (beats_list, bpm, resolved_path) or (None, None,
    None). Beats are sorted defensively; bpm is informational
    only and never affects the cut positions.
    """
    if not isinstance(spec, dict):
        errors.append(err("INVALID_SCHEMA", "beat_cut must be an object with a beats file", field="beat_cut", clip_id=oid,
                          fix="set beat_cut to {file: 'beats.json'}"))
        return None, None, None
    data, resolved = _load_json_data_file(spec.get("file"), "beat_cut.file", errors, oid)
    if data is None:
        return None, None, None
    beats = data.get("beats")
    if not isinstance(beats, list) or len(beats) < 2:
        errors.append(err("INVALID_SCHEMA", "beats file must hold at least 2 beat times in 'beats'", field="beat_cut.file", clip_id=oid,
                          fix="run AgentMedia detect_beats on the soundtrack and use its beats.json"))
        return None, None, None
    try:
        beats = sorted(float(b) for b in beats)
    except (TypeError, ValueError):
        errors.append(err("INVALID_SCHEMA", "beats must be numbers (seconds)", field="beat_cut.file", clip_id=oid,
                          fix="use the beats.json AgentMedia detect_beats wrote"))
        return None, None, None
    bpm = data.get("bpm")
    try:
        bpm = float(bpm) if bpm is not None else None
    except (TypeError, ValueError):
        bpm = None
    return beats, bpm, resolved


def _load_track_data(spec, field, errors, oid=None):
    """Validate a follow_track spec's track file (v1.44.0, F2).
    Returns (track_dict, resolved_path) or (None, None). The
    track dict keeps the AgentMedia shape: {frame_size:
    {width, height}, track: [{t, x, y, w, h, ...}]} where x/y
    is the tracked box's TOP-LEFT corner. offset_x/offset_y
    ride in the returned dict (default 0).
    """
    if not isinstance(spec, dict):
        errors.append(err("INVALID_SCHEMA", "track must be an object with a file", field=field, clip_id=oid,
                          fix="set track to {file: 'track.json'}"))
        return None, None
    data, resolved = _load_json_data_file(spec.get("file"), field + ".file", errors, oid)
    if data is None:
        return None, None
    pts = data.get("track")
    if not isinstance(pts, list) or len(pts) < 2:
        errors.append(err("INVALID_SCHEMA", "track file must hold at least 2 points in 'track'", field=field + ".file", clip_id=oid,
                          fix="run AgentMedia track_object and use its track.json"))
        return None, None
    fs = data.get("frame_size") or {}
    try:
        fw = float(fs.get("width")); fh = float(fs.get("height"))
        if fw <= 0 or fh <= 0:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        errors.append(err("INVALID_SCHEMA", "track file must hold frame_size {width, height} (positive numbers)", field=field + ".file", clip_id=oid,
                          fix="use the track.json AgentMedia track_object wrote"))
        return None, None
    try:
        ox = float(spec.get("offset_x", 0)); oy = float(spec.get("offset_y", 0))
    except (TypeError, ValueError):
        errors.append(err("INVALID_SCHEMA", "track offset_x/offset_y must be numbers (pixels)", field=field, clip_id=oid,
                          fix="set offset_x/offset_y to pixel numbers, or remove them"))
        return None, None
    try:
        pts = sorted(({"t": float(p["t"]), "x": float(p["x"]), "y": float(p["y"]),
                       "w": float(p.get("w", 0)), "h": float(p.get("h", 0))}
                      for p in pts), key=lambda p: p["t"])
    except (TypeError, ValueError, KeyError, AttributeError):
        errors.append(err("INVALID_SCHEMA", "track points must be objects with numeric t, x, y", field=field + ".file", clip_id=oid,
                          fix="use the track.json AgentMedia track_object wrote"))
        return None, None
    # Duplicate times would divide by zero in the renderer's
    # interpolation (and cannot come from track_object, which
    # samples on a fixed grid): keep the first point per time.
    _deduped = []
    for _p in pts:
        if _deduped and _p["t"] == _deduped[-1]["t"]:
            continue
        _deduped.append(_p)
    pts = _deduped
    if len(pts) < 2:
        errors.append(err("INVALID_SCHEMA", "track file must hold at least 2 points at distinct times in 'track'", field=field + ".file", clip_id=oid,
                          fix="run AgentMedia track_object and use its track.json"))
        return None, None
    return {"frame_size": {"width": fw, "height": fh}, "track": pts,
            "offset_x": ox, "offset_y": oy}, resolved


def _load_reframe_data(spec, errors, cid=None):
    """Validate a clip reframe spec's windows file (v1.44.0, F3).
    Returns (reframe_dict, resolved_path) or (None, None).
    Windows keep the AgentMedia shape: [{t, x, y, w, h}] in
    source pixels and source seconds; crop_size (when present)
    is ignored — the windows are authoritative.
    """
    if not isinstance(spec, dict):
        errors.append(err("INVALID_SCHEMA", "reframe must be an object with windows_file and target_aspect", field="clips.reframe", clip_id=cid,
                          fix="set reframe to {windows_file: 'reframe.json', target_aspect: '9:16'}"))
        return None, None
    data, resolved = _load_json_data_file(spec.get("windows_file"), "clips.reframe.windows_file", errors, cid)
    if data is None:
        return None, None
    wins = data.get("windows")
    if not isinstance(wins, list) or len(wins) < 1:
        errors.append(err("INVALID_SCHEMA", "reframe file must hold at least 1 window in 'windows'", field="clips.reframe.windows_file", clip_id=cid,
                          fix="run AgentMedia analyze_reframe and use its reframe.json"))
        return None, None
    fs = data.get("frame_size") or {}
    try:
        fw = float(fs.get("width")); fh = float(fs.get("height"))
        if fw <= 0 or fh <= 0:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        errors.append(err("INVALID_SCHEMA", "reframe file must hold frame_size {width, height} (positive numbers)", field="clips.reframe.windows_file", clip_id=cid,
                          fix="use the reframe.json AgentMedia analyze_reframe wrote"))
        return None, None
    try:
        wins = sorted(({"t": float(w["t"]), "x": float(w["x"]), "y": float(w["y"]),
                        "w": float(w["w"]), "h": float(w["h"])} for w in wins), key=lambda w: w["t"])
    except (TypeError, ValueError, KeyError, AttributeError):
        errors.append(err("INVALID_SCHEMA", "reframe windows must be objects with numeric t, x, y, w, h", field="clips.reframe.windows_file", clip_id=cid,
                          fix="use the reframe.json AgentMedia analyze_reframe wrote"))
        return None, None
    for w in wins:
        if w["w"] <= 0 or w["h"] <= 0 or w["x"] < 0 or w["y"] < 0:
            errors.append(err("INVALID_SCHEMA", "reframe windows must have positive w/h and non-negative x/y", field="clips.reframe.windows_file", clip_id=cid,
                              fix="use the reframe.json AgentMedia analyze_reframe wrote"))
            return None, None
    return {"frame_size": {"width": fw, "height": fh}, "windows": wins}, resolved


def find_font():
    return _find_font_file(LATIN_FONT_FILES)


def err(code, message, field=None, clip_id=None, fix=None):
    """Structured error. `fix` is a concrete suggested fix for the agent.

    Older callers pass no fix; new-feature errors must pass one, so an
    agent can correct the project without guessing what went wrong.
    """
    e = {"code": code, "message": message}
    if field is not None:
        e["field"] = field
    if clip_id is not None:
        e["clip_id"] = clip_id
    if fix is not None:
        e["fix"] = fix
    return e


def probe_media(path):
    """Return ffprobe info, or None when the file cannot be probed.

    Results are cached in memory and on disk (data/.probe-cache.json)
    keyed by (resolved path, mtime_ns, size): a file whose stat
    changed is re-probed, so a stale cache can never win, and a
    missing file is never cached (it may appear later). Callers get
    a deep copy, so mutating a returned probe cannot corrupt the
    cache. The cache only avoids repeat ffprobe subprocesses; it
    never changes what probe_media returns for a given file state.
    """
    if not os.path.isfile(path):
        return None
    try:
        st = os.stat(path)
        key = (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _PROBE_CACHE_LOCK:
        if key in _PROBE_CACHE:
            return copy.deepcopy(_PROBE_CACHE[key])
        disk = _probe_cache_load()
        if key in disk:
            _PROBE_CACHE[key] = disk[key]
            return copy.deepcopy(disk[key])
    info = _probe_media_uncached(path)
    if info is not None:
        with _PROBE_CACHE_LOCK:
            _PROBE_CACHE[key] = copy.deepcopy(info)
            _probe_cache_store(key, info)
    return info


_PROBE_CACHE = {}
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", ".probe-cache.json")


def _probe_cache_load():
    try:
        with open(_PROBE_CACHE_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        out = {}
        for k, v in (raw or {}).items():
            parts = k.split("\x1f")
            if len(parts) == 3:
                out[(parts[0], int(parts[1]), int(parts[2]))] = v
        return out
    except Exception:
        return {}


def _probe_cache_store(key, info):
    # Best-effort: a cache write failure must never fail a probe.
    try:
        disk = _probe_cache_load()
        disk[key] = info
        # Bound the file: keep the newest 2000 entries.
        if len(disk) > 2000:
            disk = dict(list(disk.items())[-2000:])
        tmp = _PROBE_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"\x1f".join((k[0], str(k[1]), str(k[2]))): v
                       for k, v in disk.items()}, fh)
        os.replace(tmp, _PROBE_CACHE_FILE)
    except Exception:
        pass


def _probe_media_uncached(path):
    """The actual ffprobe subprocess (probe_media without cache)."""
    if not os.path.isfile(path):
        return None
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except Exception:
        return None
    duration = None
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    width = height = None
    has_video = has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            has_video = True
            width = stream.get("width")
            height = stream.get("height")
            if duration is None:
                try:
                    duration = float(stream.get("duration"))
                except (TypeError, ValueError):
                    pass
        elif stream.get("codec_type") == "audio":
            has_audio = True
    return {
        "path": path, "duration": duration, "width": width,
        "height": height, "has_video": has_video, "has_audio": has_audio,
        "raw": data,
    }


def probe_files_parallel(paths, max_workers=4):
    """Probe many files in parallel (ThreadPoolExecutor).

    Returns {path: probe_or_None}. Used to warm the probe cache
    before validation: validate_project calls this on every media
    path in the project, so the sequential validation below hits
    the cache instead of spawning one ffprobe per file. Warming
    never changes validation results — probe_media is
    deterministic for a given file state, and a file that changed
    since warming re-probes on its cache miss (mtime/size key).
    """
    unique = []
    seen = set()
    for p in paths or []:
        if isinstance(p, str) and p and p not in seen:
            seen.add(p)
            unique.append(p)
    if not unique:
        return {}
    workers = max(1, min(max_workers, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(probe_media, unique))
    return dict(zip(unique, results))


def sandbox_roots(project_dir=None, extra_roots=None):
    """Allowed roots for Path Sandbox Validation.

    The established resolution roots, in order: the project
    directory (when known), the current working directory, the
    AgentCut base directory, plus explicitly configured media
    directories (extra_roots, or AGENTCUT_MEDIA_DIRS,
    colon-separated). Backward compatibility wins over
    strictness: existing projects reference media across
    several workspace directories, so enforcement is opt-in
    (see check_path_sandbox); these roots are the allowed set
    when it is enabled, and AGENTCUT_MEDIA_DIRS is how an
    operator covers workspace media outside the base dir.
    """
    roots = []
    for candidate in [project_dir, os.getcwd(), BASE_DIR]:
        if candidate:
            try:
                roots.append(str(Path(candidate).resolve()))
            except OSError:
                pass
    configured = extra_roots
    if configured is None:
        configured = os.environ.get("AGENTCUT_MEDIA_DIRS", "")
    if isinstance(configured, str):
        configured = [p for p in configured.split(os.pathsep) if p]
    for candidate in configured:
        try:
            roots.append(str(Path(candidate).resolve()))
        except OSError:
            pass
    return roots


def check_path_sandbox(path, project_dir=None, extra_roots=None, roots=None):
    """True when path resolves inside an allowed sandbox root.

    Resolution uses Path.resolve(), so ".." segments and symlinks
    are normalised first: a symlink inside an allowed root that
    points outside is REJECTED, as is any ".." escape. A path
    equal to a root itself is allowed. This is a pure check; it
    does not read or write the file. Pass roots (a precomputed
    sandbox_roots() list) to skip recomputing them per call.
    """
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        return False
    allowed = roots if roots is not None else sandbox_roots(project_dir, extra_roots)
    for root in allowed:
        if resolved == root or resolved.startswith(root + os.sep):
            return True
    return False


def sandbox_enabled():
    """True only when AGENTCUT_SANDBOX=1 (opt-in enforcement).

    Default off: enforcing by default would reject existing
    valid projects whose media lives outside the base dir
    (e.g. elsewhere under ~/workspace) until the operator sets
    AGENTCUT_MEDIA_DIRS. When enabled, validate_project rejects
    outside-root media with PATH_OUTSIDE_SANDBOX (see
    validate_project)."""
    return os.environ.get("AGENTCUT_SANDBOX", "") == "1"



CHROMA_NAMES = {"green": "00FF00", "blue": "0000FF", "red": "FF0000",
                "white": "FFFFFF", "black": "000000",
                "magenta": "FF00FF", "cyan": "00FFFF", "yellow": "FFFF00"}


def _validate_chroma_mask(obj, errors, oid, field_prefix):
    """Validate chroma_key / mask fields on a clip or overlay dict.

    Returns a dict of resolved values (chroma_key_hex, mask dict) to
    merge into the resolved entry. Errors carry a fix hint.
    """
    out = {}
    ck = obj.get("chroma_key")
    if ck is not None:
        if not isinstance(ck, str):
            errors.append(err("INVALID_SCHEMA", "chroma_key must be a colour name or #RRGGBB hex", field=field_prefix + ".chroma_key", clip_id=oid,
                              fix="set chroma_key to e.g. 'green' or '#00FF00'"))
        else:
            hexv = None
            if ck in CHROMA_NAMES:
                hexv = CHROMA_NAMES[ck]
            elif re.fullmatch(r"#[0-9a-fA-F]{6}", ck):
                hexv = ck[1:].upper()
            if hexv is None:
                errors.append(err("INVALID_SCHEMA", "chroma_key must be a colour name (green, blue, red, white, black, magenta, cyan, yellow) or #RRGGBB hex", field=field_prefix + ".chroma_key", clip_id=oid,
                                  fix="set chroma_key to 'green' or a #RRGGBB hex value"))
            else:
                out["chroma_key_hex"] = hexv
        for fname, default in (("chroma_similarity", 0.3), ("chroma_blend", 0.1)):
            if fname in obj:
                try:
                    fv = float(obj[fname])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"{fname} must be a number", field=field_prefix + "." + fname, clip_id=oid,
                                      fix=f"set {fname} between 0 and 1 (default {default})"))
                    continue
                if not 0.0 <= fv <= 1.0:
                    errors.append(err("INVALID_SCHEMA", f"{fname} must be 0..1", field=field_prefix + "." + fname, clip_id=oid,
                                      fix=f"set {fname} between 0 and 1"))
                    continue
                out[fname] = fv
            else:
                out[fname] = default
    shape = obj.get("mask_shape")
    if shape is not None:
        if shape not in ("rect", "circle"):
            errors.append(err("INVALID_SCHEMA", "mask_shape must be 'rect' or 'circle'", field=field_prefix + ".mask_shape", clip_id=oid,
                              fix="set mask_shape to 'rect' (box) or 'circle'"))
        else:
            mask = {"shape": shape}
            feather = obj.get("mask_feather", 0)
            try:
                feather = int(feather)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "mask_feather must be an integer", field=field_prefix + ".mask_feather", clip_id=oid,
                                  fix="set mask_feather to 0 (hard edge) or a pixel count for a soft edge"))
                feather = None
            if feather is not None and feather < 0:
                errors.append(err("INVALID_SCHEMA", "mask_feather must be >= 0", field=field_prefix + ".mask_feather", clip_id=oid,
                                  fix="set mask_feather to 0 or more"))
                feather = None
            mask["feather"] = feather if feather is not None else 0
            if shape == "rect":
                for fname in ("mask_x", "mask_y", "mask_w", "mask_h"):
                    try:
                        mv = int(obj[fname])
                    except (KeyError, TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", f"rect mask needs integer {fname}", field=field_prefix + "." + fname, clip_id=oid,
                                          fix="set mask_x/mask_y (top-left pixels) and mask_w/mask_h (size pixels)"))
                        mv = None
                    if mv is not None and mv < 0:
                        errors.append(err("INVALID_SCHEMA", f"{fname} must be >= 0", field=field_prefix + "." + fname, clip_id=oid,
                                          fix=f"set {fname} to 0 or more"))
                        mv = None
                    mask[fname] = mv
                if mask.get("mask_w") == 0 or mask.get("mask_h") == 0:
                    errors.append(err("INVALID_SCHEMA", "rect mask width and height must be > 0", field=field_prefix + ".mask_w", clip_id=oid,
                                      fix="set mask_w and mask_h to positive pixel sizes"))
            else:
                for fname in ("mask_x", "mask_y", "mask_r"):
                    try:
                        mv = int(obj[fname])
                    except (KeyError, TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", f"circle mask needs integer {fname}", field=field_prefix + "." + fname, clip_id=oid,
                                          fix="set mask_x/mask_y (centre pixels) and mask_r (radius pixels)"))
                        mv = None
                    if mv is not None and mv < 0:
                        errors.append(err("INVALID_SCHEMA", f"{fname} must be >= 0", field=field_prefix + "." + fname, clip_id=oid,
                                          fix=f"set {fname} to 0 or more"))
                        mv = None
                    mask[fname] = mv
                if mask.get("mask_r") == 0:
                    errors.append(err("INVALID_SCHEMA", "circle mask radius must be > 0", field=field_prefix + ".mask_r", clip_id=oid,
                                      fix="set mask_r to a positive pixel radius"))
            out["mask"] = mask
    return out


def _validate_effects(obj, errors, oid, field_prefix):
    """Validate per-clip/overlay effect fields (check-only: the
    original fields pass through into the resolved entry)."""
    for fname, lo, hi in (("blur", 0.0, 20.0), ("sharpen", 0.0, 5.0),
                          ("hue", -180.0, 180.0)):
        if fname in obj:
            try:
                fv = float(obj[fname])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", f"{fname} must be a number", field=field_prefix + "." + fname, clip_id=oid,
                                  fix=f"set {fname} to a number between {lo} and {hi}, or remove it"))
                continue
            if not lo <= fv <= hi:
                errors.append(err("INVALID_SCHEMA", f"{fname} must be {lo}..{hi}", field=field_prefix + "." + fname, clip_id=oid,
                                  fix=f"set {fname} between {lo} and {hi}"))
    if "pixelize" in obj:
        pv = obj["pixelize"]
        if isinstance(pv, bool) or not isinstance(pv, int) or not 2 <= pv <= 64:
            errors.append(err("INVALID_SCHEMA", "pixelize must be an integer block size 2..64", field=field_prefix + ".pixelize", clip_id=oid,
                              fix="set pixelize to a whole number between 2 and 64, or remove it"))
    if "vignette" in obj and not isinstance(obj["vignette"], bool):
        errors.append(err("INVALID_SCHEMA", "vignette must be true or false", field=field_prefix + ".vignette", clip_id=oid,
                          fix="set vignette to true for a darkened-corner effect, or remove it"))


def _validate_sfx(obj, errors, oid, field_prefix):
    """Validate an element's sfx / sfx_volume fields (v1.37.0).

    sfx is a built-in name (SFX_NAMES) or a local audio file path
    (resolved like any media path; it must exist and probe with an
    audio stream). sfx_volume is 0..2, default 0.35 at render time.
    An unknown name is a structured error listing the valid names,
    never a silent fallback to another sound."""
    sfx = obj.get("sfx")
    if sfx is not None:
        if not isinstance(sfx, str) or not sfx:
            errors.append(err("INVALID_SCHEMA", "sfx must be a built-in name or a local audio file path", field=field_prefix + ".sfx", clip_id=oid,
                              fix="set sfx to one of: " + ", ".join(SFX_NAMES) + ", or to a local audio file path"))
        elif sfx not in SFX_NAMES:
            resolved_sfx = resolve_path(sfx)
            if not os.path.isfile(resolved_sfx):
                errors.append(err("INVALID_SCHEMA", "unknown sfx '" + sfx + "'; valid built-in names are: " + ", ".join(SFX_NAMES) + " (or a local audio file path)", field=field_prefix + ".sfx", clip_id=oid,
                                  fix="set sfx to one of: " + ", ".join(SFX_NAMES) + ", or fix the audio file path"))
            else:
                _sp = probe_media(resolved_sfx)
                if _sp is None or not _sp.get("has_audio"):
                    errors.append(err("INVALID_SCHEMA", f"sfx file has no audio stream: {resolved_sfx}", field=field_prefix + ".sfx", clip_id=oid,
                                      fix="use an audio file for sfx, or a built-in name: " + ", ".join(SFX_NAMES)))
    if "sfx_volume" in obj:
        try:
            _sv = float(obj["sfx_volume"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "sfx_volume must be a number", field=field_prefix + ".sfx_volume", clip_id=oid,
                              fix="set sfx_volume between 0 (silent) and 2, or remove it for the 0.35 default"))
            _sv = None
        if _sv is not None and not 0.0 <= _sv <= 2.0:
            errors.append(err("INVALID_SCHEMA", "sfx_volume must be 0..2", field=field_prefix + ".sfx_volume", clip_id=oid,
                              fix="set sfx_volume between 0 (silent) and 2, e.g. 0.35"))


def _validate_punch_in(clip, duration, errors, cid, resolved_entry):
    """Validate a clip's punch_in field (v1.37.0) and resolve it
    onto the resolved clip entry as punch_at / punch_factor.

    punch_in is true (mid-clip, 1.20x) or {"at": seconds into the
    clip, "factor": 1.18..1.25}. "at" is in rendered-clip seconds
    (after trim/speed), strictly inside the clip, so the punch is
    a frame-boundary scale jump with content on both sides and
    no gap. Out-of-range values are structured errors."""
    punch = clip.get("punch_in")
    if punch is None:
        return
    if punch is True:
        punch_at, factor = duration / 2.0, 1.20
    elif isinstance(punch, dict):
        try:
            punch_at = float(punch.get("at", duration / 2.0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "punch_in at must be a number", field="clips.punch_in", clip_id=cid,
                              fix=f"set punch_in at to seconds into the clip, between 0 and {duration:.2f}, or omit it for mid-clip"))
            return
        try:
            factor = float(punch.get("factor", 1.20))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "punch_in factor must be a number", field="clips.punch_in", clip_id=cid,
                              fix="set punch_in factor between 1.18 and 1.25, e.g. 1.20"))
            return
    else:
        errors.append(err("INVALID_SCHEMA", "punch_in must be true or an object {at, factor}", field="clips.punch_in", clip_id=cid,
                          fix="set punch_in to true for a mid-clip 1.20x punch, or to e.g. {\"at\": 2, \"factor\": 1.22}"))
        return
    if not 0 < punch_at < duration:
        errors.append(err("CLIP_OUT_OF_RANGE", f"punch_in at {punch_at}s outside clip duration {duration:.2f}s", field="clips.punch_in", clip_id=cid,
                          fix=f"set punch_in at between 0 and {duration:.2f}"))
        return
    if not 1.18 <= factor <= 1.25:
        errors.append(err("INVALID_SCHEMA", "punch_in factor must be 1.18..1.25", field="clips.punch_in", clip_id=cid,
                          fix="set punch_in factor between 1.18 and 1.25, e.g. 1.20"))
        return
    resolved_entry["punch_at"] = punch_at
    resolved_entry["punch_factor"] = factor


def _validate_camera(clip, duration, errors, cid):
    """Validate a clip's camera path (v1.40.0).

    camera is a non-empty list of {t, x, y, scale} keyframes in
    rendered-clip seconds (after trim/speed, like punch_at):
    t strictly increasing within the clip duration, x/y centre
    fractions 0..1, scale 1.0..1.25 (the 1.37.2 comfort max, by
    construction — a camera path can never exceed it). One
    keyframe is a static framing, not an error. A camera path is
    a glide, so it cannot combine with punch_in (an absolute
    scale jump that would stack on the glide), with blurred
    fits (whose two-stream chain cannot also zoom), with
    stabilize (which needs the original frame order), or with
    speed_curve (whose segments have their own concat). An
    explicit ken_burns is NOT rejected: camera wins over it,
    with a warnings-only note (see validate_project), exactly
    like punch winning over ken_burns. Render applies the path
    through zoompan with smoothstep interpolation; the
    zoom-dependent clamp in render keeps every crop in range
    even for centre fractions at the edges."""
    cam = clip.get("camera")
    if cam is None:
        return
    if not isinstance(cam, list) or not cam:
        errors.append(err("INVALID_SCHEMA", "camera must be a non-empty list of {t, x, y, scale} keyframes", field="clips.camera", clip_id=cid,
                          fix="give camera as e.g. [{\"t\": 0, \"x\": 0.5, \"y\": 0.5, \"scale\": 1.0}, {\"t\": 2.0, \"x\": 0.7, \"y\": 0.5, \"scale\": 1.2}]"))
        return
    prev_t = None
    for ki, kf in enumerate(cam):
        if not isinstance(kf, dict):
            errors.append(err("INVALID_SCHEMA", "each camera keyframe must be an object", field="clips.camera", clip_id=cid,
                              fix="make each keyframe an object with t, x, y and scale"))
            return
        try:
            t = float(kf["t"])
            x = float(kf["x"])
            y = float(kf["y"])
            s = float(kf["scale"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "each camera keyframe needs numeric t, x, y and scale", field="clips.camera", clip_id=cid,
                              fix="set t to clip seconds, x/y to centre fractions 0..1, scale to 1.0..1.25"))
            return
        if t < 0 or t > duration + 0.001:
            errors.append(err("CLIP_OUT_OF_RANGE", f"camera keyframe {ki} time {t}s outside clip duration {duration:.2f}s", field="clips.camera", clip_id=cid,
                              fix=f"keep camera times between 0 and {duration:.2f}"))
            return
        if prev_t is not None and t <= prev_t:
            errors.append(err("INVALID_SCHEMA", "camera keyframe times must strictly increase", field="clips.camera", clip_id=cid,
                              fix="sort camera keyframes by time with no duplicates"))
            return
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            errors.append(err("INVALID_SCHEMA", f"camera keyframe {ki} x/y must be centre fractions 0..1", field="clips.camera", clip_id=cid,
                              fix="set x/y to the crop centre as a fraction of the frame, e.g. 0.5 for centre"))
            return
        if not 1.0 <= s <= 1.25:
            errors.append(err("INVALID_SCHEMA", f"camera keyframe {ki} scale must be 1.0..1.25 (the 1.37.2 comfort max)", field="clips.camera", clip_id=cid,
                              fix="set scale between 1.0 (full frame) and 1.25 (closest zoom)"))
            return
        prev_t = t
    if clip.get("punch_at") is not None or clip.get("punch_in") is not None:
        errors.append(err("INVALID_SCHEMA", "camera cannot combine with punch_in (punch is an absolute scale jump; a glide plus a jump would break the 1.25x max)", field="clips.camera", clip_id=cid,
                          fix="remove punch_in, or remove camera, from this clip"))
    if clip.get("fit") in BLUR_FITS:
        errors.append(err("INVALID_SCHEMA", "camera cannot combine with fit blur (the blurred-background chain cannot also zoom)", field="clips.camera", clip_id=cid,
                          fix="use camera or blurred-background fit, not both, on one clip"))
    if clip.get("stabilize"):
        errors.append(err("INVALID_SCHEMA", "camera cannot combine with stabilize (stabilisation needs the clip's original frame order and timing)", field="clips.camera", clip_id=cid,
                          fix="use camera or stabilize, not both, on one clip"))
    if clip.get("speed_curve") is not None:
        errors.append(err("INVALID_SCHEMA", "camera cannot combine with speed_curve (the curve's segments have their own concat)", field="clips.camera", clip_id=cid,
                          fix="use camera or speed_curve, not both, on one clip"))


AUTO_SUBCUT_CYCLE = (
    # Waypoints after the opening wide framing, cycled in
    # order (x, y, scale): zoomed-in centre, pan one way,
    # pan the other, pull back to wide. The pan direction
    # flips per clip index so neighbouring scenes do not
    # mirror each other exactly.
    (0.5, 0.5, 1.18),
    (0.58, 0.5, 1.12),
    (0.42, 0.5, 1.12),
    (0.5, 0.5, 1.0),
)


def _generate_auto_subcuts(resolved, project):
    """Auto sub-cuts / multi-shot camera pacing (v1.42.0, Fix 4b).

    Still-image clips longer than 12s with no explicit camera,
    no punch_in and no explicit ken_burns get a generated
    multi-stop camera path that cycles camera angles every
    ~10s (wide slow zoom-in -> medium pan right/left ->
    center pull-back, scale 1.0..1.18, continuous visible
    motion) instead of one imperceptible minute-long creep.
    The path is waypoints, not independent segments: each
    glide ends exactly where the next begins, so there is no
    jump at a sub-cut boundary. It runs BEFORE the motion
    continuity pass, so same-scene clips chain their scales
    through it. Opt-outs: project auto_subcuts: false, or
    per-clip auto_subcuts: false; explicit camera, punch_in
    or ken_burns always win over generation, and blurred
    fits never get a path (camera cannot combine with them).
    Default is ON: a long still that used to sit frozen now
    moves — the same default-on precedent as 1.37.1's
    default_image_motion. Returns the ids of clips that got
    a generated path (for info["auto_subcuts"]).
    """
    if project.get("auto_subcuts") is False:
        return []
    generated = []
    for ci, clip in enumerate(resolved):
        if clip.get("kind") != "image":
            continue
        if clip.get("auto_subcuts") is False:
            continue
        duration = float(clip["duration"])
        if duration <= 12:
            continue
        if clip.get("camera") or clip.get("punch_at") is not None:
            continue
        if clip.get("ken_burns", "none") != "none":
            continue
        if clip.get("fit") in BLUR_FITS:
            continue
        n = max(1, round(duration / 10.0))
        seg = duration / n
        cycle = list(AUTO_SUBCUT_CYCLE)
        if ci % 2 == 1:
            cycle[1], cycle[2] = cycle[2], cycle[1]
        keyframes = [{"t": 0.0, "x": 0.5, "y": 0.5, "scale": 1.0}]
        for k in range(1, n + 1):
            x, y, s = cycle[(k - 1) % len(cycle)]
            keyframes.append({"t": round(k * seg, 3), "x": x, "y": y,
                              "scale": s})
        clip["camera"] = keyframes
        clip["_auto_subcuts"] = True
        generated.append(clip.get("id"))
    return generated


def _validate_treatment(obj, errors, oid, field_prefix):
    """Validate a treatment dict (v1.40.0), clip-level or
    project-level (field_prefix "" for the project).

    treatment is {grain, vignette, shadow}, all optional, all
    default off: grain 0..30 (noise strength), vignette 0..1
    (corner darkening strength), shadow 0..1 (soft inner edge
    shadow strength). Unknown keys are rejected: a mistyped key
    must not silently render untreated."""
    tr = obj.get("treatment")
    if tr is None:
        return
    field = f"{field_prefix}.treatment" if field_prefix else "treatment"
    if not isinstance(tr, dict):
        errors.append(err("INVALID_SCHEMA", "treatment must be an object {grain, vignette, shadow}", field=field, clip_id=oid,
                          fix="set treatment to e.g. {\"grain\": 8, \"vignette\": 0.3}, or remove it"))
        return
    for key in tr:
        if key not in ("grain", "vignette", "shadow"):
            errors.append(err("INVALID_SCHEMA", f"unknown treatment key '{key}' (known: grain, vignette, shadow)", field=field, clip_id=oid,
                              fix="remove the unknown key, or use grain, vignette or shadow"))
    for fname, lo, hi in (("grain", 0.0, 30.0), ("vignette", 0.0, 1.0), ("shadow", 0.0, 1.0)):
        if fname in tr:
            try:
                fv = float(tr[fname])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", f"treatment {fname} must be a number", field=field, clip_id=oid,
                                  fix=f"set {fname} between {lo} and {hi}"))
                continue
            if not lo <= fv <= hi:
                errors.append(err("INVALID_SCHEMA", f"treatment {fname} must be {lo}..{hi}", field=field, clip_id=oid,
                                  fix=f"set {fname} between {lo} and {hi}"))


def effective_ken_burns(clip, index, project):
    """Ken Burns motion actually applied to a clip (v1.37.2).

    Punch-in wins over Ken Burns, always: a clip with punch_in
    (raw field) or a resolved punch_at never also zooms, because
    stacking a punch multiplier on a zooming clip blew the scale
    up to 1.8x in the 1.37.0 rhythm proof and read as vibration
    and grotesque zoom. A punched clip is static until its punch
    time, then holds its punch factor — punch IS that clip's
    motion. Otherwise, an explicit ken_burns wins; and when the
    project sets default_image_motion, image clips (except
    blurred-background ones, whose blur chain cannot also zoom)
    get smooth alternating motion by clip index: even indices
    zoom_in, odd indices zoom_out. Video clips never get default
    motion. Returns the motion name or "none"."""
    if clip.get("punch_in") is not None or clip.get("punch_at") is not None:
        return "none"
    kb = clip.get("ken_burns", "none")
    if kb not in (None, "none"):
        return kb
    if not (project or {}).get("default_image_motion"):
        return "none"
    if clip.get("kind") != "image" or clip.get("fit") in BLUR_FITS:
        return "none"
    return "zoom_in" if index % 2 == 0 else "zoom_out"


def chunk_word_timings(word_timings):
    """Group word timings into active-caption chunks (v1.37.0).

    Chunks hold 2–4 words: greedy groups of 3, with the tail
    rebalanced so no chunk is a single word (4 left -> 2+2, 1 left
    after a full group -> merged into the previous group as 4,
    unless it is the only chunk). Deterministic, so validation
    warnings and rendering agree on chunk boundaries."""
    words = list(word_timings)
    if not words:
        return []
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 3:
            chunks.append(cur)
            cur = []
    if cur:
        if len(cur) == 1 and chunks:
            chunks[-1] = chunks[-1] + cur
        elif len(cur) == 1 and not chunks:
            chunks.append(cur)
        else:
            chunks.append(cur)
    if len(chunks) >= 2 and len(chunks[-1]) == 1:
        chunks[-2] = chunks[-2] + chunks.pop()
    # A 4-word tail from the merge above is legal (2–4); a chunk
    # of 4 split as 2+2 only matters when the tail was exactly 4
    # before merging, which the greedy pass already handles as
    # 3+1 -> merged to 4. Nothing further to rebalance.
    return chunks


def _validate_word_timings(item, start, end, errors, label):
    """Validate a text/caption's word_timings field (v1.37.0
    captions, v1.40.0 generalized to texts as kinetic text).

    word_timings is the agent-supplied per-word timing list from
    transcription (AgentCut computes nothing AI-side): a non-empty
    list of {word, start, end} in absolute timeline seconds, in
    order, non-overlapping, inside the text window, whose words
    joined by spaces equal the text. A mismatch is a structured
    error: a mistimed or mistyped word list must not silently
    highlight the wrong words. Kinetic texts additionally accept
    emphasis (a pop on the current word) and emphasis_factor
    (its size multiplier, 1.0..1.5, default 1.25 when emphasis is
    true, 1.0 otherwise); emphasis without word_timings is an
    error, since there is no current word to pop."""
    wt = item.get("word_timings")
    if wt is None:
        if item.get("emphasis") is not None or item.get("emphasis_factor") is not None:
            errors.append(err("INVALID_SCHEMA", "emphasis needs word_timings (there is no current word to pop without timings)", field=label + ".emphasis",
                              fix="add word_timings to this text, or remove emphasis/emphasis_factor"))
        return
    if not isinstance(wt, list) or not wt:
        errors.append(err("INVALID_SCHEMA", "word_timings must be a non-empty list of {word, start, end} objects", field=label + ".word_timings",
                          fix="give one {word, start, end} per word of the text, in order"))
        return
    words = []
    prev_end = None
    for wi, w in enumerate(wt):
        if not isinstance(w, dict) or not isinstance(w.get("word"), str) or not w["word"].strip():
            errors.append(err("INVALID_SCHEMA", "each word_timings entry needs a non-empty word string", field=label + ".word_timings",
                              fix="give each entry as e.g. {\"word\": \"hello\", \"start\": 1.0, \"end\": 1.4}"))
            return
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "each word_timings entry needs numeric start and end", field=label + ".word_timings",
                              fix="set start/end to timeline seconds inside the caption window"))
            return
        if we <= ws:
            errors.append(err("INVALID_SCHEMA", f"word_timings word {wi} needs end after start", field=label + ".word_timings",
                              fix="set end after start for every word"))
            return
        if ws < start - 0.05 or we > end + 0.05:
            errors.append(err("CLIP_OUT_OF_RANGE", f"word_timings word {wi} ({ws}..{we}s) outside caption window {start}..{end}s", field=label + ".word_timings",
                              fix=f"keep word times inside {start}..{end}"))
            return
        if prev_end is not None and ws < prev_end - 0.001:
            errors.append(err("INVALID_SCHEMA", "word_timings entries must not overlap and must be in order", field=label + ".word_timings",
                              fix="sort word timings by start with no overlaps"))
            return
        prev_end = we
        words.append(w["word"])
    if words != str(item["text"]).split():
        errors.append(err("INVALID_SCHEMA", f"word_timings words {words} do not match text words {str(item['text']).split()} (one timing per word, in order)", field=label + ".word_timings",
                          fix="give exactly one timing per text word, in the same order and spelling"))
    hc = item.get("highlight_color", "yellow")
    if not (isinstance(hc, str) and (hc in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", hc))):
        errors.append(err("INVALID_SCHEMA", "highlight_color must be a #RRGGBB hex value or a named colour", field=label + ".highlight_color",
                          fix="set highlight_color to a named colour or #RRGGBB, e.g. yellow"))
    hm = item.get("highlight_mode", "word")
    if hm not in ("word", "chunk"):
        errors.append(err("INVALID_SCHEMA", "highlight_mode must be 'word' or 'chunk'", field=label + ".highlight_mode",
                          fix="set highlight_mode to 'word' (per-word highlight) or 'chunk' (whole active chunk highlighted)"))
    if item.get("emphasis") is not None and not isinstance(item["emphasis"], bool):
        errors.append(err("INVALID_SCHEMA", "emphasis must be true or false", field=label + ".emphasis",
                          fix="set emphasis to true for a pop on the current word, or remove it"))
    if item.get("emphasis_factor") is not None:
        try:
            _ef = float(item["emphasis_factor"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "emphasis_factor must be a number", field=label + ".emphasis_factor",
                              fix="set emphasis_factor between 1.0 (no pop) and 1.5, e.g. 1.25"))
            _ef = None
        if _ef is not None and not 1.0 <= _ef <= 1.5:
            errors.append(err("INVALID_SCHEMA", "emphasis_factor must be 1.0..1.5", field=label + ".emphasis_factor",
                              fix="set emphasis_factor between 1.0 (no pop) and 1.5"))
    if item.get("reveal") is not None or item.get("animation") is not None:
        errors.append(err("INVALID_SCHEMA", "word_timings cannot combine with reveal or animation", field=label + ".word_timings",
                          fix="remove reveal/animation from this text; the word timings drive its display"))
    if float(item.get("fade_in", 0) or 0) > 0 or float(item.get("fade_out", 0) or 0) > 0:
        errors.append(err("INVALID_SCHEMA", "word_timings cannot combine with fade_in/fade_out", field=label + ".word_timings",
                          fix="remove fade_in/fade_out from this text; the word timings drive its display"))


def wrap_caption_lines(text, size, max_width_px, font_path=None):
    """Word-wrap a caption's LOGICAL text into lines fitting
    max_width_px (v1.36.0, the headline caption fix).

    Delegates to srt.wrap_lines — the single source of truth for
    wrapping (refactoring: never duplicate the algorithm). See
    srt.wrap_lines for the greedy word-packing rules (words never
    split, Arabic words stay whole and joined, BiDi per line at
    render time). font_path (v1.46.1, R1): when given, lines are
    measured with HarfBuzz on that font, so the wrap packs what
    the renderer lays out; when None, the relative-weight table
    estimates — which underestimates Noto Sans Arabic Bold by
    ~30%, so pass the render font whenever it is known. Returns
    at least one line.
    """
    from .srt import measure_text_width, wrap_lines
    if font_path:
        _cache = {}

        def _measure(word, size=size, _c=_cache, _f=font_path):
            return measure_text_width(word, _f, size, _c)
        return wrap_lines(text, size, max_width_px, measure=_measure)
    return wrap_lines(text, size, max_width_px)


def _text_box(item, is_caption, width, height, total,
              line_spacing=1.35, max_width_frac=0.9, zone_clamp=False):
    """Approximate on-screen box (x, y, w, h) for a text/caption item.

    APPROXIMATION, documented honestly: the true drawtext box
    depends on the font's glyph advances, which validation does not
    measure. Caption width is the widest wrapped line measured
    with the same HarfBuzz measure the wrapper uses (v1.46.1, R1 —
    so the box can never exceed the wrap width it was wrapped at);
    non-caption text width keeps the flat
    len(text) x size x 0.6 overestimate (safe direction for
    collision warnings and pill backdrops). Height is size x
    1.4; positions mirror render.py (numeric x/y, the POS_MAP
    presets, centred texts, captions at height-120 by default).
    Captions are wrapped (v1.36.0): the box spans the wrapped
    block — width is the widest line, height covers all lines at
    line_spacing — so collision checks see the real wrapped
    block, not the old single-line box. The check is a warning,
    never an error, precisely because these boxes are estimates.
    zone_clamp (v1.42.0, Fix 3): when True, a no-position
    caption's block top is clamped so the block bottom stays
    inside the subtitle zone (<= 0.92*height) — the same clamp
    render.py applies to the real caption anchor, so the
    estimated box and the rendered caption agree.
    """
    from .srt import _word_width, measure_text_width
    text = str(item.get("text", ""))
    size = float(item.get("size", 36 if is_caption else 48))
    # v1.46.1, R1: measure with the real render font, so the box
    # matches the wrapped lines the renderer lays out.
    _cap_font = find_font_for_text(text) if is_caption else None
    cap_lines = wrap_caption_lines(text, size, width * max_width_frac,
                                   font_path=_cap_font) \
        if is_caption else [text]
    if is_caption:
        if _cap_font:
            est_w = max(10.0, max(measure_text_width(ln, _cap_font, size)
                                  for ln in cap_lines))
        else:
            est_w = max(10.0, max(_word_width(ln, size) for ln in cap_lines))
        if len(cap_lines) > 1:
            est_h = (len(cap_lines) - 1) * size * line_spacing + size * 1.4
        else:
            est_h = size * 1.4
    else:
        if "\n" in text:
            lines = text.split("\n")
            _font = find_bundled_font(item["font"]) if item.get("font") else find_font_for_text(text)
            if _font:
                est_w = max(10.0, max(measure_text_width(ln, _font, size) for ln in lines))
            else:
                est_w = max(10.0, max(len(ln) * size * 0.6 for ln in lines))
            est_h = (len(lines) - 1) * size * line_spacing + size * 1.4
        else:
            est_w = max(10.0, len(text) * size * 0.6)
            est_h = size * 1.4
    pos = item.get("position")
    if pos:
        presets = {
            "top_left": (40.0, 40.0),
            "top_center": ((width - est_w) / 2, 40.0),
            "top_right": (width - est_w - 40.0, 40.0),
            "center": ((width - est_w) / 2, (height - est_h) / 2),
            "bottom_left": (40.0, height - est_h - 40.0),
            "bottom_center": ((width - est_w) / 2, height - est_h - 40.0),
            "bottom_right": (width - est_w - 40.0, height - est_h - 40.0),
        }
        x, y = presets[pos]
    elif is_caption:
        # Wrapped block, stacked upward: the LAST line keeps the
        # old single-line top (height-120), earlier lines rise
        # above it — exactly like render.py. With zone_clamp the
        # whole block is pulled up so its bottom stays inside
        # the subtitle zone (0.92*height).
        x, y = (width - est_w) / 2, float(
            height - 120 - (len(cap_lines) - 1) * size * line_spacing)
        if zone_clamp:
            y = min(y, 0.92 * height - est_h)
    else:
        xv = item.get("x", "center")
        x = (width - est_w) / 2 if xv == "center" else float(xv)
        y = float(item.get("y", 100))
    return (x, y, est_w, est_h)


def _overlay_box_at(ov, t, width, height):
    """Approximate overlay box at timeline time t (linear keyframe
    interpolation; easing ignored — another documented
    approximation). Returns (x, y, w, h) or None for invisible
    overlays (opacity 0 at that time)."""
    kfs = ov.get("keyframes_resolved")
    if kfs:
        rel = t - float(ov["start"])
        # Render semantics (render.py): when keyframe scales do NOT
        # vary, the overlay is scaled by its base size and the
        # keyframe scale values are ignored; only a varying scale
        # drives the per-frame width. Mirror that here, or a
        # constant scale=1.0 keyframe would fake a full-width box.
        varying_scale = len({float(f["scale"]) for f in kfs}) > 1
        if rel <= kfs[0]["time"]:
            x, y, scale = float(kfs[0]["x"]), float(kfs[0]["y"]), float(kfs[0]["scale"])
            opacity = float(kfs[0]["opacity"])
        elif rel >= kfs[-1]["time"]:
            x, y, scale = float(kfs[-1]["x"]), float(kfs[-1]["y"]), float(kfs[-1]["scale"])
            opacity = float(kfs[-1]["opacity"])
        else:
            x = y = scale = opacity = None
            for a, b in zip(kfs, kfs[1:]):
                if a["time"] <= rel <= b["time"]:
                    f = (rel - a["time"]) / (b["time"] - a["time"]) if b["time"] > a["time"] else 0.0
                    x = float(a["x"]) + (float(b["x"]) - float(a["x"])) * f
                    y = float(a["y"]) + (float(b["y"]) - float(a["y"])) * f
                    scale = float(a["scale"]) + (float(b["scale"]) - float(a["scale"])) * f
                    opacity = float(a["opacity"]) + (float(b["opacity"]) - float(a["opacity"])) * f
                    break
        if opacity == 0:
            return None
        if not varying_scale:
            scale = float(ov.get("size", 0.35))
    else:
        scale = float(ov.get("size", 0.35))
        if float(ov.get("opacity", 1.0)) == 0:
            return None
        probe = ov.get("probe") or {}
        pw, ph = probe.get("width") or width, probe.get("height") or height
        bw = scale * width
        bh = bw * (ph / pw) if pw else bw
        presets = {
            "top_left": (20.0, 20.0),
            "top_center": ((width - bw) / 2, 20.0),
            "top_right": (width - bw - 20.0, 20.0),
            "center": ((width - bw) / 2, (height - bh) / 2),
            "bottom_left": (20.0, height - bh - 20.0),
            "bottom_center": ((width - bw) / 2, height - bh - 20.0),
            "bottom_right": (width - bw - 20.0, height - bh - 20.0),
        }
        # Per-axis, exactly like render.py: an x-only (or y-only)
        # overlay keeps the preset value on the other axis. The
        # old code required BOTH x and y for explicit placement,
        # so an x-only overlay's estimated box was the preset box
        # and collisions could be missed or misreported (1.34.0).
        px, py = presets[ov.get("position", "bottom_right")]
        x = float(ov["x"]) if "x" in ov else px
        y = float(ov["y"]) if "y" in ov else py
        return (x, y, bw, bh)
    probe = ov.get("probe") or {}
    pw, ph = probe.get("width") or width, probe.get("height") or height
    bw = scale * width
    bh = bw * (ph / pw) if pw else bw
    return (x, y, bw, bh)


def _boxes_overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and \
        a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def check_layer_text_overlap(project, info):
    """Geometric overlay-vs-text collision warnings (v1.33.0).

    Primary place for this check: validate.py, surfaced in
    info["warnings"] (and therefore in the validate_project MCP
    tool's info) and merged into produce_video's QA warnings.
    Warnings NEVER block rendering or validation: they are
    advisory only. One warning per (overlay, text) pair, naming
    the overlay id, the overlapping text and the colliding time
    range (sampled every 0.5s plus keyframe times and window
    boundaries, so a gliding overlay that only crosses a text
    briefly is still caught, and the range is the sampled
    collision range, not the whole window).
    """
    width, height, total = info["width"], info["height"], info["total_duration"]
    # The zone manager (v1.42.0, Fix 3) shares this flag: when
    # resolve_overlaps is on, caption boxes use the zone-clamped
    # anchor, matching the rendered caption position.
    zone_clamp = project.get("resolve_overlaps") is True
    texts = []
    pop_entries = []
    for _pi, item in enumerate(project.get("texts") or []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            _box = list(_text_box(item, False, width, height, total))
            texts.append((item["text"], float(item.get("start", 0)),
                          float(item.get("end", total)), _box))
            if (item.get("sfx") == "pop"
                    and isinstance(item.get("y"), (int, float))
                    and "position" not in item):
                pop_entries.append({
                    "box": _box, "start": float(item.get("start", 0)),
                    "end": float(item.get("end", total)), "index": _pi,
                    "text": item["text"],
                    "orig_y": float(item["y"])})
    cap_spacing = project.get("caption_line_spacing", 1.35)
    cap_maxw = project.get("caption_max_width", 0.8)
    try:
        cap_spacing = float(cap_spacing)
    except (TypeError, ValueError):
        cap_spacing = 1.35
    try:
        cap_maxw = float(cap_maxw)
    except (TypeError, ValueError):
        cap_maxw = 0.9
    for cap in (info.get("captions") or []):
        if isinstance(cap, dict) and isinstance(cap.get("text"), str):
            texts.append((cap["text"], float(cap.get("start", 0)),
                          float(cap.get("end", total)),
                          _text_box(cap, True, width, height, total,
                                    line_spacing=cap_spacing,
                                    max_width_frac=cap_maxw,
                                    zone_clamp=zone_clamp)))
    # Layers above the clips: overlays plus stroke diagrams
    # (v1.38.0). A diagram's box is its placement box, computed
    # with the same per-axis preset logic as overlays (its
    # "probe" size is its canvas size); the box is static, so
    # no keyframe sampling applies to diagrams.
    layers = []
    for ov in (info.get("overlays") or []):
        layers.append(("overlay", ov["id"], float(ov["start"]),
                       float(ov["start"]) + float(ov["duration"]), ov))
    for dg in (info.get("diagrams") or []):
        synthetic = {"size": float(dg["size"]),
                     "position": dg.get("position", "bottom_right"),
                     "probe": {"width": dg["canvas_w"],
                               "height": dg["canvas_h"]}}
        for axis in ("x", "y"):
            if axis in dg:
                synthetic[axis] = dg[axis]
        layers.append(("diagram", dg["id"], float(dg["start"]),
                       float(dg["end"]), synthetic))
    # Collision-proof layout (v1.41.0): when the project opts in
    # with resolve_overlaps: true, keyword pops (texts with
    # sfx "pop" and an explicit numeric y) that overlap any
    # other text, caption or diagram box in a shared time
    # window cascade DOWNWARD — new y = bottom of the
    # lowest overlapped box + 16px, repeated until clear —
    # generalizing the AI-race scene-4 fix (a pop over a
    # diagram label moved below the whole diagram). The move
    # is recorded in the returned moves list and applied by
    # the renderer; it never pushes a pop off the frame (a
    # pop that cannot clear inside the frame keeps its
    # original y and the overlap is warned about instead).
    # Pops with a position preset are warned, not moved.
    # Resolution runs BEFORE the warning passes, so a
    # resolved project reports ZERO overlap warnings and
    # the moves are visible separately.
    moves = []
    if project.get("resolve_overlaps") is True and pop_entries:
        _diag_boxes = []
        for _kind, _lid, _ds, _de, _syn in layers:
            if _kind != "diagram":
                continue
            _db = _overlay_box_at(_syn, _ds, width, height)
            if _db is not None:
                _diag_boxes.append((_db, _ds, _de))
        for _pe in pop_entries:
            _box, _orig_y = _pe["box"], _pe["orig_y"]
            for _ in range(20):
                _blocked = []
                for _t, _s, _e, _b in texts:
                    if _b is _box:
                        continue
                    if _e <= _pe["start"] or _s >= _pe["end"]:
                        continue
                    if _boxes_overlap(_box, _b):
                        _blocked.append(_b)
                for _b, _s, _e in _diag_boxes:
                    if _e <= _pe["start"] or _s >= _pe["end"]:
                        continue
                    if _boxes_overlap(_box, _b):
                        _blocked.append(_b)
                if not _blocked:
                    break
                _new_y = max(_b[1] + _b[3] for _b in _blocked) + 16
                if _new_y + _box[3] > height:
                    _box[1] = _orig_y
                    break
                _box[1] = _new_y
            if _box[1] != _orig_y:
                moves.append({"index": _pe["index"],
                              "text": _pe["text"][:40],
                              "old_y": _orig_y, "new_y": _box[1]})
    warnings = []
    # Safe-area check (v1.40.0, warning only): a text or caption
    # whose estimated box crosses the 5% action-safe rect may
    # clip on some displays. Estimates only (see _text_box), so
    # this never errors or clamps — default positions already
    # sit inside action-safe, so a quiet project stays quiet.
    _mx, _my = width * 0.05, height * 0.05
    for _txt, _s, _e, (_bx, _by, _bw, _bh) in texts:
        if (_bx < _mx or _by < _my or _bx + _bw > width - _mx
                or _by + _bh > height - _my):
            warnings.append({
                "code": "text_outside_safe_area",
                "message": (f"text {str(_txt)[:40]!r} crosses the 5% action-safe area "
                            f"(estimated box {_bx:.0f},{_by:.0f} {_bw:.0f}x{_bh:.0f} on "
                            f"{width}x{height}) — move it inside, or accept the possible clipping"),
                "text": str(_txt)[:40]})
    for kind, lid, o_start, o_end, ov in layers:
        for text, t_start, t_end, tbox in texts:
            w_start, w_end = max(o_start, t_start), min(o_end, t_end)
            if w_end <= w_start:
                continue
            samples = {w_start, w_end}
            t = w_start
            while t < w_end:
                samples.add(round(t, 3))
                t += 0.5
            for kf in (ov.get("keyframes_resolved") or []):
                at = o_start + float(kf["time"])
                if w_start <= at <= w_end:
                    samples.add(round(at, 3))
            samples.add(round(t_start, 3)) if w_start <= t_start <= w_end else None
            samples.add(round(t_end, 3)) if w_start <= t_end <= w_end else None
            hits = []
            for s in sorted(samples):
                obox = _overlay_box_at(ov, s, width, height)
                if obox is not None and _boxes_overlap(obox, tbox):
                    hits.append(s)
            if hits:
                end = min(round(hits[-1] + 0.5, 3), round(w_end, 3))
                warn = {
                    "code": "layer_text_overlap_warning",
                    "message": (f"{kind} '{lid}' overlaps text "
                                f"'{text}' during {hits[0]:.2f}..{end:.2f}s "
                                f"(approximate boxes; move the {kind} path "
                                f"or shorten the text window)"),
                    "text": text,
                    "start": round(hits[0], 3), "end": end}
                warn["overlay_id" if kind == "overlay" else "diagram_id"] = lid
                warnings.append(warn)
    # Text-vs-text collisions (v1.41.0): two texts/captions
    # whose estimated boxes overlap in a shared time window
    # — the pop-vs-diagram-label case from the AI-race
    # video, which the layer pass above cannot see because
    # both elements are texts. One warning per pair, naming
    # both texts and the colliding range.
    for _i in range(len(texts)):
        _t1, _s1, _e1, _b1 = texts[_i]
        for _j in range(_i + 1, len(texts)):
            _t2, _s2, _e2, _b2 = texts[_j]
            _ws, _we = max(_s1, _s2), min(_e1, _e2)
            if _we <= _ws or not _boxes_overlap(_b1, _b2):
                continue
            warnings.append({
                "code": "text_text_overlap_warning",
                "message": (f"text '{_t1}' overlaps text '{_t2}' "
                            f"during {_ws:.2f}..{_we:.2f}s "
                            f"(approximate boxes; move one of "
                            f"them or shorten a window)"),
                "text": _t1, "other_text": _t2,
                "start": round(_ws, 3), "end": round(_we, 3)})
    # Text overflow (1.34.0): single-line centred drawtext clips
    # both edges when the line is wider than the frame, silently
    # (the known caption-clipping case). Estimated width, same
    # approximation as the boxes above, so this is a warning,
    # never an error: split the text into shorter cues or reduce
    # its size. Long Arabic lines use the same estimate.
    for text, t_start, t_end, tbox in texts:
        if tbox[2] > width:
            warnings.append({
                "code": "text_overflow_warning",
                "message": (f"text '{text}' estimated width "
                            f"{tbox[2]:.0f}px exceeds output width {width}px "
                            f"during {t_start:.2f}..{t_end:.2f}s and will clip "
                            f"at the edges (split into shorter texts/captions "
                            f"or reduce size)"),
                "text": text, "start": round(t_start, 3),
                "end": round(t_end, 3),
                "estimated_width": round(tbox[2], 1)})
    return warnings, moves


ZONE_BANDS = {  # Fractions of frame height (v1.42.0, Fix 3).
    # Gutters between bands (18-22%, 56-60%, 70-76%) are free
    # space: a box may extend into a gutter, but it must never
    # intersect two content bands.
    "header": (0.05, 0.18),
    "diagram": (0.22, 0.56),
    "callout": (0.60, 0.70),
    "subtitle": (0.76, 0.92),
}
ZONE_MIN_GAP_PX = 40  # callout below an active diagram box


def _bands_hit(box, height):
    """Content bands a box intersects (gutters ignored)."""
    top, bot = box[1], box[1] + box[3]
    return [name for name, (a, b) in ZONE_BANDS.items()
            if top < b * height and bot > a * height]


def check_vertical_zones(project, info, overlap_moves=None):
    """Vertical zone manager (v1.42.0, Fix 3).

    Strict non-overlapping bands for 9:16 and 16:9 (fractions of
    frame height): Header 5-18% (main + scene title), Diagram
    22-56%, Callout/Emphasis 60-70%, Subtitle 76-92%.

    - Callouts (keyword pops: texts with sfx "pop" and an
      explicit numeric y, no position preset) ALWAYS belong in
      the callout band. When a diagram is active in the pop's
      window the pop is pushed below the diagram box with a
      >=40px gap (the AI-race v2 03:13 case: the overlap
      cascade had moved the pop to 16px below the diagram box,
      which reads as touching the card). When resolve_overlaps
      is on the move is recorded in the returned moves list
      (applied by the renderer AFTER overlap_moves, so the zone
      wins on conflict); otherwise the misplacement is warned
      about and the pop stays put. A pop that cannot fit inside
      the callout band keeps its original y and is warned about.
    - Non-pop texts and diagrams are checked, never moved: a
      box intersecting two content bands warns
      (text_crosses_zone_boundary / diagram_crosses_zone) naming
      the bands. Gutters do not count.
    - Captions are checked against the subtitle band; with
      resolve_overlaps on, their anchor is already zone-clamped
      (see _text_box), so a bottom violation cannot occur — a
      too-tall block rising above the band top still warns.
    Boxes are the same estimates _text_box gives the overlap
    check, so every warning here is advisory, never an error.
    Returns (warnings, moves); moves are dicts {index, text,
    old_y, new_y} shaped like the overlap cascade's moves.
    """
    width, height, total = info["width"], info["height"], info["total_duration"]
    zone_clamp = project.get("resolve_overlaps") is True
    moved_y = {}
    for mv in (overlap_moves or []):
        if isinstance(mv, dict) and isinstance(mv.get("index"), int):
            moved_y[mv["index"]] = float(mv["new_y"])
    cap_spacing = project.get("caption_line_spacing", 1.35)
    cap_maxw = project.get("caption_max_width", 0.8)
    try:
        cap_spacing = float(cap_spacing)
    except (TypeError, ValueError):
        cap_spacing = 1.35
    try:
        cap_maxw = float(cap_maxw)
    except (TypeError, ValueError):
        cap_maxw = 0.9
    texts = []
    pops = []
    for pi, item in enumerate(project.get("texts") or []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            box = list(_text_box(item, False, width, height, total))
            if pi in moved_y:
                box[1] = moved_y[pi]
            is_pop = (item.get("sfx") == "pop"
                      and isinstance(item.get("y"), (int, float))
                      and "position" not in item)
            texts.append((item["text"], float(item.get("start", 0)),
                          float(item.get("end", total)), box, is_pop))
            if is_pop:
                pops.append((item["text"], float(item.get("start", 0)),
                             float(item.get("end", total)), box, pi))
    diag_boxes = []
    for dg in (info.get("diagrams") or []):
        synthetic = {"size": float(dg["size"]),
                     "position": dg.get("position", "bottom_right"),
                     "probe": {"width": dg["canvas_w"],
                               "height": dg["canvas_h"]}}
        for axis in ("x", "y"):
            if axis in dg:
                synthetic[axis] = dg[axis]
        db = _overlay_box_at(synthetic, float(dg["start"]), width, height)
        if db is not None:
            diag_boxes.append((db, float(dg["start"]),
                               float(dg["end"]), dg["id"]))
    warnings, moves = [], []
    call_top = ZONE_BANDS["callout"][0] * height
    call_bot = ZONE_BANDS["callout"][1] * height
    for text, s, e, box, pi in pops:
        required_top = call_top
        for dbox, ds, de, did in diag_boxes:
            if de <= s or ds >= e:
                continue
            required_top = max(required_top,
                               dbox[1] + dbox[3] + ZONE_MIN_GAP_PX)
        if box[1] >= required_top and box[1] + box[3] <= call_bot:
            continue  # already inside the callout band, clear of diagrams
        if required_top + box[3] > call_bot:
            warnings.append({
                "code": "callout_zone_overflow",
                "message": (f"callout '{text}' cannot fit inside the "
                            f"callout zone ({call_top:.0f}..{call_bot:.0f}px) "
                            f"with a 40px gap below the active diagram "
                            f"during {s:.2f}..{e:.2f}s and keeps its "
                            f"original y={box[1]:.0f} (shrink it or move "
                            f"the diagram)"),
                "text": text, "start": round(s, 3), "end": round(e, 3)})
            continue
        if zone_clamp:
            moves.append({"index": pi, "text": text,
                          "old_y": box[1], "new_y": required_top})
        else:
            warnings.append({
                "code": "callout_outside_zone",
                "message": (f"callout '{text}' at y={box[1]:.0f} sits "
                            f"outside the callout zone "
                            f"({call_top:.0f}..{call_bot:.0f}px) during "
                            f"{s:.2f}..{e:.2f}s (set resolve_overlaps: "
                            f"true to auto-move it, or move it to "
                            f"y={required_top:.0f})"),
                "text": text, "start": round(s, 3), "end": round(e, 3)})
    for text, s, e, box, is_pop in texts:
        if is_pop:
            continue
        hit = _bands_hit(box, height)
        if len(hit) >= 2:
            warnings.append({
                "code": "text_crosses_zone_boundary",
                "message": (f"text '{text}' crosses the zone boundary "
                            f"between {hit[0]} and {hit[1]} "
                            f"(box {box[1]:.0f}..{box[1] + box[3]:.0f}px) "
                            f"during {s:.2f}..{e:.2f}s (keep each text "
                            f"inside one band)"),
                "text": text, "start": round(s, 3), "end": round(e, 3)})
    for dbox, ds, de, did in diag_boxes:
        hit = _bands_hit(dbox, height)
        if len(hit) >= 2:
            warnings.append({
                "code": "diagram_crosses_zone",
                "message": (f"diagram '{did}' crosses the zone boundary "
                            f"between {hit[0]} and {hit[1]} "
                            f"(box {dbox[1]:.0f}..{dbox[1] + dbox[3]:.0f}px) "
                            f"during {ds:.2f}..{de:.2f}s (keep diagrams "
                            f"inside the diagram zone)"),
                "text": did, "start": round(ds, 3), "end": round(de, 3)})
    sub_top = ZONE_BANDS["subtitle"][0] * height
    sub_bot = ZONE_BANDS["subtitle"][1] * height
    for cap in (info.get("captions") or []):
        if isinstance(cap, dict) and isinstance(cap.get("text"), str):
            cbox = _text_box(cap, True, width, height, total,
                             line_spacing=cap_spacing,
                             max_width_frac=cap_maxw,
                             zone_clamp=zone_clamp)
            cs = float(cap.get("start", 0))
            ce = float(cap.get("end", total))
            if cbox[1] < sub_top or cbox[1] + cbox[3] > sub_bot:
                warnings.append({
                    "code": "caption_outside_subtitle_zone",
                    "message": (f"caption '{cap['text'][:40]}' extends "
                                f"outside the subtitle zone "
                                f"({sub_top:.0f}..{sub_bot:.0f}px): box "
                                f"{cbox[1]:.0f}..{cbox[1] + cbox[3]:.0f}px "
                                f"during {cs:.2f}..{ce:.2f}s (with "
                                f"resolve_overlaps: true the anchor is "
                                f"clamped; a too-tall block still needs "
                                f"shorter cues)"),
                    "text": cap["text"], "start": round(cs, 3),
                    "end": round(ce, 3)})
    return warnings, moves


SFX_COOLDOWN_S = 15.0  # minimum gap between auto-SFX (v1.42.0, Fix 5)
SFX_ATTENUATION_DEFAULT_DB = 16.0  # below the voiceover, by default
SFX_KIND_PRIORITY = {"chapter": 0, "diagram": 1, "callout": 2}


def compute_sfx_plan(project, info):
    """SFX discipline plan (v1.42.0, Fix 5).

    Auto-SFX fire ONLY on chapter transitions (clip starts),
    diagram reveals (diagram starts) and major callout pop-ins
    (texts/captions/overlays with sfx) — those are exactly the
    elements that carry sfx in the schema, so the plan's job is
    the other two rules: a minimum 15s cooldown between any two
    auto-SFX, and attenuation to ~-14..-18 dB below the
    voiceover.

    Cooldown: candidates are sorted chronologically (ties break
    by kind priority chapter > diagram > callout, then by
    collection order) and kept greedily — an entry is kept only
    when it starts >= 15s after the last kept one; the rest are
    dropped with a reason naming the SFX they crowded.

    Attenuation: an explicit sfx_volume always wins. Otherwise
    the volume is 0.8 * 10^(-att/20), where att is the project
    option sfx_attenuation_db (default 16, range 0..30) — about
    0.127 at the default, against sources that peak near full
    scale. The 0.8 factor is the sources' typical level (their
    RMS sits below their peak); the resulting level below the
    voice is MEASURED in the R5 proof render, not assumed.

    Pitch: kept entries alternate 1.03 / 0.97 (deterministic,
    by kept index) so repeated SFX do not sound identical.

    Returns {"kept": [...], "dropped": [...],
    "attenuation_db": att}. Kept entries carry start, name,
    volume, kind, source and pitch; dropped entries carry
    start, name, kind, source and reason. The renderer
    consumes kept; a hand-built info dict without a plan falls
    back to the old collect-all behaviour.
    """
    att = project.get("sfx_attenuation_db",
                      SFX_ATTENUATION_DEFAULT_DB)
    try:
        att = float(att)
    except (TypeError, ValueError):
        att = SFX_ATTENUATION_DEFAULT_DB
    auto_vol = 0.8 * (10.0 ** (-att / 20.0))
    clip_starts = info.get("clip_starts") or []
    candidates = []
    for ci, clip in enumerate(info.get("clips") or []):
        if isinstance(clip, dict) and clip.get("sfx"):
            st = clip_starts[ci] if ci < len(clip_starts) else 0.0
            candidates.append({"start": round(float(st), 3),
                               "name": clip["sfx"],
                               "volume": clip.get("sfx_volume"),
                               "kind": "chapter", "source": "clip",
                               "order": len(candidates)})
    for item in (project.get("texts") or []):
        if isinstance(item, dict) and item.get("sfx"):
            candidates.append({"start": round(float(item.get("start", 0)), 3),
                               "name": item["sfx"],
                               "volume": item.get("sfx_volume"),
                               "kind": "callout", "source": "text",
                               "order": len(candidates)})
    for cap in (info.get("captions") or []):
        if isinstance(cap, dict) and cap.get("sfx"):
            candidates.append({"start": round(float(cap.get("start", 0)), 3),
                               "name": cap["sfx"],
                               "volume": cap.get("sfx_volume"),
                               "kind": "callout", "source": "caption",
                               "order": len(candidates)})
    for ov in (info.get("overlays") or []):
        if isinstance(ov, dict) and ov.get("sfx"):
            candidates.append({"start": round(float(ov.get("start", 0)), 3),
                               "name": ov["sfx"],
                               "volume": ov.get("sfx_volume"),
                               "kind": "callout", "source": "overlay",
                               "order": len(candidates)})
    for dg in (info.get("diagrams") or []):
        if isinstance(dg, dict) and dg.get("sfx"):
            candidates.append({"start": round(float(dg.get("start", 0)), 3),
                               "name": dg["sfx"],
                               "volume": dg.get("sfx_volume"),
                               "kind": "diagram", "source": "diagram",
                               "order": len(candidates)})
    # Starts are rounded to ms: a clip start computed from
    # duration sums can differ from a text's literal start by
    # float dust (531.7200000000001 vs 531.72), which would let
    # the dust decide a tie instead of the kind priority.
    candidates.sort(key=lambda c: (c["start"],
                                   SFX_KIND_PRIORITY[c["kind"]],
                                   c["order"]))
    kept, dropped = [], []
    last_kept = None
    for cand in candidates:
        if last_kept is not None and \
                cand["start"] - last_kept["start"] < SFX_COOLDOWN_S - 1e-9:
            dropped.append({
                "start": cand["start"], "name": cand["name"],
                "kind": cand["kind"], "source": cand["source"],
                "reason": (f"within 15s of kept {last_kept['kind']} "
                           f"'{last_kept['name']}' at "
                           f"{last_kept['start']:.2f}s "
                           f"({cand['start'] - last_kept['start']:.2f}s "
                           f"gap)")})
            continue
        vol = cand["volume"]
        vol = float(vol) if vol is not None else auto_vol
        kept.append({"start": cand["start"], "name": cand["name"],
                     "volume": vol, "kind": cand["kind"],
                     "source": cand["source"],
                     "pitch": 1.03 if len(kept) % 2 == 0 else 0.97})
        last_kept = kept[-1]
    return {"kept": kept, "dropped": dropped, "attenuation_db": att}


def _validate_stroke(st, cw, ch, errors, did):
    """Validate one custom stroke dict against its canvas size.

    Returns a resolved stroke dict (defaults filled) or None when
    the stroke is invalid (errors already appended). Geometry
    must fit inside the canvas: a diagram that spills past its
    own frame would crop at render time, so it fails loudly
    here instead, with a fix hint."""
    if not isinstance(st, dict):
        errors.append(err("INVALID_SCHEMA", "each diagram stroke must be an object", field="diagrams.strokes", clip_id=did,
                          fix="make each stroke an object with kind, e.g. {'kind': 'polyline', 'points': [[0, 0], [100, 50]]}"))
        return None
    kind = st.get("kind")
    if kind not in ("polyline", "arrow", "circle", "ellipse", "rect", "line", "arc"):
        errors.append(err("INVALID_SCHEMA", "stroke kind must be polyline, arrow, circle, ellipse, rect, line or arc", field="diagrams.strokes.kind", clip_id=did,
                          fix="set kind to polyline (free line), line (straight 2-point segment), arrow (line with head), circle, ellipse, rect or arc (centre + radius + angles)"))
        return None
    out = dict(st)
    color = st.get("color", "white")
    if not isinstance(color, str):
        errors.append(err("INVALID_SCHEMA", "stroke color must be a name or #RRGGBB hex", field="diagrams.strokes.color", clip_id=did,
                          fix="set color to 'white' (drawing) or 'yellow' (emphasis only), or a #RRGGBB hex value"))
        return None
    from .diagrams import resolve_color
    if resolve_color(color) is None:
        errors.append(err("INVALID_SCHEMA", f"unknown stroke color '{color}'", field="diagrams.strokes.color", clip_id=did,
                          fix="set color to 'white', 'yellow', another named colour, or a #RRGGBB hex value"))
        return None
    out["color"] = color
    try:
        wdt = float(st.get("width", 7))
    except (TypeError, ValueError):
        errors.append(err("INVALID_SCHEMA", "stroke width must be a number", field="diagrams.strokes.width", clip_id=did,
                          fix="set width between 2 and 20 pixels (7 is the standard chalk weight)"))
        return None
    if not 2.0 <= wdt <= 20.0:
        errors.append(err("INVALID_SCHEMA", "stroke width must be 2..20", field="diagrams.strokes.width", clip_id=did,
                          fix="set width between 2 and 20 pixels"))
        return None
    out["width"] = wdt
    try:
        alpha = float(st.get("alpha", 0.95))
    except (TypeError, ValueError):
        errors.append(err("INVALID_SCHEMA", "stroke alpha must be a number", field="diagrams.strokes.alpha", clip_id=did,
                          fix="set alpha between 0.2 and 1 (0.95 is solid chalk)"))
        return None
    if not 0.2 <= alpha <= 1.0:
        errors.append(err("INVALID_SCHEMA", "stroke alpha must be 0.2..1", field="diagrams.strokes.alpha", clip_id=did,
                          fix="set alpha between 0.2 (faint, e.g. grid) and 1 (solid)"))
        return None
    out["alpha"] = alpha
    if "weight" in st:
        try:
            wt = float(st["weight"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "stroke weight must be a number", field="diagrams.strokes.weight", clip_id=did,
                              fix="set weight to a positive number, or remove it to weight by path length"))
            return None
        if wt <= 0:
            errors.append(err("INVALID_SCHEMA", "stroke weight must be > 0", field="diagrams.strokes.weight", clip_id=did,
                              fix="set weight to a positive number, or remove it"))
            return None
        out["weight"] = wt
    # v1.45.0, R4a: a "seed" field, when present, is ignored
    # (it used to drive the deleted wobble/grain) — it passes
    # through in `out` for backward compatibility, unvalidated.

    def _num_pair(v, what):
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            errors.append(err("INVALID_SCHEMA", f"{what} must be an [x, y] pair", field="diagrams.strokes", clip_id=did,
                              fix=f"give {what} as two numbers, e.g. [100, 50]"))
            return None
        try:
            fx, fy = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", f"{what} must be numbers", field="diagrams.strokes", clip_id=did,
                              fix=f"give {what} as two numbers, e.g. [100, 50]"))
            return None
        if not (0 <= fx <= cw and 0 <= fy <= ch):
            errors.append(err("INVALID_SCHEMA", f"{what} ({fx}, {fy}) is outside the diagram canvas {cw}x{ch}", field="diagrams.strokes", clip_id=did,
                              fix=f"keep {what} inside 0..{cw} horizontally and 0..{ch} vertically"))
            return None
        return (fx, fy)

    if kind in ("polyline", "arrow", "line"):
        pts = st.get("points")
        if kind == "line":
            if not isinstance(pts, list) or len(pts) != 2:
                errors.append(err("INVALID_SCHEMA", "line strokes need exactly 2 points", field="diagrams.strokes.points", clip_id=did,
                                  fix="give points as exactly two [x, y] pairs, e.g. [[0, 0], [100, 50]]"))
                return None
        elif not isinstance(pts, list) or len(pts) < 2:
            errors.append(err("INVALID_SCHEMA", "polyline/arrow strokes need a points list of at least 2 [x, y] pairs", field="diagrams.strokes.points", clip_id=did,
                              fix="give points as e.g. [[0, 0], [100, 50], [200, 20]]"))
            return None
        checked = []
        for pt in pts:
            cp = _num_pair(pt, "each point")
            if cp is None:
                return None
            checked.append([cp[0], cp[1]])
        out["points"] = checked
        if kind == "arrow":
            try:
                hs = float(st.get("head_size", 36))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "arrow head_size must be a number", field="diagrams.strokes.head_size", clip_id=did,
                                  fix="set head_size between 10 and 100 pixels, or remove it for the 36 default"))
                return None
            if not 10.0 <= hs <= 100.0:
                errors.append(err("INVALID_SCHEMA", "arrow head_size must be 10..100", field="diagrams.strokes.head_size", clip_id=did,
                                  fix="set head_size between 10 and 100 pixels"))
                return None
            out["head_size"] = hs
    elif kind == "circle":
        cc = _num_pair(st.get("center"), "circle center")
        if cc is None:
            return None
        try:
            r = float(st["radius"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "circle strokes need a numeric radius", field="diagrams.strokes.radius", clip_id=did,
                              fix="set radius to a positive number that fits inside the canvas"))
            return None
        if r <= 0 or cc[0] - r < 0 or cc[0] + r > cw or cc[1] - r < 0 or cc[1] + r > ch:
            errors.append(err("INVALID_SCHEMA", "circle radius must be > 0 and fit inside the canvas", field="diagrams.strokes.radius", clip_id=did,
                              fix="shrink the radius or move the center so the whole circle fits"))
            return None
        out["center"] = [cc[0], cc[1]]
        out["radius"] = r
    elif kind == "ellipse":
        cc = _num_pair(st.get("center"), "ellipse center")
        if cc is None:
            return None
        try:
            rx, ry = float(st["radius_x"]), float(st["radius_y"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "ellipse strokes need numeric radius_x and radius_y", field="diagrams.strokes.radius_x", clip_id=did,
                              fix="set radius_x and radius_y to positive numbers that fit inside the canvas"))
            return None
        if rx <= 0 or ry <= 0 or cc[0] - rx < 0 or cc[0] + rx > cw or cc[1] - ry < 0 or cc[1] + ry > ch:
            errors.append(err("INVALID_SCHEMA", "ellipse radii must be > 0 and fit inside the canvas", field="diagrams.strokes", clip_id=did,
                              fix="shrink the radii or move the center so the whole ellipse fits"))
            return None
        out["center"] = [cc[0], cc[1]]
        out["radius_x"] = rx
        out["radius_y"] = ry
    elif kind == "arc":
        # Delta: arc = centre + radius + start/end angles in
        # degrees (0 = east, positive = clockwise on screen).
        # A zero sweep (start == end) means a full circle.
        cc = _num_pair(st.get("center"), "arc center")
        if cc is None:
            return None
        try:
            r = float(st["radius"])
            sa = float(st["start_angle"])
            ea = float(st["end_angle"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "arc strokes need a numeric radius and numeric start_angle/end_angle", field="diagrams.strokes", clip_id=did,
                              fix="set radius to a positive number that fits inside the canvas, and start_angle/end_angle in degrees (0 = east, positive = clockwise)"))
            return None
        if not (math.isfinite(sa) and math.isfinite(ea)):
            errors.append(err("INVALID_SCHEMA", "arc angles must be finite numbers", field="diagrams.strokes", clip_id=did,
                              fix="set start_angle and end_angle to finite degree values"))
            return None
        if r <= 0 or cc[0] - r < 0 or cc[0] + r > cw or cc[1] - r < 0 or cc[1] + r > ch:
            errors.append(err("INVALID_SCHEMA", "arc radius must be > 0 and fit inside the canvas", field="diagrams.strokes.radius", clip_id=did,
                              fix="shrink the radius or move the center so the whole arc circle fits"))
            return None
        out["center"] = [cc[0], cc[1]]
        out["radius"] = r
        out["start_angle"] = sa
        out["end_angle"] = ea
    else:  # rect
        try:
            x, y = float(st["x"]), float(st["y"])
            rw, rh = float(st["rect_width"]), float(st["rect_height"])
        except (KeyError, TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "rect strokes need numeric x, y, rect_width and rect_height", field="diagrams.strokes", clip_id=did,
                              fix="give x/y (top-left) plus rect_width/rect_height that fit inside the canvas"))
            return None
        if x < 0 or y < 0 or rw <= 0 or rh <= 0 or x + rw > cw or y + rh > ch:
            errors.append(err("INVALID_SCHEMA", "rect must fit inside the canvas", field="diagrams.strokes", clip_id=did,
                              fix="move or shrink the rect so it stays inside the canvas"))
            return None
        out["x"], out["y"], out["rect_width"], out["rect_height"] = x, y, rw, rh
        try:
            cr = float(st.get("corner_radius", 0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "rect corner_radius must be a number", field="diagrams.strokes.corner_radius", clip_id=did,
                              fix="set corner_radius between 0 and half the smaller side, or remove it"))
            return None
        if not 0 <= cr <= min(rw, rh) / 2:
            errors.append(err("INVALID_SCHEMA", "rect corner_radius must be 0..half the smaller side", field="diagrams.strokes.corner_radius", clip_id=did,
                              fix="set corner_radius between 0 and half the smaller side"))
            return None
        out["corner_radius"] = cr
    return out


def _adapt_template_diagram(tname, params, rdg, width, height, project=None):
    """Build the motion element for a legacy template diagram
    (v1.45.0, R4b-3).

    Returns (motion_element, display_w, display_h). The display
    rect is the diagram's: width = even(size * frame width),
    height from the template canvas aspect, position = the preset
    (20px margin, mirroring render.py OVERLAY_POS) or per-axis
    x/y. The position is clamped into the frame — the motion
    layer requires in-frame bboxes, the stroke layer did not, so
    a diagram that hung off-frame is pulled fully inside (a
    visual shift for that edge case, instead of a rejection).
    The motion timing reproduces the diagram's: draw = the
    diagram draw, and the hold+fade becomes fade_out 1.0s
    starting at start+draw+hold when that fade starts before the
    window ends (the diagram's own condition).
    """
    # Template canvas sizes — the geometry the legacy builders
    # used; the aspect fixes the display height.
    _CANVAS = {"chart": (900, 560), "basket": (700, 560),
               "balance": (900, 560), "causes": (900, 460)}
    cw, ch = _CANVAS[tname]
    dw = int(float(rdg["size"]) * width)
    dw = dw if dw % 2 == 0 else dw - 1
    dw = max(2, dw)
    dh = int(round(dw * ch / cw))
    dh = dh if dh % 2 == 0 else dh - 1
    dh = max(2, dh)
    pos = rdg.get("position", "bottom_right")
    if "x" in rdg:
        px = int(rdg["x"])
    elif pos in ("top_left", "bottom_left"):
        px = 20
    elif pos in ("top_center", "center", "bottom_center"):
        px = (width - dw) // 2
    else:  # top_right, bottom_right
        px = width - dw - 20
    if "y" in rdg:
        py = int(rdg["y"])
    elif pos in ("top_left", "top_center", "top_right"):
        py = 20
    elif pos == "center":
        py = (height - dh) // 2
    else:  # bottom_*
        py = height - dh - 20
    px = min(max(px, 0), max(0, width - dw))
    py = min(max(py, 0), max(0, height - dh))
    start = float(rdg["start"])
    end = float(rdg["end"])
    draw = float(rdg["draw"])
    hold = float(rdg.get("hold_seconds", 8.0))
    mel = {"id": rdg["id"], "start": start, "end": end}
    fade_start = start + draw + hold
    if fade_start < end:
        mel["fade_out"] = 1.0
        mel["fade_start"] = fade_start
    # Chart kinds take the display rect + draw + stagger/grow;
    # the single-coin shape below takes keyframes instead (a
    # shape rejects draw and has no w/h).
    if tname == "chart":
        values = [float(v) for v in params["values"]]
        n = len(values)
        if max(values) <= 0:
            # Degenerate all-zero chart: the motion line_chart
            # rejects an all-zero series, the chalk chart drew a
            # flat line. Keep the flat line (at the bottom) so
            # the project still renders.
            values = [1.0] * n
        mel.update({"kind": "line_chart", "x": px, "y": py,
                    "w": dw, "h": dh, "draw": draw,
                    "values": values, "grid": True})
    elif tname == "basket":
        coins = int(params.get("coins", 3))
        if coins == 1:
            # A single coin is a circle, not a chart (motion
            # bars needs >= 2 values, and a faked second bar
            # would misrepresent the count). One static white
            # circle centred in the diagram rect, at the coin's
            # chalk radius (58px on the 700px canvas).
            r = max(10.0, dw * 58.0 / 700.0)
            cx = px + dw / 2.0
            cy = py + dh / 2.0
            mel.update({"kind": "shape", "shape": "circle",
                        "radius": r, "fill": "white",
                        "keyframes": [{"t": start, "x": cx, "y": cy},
                                      {"t": end, "x": cx, "y": cy}]})
            n = 1
        else:
            mel.update({"kind": "bars", "x": px, "y": py,
                        "w": dw, "h": dh, "draw": draw,
                        "values": [1.0] * coins,
                        "labels": [str(i + 1) for i in range(coins)]})
            n = coins
    elif tname == "balance":
        left_lbl = params.get("left_label")
        right_lbl = params.get("right_label")
        labels = params.get("labels")
        if labels and isinstance(labels, (list, tuple)) and len(labels) >= 2:
            if not left_lbl:
                left_lbl = str(labels[0])
            if not right_lbl:
                right_lbl = str(labels[1])
        if not left_lbl or not right_lbl:
            is_ar = False
            if project:
                proj_title = str(project.get("title", ""))
                if any("\u0600" <= ch <= "\u06ff" for ch in proj_title):
                    is_ar = True
                else:
                    for txt in project.get("texts", []):
                        if any("\u0600" <= ch <= "\u06ff" for ch in str(txt.get("text", ""))):
                            is_ar = True
                            break
            if not is_ar:
                for s in (rdg.get("title", ""), params.get("title", "")):
                    if any("\u0600" <= ch <= "\u06ff" for ch in str(s)):
                        is_ar = True
                        break
            if not left_lbl:
                left_lbl = "اليمين" if is_ar else "A"
            if not right_lbl:
                right_lbl = "اليسار" if is_ar else "B"

        mel.update({"kind": "comparison_cards", "x": px, "y": py,
                    "w": dw, "h": dh, "draw": draw,
                    "cards": [{"label": str(left_lbl),
                               "value": float(params.get("left", 1))},
                              {"label": str(right_lbl),
                               "value": float(params.get("right", 1))}]})
        n = 2
    else:  # causes
        items = params.get("items") or params.get("labels")
        if items and isinstance(items, (list, tuple)):
            cards = []
            for i, itm in enumerate(items):
                if isinstance(itm, dict):
                    cards.append({"label": str(itm.get("label", i + 1)),
                                  "value": itm.get("value")})
                else:
                    cards.append({"label": str(itm), "value": None})
            rows = len(cards)
        else:
            rows = int(params.get("rows", 3))
            cards = [{"label": str(i + 1), "value": None}
                      for i in range(rows)]
        mel.update({"kind": "comparison_cards", "x": px, "y": py,
                    "w": dw, "h": dh, "draw": draw,
                    "cards": cards})
        n = rows
    # Stagger/grow: grow 0.5, stagger scaled so the last item
    # finishes inside the draw ((n-1)*stagger + grow <= 1).
    # Chart kinds only — the single-coin shape has no stagger.
    if mel["kind"] in ("bars", "line_chart", "comparison_cards"):
        mel["grow"] = 0.5
        mel["stagger"] = min(0.12, 0.5 / (n - 1)) if n > 1 else 0.0
    return mel, dw, dh


def _validate_diagrams(project, total, width, height, overlay_ids,
                       errors):
    """Validate the project-level `diagrams` list (v1.38.0).

    Returns (resolved diagram dicts for info["diagrams"],
    adapted motion elements). A diagram is custom strokes (with
    a canvas, default 900x560) or one built-in template with
    parameters — never both, and labels are never part of a
    diagram (templates take no text; labels are AgentCut texts).
    Placement mirrors overlays: size (fraction of output width),
    position preset, or x/y pixel offsets winning per axis.
    The four legacy templates (chart, basket, balance, causes)
    are motion adapters (v1.45.0, R4b-3): their diagram entry
    stays (placement, sfx, card) but carries no strokes, and the
    adapted motion element is returned for the motion validator.
    Only `primitives` still builds strokes.
    """
    from .diagrams import ADAPTER_TEMPLATES, TEMPLATES, build_template
    resolved = []
    adapted = []
    raw = project.get("diagrams")
    if raw is None:
        return resolved, adapted
    if not isinstance(raw, list):
        errors.append(err("INVALID_SCHEMA", "diagrams must be a list", field="diagrams",
                          fix="give diagrams as a list of diagram objects, or remove diagrams"))
        return resolved, adapted
    seen = set()
    for dg in raw:
        if not isinstance(dg, dict):
            errors.append(err("INVALID_SCHEMA", "each diagram must be an object", field="diagrams",
                              fix="make each diagram an object with id, start, end and strokes or template"))
            continue
        did = dg.get("id")
        if not isinstance(did, str) or not did.strip():
            errors.append(err("INVALID_SCHEMA", "each diagram needs a non-empty string id", field="diagrams.id",
                              fix="give every diagram an id, e.g. 'chart1'"))
            continue
        if did in seen or did in overlay_ids:
            errors.append(err("INVALID_SCHEMA", f"duplicate diagram id {did}", field="diagrams.id", clip_id=did,
                              fix=f"rename one of the diagrams/overlays called {did}"))
            continue
        seen.add(did)
        try:
            start = float(dg.get("start", 0))
            end = float(dg.get("end", total))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "diagram start/end must be numbers", field="diagrams", clip_id=did,
                              fix="set start/end to timeline seconds, e.g. start 2, end 10"))
            continue
        if start < 0 or end <= start or end > total + 0.05:
            errors.append(err("CLIP_OUT_OF_RANGE", f"diagram range {start}..{end} outside timeline {total:.2f}s", field="diagrams", clip_id=did,
                              fix=f"keep the diagram window inside 0..{total:.2f} with end after start"))
            continue
        window = end - start
        if "draw" in dg:
            try:
                draw = float(dg["draw"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "diagram draw must be a number of seconds", field="diagrams.draw", clip_id=did,
                                  fix="set draw to the draw-on duration in seconds, e.g. 3.0, or remove it"))
                continue
            if draw <= 0 or draw > window + 1e-9:
                errors.append(err("INVALID_SCHEMA", "diagram draw must be > 0 and no longer than its window", field="diagrams.draw", clip_id=did,
                                  fix=f"set draw between 0 and {window:.2f}s (the diagram window), or remove it"))
                continue
        else:
            draw = min(4.0, window)
        # Max diagram hold (v1.42.0, Fix 4a): after the draw
        # animation completes the diagram holds for hold_seconds
        # (default 8.0, the spec's 6-10s band) and then fades
        # out over 1.0s — it never sits frozen for 40s. A hold
        # of 0 fades immediately after the draw. The fade is
        # applied by the renderer to the diagram and card
        # streams; a window that ends before the fade starts
        # simply ends first.
        try:
            hold = float(dg.get("hold_seconds", 8.0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "diagram hold_seconds must be a number of seconds", field="diagrams.hold_seconds", clip_id=did,
                              fix="set hold_seconds to e.g. 8.0, or remove it"))
            continue
        if not 0 <= hold <= 60:
            errors.append(err("INVALID_SCHEMA", "diagram hold_seconds must be 0..60", field="diagrams.hold_seconds", clip_id=did,
                              fix="set hold_seconds between 0 and 60 seconds"))
            continue
        try:
            size = float(dg.get("size", 0.5))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "diagram size must be a number", field="diagrams.size", clip_id=did,
                              fix="set size to a fraction of the output width, e.g. 0.5"))
            continue
        if not 0.05 <= size <= 1.0:
            errors.append(err("INVALID_SCHEMA", "diagram size must be 0.05..1.0 (fraction of output width)", field="diagrams.size", clip_id=did,
                              fix="set size between 0.05 and 1.0"))
            continue
        position = dg.get("position", "bottom_right")
        if position not in TEXT_POSITIONS:
            errors.append(err("INVALID_SCHEMA", "diagram position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field="diagrams.position", clip_id=did,
                              fix="use a preset position, or set x and y pixel offsets instead"))
            continue
        rdg = {"id": did, "start": start, "end": end, "draw": draw,
               "size": size, "position": position,
               "hold_seconds": hold}
        bad_xy = False
        for axis in ("x", "y"):
            if axis in dg:
                try:
                    aval = int(dg[axis])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"diagram {axis} must be an integer pixel offset", field=f"diagrams.{axis}", clip_id=did,
                                      fix=f"set {axis} to a non-negative integer pixel offset"))
                    bad_xy = True
                    continue
                if aval < 0:
                    errors.append(err("INVALID_SCHEMA", f"diagram {axis} must be >= 0", field=f"diagrams.{axis}", clip_id=did,
                                      fix=f"set {axis} to 0 or more"))
                    bad_xy = True
                    continue
                rdg[axis] = aval
        if bad_xy:
            continue
        has_strokes = "strokes" in dg and dg.get("strokes") is not None
        has_template = "template" in dg and dg.get("template") is not None
        if has_strokes == has_template:
            errors.append(err("INVALID_SCHEMA", "a diagram needs exactly one of strokes or template", field="diagrams", clip_id=did,
                              fix="give custom strokes, or a template name with params — not both, not neither"))
            continue
        if has_template:
            if "canvas" in dg:
                errors.append(err("INVALID_SCHEMA", "a template diagram must not set canvas (the template fixes its own geometry)", field="diagrams.canvas", clip_id=did,
                                  fix="remove canvas from this template diagram, or use custom strokes with canvas instead"))
                continue
            tname = dg["template"]
            from .plugins import get_diagram_plugin
            dplugin = get_diagram_plugin(tname)
            if tname not in TEMPLATES and dplugin is None:
                errors.append(err("INVALID_SCHEMA", f"unknown diagram template '{tname}'; valid templates are: " + ", ".join(sorted(TEMPLATES)), field="diagrams.template", clip_id=did,
                                  fix="set template to one of: " + ", ".join(sorted(TEMPLATES))))
                continue
            params = dg.get("params") or {}
            if not isinstance(params, dict):
                errors.append(err("INVALID_SCHEMA", "diagram params must be an object", field="diagrams.params", clip_id=did,
                                  fix="give params as an object, e.g. {'values': [10, 12, 14]}"))
                continue
            if dplugin is not None:
                # Custom diagram plugin handles its own params in build_template
                ok = True
            else:
                allowed = {"chart": {"values", "fill", "y_grid_values"},
                           "basket": {"coins"},
                           "balance": {"left", "right"}, "causes": {"rows"},
                           "primitives": {"strokes", "canvas"},
                           "timeline": {"steps", "direction", "color", "accent_color", "arrow"},
                           "process_flow": {"steps", "color", "accent_color"}}[tname]
                unknown = sorted(set(params) - allowed)
                if unknown:
                    errors.append(err("INVALID_SCHEMA", f"template '{tname}' does not take params: " + ", ".join(unknown), field="diagrams.params", clip_id=did,
                                      fix=f"template '{tname}' takes only: " + ", ".join(sorted(allowed))))
                    continue
                ok = True
            if tname == "chart":
                vals = params.get("values")
                if not isinstance(vals, list) or not 2 <= len(vals) <= 8:
                    errors.append(err("INVALID_SCHEMA", "chart template needs values: a list of 2..8 numbers", field="diagrams.params.values", clip_id=did,
                                      fix="give values as e.g. [10, 12, 14, 18]"))
                    ok = False
                else:
                    try:
                        vals = [float(v) for v in vals]
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", "chart values must be numbers", field="diagrams.params.values", clip_id=did,
                                          fix="give values as numbers, e.g. [10, 12, 14]"))
                        ok = False
                    else:
                        params = dict(params, values=vals)
                    # v1.41.0: fill must be a bool; y_grid_values
                    # a list of numbers (any count — each maps to
                    # a gridline through the curve normalization).
                    if params.get("fill") is not None and \
                            not isinstance(params["fill"], bool):
                        errors.append(err("INVALID_SCHEMA", "chart fill must be true or false", field="diagrams.params.fill", clip_id=did,
                                          fix="set fill to true for a translucent area under the curve, or remove it"))
                        ok = False
                    gv = params.get("y_grid_values")
                    if gv is not None:
                        try:
                            gv = [float(v) for v in gv]
                        except (TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", "chart y_grid_values must be a list of numbers", field="diagrams.params.y_grid_values", clip_id=did,
                                              fix="give y_grid_values as e.g. [0, 50, 100] in the same units as values"))
                            ok = False
                        else:
                            params = dict(params, y_grid_values=gv)
            elif tname == "basket":
                try:
                    coins = int(params.get("coins", 3))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "basket coins must be an integer", field="diagrams.params.coins", clip_id=did,
                                      fix="set coins to a whole number 1..5, or remove it for 3"))
                    ok = False
                else:
                    if isinstance(params.get("coins", 3), bool) or not 1 <= coins <= 5:
                        errors.append(err("INVALID_SCHEMA", "basket coins must be 1..5", field="diagrams.params.coins", clip_id=did,
                                          fix="set coins between 1 and 5"))
                        ok = False
                    else:
                        params = dict(params, coins=coins)
            elif tname == "balance":
                for fname in ("left", "right"):
                    try:
                        fv = float(params.get(fname, 1))
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", f"balance {fname} must be a number", field="diagrams.params", clip_id=did,
                                          fix=f"set {fname} to a number >= 0, or remove it"))
                        ok = False
                        break
                    if fv < 0:
                        errors.append(err("INVALID_SCHEMA", f"balance {fname} must be >= 0", field="diagrams.params", clip_id=did,
                                          fix=f"set {fname} to 0 or more"))
                        ok = False
                        break
                    params = dict(params, **{fname: fv})
            elif tname == "causes":
                try:
                    rows = int(params.get("rows", 3))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "causes rows must be an integer", field="diagrams.params.rows", clip_id=did,
                                      fix="set rows to a whole number 1..4, or remove it for 3"))
                    ok = False
                else:
                    if isinstance(params.get("rows", 3), bool) or not 1 <= rows <= 4:
                        errors.append(err("INVALID_SCHEMA", "causes rows must be 1..4", field="diagrams.params.rows", clip_id=did,
                                          fix="set rows between 1 and 4"))
                        ok = False
                    else:
                        params = dict(params, rows=rows)
            elif tname == "timeline":
                try:
                    steps = int(params.get("steps", 3))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "timeline steps must be an integer", field="diagrams.params.steps", clip_id=did,
                                      fix="set steps to a whole number 2..6, or remove it for 3"))
                    ok = False
                else:
                    if isinstance(params.get("steps", 3), bool) or not 2 <= steps <= 6:
                        errors.append(err("INVALID_SCHEMA", "timeline steps must be 2..6", field="diagrams.params.steps", clip_id=did,
                                          fix="set steps between 2 and 6"))
                        ok = False
                    else:
                        params = dict(params, steps=steps)
                direction = params.get("direction", "horizontal")
                if direction not in ("horizontal", "vertical"):
                    errors.append(err("INVALID_SCHEMA", "timeline direction must be 'horizontal' or 'vertical'", field="diagrams.params.direction", clip_id=did,
                                      fix="set direction to 'horizontal' or 'vertical'"))
                    ok = False
            elif tname == "process_flow":
                try:
                    steps = int(params.get("steps", 3))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "process_flow steps must be an integer", field="diagrams.params.steps", clip_id=did,
                                      fix="set steps to a whole number 2..5, or remove it for 3"))
                    ok = False
                else:
                    if isinstance(params.get("steps", 3), bool) or not 2 <= steps <= 5:
                        errors.append(err("INVALID_SCHEMA", "process_flow steps must be 2..5", field="diagrams.params.steps", clip_id=did,
                                          fix="set steps between 2 and 5"))
                        ok = False
                    else:
                        params = dict(params, steps=steps)
            elif tname == "primitives":
                # General primitives template: strokes are validated
                # exactly like custom strokes, against the template
                # canvas (params.canvas, default 900x560). The
                # validated strokes replace the raw ones in params,
                # so build_template below returns them unchanged.
                _canvas = params.get("canvas") or {}
                if isinstance(_canvas, (list, tuple)) and len(_canvas) == 2:
                    try:
                        _cw, _ch = int(_canvas[0]), int(_canvas[1])
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", "primitives canvas must be [width, height] integers", field="diagrams.params.canvas", clip_id=did,
                                          fix="give canvas as e.g. [900, 560], or remove it for 900x560"))
                        ok = False
                        _cw = _ch = None
                elif isinstance(_canvas, dict):
                    try:
                        _cw = int(_canvas.get("width", 900))
                        _ch = int(_canvas.get("height", 560))
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", "primitives canvas width/height must be integers", field="diagrams.params.canvas", clip_id=did,
                                          fix="give canvas as e.g. {'width': 900, 'height': 560}, or remove it"))
                        ok = False
                        _cw = _ch = None
                else:
                    errors.append(err("INVALID_SCHEMA", "primitives canvas must be [width, height] or {width, height}", field="diagrams.params.canvas", clip_id=did,
                                      fix="give canvas as e.g. [900, 560], or remove it for 900x560"))
                    ok = False
                    _cw = _ch = None
                if ok and (not 100 <= _cw <= 1920 or not 100 <= _ch <= 1920):
                    errors.append(err("INVALID_SCHEMA", "primitives canvas must be 100..1920 per side", field="diagrams.params.canvas", clip_id=did,
                                      fix="set canvas between 100 and 1920 pixels per side"))
                    ok = False
                _pstrokes = params.get("strokes")
                if ok and (not isinstance(_pstrokes, list) or not _pstrokes):
                    errors.append(err("INVALID_SCHEMA", "primitives needs a non-empty strokes list", field="diagrams.params.strokes", clip_id=did,
                                      fix="give at least one stroke, e.g. {'kind': 'line', 'points': [[0, 0], [100, 50]]}"))
                    ok = False
                if ok:
                    _rstrokes = []
                    for _si, _st in enumerate(_pstrokes):
                        _st2 = dict(_st) if isinstance(_st, dict) else _st
                        _rs = _validate_stroke(_st2, _cw, _ch, errors, did)
                        if _rs is None:
                            _rstrokes = None
                            break
                        _rstrokes.append(_rs)
                    if _rstrokes is None:
                        ok = False
                    else:
                        params = dict(params, strokes=_rstrokes)
            if not ok:
                continue
            if tname in ADAPTER_TEMPLATES:
                # v1.45.0, R4b-3: the legacy templates build no
                # strokes. The diagram entry stays (source
                # template, placement, sfx, card) so the SFX plan,
                # the overlap checker and the timeline summary
                # keep working; canvas_w/h are the DISPLAY size
                # (the card PNG is generated at that size). The
                # motion element renders the chart instead.
                _mel, _dw, _dh = _adapt_template_diagram(
                    tname, params, rdg, width, height, project=project)
                rdg.update({"source": "template", "template": tname,
                            "params": params, "canvas_w": _dw,
                            "canvas_h": _dh, "adapted_to_motion": True})
                adapted.append(_mel)
                # Falls through to the SFX/card block below and
                # resolved.append(rdg): the adapted entry keeps
                # its sfx and card (the renderer composites the
                # card under the motion element).
            else:
                # Only `primitives` still builds strokes.
                try:
                    cw, ch, tstrokes = build_template(tname, params)
                except ValueError as exc:
                    errors.append(err("INVALID_SCHEMA", f"template '{tname}' failed: {exc}", field="diagrams.template", clip_id=did,
                                      fix="check the template parameters and retry"))
                    continue
                rdg.update({"source": "template", "template": tname,
                            "params": params, "canvas_w": cw, "canvas_h": ch,
                            "strokes": tstrokes})
        else:
            canvas = dg.get("canvas") or {}
            if not isinstance(canvas, dict):
                errors.append(err("INVALID_SCHEMA", "diagram canvas must be an object", field="diagrams.canvas", clip_id=did,
                                  fix="give canvas as e.g. {'width': 900, 'height': 560}, or remove it"))
                continue
            try:
                cw = int(canvas.get("width", 900))
                ch = int(canvas.get("height", 560))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "diagram canvas width/height must be integers", field="diagrams.canvas", clip_id=did,
                                  fix="set canvas width/height to whole pixels, e.g. 900x560"))
                continue
            if not 100 <= cw <= 1920 or not 100 <= ch <= 1920:
                errors.append(err("INVALID_SCHEMA", "diagram canvas width/height must be 100..1920", field="diagrams.canvas", clip_id=did,
                                  fix="set canvas between 100 and 1920 pixels per side"))
                continue
            strokes = dg.get("strokes")
            if not isinstance(strokes, list) or not strokes:
                errors.append(err("INVALID_SCHEMA", "a custom diagram needs a non-empty strokes list", field="diagrams.strokes", clip_id=did,
                                  fix="give at least one stroke, e.g. {'kind': 'polyline', 'points': [[0, 0], [100, 50]]}"))
                continue
            rstrokes = []
            for si, st in enumerate(strokes):
                st2 = dict(st) if isinstance(st, dict) else st
                rs = _validate_stroke(st2, cw, ch, errors, did)
                if rs is None:
                    rstrokes = None
                    break
                rstrokes.append(rs)
            if rstrokes is None:
                continue
            rdg.update({"source": "custom", "template": None,
                        "canvas_w": cw, "canvas_h": ch, "strokes": rstrokes})
        # v1.41.0: diagram SFX (chart-reveal chime and friends).
        # Validated exactly like clip/text SFX; carried into the
        # resolved diagram so the render schedules it at the
        # diagram's start. Invalid SFX leaves the diagram
        # un-carried (validation fails anyway on the errors).
        _nerr = len(errors)
        _validate_sfx(dg, errors, did, "diagrams")
        if len(errors) == _nerr:
            if "sfx" in dg:
                rdg["sfx"] = dg["sfx"]
            if "sfx_volume" in dg:
                rdg["sfx_volume"] = dg["sfx_volume"]
        # v1.41.0 Fix 6: diagram card — a rounded translucent
        # backdrop composited UNDER the diagram for its whole
        # window (static layer, not drawn progressively).
        # Validated exactly like SFX above; carried only when
        # clean. Defaults (black, 0.45) match the card text
        # preset and are applied at render time, so a bare
        # card: true needs no further fields.
        _nerr = len(errors)
        if "card" in dg and not isinstance(dg["card"], bool):
            errors.append(err("INVALID_SCHEMA", "diagram card must be true or false", field="diagrams.card", clip_id=did,
                              fix="set card to true for a backdrop card, or remove it"))
        if "card_color" in dg:
            from .diagrams import resolve_color
            if resolve_color(dg["card_color"]) is None:
                errors.append(err("INVALID_SCHEMA", f"unknown diagram card_color '{dg['card_color']}'", field="diagrams.card_color", clip_id=did,
                                  fix="set card_color to a colour name or #RRGGBB hex"))
        if "card_opacity" in dg:
            _co = dg["card_opacity"]
            if isinstance(_co, bool) or not isinstance(_co, (int, float)) or not 0.0 <= float(_co) <= 1.0:
                errors.append(err("INVALID_SCHEMA", "diagram card_opacity must be a number 0..1", field="diagrams.card_opacity", clip_id=did,
                                  fix="set card_opacity between 0 and 1, e.g. 0.45"))
        # Glassmorphism (v1.45.0, R7): a frosted backdrop-blur
        # card behind the diagram's backdrop card, ON by
        # default when a card is present. Set backdrop_blur:
        # false for a plain (non-frosted) card.
        if "backdrop_blur" in dg and not isinstance(dg["backdrop_blur"], bool):
            errors.append(err("INVALID_SCHEMA", "diagram backdrop_blur must be true or false", field="diagrams.backdrop_blur", clip_id=did,
                              fix="set backdrop_blur to true for a frosted card, or false for a plain card"))
        if len(errors) == _nerr:
            if "card" in dg:
                rdg["card"] = dg["card"]
            if "card_color" in dg:
                rdg["card_color"] = dg["card_color"]
            if "card_opacity" in dg:
                rdg["card_opacity"] = dg["card_opacity"]
            if dg.get("card"):
                rdg["backdrop_blur"] = bool(dg.get("backdrop_blur", True))
        resolved.append(rdg)
    return resolved, adapted


def _validate_motion_timing(el, rel, kind, mid, start, end, errors):
    """draw / fade_out / fade_start on a motion element (v1.45.0,
    R4b-2).

    draw (seconds) is chart kinds only: the staggered draw-on
    runs over `draw` and then holds at full size for the rest of
    the window. fade_out (seconds) + optional fade_start
    (absolute timeline seconds) are raster kinds only: the
    element fades out over fade_out seconds, starting at
    fade_start when given, else at end - fade_out (so it
    completes exactly at the window end). A fade that starts
    before the window end but runs past it is cut at the end —
    the diagram fade behaves the same way. Returns True when
    clean (fields copied onto rel), False otherwise.
    """
    window = end - start
    ok = True

    def _num_sec(name):
        nonlocal ok
        v = el[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be a number of seconds", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to a number of seconds, e.g. 1.0"))
            ok = False
            return None
        return float(v)

    if "draw" in el:
        if kind not in ("bars", "donut", "pie", "line_chart",
                        "comparison_cards"):
            errors.append(err("INVALID_SCHEMA", f"motion draw applies to chart kinds only (bars, donut, pie, line_chart, comparison_cards), not {kind}", field="motion.draw", clip_id=mid,
                              fix="remove draw, or use a chart kind"))
            ok = False
        else:
            d = _num_sec("draw")
            if d is not None:
                if not 0 < d <= window:
                    errors.append(err("INVALID_SCHEMA", f"motion draw must be > 0 and no longer than the {window}s window", field="motion.draw", clip_id=mid,
                                      fix=f"set draw between 0 and {window} seconds"))
                    ok = False
                else:
                    rel["draw"] = d
    if "fade_start" in el and "fade_out" not in el:
        errors.append(err("INVALID_SCHEMA", "motion fade_start needs fade_out (a fade start without a duration)", field="motion.fade_start", clip_id=mid,
                          fix="set fade_out as well, e.g. fade_out 1.0"))
        ok = False
    if "fade_out" in el:
        if kind == "counter":
            errors.append(err("INVALID_SCHEMA", "motion counter does not take fade_out/fade_start (it is a live number, not a video)", field="motion.fade_out", clip_id=mid,
                              fix="remove fade_out/fade_start, or use a raster kind"))
            ok = False
        else:
            fo = _num_sec("fade_out")
            if fo is not None:
                if not 0 < fo <= window:
                    errors.append(err("INVALID_SCHEMA", f"motion fade_out must be > 0 and no longer than the {window}s window", field="motion.fade_out", clip_id=mid,
                                      fix="set fade_out between 0 and {window} seconds"))
                    ok = False
                else:
                    rel["fade_out"] = fo
    if "fade_start" in el and "fade_out" in el and kind != "counter":
        fs = _num_sec("fade_start")
        if fs is not None:
            if not start < fs < end:
                errors.append(err("INVALID_SCHEMA", f"motion fade_start must be inside the window ({start}..{end})", field="motion.fade_start", clip_id=mid,
                                  fix=f"set fade_start between {start} and {end}"))
                ok = False
            else:
                rel["fade_start"] = fs
    return ok


def _validate_motion_chart(el, kind, mid, start, end, width, height,
                           fps, errors):
    """Validate one bars / donut / pie / line_chart /
    comparison_cards element (R2; comparison_cards v1.45.0,
    R4b-1).

    Returns the resolved dict, or None (errors appended). Charts
    use a TOP-LEFT anchor (x, y, w, h) — except donut/pie, which
    use a CENTER (x, y) + radius. The raster bbox must sit fully
    inside the frame (charts do not slide); derived labels are
    clamped into the frame at render time. Comparison cards take
    `cards` (2..4 {label, value}) instead of `values`/`labels`.
    """
    from .diagrams import resolve_color
    from .motion import _chart_bbox

    _PALETTE = ["#facc15", "#22d3ee", "#4ade80", "#f97316",
                "#e879f9", "#60a5fa", "#f43f5e", "#a3e635"]
    rel = {"id": mid, "kind": kind, "start": start, "end": end}
    ok = True

    def _num(name, lo=None, hi=None, default=None, required=False):
        nonlocal ok
        if name not in el:
            if required:
                errors.append(err("INVALID_SCHEMA", f"motion {kind} needs a numeric {name}", field=f"motion.{name}", clip_id=mid,
                                  fix=f"set {name} to a number, e.g. {default if default is not None else 100}"))
                ok = False
            return default
        v = el[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be a number", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to a number"))
            ok = False
            return default
        v = float(v)
        if lo is not None and not v >= lo:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be >= {lo}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {lo} or more"))
            ok = False
            return default
        if hi is not None and not v <= hi:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be <= {hi}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {hi} or less"))
            ok = False
            return default
        return v

    def _color(name, default):
        nonlocal ok
        if name not in el:
            return resolve_color(default)
        c = resolve_color(el[name])
        if c is None:
            errors.append(err("INVALID_SCHEMA", f"unknown motion {name} colour '{el[name]}'", field=f"motion.{name}", clip_id=mid,
                              fix="set it to a colour name or #RRGGBB hex"))
            ok = False
            return None
        return c

    x = _num("x", required=True)
    y = _num("y", required=True)
    if x is not None:
        rel["x"] = x
    if y is not None:
        rel["y"] = y
    if kind in ("bars", "line_chart", "comparison_cards"):
        w = _num("w", lo=1, required=True)
        h = _num("h", lo=1, required=True)
        if w is not None:
            rel["w"] = w
        if h is not None:
            rel["h"] = h
    else:
        radius = _num("radius", lo=1, required=True)
        if radius is not None:
            rel["radius"] = radius
        if kind == "donut":
            rel["donut_ratio"] = _num("donut_ratio", lo=0.0, hi=0.9,
                                      default=0.55)
        elif "donut_ratio" in el:
            errors.append(err("INVALID_SCHEMA", "motion pie does not take donut_ratio (a pie has no hole)", field="motion.donut_ratio", clip_id=mid,
                              fix="remove donut_ratio, or use kind 'donut'"))
            ok = False
    if kind == "comparison_cards":
        # Cards carry their own labels (and an optional value
        # each); `values` / `labels` are not chart fields here.
        if "values" in el:
            errors.append(err("INVALID_SCHEMA", "motion comparison_cards takes cards, not values", field="motion.values", clip_id=mid,
                              fix="give cards as e.g. [{'label': 'A', 'value': 10}, {'label': 'B', 'value': 20}]"))
            return None
        if "labels" in el:
            errors.append(err("INVALID_SCHEMA", "motion comparison_cards takes labels inside cards, not a labels list", field="motion.labels", clip_id=mid,
                              fix="put each label inside its card, e.g. {'label': 'A', 'value': 10}"))
            return None
        cards = el.get("cards")
        if not isinstance(cards, list) or not 2 <= len(cards) <= 4:
            errors.append(err("INVALID_SCHEMA", "motion comparison_cards needs cards: a list of 2..4 objects", field="motion.cards", clip_id=mid,
                              fix="give cards as e.g. [{'label': 'A', 'value': 10}, {'label': 'B', 'value': 20}]"))
            return None
        clean_cards = []
        for ci, c in enumerate(cards):
            if not isinstance(c, dict) or not isinstance(c.get("label"), str) \
                    or not c["label"].strip():
                errors.append(err("INVALID_SCHEMA", f"motion card {ci} needs a non-empty string label", field="motion.cards", clip_id=mid,
                                  fix="give each card a label, e.g. {'label': 'A', 'value': 10}"))
                return None
            v = c.get("value")
            if v is not None and (isinstance(v, bool)
                                  or not isinstance(v, (int, float, str))
                                  or (isinstance(v, float) and not math.isfinite(v))):
                errors.append(err("INVALID_SCHEMA", f"motion card {ci} value must be a number or a string", field="motion.cards", clip_id=mid,
                                  fix="give value as a number or a short string, e.g. 10 or 'high'"))
                return None
            clean_cards.append({"label": c["label"], "value": v})
        rel["cards"] = clean_cards
        n_cards = len(clean_cards)
        labels = None
    else:
        values = el.get("values")
        if not isinstance(values, list) or not 2 <= len(values) <= 8:
            errors.append(err("INVALID_SCHEMA", f"motion {kind} needs values: a list of 2..8 numbers", field="motion.values", clip_id=mid,
                              fix="give values as e.g. [10, 25, 40]"))
            return None
        clean_values = []
        for v in values:
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(float(v)) or float(v) < 0:
                errors.append(err("INVALID_SCHEMA", "motion values must be numbers >= 0", field="motion.values", clip_id=mid,
                                  fix="give values as non-negative numbers, e.g. [10, 25, 40]"))
                return None
            clean_values.append(float(v))
        if max(clean_values) <= 0:
            errors.append(err("INVALID_SCHEMA", "motion values need at least one value above 0", field="motion.values", clip_id=mid,
                              fix="give at least one value above 0"))
            return None
        rel["values"] = clean_values
        n_cards = None
    # A comparison_cards element never reaches this block with
    # a labels list (it is rejected above), so the chart labels
    # validation below applies to bars/donut/pie/line_chart only.
    labels = el.get("labels")
    if labels is not None:
        if not isinstance(labels, list) or len(labels) != len(values) \
                or any(not isinstance(s, str) for s in labels):
            errors.append(err("INVALID_SCHEMA", "motion labels must be a list of strings, one per value", field="motion.labels", clip_id=mid,
                              fix="give one label string per value, e.g. ['Jan', 'Feb', 'Mar']"))
            return None
        rel["labels"] = list(labels)
    colors = el.get("colors")
    if colors is None:
        rel["colors"] = [resolve_color(c) for c in _PALETTE]
    elif isinstance(colors, str):
        c = resolve_color(colors)
        if c is None:
            errors.append(err("INVALID_SCHEMA", f"unknown motion colors colour '{colors}'", field="motion.colors", clip_id=mid,
                              fix="set colors to a colour name, a #RRGGBB hex, or a list of them"))
            return None
        rel["colors"] = [c]
    elif isinstance(colors, list) and colors and all(
            isinstance(c, str) for c in colors):
        resolved_colors = []
        for c in colors:
            rc = resolve_color(c)
            if rc is None:
                errors.append(err("INVALID_SCHEMA", f"unknown motion colors colour '{c}'", field="motion.colors", clip_id=mid,
                                  fix="set colors to colour names or #RRGGBB hex values"))
                return None
            resolved_colors.append(rc)
        rel["colors"] = resolved_colors
    else:
        errors.append(err("INVALID_SCHEMA", "motion colors must be a colour or a list of colours", field="motion.colors", clip_id=mid,
                          fix="set colors to e.g. 'yellow' or ['yellow', 'cyan']"))
        return None
    rel["label_size"] = _num("label_size", lo=8, hi=120, default=28)
    rel["label_color"] = _color("label_color", "white")
    rel["stagger"] = _num("stagger", lo=0.0, hi=0.5, default=0.12)
    rel["grow"] = _num("grow", lo=0.1, hi=1.0, default=0.5)
    # Glassmorphism (v1.45.0, R7): a frosted backdrop-blur
    # card behind the chart, ON by default. Set
    # backdrop_blur: false for a bare chart with no card.
    if "backdrop_blur" in el and not isinstance(el["backdrop_blur"], bool):
        errors.append(err("INVALID_SCHEMA", "motion backdrop_blur must be true or false", field="motion.backdrop_blur", clip_id=mid,
                          fix="set backdrop_blur to true for a frosted card, or false for a bare chart"))
        ok = False
    rel["backdrop_blur"] = bool(el.get("backdrop_blur", True))
    # Growth easing (v1.45.0, R6): spring by default — the
    # bars/donut segments grow with a spring overshoot
    # instead of the old smoothstep. An explicit easing wins.
    from .motion import EASINGS as _EASINGS
    _ce = el.get("easing", "spring")
    if _ce not in _EASINGS:
        errors.append(err("INVALID_SCHEMA", f"unknown motion easing '{_ce}'; valid easings are: " + ", ".join(_EASINGS), field="motion.easing", clip_id=mid,
                          fix="set easing to one of: " + ", ".join(_EASINGS)))
        ok = False
    else:
        rel["easing"] = _ce
    if kind == "comparison_cards":
        # Cards have no grid: the body is a solid rounded rect.
        if "grid" in el:
            errors.append(err("INVALID_SCHEMA", "motion comparison_cards does not take grid (cards have no grid lines)", field="motion.grid", clip_id=mid,
                              fix="remove grid, or use kind 'bars' or 'line_chart'"))
            ok = False
        rel["grid"] = False
        rel["value_size"] = _num("value_size", lo=8, hi=160, default=44)
        rel["card_gap"] = _num("card_gap", lo=0.0, hi=0.3, default=0.04)
    else:
        if "grid" in el and not isinstance(el["grid"], bool):
            errors.append(err("INVALID_SCHEMA", "motion grid must be true or false", field="motion.grid", clip_id=mid,
                              fix="set grid to true for faint grid lines, or false to hide them"))
            ok = False
        rel["grid"] = bool(el.get("grid", True))
    if kind == "bars":
        rel["bar_gap"] = _num("bar_gap", lo=0.0, hi=0.8, default=0.2)
    if kind == "line_chart":
        rel["color"] = _color("color", "white")
        rel["head_color"] = _color("head_color", "#facc15")
        rel["line_width"] = _num("line_width", lo=1, hi=20, default=4)
        if "show_points" in el and not isinstance(el["show_points"], bool):
            errors.append(err("INVALID_SCHEMA", "motion show_points must be true or false", field="motion.show_points", clip_id=mid,
                              fix="set show_points to true for dots on reached points, or false to hide them"))
            ok = False
        rel["show_points"] = bool(el.get("show_points", True))
    if not ok:
        return None
    # Stagger must let every bar/segment/card finish inside the
    # window.
    n = n_cards if kind == "comparison_cards" else len(clean_values)
    if (n - 1) * rel["stagger"] + rel["grow"] > 1.0 + 1e-9:
        errors.append(err("INVALID_SCHEMA", f"motion stagger/grow leaves the last item unfinished: {(n - 1) * rel['stagger'] + rel['grow']:.2f} > 1", field="motion.stagger", clip_id=mid,
                          fix="lower stagger or grow so (values-1)*stagger + grow <= 1, e.g. stagger 0.12 and grow 0.5"))
        return None
    if not _validate_motion_timing(el, rel, kind, mid, start, end,
                                  errors):
        return None
    try:
        bx, by, bw, bh = _chart_bbox(rel)
    except Exception as exc:
        errors.append(err("INVALID_SCHEMA", f"motion chart geometry failed: {exc}", field="motion", clip_id=mid,
                          fix="check the chart geometry (x, y, w/h or radius)"))
        return None
    if bx < 0 or by < 0 or bx + bw > width or by + bh > height:
        errors.append(err("INVALID_SCHEMA", f"motion {kind} '{mid}' bbox {bx},{by} {bw}x{bh} is outside the {width}x{height} frame", field="motion", clip_id=mid,
                          fix="move the chart inside the frame (charts do not slide in from off-screen)"))
        return None
    rel["_bbox"] = (bx, by, bw, bh)
    rel["_frames"] = max(1, int(round((end - start) * fps)))
    return rel


def _validate_motion_counter(el, mid, start, end, width, height,
                               errors):
    """Validate one counter element (R3).

    Returns the resolved dict, or None (errors appended). The
    counter produces no video: render.py emits a drawtext
    %{eif} expression for it, so x/y are the CENTER of the
    counter text. from/to must be >= 0 (floor/trunc sign edge);
    decimals is 0 or 1 (eif formats integers only on this
    build). Affixes reject filter-breaking characters — a
    literal Latin % is impossible on this build (%% gives
    "Stray %" inline AND in textfile), so use \u066a (\u066a,
    the Arabic percent sign).
    """
    # A counter is a live number, not a video: draw/fade do not
    # apply (see _validate_motion_timing, which rejects them for
    # counters too — this early check keeps the message specific
    # to the counter instead of the generic raster one).
    for _f in ("draw", "fade_out", "fade_start"):
        if _f in el:
            errors.append(err("INVALID_SCHEMA", f"motion counter does not take {_f} (it is a live number, not a video)", field=f"motion.{_f}", clip_id=mid,
                              fix=f"remove {_f}, or use a raster kind"))
            return None
    from .motion import EASINGS

    rel = {"id": mid, "kind": "counter", "start": start, "end": end}
    ok = True

    def _num(name, lo=None, hi=None, default=None, required=False):
        nonlocal ok
        if name not in el:
            if required:
                errors.append(err("INVALID_SCHEMA", f"motion counter needs a numeric {name}", field=f"motion.{name}", clip_id=mid,
                                  fix=f"set {name} to a number"))
                ok = False
            return default
        v = el[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be a number", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to a number"))
            ok = False
            return default
        v = float(v)
        if lo is not None and not v >= lo:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be >= {lo}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {lo} or more"))
            ok = False
            return default
        if hi is not None and not v <= hi:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be <= {hi}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {hi} or less"))
            ok = False
            return default
        return v

    x = _num("x", required=True)
    y = _num("y", required=True)
    if x is not None:
        rel["x"] = x
    if y is not None:
        rel["y"] = y
    if x is not None and not 0 <= x <= width:
        errors.append(err("INVALID_SCHEMA", f"motion counter x {x} is outside the {width}px frame width", field="motion.x", clip_id=mid,
                          fix=f"set x between 0 and {width}"))
        ok = False
    if y is not None and not 0 <= y <= height:
        errors.append(err("INVALID_SCHEMA", f"motion counter y {y} is outside the {height}px frame height", field="motion.y", clip_id=mid,
                          fix=f"set y between 0 and {height}"))
        ok = False
    rel["from"] = _num("from", lo=0.0, required=True)
    rel["to"] = _num("to", lo=0.0, required=True)
    decimals = _num("decimals", lo=0, hi=1, default=0)
    if decimals is not None and decimals != int(decimals):
        errors.append(err("INVALID_SCHEMA", "motion decimals must be 0 or 1", field="motion.decimals", clip_id=mid,
                          fix="set decimals to 0 for whole numbers or 1 for one decimal place"))
        ok = False
    rel["decimals"] = int(decimals) if decimals is not None else 0
    # Counter easing (v1.45.0, R6): spring by default — the
    # number counts up with a spring overshoot instead of the
    # old linear ramp. An explicit easing wins.
    easing = el.get("easing", "spring")
    if easing not in EASINGS:
        errors.append(err("INVALID_SCHEMA", f"unknown motion easing '{easing}'; valid easings are: " + ", ".join(EASINGS), field="motion.easing", clip_id=mid,
                          fix="set easing to one of: " + ", ".join(EASINGS)))
        ok = False
    else:
        rel["easing"] = easing
    size = _num("size", lo=8, hi=120, default=48)
    if size is not None:
        rel["size"] = size
    color = el.get("color", "white")
    if not (isinstance(color, str) and (color in TEXT_COLORS
                                        or re.fullmatch(r"#[0-9a-fA-F]{6}", color))):
        errors.append(err("INVALID_SCHEMA", "motion counter color must be a #RRGGBB hex value or one of: " + ", ".join(sorted(TEXT_COLORS)), field="motion.color", clip_id=mid,
                          fix="set color to e.g. 'white' or '#facc15'"))
        ok = False
    else:
        rel["color"] = color
    if "box" in el and not isinstance(el["box"], bool):
        errors.append(err("INVALID_SCHEMA", "motion box must be true or false", field="motion.box", clip_id=mid,
                          fix="set box to true for a backdrop box, or false for bare digits"))
        ok = False
    rel["box"] = bool(el.get("box", True))
    for aname in ("prefix", "suffix"):
        a = el.get(aname, "")
        if not isinstance(a, str):
            errors.append(err("INVALID_SCHEMA", f"motion {aname} must be a string", field=f"motion.{aname}", clip_id=mid,
                              fix=f"set {aname} to a string, e.g. '\\u066a' for a percent suffix"))
            ok = False
            continue
        bad = [ch for ch in a if ch in "%:'\"\\,;[]" or ord(ch) < 32]
        if bad:
            errors.append(err("INVALID_SCHEMA", f"motion {aname} contains a filter-breaking character {bad[0]!r}", field=f"motion.{aname}", clip_id=mid,
                              fix="remove that character; for a percent sign use \u066a (\u066a), not %"))
            ok = False
            continue
        rel[aname] = a
    if not ok:
        return None
    return rel


def _validate_motion_lower_third(el, mid, start, end, width, height,
                                 fps, errors):
    """Validate one lower_third element (R3).

    Returns the resolved dict, or None (errors appended). The
    card's rest position (x, y, w, h) must sit fully inside the
    frame; the slide-in bbox (from fully off-screen left) uses
    the same one-bbox-size extension rule as shapes. The text
    line is a derived drawtext item at render time, so it keeps
    the BiDi-verified path.
    """
    from .diagrams import resolve_color
    from .motion import lower_third_bbox

    rel = {"id": mid, "kind": "lower_third", "start": start, "end": end}
    ok = True

    def _num(name, lo=None, hi=None, default=None, required=False):
        nonlocal ok
        if name not in el:
            if required:
                errors.append(err("INVALID_SCHEMA", f"motion lower_third needs a numeric {name}", field=f"motion.{name}", clip_id=mid,
                                  fix=f"set {name} to a number"))
                ok = False
            return default
        v = el[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be a number", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to a number"))
            ok = False
            return default
        v = float(v)
        if lo is not None and not v >= lo:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be >= {lo}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {lo} or more"))
            ok = False
            return default
        if hi is not None and not v <= hi:
            errors.append(err("INVALID_SCHEMA", f"motion {name} must be <= {hi}", field=f"motion.{name}", clip_id=mid,
                              fix=f"set {name} to {hi} or less"))
            ok = False
            return default
        return v

    x = _num("x", required=True)
    y = _num("y", required=True)
    # w is optional since v1.45.0 (R4c-4, Defect 4): when it is
    # omitted the card is sized to its text (see the auto-width
    # block below), instead of the caller guessing a width and
    # leaving a wide empty card for a short word. An explicit w
    # is kept as-is (backward compatible).
    w = _num("w", lo=1, required=False)
    h = _num("h", lo=1, required=True)
    if x is not None:
        rel["x"] = x
    if y is not None:
        rel["y"] = y
    if w is not None:
        rel["w"] = w
    if h is not None:
        rel["h"] = h
    text = el.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append(err("INVALID_SCHEMA", "motion lower_third needs a non-empty text string", field="motion.text", clip_id=mid,
                          fix="set text to the line to show, e.g. 'السباق نحو الذكاء الاصطناعي'"))
        ok = False
    else:
        rel["text"] = text
    size = _num("size", lo=8, hi=120, default=36)
    if size is not None:
        rel["size"] = size
    color = el.get("color", "white")
    if not (isinstance(color, str) and (color in TEXT_COLORS
                                        or re.fullmatch(r"#[0-9a-fA-F]{6}", color))):
        errors.append(err("INVALID_SCHEMA", "motion lower_third color must be a #RRGGBB hex value or one of: " + ", ".join(sorted(TEXT_COLORS)), field="motion.color", clip_id=mid,
                          fix="set color to e.g. 'white' or '#facc15'"))
        ok = False
    else:
        rel["color"] = color
    font = el.get("font")
    if font is not None:
        if not isinstance(font, str) or font not in BUNDLED_FONTS:
            errors.append(err("INVALID_SCHEMA", f"unknown motion font '{font}'; valid fonts are: " + ", ".join(sorted(BUNDLED_FONTS)), field="motion.font", clip_id=mid,
                              fix="set font to one of: " + ", ".join(sorted(BUNDLED_FONTS))))
            ok = False
        else:
            rel["font"] = font
    # Auto card width (v1.45.0, R4c-4, Defect 4): when w is
    # omitted, size the card to the text — card_w =
    # clamp(text_w + 80, 220, 0.82 * W) — measured with the
    # shaped (HarfBuzz) width at the size the text will
    # render at (the style's size when a style is given, else
    # the element's size). The 80px covers the 24px text
    # padding on each side plus the accent bar; 220px is the
    # minimum readable card; 0.82*W keeps it inside the
    # frame. If the auto width would run past the frame's
    # right edge, the card shifts left to fit (only in the
    # auto case — an explicit w keeps its x and is checked
    # by the frame test below). _measure_text_width lives in
    # render (the HarfBuzz loader); the lazy import keeps
    # validate free of a module-level render import (render
    # imports validate), and it is safe here because both
    # modules are fully loaded by validation time.
    if w is None and ok and text is not None and size is not None:
        _msize = size
        _style = el.get("style")
        if isinstance(_style, str) and _style in TEXT_STYLES:
            _msize = TEXT_STYLES[_style]["size_frac"] * height
        _font_file = None
        if isinstance(font, str) and font in BUNDLED_FONTS:
            _font_file = find_bundled_font(font)
        if _font_file is None:
            _font_file = find_font_for_text(text)
        from .render import _measure_text_width
        _tw = _measure_text_width(text, _font_file, _msize)
        w = max(220.0, min(float(_tw) + 80.0, 0.82 * width))
        if x is not None and x + w > width:
            x = max(0.0, width - w)
            rel["x"] = x
        rel["w"] = w
    # Accent bar colour (v1.45.0, R4c-4): an optional vertical
    # accent bar on the card edge — left for Latin text, right
    # for Arabic (the render side reads contains_arabic).
    # Absent by default: the 1.44.0 lower third has no accent
    # bar, so existing projects stay byte-identical.
    if el.get("accent_color") is not None:
        _accent = resolve_color(el.get("accent_color"))
        if _accent is None:
            errors.append(err("INVALID_SCHEMA", f"unknown motion accent_color colour '{el.get('accent_color')}'", field="motion.accent_color", clip_id=mid,
                              fix="set accent_color to a colour name or #RRGGBB hex"))
            ok = False
        else:
            rel["accent_color"] = _accent
    card_color = resolve_color(el.get("card_color", "black"))
    if card_color is None:
        errors.append(err("INVALID_SCHEMA", f"unknown motion card_color colour '{el.get('card_color')}'", field="motion.card_color", clip_id=mid,
                          fix="set card_color to a colour name or #RRGGBB hex"))
        ok = False
    else:
        rel["card_color"] = card_color
    rel["card_opacity"] = _num("card_opacity", lo=0.0, hi=1.0,
                               default=0.75)
    if w is not None and h is not None:
        rel["corner_radius"] = _num("corner_radius", lo=0.0,
                                    hi=min(w, h) / 2.0, default=16)
    else:
        rel["corner_radius"] = _num("corner_radius", lo=0.0, default=16)
    enter = _num("enter", lo=0.0, hi=2.0, default=0.6)
    exit_ = _num("exit", lo=0.0, hi=2.0, default=0.6)
    if enter is not None:
        rel["enter"] = enter
    if exit_ is not None:
        rel["exit"] = exit_
    if enter is not None and exit_ is not None \
            and enter + exit_ > end - start + 1e-9:
        errors.append(err("INVALID_SCHEMA", f"motion enter+exit {enter + exit_} exceeds the {end - start}s window", field="motion.enter", clip_id=mid,
                          fix="shorten enter/exit so enter + exit fits inside the window"))
        ok = False
    if not ok:
        return None
    if not (0 <= x <= width - w and 0 <= y <= height - h):
        errors.append(err("INVALID_SCHEMA", f"motion lower_third '{mid}' card {x},{y} {w}x{h} is outside the {width}x{height} frame", field="motion", clip_id=mid,
                          fix="move the card fully inside the frame (it slides in from off-screen on its own)"))
        return None
    try:
        bx, by, bw, bh = lower_third_bbox(rel)
    except Exception as exc:
        errors.append(err("INVALID_SCHEMA", f"motion lower_third geometry failed: {exc}", field="motion", clip_id=mid,
                          fix="check the card geometry (x, y, w, h)"))
        return None
    if (bx + bw <= 0 or by + bh <= 0 or bx >= width or by >= height
            or bx < -bw or by < -bh or bx + bw > width + bw
            or by + bh > height + bh):
        errors.append(err("INVALID_SCHEMA", f"motion lower_third '{mid}' bbox {bx},{by} {bw}x{bh} is outside the {width}x{height} frame", field="motion", clip_id=mid,
                          fix="move the card inside the frame"))
        return None
    if not _validate_motion_timing(el, rel, "lower_third", mid,
                                  start, end, errors):
        return None
    rel["_bbox"] = (bx, by, bw, bh)
    rel["_frames"] = max(1, int(round((end - start) * fps)))
    return rel


def _motion_style_passthrough(el, rel, mid, errors):
    """Shared style passthrough for motion elements (v1.44.0,
    F4d): chart/counter labels take `label_style`, lower thirds
    take `style` — both must be TEXT_STYLES names and ride on
    the resolved element for the renderer's shared style
    resolver. An unknown name is an error, never a silent
    fallback.
    """
    for key in ("label_style", "style"):
        if el.get(key) is not None:
            if el[key] not in TEXT_STYLES:
                errors.append(err("INVALID_SCHEMA", f"motion {key} must be one of: " + ", ".join(sorted(TEXT_STYLES)), field="motion." + key, clip_id=mid,
                                  fix="set " + key + " to one of: " + ", ".join(sorted(TEXT_STYLES)) + ", or remove it"))
            else:
                rel[key] = el[key]


def _validate_motion(project, total, width, height, fps, overlay_ids,
                     diagram_ids, errors):
    """Validate the project-level `motion` list (v1.43.0).

    Returns resolved motion elements for info["motion"]. All
    seven kinds are landed (shape in R1, bars/donut/pie/line_chart
    in R2, counter and lower_third in R3) — an unknown kind is an
    error, never a silent drop. Keyframe times are ABSOLUTE
    timeline seconds inside the element window; x/y are the shape
    CENTER.
    """
    from .diagrams import resolve_color
    from .motion import EASINGS, MOTION_KINDS, shape_bbox

    resolved = []
    raw = project.get("motion")
    if raw is None:
        return resolved
    if not isinstance(raw, list):
        errors.append(err("INVALID_SCHEMA", "motion must be a list", field="motion",
                          fix="give motion as a list of motion elements, or remove motion"))
        return resolved
    seen = set()
    for el in raw:
        if not isinstance(el, dict):
            errors.append(err("INVALID_SCHEMA", "each motion element must be an object", field="motion",
                              fix="make each motion element an object with id, kind, start and end"))
            continue
        mid = el.get("id")
        if not isinstance(mid, str) or not mid.strip():
            errors.append(err("INVALID_SCHEMA", "each motion element needs a non-empty string id", field="motion.id",
                              fix="give every motion element an id, e.g. 'dot1'"))
            continue
        if mid in seen or mid in overlay_ids or mid in diagram_ids:
            errors.append(err("INVALID_SCHEMA", f"duplicate motion id {mid}", field="motion.id", clip_id=mid,
                              fix=f"rename one of the motion elements/overlays/diagrams called {mid}"))
            continue
        seen.add(mid)
        kind = el.get("kind")
        from .plugins import is_known_motion_kind, get_motion_plugin
        if not is_known_motion_kind(kind):
            errors.append(err("INVALID_SCHEMA", f"unknown motion kind '{kind}'; valid kinds are: " + ", ".join(sorted(MOTION_KINDS)), field="motion.kind", clip_id=mid,
                              fix="set kind to one of: " + ", ".join(sorted(MOTION_KINDS))))
            continue
        try:
            start = float(el.get("start", 0))
            end = float(el.get("end", total))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "motion start/end must be numbers", field="motion", clip_id=mid,
                              fix="set start/end to timeline seconds, e.g. start 2, end 10"))
            continue
        if start < 0 or end <= start or end > total + 0.05:
            errors.append(err("CLIP_OUT_OF_RANGE", f"motion range {start}..{end} outside timeline {total:.2f}s", field="motion", clip_id=mid,
                              fix=f"keep the motion window inside 0..{total:.2f} with end after start"))
            continue
        if kind in ("bars", "donut", "pie", "line_chart",
                    "comparison_cards"):
            rel = _validate_motion_chart(el, kind, mid, start, end,
                                         width, height, fps, errors)
            if rel is not None:
                _motion_style_passthrough(el, rel, mid, errors)
                resolved.append(rel)
            continue
        if kind == "counter":
            rel = _validate_motion_counter(el, mid, start, end,
                                           width, height, errors)
            if rel is not None:
                _motion_style_passthrough(el, rel, mid, errors)
                resolved.append(rel)
            continue
        if kind == "lower_third":
            rel = _validate_motion_lower_third(el, mid, start, end,
                                               width, height, fps,
                                               errors)
            if rel is not None:
                _motion_style_passthrough(el, rel, mid, errors)
                resolved.append(rel)
            continue
        if kind != "shape":
            plugin = get_motion_plugin(kind)
            if plugin is not None:
                rel = plugin.validate(el, mid, start, end, width, height, fps, errors)
                if rel is not None:
                    _motion_style_passthrough(el, rel, mid, errors)
                    resolved.append(rel)
                continue
            # Later rounds land the remaining kinds. Until then
            # the kind is known but unimplemented, so it fails
            # loudly instead of validating into a drop.
            errors.append(err("INVALID_SCHEMA", f"motion kind '{kind}' is not implemented yet", field="motion.kind", clip_id=mid,
                              fix="use kind 'shape', 'bars', 'donut', 'pie', 'line_chart', 'comparison_cards', 'counter' or 'lower_third' for now, or wait for the next motion round"))
            continue
        rel = {"id": mid, "kind": "shape", "start": start, "end": end}
        shape = el.get("shape", "rect")
        if shape not in ("rect", "circle", "ellipse", "line", "polygon"):
            errors.append(err("INVALID_SCHEMA", f"unknown shape '{shape}'; valid shapes are: rect, circle, ellipse, line, polygon", field="motion.shape", clip_id=mid,
                              fix="set shape to rect, circle, ellipse, line or polygon"))
            continue
        rel["shape"] = shape
        # Element-level defaults; keyframes override per keyframe.
        defaults = {}
        ok = True
        for fname, lo, lo_exclusive, hi in (
                ("x", None, False, None), ("y", None, False, None),
                ("scale", 0.0, True, None), ("rotation", None, False, None),
                ("opacity", 0.0, False, 1.0)):
            if fname in el:
                try:
                    v = float(el[fname])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"motion {fname} must be a number", field=f"motion.{fname}", clip_id=mid,
                                      fix=f"set {fname} to a number, or remove it"))
                    ok = False
                    continue
                if isinstance(el[fname], bool):
                    errors.append(err("INVALID_SCHEMA", f"motion {fname} must be a number", field=f"motion.{fname}", clip_id=mid,
                                      fix=f"set {fname} to a number, or remove it"))
                    ok = False
                    continue
                if lo is not None and (not v > lo if lo_exclusive else not v >= lo):
                    errors.append(err("INVALID_SCHEMA", f"motion {fname} must be >= {lo}" if not lo_exclusive else f"motion {fname} must be > {lo}", field=f"motion.{fname}", clip_id=mid,
                                      fix=f"set {fname} above {lo}" if lo_exclusive else f"set {fname} between {lo} and 1"))
                    ok = False
                    continue
                if hi is not None and not v <= hi:
                    errors.append(err("INVALID_SCHEMA", f"motion {fname} must be 0..1", field=f"motion.{fname}", clip_id=mid,
                                      fix=f"set {fname} between 0 and 1"))
                    ok = False
                    continue
                defaults[fname] = v
        if not ok:
            continue
        rel.update(defaults)
        # Geometry, per shape kind. Fields that belong to another
        # kind are rejected, not silently ignored (1.34.0 rule).
        if shape in ("rect", "ellipse"):
            for gname in ("w", "h"):
                try:
                    gv = float(el[gname])
                except (KeyError, TypeError, ValueError):
                    # v1.45.0, R1: a missing w/h raised KeyError
                    # and crashed the validator; it is a schema
                    # error like a non-numeric one.
                    errors.append(err("INVALID_SCHEMA", f"motion {shape} needs a numeric {gname}", field=f"motion.{gname}", clip_id=mid,
                                      fix=f"set {gname} to a pixel size above 0, e.g. 200"))
                    ok = False
                    continue
                if not gv > 0:
                    errors.append(err("INVALID_SCHEMA", f"motion {gname} must be > 0", field=f"motion.{gname}", clip_id=mid,
                                      fix=f"set {gname} above 0"))
                    ok = False
                    continue
                rel[gname] = gv
            if "radius" in el:
                errors.append(err("INVALID_SCHEMA", f"motion {shape} uses w/h, not radius", field="motion.radius", clip_id=mid,
                                  fix="remove radius, or use shape 'circle'"))
                ok = False
            if "points" in el:
                errors.append(err("INVALID_SCHEMA", f"motion {shape} uses w/h, not points", field="motion.points", clip_id=mid,
                                  fix="remove points, or use shape 'polygon'/'line'"))
                ok = False
            if shape == "ellipse" and "corner_radius" in el:
                errors.append(err("INVALID_SCHEMA", "motion corner_radius only applies to rect shapes", field="motion.corner_radius", clip_id=mid,
                                  fix="remove corner_radius, or use shape 'rect'"))
                ok = False
        elif shape == "circle":
            try:
                rv = float(el["radius"])
            except (KeyError, TypeError, ValueError):
                # v1.45.0, R1: a missing radius raised KeyError
                # and crashed the validator instead of returning
                # INVALID_SCHEMA with a fix hint.
                errors.append(err("INVALID_SCHEMA", "motion circle needs a numeric radius", field="motion.radius", clip_id=mid,
                                  fix="set radius to a pixel size above 0, e.g. 60"))
                ok = False
            else:
                if not rv > 0:
                    errors.append(err("INVALID_SCHEMA", "motion radius must be > 0", field="motion.radius", clip_id=mid,
                                      fix="set radius above 0"))
                    ok = False
                else:
                    rel["radius"] = rv
            for gname in ("w", "h", "points", "corner_radius"):
                if gname in el:
                    errors.append(err("INVALID_SCHEMA", f"motion circle does not take {gname}", field=f"motion.{gname}", clip_id=mid,
                                      fix=f"remove {gname}"))
                    ok = False
        else:  # line / polygon: local points relative to the center.
            pts = el.get("points")
            if not isinstance(pts, list) or not pts:
                errors.append(err("INVALID_SCHEMA", f"motion {shape} needs a points list", field="motion.points", clip_id=mid,
                                  fix="give points as [[x, y], ...] relative to the shape center"))
                ok = False
            else:
                need = 2 if shape == "line" else 3
                if len(pts) < need:
                    errors.append(err("INVALID_SCHEMA", f"motion {shape} needs at least {need} points", field="motion.points", clip_id=mid,
                                      fix=f"give at least {need} [x, y] points"))
                    ok = False
                else:
                    clean = []
                    for pt in pts:
                        if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                                or isinstance(pt[0], bool) or isinstance(pt[1], bool)):
                            errors.append(err("INVALID_SCHEMA", "motion points must be [x, y] pairs of numbers", field="motion.points", clip_id=mid,
                                              fix="give each point as [x, y], e.g. [0, 0]"))
                            ok = False
                            break
                        try:
                            clean.append((float(pt[0]), float(pt[1])))
                        except (TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", "motion points must be [x, y] pairs of numbers", field="motion.points", clip_id=mid,
                                              fix="give each point as [x, y], e.g. [0, 0]"))
                            ok = False
                            break
                    if ok:
                        rel["points"] = clean
            for gname in ("w", "h", "radius", "corner_radius"):
                if gname in el:
                    errors.append(err("INVALID_SCHEMA", f"motion {shape} does not take {gname}", field=f"motion.{gname}", clip_id=mid,
                                      fix=f"remove {gname}"))
                    ok = False
        if shape == "rect" and "corner_radius" in el:
            try:
                cr = float(el["corner_radius"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "motion corner_radius must be a number", field="motion.corner_radius", clip_id=mid,
                                  fix="set corner_radius to 0 (square corners) or a radius in pixels"))
                ok = False
            else:
                cap = min(rel.get("w", 0), rel.get("h", 0)) / 2.0
                if not 0 <= cr <= cap:
                    errors.append(err("INVALID_SCHEMA", f"motion corner_radius must be 0..{cap:.0f} (half the smaller side)", field="motion.corner_radius", clip_id=mid,
                                      fix=f"set corner_radius between 0 and {cap:.0f}"))
                    ok = False
                else:
                    rel["corner_radius"] = cr
        if not ok:
            continue
        # Fill / stroke. At least one must be present, or the shape
        # is invisible. Colours resolve to RGB tuples here.
        fill = el.get("fill")
        stroke = el.get("stroke")
        if fill is None and stroke is None:
            errors.append(err("INVALID_SCHEMA", "motion shape needs a fill or a stroke (or both)", field="motion", clip_id=mid,
                              fix="set fill to a colour, e.g. 'yellow', or stroke to a colour"))
            continue
        if fill is not None:
            fc = resolve_color(fill)
            if fc is None:
                errors.append(err("INVALID_SCHEMA", f"unknown motion fill colour '{fill}'", field="motion.fill", clip_id=mid,
                                  fix="set fill to a colour name or #RRGGBB hex"))
                continue
            rel["fill"] = fc
        if stroke is not None:
            sc = resolve_color(stroke)
            if sc is None:
                errors.append(err("INVALID_SCHEMA", f"unknown motion stroke colour '{stroke}'", field="motion.stroke", clip_id=mid,
                                  fix="set stroke to a colour name or #RRGGBB hex"))
                continue
            rel["stroke"] = sc
            try:
                sw = float(el.get("stroke_width", 4))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "motion stroke_width must be a number", field="motion.stroke_width", clip_id=mid,
                                  fix="set stroke_width to a pixel width 0.5..50, e.g. 4"))
                continue
            if not 0.5 <= sw <= 50:
                errors.append(err("INVALID_SCHEMA", "motion stroke_width must be 0.5..50", field="motion.stroke_width", clip_id=mid,
                                  fix="set stroke_width between 0.5 and 50 pixels"))
                continue
            rel["stroke_width"] = sw
        elif "stroke_width" in el:
            errors.append(err("INVALID_SCHEMA", "motion stroke_width needs a stroke colour", field="motion.stroke_width", clip_id=mid,
                              fix="set stroke to a colour, or remove stroke_width"))
            continue
        # Keyframes: non-empty, absolute times, strictly increasing,
        # inside the window. Each keyframe overrides any subset of
        # x/y/scale/rotation/opacity; missing fields carry forward
        # from the element defaults, then from earlier keyframes.
        kfs = el.get("keyframes")
        if not isinstance(kfs, list) or not kfs:
            errors.append(err("INVALID_SCHEMA", "motion shape needs a non-empty keyframes list", field="motion.keyframes", clip_id=mid,
                              fix="give at least one keyframe, e.g. {'t': 0, 'x': 100, 'y': 100}"))
            continue
        norm = []
        prev_t = None
        cur = {"x": defaults.get("x"), "y": defaults.get("y"),
               "scale": defaults.get("scale", 1.0),
               "rotation": defaults.get("rotation", 0.0),
               "opacity": defaults.get("opacity", 1.0)}
        for kf in kfs:
            if not isinstance(kf, dict):
                errors.append(err("INVALID_SCHEMA", "each motion keyframe must be an object", field="motion.keyframes", clip_id=mid,
                                  fix="give each keyframe as {'t': seconds, 'x': ..., 'y': ...}"))
                ok = False
                break
            try:
                kt = float(kf["t"])
            except (KeyError, TypeError, ValueError):
                # v1.45.0, R1: a keyframe without t raised
                # KeyError and crashed the validator (same class
                # as the circle-radius crash above).
                errors.append(err("INVALID_SCHEMA", "motion keyframe t must be a number", field="motion.keyframes.t", clip_id=mid,
                                  fix="set t to an absolute timeline second inside the element window"))
                ok = False
                break
            if kt < start - 1e-9 or kt > end + 1e-9:
                errors.append(err("INVALID_SCHEMA", f"motion keyframe t={kt} is outside the element window {start}..{end}", field="motion.keyframes.t", clip_id=mid,
                                  fix=f"keep keyframe times inside {start}..{end}"))
                ok = False
                break
            if prev_t is not None and not kt > prev_t:
                errors.append(err("INVALID_SCHEMA", "motion keyframes must be in strictly increasing time order", field="motion.keyframes.t", clip_id=mid,
                                  fix="sort the keyframes by t, with no two keyframes at the same time"))
                ok = False
                break
            prev_t = kt
            nkf = {"t": kt}
            for fname in ("x", "y", "scale", "rotation", "opacity"):
                if fname in kf:
                    try:
                        fv = float(kf[fname])
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", f"motion keyframe {fname} must be a number", field=f"motion.keyframes.{fname}", clip_id=mid,
                                          fix=f"set {fname} to a number, or remove it from this keyframe"))
                        ok = False
                        break
                    if fname == "scale" and not fv > 0:
                        errors.append(err("INVALID_SCHEMA", "motion keyframe scale must be > 0", field="motion.keyframes.scale", clip_id=mid,
                                          fix="set scale above 0"))
                        ok = False
                        break
                    if fname == "opacity" and not 0.0 <= fv <= 1.0:
                        errors.append(err("INVALID_SCHEMA", "motion keyframe opacity must be 0..1", field="motion.keyframes.opacity", clip_id=mid,
                                          fix="set opacity between 0 and 1"))
                        ok = False
                        break
                    cur[fname] = fv
                nkf[fname] = cur[fname]
            if not ok:
                break
            if "easing" in kf:
                if kf["easing"] not in EASINGS:
                    errors.append(err("INVALID_SCHEMA", f"unknown motion easing '{kf['easing']}'; valid easings are: " + ", ".join(EASINGS), field="motion.keyframes.easing", clip_id=mid,
                                      fix="set easing to one of: " + ", ".join(EASINGS)))
                    ok = False
                    break
                nkf["easing"] = kf["easing"]
            norm.append(nkf)
        if not ok:
            continue
        if norm[0]["x"] is None or norm[0]["y"] is None:
            errors.append(err("INVALID_SCHEMA", "motion shape needs x and y: set them on the element or on the first keyframe", field="motion", clip_id=mid,
                              fix="set x and y on the element, e.g. {'x': 360, 'y': 640}, or on the first keyframe"))
            continue
        rel["keyframes"] = norm
        rel["x"] = norm[0]["x"]
        rel["y"] = norm[0]["y"]
        # Geometry bounds: the keyframe-union bbox must overlap the
        # frame, and may extend outside it by at most one bbox-size
        # per side (slide-in/out from off-screen). Anything further
        # out, or fully outside, is an authoring error, not a clip.
        if not _validate_motion_timing(el, rel, "shape", mid,
                                      start, end, errors):
            continue
        try:
            bx, by, bw, bh = shape_bbox(rel)
        except Exception as exc:
            errors.append(err("INVALID_SCHEMA", f"motion shape geometry failed: {exc}", field="motion", clip_id=mid,
                              fix="check the shape geometry and keyframes"))
            continue
        if (bx + bw <= 0 or by + bh <= 0 or bx >= width or by >= height
                or bx < -bw or by < -bh or bx + bw > width + bw
                or by + bh > height + bh):
            errors.append(err("INVALID_SCHEMA", f"motion shape '{mid}' bbox {bx},{by} {bw}x{bh} is outside the {width}x{height} frame", field="motion", clip_id=mid,
                              fix="move the shape and its keyframes inside the frame (a slide-in may start at most one shape-width off-screen)"))
            continue
        rel["_bbox"] = (bx, by, bw, bh)
        rel["_frames"] = max(1, int(round((end - start) * fps)))
        resolved.append(rel)
    return resolved


def _recompute_clip_starts(resolved):
    """Recompute clip_starts + total from a resolved clip list
    (v1.44.0, F1). Mirrors the clip loop exactly: an xfade
    transition overlaps by its transition_duration, every other
    transition (including custom ones) concatenates.
    """
    starts = []
    total = 0.0
    prev_start = 0.0
    prev_dur = 0.0
    _seen_joined = False
    for clip in resolved:
        if clip.get("track", 0) == 1:
            # Absolute start; does not advance the base timeline.
            starts.append(float(clip.get("start", 0.0)))
            continue
        if not _seen_joined:
            starts.append(0.0)
            _seen_joined = True
        elif clip.get("transition") in XFADE_TRANSITIONS:
            td = float(clip.get("transition_duration", 0.5))
            starts.append(prev_start + prev_dur - td)
        else:
            starts.append(prev_start + prev_dur)
        prev_start = starts[-1]
        prev_dur = float(clip["duration"])
    if _seen_joined:
        total = prev_start + prev_dur
    return starts, total


def _apply_beat_cut(project, resolved, clip_starts, total, errors, data_files):
    """Split clips at beat times (v1.44.0, F1). Returns
    (resolved, clip_starts, total, beat_cuts). beat_cut is
    {file, every=1, window=[start, end]}: every Nth beat (1 =
    every beat), window restricts the beats used (default the
    whole timeline). Segments shorter than 0.2s are never made
    (the cut is dropped, not the segment). The first segment
    keeps the original transition; later segments get
    transition "none" and ids "id#b1", "id#b2", ... Video
    segments re-trim by source time (linear speed and reverse
    are mapped; speed_curve, loop_count, camera, punch_in and
    freeze_at are rejected on a split clip because their timing
    is clip-global and would land wrong after the split).
    Ken Burns is allowed: each segment re-runs its sweep, which
    is a visible cut anyway. Total duration is unchanged.
    """
    spec = project.get("beat_cut")
    if spec is None:
        return resolved, clip_starts, total, []
    beats, bpm, resolved_path = _load_beats_data(spec, errors)
    if beats is None:
        return resolved, clip_starts, total, []
    data_files[resolved_path] = {"kind": "beats",
                                 "data": {"bpm": bpm, "beats": beats}}
    every = spec.get("every", 1)
    if isinstance(every, bool) or not isinstance(every, int):
        try:
            every = int(every)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "beat_cut.every must be an integer >= 1", field="beat_cut.every",
                              fix="set every to 1 (every beat), 2 (every other beat), or remove it"))
            return resolved, clip_starts, total, []
    if every < 1:
        errors.append(err("INVALID_SCHEMA", "beat_cut.every must be >= 1", field="beat_cut.every",
                          fix="set every to 1 (every beat), 2 (every other beat), or remove it"))
        return resolved, clip_starts, total, []
    window = spec.get("window")
    ws, we = 0.0, total
    if window is not None:
        try:
            ws = float(window[0]); we = float(window[1])
        except (TypeError, ValueError, IndexError):
            errors.append(err("INVALID_SCHEMA", "beat_cut.window must be [start, end] seconds", field="beat_cut.window",
                              fix="set window to [start, end] inside the timeline, or remove it"))
            return resolved, clip_starts, total, []
        if not (0 <= ws < we <= total + 0.05):
            errors.append(err("INVALID_SCHEMA", f"beat_cut.window {ws}..{we} outside timeline 0..{total:.2f}", field="beat_cut.window",
                              fix=f"keep window inside 0..{total:.2f} with end after start"))
            return resolved, clip_starts, total, []
    selected = [b for i, b in enumerate(beats)
                if i % every == 0 and ws <= b <= we]
    if not selected:
        errors.append(err("INVALID_SCHEMA", "beat_cut selects no beats inside the project timeline", field="beat_cut",
                          fix="check the beats file times against the project duration, or widen window"))
        return resolved, clip_starts, total, []
    new_resolved = []
    beat_cuts = []
    made_cuts = 0
    for clip, cs in zip(resolved, clip_starts):
        if clip.get("track", 0) == 1:
            # Track 1 clips are composited, not joined: beats
            # never split them.
            new_resolved.append(clip)
            continue
        dur = float(clip["duration"])
        ce = cs + dur
        cuts = [b for b in selected if cs < b < ce]
        # Min segment 0.2s: drop cuts too close to a boundary
        # or to the previous kept cut inside this clip.
        kept = []
        last = cs
        for b in cuts:
            if b - last >= 0.2 and ce - b >= 0.2:
                kept.append(b)
                last = b
        if not kept:
            new_resolved.append(clip)
            continue
        if (clip.get("speed_curve") is not None or clip.get("loop_count") is not None
                or clip.get("camera") or clip.get("punch_at") is not None
                or clip.get("freeze_at") is not None):
            errors.append(err("INVALID_SCHEMA", f"beat_cut cannot split clip '{clip.get('id')}' with speed_curve, loop_count, camera, punch_in or freeze_at (their timing is clip-global and would land wrong after the split)", field="beat_cut", clip_id=clip.get("id"),
                              fix="remove speed_curve/loop_count/camera/punch_in/freeze_at from that clip, or move the beat_cut window off it"))
            new_resolved.append(clip)
            continue
        is_video = "trim_in" in clip
        speed = float(clip.get("speed", 1.0))
        reverse = bool(clip.get("reverse"))
        trim_in = float(clip.get("trim_in", 0.0))
        trim_out = float(clip.get("trim_out", 0.0))
        bounds = [cs] + kept + [ce]
        for k in range(len(bounds) - 1):
            seg_start, seg_end = bounds[k], bounds[k + 1]
            seg = dict(clip)
            seg["duration"] = seg_end - seg_start
            if k > 0:
                seg["id"] = f"{clip.get('id')}#b{k}"
                seg["transition"] = "none"
                seg["transition_duration"] = 0.0
                # A beat cut is an edit point: later segments do
                # not inherit scene continuity, so each segment
                # restarts its motion instead of chaining across
                # the cut (which would hide the cut entirely).
                seg.pop("scene_id", None)
                beat_cuts.append({"time": seg_start, "clip_id": clip.get("id")})
                made_cuts += 1
            if is_video:
                if reverse:
                    seg["trim_in"] = trim_out - (seg_end - cs) * speed
                    seg["trim_out"] = trim_out - (seg_start - cs) * speed
                else:
                    seg["trim_in"] = trim_in + (seg_start - cs) * speed
                    seg["trim_out"] = trim_in + (seg_end - cs) * speed
            new_resolved.append(seg)
    if made_cuts == 0:
        errors.append(err("INVALID_SCHEMA", "beat_cut selects beats but none produce a valid split (all too close to a clip boundary)", field="beat_cut",
                          fix="use beats further from clip starts/ends, or a different every/window"))
        return resolved, clip_starts, total, []
    starts, new_total = _recompute_clip_starts(new_resolved)
    return new_resolved, starts, new_total, beat_cuts


def _validate_reframe_clip(clip, probe, trim_in, trim_out, errors, cid,
                           out_w=None, out_h=None):
    """Validate a clip-level reframe spec (v1.44.0, F3).
    Returns (reframe_data, resolved_path, warnings) or
    (None, None, []). The data keeps the AgentMedia shape plus
    the parsed target_aspect/target_ratio. Windows are the crop
    in SOURCE pixels and source seconds; the renderer samples
    them at the clip's source time (trim_in + timeline offset
    x speed). Outside the windows' range the renderer holds
    the nearest window (a warning, not an error).
    """
    spec = clip.get("reframe")
    data, resolved = _load_reframe_data(spec, errors, cid)
    if data is None:
        return None, None, []
    target = spec.get("target_aspect")
    try:
        tw, th = target.split(":")
        tw, th = float(tw), float(th)
        if tw <= 0 or th <= 0:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        errors.append(err("INVALID_SCHEMA", "reframe target_aspect must be 'W:H', e.g. '9:16'", field="clips.reframe.target_aspect", clip_id=cid,
                          fix="set target_aspect to '9:16' for a vertical cut, or '16:9' for a wide one"))
        return None, None, []
    pw, ph = probe.get("width"), probe.get("height")
    if abs(data["frame_size"]["width"] - pw) > 0.5 or abs(data["frame_size"]["height"] - ph) > 0.5:
        errors.append(err("INVALID_SCHEMA", f"reframe windows are for a {data['frame_size']['width']:.0f}x{data['frame_size']['height']:.0f} source but this clip is {pw}x{ph}", field="clips.reframe.windows_file", clip_id=cid,
                          fix="run AgentMedia analyze_reframe on this clip's source file"))
        return None, None, []
    target_ratio = tw / th
    for w in data["windows"]:
        if abs(w["w"] / w["h"] - target_ratio) > 0.02:
            errors.append(err("INVALID_SCHEMA", f"reframe crop {w['w']:.0f}x{w['h']:.0f} does not match target_aspect {target} (ratio {w['w'] / w['h']:.3f} vs {target_ratio:.3f})", field="clips.reframe.windows_file", clip_id=cid,
                              fix="run AgentMedia analyze_reframe with the same target_aspect"))
            return None, None, []
        if w["w"] != int(w["w"]) or w["h"] != int(w["h"]) or int(w["w"]) % 2 or int(w["h"]) % 2:
            errors.append(err("INVALID_SCHEMA", f"reframe crop {w['w']:.0f}x{w['h']:.0f} has odd dimensions (yuv420p needs even crops)", field="clips.reframe.windows_file", clip_id=cid,
                              fix="run AgentMedia analyze_reframe — it enforces even crops"))
            return None, None, []
        if w["x"] + w["w"] > pw + 0.5 or w["y"] + w["h"] > ph + 0.5:
            errors.append(err("INVALID_SCHEMA", f"reframe window at t={w['t']} extends past the source frame", field="clips.reframe.windows_file", clip_id=cid,
                              fix="run AgentMedia analyze_reframe on this clip's source file"))
            return None, None, []
    # One crop size for all windows (the AgentMedia
    # contract): the renderer's crop filter takes constant w/h
    # with per-frame x/y only.
    _sizes = {(w["w"], w["h"]) for w in data["windows"]}
    if len(_sizes) > 1:
        errors.append(err("INVALID_SCHEMA", "reframe windows must share one crop size (w/h)", field="clips.reframe.windows_file", clip_id=cid,
                          fix="run AgentMedia analyze_reframe — it emits one crop size for all windows"))
        return None, None, []
    if clip.get("camera"):
        errors.append(err("INVALID_SCHEMA", "reframe cannot combine with camera (both drive the per-frame crop)", field="clips.reframe", clip_id=cid,
                          fix="remove camera, or remove reframe, on this clip"))
        return None, None, []
    if clip.get("stabilize"):
        errors.append(err("INVALID_SCHEMA", "reframe cannot combine with stabilize (stabilisation re-times the frames the crop follows)", field="clips.reframe", clip_id=cid,
                          fix="remove stabilize, or remove reframe, on this clip"))
        return None, None, []
    if clip.get("fit") in BLUR_FITS:
        errors.append(err("INVALID_SCHEMA", "reframe cannot combine with fit blur (the blurred background owns the frame geometry)", field="clips.reframe", clip_id=cid,
                          fix="remove the blur fit, or remove reframe, on this clip"))
        return None, None, []
    if clip.get("freeze_at") is not None or clip.get("reverse") or clip.get("speed_curve") is not None:
        errors.append(err("INVALID_SCHEMA", "reframe cannot combine with freeze_at, reverse or speed_curve (they change the frame order/timing the windows follow)", field="clips.reframe", clip_id=cid,
                          fix="remove freeze_at/reverse/speed_curve, or remove reframe, on this clip"))
        return None, None, []
    warns = []
    wins = data["windows"]
    if wins[0]["t"] > trim_in + 0.05 or wins[-1]["t"] < trim_out - 0.05:
        warns.append({"code": "reframe_partial_coverage",
                      "message": f"clip '{cid}' reframe windows cover {wins[0]['t']:.2f}..{wins[-1]['t']:.2f}s but the trimmed clip runs {trim_in:.2f}..{trim_out:.2f}s; the nearest window is held outside the range",
                      "clip_id": cid})
    # Output-aspect mismatch is a warning, not an error: the
    # reframed frame is stretched to the output size, which is
    # what the project asked for.
    if out_w and out_h and abs(out_w / out_h - target_ratio) > 0.02:
        warns.append({"code": "reframe_output_aspect_mismatch",
                      "message": f"clip '{cid}' reframe target_aspect {target} (ratio {target_ratio:.3f}) does not match the output aspect {out_w}x{out_h} (ratio {out_w / out_h:.3f}); the reframed frame is stretched to the output size",
                      "clip_id": cid})
    data["target_aspect"] = target
    data["target_ratio"] = target_ratio
    return data, resolved, warns


def _validate_adjustments(project, total, errors):
    """Validate project-level `adjustments` (v1.44.0, F6b).
    Each adjustment is {start, end, grade?, effects?} with at
    least one of grade/effects; grade must be a GRADE_PRESETS
    name; effects reuse the clip effects vocabulary. Returns
    the resolved list (empty when absent).
    """
    raw = project.get("adjustments")
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(err("INVALID_SCHEMA", "adjustments must be a list", field="adjustments",
                          fix="give adjustments as a list of {start, end, grade} windows, or remove it"))
        return []
    resolved = []
    for adj in raw:
        if not isinstance(adj, dict):
            errors.append(err("INVALID_SCHEMA", "each adjustment must be an object", field="adjustments",
                              fix="make adjustments a list of objects with start, end and grade or effects"))
            continue
        try:
            start = float(adj.get("start", 0)); end = float(adj.get("end", total))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "adjustment start/end must be numbers", field="adjustments",
                              fix="set start/end to timeline seconds, e.g. start 2, end 4"))
            continue
        if start < 0 or end <= start or end > total + 0.05:
            errors.append(err("CLIP_OUT_OF_RANGE", f"adjustment range {start}..{end} outside timeline {total:.2f}s", field="adjustments",
                              fix=f"keep the adjustment window inside 0..{total:.2f} with end after start"))
            continue
        grade = adj.get("grade")
        if grade is not None and grade not in GRADE_PRESETS:
            errors.append(err("INVALID_SCHEMA", "adjustment grade must be one of: " + ", ".join(sorted(GRADE_PRESETS)), field="adjustments.grade",
                              fix="set grade to one of: " + ", ".join(sorted(GRADE_PRESETS)) + ", or remove it"))
            continue
        effects = adj.get("effects")
        if effects is not None and not isinstance(effects, dict):
            errors.append(err("INVALID_SCHEMA", "adjustment effects must be an object", field="adjustments.effects",
                              fix="set effects to an object like {blur: 2}, or remove it"))
            continue
        if grade is None and not effects:
            errors.append(err("INVALID_SCHEMA", "each adjustment needs a grade or effects (it must change something)", field="adjustments",
                              fix="set grade, e.g. 'noir', or effects, e.g. {blur: 2}"))
            continue
        if effects:
            _validate_effects(effects, errors, None, "adjustments.effects")
        resolved.append({"start": start, "end": end, "grade": grade, "effects": effects or {}})
    return resolved


def validate_project(project):
    """Validate a project dict.

    Returns (errors, info). info carries output dimensions, fps, the
    resolved clip durations and the total duration when computable.
    """
    errors = []
    # v1.44.0: AgentMedia data files referenced by beat_cut /
    # follow_track / reframe, keyed by resolved absolute path
    # for the renderer; and warnings collected during validation
    # (track / reframe partial coverage) merged into
    # info["warnings"] at assembly.
    _data_files = {}
    _extra_warnings = []
    if not isinstance(project, dict):
        return [err("INVALID_SCHEMA", "project must be a JSON object")], {}
    if project.get("version") != 1:
        errors.append(err("INVALID_SCHEMA", "version must be 1", field="version"))
    pid = project.get("id")
    if not isinstance(pid, str) or not pid:
        errors.append(err("INVALID_SCHEMA", "id must be a non-empty string", field="id"))
    output = project.get("output") or {}
    if not isinstance(output, dict):
        errors.append(err("INVALID_SCHEMA", "output must be an object", field="output"))
        output = {}
    width = output.get("width", 1280)
    height = output.get("height", 720)
    fps = output.get("fps", 30)
    if type(width) is not int or not 16 <= width <= MAX_DIMENSION:
        errors.append(err("INVALID_DIMENSION", "width must be an integer 16..3840 (not a float or boolean)", field="output.width"))
    if type(height) is not int or not 16 <= height <= MAX_DIMENSION:
        errors.append(err("INVALID_DIMENSION", "height must be an integer 16..3840 (not a float or boolean)", field="output.height"))
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not 1 <= fps <= MAX_FPS:
        errors.append(err("INVALID_DIMENSION", "fps must be a number 1..60", field="output.fps"))
    clips = project.get("clips")
    if not isinstance(clips, list) or not clips:
        errors.append(err("EMPTY_TIMELINE", "clips must be a non-empty list", field="clips"))
        return errors, {"width": width, "height": height, "fps": fps, "clips": [], "total_duration": 0}
    # Parallel probe warm-up (refactoring): probe every media file
    # in the project concurrently so the sequential validation below
    # hits the probe cache instead of spawning one ffprobe per file.
    # Warming is result-neutral (probe_media is deterministic per
    # file state; a file changed since warming re-probes on its
    # cache miss). Paths that do not resolve to files are skipped
    # here; validation below still reports them as FILE_NOT_FOUND.
    _warm_paths = []
    for _c in clips:
        if isinstance(_c, dict) and isinstance(_c.get("src"), str):
            _warm_paths.append(resolve_path(_c["src"]))
    for _ov in project.get("overlays") or []:
        if isinstance(_ov, dict) and isinstance(_ov.get("src"), str):
            _warm_paths.append(resolve_path(_ov["src"]))
    for _tr in project.get("audio") or []:
        if isinstance(_tr, dict) and isinstance(_tr.get("src"), str):
            _warm_paths.append(resolve_path(_tr["src"]))
    if isinstance(project.get("captions_file"), str):
        _warm_paths.append(resolve_path(project["captions_file"]))
    _warm_paths = [p for p in _warm_paths if os.path.isfile(p)]
    if _warm_paths:
        probe_files_parallel(_warm_paths)
    # Path Sandbox Validation (refactoring, opt-in): when
    # AGENTCUT_SANDBOX=1, media outside the allowed roots
    # (project dir + cwd + AgentCut base + AGENTCUT_MEDIA_DIRS)
    # is rejected. Default off, so existing projects whose media
    # lives elsewhere under ~/workspace keep validating; see
    # sandbox_roots / check_path_sandbox for the exact policy.
    _sandbox_on = sandbox_enabled()
    _sandbox_roots = sandbox_roots() if _sandbox_on else None
    # The Latin default font (may be None); per-text font requirements
    # are checked after captions are merged, below. The old blanket
    # check ("any text needs DejaVu") was wrong in both directions:
    # an Arabic-only project failed on a machine with only Noto fonts
    # even though it could render, while an Arabic project with only
    # DejaVu passed validation and then rendered tofu boxes.
    needs_font = bool(project.get("texts") or project.get("captions")
                      or project.get("captions_file"))
    font = find_font() if needs_font else None
    resolved = []
    total = 0.0
    seen_ids = set()
    prev_duration = None
    _prev_start0 = 0.0
    _prev_dur0 = 0.0
    clip_starts = []
    for clip in clips:
        if not isinstance(clip, dict):
            errors.append(err("INVALID_SCHEMA", "each clip must be an object", field="clips"))
            continue
        cid = clip.get("id")
        if not isinstance(cid, str) or not cid:
            errors.append(err("INVALID_SCHEMA", "each clip needs a non-empty string id", field="clips.id"))
            continue
        if cid in seen_ids:
            errors.append(err("INVALID_SCHEMA", f"duplicate clip id {cid}", field="clips.id", clip_id=cid))
        seen_ids.add(cid)
        # Multi-track (v1.44.0, F6a): track 0 is the base
        # timeline, track 1 is a second video track composited
        # over it. track must be the integer 0 or 1 (a bool is
        # not an int here -- isinstance(True, int) is True).
        _track = clip.get("track", 0)
        if isinstance(_track, bool) or not isinstance(_track, int) or _track not in (0, 1):
            errors.append(err("INVALID_SCHEMA", "clip track must be 0 (base timeline) or 1 (second track)", field="clips.track", clip_id=cid,
                              fix="set track to 0 or 1, or remove it (0 is the default)"))
            _track = 0
        _is_t1 = _track == 1
        _t1_start = 0.0
        if _is_t1:
            _raw_start = clip.get("start")
            if isinstance(_raw_start, bool) or not isinstance(_raw_start, (int, float)):
                errors.append(err("INVALID_SCHEMA", "a track 1 clip needs a numeric start (timeline seconds)", field="clips.start", clip_id=cid,
                                  fix="set start to the timeline second the clip appears at, e.g. start 2"))
            else:
                _t1_start = float(_raw_start)
                if _t1_start < 0:
                    errors.append(err("INVALID_SCHEMA", "track 1 clip start must be >= 0", field="clips.start", clip_id=cid,
                                      fix="set start to 0 or later"))
                    _t1_start = 0.0
            if "volume" in clip:
                try:
                    _t1_vol = float(clip["volume"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "track 1 clip volume must be a number", field="clips.volume", clip_id=cid,
                                      fix="set volume between 0 (muted) and 2, or remove it (1.0 is the default)"))
                    _t1_vol = None
                if _t1_vol is not None and not 0.0 <= _t1_vol <= 2.0:
                    errors.append(err("INVALID_SCHEMA", "track 1 clip volume must be 0..2", field="clips.volume", clip_id=cid,
                                      fix="set volume between 0 (muted) and 2, or remove it (1.0 is the default)"))
        # v1.40.0: scene_id groups sequential clips for motion
        # continuity (see the continuity pass after the clip
        # loop). Same character rule as clip ids.
        _sid = clip.get("scene_id")
        if _sid is not None and (not isinstance(_sid, str) or not _sid
                                 or not re.fullmatch(r"[A-Za-z0-9_-]+", _sid)):
            errors.append(err("INVALID_SCHEMA", "clip scene_id must be a non-empty string of letters, digits, - or _", field="clips.scene_id", clip_id=cid,
                              fix="set scene_id to e.g. 'scene-1', or remove it"))
        kind = clip.get("kind")
        src = clip.get("src")
        if kind not in ("video", "image"):
            errors.append(err("INVALID_SCHEMA", "clip kind must be 'video' or 'image'", field="clips.kind", clip_id=cid))
            continue
        if not isinstance(src, str) or not src:
            errors.append(err("INVALID_SCHEMA", "clip src must be a non-empty path", field="clips.src", clip_id=cid))
            continue
        src = resolve_path(src)
        if not os.path.isfile(src):
            errors.append(err("FILE_NOT_FOUND", f"clip file not found: {src}", field="clips.src", clip_id=cid))
            continue
        if _sandbox_on and not check_path_sandbox(src, roots=_sandbox_roots):
            errors.append(err("PATH_OUTSIDE_SANDBOX", f"clip file outside the allowed media roots: {src}", field="clips.src", clip_id=cid,
                              fix="move the file under the project directory, the working directory, or a directory listed in AGENTCUT_MEDIA_DIRS"))
            continue
        if kind == "video":
            # A video clip's duration comes from trim_in/trim_out
            # (and speed), never from a "duration" key: accepting
            # one would silently ignore the author's intent (1.34.0).
            if "duration" in clip:
                errors.append(err("INVALID_SCHEMA", "video clips must not set duration (duration comes from trim_in/trim_out and speed)", field="clips.duration", clip_id=cid,
                                  fix="remove duration from this video clip, or set trim_in/trim_out to choose its length"))
                continue
            info = probe_media(src)
            if info is None or info["duration"] is None:
                errors.append(err("CLIP_OUT_OF_RANGE", "could not probe video duration (file may be corrupt or not a video)", field="clips.src", clip_id=cid))
                continue
            if not info.get("has_video"):
                # An audio-only file used to validate here (it has
                # a duration) and fail only at render time, when
                # the filter graph found no video stream (1.34.0).
                errors.append(err("INVALID_SCHEMA", "video clip src has no video stream (audio-only file)", field="clips.src", clip_id=cid,
                                  fix="use a video file for a video clip, or add the file as an audio track instead"))
                continue
            if not info.get("width") or not info.get("height"):
                errors.append(err("INVALID_SCHEMA", "could not determine video dimensions (file may be corrupt)", field="clips.src", clip_id=cid,
                                  fix="replace the file with a readable video"))
                continue
            trim_in = clip.get("trim_in", 0)
            trim_out = clip.get("trim_out", info["duration"])
            try:
                trim_in = float(trim_in)
                trim_out = float(trim_out)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "trim_in/trim_out must be numbers", field="clips.trim", clip_id=cid))
                continue
            if trim_in < 0 or trim_out <= trim_in or trim_out > info["duration"] + 0.05:
                errors.append(err(
                    "CLIP_OUT_OF_RANGE",
                    f"trim {trim_in}..{trim_out} outside source duration {info['duration']}",
                    field="clips.trim", clip_id=cid))
                continue
            base_duration = trim_out - trim_in
            speed = clip.get("speed", 1.0)
            try:
                speed = float(speed)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "clip speed must be a number", field="clips.speed", clip_id=cid))
                continue
            if not 0.25 <= speed <= 4.0:
                errors.append(err("INVALID_SCHEMA", "clip speed must be 0.25..4.0", field="clips.speed", clip_id=cid))
                continue
            if "reverse" in clip and not isinstance(clip["reverse"], bool):
                errors.append(err("INVALID_SCHEMA", "clip reverse must be true or false", field="clips.reverse", clip_id=cid,
                                  fix="set reverse to true to play the clip backwards, or remove it"))
                continue
            loop_count = clip.get("loop_count")
            if loop_count is not None:
                if isinstance(loop_count, bool) or not isinstance(loop_count, int) or not 2 <= loop_count <= 8:
                    errors.append(err("INVALID_SCHEMA", "clip loop_count must be an integer 2..8", field="clips.loop_count", clip_id=cid,
                                      fix="set loop_count to a whole number between 2 and 8, or remove it"))
                    continue
            freeze_at = clip.get("freeze_at")
            if freeze_at is not None:
                try:
                    freeze_at = float(freeze_at)
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "clip freeze_at must be a number", field="clips.freeze_at", clip_id=cid,
                                      fix="set freeze_at to a second inside the trimmed clip range"))
                    continue
                if not trim_in <= freeze_at <= trim_out:
                    errors.append(err("CLIP_OUT_OF_RANGE", f"freeze_at {freeze_at}s outside trim {trim_in}..{trim_out}", field="clips.freeze_at", clip_id=cid,
                                      fix=f"set freeze_at between {trim_in} and {trim_out}"))
                    continue
            if freeze_at is not None and speed != 1.0:
                errors.append(err("INVALID_SCHEMA", "freeze_at cannot combine with speed", field="clips.freeze_at", clip_id=cid,
                                  fix="remove speed (freeze holds one frame for the whole trim duration)"))
                continue
            if loop_count is not None and speed != 1.0:
                errors.append(err("INVALID_SCHEMA", "loop_count cannot combine with speed", field="clips.loop_count", clip_id=cid,
                                  fix="remove speed, or remove loop_count and use speed alone"))
                continue
            if freeze_at is not None and clip.get("reverse"):
                errors.append(err("INVALID_SCHEMA", "freeze_at cannot combine with reverse", field="clips.reverse", clip_id=cid,
                                  fix="use freeze_at or reverse, not both, on one clip"))
                continue
            if loop_count is not None and clip.get("reverse"):
                errors.append(err("INVALID_SCHEMA", "loop_count cannot combine with reverse", field="clips.reverse", clip_id=cid,
                                  fix="use loop_count or reverse, not both, on one clip"))
                continue
            curve = clip.get("speed_curve")
            curve_total = None
            if curve is not None:
                if not isinstance(curve, list) or not curve:
                    errors.append(err("INVALID_SCHEMA", "speed_curve must be a non-empty list of segments", field="clips.speed_curve", clip_id=cid,
                                      fix="give segments with start, end and speed that partition the trimmed range"))
                    curve = None
                else:
                    segs = []
                    bad_curve = False
                    for si, seg in enumerate(curve):
                        if not isinstance(seg, dict):
                            errors.append(err("INVALID_SCHEMA", "each speed_curve segment must be an object", field="clips.speed_curve", clip_id=cid,
                                              fix="make each segment an object with start, end and speed"))
                            bad_curve = True
                            break
                        try:
                            ss, se, sp = float(seg["start"]), float(seg["end"]), float(seg["speed"])
                        except (KeyError, TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", "speed_curve segments need numeric start, end and speed", field="clips.speed_curve", clip_id=cid,
                                              fix="set start/end in trimmed-source seconds and speed 0.25..4.0"))
                            bad_curve = True
                            break
                        if se <= ss or not 0.25 <= sp <= 4.0:
                            errors.append(err("INVALID_SCHEMA", "speed_curve segment needs end > start and speed 0.25..4.0", field="clips.speed_curve", clip_id=cid,
                                              fix="keep each segment's end after its start and its speed between 0.25 and 4.0"))
                            bad_curve = True
                            break
                        segs.append((ss, se, sp))
                    if not bad_curve:
                        if abs(segs[0][0] - trim_in) > 0.001 or abs(segs[-1][1] - trim_out) > 0.001:
                            errors.append(err("INVALID_SCHEMA", f"speed_curve must partition the trimmed range {trim_in}..{trim_out} (first start, last end)", field="clips.speed_curve", clip_id=cid,
                                              fix=f"start the first segment at {trim_in} and end the last at {trim_out}"))
                            bad_curve = True
                        for (a0, a1, _), (b0, b1, _) in zip(segs, segs[1:]):
                            if abs(a1 - b0) > 0.001:
                                errors.append(err("INVALID_SCHEMA", "speed_curve segments must be contiguous with no gaps or overlaps", field="clips.speed_curve", clip_id=cid,
                                                  fix="make each segment start where the previous one ends"))
                                bad_curve = True
                                break
                    if bad_curve:
                        curve = None
                    else:
                        curve_total = sum((se - ss) / sp for ss, se, sp in segs)
            if curve is not None and speed != 1.0:
                errors.append(err("INVALID_SCHEMA", "speed_curve cannot combine with speed", field="clips.speed_curve", clip_id=cid,
                                  fix="remove speed and express the whole clip in speed_curve segments"))
                curve = None
            if curve is not None and (freeze_at is not None or loop_count is not None or clip.get("reverse")):
                errors.append(err("INVALID_SCHEMA", "speed_curve cannot combine with freeze_at, loop_count or reverse", field="clips.speed_curve", clip_id=cid,
                                  fix="use speed_curve alone on this clip"))
                curve = None
            if curve is not None and clip.get("fit") in BLUR_FITS:
                errors.append(err("INVALID_SCHEMA", "speed_curve cannot combine with fit blur", field="clips.fit", clip_id=cid,
                                  fix="use speed_curve or blurred-background fit, not both, on one clip"))
                curve = None
            if curve is not None:
                duration = curve_total
            elif freeze_at is not None:
                duration = base_duration
            elif loop_count is not None:
                duration = base_duration * loop_count
            else:
                duration = base_duration / speed
            _reframe_data = _reframe_path = None
            _reframe_warns = []
            if "reframe" in clip:
                _reframe_data, _reframe_path, _reframe_warns = _validate_reframe_clip(
                    clip, info, trim_in, trim_out, errors, cid,
                    width, height)
            resolved.append({**clip, "src": src, "duration": duration, "trim_in": trim_in, "trim_out": trim_out, "probe": info})
            if _reframe_data is not None:
                resolved[-1]["reframe_file"] = _reframe_path
                _data_files[_reframe_path] = {"kind": "reframe", "data": _reframe_data}
                _extra_warnings.extend(_reframe_warns)
            if not _is_t1:
                total += duration
        else:
            # Image clips have no trim: accepting trim_in/trim_out
            # would silently ignore them (duration is the image's
            # length), so they are rejected with a fix (1.34.0).
            if "trim_in" in clip or "trim_out" in clip:
                errors.append(err("INVALID_SCHEMA", "image clips must not set trim_in/trim_out (set duration instead)", field="clips.trim", clip_id=cid,
                                  fix="remove trim_in/trim_out from this image clip and set duration"))
                continue
            if "speed" in clip:
                errors.append(err("INVALID_SCHEMA", "clip speed is only for video clips", field="clips.speed", clip_id=cid))
                continue
            for _vf in ("reverse", "freeze_at", "loop_count", "speed_curve", "denoise", "stabilize", "reframe"):
                if _vf in clip:
                    errors.append(err("INVALID_SCHEMA", f"clip {_vf} is only for video clips", field=f"clips.{_vf}", clip_id=cid,
                                      fix=f"remove {_vf} from this image clip"))
                    continue
            iinfo = probe_media(src)
            if iinfo is None or not iinfo["has_video"]:
                errors.append(err("INVALID_SCHEMA", f"could not probe image file (not a readable image): {src}", field="clips.src", clip_id=cid))
                continue
            if not iinfo.get("width") or not iinfo.get("height"):
                # ffprobe "succeeds" on garbage named .png (image2
                # probe, 0x0 dimensions), which used to validate
                # clean and fail only at render time (1.34.0).
                errors.append(err("INVALID_SCHEMA", f"image file has no usable dimensions (file may be corrupt): {src}", field="clips.src", clip_id=cid,
                                  fix="replace the file with a readable still image"))
                continue
            # Codec check, NOT duration: a JPEG probed via image2 gets
            # duration 0.04 (one frame at 25fps) while a PNG via png_pipe
            # gets N/A, so duration cannot tell a still from a video.
            # A video codec (h264, hevc, vp9, ...) is the real signal.
            _vstreams = [st for st in iinfo["raw"].get("streams", []) if st.get("codec_type") == "video"]
            _codec = _vstreams[0].get("codec_name") if _vstreams else None
            if _codec not in ("mjpeg", "jpeg", "png", "webp", "bmp", "gif", "tiff"):
                errors.append(err("INVALID_SCHEMA", f"image clip src is not a still image (video codec {_codec}): {src}", field="clips.src", clip_id=cid))
                continue
            duration = clip.get("duration")
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "image clip needs a numeric duration", field="clips.duration", clip_id=cid))
                continue
            if duration <= 0:
                errors.append(err("INVALID_SCHEMA", "image clip duration must be > 0", field="clips.duration", clip_id=cid))
                continue
            resolved.append({**clip, "src": src, "duration": duration, "probe": iinfo})
            if not _is_t1:
                total += duration
        fit = clip.get("fit", "contain")
        if fit not in ("contain", "cover", "blur", "blur_pad"):
            errors.append(err("INVALID_SCHEMA", "fit must be 'contain', 'cover', 'blur' or 'blur_pad' (blurred background)", field="clips.fit", clip_id=cid))
        kb = clip.get("ken_burns", "none")
        if kb not in ("none", "zoom_in", "zoom_out", "pan_left", "pan_right"):
            errors.append(err("INVALID_SCHEMA", "ken_burns must be 'none', 'zoom_in', 'zoom_out', 'pan_left' or 'pan_right'", field="clips.ken_burns", clip_id=cid))
        elif kb != "none" and kind != "image":
            errors.append(err("INVALID_SCHEMA", "ken_burns is only for image clips", field="clips.ken_burns", clip_id=cid))
        # Track 1 clips are full-frame overlays: cover by
        # default (an explicit fit wins).
        if _is_t1 and "fit" not in clip and resolved and resolved[-1].get("id") == cid:
            resolved[-1]["fit"] = "cover"
        # Auto sub-cuts (v1.42.0, Fix 4b): per-clip opt-out
        # must be a boolean when present.
        if "auto_subcuts" in clip and not isinstance(
                clip["auto_subcuts"], bool):
            errors.append(err("INVALID_SCHEMA", "clip auto_subcuts must be true or false", field="clips.auto_subcuts", clip_id=cid,
                              fix="set auto_subcuts to false to keep this still frozen, or remove it"))
        for fname, lo, hi in (("brightness", -1.0, 1.0), ("contrast", 0.0, 2.0), ("saturation", 0.0, 3.0)):
            if fname in clip:
                try:
                    fval = float(clip[fname])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"clip {fname} must be a number", field=f"clips.{fname}", clip_id=cid))
                    continue
                if not lo <= fval <= hi:
                    errors.append(err("INVALID_SCHEMA", f"clip {fname} must be {lo}..{hi}", field=f"clips.{fname}", clip_id=cid))
        if clip.get("ken_burns") not in (None, "none") and clip.get("fit") in BLUR_FITS:
            errors.append(err("INVALID_SCHEMA", "ken_burns cannot combine with fit blur", field="clips.fit", clip_id=cid,
                              fix="use ken_burns or blurred-background fit, not both, on one clip"))
        # v1.40.0: camera path, validated against the resolved
        # duration like punch_in (same guard: only the clip just
        # resolved, so a failed clip cannot reach it).
        if resolved and resolved[-1].get("id") == cid:
            _validate_camera(clip, duration, errors, cid)
        if clip.get("fit") in BLUR_FITS and "chroma_key" in clip:
            errors.append(err("INVALID_SCHEMA", "fit blur cannot combine with chroma_key", field="clips.chroma_key", clip_id=cid,
                              fix="use blurred-background fit or chroma key, not both, on one clip"))
        if resolved:
            resolved[-1].update(_validate_chroma_mask(clip, errors, cid, "clips"))
        _validate_effects(clip, errors, cid, "clips")
        # v1.40.0: per-clip image treatment.
        _validate_treatment(clip, errors, cid, "clips")
        # Pro pack: per-clip colour grade preset and/or .cube LUT.
        # A grade is a named filter combination (see GRADE_PRESETS),
        # never a claim of film emulation. A LUT must be a local
        # .cube file that exists; it renders via FFmpeg lut3d.
        if "grade" in clip:
            if not isinstance(clip["grade"], str) or clip["grade"] not in GRADE_PRESETS:
                errors.append(err("INVALID_SCHEMA", "clip grade must be one of: " + ", ".join(sorted(GRADE_PRESETS)), field="clips.grade", clip_id=cid,
                                  fix="set grade to one of: " + ", ".join(sorted(GRADE_PRESETS)) + ", or remove it"))
        if "lut" in clip:
            lut = clip["lut"]
            if not isinstance(lut, str) or not lut:
                errors.append(err("INVALID_SCHEMA", "clip lut must be a local .cube file path", field="clips.lut", clip_id=cid,
                                  fix="set lut to a local .cube file path, e.g. examples/test-lut.cube"))
            elif "'" in lut:
                errors.append(err("INVALID_SCHEMA", "clip lut path must not contain a quote character", field="clips.lut", clip_id=cid,
                                  fix="rename or copy the LUT to a path without quotes"))
            elif not lut.lower().endswith(".cube"):
                errors.append(err("INVALID_SCHEMA", "clip lut must end in .cube", field="clips.lut", clip_id=cid,
                                  fix="use a .cube LUT file (FFmpeg lut3d format), or remove lut"))
            else:
                resolved_lut = resolve_path(lut)
                if not os.path.isfile(resolved_lut):
                    errors.append(err("FILE_NOT_FOUND", f"LUT file not found: {resolved_lut}", field="clips.lut", clip_id=cid,
                                      fix="fix the lut path, or copy the .cube file to that path"))
                elif resolved:
                    resolved[-1]["lut_resolved"] = resolved_lut
        if "stabilize" in clip and not isinstance(clip["stabilize"], bool):
            errors.append(err("INVALID_SCHEMA", "clip stabilize must be true or false", field="clips.stabilize", clip_id=cid,
                              fix="set stabilize to true for two-pass vidstab stabilisation, or remove it"))
        if clip.get("stabilize") and (clip.get("freeze_at") is not None or clip.get("loop_count") is not None or clip.get("reverse") or clip.get("speed_curve") is not None or float(clip.get("speed", 1.0)) != 1.0 or clip.get("fit") in BLUR_FITS):
            errors.append(err("INVALID_SCHEMA", "stabilize cannot combine with freeze_at, loop_count, reverse, speed_curve, speed or fit blur (stabilisation needs the clip's original frame order and timing)", field="clips.stabilize", clip_id=cid,
                              fix="use stabilize alone on a plain trimmed clip"))
        if "denoise" in clip and not isinstance(clip["denoise"], bool):
            errors.append(err("INVALID_SCHEMA", "clip denoise must be true or false", field="clips.denoise", clip_id=cid,
                              fix="set denoise to true to run FFT noise reduction on the clip's audio, or remove it"))
        if "grayscale" in clip and not isinstance(clip["grayscale"], bool):
            errors.append(err("INVALID_SCHEMA", "clip grayscale must be true or false", field="clips.grayscale", clip_id=cid))
        transition = clip.get("transition", "none")
        allowed = ["none", "fade"] + list(XFADE_TRANSITIONS) + sorted(CUSTOM_TRANSITIONS)
        # _joined counts track 0 clips only: the first JOINED
        # clip is what the first-clip rule is about.
        _joined = sum(1 for _c in resolved if _c.get("track", 0) == 0)
        if transition not in allowed:
            errors.append(err("INVALID_SCHEMA", f"transition must be one of {', '.join(allowed)}", field="clips.transition", clip_id=cid))
        elif _is_t1 and transition != "none":
            errors.append(err("INVALID_SCHEMA", f"{transition} on a track 1 clip has nothing to transition from (track 1 clips are composited, not joined)", field="clips.transition", clip_id=cid,
                              fix="remove transition from the track 1 clip"))
        elif transition in XFADE_TRANSITIONS and _joined == 1:
            errors.append(err("INVALID_SCHEMA", f"{transition} on the first clip has nothing to transition from", field="clips.transition", clip_id=cid))
        elif transition in CUSTOM_TRANSITIONS and _joined == 1:
            errors.append(err("INVALID_SCHEMA", f"{transition} on the first clip has nothing to transition from", field="clips.transition", clip_id=cid))
        td = None
        if "transition_duration" in clip:
            try:
                td = float(clip["transition_duration"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "transition_duration must be a number", field="clips.transition_duration", clip_id=cid))
                td = None
            if td is not None and (td <= 0 or td > 2.0):
                errors.append(err("INVALID_SCHEMA", "transition_duration must be > 0 and <= 2.0 seconds", field="clips.transition_duration", clip_id=cid))
        if transition in XFADE_TRANSITIONS and _joined >= 2:
            if td is None:
                td = 0.5
            if prev_duration is None or td >= duration or td >= prev_duration:
                errors.append(err("INVALID_SCHEMA", f"{transition} transition_duration {td}s must be less than both clip durations", field="clips.transition_duration", clip_id=cid))
        elif transition in CUSTOM_TRANSITIONS and _joined >= 2:
            if td is None:
                td = 0.5
            if prev_duration is None or td >= duration or td >= prev_duration:
                errors.append(err("INVALID_SCHEMA", f"{transition} transition_duration {td}s must be less than both clip durations", field="clips.transition_duration", clip_id=cid))
        elif transition == "fade":
            if td is None:
                td = 0.3
            if td >= duration:
                errors.append(err("INVALID_SCHEMA", f"fade transition_duration {td}s must be less than the clip duration", field="clips.transition_duration", clip_id=cid))
        else:
            td = 0.0
        if resolved:
            resolved[-1]["transition_duration"] = td
        # v1.37.0: punch-in (resolved onto the clip) and clip SFX
        # (fired at the clip's timeline start, i.e. the transition
        # start for xfade clips). Both validate against the
        # resolved duration computed above.
        if resolved and resolved[-1].get("id") == cid:
            _validate_punch_in(clip, duration, errors, cid, resolved[-1])
        _validate_sfx(clip, errors, cid, "clips")
        # start offset in the final timeline (after xfade
        # overlaps). Track 1 clips keep their absolute start
        # (they are composited, not joined); clip_starts stays
        # aligned with resolved, one entry per clip. _prev_* track
        # the previous TRACK 0 clip only, so a track 1 clip in
        # the list never shifts the base timeline.
        if _is_t1:
            clip_starts.append(float(_t1_start))
        elif _joined == 1:
            clip_starts.append(0.0)
            _prev_start0, _prev_dur0 = 0.0, duration
        elif transition in XFADE_TRANSITIONS:
            clip_starts.append(_prev_start0 + _prev_dur0 - td)
            _prev_start0, _prev_dur0 = clip_starts[-1], duration
        else:
            clip_starts.append(_prev_start0 + _prev_dur0)
            _prev_start0, _prev_dur0 = clip_starts[-1], duration
        if not _is_t1:
            prev_duration = duration
    overlap_total = sum(float(c.get("transition_duration", 0.5)) for c in resolved[1:] if c.get("transition") in XFADE_TRANSITIONS)
    total = total - overlap_total
    if total > MAX_DURATION:
        errors.append(err("DURATION_TOO_LONG", f"total duration {total:.2f}s exceeds {MAX_DURATION}s", field="clips"))
    # Multi-track (v1.44.0, F6a): the base timeline needs at
    # least one track 0 clip, and every track 1 clip must sit
    # inside it (it never extends the total).
    if not any(c.get("track", 0) == 0 for c in resolved):
        errors.append(err("EMPTY_TIMELINE", "at least one clip must be on track 0 (the base timeline)", field="clips",
                          fix="set track to 0 on the main clip, or remove track 1 from every clip"))
    for _c in resolved:
        if _c.get("track", 0) == 1:
            _s_raw = _c.get("start")
            if isinstance(_s_raw, bool) or not isinstance(_s_raw, (int, float)):
                continue  # per-clip start error already recorded
            _s = float(_s_raw); _e = _s + float(_c["duration"])
            if _e > total + 0.05:
                errors.append(err("CLIP_OUT_OF_RANGE", f"track 1 clip '{_c.get('id')}' runs {_s:.2f}..{_e:.2f}, past the {total:.2f}s base timeline", field="clips.start", clip_id=_c.get("id"),
                                  fix=f"move the clip earlier or shorten it so it ends by {total:.2f}s"))
    # Auto sub-cuts (v1.42.0, Fix 4b): generated camera paths
    # for long stills, BEFORE the continuity pass so the pass
    # chains their scales across same-scene clips. (info is
    # assembled later, so the ids ride in a local until then.)
    # Beat cutting (v1.44.0, F1): split clips at AgentMedia
    # detect_beats times, BEFORE auto-subcuts and continuity so
    # both see the expanded timeline. info carries the cut list.
    resolved, clip_starts, total, _beat_cuts = _apply_beat_cut(
        project, resolved, clip_starts, total, errors, _data_files)
    _auto_subcut_ids = _generate_auto_subcuts(resolved, project)
    # Motion continuity (v1.40.0): sequential clips sharing a
    # scene_id chain their scales — clip N's end scale is clip
    # N+1's start scale — fixing the 1.37.2 residual where a
    # same-scene boundary stepped up to 0.25x. Only ADJACENT
    # clips chain (a gap or a sceneless clip breaks the chain),
    # and the 1.25x max still holds by construction (every end
    # scale is clamped to it). Camera keyframes are adjusted by
    # deep copy (resolved clips share the raw clip's list, so
    # an in-place edit would mutate the caller's project), and
    # only when the adjustment actually changes a value;
    # Ken Burns start/end are stored as _kb_start/_kb_end only
    # when they differ from the legacy defaults (tolerance
    # 1e-9, since 1.12-0.12 need not round-trip exactly), so old
    # graphs stay byte-identical. Punch is a jump by design:
    # its start is never re-based, but its end factor feeds the
    # next clip. A static clip (no camera, KB or punch) resets
    # the chain to 1.0 — it cannot start zoomed. _motion_end is
    # recorded on every clip for the warnings below.
    _prev_end = 1.0
    _prev_scene = None
    for _ci, _clip in enumerate(resolved):
        if _clip.get("track", 0) == 1:
            # A second-track clip does not interrupt the base
            # timeline: it neither chains from the previous base
            # clip nor resets the chain for the next one.
            _clip["_motion_end"] = 1.0
            continue
        _scene = _clip.get("scene_id")
        _continuous = _scene is not None and _scene == _prev_scene
        _start = _end = 1.0
        if _clip.get("punch_at") is not None:
            _start = 1.0
            _end = float(_clip.get("punch_factor", 1.20))
        elif _clip.get("camera"):
            _cam = [dict(kf) if isinstance(kf, dict) else kf
                    for kf in _clip["camera"]]
            if _continuous and _cam:
                _first = _cam[0]
                if float(_first.get("t", 0)) == 0:
                    if float(_first["scale"]) != _prev_end:
                        _first["scale"] = _prev_end
                        _clip["camera"] = _cam
                        _clip["_continuity"] = True
                elif float(_first["scale"]) != _prev_end:
                    _cam.insert(0, {"t": 0, "x": _first.get("x", 0.5),
                                    "y": _first.get("y", 0.5),
                                    "scale": _prev_end})
                    _clip["camera"] = _cam
                    _clip["_continuity"] = True
            _start = float(_cam[0]["scale"]) if _cam else 1.0
            _end = float(_cam[-1]["scale"]) if _cam else 1.0
        else:
            _kb = effective_ken_burns(_clip, _ci, project)
            if _kb == "zoom_in":
                _start = _prev_end if _continuous else 1.0
                _end = min(1.25, _start + 0.12)
                _legacy = (1.0, 1.12)
            elif _kb == "zoom_out":
                _start = _prev_end if _continuous else 1.12
                _end = max(1.0, _start - 0.12)
                _legacy = (1.12, 1.0)
            elif _kb in ("pan_left", "pan_right"):
                _start = _end = _prev_end if _continuous else 1.12
                _legacy = (1.12, 1.12)
            else:
                _legacy = None
            if _legacy is not None and _continuous and (
                    abs(_start - _legacy[0]) > 1e-9
                    or abs(_end - _legacy[1]) > 1e-9):
                _clip["_kb_start"] = _start
                _clip["_kb_end"] = _end
                _clip["_continuity"] = True
        _clip["_motion_end"] = _end
        _prev_end = _end
        _prev_scene = _scene
    # Overlays (Loop 5 round 1): picture-in-picture tracks above the
    # main timeline. They never change the timeline length; they must
    # fit inside it. Video overlays loop when their shown duration
    # exceeds the trimmed source duration (render handles the loop).
    resolved_overlays = []
    seen_overlay_ids = set()
    for ov in project.get("overlays") or []:
        if not isinstance(ov, dict):
            errors.append(err("INVALID_SCHEMA", "each overlay must be an object", field="overlays",
                              fix="make overlays a list of objects with id, kind and src"))
            continue
        oid = ov.get("id")
        if not isinstance(oid, str) or not oid:
            errors.append(err("INVALID_SCHEMA", "each overlay needs a non-empty string id", field="overlays.id",
                              fix="give every overlay a unique non-empty id"))
            continue
        if oid in seen_overlay_ids:
            errors.append(err("INVALID_SCHEMA", f"duplicate overlay id {oid}", field="overlays.id", clip_id=oid,
                              fix=f"rename one of the overlays called {oid}"))
            continue
        seen_overlay_ids.add(oid)
        kind = ov.get("kind")
        src = ov.get("src")
        if kind not in ("video", "image"):
            errors.append(err("INVALID_SCHEMA", "overlay kind must be 'video' or 'image'", field="overlays.kind", clip_id=oid,
                              fix="set kind to 'video' for a moving overlay or 'image' for a still one"))
            continue
        if not isinstance(src, str) or not src:
            errors.append(err("INVALID_SCHEMA", "overlay src must be a non-empty path", field="overlays.src", clip_id=oid,
                              fix="point src at a local media file, as with clips"))
            continue
        src = resolve_path(src)
        if not os.path.isfile(src):
            errors.append(err("FILE_NOT_FOUND", f"overlay file not found: {src}", field="overlays.src", clip_id=oid,
                              fix="fix the path, or copy the media file to that path"))
            continue
        info_p = probe_media(src)
        if info_p is None or not info_p["has_video"]:
            errors.append(err("INVALID_SCHEMA", f"could not probe overlay file (not a readable video/image): {src}", field="overlays.src", clip_id=oid,
                              fix="replace src with a readable video or still image"))
            continue
        if not info_p.get("width") or not info_p.get("height"):
            errors.append(err("INVALID_SCHEMA", f"overlay file has no usable dimensions (file may be corrupt): {src}", field="overlays.src", clip_id=oid,
                              fix="replace src with a readable video or still image"))
            continue
        rov = {**ov, "src": src, "probe": info_p}
        if kind == "video":
            if info_p["duration"] is None:
                errors.append(err("CLIP_OUT_OF_RANGE", "could not probe overlay video duration", field="overlays.src", clip_id=oid,
                                  fix="replace src with a video ffprobe can read"))
                continue
            trim_in = ov.get("trim_in", 0)
            trim_out = ov.get("trim_out", info_p["duration"])
            try:
                trim_in = float(trim_in); trim_out = float(trim_out)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay trim_in/trim_out must be numbers", field="overlays.trim", clip_id=oid,
                                  fix="set trim_in/trim_out to numbers inside the source duration"))
                continue
            if trim_in < 0 or trim_out <= trim_in or trim_out > info_p["duration"] + 0.05:
                errors.append(err("CLIP_OUT_OF_RANGE", f"overlay trim {trim_in}..{trim_out} outside source duration {info_p['duration']}", field="overlays.trim", clip_id=oid,
                                  fix=f"keep trim inside 0..{info_p['duration']:.2f}"))
                continue
            rov.update({"trim_in": trim_in, "trim_out": trim_out, "source_duration": trim_out - trim_in})
            if "duration" in ov:
                try:
                    odur = float(ov["duration"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "overlay duration must be a number", field="overlays.duration", clip_id=oid,
                                      fix="set duration to a number of seconds, or omit it to use the trimmed source duration"))
                    continue
                if odur <= 0:
                    errors.append(err("INVALID_SCHEMA", "overlay duration must be > 0", field="overlays.duration", clip_id=oid,
                                      fix="set duration to a positive number of seconds"))
                    continue
                rov["duration"] = odur
            else:
                rov["duration"] = trim_out - trim_in
        else:
            _vstreams = [st for st in info_p["raw"].get("streams", []) if st.get("codec_type") == "video"]
            _codec = _vstreams[0].get("codec_name") if _vstreams else None
            if _codec not in ("mjpeg", "jpeg", "png", "webp", "bmp", "gif", "tiff"):
                errors.append(err("INVALID_SCHEMA", f"overlay image src is not a still image (video codec {_codec}): {src}", field="overlays.src", clip_id=oid,
                                  fix="use a still image file (PNG, JPEG, WebP) for an image overlay"))
                continue
            try:
                odur = float(ov["duration"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "image overlay needs a numeric duration", field="overlays.duration", clip_id=oid,
                                  fix="set duration to the number of seconds the overlay stays on screen"))
                continue
            if odur <= 0:
                errors.append(err("INVALID_SCHEMA", "image overlay duration must be > 0", field="overlays.duration", clip_id=oid,
                                  fix="set duration to a positive number of seconds"))
                continue
            rov["duration"] = odur
        try:
            ostart = float(ov.get("start", 0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "overlay start must be a number", field="overlays.start", clip_id=oid,
                              fix="set start to the timeline second where the overlay appears"))
            continue
        if ostart < 0 or ostart >= total:
            errors.append(err("CLIP_OUT_OF_RANGE", f"overlay start {ostart}s outside timeline {total:.2f}s", field="overlays.start", clip_id=oid,
                              fix=f"set start between 0 and {total:.2f}"))
            continue
        if ostart + rov["duration"] > total + 0.05:
            errors.append(err("CLIP_OUT_OF_RANGE", f"overlay {ostart}..{ostart + rov['duration']:.2f}s runs past timeline end {total:.2f}s", field="overlays.duration", clip_id=oid,
                              fix=f"shorten duration to at most {total - ostart:.2f}s, or move start earlier"))
            continue
        rov["start"] = ostart
        size = ov.get("size", 0.35)
        try:
            size = float(size)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "overlay size must be a number", field="overlays.size", clip_id=oid,
                              fix="set size to a fraction of the output width, e.g. 0.35"))
            continue
        if not 0.05 <= size <= 1.0:
            errors.append(err("INVALID_SCHEMA", "overlay size must be 0.05..1.0 (fraction of output width)", field="overlays.size", clip_id=oid,
                              fix="set size between 0.05 and 1.0"))
            continue
        rov["size"] = size
        opacity = ov.get("opacity", 1.0)
        try:
            opacity = float(opacity)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "overlay opacity must be a number", field="overlays.opacity", clip_id=oid,
                              fix="set opacity between 0 (invisible) and 1 (opaque)"))
            continue
        if not 0.0 <= opacity <= 1.0:
            errors.append(err("INVALID_SCHEMA", "overlay opacity must be 0..1", field="overlays.opacity", clip_id=oid,
                              fix="set opacity between 0 and 1"))
            continue
        rov["opacity"] = opacity
        # Overlay audio (Loop 6 round 4): optional, default muted
        # so every existing project keeps its old behaviour. A
        # volume above zero needs a video overlay whose file
        # actually has an audio stream (ffprobe), images and
        # silent videos included in the rejection.
        vol = ov.get("volume", 0.0)
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "overlay volume must be a number", field="overlays.volume", clip_id=oid,
                              fix="set volume to a number between 0 (muted) and 2, or omit it"))
            continue
        if not 0.0 <= vol <= 2.0:
            errors.append(err("INVALID_SCHEMA", "overlay volume must be 0..2", field="overlays.volume", clip_id=oid,
                              fix="set volume between 0 (muted) and 2"))
            continue
        if vol > 0 and (kind != "video" or not info_p.get("has_audio")):
            errors.append(err("INVALID_SCHEMA", "overlay volume needs a video overlay with an audio stream", field="overlays.volume", clip_id=oid,
                              fix="use a video file with sound for this overlay, or remove volume to keep it muted"))
            continue
        rov["volume"] = vol
        position = ov.get("position", "bottom_right")
        if position not in TEXT_POSITIONS:
            errors.append(err("INVALID_SCHEMA", "overlay position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field="overlays.position", clip_id=oid,
                              fix="use a preset position, or set x and y pixel offsets instead"))
            continue
        rov["position"] = position
        # v1.37.0: overlay SFX, fired at the overlay's start.
        _validate_sfx(ov, errors, oid, "overlays")
        for axis in ("x", "y"):
            if axis in ov:
                try:
                    aval = int(ov[axis])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"overlay {axis} must be an integer pixel offset", field=f"overlays.{axis}", clip_id=oid,
                                      fix=f"set {axis} to a non-negative integer pixel offset"))
                    continue
                if aval < 0:
                    errors.append(err("INVALID_SCHEMA", f"overlay {axis} must be >= 0", field=f"overlays.{axis}", clip_id=oid,
                                      fix=f"set {axis} to 0 or more"))
                    continue
                rov[axis] = aval
        _validate_effects(ov, errors, oid, "overlays")
        _ov_cm = _validate_chroma_mask(ov, errors, oid, "overlays")
        rov.update(_ov_cm)
        # Overlay dressing (v1.36.0): optional border, rounded
        # corners and drop shadow, all default off so old overlays
        # render identically. Fields pass through rov (it starts
        # as a copy of ov), so validation here is the gate.
        if "border_width" in ov:
            try:
                _bw = int(ov["border_width"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay border_width must be an integer", field="overlays.border_width", clip_id=oid,
                                  fix="set border_width to 0 (no border) or a width 1..20"))
                _bw = None
            if _bw is not None and not 0 <= _bw <= 20:
                errors.append(err("INVALID_SCHEMA", "overlay border_width must be 0..20", field="overlays.border_width", clip_id=oid,
                                  fix="set border_width between 0 and 20 pixels"))
        _bcol = ov.get("border_color", "black")
        if not (isinstance(_bcol, str) and (_bcol in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", _bcol))):
            errors.append(err("INVALID_SCHEMA", "overlay border_color must be a #RRGGBB hex value or a named colour", field="overlays.border_color", clip_id=oid,
                              fix="set border_color to a named colour or #RRGGBB, e.g. white"))
        if "corner_radius" in ov:
            try:
                _cr = int(ov["corner_radius"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay corner_radius must be an integer", field="overlays.corner_radius", clip_id=oid,
                                  fix="set corner_radius to 0 (square corners) or a radius 1..200"))
                _cr = None
            if _cr is not None:
                if not 0 <= _cr <= 200:
                    errors.append(err("INVALID_SCHEMA", "overlay corner_radius must be 0..200", field="overlays.corner_radius", clip_id=oid,
                                      fix="set corner_radius between 0 and 200 pixels"))
                else:
                    # The rounded mask is a circle test per corner,
                    # so a radius larger than half the smaller
                    # displayed side would fold corners over each
                    # other. Checked against the base size (for
                    # varying-scale keyframes the base size is the
                    # documented approximation; a smaller scale
                    # only softens the corners into a blob, never
                    # a crash).
                    _pw, _ph = info_p.get("width") or width, info_p.get("height") or height
                    _bw_disp = float(size) * width
                    _bh_disp = _bw_disp * (_ph / _pw) if _pw else _bw_disp
                    if _cr > min(_bw_disp, _bh_disp) / 2:
                        errors.append(err("INVALID_SCHEMA", f"overlay corner_radius {_cr} exceeds half the smaller displayed side ({min(_bw_disp, _bh_disp) / 2:.0f}px)", field="overlays.corner_radius", clip_id=oid,
                                          fix="reduce corner_radius, or increase overlay size"))
        for _axis in ("shadow_dx", "shadow_dy"):
            if _axis in ov:
                try:
                    _sv = int(ov[_axis])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"overlay {_axis} must be an integer", field=f"overlays.{_axis}", clip_id=oid,
                                      fix=f"set {_axis} to a pixel offset -100..100"))
                    _sv = None
                if _sv is not None and not -100 <= _sv <= 100:
                    errors.append(err("INVALID_SCHEMA", f"overlay {_axis} must be -100..100", field=f"overlays.{_axis}", clip_id=oid,
                                      fix=f"set {_axis} between -100 and 100 pixels"))
        if "shadow_blur" in ov:
            try:
                _sb = float(ov["shadow_blur"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay shadow_blur must be a number", field="overlays.shadow_blur", clip_id=oid,
                                  fix="set shadow_blur to a blur sigma 0..30"))
                _sb = None
            if _sb is not None and not 0.0 <= _sb <= 30.0:
                errors.append(err("INVALID_SCHEMA", "overlay shadow_blur must be 0..30", field="overlays.shadow_blur", clip_id=oid,
                                  fix="set shadow_blur between 0 and 30"))
        if "shadow_opacity" in ov:
            try:
                _so = float(ov["shadow_opacity"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay shadow_opacity must be a number", field="overlays.shadow_opacity", clip_id=oid,
                                  fix="set shadow_opacity between 0 (invisible) and 1 (opaque)"))
                _so = None
            if _so is not None and not 0.0 <= _so <= 1.0:
                errors.append(err("INVALID_SCHEMA", "overlay shadow_opacity must be 0..1", field="overlays.shadow_opacity", clip_id=oid,
                                  fix="set shadow_opacity between 0 and 1"))
        # Parallax depth (v1.40.0): how strongly the overlay
        # follows camera/background motion. 0 is the old
        # screen-pinned behaviour; 1 follows the motion fully.
        # Render applies the term only when the clip under the
        # overlay actually moves (camera path or Ken Burns
        # pan); otherwise depth is a harmless no-op, flagged by
        # a warnings-only note (see validate_project).
        if "depth" in ov:
            try:
                _dep = float(ov["depth"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "overlay depth must be a number", field="overlays.depth", clip_id=oid,
                                  fix="set depth between 0 (pinned) and 1 (fully follows camera motion)"))
                _dep = None
            if _dep is not None and not 0.0 <= _dep <= 1.0:
                errors.append(err("INVALID_SCHEMA", "overlay depth must be 0..1", field="overlays.depth", clip_id=oid,
                                  fix="set depth between 0 (pinned) and 1 (fully follows camera motion)"))
        # Keyframes (Loop 5 round 2): animate overlay position, scale
        # and opacity over time. Times are relative to the overlay
        # start, in seconds. Missing fields inherit the previous
        # keyframe's value, or the overlay's own base value.
        kfs = ov.get("keyframes")
        if kfs is not None:
            if not isinstance(kfs, list) or len(kfs) < 2:
                errors.append(err("INVALID_SCHEMA", "overlay keyframes must be a list of at least 2 keyframes", field="overlays.keyframes", clip_id=oid,
                                  fix="give at least 2 keyframes, e.g. one at time 0 and one at the end"))
            else:
                raw_kfs = []
                prev_time = None
                bad_kf = False
                for ki, kf in enumerate(kfs):
                    if not isinstance(kf, dict):
                        errors.append(err("INVALID_SCHEMA", "each keyframe must be an object", field="overlays.keyframes", clip_id=oid,
                                          fix="make each keyframe an object with time plus x, y, scale or opacity"))
                        bad_kf = True
                        break
                    try:
                        kt = float(kf["time"])
                    except (KeyError, TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", "each keyframe needs a numeric time", field="overlays.keyframes.time", clip_id=oid,
                                          fix="set time to seconds since the overlay starts, between 0 and its duration"))
                        bad_kf = True
                        break
                    if kt < 0 or kt > rov["duration"] + 0.001:
                        errors.append(err("CLIP_OUT_OF_RANGE", f"keyframe time {kt}s outside overlay duration {rov['duration']:.2f}s", field="overlays.keyframes.time", clip_id=oid,
                                          fix=f"keep keyframe times between 0 and {rov['duration']:.2f}"))
                        bad_kf = True
                        break
                    if prev_time is not None and kt <= prev_time:
                        errors.append(err("INVALID_SCHEMA", "keyframe times must strictly increase", field="overlays.keyframes.time", clip_id=oid,
                                          fix="sort keyframes by time with no duplicates"))
                        bad_kf = True
                        break
                    prev_time = kt
                    vals = {}
                    for fname, lo, hi in (("scale", 0.05, 1.0), ("opacity", 0.0, 1.0)):
                        if fname in kf:
                            try:
                                fv = float(kf[fname])
                            except (TypeError, ValueError):
                                errors.append(err("INVALID_SCHEMA", f"keyframe {fname} must be a number", field=f"overlays.keyframes.{fname}", clip_id=oid,
                                                  fix=f"set {fname} to a number between {lo} and {hi}"))
                                bad_kf = True
                                break
                            if not lo <= fv <= hi:
                                errors.append(err("INVALID_SCHEMA", f"keyframe {fname} must be {lo}..{hi}", field=f"overlays.keyframes.{fname}", clip_id=oid,
                                                  fix=f"set {fname} between {lo} and {hi}"))
                                bad_kf = True
                                break
                            vals[fname] = fv
                    if bad_kf:
                        break
                    for axis in ("x", "y"):
                        if axis in kf:
                            try:
                                av = int(kf[axis])
                            except (TypeError, ValueError):
                                errors.append(err("INVALID_SCHEMA", f"keyframe {axis} must be an integer pixel offset", field=f"overlays.keyframes.{axis}", clip_id=oid,
                                                  fix=f"set {axis} to a non-negative integer pixel coordinate"))
                                bad_kf = True
                                break
                            if av < 0:
                                errors.append(err("INVALID_SCHEMA", f"keyframe {axis} must be >= 0", field=f"overlays.keyframes.{axis}", clip_id=oid,
                                                  fix=f"set {axis} to 0 or more"))
                                bad_kf = True
                                break
                            vals[axis] = av
                    if bad_kf:
                        break
                    if not vals:
                        errors.append(err("INVALID_SCHEMA", "each keyframe must set at least one of x, y, scale, opacity", field="overlays.keyframes", clip_id=oid,
                                          fix="add x, y, scale or opacity to the keyframe"))
                        bad_kf = True
                        break
                    # Pro pack: easing reshapes the segment's time
                    # curve. It is stored alongside the values so
                    # the render can apply it to scale, position
                    # and opacity alike. An unknown value is a
                    # validation error with a fix, never a silent
                    # fall back to linear.
                    ev = kf.get("easing", "linear")
                    if not isinstance(ev, str) or ev not in EASING_VALUES:
                        errors.append(err("INVALID_SCHEMA", "keyframe easing must be one of: " + ", ".join(sorted(EASING_VALUES)), field="overlays.keyframes.easing", clip_id=oid,
                                          fix="set easing to one of: " + ", ".join(sorted(EASING_VALUES)) + ", or remove it"))
                        bad_kf = True
                        break
                    raw_kfs.append((kt, vals, ev))
                if not bad_kf:
                    # Resolve base position pixels for preset positions:
                    # the overlay's display size at its base scale.
                    pw = info_p.get("width") or width
                    ph = info_p.get("height") or height
                    bw = float(rov["size"]) * width
                    bh = bw * (ph / pw) if pw else bw
                    _preset_xy = {
                        "top_left": (20, 20),
                        "top_center": ((width - bw) / 2, 20),
                        "top_right": (width - bw - 20, 20),
                        "center": ((width - bw) / 2, (height - bh) / 2),
                        "bottom_left": (20, height - bh - 20),
                        "bottom_center": ((width - bw) / 2, height - bh - 20),
                        "bottom_right": (width - bw - 20, height - bh - 20),
                    }
                    base_x = rov.get("x", int(round(_preset_xy[rov["position"]][0])))
                    base_y = rov.get("y", int(round(_preset_xy[rov["position"]][1])))
                    filled = []
                    cur = {"x": base_x, "y": base_y, "scale": float(rov["size"]), "opacity": float(rov["opacity"]), "easing": "linear"}
                    for kt, vals, ev in raw_kfs:
                        cur = {**cur, **vals, "easing": ev}
                        filled.append({"time": kt, "x": cur["x"], "y": cur["y"],
                                       "scale": cur["scale"], "opacity": cur["opacity"],
                                       "easing": cur["easing"]})
                    scales = {f["scale"] for f in filled}
                    if kind == "video" and len(scales) > 1 and rov["duration"] > rov.get("source_duration", rov["duration"]) + 0.01:
                        errors.append(err("INVALID_SCHEMA", "scale keyframes on a looping video overlay are not supported", field="overlays.keyframes.scale", clip_id=oid,
                                          fix="shorten the overlay duration to the trimmed source duration, or remove scale keyframes"))
                    else:
                        rov["keyframes_resolved"] = filled
                        if "mask" in rov and len({f["scale"] for f in filled}) > 1:
                            errors.append(err("INVALID_SCHEMA", "mask cannot combine with scale keyframes (mask coordinates are in fixed overlay-frame pixels)", field="overlays.mask_shape", clip_id=oid,
                                              fix="remove scale keyframes, or remove the mask, on this overlay"))
                        if rov.get("chroma_key_hex") and len({f["scale"] for f in filled}) > 1:
                            # Verified on this FFmpeg build (Loop 6
                            # round 3): colorkey fails while filtering
                            # when its input frame size changes every
                            # frame, so a varying scale cannot be
                            # keyed. Reject loudly, never render a
                            # broken or half-keyed overlay.
                            errors.append(err("INVALID_SCHEMA", "chroma_key cannot combine with scale keyframes (colorkey cannot handle per-frame size changes on this FFmpeg build)", field="overlays.chroma_key", clip_id=oid,
                                              fix="remove chroma_key, or use a constant overlay size (drop scale keyframes), on this overlay"))
        if "track" in ov:
            if ov.get("keyframes"):
                errors.append(err("INVALID_SCHEMA", "overlay track cannot combine with keyframes (the track drives the position)", field="overlays.track", clip_id=oid,
                                  fix="remove keyframes, or remove track, on this overlay"))
            else:
                _trk, _trk_path = _load_track_data(ov["track"], "overlays.track", errors, oid)
                if _trk is not None:
                    rov["track_file"] = _trk_path
                    # Offsets are ELEMENT properties, not file data:
                    # two elements sharing one track file with
                    # different offsets must not overwrite each
                    # other in data_files (R3: an overlay inherited
                    # a text's offset_y=-60 through the shared key).
                    rov["track_offset"] = {"x": _trk["offset_x"],
                                           "y": _trk["offset_y"]}
                    _data_files[_trk_path] = {
                        "kind": "track",
                        "data": {"frame_size": _trk["frame_size"],
                                 "track": _trk["track"]}}
                    # Track times are ELEMENT-RELATIVE
                    # (v1.44.0, F2, corrected R3): the track is
                    # sampled in the overlay's source time (t -
                    # start + trim_in), so it must cover
                    # [trim_in, trim_in + duration], not the
                    # absolute timeline window.
                    _ti = float(rov.get("trim_in", 0))
                    _tw0 = _ti
                    _tw1 = _ti + float(rov["duration"])
                    _t0 = _trk["track"][0]["t"]; _t1 = _trk["track"][-1]["t"]
                    if _t1 < _tw0 or _t0 > _tw1:
                        errors.append(err("INVALID_SCHEMA", f"track covers {_t0:.2f}..{_t1:.2f}s but the overlay source time runs {_tw0:.2f}..{_tw1:.2f}s (no overlap)", field="overlays.track", clip_id=oid,
                                          fix="use a track file covering the overlay's source time (t - start + trim_in), or shorten the overlay"))
                    elif _t0 > _tw0 or _t1 < _tw1:
                        _extra_warnings.append({"code": "track_partial_coverage",
                                                "message": f"overlay '{oid}' track covers {_t0:.2f}..{_t1:.2f}s but the overlay source time runs {_tw0:.2f}..{_tw1:.2f}s; the first/last track point is held outside the track",
                                                "clip_id": oid})
        resolved_overlays.append(rov)
    for label, items in (("texts", project.get("texts") or []), ("captions", project.get("captions") or [])):
        if not isinstance(items, list):
            errors.append(err("INVALID_SCHEMA", f"{label} must be a list", field=label))
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                errors.append(err("INVALID_SCHEMA", f"each {label} item needs a text string", field=label))
                continue
            if not item["text"].strip():
                errors.append(err("INVALID_SCHEMA", f"each {label} item needs non-empty text", field=label))
                continue
            try:
                start = float(item.get("start", 0))
                end = float(item.get("end", total))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", f"{label} start/end must be numbers", field=label))
                continue
            if start < 0 or end <= start or end > total + 0.05:
                errors.append(err("CLIP_OUT_OF_RANGE", f"{label} range {start}..{end} outside timeline {total:.2f}s", field=label))
            # v1.37.0: element SFX (fired at the item's start) and,
            # for captions, word-timed active highlighting.
            _validate_sfx(item, errors, None, label)
            _validate_word_timings(item, start, end, errors, label)
            # v1.40.0: text style presets (usable on any text or
            # caption; render fills only unset fields).
            if item.get("style") is not None and item["style"] not in TEXT_STYLES:
                errors.append(err("INVALID_SCHEMA", "text style must be one of: " + ", ".join(sorted(TEXT_STYLES)), field=label + ".style",
                                  fix="set style to one of: " + ", ".join(sorted(TEXT_STYLES)) + ", or remove it"))
            # v1.44.0: animated text presets + follow_track.
            _preset = item.get("preset")
            if _preset is not None and _preset not in TEXT_PRESETS:
                errors.append(err("INVALID_SCHEMA", "text preset must be one of: " + ", ".join(TEXT_PRESETS), field=label + ".preset",
                                  fix="set preset to one of: " + ", ".join(TEXT_PRESETS) + ", or remove it"))
            if _preset is not None and item.get("word_timings"):
                errors.append(err("INVALID_SCHEMA", "text preset cannot combine with word_timings (both drive the word reveal)", field=label + ".preset",
                                  fix="use preset or word_timings on this text, not both"))
            if _preset is not None and item.get("animation"):
                errors.append(err("INVALID_SCHEMA", "text preset cannot combine with animation (the preset sets the entrance)", field=label + ".preset",
                                  fix="use preset or animation on this text, not both"))
            if _preset is not None and item.get("reveal"):
                errors.append(err("INVALID_SCHEMA", "text preset cannot combine with reveal (the preset sets the entrance)", field=label + ".preset",
                                  fix="use preset or reveal on this text, not both"))
            if "track" in item:
                if item.get("keyframes"):
                    errors.append(err("INVALID_SCHEMA", "text track cannot combine with keyframes (the track drives the position)", field=label + ".track",
                                      fix="remove keyframes, or remove track, on this text"))
                else:
                    _trk, _trk_path = _load_track_data(item["track"], label + ".track", errors)
                    if _trk is not None:
                        # Offsets stay on the element (item["track"]);
                        # the shared entry holds pure file data only
                        # (see the overlay block above).
                        _data_files[_trk_path] = {
                            "kind": "track",
                            "data": {"frame_size": _trk["frame_size"],
                                     "track": _trk["track"]}}
                        # Track times are ELEMENT-RELATIVE
                        # (v1.44.0, F2, corrected R3): sampled in
                        # the text's source time (t - start), so
                        # the track must cover [0, end - start].
                        _tdur = end - start
                        _t0 = _trk["track"][0]["t"]; _t1 = _trk["track"][-1]["t"]
                        if _t1 < 0 or _t0 > _tdur:
                            errors.append(err("INVALID_SCHEMA", f"track covers {_t0:.2f}..{_t1:.2f}s but the text source time runs 0..{_tdur:.2f}s (no overlap)", field=label + ".track",
                                              fix="use a track file covering the text's source time (t - start), or shorten the text"))
                        elif _t0 > 0 or _t1 < _tdur:
                            _extra_warnings.append({"code": "track_partial_coverage",
                                                    "message": f"{label} text track covers {_t0:.2f}..{_t1:.2f}s but the text source time runs 0..{_tdur:.2f}s; the first/last track point is held outside the track"})
            fi = fo = 0.0
            for fname in ("fade_in", "fade_out"):
                try:
                    fval = float(item.get(fname, 0))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"{label} {fname} must be a number", field=label))
                    fval = 0.0
                if fval < 0:
                    errors.append(err("INVALID_SCHEMA", f"{label} {fname} must be >= 0", field=label))
                if fname == "fade_in":
                    fi = fval
                else:
                    fo = fval
            if fi + fo >= (end - start):
                errors.append(err("INVALID_SCHEMA", f"{label} fade_in + fade_out must be less than the text duration", field=label))
            anim = item.get("animation")
            if anim is not None and anim not in ("slide_in", "slide_out", "slide_in_out", "pop", "pop_in"):
                errors.append(err("INVALID_SCHEMA", "text animation must be slide_in, slide_out, slide_in_out, pop or pop_in", field=label + ".animation",
                                  fix="set animation to slide_in, slide_out, slide_in_out, pop or pop_in, or remove it"))
            ad = item.get("animation_duration", 1.0)
            try:
                ad = float(ad)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "animation_duration must be a number", field=label + ".animation_duration",
                                  fix="set animation_duration to seconds, e.g. 1.0, shorter than the text duration"))
                ad = None
            if ad is not None:
                if ad <= 0:
                    errors.append(err("INVALID_SCHEMA", "animation_duration must be > 0", field=label + ".animation_duration",
                                      fix="set animation_duration to a positive number of seconds"))
                elif anim == "slide_in_out" and 2 * ad >= (end - start):
                    errors.append(err("INVALID_SCHEMA", "slide_in_out needs animation_duration less than half the text duration", field=label + ".animation_duration",
                                      fix="shorten animation_duration, or lengthen the text window"))
                elif anim in ("slide_in", "slide_out", "pop", "pop_in") and ad >= (end - start):
                    errors.append(err("INVALID_SCHEMA", "animation_duration must be less than the text duration", field=label + ".animation_duration",
                                      fix="shorten animation_duration, or lengthen the text window"))
            reveal = item.get("reveal")
            if reveal is not None and reveal != "word":
                errors.append(err("INVALID_SCHEMA", "text reveal must be 'word'",
                                  field=label + ".reveal",
                                  fix="set reveal to 'word' (word reveal only) or remove it"))
            if reveal == "word" and anim is not None:
                errors.append(err("INVALID_SCHEMA", "text reveal cannot combine with animation",
                                  field=label + ".reveal",
                                  fix="use reveal or animation, not both, on one text"))
            # Title badge (v1.42.0, Fix 4c): a text (titles only,
            # never captions) shows big until shrink_after, then
            # shrinks to a small top badge for the rest of its
            # window. shrink_after must leave >= 0.5s on each
            # side (the badge fades in over 0.3s). It cannot
            # combine with word_timings (the ASS event would
            # span the whole window) or reveal (prefix expansion
            # conflicts with the split); an animation is allowed
            # on the big window only, so its duration must fit
            # inside the big window too.
            if label == "captions" and "shrink_after" in item:
                errors.append(err("INVALID_SCHEMA", "shrink_after is for titles (texts), not captions",
                                  field=label + ".shrink_after",
                                  fix="remove shrink_after from this caption, or move the shrink to a title text"))
            _shrink = None
            if label == "texts" and "shrink_after" in item:
                try:
                    _shrink = float(item["shrink_after"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "text shrink_after must be a number of timeline seconds",
                                      field=label + ".shrink_after",
                                      fix="set shrink_after to e.g. 6.0, or remove it"))
                    _shrink = None
                if _shrink is not None:
                    if not start + 0.5 <= _shrink <= end - 0.5:
                        errors.append(err("INVALID_SCHEMA", "text shrink_after must leave at least 0.5s on each side of the window",
                                          field=label + ".shrink_after",
                                          fix=f"set shrink_after between {start + 0.5:.2f} and {end - 0.5:.2f}"))
                    if item.get("word_timings"):
                        errors.append(err("INVALID_SCHEMA", "text shrink_after cannot combine with word_timings",
                                          field=label + ".shrink_after",
                                          fix="use shrink_after on a plain title, or word_timings without it"))
                    if reveal == "word":
                        errors.append(err("INVALID_SCHEMA", "text shrink_after cannot combine with reveal",
                                          field=label + ".shrink_after",
                                          fix="use shrink_after or word reveal, not both, on one text"))
                    if anim is not None and ad is not None and ad >= (_shrink - start):
                        errors.append(err("INVALID_SCHEMA", "text animation_duration must be less than the big window before shrink_after",
                                          field=label + ".animation_duration",
                                          fix="shorten animation_duration, or move shrink_after later"))
                for _sfield, _slo, _shi in (("shrink_size", 8, 240),
                                            ("shrink_y", 0, 10000)):
                    if _sfield in item:
                        try:
                            _sv = float(item[_sfield])
                        except (TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", f"text {_sfield} must be a number",
                                              field=label + "." + _sfield,
                                              fix=f"set {_sfield} to a number, or remove it"))
                            continue
                        if not _slo <= _sv <= _shi:
                            errors.append(err("INVALID_SCHEMA", f"text {_sfield} must be {_slo}..{_shi}",
                                              field=label + "." + _sfield,
                                              fix=f"set {_sfield} between {_slo} and {_shi}"))
            if "shrink_size" in item or "shrink_y" in item:
                if label == "captions":
                    errors.append(err("INVALID_SCHEMA", "shrink_size/shrink_y are for titles (texts), not captions",
                                      field=label + ".shrink_after",
                                      fix="remove shrink_size/shrink_y from this caption"))
                elif "shrink_after" not in item:
                    errors.append(err("INVALID_SCHEMA", "text shrink_size/shrink_y need shrink_after",
                                      field=label + ".shrink_after",
                                      fix="add shrink_after, or remove shrink_size/shrink_y"))
            if "stroke" in item:
                try:
                    st = int(item["stroke"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "text stroke must be an integer", field=label + ".stroke",
                                      fix="set stroke to a whole number 0..10 for the outline width"))
                    st = None
                if st is not None and not 0 <= st <= 10:
                    errors.append(err("INVALID_SCHEMA", "text stroke must be 0..10", field=label + ".stroke",
                                      fix="set stroke between 0 (no outline) and 10"))
            sc = item.get("stroke_color", "black")
            if not (isinstance(sc, str) and (sc in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", sc))):
                errors.append(err("INVALID_SCHEMA", "stroke_color must be a #RRGGBB hex value or a named colour", field=label + ".stroke_color",
                                  fix="set stroke_color to a named colour or #RRGGBB, e.g. black"))
            # Text shadow (v1.36.0): drawtext shadowx/shadowy. Flat
            # fields mirroring stroke/stroke_color. Captions get a
            # subtle default shadow at render time (see render.py);
            # an explicit 0 disables it.
            for _axis in ("shadow_x", "shadow_y"):
                if _axis in item:
                    try:
                        _sv = int(item[_axis])
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", f"text {_axis} must be an integer", field=label + f".{_axis}",
                                          fix=f"set {_axis} to a pixel offset -20..20"))
                        _sv = None
                    if _sv is not None and not -20 <= _sv <= 20:
                        errors.append(err("INVALID_SCHEMA", f"text {_axis} must be -20..20", field=label + f".{_axis}",
                                          fix=f"set {_axis} between -20 and 20 pixels"))
            _shc = item.get("shadow_color", "black")
            if not (isinstance(_shc, str) and (_shc in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", _shc))):
                errors.append(err("INVALID_SCHEMA", "shadow_color must be a #RRGGBB hex value or a named colour", field=label + ".shadow_color",
                                  fix="set shadow_color to a named colour or #RRGGBB, e.g. black"))
            # Rounded text background boxes (v1.36.0): a generated
            # rounded-rect PNG under the text, sized from the same
            # estimate as the collision boxes. Constraints, by
            # design: the box is static, so it cannot combine with
            # animation (the text moves but the box would not) or
            # fades (the text fades but the box would not), and it
            # replaces the rectangular drawtext box (box must stay
            # true). Wrapped caption lines each get their own pill.
            _br = item.get("box_radius", 0)
            try:
                _br = int(_br)
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "box_radius must be an integer", field=label + ".box_radius",
                                  fix="set box_radius to 0 (rectangular box) or a radius 1..100"))
                _br = 0
            if _br < 0 or _br > 100:
                errors.append(err("INVALID_SCHEMA", "box_radius must be 0..100", field=label + ".box_radius",
                                  fix="set box_radius between 0 and 100 pixels"))
            if _br > 0:
                if item.get("box", True) is False:
                    errors.append(err("INVALID_SCHEMA", "box_radius needs box enabled (it replaces the rectangular box)", field=label + ".box_radius",
                                      fix="remove box: false, or set box_radius to 0"))
                if anim is not None:
                    errors.append(err("INVALID_SCHEMA", "box_radius cannot combine with animation (the box would not move with the text)", field=label + ".box_radius",
                                      fix="remove animation, or set box_radius to 0"))
                if fi > 0 or fo > 0:
                    errors.append(err("INVALID_SCHEMA", "box_radius cannot combine with fade_in/fade_out (the box would not fade with the text)", field=label + ".box_radius",
                                      fix="remove fade_in/fade_out, or set box_radius to 0"))
            _bc = item.get("box_color", "black")
            if not (isinstance(_bc, str) and (_bc in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", _bc))):
                errors.append(err("INVALID_SCHEMA", "box_color must be a #RRGGBB hex value or a named colour", field=label + ".box_color",
                                  fix="set box_color to a named colour or #RRGGBB, e.g. black"))
            if "box_opacity" in item:
                try:
                    _bo = float(item["box_opacity"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "box_opacity must be a number", field=label + ".box_opacity",
                                      fix="set box_opacity between 0 (transparent) and 1 (opaque)"))
                    _bo = None
                if _bo is not None and not 0.0 <= _bo <= 1.0:
                    errors.append(err("INVALID_SCHEMA", "box_opacity must be 0..1", field=label + ".box_opacity",
                                      fix="set box_opacity between 0 and 1"))
            if "box_padding" in item:
                try:
                    _bp = int(item["box_padding"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "box_padding must be an integer", field=label + ".box_padding",
                                      fix="set box_padding to a pixel count 0..40"))
                    _bp = None
                if _bp is not None and not 0 <= _bp <= 40:
                    errors.append(err("INVALID_SCHEMA", "box_padding must be 0..40", field=label + ".box_padding",
                                      fix="set box_padding between 0 and 40 pixels"))
            color = item.get("color", "white")
            if not (isinstance(color, str) and (color in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", color))):
                errors.append(err("INVALID_SCHEMA", "text color must be a #RRGGBB hex value or one of: " + ", ".join(sorted(TEXT_COLORS)), field=label + ".color"))
            if "box" in item and not isinstance(item["box"], bool):
                errors.append(err("INVALID_SCHEMA", "text box must be true or false", field=label + ".box"))
            # v1.41.0: font option — a bundled font by registry
            # key (noto-sans-bold is the Arabic display face).
            if "font" in item:
                if not isinstance(item["font"], str) or item["font"] not in BUNDLED_FONTS:
                    errors.append(err("INVALID_SCHEMA", "text font must be one of: " + ", ".join(sorted(BUNDLED_FONTS)), field=label + ".font",
                                      fix="set font to a bundled font, e.g. 'noto-sans-bold' for Arabic display titles, or remove it for the script default"))
                elif find_bundled_font(item["font"]) is None:
                    errors.append(err("FONT_NOT_FOUND", "bundled font '" + item["font"] + "' file is missing", field=label + ".font",
                                      fix="restore the bundled fonts/ directory, or remove the font option"))
            # v1.41.0: hold — kinetic text base words persist to
            # the text's own end by default (scene titles stay
            # for the whole scene); false keeps the old
            # chunk-window behaviour. Render applies it on the
            # text path only; captions stay timed cues.
            if "hold" in item and not isinstance(item["hold"], bool):
                errors.append(err("INVALID_SCHEMA", "text hold must be true or false", field=label + ".hold",
                                  fix="set hold to true (base words persist to the text end) or false (old chunk-window behaviour), or remove it"))
            position = item.get("position")
            if position is not None and position not in TEXT_POSITIONS:
                errors.append(err("INVALID_SCHEMA", "text position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field=label + ".position"))
            # Text geometry (1.34.0): size and x/y used to be
            # unvalidated, so a non-numeric size or a negative
            # coordinate passed validation and failed only inside
            # FFmpeg. Captions ignore x/y entirely in render
            # (position or the bottom default), so a caption x/y
            # is rejected rather than silently ignored.
            if "size" in item:
                try:
                    sv = float(item["size"])
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", f"{label} size must be a number", field=label + ".size",
                                      fix="set size to a number of pixels, e.g. 48 for texts or 36 for captions"))
                    sv = None
                if sv is not None and not 8 <= sv <= 240:
                    errors.append(err("INVALID_SCHEMA", f"{label} size must be 8..240", field=label + ".size",
                                      fix="set size between 8 and 240 pixels"))
            if label == "captions":
                if "x" in item or "y" in item:
                    errors.append(err("INVALID_SCHEMA", "captions must not set x/y (use position or caption_position)", field=label,
                                      fix="remove x/y from this caption and set position, or set caption_position on the project"))
            else:
                xv = item.get("x", "center")
                if xv != "center":
                    try:
                        xvf = float(xv)
                    except (TypeError, ValueError):
                        errors.append(err("INVALID_SCHEMA", "text x must be 'center' or a number", field=label + ".x",
                                          fix="set x to 'center' or a pixel offset >= 0"))
                        xvf = None
                    if xvf is not None and xvf < 0:
                        errors.append(err("INVALID_SCHEMA", "text x must be >= 0", field=label + ".x",
                                          fix="set x to 'center' or a pixel offset >= 0"))
                try:
                    yvf = float(item.get("y", 100))
                except (TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "text y must be a number", field=label + ".y",
                                      fix="set y to a pixel offset >= 0"))
                    yvf = None
                if yvf is not None and yvf < 0:
                    errors.append(err("INVALID_SCHEMA", "text y must be >= 0", field=label + ".y",
                                      fix="set y to a pixel offset >= 0"))
    # Project-level caption defaults: colour/size/position applied to
    # every caption (list captions and SRT cues) that does not set its
    # own value. SRT files carry no styling, so this is how SRT
    # captions get styled.
    caption_color = project.get("caption_color")
    if caption_color is not None and not (isinstance(caption_color, str) and (caption_color in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", caption_color))):
        errors.append(err("INVALID_SCHEMA", "caption_color must be a #RRGGBB hex value or a named colour", field="caption_color"))
    caption_size = project.get("caption_size")
    if caption_size is not None:
        try:
            caption_size = float(caption_size)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "caption_size must be a number", field="caption_size"))
            caption_size = None
        if caption_size is not None and not 12 <= caption_size <= 120:
            errors.append(err("INVALID_SCHEMA", "caption_size must be 12..120", field="caption_size"))
    caption_position = project.get("caption_position")
    if caption_position is not None and caption_position not in TEXT_POSITIONS:
        errors.append(err("INVALID_SCHEMA", "caption_position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field="caption_position"))
    # Caption wrapping controls (v1.36.0): cues wider than
    # caption_max_width (fraction of frame width) word-wrap into
    # centred lines stacked at caption_line_spacing x size.
    # Default 0.8 (v1.41.0 Fix 2 cap) / 1.35; explicit 0.9 still
    # available for projects that want the old wider block.
    if "caption_max_width" in project:
        try:
            _cmw = float(project["caption_max_width"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "caption_max_width must be a number", field="caption_max_width",
                              fix="set caption_max_width to a fraction of the frame width, e.g. 0.8"))
            _cmw = None
        if _cmw is not None and not 0.5 <= _cmw <= 1.0:
            errors.append(err("INVALID_SCHEMA", "caption_max_width must be 0.5..1.0", field="caption_max_width",
                              fix="set caption_max_width between 0.5 and 1.0 (0.8 = 80% of the frame width)"))
    if "caption_line_spacing" in project:
        try:
            _cls = float(project["caption_line_spacing"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "caption_line_spacing must be a number", field="caption_line_spacing",
                              fix="set caption_line_spacing to a line-height multiplier, e.g. 1.35"))
            _cls = None
        if _cls is not None and not 1.0 <= _cls <= 2.5:
            errors.append(err("INVALID_SCHEMA", "caption_line_spacing must be 1.0..2.5", field="caption_line_spacing",
                              fix="set caption_line_spacing between 1.0 (tight) and 2.5 (loose)"))
    # Gradient scrims (v1.36.0): optional project-level top/bottom
    # black gradients, over clips and under texts/captions/overlays
    # (see render.py). Each side is false (off), true (defaults),
    # or {"opacity": 0..1, "depth": 0..0.6 (fraction of height)}.
    # Default off, so old projects render identically.
    scrims = project.get("scrims")
    if scrims is not None:
        if not isinstance(scrims, dict):
            errors.append(err("INVALID_SCHEMA", "scrims must be an object with optional top/bottom entries", field="scrims",
                              fix="set scrims to e.g. {\"bottom\": {\"opacity\": 0.6, \"depth\": 0.35}} or remove it"))
        else:
            for _side in ("top", "bottom"):
                if _side not in scrims:
                    continue
                _sv = scrims[_side]
                if isinstance(_sv, bool):
                    continue
                if not isinstance(_sv, dict):
                    errors.append(err("INVALID_SCHEMA", f"scrims.{_side} must be true, false, or an object", field=f"scrims.{_side}",
                                      fix=f"set scrims.{_side} to true, false, or {{\"opacity\": 0.6, \"depth\": 0.35}}"))
                    continue
                for _fname, _lo, _hi in (("opacity", 0.0, 1.0), ("depth", 0.05, 0.6)):
                    if _fname in _sv:
                        try:
                            _fv = float(_sv[_fname])
                        except (TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", f"scrims.{_side}.{_fname} must be a number", field=f"scrims.{_side}.{_fname}"))
                            continue
                        if not _lo <= _fv <= _hi:
                            errors.append(err("INVALID_SCHEMA", f"scrims.{_side}.{_fname} must be {_lo}..{_hi}", field=f"scrims.{_side}.{_fname}",
                                              fix=f"set scrims.{_side}.{_fname} between {_lo} and {_hi}"))
    # General image treatment (v1.40.0): project-level treatment
    # applies to the joined timeline (after scrims, before
    # overlays/texts, so graphics stay clean); per-clip treatment
    # is validated in the clip loop above.
    _validate_treatment(project, errors, None, "")
    # Default micro-motion (v1.37.0): when true, image clips
    # without an explicit ken_burns get alternating zoom_in /
    # zoom_out by clip index (see effective_ken_burns). It is a
    # project-level switch, so old projects (field absent) render
    # identically.
    if "default_image_motion" in project and not isinstance(project["default_image_motion"], bool):
        errors.append(err("INVALID_SCHEMA", "default_image_motion must be true or false", field="default_image_motion",
                          fix="set default_image_motion to true to auto-animate still images, or remove it"))
    merged_captions = [dict(c) if isinstance(c, dict) else c for c in (project.get("captions") or [])]
    captions_file = project.get("captions_file")
    if captions_file is not None:
        if not isinstance(captions_file, str) or not captions_file:
            errors.append(err("INVALID_SCHEMA", "captions_file must be a non-empty path", field="captions_file"))
        else:
            resolved_srt = resolve_path(captions_file)
            if not os.path.isfile(resolved_srt):
                errors.append(err("FILE_NOT_FOUND", f"SRT file not found: {captions_file}", field="captions_file"))
            else:
                try:
                    file_cues = parse_srt_file(resolved_srt)
                except Exception as exc:
                    errors.append(err("INVALID_SCHEMA", f"cannot parse SRT file: {exc}", field="captions_file"))
                else:
                    for cue in file_cues:
                        if cue["start"] < 0 or cue["end"] > total + 0.05:
                            errors.append(err("CLIP_OUT_OF_RANGE", f"SRT cue {cue['start']}..{cue['end']} outside timeline {total:.2f}s", field="captions_file"))
                    merged_captions.extend(file_cues)
    for _cap in merged_captions:
        if not isinstance(_cap, dict):
            continue
        if caption_color is not None:
            _cap.setdefault("color", caption_color)
        if caption_size is not None:
            _cap.setdefault("size", caption_size)
        if caption_position is not None:
            _cap.setdefault("position", caption_position)
    # Per-text font requirements (v1.35.0, on-device portability):
    # every rendered text needs a font for its own script. Arabic
    # text with no Arabic font is a hard error, never a silent
    # fallback to a Latin font (which would render tofu boxes);
    # Latin text with no Latin font is a hard error the same way.
    # The fix names the two portable remedies: the bundled fonts/
    # directory, or $AGENTCUT_FONT_DIR.
    for _t in list(project.get("texts") or []) + merged_captions:
        if not isinstance(_t, dict) or not isinstance(_t.get("text"), str):
            continue
        _txt = _t["text"]
        if not _txt:
            continue
        if contains_arabic(_txt):
            if find_font_for_text(_txt) is None:
                errors.append(err("FONT_NOT_FOUND",
                                  f"no Arabic font found for text {_txt[:40]!r}; "
                                  "Arabic text needs Noto Sans Arabic",
                                  field="texts",
                                  fix="use the bundled fonts/ directory, or set AGENTCUT_FONT_DIR to a directory containing NotoSansArabic-Bold.ttf"))
                break
        elif find_font() is None:
            errors.append(err("FONT_NOT_FOUND",
                              f"no Latin font found for text {_txt[:40]!r}",
                              field="texts",
                              fix="use the bundled fonts/ directory, or set AGENTCUT_FONT_DIR to a directory containing DejaVuSans.ttf"))
            break
    # Resolved audio copies (1.34.0): validation used to mutate the
    # caller's project in place (resolving track srcs and coercing
    # starts), which leaked absolute paths into stored projects
    # whenever the server ran from another directory. Tracks are
    # now copied; the resolved list lives in info["audio"] and the
    # render uses it, while the caller's project is untouched.
    resolved_audio = []
    for track in project.get("audio") or []:
        if not isinstance(track, dict) or not isinstance(track.get("src"), str):
            errors.append(err("INVALID_SCHEMA", "each audio track needs a src path", field="audio"))
            continue
        track = dict(track)
        resolved_audio.append(track)
        track["src"] = resolve_path(track["src"])
        if not os.path.isfile(track["src"]):
            errors.append(err("FILE_NOT_FOUND", f"audio file not found: {track['src']}", field="audio.src"))
        if track.get("kind", "music") not in ("voice", "music"):
            errors.append(err("INVALID_SCHEMA", "audio kind must be 'voice' or 'music'", field="audio.kind"))
        try:
            vol = float(track.get("volume", 1.0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "audio volume must be a number", field="audio.volume"))
            continue
        if vol < 0 or vol > 2:
            errors.append(err("INVALID_SCHEMA", "audio volume must be 0..2", field="audio.volume"))
        if "denoise" in track and not isinstance(track["denoise"], bool):
            errors.append(err("INVALID_SCHEMA", "audio denoise must be true or false", field="audio.denoise",
                              fix="set denoise to true to run FFT noise reduction on this track, or remove it"))
        if "loudnorm" in track and not isinstance(track["loudnorm"], bool):
            errors.append(err("INVALID_SCHEMA", "audio loudnorm must be true or false", field="audio.loudnorm"))
        try:
            start = float(track.get("start", 0))
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "audio start must be a number", field="audio.start"))
            continue
        if start < 0:
            errors.append(err("INVALID_SCHEMA", "audio start must be >= 0", field="audio.start"))
        elif start >= total:
            errors.append(err("CLIP_OUT_OF_RANGE", f"audio start {start}s is at or after timeline end {total:.2f}s", field="audio.start"))
        fade_in = fade_out = 0.0
        for fname in ("fade_in", "fade_out"):
            try:
                fval = float(track.get(fname, 0))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", f"audio {fname} must be a number", field=f"audio.{fname}"))
                fval = 0.0
            if fval < 0:
                errors.append(err("INVALID_SCHEMA", f"audio {fname} must be >= 0", field=f"audio.{fname}"))
            if fname == "fade_in":
                fade_in = fval
            else:
                fade_out = fval
        if fade_in + fade_out >= total and total > 0:
            errors.append(err("INVALID_SCHEMA", f"audio fade_in + fade_out ({fade_in + fade_out}s) must be less than the timeline ({total:.2f}s)", field="audio"))
        track["start"] = start
    # Pro pack: project-level output options for delivery presets.
    # video_bitrate is a bitrate string (e.g. "8M", "5000k") or a
    # positive integer (kbps); loudness_target is a LUFS value
    # used by loudnorm tracks (default -16 when absent).
    video_bitrate = None
    loudness_target = -16.0
    master_loudnorm = False
    loudnorm_i = -14.0
    color_pipeline = "yuv"
    if isinstance(output, dict):
        if "video_bitrate" in output:
            vb = output["video_bitrate"]
            if isinstance(vb, bool) or (isinstance(vb, int) and vb <= 0) or \
                    (isinstance(vb, str) and not re.fullmatch(r"[0-9]+(\.[0-9]+)?[kKmM]", vb)) or \
                    not isinstance(vb, (int, str)):
                errors.append(err("INVALID_SCHEMA", "output video_bitrate must be a bitrate like '8M' or a positive integer kbps", field="output.video_bitrate",
                                  fix="set video_bitrate to e.g. '8M', or remove it to use the default quality setting"))
            else:
                video_bitrate = vb
        if "loudness_target" in output:
            try:
                lt = float(output["loudness_target"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "output loudness_target must be a number", field="output.loudness_target",
                                  fix="set loudness_target to a LUFS value such as -14 or -16, or remove it"))
                lt = None
            if lt is not None:
                if not -30.0 <= lt <= 0.0:
                    errors.append(err("INVALID_SCHEMA", "output loudness_target must be -30..0 LUFS", field="output.loudness_target",
                                      fix="set loudness_target between -30 and 0, e.g. -14"))
                else:
                    loudness_target = lt
        if "master_loudnorm" in output:
            # Delta: final-master loudnorm switch (render.py
            # applies loudnorm I=-14:TP=-1.5:LRA=11 to the
            # finished mix). Distinct from per-track loudnorm
            # and from delivery presets.
            if not isinstance(output["master_loudnorm"], bool):
                errors.append(err("INVALID_SCHEMA", "output master_loudnorm must be true or false", field="output.master_loudnorm",
                                  fix="set master_loudnorm to true to master the final mix at -14 LUFS, or remove it"))
            else:
                master_loudnorm = output["master_loudnorm"]
        if "loudnorm_i" in output:            # v1.41.0: master loudnorm target. Default -14
            # (the established video master standard; the
            # spec's -16 is selectable here, not forced,
            # because -14 is what every delivery preset and
            # the v1 verified output used).
            try:
                _li = float(output["loudnorm_i"])
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "output loudnorm_i must be a number", field="output.loudnorm_i",
                                  fix="set loudnorm_i to a LUFS value such as -14 or -16, or remove it"))
                _li = None
            if _li is not None:
                if not -70.0 <= _li <= -5.0:
                    errors.append(err("INVALID_SCHEMA", "output loudnorm_i must be -70..-5 LUFS", field="output.loudnorm_i",
                                      fix="set loudnorm_i between -70 and -5, e.g. -14"))
                else:
                    loudnorm_i = _li
        if "color_pipeline" in output:
            # v1.41.0 (Fix 1): "yuv" (default, current
            # behaviour) or "rgb" (full-range gbrp internal
            # graph + single final conversion to yuv420p).
            if output["color_pipeline"] not in ("yuv", "rgb"):
                errors.append(err("INVALID_SCHEMA", "output color_pipeline must be 'yuv' or 'rgb'", field="output.color_pipeline",
                                  fix="set color_pipeline to 'rgb' for the full-range internal graph, or remove it (default 'yuv')"))
            else:
                color_pipeline = output["color_pipeline"]
    # Pro pack: chapters. An explicit list of {title, start} written
    # into the output file's metadata at render time. Starts must
    # strictly increase and stay inside the timeline; each chapter
    # runs until the next chapter's start, or the timeline end.
    chapters = []
    raw_chapters = project.get("chapters")
    if raw_chapters is not None:
        if not isinstance(raw_chapters, list) or not raw_chapters:
            errors.append(err("INVALID_SCHEMA", "chapters must be a non-empty list of {title, start} objects", field="chapters",
                              fix="give chapters as e.g. [{'title': 'Intro', 'start': 0}], or remove chapters"))
        else:
            prev_start = None
            for ci, ch in enumerate(raw_chapters):
                if not isinstance(ch, dict) or not isinstance(ch.get("title"), str) or not ch["title"].strip():
                    errors.append(err("INVALID_SCHEMA", "each chapter needs a non-empty title string", field="chapters.title",
                                      fix="give every chapter a title, e.g. 'Intro'"))
                    continue
                try:
                    cs = float(ch["start"])
                except (KeyError, TypeError, ValueError):
                    errors.append(err("INVALID_SCHEMA", "each chapter needs a numeric start", field="chapters.start",
                                      fix="set start to the timeline second where the chapter begins"))
                    continue
                if cs < 0 or cs >= total:
                    errors.append(err("CLIP_OUT_OF_RANGE", f"chapter start {cs}s outside timeline {total:.2f}s", field="chapters.start",
                                      fix=f"set chapter starts between 0 and {total:.2f}"))
                    continue
                if prev_start is not None and cs <= prev_start:
                    errors.append(err("INVALID_SCHEMA", "chapter starts must strictly increase", field="chapters.start",
                                      fix="sort chapters by start with no duplicates"))
                    continue
                if "\n" in ch["title"] or "\r" in ch["title"]:
                    errors.append(err("INVALID_SCHEMA", "chapter titles must not contain newlines", field="chapters.title",
                                      fix="remove newlines from the chapter title"))
                    continue
                prev_start = cs
                chapters.append({"title": ch["title"], "start": cs})
            for ci, ch in enumerate(chapters):
                ch["end"] = chapters[ci + 1]["start"] if ci + 1 < len(chapters) else total
    # Pro pack: explicit timecode burn-in. False (the default)
    # means final renders carry no timecode; True burns a running
    # timecode into the final render itself. Previews may add
    # timecode independently (see preview_project).
    if "timecode" in project and not isinstance(project["timecode"], bool):
        errors.append(err("INVALID_SCHEMA", "timecode must be true or false", field="timecode",
                          fix="set timecode to true to burn a timecode into the render, or remove it"))
    # Pro pack: deterministic ducking. An explicit ducking object
    # replaces the automatic sidechain compressor for music tracks:
    # while the voice track's time windows are active, music gain
    # drops by amount_db with smooth fades. This is volume
    # automation from time windows, NOT sidechain compression and
    # NOT level detection — it ducks on schedule even if the voice
    # track itself is quiet, and it never ducks outside windows.
    ducking = None
    raw_duck = project.get("ducking")
    if raw_duck is not None:
        audio_tracks = resolved_audio
        music_tracks = [t for t in audio_tracks if isinstance(t, dict) and t.get("kind") == "music"]
        if not isinstance(raw_duck, dict):
            errors.append(err("INVALID_SCHEMA", "ducking must be an object", field="ducking",
                              fix="set ducking to e.g. {'voice_track': 1, 'amount_db': 12, 'fade': 0.25}"))
        elif not music_tracks:
            errors.append(err("INVALID_SCHEMA", "ducking needs a music track to duck", field="ducking",
                              fix="add a music audio track, or remove ducking"))
        else:
            vref = raw_duck.get("voice_track")
            voice_index = None
            if isinstance(vref, bool) or not isinstance(vref, (int, str)):
                errors.append(err("INVALID_SCHEMA", "ducking voice_track must be an audio-track index or a track src", field="ducking.voice_track",
                                  fix="set voice_track to the index of the voice track in audio (e.g. 1), or to its src path"))
            elif isinstance(vref, int):
                if not 0 <= vref < len(audio_tracks):
                    errors.append(err("INVALID_SCHEMA", f"ducking voice_track index {vref} is outside the audio list", field="ducking.voice_track",
                                      fix=f"set voice_track to an index between 0 and {len(audio_tracks) - 1}"))
                elif audio_tracks[vref].get("kind") != "voice":
                    errors.append(err("INVALID_SCHEMA", "ducking voice_track must point at a voice track", field="ducking.voice_track",
                                      fix="point voice_track at a track whose kind is 'voice'"))
                else:
                    voice_index = vref
            else:
                matches = [i for i, t in enumerate(audio_tracks)
                           if isinstance(t, dict) and t.get("kind") == "voice"
                           and (t.get("src") == vref or os.path.basename(str(t.get("src", ""))) == os.path.basename(vref))]
                if len(matches) != 1:
                    errors.append(err("INVALID_SCHEMA", f"ducking voice_track {vref!r} does not match exactly one voice track", field="ducking.voice_track",
                                      fix="set voice_track to the voice track's index in audio, or to its exact src path"))
                else:
                    voice_index = matches[0]
            try:
                amount_db = float(raw_duck.get("amount_db", 12.0))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "ducking amount_db must be a number", field="ducking.amount_db",
                                  fix="set amount_db to a number of decibels, e.g. 12"))
                amount_db = None
            if amount_db is not None and not 0.0 <= amount_db <= 30.0:
                errors.append(err("INVALID_SCHEMA", "ducking amount_db must be 0..30 dB", field="ducking.amount_db",
                                  fix="set amount_db between 0 and 30"))
                amount_db = None
            try:
                fade = float(raw_duck.get("fade", 0.25))
            except (TypeError, ValueError):
                errors.append(err("INVALID_SCHEMA", "ducking fade must be a number", field="ducking.fade",
                                  fix="set fade to seconds, e.g. 0.25, or remove it"))
                fade = None
            if fade is not None and not 0.0 <= fade <= 2.0:
                errors.append(err("INVALID_SCHEMA", "ducking fade must be 0..2 seconds", field="ducking.fade",
                                  fix="set fade between 0 and 2 seconds"))
                fade = None
            windows = None
            if "windows" in raw_duck:
                if not isinstance(raw_duck["windows"], list) or not raw_duck["windows"]:
                    errors.append(err("INVALID_SCHEMA", "ducking windows must be a non-empty list of {start, end} objects", field="ducking.windows",
                                      fix="give windows as e.g. [{'start': 2, 'end': 5}], or omit windows to derive them from the voice track"))
                else:
                    windows = []
                    prev_end = None
                    for w in raw_duck["windows"]:
                        if not isinstance(w, dict):
                            errors.append(err("INVALID_SCHEMA", "each ducking window must be an object", field="ducking.windows",
                                              fix="make each window an object with start and end"))
                            windows = None
                            break
                        try:
                            ws, we = float(w["start"]), float(w["end"])
                        except (KeyError, TypeError, ValueError):
                            errors.append(err("INVALID_SCHEMA", "ducking windows need numeric start and end", field="ducking.windows",
                                              fix="set start and end to timeline seconds"))
                            windows = None
                            break
                        if ws < 0 or we <= ws or we > total + 0.05:
                            errors.append(err("CLIP_OUT_OF_RANGE", f"ducking window {ws}..{we}s outside timeline {total:.2f}s", field="ducking.windows",
                                              fix=f"keep windows inside 0..{total:.2f} with end after start"))
                            windows = None
                            break
                        if prev_end is not None and ws < prev_end:
                            errors.append(err("INVALID_SCHEMA", "ducking windows must not overlap", field="ducking.windows",
                                              fix="sort windows by start with no overlaps"))
                            windows = None
                            break
                        prev_end = we
                        windows.append({"start": ws, "end": min(we, total)})
            if voice_index is not None and amount_db is not None and fade is not None and windows is None and "windows" not in raw_duck:
                # Derive the window from the voice track: its start
                # plus its probed source duration, clipped to the
                # timeline. This is the voice segment's time window.
                vtrack = audio_tracks[voice_index]
                vprobe = probe_media(vtrack.get("src", ""))
                if vprobe is None or vprobe.get("duration") is None:
                    errors.append(err("CLIP_OUT_OF_RANGE", "ducking cannot derive a window: the voice track duration could not be probed", field="ducking.voice_track",
                                      fix="give explicit ducking windows, or replace the voice track with a file ffprobe can read"))
                else:
                    vs = float(vtrack.get("start", 0))
                    ve = min(vs + float(vprobe["duration"]), total)
                    windows = [{"start": vs, "end": ve}] if ve > vs else []
            if voice_index is not None and amount_db is not None and fade is not None and windows is not None:
                ducking = {"voice_index": voice_index, "amount_db": amount_db,
                           "fade": fade, "windows": windows}
    # v1.41.0: duck_amount_db — an explicit, gentler ducking
    # depth for the automatic music duck. Precedence, exactly:
    # explicit ducking object > duck_amount_db > auto sidechain
    # (auto_duck true, voice + music present) > plain mix.
    # When set and no explicit ducking exists, it synthesizes
    # the same deterministic ducking object at the given depth
    # (same window derivation from the voice track), so the old
    # 12 dB default is a choice, not a trap: a bed at volume
    # 0.3 under 12 dB of ducking sits ~22 dB under narration
    # (measured on the AI-race mix: voice -15.99 LUFS, bed
    # -16.37 LUFS, 0.3 = -10.46 dB, minus 12 dB = -38.83 LUFS
    # effective) and is inaudible on phone speakers.
    raw_dadb = project.get("duck_amount_db")
    if ducking is None and raw_dadb is not None:
        try:
            dadb = float(raw_dadb)
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "duck_amount_db must be a number", field="duck_amount_db",
                              fix="set duck_amount_db to a number 0..30, e.g. 6 for a gentler duck, or remove it"))
            dadb = None
        if dadb is not None:
            if isinstance(raw_dadb, bool) or not 0 <= dadb <= 30:
                errors.append(err("INVALID_SCHEMA", "duck_amount_db must be 0..30 dB", field="duck_amount_db",
                                  fix="set duck_amount_db between 0 and 30"))
            else:
                _vidx = next((i for i, t in enumerate(resolved_audio) if t.get("kind") == "voice"), None)
                _has_music = any(t.get("kind") == "music" for t in resolved_audio)
                if _vidx is None:
                    errors.append(err("INVALID_SCHEMA", "duck_amount_db needs a voice track to duck under", field="duck_amount_db",
                                      fix="add a voice audio track, or remove duck_amount_db"))
                elif not _has_music:
                    errors.append(err("INVALID_SCHEMA", "duck_amount_db needs a music track to duck", field="duck_amount_db",
                                      fix="add a music audio track, or remove duck_amount_db"))
                else:
                    _vt = resolved_audio[_vidx]
                    _vp = probe_media(_vt.get("src", ""))
                    if _vp is None or _vp.get("duration") is None:
                        errors.append(err("CLIP_OUT_OF_RANGE", "duck_amount_db cannot derive a window: the voice track duration could not be probed", field="duck_amount_db",
                                          fix="give explicit ducking windows, or replace the voice track with a file ffprobe can read"))
                    else:
                        _vs = float(_vt.get("start", 0))
                        _ve = min(_vs + float(_vp["duration"]), total)
                        _wins = [{"start": _vs, "end": _ve}] if _ve > _vs else []
                        ducking = {"voice_index": _vidx, "amount_db": dadb,
                                   "fade": 0.25, "windows": _wins}
    # Delta: explicit auto-duck switch. Precedence, exactly:
    # explicit ducking object > duck_amount_db (v1.41.0) > auto
    # sidechain (auto_duck true, voice + music present, no
    # explicit ducking) > plain mix.
    # Default True is the pre-delta behaviour, so existing
    # projects render identically; False disables only the auto
    # path (an explicit ducking object and duck_amount_db still
    # win over False).
    auto_duck = True
    raw_auto = project.get("auto_duck")
    if raw_auto is not None:
        if not isinstance(raw_auto, bool):
            errors.append(err("INVALID_SCHEMA", "auto_duck must be true or false", field="auto_duck",
                              fix="set auto_duck to false to disable automatic sidechain ducking, or remove it (default true)"))
        else:
            auto_duck = raw_auto
    # Collision-proof layout (v1.41.0): resolve_overlaps must
    # be a boolean when present; True moves keyword pops
    # below overlapped boxes (see check_layer_text_overlap).
    if "resolve_overlaps" in project and not isinstance(
            project["resolve_overlaps"], bool):
        errors.append(err("INVALID_SCHEMA",
                          "resolve_overlaps must be true or false",
                          field="resolve_overlaps",
                          fix="set resolve_overlaps to true to auto-move "
                              "overlapping keyword pops, or remove it"))
    # Auto sub-cuts (v1.42.0, Fix 4b): auto_subcuts must be a
    # boolean when present; True (the default) generates
    # multi-stop camera paths for still-image clips longer
    # than 12s. Per-clip auto_subcuts is validated in the
    # clip loop, next to ken_burns.
    if "auto_subcuts" in project and not isinstance(
            project["auto_subcuts"], bool):
        errors.append(err("INVALID_SCHEMA",
                          "auto_subcuts must be true or false",
                          field="auto_subcuts",
                          fix="set auto_subcuts to false to keep long "
                              "stills frozen, or remove it"))
    # SFX discipline (v1.42.0, Fix 5): sfx_attenuation_db is
    # the dB below the voiceover for auto-SFX (default 16,
    # the spec's -14..-18 band); 0..30.
    if "sfx_attenuation_db" in project:
        try:
            _att = float(project["sfx_attenuation_db"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA",
                              "sfx_attenuation_db must be a number",
                              field="sfx_attenuation_db",
                              fix="set sfx_attenuation_db between 0 and "
                                  "30, e.g. 16, or remove it"))
            _att = None
        if _att is not None and not 0 <= _att <= 30:
            errors.append(err("INVALID_SCHEMA",
                              "sfx_attenuation_db must be 0..30",
                              field="sfx_attenuation_db",
                              fix="set sfx_attenuation_db between 0 and "
                                  "30, e.g. 16"))
    # Animated stroke diagrams (v1.38.0): validated after the
    # overlays so diagram ids can be checked against overlay ids.
    # Diagrams are resolved data (strokes/templates expanded),
    # never file references, so render packs carry them verbatim
    # in project.json and regenerate the animation at render.
    resolved_diagrams, _adapted_motion = _validate_diagrams(
        project, total, width, height,
        {ov["id"] for ov in resolved_overlays}, errors)
    # Motion graphics (v1.43.0): validated after the diagrams so
    # motion ids can be checked against overlay and diagram ids.
    # The adapted template diagrams (v1.45.0, R4b-3) join the
    # motion list for validation, and their ids are excluded
    # from the diagram-ids set — the adapted motion element
    # carries its diagram's id, so it would otherwise be
    # rejected as a duplicate of itself. A user motion element
    # colliding with an adapted diagram id is still caught by
    # the motion list's own duplicate check.
    _motion_project = dict(project)
    _motion_project["motion"] = ((project.get("motion") or [])
                                 + _adapted_motion)
    resolved_motion = _validate_motion(
        _motion_project, total, width, height, fps,
        {ov["id"] for ov in resolved_overlays},
        {dg["id"] for dg in resolved_diagrams
         if not dg.get("adapted_to_motion")}, errors)
    # Typography profile (v1.44.0, F4): project-level default
    # style, opt-in only — a project without `typography`
    # renders exactly as before.
    _typography = {}
    if "typography" in project:
        _typo = project["typography"]
        if not isinstance(_typo, dict):
            errors.append(err("INVALID_SCHEMA", "typography must be an object", field="typography",
                              fix="set typography to {default_style: 'display'}, or remove it"))
        elif _typo.get("default_style") is not None and _typo["default_style"] not in TEXT_STYLES:
            errors.append(err("INVALID_SCHEMA", "typography default_style must be one of: " + ", ".join(sorted(TEXT_STYLES)), field="typography.default_style",
                              fix="set default_style to one of: " + ", ".join(sorted(TEXT_STYLES)) + ", or remove it"))
        elif _typo.get("default_style"):
            _typography = {"default_style": _typo["default_style"]}
    # Adjustment layers (v1.44.0, F6b): grade/effects windows
    # over the base video only.
    _adjustments = _validate_adjustments(project, total, errors)
    # Custom transitions (v1.44.0, F5): the post-join effect pass
    # straddles each cut; the cut time is the clip's timeline
    # start (clip_starts already accounts for xfade overlaps
    # elsewhere in the join, and for beat-cut expansion above).
    _custom_transitions = [
        {"time": clip_starts[i], "kind": c.get("transition"),
         "duration": float(c.get("transition_duration", 0.5)),
         "clip_id": c.get("id")}
        for i, c in enumerate(resolved)
        if c.get("transition") in CUSTOM_TRANSITIONS
        and i < len(clip_starts)]
    info = {"width": width, "height": height, "fps": fps, "clips": resolved,
            "total_duration": total, "clip_starts": clip_starts, "font": font,
            "captions": merged_captions, "overlays": resolved_overlays,
            "diagrams": resolved_diagrams,
            "motion": resolved_motion,
            "audio": resolved_audio,
            "chapters": chapters, "ducking": ducking,
            "auto_duck": auto_duck,
            "auto_subcuts": _auto_subcut_ids,
            "video_bitrate": video_bitrate, "loudness_target": loudness_target,
            "master_loudnorm": master_loudnorm, "loudnorm_i": loudnorm_i,
            "color_pipeline": color_pipeline,
            "beat_cuts": _beat_cuts, "typography": _typography,
            "custom_transitions": _custom_transitions,
            "adjustments": _adjustments, "data_files": _data_files}
    # Layer/text collision is warnings-only: it never adds errors.
    try:
        info["warnings"], info["overlap_moves"] = \
            check_layer_text_overlap(project, info)
    except Exception:
        info["warnings"] = []
        info["overlap_moves"] = []
    # v1.44.0: data-file warnings (track / reframe partial
    # coverage) collected during validation.
    info["warnings"].extend(_extra_warnings)
    # Vertical zones (v1.42.0, Fix 3): strict bands; callout
    # moves are recorded in info["zone_moves"] and applied by
    # the renderer after overlap_moves (zone wins on conflict).
    try:
        _zw, info["zone_moves"] = check_vertical_zones(
            project, info, info.get("overlap_moves"))
        info["warnings"].extend(_zw)
    except Exception:
        info["zone_moves"] = []
    # SFX discipline (v1.42.0, Fix 5): the cooldown /
    # attenuation / pitch plan for auto-SFX. Computed here so
    # the validate tool reports it; the renderer consumes it.
    try:
        info["sfx_plan"] = compute_sfx_plan(project, info)
    except Exception:
        info["sfx_plan"] = {"kept": [], "dropped": [],
                            "attenuation_db": 16.0}
    # Static-shot warnings (v1.37.0, warnings only, never
    # blocking): a still-image clip longer than 4.5s with no
    # motion of any kind — no explicit or default Ken Burns, no
    # punch_in, and no animated overlay (keyframes) overlapping
    # its window — will read as a frozen frame. Each warning names
    # the clip, so the agent can add motion or accept it.
    try:
        # Punch-over-Ken-Burns warnings (v1.37.2, warnings only):
        # a clip that sets BOTH an explicit ken_burns and punch_in
        # renders punch-only motion — Ken Burns is skipped, not
        # stacked — so the project degrades visibly, not silently.
        # Default motion + punch does NOT warn: punch winning over
        # a default is the designed rule, not a user conflict.
        _raw_clips = project.get("clips") or []
        for _ci, _clip in enumerate(info["clips"]):
            _raw = _raw_clips[_ci] if _ci < len(_raw_clips) else {}
            if _raw.get("ken_burns") not in (None, "none") \
                    and _clip.get("punch_at") is not None:
                info["warnings"].append({
                    "code": "punch_in_overrides_ken_burns",
                    "message": (f"image clip '{_clip['id']}' sets both ken_burns "
                                f"('{_raw.get('ken_burns')}') and punch_in; punch_in wins "
                                f"and ken_burns is skipped — remove ken_burns to silence "
                                f"this warning"),
                    "clip_id": _clip["id"]})
            # v1.40.0: same rule for camera — an explicit camera
            # path wins over ken_burns, which is then skipped.
            if _raw.get("ken_burns") not in (None, "none") \
                    and _clip.get("camera"):
                info["warnings"].append({
                    "code": "camera_overrides_ken_burns",
                    "message": (f"image clip '{_clip['id']}' sets both ken_burns "
                                f"('{_raw.get('ken_burns')}') and camera; camera wins "
                                f"and ken_burns is skipped — remove ken_burns to silence "
                                f"this warning"),
                    "clip_id": _clip["id"]})
        _starts = info.get("clip_starts") or []
        for _ci, _clip in enumerate(info["clips"]):
            if _clip.get("kind") != "image":
                continue
            if float(_clip["duration"]) <= 4.5:
                continue
            if _clip.get("punch_at") is not None:
                continue
            if _clip.get("camera"):
                continue
            if effective_ken_burns(_clip, _ci, project) != "none":
                continue
            _cs = _starts[_ci] if _ci < len(_starts) else 0.0
            _ce = _cs + float(_clip["duration"])
            _animated = False
            for _ov in (info.get("overlays") or []):
                if not _ov.get("keyframes_resolved"):
                    continue
                _os, _oe = float(_ov["start"]), float(_ov["start"]) + float(_ov["duration"])
                if _os < _ce and _oe > _cs:
                    _animated = True
                    break
            if not _animated:
                # A stroke diagram drawing over the still is
                # motion too (v1.38.0): its draw window animates
                # even though the underlying clip is a still.
                for _dg in (info.get("diagrams") or []):
                    _ds, _de = float(_dg["start"]), float(_dg["end"])
                    if _ds < _ce and _de > _cs:
                        _animated = True
                        break
            if not _animated:
                # A motion-graphics element over the still is
                # motion too (v1.43.0): it animates across its
                # window even though the underlying clip is a still.
                for _me in (info.get("motion") or []):
                    _ms, _me2 = float(_me["start"]), float(_me["end"])
                    if _ms < _ce and _me2 > _cs:
                        _animated = True
                        break
            if _animated:
                continue
            info["warnings"].append({
                "code": "static_shot_warning",
                "message": (f"image clip '{_clip['id']}' is {float(_clip['duration']):.1f}s long with no motion "
                            f"(no ken_burns, punch_in, default_image_motion or animated overlay); "
                            f"it will look frozen — add ken_burns, punch_in or default_image_motion"),
                "clip_id": _clip["id"], "start": round(_cs, 3),
                "end": round(_ce, 3)})
        # Active-caption chunk width (v1.37.0, warning only): a
        # 2–4 word chunk wider than the frame would clip, since
        # active chunks are single-line by design. v1.40.0: the
        # same check covers kinetic texts (their chunks are
        # single-line too); the size default is 48 for texts.
        _kinetic_items = [(c, "caption")
                          for c in (info.get("captions") or [])]
        _kinetic_items += [(t, "text") for t in (project.get("texts") or [])
                           if isinstance(t, dict) and t.get("word_timings")]
        for _cap, _kind in _kinetic_items:
            if not isinstance(_cap, dict) or not _cap.get("word_timings"):
                continue
            _size = float(_cap.get("size", 36 if _kind == "caption" else 48))
            _wt = _cap["word_timings"]
            _chunks = chunk_word_timings(_wt)
            for _ch in _chunks:
                _text = " ".join(w["word"] for w in _ch)
                if len(_text) * _size * 0.6 > width:
                    info["warnings"].append({
                        "code": "active_chunk_overflow_warning",
                        "message": (f"active {_kind} chunk '{_text}' estimated width "
                                    f"{len(_text) * _size * 0.6:.0f}px exceeds output width {width}px "
                                    f"and may clip (shorten the chunk or reduce the size)"),
                        "text": _text})
        # Parallax depth with no motion (v1.40.0, warning only):
        # depth only moves an overlay when the clip under its
        # start has a camera path or a Ken Burns pan; otherwise
        # it is a no-op, and the agent should know rather than
        # assume the overlay will drift.
        _pstarts = info.get("clip_starts") or []
        for _ov in (info.get("overlays") or []):
            _dep = float(_ov.get("depth", 0) or 0)
            if _dep <= 0:
                continue
            _ci = 0
            for _j, _cs in enumerate(_pstarts):
                if _cs <= float(_ov["start"]):
                    _ci = _j
            if _ci >= len(info["clips"]):
                continue
            _cclip = info["clips"][_ci]
            _has_motion = _cclip.get("camera") is not None or \
                effective_ken_burns(_cclip, _ci, project) in ("pan_left", "pan_right")
            if not _has_motion:
                info["warnings"].append({
                    "code": "parallax_depth_no_motion",
                    "message": (f"overlay '{_ov['id']}' sets depth {_dep} but the clip under its start "
                                f"('{_cclip['id']}') has no camera path or Ken Burns pan, so depth has no effect — "
                                f"add a camera path or a pan to that clip, or remove depth"),
                    "clip_id": _ov["id"]})
        # Motion-continuity gap (v1.40.0, warning only): a
        # same-scene clip with no motion of its own still starts
        # at 1.0x while the previous same-scene clip ended zoomed,
        # so the boundary steps. Add camera or ken_burns to the
        # clip, or accept the step. (A punch clip is an
        # intentional jump, so it never warns here.)
        _cprev_end = 1.0
        _cprev_scene = None
        for _ci, _clip in enumerate(info["clips"]):
            _scene = _clip.get("scene_id")
            if _scene is not None and _scene == _cprev_scene and abs(_cprev_end - 1.0) > 1e-9:
                _has_motion = _clip.get("camera") is not None or \
                    _clip.get("punch_at") is not None or \
                    effective_ken_burns(_clip, _ci, project) != "none"
                if not _has_motion:
                    info["warnings"].append({
                        "code": "scene_continuity_static",
                        "message": (f"clip '{_clip['id']}' shares scene '{_scene}' with the previous clip, which ended at "
                                    f"{_cprev_end:.2f}x, but has no camera, ken_burns or punch_in of its own, so it starts at "
                                    f"1.0x and the boundary steps — add motion to this clip, or accept the step"),
                        "clip_id": _clip["id"]})
            _cprev_end = float(_clip.get("_motion_end", 1.0))
            _cprev_scene = _scene
    except Exception:
        pass
    return errors, info
