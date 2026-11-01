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
EASING_VALUES = {"linear", "ease_in", "ease_out", "ease_in_out"}

# Colour-grade presets (pro pack). These are honest, documented
# filter combinations — eq/colorbalance/saturation settings, NOT
# film-stock emulation. Each value is the FFmpeg filter chain
# appended after a clip's other colour filters.
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
# as missing glyphs. Noto Naskh Arabic is the preferred Arabic font
# when it exists; Noto Sans Arabic is the fallback.
LATIN_FONT_FILES = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]
ARABIC_FONT_FILES = ["NotoNaskhArabic-Regular.ttf",
                     "NotoNaskhArabic-Medium.ttf",
                     "NotoSansArabic-Regular.ttf"]


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
        return path
    if os.path.isfile(path):
        return path
    candidate = os.path.join(BASE_DIR, path)
    if os.path.isfile(candidate):
        return candidate
    return path


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


def wrap_caption_lines(text, size, max_width_px):
    """Word-wrap a caption's LOGICAL text into lines fitting
    max_width_px (v1.36.0, the headline caption fix).

    Delegates to srt.wrap_lines — the single source of truth for
    wrapping (refactoring: never duplicate the algorithm). See
    srt.wrap_lines for the greedy word-packing rules (words never
    split, Arabic words stay whole and joined, BiDi per line at
    render time). Returns at least one line.
    """
    from .srt import wrap_lines
    return wrap_lines(text, size, max_width_px)


def _text_box(item, is_caption, width, height, total,
              line_spacing=1.35, max_width_frac=0.9):
    """Approximate on-screen box (x, y, w, h) for a text/caption item.

    APPROXIMATION, documented honestly: the true drawtext box
    depends on the font's glyph advances, which validation does not
    measure. Width is estimated as len(text) x size x 0.6 and height
    as size x 1.4; positions mirror render.py (numeric x/y, the
    POS_MAP presets, centred texts, captions at height-120 by
    default). Captions are wrapped (v1.36.0): the box spans the
    wrapped block — width is the widest line, height covers all
    lines at line_spacing — so collision checks see the real
    wrapped block, not the old single-line box. The check is a
    warning, never an error, precisely because these boxes are
    estimates.
    """
    text = str(item.get("text", ""))
    size = float(item.get("size", 36 if is_caption else 48))
    cap_lines = wrap_caption_lines(text, size, width * max_width_frac) \
        if is_caption else [text]
    if is_caption:
        est_w = max(10.0, max(len(ln) * size * 0.6 for ln in cap_lines))
        if len(cap_lines) > 1:
            est_h = (len(cap_lines) - 1) * size * line_spacing + size * 1.4
        else:
            est_h = size * 1.4
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
        # above it — exactly like render.py.
        x, y = (width - est_w) / 2, float(
            height - 120 - (len(cap_lines) - 1) * size * line_spacing)
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
    texts = []
    for item in (project.get("texts") or []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            texts.append((item["text"], float(item.get("start", 0)),
                          float(item.get("end", total)),
                          _text_box(item, False, width, height, total)))
    cap_spacing = project.get("caption_line_spacing", 1.35)
    cap_maxw = project.get("caption_max_width", 0.9)
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
                                    max_width_frac=cap_maxw)))
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
    return warnings


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
    if "seed" in st:
        try:
            out["seed"] = int(st["seed"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "stroke seed must be an integer", field="diagrams.strokes.seed", clip_id=did,
                              fix="set seed to a whole number, or remove it (defaults to the stroke index)"))
            return None

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


def _validate_diagrams(project, total, overlay_ids, errors):
    """Validate the project-level `diagrams` list (v1.38.0).

    Returns resolved diagram dicts for info["diagrams"]. A
    diagram is custom strokes (with a canvas, default 900x560)
    or one built-in template with parameters — never both, and
    labels are never part of a diagram (templates take no text;
    labels are AgentCut texts). Placement mirrors overlays:
    size (fraction of output width), position preset, or x/y
    pixel offsets winning per axis."""
    from .diagrams import TEMPLATES, build_template
    resolved = []
    raw = project.get("diagrams")
    if raw is None:
        return resolved
    if not isinstance(raw, list):
        errors.append(err("INVALID_SCHEMA", "diagrams must be a list", field="diagrams",
                          fix="give diagrams as a list of diagram objects, or remove diagrams"))
        return resolved
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
               "size": size, "position": position}
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
            if tname not in TEMPLATES:
                errors.append(err("INVALID_SCHEMA", f"unknown diagram template '{tname}'; valid templates are: " + ", ".join(sorted(TEMPLATES)), field="diagrams.template", clip_id=did,
                                  fix="set template to one of: " + ", ".join(sorted(TEMPLATES))))
                continue
            params = dg.get("params") or {}
            if not isinstance(params, dict):
                errors.append(err("INVALID_SCHEMA", "diagram params must be an object", field="diagrams.params", clip_id=did,
                                  fix="give params as an object, e.g. {'values': [10, 12, 14]}"))
                continue
            allowed = {"chart": {"values"}, "basket": {"coins"},
                       "balance": {"left", "right"}, "causes": {"rows"},
                       "primitives": {"strokes", "canvas"}}[tname]
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
                        if isinstance(_st2, dict) and "seed" not in _st2:
                            _st2["seed"] = _si
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
                if isinstance(st2, dict) and "seed" not in st2:
                    st2["seed"] = si
                rs = _validate_stroke(st2, cw, ch, errors, did)
                if rs is None:
                    rstrokes = None
                    break
                rstrokes.append(rs)
            if rstrokes is None:
                continue
            rdg.update({"source": "custom", "template": None,
                        "canvas_w": cw, "canvas_h": ch, "strokes": rstrokes})
        resolved.append(rdg)
    return resolved


def validate_project(project):
    """Validate a project dict.

    Returns (errors, info). info carries output dimensions, fps, the
    resolved clip durations and the total duration when computable.
    """
    errors = []
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
            resolved.append({**clip, "src": src, "duration": duration, "trim_in": trim_in, "trim_out": trim_out, "probe": info})
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
            for _vf in ("reverse", "freeze_at", "loop_count", "speed_curve", "denoise", "stabilize"):
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
            total += duration
        fit = clip.get("fit", "contain")
        if fit not in ("contain", "cover", "blur", "blur_pad"):
            errors.append(err("INVALID_SCHEMA", "fit must be 'contain', 'cover', 'blur' or 'blur_pad' (blurred background)", field="clips.fit", clip_id=cid))
        kb = clip.get("ken_burns", "none")
        if kb not in ("none", "zoom_in", "zoom_out", "pan_left", "pan_right"):
            errors.append(err("INVALID_SCHEMA", "ken_burns must be 'none', 'zoom_in', 'zoom_out', 'pan_left' or 'pan_right'", field="clips.ken_burns", clip_id=cid))
        elif kb != "none" and kind != "image":
            errors.append(err("INVALID_SCHEMA", "ken_burns is only for image clips", field="clips.ken_burns", clip_id=cid))
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
        allowed = ["none", "fade"] + list(XFADE_TRANSITIONS)
        if transition not in allowed:
            errors.append(err("INVALID_SCHEMA", f"transition must be one of {', '.join(allowed)}", field="clips.transition", clip_id=cid))
        elif transition in XFADE_TRANSITIONS and len(resolved) == 1:
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
        if transition in XFADE_TRANSITIONS and len(resolved) >= 2:
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
        # start offset in the final timeline (after xfade overlaps)
        if len(resolved) >= 2 and transition in XFADE_TRANSITIONS:
            clip_starts.append(clip_starts[-1] + prev_duration - td)
        elif resolved:
            clip_starts.append((clip_starts[-1] + prev_duration) if clip_starts else 0.0)
        prev_duration = duration
    overlap_total = sum(float(c.get("transition_duration", 0.5)) for c in resolved[1:] if c.get("transition") in XFADE_TRANSITIONS)
    total = total - overlap_total
    if total > MAX_DURATION:
        errors.append(err("DURATION_TOO_LONG", f"total duration {total:.2f}s exceeds {MAX_DURATION}s", field="clips"))
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
                                          fix="set easing to linear, ease_in, ease_out or ease_in_out, or remove it"))
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
            if anim is not None and anim not in ("slide_in", "slide_out", "slide_in_out", "pop"):
                errors.append(err("INVALID_SCHEMA", "text animation must be slide_in, slide_out, slide_in_out or pop", field=label + ".animation",
                                  fix="set animation to slide_in, slide_out, slide_in_out or pop, or remove it"))
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
                elif anim in ("slide_in", "slide_out", "pop") and ad >= (end - start):
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
    # Defaults 0.9 / 1.35 keep old single-line renders identical.
    if "caption_max_width" in project:
        try:
            _cmw = float(project["caption_max_width"])
        except (TypeError, ValueError):
            errors.append(err("INVALID_SCHEMA", "caption_max_width must be a number", field="caption_max_width",
                              fix="set caption_max_width to a fraction of the frame width, e.g. 0.9"))
            _cmw = None
        if _cmw is not None and not 0.5 <= _cmw <= 1.0:
            errors.append(err("INVALID_SCHEMA", "caption_max_width must be 0.5..1.0", field="caption_max_width",
                              fix="set caption_max_width between 0.5 and 1.0 (0.9 = 90% of the frame width)"))
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
                                  "Arabic text needs Noto Naskh Arabic (or Noto Sans Arabic)",
                                  field="texts",
                                  fix="use the bundled fonts/ directory, or set AGENTCUT_FONT_DIR to a directory containing NotoNaskhArabic-Regular.ttf"))
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
    # Delta: explicit auto-duck switch. Precedence, exactly:
    # explicit ducking object > auto sidechain (auto_duck true,
    # voice + music present, no explicit ducking) > plain mix.
    # Default True is the pre-delta behaviour, so existing
    # projects render identically; False disables only the auto
    # path (an explicit ducking object still wins over False).
    auto_duck = True
    raw_auto = project.get("auto_duck")
    if raw_auto is not None:
        if not isinstance(raw_auto, bool):
            errors.append(err("INVALID_SCHEMA", "auto_duck must be true or false", field="auto_duck",
                              fix="set auto_duck to false to disable automatic sidechain ducking, or remove it (default true)"))
        else:
            auto_duck = raw_auto
    # Animated stroke diagrams (v1.38.0): validated after the
    # overlays so diagram ids can be checked against overlay ids.
    # Diagrams are resolved data (strokes/templates expanded),
    # never file references, so render packs carry them verbatim
    # in project.json and regenerate the animation at render.
    resolved_diagrams = _validate_diagrams(
        project, total, {ov["id"] for ov in resolved_overlays}, errors)
    info = {"width": width, "height": height, "fps": fps, "clips": resolved,
            "total_duration": total, "clip_starts": clip_starts, "font": font,
            "captions": merged_captions, "overlays": resolved_overlays,
            "diagrams": resolved_diagrams,
            "audio": resolved_audio,
            "chapters": chapters, "ducking": ducking,
            "auto_duck": auto_duck,
            "video_bitrate": video_bitrate, "loudness_target": loudness_target,
            "master_loudnorm": master_loudnorm}
    # Layer/text collision is warnings-only: it never adds errors.
    try:
        info["warnings"] = check_layer_text_overlap(project, info)
    except Exception:
        info["warnings"] = []
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
