"""Project schema, probing and validation for AgentCut v1.

Validation runs before any render and returns structured errors, never
a bare exception, so an agent can fix the project and retry.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

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
}

TEXT_COLORS = {"white", "black", "yellow", "red", "green", "blue", "cyan",
               "magenta", "orange", "purple"}
TEXT_POSITIONS = {"top_left", "top_center", "top_right", "center",
                  "bottom_left", "bottom_center", "bottom_right"}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/DejaVuSans.ttf",
]


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
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


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
    """Return ffprobe info, or None when the file cannot be probed."""
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
    needs_font = bool(project.get("texts") or project.get("captions"))
    font = find_font() if needs_font else None
    if needs_font and font is None:
        errors.append(err("FONT_NOT_FOUND", "no usable DejaVu font found for text overlays", field="texts"))
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
        if kind == "video":
            info = probe_media(src)
            if info is None or info["duration"] is None:
                errors.append(err("CLIP_OUT_OF_RANGE", "could not probe video duration (file may be corrupt or not a video)", field="clips.src", clip_id=cid))
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
            if curve is not None and clip.get("fit") == "blur":
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
        if fit not in ("contain", "cover", "blur"):
            errors.append(err("INVALID_SCHEMA", "fit must be 'contain', 'cover' or 'blur' (blurred background)", field="clips.fit", clip_id=cid))
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
        if clip.get("ken_burns") not in (None, "none") and clip.get("fit") == "blur":
            errors.append(err("INVALID_SCHEMA", "ken_burns cannot combine with fit blur", field="clips.fit", clip_id=cid,
                              fix="use ken_burns or blurred-background fit, not both, on one clip"))
        if clip.get("fit") == "blur" and "chroma_key" in clip:
            errors.append(err("INVALID_SCHEMA", "fit blur cannot combine with chroma_key", field="clips.chroma_key", clip_id=cid,
                              fix="use blurred-background fit or chroma key, not both, on one clip"))
        if resolved:
            resolved[-1].update(_validate_chroma_mask(clip, errors, cid, "clips"))
        _validate_effects(clip, errors, cid, "clips")
        if "stabilize" in clip and not isinstance(clip["stabilize"], bool):
            errors.append(err("INVALID_SCHEMA", "clip stabilize must be true or false", field="clips.stabilize", clip_id=cid,
                              fix="set stabilize to true for two-pass vidstab stabilisation, or remove it"))
        if clip.get("stabilize") and (clip.get("freeze_at") is not None or clip.get("loop_count") is not None or clip.get("reverse") or clip.get("speed_curve") is not None or float(clip.get("speed", 1.0)) != 1.0 or clip.get("fit") == "blur"):
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
        position = ov.get("position", "bottom_right")
        if position not in TEXT_POSITIONS:
            errors.append(err("INVALID_SCHEMA", "overlay position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field="overlays.position", clip_id=oid,
                              fix="use a preset position, or set x and y pixel offsets instead"))
            continue
        rov["position"] = position
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
                    raw_kfs.append((kt, vals))
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
                    cur = {"x": base_x, "y": base_y, "scale": float(rov["size"]), "opacity": float(rov["opacity"])}
                    for kt, vals in raw_kfs:
                        cur = {**cur, **vals}
                        filled.append({"time": kt, "x": cur["x"], "y": cur["y"],
                                       "scale": cur["scale"], "opacity": cur["opacity"]})
                    scales = {f["scale"] for f in filled}
                    if kind == "video" and len(scales) > 1 and rov["duration"] > rov.get("source_duration", rov["duration"]) + 0.01:
                        errors.append(err("INVALID_SCHEMA", "scale keyframes on a looping video overlay are not supported", field="overlays.keyframes.scale", clip_id=oid,
                                          fix="shorten the overlay duration to the trimmed source duration, or remove scale keyframes"))
                    else:
                        rov["keyframes_resolved"] = filled
                        if "mask" in rov and len({f["scale"] for f in filled}) > 1:
                            errors.append(err("INVALID_SCHEMA", "mask cannot combine with scale keyframes (mask coordinates are in fixed overlay-frame pixels)", field="overlays.mask_shape", clip_id=oid,
                                              fix="remove scale keyframes, or remove the mask, on this overlay"))
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
            color = item.get("color", "white")
            if not (isinstance(color, str) and (color in TEXT_COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", color))):
                errors.append(err("INVALID_SCHEMA", "text color must be a #RRGGBB hex value or one of: " + ", ".join(sorted(TEXT_COLORS)), field=label + ".color"))
            if "box" in item and not isinstance(item["box"], bool):
                errors.append(err("INVALID_SCHEMA", "text box must be true or false", field=label + ".box"))
            position = item.get("position")
            if position is not None and position not in TEXT_POSITIONS:
                errors.append(err("INVALID_SCHEMA", "text position must be one of: " + ", ".join(sorted(TEXT_POSITIONS)), field=label + ".position"))
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
    for track in project.get("audio") or []:
        if not isinstance(track, dict) or not isinstance(track.get("src"), str):
            errors.append(err("INVALID_SCHEMA", "each audio track needs a src path", field="audio"))
            continue
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
    info = {"width": width, "height": height, "fps": fps, "clips": resolved,
            "total_duration": total, "clip_starts": clip_starts, "font": font,
            "captions": merged_captions, "overlays": resolved_overlays}
    return errors, info
