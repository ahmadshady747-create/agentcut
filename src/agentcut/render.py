"""FFmpeg rendering for AgentCut v1: sequential clips, text, audio.

Design note: clips are sequential, not free-form tracks, in v1. That is
deliberate — a sequential timeline is fully deterministic for an agent
to reason about, and every later track feature is an addition to this
core, not a replacement of it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid

from .validate import XFADE_TRANSITIONS, find_font, probe_media, validate_project


# Text overlays use drawtext textfile=, not text=. A literal apostrophe,
# colon or percent in text= breaks FFmpeg's filter parser no matter how
# it is backslash-escaped (verified 2026-10-28: single and double
# backslash both failed); a text file carries the exact characters.


def _even(n):
    return int(n) if int(n) % 2 == 0 else int(n) - 1


def _atempo_chain(speed):
    """atempo only accepts 0.5..100, so split out-of-range speeds."""
    parts = []
    v = float(speed)
    while v < 0.5:
        parts.append("atempo=0.5")
        v *= 2
    while v > 2.0:
        parts.append("atempo=2.0")
        v /= 2
    parts.append(f"atempo={v}")
    return ",".join(parts)


def build_ffmpeg_args(project, info, output_path, scale_factor=1.0):
    """Return (argv, total_duration) for a validated project."""
    width = _even(info["width"] * scale_factor)
    height = _even(info["height"] * scale_factor)
    fps = info["fps"]
    clips = info["clips"]
    total = info["total_duration"]
    argv = ["ffmpeg", "-y", "-v", "error"]
    # Inputs: clips first, then audio tracks.
    for clip in clips:
        if clip["kind"] == "image" and clip.get("ken_burns") not in (None, "none"):
            # Ken Burns uses zoompan on a SINGLE input frame: zoompan
            # itself generates d output frames. A looped input would
            # make zoompan emit d frames per input frame and blow up
            # the clip duration.
            pass
        elif clip["kind"] == "image":
            argv += ["-loop", "1", "-t", str(clip["duration"])]
        argv += ["-i", clip["src"]]
    audio_tracks = project.get("audio") or []
    for track in audio_tracks:
        argv += ["-i", track["src"]]
    filters = []
    # Per-clip video and audio.
    for i, clip in enumerate(clips):
        dur = clip["duration"]
        # Large-image guard: decode at most 2560px on the long side
        # before the main scale, so an 8K photo does not blow up
        # memory/time. Output quality is unaffected (output <= 1920px).
        downscale = ""
        if clip["kind"] == "image":
            _probe = clip.get("probe") or {}
            _iw, _ih = _probe.get("width"), _probe.get("height")
            if _iw and _ih and max(_iw, _ih) > 2560:
                downscale = "scale=2560:2560:force_original_aspect_ratio=decrease,"
        if clip["kind"] == "video":
            speed = float(clip.get("speed", 1.0))
            speed_part = f",setpts=PTS/{speed}" if speed != 1.0 else ""
            if clip.get("fit") == "cover":
                vf = (f"[{i}:v]trim=start={clip['trim_in']}:end={clip['trim_out']},"
                      f"setpts=PTS-STARTPTS{speed_part},scale={width}:{height}:"
                      f"force_original_aspect_ratio=increase,"
                      f"crop={width}:{height},"
                      f"fps={fps},format=yuv420p")
            else:
                vf = (f"[{i}:v]trim=start={clip['trim_in']}:end={clip['trim_out']},"
                      f"setpts=PTS-STARTPTS{speed_part},scale={width}:{height}:"
                      f"force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                      f"fps={fps},format=yuv420p")
        elif clip.get("ken_burns") not in (None, "none"):
            kb = clip["ken_burns"]
            frames = max(1, int(round(dur * fps)))
            # Pre-scale to 2x output (cropped to a centred 2x frame) so
            # zoompan always crops from more pixels than it outputs.
            pre = (f"{downscale}scale={2 * width}:{2 * height}:"
                   f"force_original_aspect_ratio=increase,"
                   f"crop={2 * width}:{2 * height}")
            if kb == "zoom_in":
                z = f"1+0.5*on/{frames}"
                x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif kb == "zoom_out":
                z = f"1.5-0.5*on/{frames}"
                x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif kb == "pan_left":
                z = "1.3"
                x_expr, y_expr = f"(iw-iw/zoom)*on/{frames}", "(ih-ih/zoom)/2"
            else:  # pan_right
                z = "1.3"
                x_expr, y_expr = f"(iw-iw/zoom)*(1-on/{frames})", "(ih-ih/zoom)/2"
            vf = (f"[{i}:v]{pre},zoompan=z='{z}':x='{x_expr}':y='{y_expr}':"
                  f"d={frames}:s={width}x{height}:fps={fps},"
                  f"setpts=PTS-STARTPTS,format=yuv420p")
        elif clip.get("fit") == "cover":
            vf = (f"[{i}:v]{downscale}scale={width}:{height}:"
                  f"force_original_aspect_ratio=increase,"
                  f"crop={width}:{height},"
                  f"fps={fps},format=yuv420p")
        else:
            vf = (f"[{i}:v]{downscale}scale={width}:{height}:"
                  f"force_original_aspect_ratio=decrease,"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                  f"fps={fps},format=yuv420p")
        fades = []
        if clip.get("transition") == "fade":
            if i > 0:
                fades.append("fade=t=in:st=0:d=0.3")
            next_transition = clips[i + 1].get("transition") if i < len(clips) - 1 else None
            if i < len(clips) - 1 and next_transition not in XFADE_TRANSITIONS:
                fades.append(f"fade=t=out:st={dur - 0.3:.3f}:d=0.3")
        if fades:
            vf += "," + ",".join(fades)
        filters.append(f"{vf}[v{i}]")
        probe = clip.get("probe") or {}
        if clip["kind"] == "video" and probe.get("has_audio"):
            speed = float(clip.get("speed", 1.0))
            tempo = f",{_atempo_chain(speed)}" if speed != 1.0 else ""
            filters.append(
                f"[{i}:a]atrim=start={clip['trim_in']}:end={clip['trim_out']},"
                f"asetpts=PTS-STARTPTS{tempo},aresample=48000,"
                f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=end={dur},"
                f"asetpts=PTS-STARTPTS[a{i}]")
    # Chain clips: true crossfade (xfade/acrossfade, 0.5s) where the
    # incoming clip asks for it, plain concat otherwise. The chain
    # duration therefore equals info["total_duration"].
    vcur_chain = "v0"
    cur_dur = clips[0]["duration"]
    for i in range(1, len(clips)):
        dur = clips[i]["duration"]
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            xname = XFADE_TRANSITIONS[clips[i]["transition"]]
            filters.append(
                f"[{vcur_chain}][v{i}]xfade=transition={xname}:duration=0.5:"
                f"offset={cur_dur - 0.5:.3f}[x{i}]")
            vcur_chain = f"x{i}"
            cur_dur += dur - 0.5
        else:
            filters.append(f"[{vcur_chain}][v{i}]concat=n=2:v=1:a=0[c{i}]")
            vcur_chain = f"c{i}"
            cur_dur += dur
    acur_chain = "a0"
    cur_dur_a = clips[0]["duration"]
    for i in range(1, len(clips)):
        dur = clips[i]["duration"]
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            filters.append(
                f"[{acur_chain}][a{i}]acrossfade=d=0.5:c1=tri:c2=tri[ax{i}]")
            acur_chain = f"ax{i}"
            cur_dur_a += dur - 0.5
        else:
            filters.append(f"[{acur_chain}][a{i}]concat=n=2:v=0:a=1[ca{i}]")
            acur_chain = f"ca{i}"
            cur_dur_a += dur
    # Text overlays, then captions, on the concatenated video.
    # Text goes through files (see note above), created here and cleaned
    # up by render_project after ffmpeg exits.
    font = info.get("font") or find_font()
    all_overlays = (project.get("texts") or []) + (info.get("captions", project.get("captions") or []))
    text_dir = None
    if all_overlays:
        text_dir = tempfile.mkdtemp(prefix="agentcut-text-")
    vcur = vcur_chain
    overlay_index = 0
    merged_captions = info.get("captions", project.get("captions") or [])
    for item in (project.get("texts") or []) + merged_captions:
        is_caption = overlay_index >= len(project.get("texts") or [])
        size = item.get("size", 36 if is_caption else 48)
        if is_caption:
            x_expr = "(w-text_w)/2"
            y_expr = str(height - 120)
        else:
            x = item.get("x", "center")
            x_expr = "(w-text_w)/2" if x == "center" else str(x)
            y_expr = str(item.get("y", 100))
        start = float(item.get("start", 0))
        end = float(item.get("end", total))
        if not is_caption and item.get("position"):
            pos = item["position"]
            pos_map = {
                "top_left": ("40", "40"),
                "top_center": ("(w-text_w)/2", "40"),
                "top_right": ("w-text_w-40", "40"),
                "center": ("(w-text_w)/2", "(h-text_h)/2"),
                "bottom_left": ("40", "h-text_h-40"),
                "bottom_center": ("(w-text_w)/2", "h-text_h-40"),
                "bottom_right": ("w-text_w-40", "h-text_h-40"),
            }
            x_expr, y_expr = pos_map[pos]
        color = item.get("color", "white")
        if isinstance(color, str) and color.startswith("#"):
            color = "0x" + color[1:]
        box = 1 if item.get("box", True) else 0
        box_part = ":box=1:boxcolor=black@0.5:boxborderw=12" if box else ":box=0"
        text_path = os.path.join(text_dir, f"text_{overlay_index}.txt")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write(str(item["text"]))
        filters.append(
            f"[{vcur}]drawtext=fontfile={font}:textfile={text_path}:"
            f"fontsize={size}:fontcolor={color}{box_part}:"
            f"x={x_expr}:y={y_expr}:"
            f"enable='between(t,{start},{end})'[vout{overlay_index}]")
        vcur = f"vout{overlay_index}"
        overlay_index += 1
    if overlay_index == 0:
        vout = vcur_chain
    else:
        vout = vcur
    # Audio tracks. When voice and music are both present, music is
    # ducked with a real sidechain compressor keyed off the voice mix
    # (FFmpeg sidechaincompress), not a flat volume cut: music drops
    # while voice is speaking and returns when it stops.
    has_voice = any(t.get("kind") == "voice" for t in audio_tracks)
    has_music = any(t.get("kind") == "music" for t in audio_tracks)
    mix_inputs = [f"[{acur_chain}]"]
    base = len(clips)
    for j, track in enumerate(audio_tracks):
        vol = float(track.get("volume", 1.0))
        start_ms = int(round(float(track.get("start", 0)) * 1000))
        fade_parts = ""
        if float(track.get("fade_in", 0)) > 0:
            fade_parts += f",afade=t=in:st={float(track.get('start', 0))}:d={float(track['fade_in'])}"
        if float(track.get("fade_out", 0)) > 0:
            fade_parts += f",afade=t=out:st={total - float(track['fade_out'])}:d={float(track['fade_out'])}"
        filters.append(
            f"[{base + j}:a]aresample=48000,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={start_ms}|{start_ms},apad=whole_dur={total},"
            f"atrim=end={total},asetpts=PTS-STARTPTS,"
            f"volume={vol}{fade_parts}[t{j}]")
    if has_voice and has_music:
        voice_labels = [f"[t{j}]" for j, t in enumerate(audio_tracks)
                        if t.get("kind") == "voice"]
        filters.append(
            f"{''.join(voice_labels)}amix=inputs={len(voice_labels)}:"
            f"duration=longest:normalize=0,asplit=2[voicemix][voice_out]")
        mix_inputs.append("[voice_out]")
        for j, track in enumerate(audio_tracks):
            if track.get("kind") != "music":
                continue
            filters.append(
                f"[t{j}][voicemix]sidechaincompress=threshold=0.02:"
                f"ratio=8:attack=200:release=1000:makeup=1[td{j}]")
            mix_inputs.append(f"[td{j}]")
    else:
        for j in range(len(audio_tracks)):
            mix_inputs.append(f"[t{j}]")
    if len(mix_inputs) == 1:
        aout = acur_chain
    else:
        filters.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
            f"duration=longest:normalize=0[aout]")
        aout = "aout"
    # Final range conversion: -pix_fmt alone can leave yuvj420p (full
    # range) from JPEG inputs; scale out_range=tv converts to
    # limited-range yuv420p, the format phone players expect.
    filters.append(f"[{vout}]scale=out_range=tv,format=yuv420p[vfinal]")
    filter_complex = ";".join(filters)
    argv += ["-filter_complex", filter_complex,
             "-map", "[vfinal]", "-map", f"[{aout}]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             # Force the widely playable pixel format. Without this,
             # image inputs (JPEG yuvj) can leak through xfade as
             # yuvj444p / High 4:4:4, which most phone players refuse.
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", "-t", str(total), output_path]
    return argv, total, text_dir


def render_project(project, output_path, scale_factor=1.0, timeout=600):
    """Render a project dict. Returns (ok, info_or_error)."""
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    argv, total, text_dir = build_ffmpeg_args(project, info, output_path, scale_factor)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"code": "RENDER_FAILED", "message": "render timed out"}
    finally:
        if text_dir:
            shutil.rmtree(text_dir, ignore_errors=True)
    if proc.returncode != 0:
        return False, {"code": "RENDER_FAILED", "message": proc.stderr[-2000:]}
    probe = probe_media(output_path)
    return True, {"output": output_path, "duration": total,
                  "width": info["width"], "height": info["height"],
                  "fps": info["fps"], "probe": probe}


def poster_frame(project, output_path, time=None, timeout=600):
    """Export one full-resolution frame as a PNG poster.

    time defaults to the middle of the timeline. The frame is taken
    from a real render of the project, so it matches the final video.
    """
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    total = info["total_duration"]
    if time is None:
        t = total / 2
    else:
        try:
            t = float(time)
        except (TypeError, ValueError):
            return False, {"code": "INVALID_SCHEMA", "message": "poster time must be a number"}
        if t < 0 or t > total:
            return False, {"code": "CLIP_OUT_OF_RANGE",
                           "message": f"poster time {t}s outside timeline {total:.2f}s"}
    t = min(max(t, 0.0), max(total - 0.01, 0.0)) if total > 0 else 0.0
    tmpdir = tempfile.mkdtemp(prefix="agentcut-poster-")
    try:
        tmp_video = os.path.join(tmpdir, "poster.mp4")
        ok, rinfo = render_project(project, tmp_video, timeout=timeout)
        if not ok:
            return False, rinfo
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", tmp_video,
             "-frames:v", "1", output_path],
            capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not os.path.isfile(output_path):
            return False, {"code": "RENDER_FAILED", "message": proc.stderr[-1000:]}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    probe = probe_media(output_path)
    return True, {"output": output_path, "time": t,
                  "width": probe["width"] if probe else None,
                  "height": probe["height"] if probe else None}


def preview_project(project, out_dir, scale_factor=0.5):
    """Render a proxy and extract frames + a contact sheet."""
    os.makedirs(out_dir, exist_ok=True)
    proxy = os.path.join(out_dir, "proxy.mp4")
    ok, info = render_project(project, proxy, scale_factor=scale_factor)
    if not ok:
        return False, info
    _, vinfo = validate_project(project)
    total = vinfo["total_duration"]
    times = sorted({0.0, total * 0.25, total * 0.5, total * 0.75, total * 0.99})
    frames = []
    for k, t in enumerate(times):
        frame = os.path.join(out_dir, f"frame_{k}.png")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", proxy,
             "-frames:v", "1", frame],
            capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and os.path.isfile(frame):
            frames.append({"time": t, "path": frame})
    contact = os.path.join(out_dir, "contact.png")
    if frames:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for f in frames:
            cmd += ["-i", f["path"]]
        labels = "".join(f"[{i}:v]" for i in range(len(frames)))
        cmd += ["-filter_complex", f"{labels}hstack=inputs={len(frames)}", contact]
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return True, {"proxy": proxy, "frames": frames,
                  "contact_sheet": contact if os.path.isfile(contact) else None,
                  "duration": total}


class JobStore:
    """Async render/preview jobs stored as JSON files under a directory."""

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, job_id):
        return os.path.join(self.directory, f"{job_id}.json")

    def create(self, kind, project):
        job_id = uuid.uuid4().hex[:12]
        record = {"id": job_id, "kind": kind, "status": "queued",
                  "project": project, "output": None, "error": None}
        with open(self._path(job_id), "w") as fh:
            json.dump(record, fh)
        return job_id

    def get(self, job_id):
        path = self._path(job_id)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def _write(self, record):
        with open(self._path(record["id"]), "w") as fh:
            json.dump(record, fh)

    def run_async(self, job_id, out_dir):
        record = self.get(job_id)
        if record is None:
            return
        record["status"] = "running"
        self._write(record)

        def _work():
            try:
                os.makedirs(out_dir, exist_ok=True)
                if record["kind"] == "preview":
                    ok, info = preview_project(record["project"], out_dir)
                else:
                    out_path = os.path.join(out_dir, "output.mp4")
                    ok, info = render_project(record["project"], out_path)
                record["status"] = "done" if ok else "failed"
                record["output"] = info if ok else None
                record["error"] = None if ok else info
            except Exception as exc:  # never leave a job stuck in running
                record["status"] = "failed"
                record["error"] = {"code": "RENDER_FAILED", "message": str(exc)}
            self._write(record)

        threading.Thread(target=_work, daemon=True).start()
