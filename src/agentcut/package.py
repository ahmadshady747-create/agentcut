"""Portable render packs (v1.35.0, on-device rendering).

A render pack is a self-contained directory that renders with NO
network, NO MCP server and NO repo checkout: it holds a rewritten
project.json (every media reference rewritten to a relative
media/... path), every media file the project references, a copy
of the AgentCut source tree, the bundled fonts, a render.sh that
runs the local render to output.mp4, and a README.

Workflow: the agent builds the project, runs
`./run.sh package project.json packdir`, and delivers the pack
(archive or link). The user unpacks it on their own device
(Termux on Android: python + ffmpeg installed), runs
`./render.sh` there, and reports the result back. The agent
CANNOT run the on-device render itself — there is no shell
channel to the user's device — so the pack, not a remote call,
is the delivery.

Only stdlib + ffmpeg/ffprobe are used, like the rest of AgentCut.
"""

import hashlib
import json
import os
import shutil
import subprocess

from .validate import BASE_DIR, resolve_path, validate_project

PACKAGE_SRC_DIR = "src"
PACKAGE_MEDIA_DIR = "media"
PACKAGE_FONTS_DIR = "fonts"

# Safety margin (seconds) kept on each side of a trimmed clip's used
# segment (trim_clips=True): stream-copy trimming cuts at the
# nearest keyframe before the start, so a margin guarantees the
# used frames survive even when the exact trim_in is not a
# keyframe, and it keeps a little handle for re-editing.
TRIM_MARGIN = 1.0

RENDER_SH = """#!/bin/sh
# AgentCut render pack: renders project.json to output.mp4 with
# nothing but this directory, python3 and ffmpeg/ffprobe.
# No network, no MCP server, no repo checkout needed.
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"
command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found (Termux: pkg install python)" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "error: ffmpeg not found (Termux: pkg install ffmpeg)" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "error: ffprobe not found (Termux: pkg install ffmpeg)" >&2; exit 1; }
if [ -d "$DIR/fonts" ]; then
  AGENTCUT_FONT_DIR="$DIR/fonts"
  export AGENTCUT_FONT_DIR
fi
PYTHONPATH="$DIR/src" python3 -m agentcut render project.json output.mp4
echo "done: $DIR/output.mp4"
"""

RENDER_BAT = """@echo off
set "DIR=%~dp0"
cd /d "%DIR%"
if exist "%DIR%fonts" set "AGENTCUT_FONT_DIR=%DIR%fonts"
set "PYTHONPATH=%DIR%src;%PYTHONPATH%"
python -m agentcut render project.json output.mp4
echo done: %DIR%output.mp4
"""

README_TEXT = """# AgentCut render pack — render on your own device

This directory renders itself. You need only python3 and ffmpeg
(+ ffprobe, which ships with ffmpeg) installed on the device.
No network, no server, no repo checkout.

## Termux (Android)

```
pkg install python ffmpeg
```

Then unpack this directory somewhere writable inside Termux home
(NOT on an SD card — Android restricts writes there), enter it,
and run:

```
./render.sh
```

The finished video is `output.mp4` in this directory.

## Notes

- Keep the phone charging and Termux in the foreground: renders
  are CPU-heavy, the phone will warm up and may throttle, and
  Android can kill background work. Start with short projects.
- `render.sh` uses the bundled `fonts/` directory automatically,
  so Arabic text renders even with no system fonts installed.
- If rendering fails, the error message names the problem
  (missing file, missing font, bad value). Report the exact
  message back to the agent that built the pack.
"""


def _unique_name(used, basename):
    """A media/ name that never collides: plain basename first,
    then name_2.ext, name_3.ext, ... in collection order."""
    if basename not in used:
        used.add(basename)
        return basename
    stem, ext = os.path.splitext(basename)
    n = 2
    while True:
        candidate = f"{stem}_{n}{ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _file_sha256(path):
    """SHA-256 hex of a file's bytes (chunked read), or None when
    the file cannot be read. A None hash never dedups with
    anything (the file is treated as unique)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _collect_files(project, info):
    """{abspath: canonical_abspath} for every file a render reads.

    Sources: validated clips (resolved srcs + LUTs), validated
    overlays (resolved srcs), validated audio tracks (resolved
    srcs), file-path SFX on clips/texts/captions/overlays, and
    the captions SRT file. Anything else in a project
    (texts, chapters, ducking windows, presets) is data, not a
    file reference, and is carried by project.json itself.

    Deduplication (refactoring): files with identical SHA-256
    content share one canonical path (the first collected with
    that hash), so the same content is never copied twice under
    different paths — package_project copies the canonical file
    once and rewrites every reference to it. An unreadable file
    maps to itself (never deduped); a missing file is still
    listed here and reported by package_project's isfile check.
    """
    from .validate import SFX_NAMES
    collected = []
    for clip in info.get("clips") or []:
        collected.append(clip["src"])
        if clip.get("lut_resolved"):
            collected.append(clip["lut_resolved"])
    for ov in info.get("overlays") or []:
        collected.append(ov["src"])
    for track in info.get("audio") or []:
        collected.append(track["src"])
    for _holder in (list(project.get("clips") or [])
                    + list(project.get("texts") or [])
                    + list(info.get("captions") or [])
                    + list(project.get("overlays") or [])):
        _sfx = _holder.get("sfx") if isinstance(_holder, dict) else None
        if isinstance(_sfx, str) and _sfx not in SFX_NAMES:
            _resolved = resolve_path(_sfx)
            if os.path.isfile(_resolved):
                collected.append(_resolved)
    captions_file = project.get("captions_file")
    if captions_file:
        collected.append(resolve_path(captions_file))
    by_hash = {}
    out = {}
    for abspath in collected:
        digest = _file_sha256(abspath) if os.path.isfile(abspath) else None
        if digest is None:
            out[abspath] = abspath
        elif digest in by_hash:
            out[abspath] = by_hash[digest]
        else:
            by_hash[digest] = abspath
            out[abspath] = abspath
    return out


def _trim_clip_window(clip, file_duration):
    """(ss, to) trim window for a video clip, or None to skip trim.

    The window is the clip's used segment plus TRIM_MARGIN on
    each side, clamped to the file. Trimming is skipped (copy
    whole) when: the clip is not a plain trimmed video (image
    clips have no trim range); the window already covers the
    whole file (nothing to save); or the clip references file
    times outside the window (freeze_at, speed_curve segment
    bounds) — trimming would cut content the render needs.
    Reverse/loop clips are trimmable: they operate on the trim
    range, which is re-based below, so their output is unchanged.
    """
    if clip.get("kind") != "video":
        return None
    try:
        tin = float(clip.get("trim_in", 0))
        tout = float(clip.get("trim_out", file_duration))
    except (TypeError, ValueError):
        return None
    if file_duration is None or file_duration <= 0:
        return None
    ss = max(0.0, tin - TRIM_MARGIN)
    to = min(float(file_duration), tout + TRIM_MARGIN)
    if ss <= 0.0 and to >= float(file_duration):
        return None
    if clip.get("freeze_at") is not None:
        try:
            if not (ss <= float(clip["freeze_at"]) <= to):
                return None
        except (TypeError, ValueError):
            return None
    for seg in clip.get("speed_curve") or []:
        try:
            if not (ss <= float(seg["start"]) and float(seg["end"]) <= to):
                return None
        except (TypeError, ValueError, KeyError):
            return None
    return (ss, to)


def _trim_file(src, dst, ss, to):
    """Stream-copy trim src[ss:to] to dst via ffmpeg -ss/-to.

    -ss seeks (keyframe-accurate, fast) and -to stops at output
    time to-ss, so the trimmed file covers exactly the input
    window [ss, to]. Stream copy: no re-encode, no quality loss.
    Raises ValueError when ffmpeg fails or the result is empty.
    """
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", repr(ss),
         "-i", src, "-to", repr(to - ss), "-map", "0", "-c", "copy", dst],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise ValueError(f"trim failed for {src}: {proc.stderr[-500:]}")


def package_project(project, outdir, trim_clips=False):
    """Build a render pack. Returns a summary dict.

    Raises ValueError with a plain message when the project is
    invalid or a referenced file is missing — a pack must never
    be built from a project that cannot render.

    trim_clips (default False, preserves current behaviour
    exactly): when True, each trimmable video clip is copied as
    only its used segment plus TRIM_MARGIN on each side (ffmpeg
    stream copy, no re-encode), and the packed project.json
    re-bases that clip's trim_in/trim_out (and freeze_at /
    speed_curve bounds, when present) by the trim start, so the
    packed render produces the same frames. Clips that cannot be
    trimmed safely (whole file used, or referenced times outside
    the window) are copied whole. Trimmed clips always get their
    own media file, even when another clip shares the same
    source: content dedup cannot apply across different trim
    windows. Audio tracks and images are never trimmed (tracks
    have no trim range in the schema; images are single frames).
    """
    errors, info = validate_project(project)
    if errors:
        raise ValueError("project is invalid: " + json.dumps(errors, ensure_ascii=False))
    collected = _collect_files(project, info)
    media_map = {}  # abspath -> media/ relpath (deduped by canonical)
    used = set()
    relpath_for_canonical = {}
    for abspath, canonical in collected.items():
        if not os.path.isfile(abspath):
            raise ValueError(f"referenced file not found: {abspath}")
        if canonical not in relpath_for_canonical:
            relpath_for_canonical[canonical] = (
                f"{PACKAGE_MEDIA_DIR}/{_unique_name(used, os.path.basename(canonical))}")
        media_map[abspath] = relpath_for_canonical[canonical]

    packed = json.loads(json.dumps(project, ensure_ascii=False))

    # Trim overrides (trim_clips=True only): clip id -> (relpath, ss).
    # A trimmed clip's packed src points at its own trimmed copy,
    # and its trim times are re-based by ss below.
    trim_overrides = {}
    info_by_id = {c.get("id"): c for c in info.get("clips") or []}
    if trim_clips:
        for clip in packed.get("clips") or []:
            if not isinstance(clip, dict) or not clip.get("src"):
                continue
            resolved = resolve_path(clip["src"])
            iclip = info_by_id.get(clip.get("id")) or {}
            probe = iclip.get("probe") or {}
            window = _trim_clip_window(iclip, probe.get("duration"))
            if window is None:
                continue
            ss, _to = window
            relpath = (
                f"{PACKAGE_MEDIA_DIR}/{_unique_name(used, os.path.basename(resolved))}")
            # Store re-based times now (validated times always
            # exist for video clips); the rewrite below assigns
            # them, so a missing key can never leave a packed
            # clip with un-rebased times.
            try:
                tin_r = float(iclip.get("trim_in", 0)) - ss
                tout_r = float(iclip.get("trim_out")) - ss
            except (TypeError, ValueError):
                continue
            trim_overrides[clip.get("id")] = (relpath, ss, tin_r, tout_r)

    def _rewrite(path):
        resolved = resolve_path(path)
        return media_map[resolved]

    for clip in packed.get("clips") or []:
        if isinstance(clip, dict) and clip.get("src"):
            if clip.get("id") in trim_overrides:
                relpath, ss, tin_r, tout_r = trim_overrides[clip["id"]]
                clip["src"] = relpath
                # Re-base every file-time field by the trim start
                # (precomputed validated times, so the packed clip
                # renders the same frames from its trimmed copy).
                clip["trim_in"] = tin_r
                clip["trim_out"] = tout_r
                if clip.get("freeze_at") is not None:
                    clip["freeze_at"] = float(clip["freeze_at"]) - ss
                for seg in clip.get("speed_curve") or []:
                    if isinstance(seg, dict):
                        if "start" in seg:
                            seg["start"] = float(seg["start"]) - ss
                        if "end" in seg:
                            seg["end"] = float(seg["end"]) - ss
            else:
                clip["src"] = _rewrite(clip["src"])
        if isinstance(clip, dict) and clip.get("lut"):
            clip["lut"] = _rewrite(clip["lut"])
        if isinstance(clip, dict) and isinstance(clip.get("sfx"), str) and resolve_path(clip["sfx"]) in media_map:
            clip["sfx"] = _rewrite(clip["sfx"])
    for ov in packed.get("overlays") or []:
        if isinstance(ov, dict) and ov.get("src"):
            ov["src"] = _rewrite(ov["src"])
        if isinstance(ov, dict) and isinstance(ov.get("sfx"), str) and resolve_path(ov["sfx"]) in media_map:
            ov["sfx"] = _rewrite(ov["sfx"])
    for track in packed.get("audio") or []:
        if isinstance(track, dict) and track.get("src"):
            track["src"] = _rewrite(track["src"])
    for _item in (packed.get("texts") or []) + (packed.get("captions") or []):
        if isinstance(_item, dict) and isinstance(_item.get("sfx"), str) and resolve_path(_item["sfx"]) in media_map:
            _item["sfx"] = _rewrite(_item["sfx"])
    if packed.get("captions_file"):
        packed["captions_file"] = _rewrite(packed["captions_file"])

    os.makedirs(outdir, exist_ok=True)
    media_dir = os.path.join(outdir, PACKAGE_MEDIA_DIR)
    os.makedirs(media_dir, exist_ok=True)
    # Referenced relpaths: shared copies for untrimmed references
    # plus one trimmed copy per trimmed clip. A shared file used
    # ONLY by trimmed clips (no untrimmed reference left) is not
    # copied untrimmed — its trimmed copies carry the content.
    referenced_shared = set()
    for clip in packed.get("clips") or []:
        if isinstance(clip, dict) and clip.get("src") and clip.get("id") not in trim_overrides:
            referenced_shared.add(clip["src"])
    for ov in packed.get("overlays") or []:
        if isinstance(ov, dict) and ov.get("src"):
            referenced_shared.add(ov["src"])
    for track in packed.get("audio") or []:
        if isinstance(track, dict) and track.get("src"):
            referenced_shared.add(track["src"])
    for _item in (packed.get("texts") or []) + (packed.get("captions") or []):
        if isinstance(_item, dict) and isinstance(_item.get("sfx"), str) and resolve_path(_item["sfx"]) in media_map:
            referenced_shared.add(_item["sfx"])
    if packed.get("captions_file"):
        referenced_shared.add(packed["captions_file"])
    # LUTs are rewritten via media_map; collect their relpaths too.
    for clip in packed.get("clips") or []:
        if isinstance(clip, dict) and clip.get("lut"):
            referenced_shared.add(clip["lut"])
    copied = 0
    deduped = 0
    _copied_relpaths = set()
    for abspath, relpath in media_map.items():
        if relpath not in referenced_shared or relpath in _copied_relpaths:
            continue
        shutil.copyfile(abspath, os.path.join(outdir, relpath))
        _copied_relpaths.add(relpath)
        copied += 1
    # Count duplicate references skipped by dedup (references to
    # a canonical file beyond the first).
    seen_canonical = set()
    for abspath, canonical in collected.items():
        if canonical in seen_canonical:
            deduped += 1
        else:
            seen_canonical.add(canonical)
    trimmed = 0
    if trim_clips:
        for clip in packed.get("clips") or []:
            if not isinstance(clip, dict) or clip.get("id") not in trim_overrides:
                continue
            relpath, ss, _tin_r, _tout_r = trim_overrides[clip["id"]]
            iclip = info_by_id.get(clip.get("id")) or {}
            probe = iclip.get("probe") or {}
            window = _trim_clip_window(iclip, probe.get("duration"))
            if window is None:
                continue
            _ss, _to = window
            # Source is the ORIGINAL file (packed src was rewritten).
            orig = None
            for _c in project.get("clips") or []:
                if isinstance(_c, dict) and _c.get("id") == clip.get("id"):
                    orig = resolve_path(_c["src"])
                    break
            if orig is None:
                continue
            _trim_file(orig, os.path.join(outdir, relpath), _ss, _to)
            trimmed += 1
    with open(os.path.join(outdir, "project.json"), "w", encoding="utf-8") as fh:
        json.dump(packed, fh, ensure_ascii=False, indent=2)

    # The renderer itself: a copy of this package's source tree,
    # so the pack needs no repo checkout on the target device.
    src_dst = os.path.join(outdir, PACKAGE_SRC_DIR, "agentcut")
    if os.path.isdir(src_dst):
        shutil.rmtree(src_dst)
    shutil.copytree(os.path.join(BASE_DIR, "src", "agentcut"), src_dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    # Bundled fonts (with their licences), when the repo has them.
    fonts_src = os.path.join(BASE_DIR, "fonts")
    if os.path.isdir(fonts_src):
        fonts_dst = os.path.join(outdir, PACKAGE_FONTS_DIR)
        if os.path.isdir(fonts_dst):
            shutil.rmtree(fonts_dst)
        shutil.copytree(fonts_src, fonts_dst)
    render_sh = os.path.join(outdir, "render.sh")
    with open(render_sh, "w", encoding="utf-8") as fh:
        fh.write(RENDER_SH)
    os.chmod(render_sh, 0o755)
    render_bat = os.path.join(outdir, "render.bat")
    with open(render_bat, "w", encoding="utf-8") as fh:
        fh.write(RENDER_BAT)
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README_TEXT)
    return {"outdir": os.path.abspath(outdir), "files_copied": copied,
            "files_deduped": deduped, "files_trimmed": trimmed,
            "project": packed.get("id"),
            "total_duration": info.get("total_duration"),
            "render_command": "./render.sh"}
