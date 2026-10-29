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

import json
import os
import shutil

from .validate import BASE_DIR, resolve_path, validate_project

PACKAGE_SRC_DIR = "src"
PACKAGE_MEDIA_DIR = "media"
PACKAGE_FONTS_DIR = "fonts"

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


def _collect_files(project, info):
    """(relpath -> abspath) for every file a render reads.

    Sources: validated clips (resolved srcs + LUTs), validated
    overlays (resolved srcs), validated audio tracks (resolved
    srcs), and the captions SRT file. Anything else in a project
    (texts, chapters, ducking windows, presets) is data, not a
    file reference, and is carried by project.json itself.
    """
    files = {}
    for clip in info.get("clips") or []:
        files[clip["src"]] = clip["src"]
        if clip.get("lut_resolved"):
            files[clip["lut_resolved"]] = clip["lut_resolved"]
    for ov in info.get("overlays") or []:
        files[ov["src"]] = ov["src"]
    for track in info.get("audio") or []:
        files[track["src"]] = track["src"]
    captions_file = project.get("captions_file")
    if captions_file:
        files[resolve_path(captions_file)] = resolve_path(captions_file)
    return files


def package_project(project, outdir):
    """Build a render pack. Returns a summary dict.

    Raises ValueError with a plain message when the project is
    invalid or a referenced file is missing — a pack must never
    be built from a project that cannot render.
    """
    errors, info = validate_project(project)
    if errors:
        raise ValueError("project is invalid: " + json.dumps(errors, ensure_ascii=False))
    media_map = {}  # abspath -> media/ relpath
    used = set()
    for abspath in _collect_files(project, info):
        if not os.path.isfile(abspath):
            raise ValueError(f"referenced file not found: {abspath}")
        media_map[abspath] = os.path.join(
            PACKAGE_MEDIA_DIR, _unique_name(used, os.path.basename(abspath)))

    packed = json.loads(json.dumps(project, ensure_ascii=False))

    def _rewrite(path):
        resolved = resolve_path(path)
        return media_map[resolved]

    for clip in packed.get("clips") or []:
        if isinstance(clip, dict) and clip.get("src"):
            clip["src"] = _rewrite(clip["src"])
        if isinstance(clip, dict) and clip.get("lut"):
            clip["lut"] = _rewrite(clip["lut"])
    for ov in packed.get("overlays") or []:
        if isinstance(ov, dict) and ov.get("src"):
            ov["src"] = _rewrite(ov["src"])
    for track in packed.get("audio") or []:
        if isinstance(track, dict) and track.get("src"):
            track["src"] = _rewrite(track["src"])
    if packed.get("captions_file"):
        packed["captions_file"] = _rewrite(packed["captions_file"])

    os.makedirs(outdir, exist_ok=True)
    media_dir = os.path.join(outdir, PACKAGE_MEDIA_DIR)
    os.makedirs(media_dir, exist_ok=True)
    copied = 0
    for abspath, relpath in media_map.items():
        shutil.copyfile(abspath, os.path.join(outdir, relpath))
        copied += 1
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
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README_TEXT)
    return {"outdir": os.path.abspath(outdir), "files_copied": copied,
            "project": packed.get("id"),
            "total_duration": info.get("total_duration"),
            "render_command": "./render.sh"}
