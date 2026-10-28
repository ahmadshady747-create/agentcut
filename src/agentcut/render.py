"""FFmpeg rendering for AgentCut v1: sequential clips, text, audio.

Design note: clips are sequential, not free-form tracks, in v1. That is
deliberate — a sequential timeline is fully deterministic for an agent
to reason about, and every later track feature is an addition to this
core, not a replacement of it.
"""
from __future__ import annotations

import json
import os
import re
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


POS_MAP = {
    "top_left": ("40", "40"),
    "top_center": ("(w-text_w)/2", "40"),
    "top_right": ("w-text_w-40", "40"),
    "center": ("(w-text_w)/2", "(h-text_h)/2"),
    "bottom_left": ("40", "h-text_h-40"),
    "bottom_center": ("(w-text_w)/2", "h-text_h-40"),
    "bottom_right": ("w-text_w-40", "h-text_h-40"),
}


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



def _effect_chain(obj):
    """Filter suffix for the generic per-clip/overlay effects.

    Field names are deliberately generic (blur, sharpen, vignette,
    hue, pixelize) and map to plain FFmpeg filters, not to any
    other editor's effect names.
    """
    parts = []
    if "blur" in obj and float(obj["blur"]) > 0:
        parts.append(f"gblur=sigma={float(obj['blur'])}")
    if "sharpen" in obj and float(obj["sharpen"]) > 0:
        parts.append(f"unsharp=5:5:{float(obj['sharpen'])}")
    if obj.get("vignette"):
        parts.append("vignette=PI/5")
    if "hue" in obj and float(obj["hue"]) != 0:
        parts.append(f"hue=h={float(obj['hue'])}")
    if "pixelize" in obj:
        parts.append(f"pixelize=w={int(obj['pixelize'])}:h={int(obj['pixelize'])}")
    return ("," + ",".join(parts)) if parts else ""


def _mask_geq(mask):
    """geq filter that keeps only the mask region, softly if feathered.

    Coordinates are pixels in the frame the filter sees (output frame
    for clips, overlay frame for overlays). The alpha expression
    multiplies by the incoming alpha, so chroma-key transparency
    survives the mask.
    """
    f = int(mask.get("feather", 0))
    if mask["shape"] == "rect":
        x0, y0 = int(mask["mask_x"]), int(mask["mask_y"])
        x1, y1 = x0 + int(mask["mask_w"]), y0 + int(mask["mask_h"])
        if f > 0:
            fx = f"clip(min(X-{x0},{x1}-X)/{f},0,1)"
            fy = f"clip(min(Y-{y0},{y1}-Y)/{f},0,1)"
        else:
            fx = f"gte(X,{x0})*lte(X,{x1})"
            fy = f"gte(Y,{y0})*lte(Y,{y1})"
        alpha = f"({fx})*({fy})*255"
    else:
        cx, cy, r = int(mask["mask_x"]), int(mask["mask_y"]), int(mask["mask_r"])
        if f > 0:
            alpha = f"clip(({r}+{f}-hypot(X-{cx},Y-{cy}))/{f},0,1)*255"
        else:
            alpha = f"if(lt(hypot(X-{cx},Y-{cy}),{r}),1,0)*255"
    return (f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha}'")


def _scale_part(fit, width, height):
    """Scale (and pad/crop) string for a clip at the output size."""
    if fit == "cover":
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}")
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")


def _input_count(argv):
    """Number of -i inputs already present in argv."""
    return sum(1 for a in argv if a == "-i")


def _interp_expr(points, var):
    """Piecewise-linear expression for `var` over (time, value) points.

    points must be sorted by strictly increasing time. Outside the
    range the first/last value holds. Used for overlay keyframes:
    var is "t" for position (main timeline time) or an overlay-time
    expression for opacity (segment start + N/fps).
    """
    if len(points) == 1:
        return repr(float(points[0][1]))
    expr = repr(float(points[-1][1]))
    for i in range(len(points) - 2, -1, -1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        seg = f"{v0!r}+({v1!r}-{v0!r})*({var}-{t0!r})/({t1!r}-{t0!r})"
        expr = f"if(lt({var},{t1!r}),{seg},{expr})"
    return f"if(lt({var},{points[0][0]!r}),{points[0][1]!r},{expr})"


def _interp_value(points, t):
    """Python-side linear interpolation over (time, value) points."""
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        t0, v0 = points[i]
        t1, v1 = points[i + 1]
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


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
    overlays = info.get("overlays", []) or []
    # Overlay segments: normally one per overlay. An overlay whose
    # scale keyframes vary is split into short segments (<=0.5s,
    # capped at 40) because FFmpeg crop/scale dimensions are fixed
    # at filter configuration time — a single stream cannot change
    # its frame size per frame. Each segment is a separate input
    # with a fixed scale interpolated at the segment start; position
    # stays smooth because the overlay x/y expressions use main
    # timeline time, and opacity stays smooth via geq (see below).
    overlay_segments = []  # (overlay, seg_start, seg_end, input_index)
    for ov in overlays:
        kfs = ov.get("keyframes_resolved")
        scales_vary = bool(kfs) and len({f["scale"] for f in kfs}) > 1
        if scales_vary:
            bounds = sorted({f["time"] for f in kfs} | {float(ov["duration"])})
            segs = []
            for a, b in zip(bounds, bounds[1:]):
                steps = max(1, int(-(-(b - a) // 0.5)))
                for k in range(steps):
                    segs.append((a + (b - a) * k / steps, a + (b - a) * (k + 1) / steps))
            if len(segs) > 40:
                dur = float(ov["duration"])
                segs = [(dur * k / 40, dur * (k + 1) / 40) for k in range(40)]
        else:
            segs = [(0.0, float(ov["duration"]))]
        for seg_start, seg_end in segs:
            idx = _input_count(argv)
            if ov["kind"] == "image":
                argv += ["-loop", "1", "-t", str(seg_end - seg_start)]
            argv += ["-i", ov["src"]]
            overlay_segments.append((ov, seg_start, seg_end, idx))
    filters = []
    # Per-clip video and audio. vlabels[i] is the label the chain
    # must use for clip i (v{i}, or vout{i} when chroma-keyed).
    vlabels = []
    for i, clip in enumerate(clips):
        dur = clip["duration"]
        # Large-image guard: decode at most the output long side
        # (and at least 2560px) before the main scale, so an 8K
        # photo does not blow up memory/time. For a 3840px export
        # the guard rises to 3840 so detail is not thrown away.
        guard = max(2560, width, height)
        downscale = ""
        if clip["kind"] == "image":
            _probe = clip.get("probe") or {}
            _iw, _ih = _probe.get("width"), _probe.get("height")
            if _iw and _ih and max(_iw, _ih) > guard:
                downscale = f"scale={guard}:{guard}:force_original_aspect_ratio=decrease,"
        if clip["kind"] == "video":
            speed = float(clip.get("speed", 1.0))
            speed_part = f",setpts=PTS/{speed}" if speed != 1.0 else ""
            dur = clip["duration"]
            if clip.get("freeze_at") is not None:
                # Freeze: one frame, looped for the whole trim
                # duration. The loop filter repeats a 1-frame buffer.
                fa = float(clip["freeze_at"])
                pre = (f"trim=start={fa}:end={fa + 0.05},setpts=PTS-STARTPTS,"
                       f"fps={fps},loop=loop=-1:size=1:start=0,"
                       f"trim=end={dur},setpts=PTS-STARTPTS")
            elif clip.get("reverse"):
                pre = (f"trim=start={clip['trim_in']}:end={clip['trim_out']},"
                       f"reverse,setpts=PTS-STARTPTS{speed_part}")
            elif clip.get("loop_count"):
                base_dur = float(clip["trim_out"]) - float(clip["trim_in"])
                frames = max(1, int(round(base_dur * fps)))
                pre = (f"trim=start={clip['trim_in']}:end={clip['trim_out']},"
                       f"setpts=PTS-STARTPTS,fps={fps},"
                       f"loop=loop={int(clip['loop_count']) - 1}:size={frames}:start=0,"
                       f"trim=end={dur},setpts=PTS-STARTPTS")
            else:
                pre = (f"trim=start={clip['trim_in']}:end={clip['trim_out']},"
                       f"setpts=PTS-STARTPTS{speed_part}")
            if clip.get("_stab_trf"):
                # Two-pass vidstab: pass 1 (detect) ran in
                # render_project at this same scale; pass 2
                # transforms the scaled frames.
                vf = (f"[{i}:v]trim=start={clip['trim_in']}:end={clip['trim_out']},"
                      f"setpts=PTS-STARTPTS,"
                      f"{_scale_part(clip.get('fit'), width, height)},"
                      f"vidstabtransform=input={clip['_stab_trf']},"
                      f"fps={fps},format=yuv420p")
            elif clip.get("fit") == "blur":
                # Blurred background: the same stream twice — once
                # scaled to cover and blurred/darkened as the
                # background, once scaled to contain as the
                # foreground, composited centred.
                filters.append(f"[{i}:v]{pre},split=2[ba{i}][bb{i}]")
                filters.append(
                    f"[ba{i}]scale={width}:{height}:"
                    f"force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},gblur=sigma=20,"
                    f"eq=brightness=-0.2[bg{i}]")
                filters.append(
                    f"[bb{i}]scale={width}:{height}:"
                    f"force_original_aspect_ratio=decrease[fg{i}]")
                vf = (f"[bg{i}][fg{i}]overlay=(main_w-overlay_w)/2:"
                      f"(main_h-overlay_h)/2,fps={fps},format=yuv420p")
            elif clip.get("fit") == "cover":
                vf = (f"[{i}:v]{pre},scale={width}:{height}:"
                      f"force_original_aspect_ratio=increase,"
                      f"crop={width}:{height},"
                      f"fps={fps},format=yuv420p")
            else:
                vf = (f"[{i}:v]{pre},scale={width}:{height}:"
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
        elif clip.get("fit") == "blur":
            filters.append(f"[{i}:v]{downscale}split=2[ba{i}][bb{i}]")
            filters.append(
                f"[ba{i}]scale={width}:{height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=20,"
                f"eq=brightness=-0.2[bg{i}]")
            filters.append(
                f"[bb{i}]scale={width}:{height}:"
                f"force_original_aspect_ratio=decrease[fg{i}]")
            vf = (f"[bg{i}][fg{i}]overlay=(main_w-overlay_w)/2:"
                  f"(main_h-overlay_h)/2,fps={fps},format=yuv420p")
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
        if clip["kind"] == "video" and clip.get("speed_curve"):
            # Speed curve: each segment is trimmed from the source,
            # retimed at its own speed, scaled, and concatenated.
            # Segments partition the trimmed range (validated), so
            # the concat duration is the curve total.
            seg_labels = []
            for si, seg in enumerate(clip["speed_curve"]):
                ss, se, sp = float(seg["start"]), float(seg["end"]), float(seg["speed"])
                sp_part = f",setpts=PTS/{sp}" if sp != 1.0 else ""
                seg_pre = f"trim=start={ss}:end={se},setpts=PTS-STARTPTS{sp_part}"
                if clip.get("fit") == "cover":
                    seg_scale = (f"scale={width}:{height}:"
                                 f"force_original_aspect_ratio=increase,"
                                 f"crop={width}:{height}")
                else:
                    seg_scale = (f"scale={width}:{height}:"
                                 f"force_original_aspect_ratio=decrease,"
                                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
                filters.append(f"[{i}:v]{seg_pre},{seg_scale},fps={fps},format=yuv420p[sc{si}]")
                seg_labels.append(f"[sc{si}]")
            vf = "".join(seg_labels) + f"concat=n={len(seg_labels)}:v=1:a=0"
        # Per-clip colour filters, applied after scale/format so every
        # clip kind (video, image, Ken Burns) gets them identically.
        eq_parts = []
        if "brightness" in clip:
            eq_parts.append(f"brightness={float(clip['brightness'])}")
        if "contrast" in clip:
            eq_parts.append(f"contrast={float(clip['contrast'])}")
        if clip.get("grayscale"):
            eq_parts.append("saturation=0")
        elif "saturation" in clip:
            eq_parts.append(f"saturation={float(clip['saturation'])}")
        if eq_parts:
            vf += ",eq=" + ":".join(eq_parts)
        vf += _effect_chain(clip)
        fades = []
        if clip.get("transition") == "fade":
            ftd = float(clip.get("transition_duration", 0.3))
            if i > 0:
                fades.append(f"fade=t=in:st=0:d={ftd:.3f}")
            next_transition = clips[i + 1].get("transition") if i < len(clips) - 1 else None
            if i < len(clips) - 1 and next_transition not in XFADE_TRANSITIONS:
                fades.append(f"fade=t=out:st={dur - ftd:.3f}:d={ftd:.3f}")
        if fades:
            vf += "," + ",".join(fades)
        if clip.get("mask"):
            # Geq only changes alpha, and yuv420p has no alpha, so a
            # masked main clip must be composited over black below;
            # the chain stays rgba here on purpose.
            vf += ",format=rgba," + _mask_geq(clip["mask"])
        filters.append(f"{vf}[v{i}]")
        vlabel = f"v{i}"
        if clip.get("mask") and not clip.get("chroma_key_hex"):
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={dur}[bgm{i}]")
            filters.append(
                f"[bgm{i}][v{i}]overlay=0:0,format=yuv420p[vout{i}]")
            vlabel = f"vout{i}"
        if clip.get("chroma_key_hex"):
            # Key out the colour, then composite over black: a
            # chroma-keyed main clip shows the background through
            # the keyed area instead of a green rectangle.
            sim = float(clip.get("chroma_similarity", 0.3))
            blend = float(clip.get("chroma_blend", 0.1))
            filters.append(
                f"[v{i}]format=rgba,"
                f"colorkey=0x{clip['chroma_key_hex']}:similarity={sim}:blend={blend},"
                f"format=yuva420p[vck{i}]")
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={dur}[bgc{i}]")
            filters.append(
                f"[bgc{i}][vck{i}]overlay=0:0,format=yuv420p[vout{i}]")
            vlabel = f"vout{i}"
        vlabels.append(vlabel)
        probe = clip.get("probe") or {}
        dn = ",afftdn" if clip.get("denoise") else ""
        if clip["kind"] == "video" and probe.get("has_audio") and clip.get("freeze_at") is None and clip.get("speed_curve"):
            seg_labels_a = []
            for si, seg in enumerate(clip["speed_curve"]):
                ss, se, sp = float(seg["start"]), float(seg["end"]), float(seg["speed"])
                tempo = f",{_atempo_chain(sp)}" if sp != 1.0 else ""
                filters.append(
                    f"[{i}:a]atrim=start={ss}:end={se},"
                    f"asetpts=PTS-STARTPTS{tempo},aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo{dn}[sa{si}]")
                seg_labels_a.append(f"[sa{si}]")
            filters.append(
                "".join(seg_labels_a) + f"concat=n={len(seg_labels_a)}:v=0:a=1[a{i}]")
        elif clip["kind"] == "video" and probe.get("has_audio") and clip.get("freeze_at") is None:
            speed = float(clip.get("speed", 1.0))
            tempo = f",{_atempo_chain(speed)}" if speed != 1.0 else ""
            if clip.get("reverse"):
                filters.append(
                    f"[{i}:a]atrim=start={clip['trim_in']}:end={clip['trim_out']},"
                    f"areverse,asetpts=PTS-STARTPTS{tempo},aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo{dn}[a{i}]")
            elif clip.get("loop_count"):
                base_dur = float(clip["trim_out"]) - float(clip["trim_in"])
                samples = max(1, int(round(base_dur * 48000)))
                filters.append(
                    f"[{i}:a]atrim=start={clip['trim_in']}:end={clip['trim_out']},"
                    f"asetpts=PTS-STARTPTS,aresample=48000,"
                    f"aloop=loop={int(clip['loop_count']) - 1}:size={samples}:start=0,"
                    f"atrim=end={clip['duration']},asetpts=PTS-STARTPTS,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo{dn}[a{i}]")
            else:
                filters.append(
                    f"[{i}:a]atrim=start={clip['trim_in']}:end={clip['trim_out']},"
                    f"asetpts=PTS-STARTPTS{tempo},aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo{dn}[a{i}]")
        else:
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=end={dur},"
                f"asetpts=PTS-STARTPTS[a{i}]")
    # Chain clips: true crossfade (xfade/acrossfade, 0.5s) where the
    # incoming clip asks for it, plain concat otherwise. The chain
    # duration therefore equals info["total_duration"].
    vcur_chain = vlabels[0]
    cur_dur = clips[0]["duration"]
    for i in range(1, len(clips)):
        dur = clips[i]["duration"]
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            xname = XFADE_TRANSITIONS[clips[i]["transition"]]
            xtd = float(clips[i].get("transition_duration", 0.5))
            filters.append(
                f"[{vcur_chain}][{vlabels[i]}]xfade=transition={xname}:duration={xtd:.3f}:"
                f"offset={cur_dur - xtd:.3f}[x{i}]")
            vcur_chain = f"x{i}"
            cur_dur += dur - xtd
        else:
            filters.append(f"[{vcur_chain}][{vlabels[i]}]concat=n=2:v=1:a=0[c{i}]")
            vcur_chain = f"c{i}"
            cur_dur += dur
    acur_chain = "a0"
    cur_dur_a = clips[0]["duration"]
    for i in range(1, len(clips)):
        dur = clips[i]["duration"]
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            atd = float(clips[i].get("transition_duration", 0.5))
            filters.append(
                f"[{acur_chain}][a{i}]acrossfade=d={atd:.3f}:c1=tri:c2=tri[ax{i}]")
            acur_chain = f"ax{i}"
            cur_dur_a += dur - atd
        else:
            filters.append(f"[{acur_chain}][a{i}]concat=n=2:v=0:a=1[ca{i}]")
            acur_chain = f"ca{i}"
            cur_dur_a += dur
    # Overlays (Loop 5 round 1): picture-in-picture tracks above the
    # main timeline. Each overlay is scaled to a fraction of the
    # output width, delayed to its timeline start, and composited
    # with overlay= only while it is on screen (enable between
    # start and end, eof_action=pass so the main timeline continues
    # after the overlay stream ends). Overlay audio is not mixed in
    # v1: an overlay is a visual track; sound comes from project
    # audio tracks, which the agent can align with the same start.
    OVERLAY_POS = {
        "top_left": ("20", "20"),
        "top_center": ("(main_w-overlay_w)/2", "20"),
        "top_right": ("main_w-overlay_w-20", "20"),
        "center": ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"),
        "bottom_left": ("20", "main_h-overlay_h-20"),
        "bottom_center": ("(main_w-overlay_w)/2", "main_h-overlay_h-20"),
        "bottom_right": ("main_w-overlay_w-20", "main_h-overlay_h-20"),
    }
    vcur = vcur_chain
    for si, (ov, seg_start, seg_end, idx) in enumerate(overlay_segments):
        ostart = float(ov["start"])
        odur = float(ov["duration"])
        kfs = ov.get("keyframes_resolved")
        abs_start = ostart + seg_start
        abs_end = ostart + seg_end
        if ov["kind"] == "video":
            if kfs and len({f["scale"] for f in kfs}) > 1:
                # Segmented scale keyframes: this segment shows one
                # slice of the trimmed source (looping excluded by
                # validation for this combination).
                s_in = float(ov["trim_in"]) + seg_start
                s_out = float(ov["trim_in"]) + seg_end
            else:
                s_in, s_out = float(ov["trim_in"]), float(ov["trim_out"])
                src_dur = float(ov.get("source_duration", odur))
            chain = (f"[{idx}:v]trim=start={s_in}:end={s_out},"
                     f"setpts=PTS-STARTPTS,fps={fps}")
            if not (kfs and len({f["scale"] for f in kfs}) > 1) and odur > src_dur + 0.01:
                frames = max(1, int(round(src_dur * fps)))
                chain += (f",loop=loop=-1:size={frames}:start=0,"
                          f"trim=end={odur},setpts=PTS-STARTPTS")
        else:
            chain = f"[{idx}:v]fps={fps},setpts=PTS-STARTPTS"
            _probe = ov.get("probe") or {}
            _iw, _ih = _probe.get("width"), _probe.get("height")
            _guard = max(2560, width, height)
            if _iw and _ih and max(_iw, _ih) > _guard:
                chain += f",scale={_guard}:{_guard}:force_original_aspect_ratio=decrease"
        if kfs and len({f["scale"] for f in kfs}) > 1:
            seg_scale = _interp_value([(f["time"], f["scale"]) for f in kfs], seg_start)
        else:
            seg_scale = float(ov["size"])
        ow = _even(seg_scale * width)
        chain += f",scale={ow}:-2,format=rgba"
        if ov.get("chroma_key_hex"):
            sim = float(ov.get("chroma_similarity", 0.3))
            blend = float(ov.get("chroma_blend", 0.1))
            chain += (f",colorkey=0x{ov['chroma_key_hex']}:similarity={sim}:blend={blend}")
        if ov.get("mask"):
            chain += "," + _mask_geq(ov["mask"])
        chain += _effect_chain(ov)
        if kfs and len({f["opacity"] for f in kfs}) > 1:
            # Smooth opacity via geq: N counts frames of this segment
            # stream (fps-normalised), so overlay time is
            # seg_start + N/fps. Verified against FFmpeg: geq's T
            # does not track segment time, N does.
            op_expr = _interp_expr([(f["time"], f["opacity"]) for f in kfs],
                                   f"({seg_start!r}+N/{fps})")
            chain += (f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                      f"a='255*({op_expr})'")
        elif float(ov.get("opacity", 1.0)) < 1.0:
            chain += f",colorchannelmixer=aa={float(ov['opacity'])}"
        chain += f",setpts=PTS+{abs_start}/TB[ov{si}]"
        filters.append(chain)
        if kfs:
            # Smooth position: overlay x/y expressions use main
            # timeline time t (verified: overlay honours t).
            x_expr = _interp_expr([(ostart + f["time"], f["x"]) for f in kfs], "t")
            y_expr = _interp_expr([(ostart + f["time"], f["y"]) for f in kfs], "t")
        else:
            px, py = OVERLAY_POS[ov.get("position", "bottom_right")]
            x_expr = str(ov["x"]) if "x" in ov else px
            y_expr = str(ov["y"]) if "y" in ov else py
        filters.append(
            f"[{vcur}][ov{si}]overlay=x='{x_expr}':y='{y_expr}':"
            f"eof_action=pass:enable='between(t,{abs_start},{abs_end})'[vov{si}]")
        vcur = f"vov{si}"
    # Text overlays, then captions, on the concatenated video.
    # Text goes through files (see note above), created here and cleaned
    # up by render_project after ffmpeg exits.
    font = info.get("font") or find_font()
    all_overlays = (project.get("texts") or []) + (info.get("captions", project.get("captions") or []))
    text_dir = None
    if all_overlays:
        text_dir = tempfile.mkdtemp(prefix="agentcut-text-")
    overlay_index = 0
    merged_captions = info.get("captions", project.get("captions") or [])
    for item in (project.get("texts") or []) + merged_captions:
        is_caption = overlay_index >= len(project.get("texts") or [])
        size = item.get("size", 36 if is_caption else 48)
        if is_caption:
            if item.get("position"):
                x_expr, y_expr = POS_MAP[item["position"]]
            else:
                x_expr = "(w-text_w)/2"
                y_expr = str(height - 120)
        else:
            x = item.get("x", "center")
            x_expr = "(w-text_w)/2" if x == "center" else str(x)
            y_expr = str(item.get("y", 100))
        start = float(item.get("start", 0))
        end = float(item.get("end", total))
        if not is_caption and item.get("position"):
            x_expr, y_expr = POS_MAP[item["position"]]
        color = item.get("color", "white")
        if isinstance(color, str) and color.startswith("#"):
            color = "0x" + color[1:]
        box = 1 if item.get("box", True) else 0
        box_part = ":box=1:boxcolor=black@0.5:boxborderw=12" if box else ":box=0"
        fi = float(item.get("fade_in", 0) or 0)
        fo = float(item.get("fade_out", 0) or 0)
        alpha_part = ""
        if fi > 0 and fo > 0:
            alpha_part = (f":alpha='if(lt(t,{start}+{fi}),(t-{start})/{fi},"
                          f"if(gt(t,{end}-{fo}),({end}-t)/{fo},1))'")
        elif fi > 0:
            alpha_part = f":alpha='if(lt(t,{start}+{fi}),(t-{start})/{fi},1)'"
        elif fo > 0:
            alpha_part = f":alpha='if(gt(t,{end}-{fo}),({end}-t)/{fo},1)'"
        # Animated text (Loop 5 round 7). Slide moves x in main
        # timeline time t (drawtext x honours t, verified); pop
        # scales fontsize with t (also verified per-frame).
        anim = item.get("animation")
        ad = float(item.get("animation_duration", 1.0))
        x_out = x_expr
        size_out = str(size)
        if anim in ("slide_in", "slide_out", "slide_in_out"):
            in_expr = (f"({x_expr})*(t-{start})/{ad}-text_w*(1-(t-{start})/{ad})")
            out_expr = (f"({x_expr})+(w-({x_expr})+text_w)*((t-({end}-{ad}))/{ad})")
            if anim == "slide_in":
                x_out = f"if(lt(t,{start + ad}),{in_expr},{x_expr})"
            elif anim == "slide_out":
                x_out = f"if(gt(t,{end - ad}),{out_expr},{x_expr})"
            else:
                x_out = (f"if(lt(t,{start + ad}),{in_expr},"
                         f"if(gt(t,{end - ad}),{out_expr},{x_expr}))")
        if anim == "pop":
            size_out = (f"if(lt(t,{start + ad}),"
                        f"{size}*(0.5+0.5*(t-{start})/{ad}),{size})")
        stroke = int(item.get("stroke", 0))
        stroke_part = ""
        if stroke > 0:
            sc = item.get("stroke_color", "black")
            if isinstance(sc, str) and sc.startswith("#"):
                sc = "0x" + sc[1:]
            stroke_part = f":borderw={stroke}:bordercolor={sc}"
        text_path = os.path.join(text_dir, f"text_{overlay_index}.txt")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write(str(item["text"]))
        filters.append(
            f"[{vcur}]drawtext=fontfile={font}:textfile={text_path}:"
            f"fontsize='{size_out}':fontcolor={color}{box_part}{alpha_part}{stroke_part}:"
            f"x='{x_out}':y={y_expr}:"
            f"enable='between(t,{start},{end})'[vout{overlay_index}]")
        vcur = f"vout{overlay_index}"
        overlay_index += 1
    # vcur is the last stage so far: overlay output when overlays exist
    # (and no texts), the last text output when texts exist, or the
    # main chain when neither exists. Resetting to vcur_chain here
    # would silently drop the overlay stage.
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
        loudnorm_part = ""
        if track.get("loudnorm"):
            # Single-pass EBU R128-style normalisation to -16 LUFS
            # (online-video target, not the -23 broadcast target).
            loudnorm_part = ",loudnorm=I=-16:TP=-1.5:LRA=11"
        dn_part = ",afftdn" if track.get("denoise") else ""
        filters.append(
            f"[{base + j}:a]aresample=48000,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={start_ms}|{start_ms},apad=whole_dur={total},"
            f"atrim=end={total},asetpts=PTS-STARTPTS,"
            f"volume={vol}{fade_parts}{loudnorm_part}{dn_part}[t{j}]")
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


def _remove_quietly(path):
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def render_project(project, output_path, scale_factor=1.0, timeout=600):
    """Render a project dict. Returns (ok, info_or_error).

    Atomic output: ffmpeg writes to a temp file in the output
    directory, and it is moved into place only on success. A failed
    render therefore never leaves a partial file at output_path, and
    never overwrites a previous good output at that path.
    """
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    # Stabilisation pass 1 (vidstabdetect) for stabilized clips:
    # the transform file is computed at the clip's final scale, so
    # pass 2 in the main graph sees identical frames. A failed
    # detect fails the render rather than silently shipping an
    # unstabilized clip.
    stab_dir = None
    stab_clips = [c for c in info["clips"] if c.get("stabilize") and c["kind"] == "video"]
    if stab_clips:
        stab_dir = tempfile.mkdtemp(prefix="agentcut-stab-")
        tw = _even(info["width"] * scale_factor)
        th = _even(info["height"] * scale_factor)
        for si, clip in enumerate(stab_clips):
            trf = os.path.join(stab_dir, f"stab_{si}.trf")
            vf = (f"trim=start={clip['trim_in']}:end={clip['trim_out']},"
                  f"setpts=PTS-STARTPTS,"
                  f"{_scale_part(clip.get('fit'), tw, th)},"
                  f"vidstabdetect=result={trf}")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", clip["src"],
                 "-vf", vf, "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=300)
            if proc.returncode != 0 or not os.path.isfile(trf):
                if stab_dir:
                    shutil.rmtree(stab_dir, ignore_errors=True)
                return False, {"code": "RENDER_FAILED",
                               "message": f"stabilisation detect failed for clip {clip.get('id')}: {proc.stderr[-500:]}"}
            clip["_stab_trf"] = trf
    # Pre-scale image clips before the main filter graph: the main
    # graph (especially xfade chains) then decodes small files instead
    # of full-size photos, and the >2560px guard becomes a no-op.
    # A failed pre-scale falls back to the original file, so this can
    # never break a render that would otherwise work.
    prescale_dir = None
    image_clips = [c for c in info["clips"] if c["kind"] == "image"]
    if image_clips:
        prescale_dir = tempfile.mkdtemp(prefix="agentcut-prescale-")
        tw = _even(info["width"] * scale_factor)
        th = _even(info["height"] * scale_factor)
        for ci, clip in enumerate(image_clips):
            if clip.get("fit") == "blur":
                # Blurred-background clips need the unpadded source
                # in the main graph (background cover + foreground
                # contain); a contain prescale would bake black bars
                # into the background.
                continue
            if clip.get("ken_burns") not in (None, "none"):
                pw, ph = 2 * tw, 2 * th
                vf = (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,"
                      f"crop={pw}:{ph}")
            elif clip.get("fit") == "cover":
                pw, ph = tw, th
                vf = (f"scale={pw}:{ph}:force_original_aspect_ratio=increase,"
                      f"crop={pw}:{ph}")
            else:
                pw, ph = tw, th
                vf = (f"scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
                      f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2")
            tmp_img = os.path.join(prescale_dir, f"img_{ci}.jpg")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", clip["src"],
                 "-vf", vf, "-frames:v", "1", "-q:v", "2", tmp_img],
                capture_output=True, text=True, timeout=120)
            if proc.returncode == 0 and os.path.isfile(tmp_img):
                clip["src"] = tmp_img
                if clip.get("probe"):
                    clip["probe"] = {**clip["probe"], "width": pw, "height": ph}
    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".agentcut-tmp-", suffix=".mp4", dir=out_dir)
        os.close(fd)
    except OSError as exc:
        return False, {"code": "RENDER_FAILED",
                       "message": f"cannot create output temp file in {out_dir}: {exc}"}
    argv, total, text_dir = build_ffmpeg_args(project, info, tmp_path, scale_factor)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _remove_quietly(tmp_path)
        return False, {"code": "RENDER_FAILED", "message": "render timed out"}
    finally:
        if text_dir:
            shutil.rmtree(text_dir, ignore_errors=True)
        if prescale_dir:
            shutil.rmtree(prescale_dir, ignore_errors=True)
        if stab_dir:
            shutil.rmtree(stab_dir, ignore_errors=True)
    if proc.returncode != 0:
        _remove_quietly(tmp_path)
        return False, {"code": "RENDER_FAILED", "message": proc.stderr[-2000:]}
    try:
        os.replace(tmp_path, output_path)
    except OSError as exc:
        _remove_quietly(tmp_path)
        return False, {"code": "RENDER_FAILED",
                       "message": f"cannot move finished render into place: {exc}"}
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


FORMATS = {
    "vertical": (720, 1280),
    "square": (720, 720),
    "landscape": (1280, 720),
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


def _brief_kind(src):
    return "image" if os.path.splitext(src)[1].lower() in _IMAGE_EXTS else "video"



def _run_detect(path, filter_arg, kind, duration, timeout=60):
    """Return (segments, total, ok). kind is 'black' or 'silence'."""
    if kind == "black":
        cmd = ["ffmpeg", "-v", "info", "-i", path, "-vf", filter_arg,
               "-an", "-f", "null", "-"]
        start_re = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)")
    else:
        cmd = ["ffmpeg", "-v", "info", "-i", path, "-af", filter_arg,
               "-vn", "-sn", "-f", "null", "-"]
        start_re = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return [], 0.0, False
    if proc.returncode != 0:
        return [], 0.0, False
    text = proc.stderr or ""
    segments = []
    if kind == "black":
        for m in start_re.finditer(text):
            st, en, du = (float(x) for x in m.groups())
            segments.append({"start": st, "end": en, "duration": du})
    else:
        open_start = None
        for line in text.splitlines():
            m = re.search(r"silence_start:\s*([0-9.\-]+)", line)
            if m:
                open_start = float(m.group(1))
                continue
            m = re.search(r"silence_end:\s*([0-9.\-]+)\s*\|\s*silence_duration:\s*([0-9.]+)", line)
            if m and open_start is not None:
                en, du = float(m.group(1)), float(m.group(2))
                segments.append({"start": open_start, "end": en, "duration": du})
                open_start = None
        if open_start is not None and duration:
            segments.append({"start": open_start, "end": duration,
                             "duration": max(0.0, duration - open_start)})
    total = sum(seg["duration"] for seg in segments)
    return segments, total, True



def _brief_audio(brief):
    """Build the audio list for a brief, including the soundtrack shortcut.

    `soundtrack` is a string (a music source path) or a dict with `src`
    plus any of volume/start/fade_in/fade_out/loudnorm. It is appended
    as a music track after any explicit `audio` list. Returns a list,
    or raises ValueError with a structured-style message (caught by
    produce_video below into an INVALID_SCHEMA error).
    """
    audio = list(brief.get("audio") or [])
    st = brief.get("soundtrack")
    if st is None:
        return audio
    if isinstance(st, str):
        audio.append({"src": st, "kind": "music", "volume": 1.0})
    elif isinstance(st, dict) and st.get("src"):
        track = {"src": st["src"], "kind": "music"}
        for key in ("volume", "start", "fade_in", "fade_out", "loudnorm"):
            if key in st:
                track[key] = st[key]
        audio.append(track)
    else:
        raise ValueError("soundtrack must be a path string or a dict with src")
    return audio



def produce_video(brief, output_path, review_dir=None):
    """Producer-level one call: a short brief in, a finished video out.

    A brief is far smaller than a full project: a format preset, a list
    of media paths, an optional title, and optional texts/audio. The
    function builds the full project, validates it, renders it, and
    extracts review frames from the finished video, so any agent —
    Muse, Claude, Cursor, or any other MCP client — can produce a
    video in one step and then review its own frames before delivery.
    Returns (ok, info_or_error).
    """
    if not isinstance(brief, dict):
        return False, {"code": "INVALID_SCHEMA", "message": "produce brief must be an object"}
    fmt = brief.get("format", "vertical")
    if fmt not in FORMATS:
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"format must be one of {sorted(FORMATS)}"}
    width, height = FORMATS[fmt]
    # Sections are an alternative to a flat clips list, not an addition:
    # each section contributes its clips in order, and gets an auto
    # title card at its start (see below). A brief with both is an
    # error, so the timeline can never be ambiguous.
    sections = brief.get("sections")
    section_first = []
    if sections is not None:
        if brief.get("clips"):
            return False, {"code": "INVALID_SCHEMA",
                           "message": "use sections or clips, not both"}
        if not isinstance(sections, list) or not sections:
            return False, {"code": "EMPTY_TIMELINE",
                           "message": "produce brief sections must be a non-empty list"}
        brief_items = []
        for si, sec in enumerate(sections):
            if not isinstance(sec, dict) or not sec.get("clips"):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"section {si} needs a non-empty clips list"}
            section_first.append(len(brief_items))
            brief_items.extend(sec["clips"])
    else:
        brief_items = brief.get("clips") or []
    if not brief_items:
        return False, {"code": "EMPTY_TIMELINE", "message": "produce brief needs a non-empty clips list"}
    clips = []
    for i, item in enumerate(brief_items):
        if not isinstance(item, dict) or not item.get("src"):
            return False, {"code": "INVALID_SCHEMA", "message": f"clip {i} needs a src path"}
        src = item["src"]
        kind = item.get("kind") or _brief_kind(src)
        clip = {"id": item.get("id", f"c{i + 1}"), "kind": kind, "src": src}
        if kind == "image":
            clip["duration"] = float(item.get("duration", 3))
        else:
            if item.get("trim_in") is not None:
                clip["trim_in"] = item["trim_in"]
            if item.get("trim_out") is not None:
                clip["trim_out"] = item["trim_out"]
        if i > 0:
            clip["transition"] = item.get("transition", "crossfade")
        for key in ("ken_burns", "fit", "speed", "brightness", "contrast",
                    "saturation", "grayscale", "transition_duration"):
            if key in item:
                clip[key] = item[key]
        clips.append(clip)
    # Timeline durations and clip starts in final-timeline coordinates
    # (after xfade overlaps; fade-to-black does not shorten the
    # timeline). Used for the total, for section title cards, and
    # for the default title card.
    durations = []
    for clip in clips:
        if clip["kind"] == "image":
            dur = float(clip["duration"])
        else:
            info_p = probe_media(clip["src"])
            if info_p is None or info_p.get("duration") is None:
                return False, {"code": "FILE_NOT_FOUND",
                               "message": f"cannot probe video clip: {clip['src']}"}
            dur = (clip.get("trim_out", info_p["duration"])
                   - clip.get("trim_in", 0))
            if "speed" in clip:
                dur = dur / float(clip["speed"])
        durations.append(dur)
    starts = [0.0]
    total = durations[0] if durations else 0.0
    for i in range(1, len(clips)):
        overlap = 0.0
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            overlap = float(clips[i].get("transition_duration", 0.5))
        starts.append(starts[-1] + durations[i - 1] - overlap)
        total += durations[i] - overlap
    try:
        brief_audio = _brief_audio(brief)
    except ValueError as exc:
        return False, {"code": "INVALID_SCHEMA", "message": str(exc)}
    texts = list(brief.get("texts") or [])
    # Section title cards: one auto text at each section's start.
    section_info = []
    if sections is not None:
        for si, sec in enumerate(sections):
            title_s = sec.get("title")
            if not title_s:
                continue
            st = starts[section_first[si]]
            nxt = (starts[section_first[si + 1]] if si + 1 < len(sections)
                   else total)
            end = min(st + 4.0, nxt, total)
            if end <= st:
                end = total
            tstart = st + 0.5 if st + 0.5 < end else st
            texts.append({"text": title_s, "start": tstart, "end": end,
                          "size": 64, "x": "center", "y": 100})
            section_info.append({"title": title_s, "start": st, "end": end})
    title = brief.get("title")
    if title and not brief.get("texts") and sections is None:
        end = min(4.0, total - 0.5) if total > 1.5 else total
        texts.append({"text": title, "start": 0.5, "end": end,
                      "size": 64, "x": "center", "y": 100})
    project = {
        "id": brief.get("id", "produced"),
        "title": title or "Produced video",
        "version": 1,
        "output": {"width": width, "height": height,
                   "fps": int(brief.get("fps", 24))},
        "clips": clips,
        "texts": texts,
        "captions": brief.get("captions") or [],
        "audio": brief_audio,
    }
    if brief.get("overlays"):
        project["overlays"] = brief["overlays"]
    if brief.get("caption_color") is not None:
        project["caption_color"] = brief["caption_color"]
    if brief.get("caption_size") is not None:
        project["caption_size"] = brief["caption_size"]
    if brief.get("caption_position") is not None:
        project["caption_position"] = brief["caption_position"]
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    ok, rinfo = render_project(project, output_path)
    if not ok:
        return False, rinfo
    probe = probe_media(output_path) or {}
    video = [st for st in (probe.get("raw", {}).get("streams") or [])
             if st.get("codec_type") == "video"]
    has_audio = any(st.get("codec_type") == "audio"
                    for st in (probe.get("raw", {}).get("streams") or []))
    actual_duration = float((probe.get("raw", {}) or {}).get("format", {}).get("duration") or 0.0)
    actual_w = video[0].get("width") if video else None
    actual_h = video[0].get("height") if video else None
    actual_pix = video[0].get("pix_fmt") if video else None
    black_segs, black_total, black_ok = _run_detect(
        output_path, "blackdetect=d=0.5:pix_th=0.10", "black", actual_duration)
    sil_segs, sil_total, sil_ok = _run_detect(
        output_path, "silencedetect=noise=-50dB:d=0.5", "silence", actual_duration)
    warnings = []
    if abs(actual_duration - total) > 0.5:
        warnings.append(f"duration mismatch: expected {total:.2f}s, got {actual_duration:.2f}s")
    if actual_w != width or actual_h != height:
        warnings.append(f"resolution mismatch: expected {width}x{height}, got {actual_w}x{actual_h}")
    if actual_pix != "yuv420p":
        warnings.append(f"pix_fmt is {actual_pix}, expected yuv420p (phone playback risk)")
    if not has_audio:
        warnings.append("no audio stream in output")
    if not black_ok:
        warnings.append("black-frame detection unavailable")
    elif black_total > 0:
        warnings.append(f"black frames detected: {black_total:.1f}s in {len(black_segs)} segment(s)")
    if not sil_ok:
        warnings.append("silence detection unavailable")
    elif has_audio and actual_duration and sil_total >= actual_duration - 0.5:
        warnings.append("audio is silent throughout; add a music track if you want sound")
    qa = {
        "duration_expected": total, "duration_actual": actual_duration,
        "width_expected": width, "height_expected": height,
        "width_actual": actual_w, "height_actual": actual_h,
        "pix_fmt": actual_pix, "has_audio": has_audio,
        "black_segments": black_segs, "black_duration": black_total,
        "silence_segments": sil_segs, "silence_duration": sil_total,
        "warnings": warnings,
    }
    # Overlay feedback (integration item e): placement and
    # keyframe summary in the result, plus warnings for overlays
    # that would be invisible or dominate the frame — never silent.
    overlay_summary = []
    for ov in info.get("overlays", []):
        overlay_summary.append({
            "id": ov["id"], "kind": ov["kind"],
            "start": float(ov["start"]),
            "end": round(float(ov["start"]) + float(ov["duration"]), 3),
            "size": float(ov.get("size", 0.35)),
            "position": ov.get("position", "bottom_right"),
            "opacity": float(ov.get("opacity", 1.0)),
            "keyframes": len(ov.get("keyframes_resolved", []) or []),
        })
        if float(ov.get("opacity", 1.0)) == 0:
            warnings.append(f"overlay {ov['id']} is fully transparent (opacity 0) and will not be visible")
        if float(ov.get("size", 0.35)) >= 0.9:
            warnings.append(f"overlay {ov['id']} covers {float(ov['size']) * 100:.0f}% of the frame width and may dominate the main timeline")
    conformance = check_brief_conformance(brief, project, actual_duration=info["total_duration"])
    if review_dir is None:
        review_dir = output_path + ".review"
    os.makedirs(review_dir, exist_ok=True)
    frames = []
    total = info["total_duration"]
    for k, t in enumerate(sorted({0.0, total * 0.25, total * 0.5,
                                  total * 0.75, total * 0.99})):
        frame = os.path.join(review_dir, f"frame_{k}.png")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", output_path,
             "-frames:v", "1", frame],
            capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and os.path.isfile(frame):
            frames.append({"time": t, "path": frame})
    return True, {
        "output": output_path,
        "duration": total,
        "width": width,
        "height": height,
        "fps": project["output"]["fps"],
        "pix_fmt": video[0].get("pix_fmt") if video else None,
        "has_video": bool(video),
        "has_audio": any(st.get("codec_type") == "audio"
                         for st in (probe.get("raw", {}).get("streams") or [])),
        "audio_tracks_provided": bool(project["audio"]),
        "review_frames": frames,
        "review_dir": review_dir,
        "qa": qa,
        "overlays": overlay_summary,
        "conformance": conformance,
        "sections": section_info,
        "project": project,
    }


def produce_variants(brief, output_dir):
    """Render one brief as vertical, square and landscape in one call.

    Each variant is a full produce_video run (validate, render,
    review frames, QA) with the same clips, title, texts and audio,
    only the format preset changed. Outputs are
    <output_dir>/<brief-id>-<format>.mp4. Returns (ok, info) where
    info["variants"] maps format -> produce info (project stripped).
    On a variant failure the call stops and reports that format;
    variants already produced stay on disk (each is atomic).
    """
    import copy
    if not isinstance(brief, dict):
        return False, {"code": "INVALID_SCHEMA", "message": "produce brief must be an object"}
    os.makedirs(output_dir, exist_ok=True)
    base_id = brief.get("id", "produced")
    variants = {}
    for fmt in ("vertical", "square", "landscape"):
        variant = copy.deepcopy(brief)
        variant["format"] = fmt
        out = os.path.join(output_dir, f"{base_id}-{fmt}.mp4")
        ok, info = produce_video(variant, out)
        if not ok:
            return False, {"code": info.get("code", "RENDER_FAILED"),
                           "message": info.get("message") or info,
                           "format": fmt, "variants": variants}
        info = dict(info)
        info.pop("project", None)
        variants[fmt] = info
    return True, {"output_dir": output_dir, "variants": variants}



def timeline_summary(project):
    """A compact, agent-readable summary of a project timeline.

    The agent reasons from this instead of re-reading the whole
    project: segments with start/end, media kind, transitions,
    texts, captions, audio tracks and overlays. Returns
    (ok, summary_or_error).
    """
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    segments = []
    for i, clip in enumerate(info["clips"]):
        start = info["clip_starts"][i] if i < len(info["clip_starts"]) else 0.0
        seg = {"index": i, "clip_id": clip["id"], "kind": clip["kind"],
               "src": clip["src"], "start": round(start, 3),
               "end": round(start + float(clip["duration"]), 3),
               "duration": round(float(clip["duration"]), 3),
               "transition": clip.get("transition", "none")}
        for fname in ("speed", "speed_curve", "reverse", "freeze_at",
                      "loop_count", "ken_burns", "fit", "stabilize"):
            if fname in clip:
                seg[fname] = clip[fname]
        segments.append(seg)
    texts = [{"text": t["text"], "start": float(t.get("start", 0)),
              "end": float(t.get("end", info["total_duration"]))}
             for t in (project.get("texts") or [])]
    captions = [{"text": c["text"], "start": float(c.get("start", 0)),
                 "end": float(c.get("end", info["total_duration"]))}
                for c in info.get("captions", [])]
    audio = [{"src": t["src"], "kind": t.get("kind", "music"),
              "start": float(t.get("start", 0)), "volume": float(t.get("volume", 1.0))}
             for t in (project.get("audio") or [])]
    overlays = [{"id": o["id"], "kind": o["kind"], "src": o["src"],
                 "start": float(o["start"]),
                 "end": round(float(o["start"]) + float(o["duration"]), 3),
                 "size": float(o.get("size", 0.35)),
                 "position": o.get("position", "bottom_right"),
                 "opacity": float(o.get("opacity", 1.0)),
                 "keyframes": len(o.get("keyframes_resolved", []) or [])}
                for o in info.get("overlays", [])]
    return True, {"id": project["id"],
                  "total_duration": round(info["total_duration"], 3),
                  "width": info["width"], "height": info["height"],
                  "fps": info["fps"], "segments": segments,
                  "texts": texts, "captions": captions,
                  "audio": audio, "overlays": overlays}


def check_brief_conformance(brief, project, actual_duration=None):
    """Check a produced project against the brief that asked for it.

    Returns a dict with an overall pass/fail and one check per
    requirement: every brief clip used, title present, format
    matches, duration within target (when the brief sets
    target_duration), brief captions present, soundtrack/audio
    present. This is deterministic verification of the agent's
    own plan against execution — it never generates content.
    """
    if not isinstance(brief, dict) or not isinstance(project, dict):
        return {"passed": False, "checks": [{"name": "input",
                "passed": False, "detail": "brief and project must both be objects"}]}
    import os as _os
    errors, info = validate_project(project)
    if errors:
        return {"passed": False, "checks": [{"name": "project_valid",
                "passed": False, "detail": f"project has {len(errors)} validation error(s)"}]}
    checks = []
    # Brief clips: flat list, or sections flattened (same as produce).
    brief_items = []
    if brief.get("sections"):
        for sec in brief["sections"]:
            if isinstance(sec, dict):
                brief_items.extend(sec.get("clips") or [])
    else:
        brief_items = brief.get("clips") or []
    proj_bases = {_os.path.basename(c["src"]) for c in info["clips"]}
    missing = [it["src"] for it in brief_items
               if isinstance(it, dict) and it.get("src")
               and _os.path.basename(it["src"]) not in proj_bases]
    checks.append({"name": "clips_used", "passed": not missing,
                   "detail": f"{len(brief_items) - len(missing)}/{len(brief_items)} brief clips used"
                             + (f"; missing: {', '.join(missing)}" if missing else "")})
    titles = []
    if brief.get("title"):
        titles.append(brief["title"])
    if brief.get("sections"):
        titles.extend(sec.get("title") for sec in brief["sections"]
                      if isinstance(sec, dict) and sec.get("title"))
    proj_texts = [t["text"] for t in (project.get("texts") or [])]
    missing_titles = [t for t in titles if t not in proj_texts]
    checks.append({"name": "title_present",
                   "passed": not missing_titles if titles else True,
                   "detail": ("no title in brief" if not titles else
                              ("all titles present" if not missing_titles else
                               f"missing titles: {', '.join(missing_titles)}"))})
    if brief.get("format") in FORMATS:
        fw, fh = FORMATS[brief["format"]]
        checks.append({"name": "format", "passed": info["width"] == fw and info["height"] == fh,
                       "detail": f"expected {fw}x{fh}, got {info['width']}x{info['height']}"})
    total = actual_duration if actual_duration is not None else info["total_duration"]
    if brief.get("target_duration") is not None:
        try:
            target = float(brief["target_duration"])
            tol = max(0.5, 0.1 * target)
            checks.append({"name": "duration_within_target",
                           "passed": abs(total - target) <= tol,
                           "detail": f"target {target}s, actual {total:.2f}s, tolerance {tol:.2f}s"})
        except (TypeError, ValueError):
            checks.append({"name": "duration_within_target", "passed": False,
                           "detail": "target_duration must be a number"})
    else:
        checks.append({"name": "duration", "passed": True,
                       "detail": f"no target_duration in brief; actual {total:.2f}s"})
    brief_caps = brief.get("captions") or []
    if brief_caps:
        proj_caps = [(c["text"], float(c.get("start", 0)), float(c.get("end", 0)))
                     for c in info.get("captions", [])]
        missing_caps = []
        for cap in brief_caps:
            if not isinstance(cap, dict):
                continue
            hit = any(pc[0] == cap.get("text") and abs(pc[1] - float(cap.get("start", 0))) <= 0.1
                      and abs(pc[2] - float(cap.get("end", 0))) <= 0.1 for pc in proj_caps)
            if not hit:
                missing_caps.append(str(cap.get("text")))
        checks.append({"name": "captions_present", "passed": not missing_caps,
                       "detail": (f"{len(brief_caps) - len(missing_caps)}/{len(brief_caps)} brief captions present"
                                  + (f"; missing: {', '.join(missing_caps)}" if missing_caps else ""))})
    if brief.get("soundtrack") is not None or brief.get("audio"):
        st_src = None
        if isinstance(brief.get("soundtrack"), str):
            st_src = brief["soundtrack"]
        elif isinstance(brief.get("soundtrack"), dict):
            st_src = brief["soundtrack"].get("src")
        proj_audio_bases = {_os.path.basename(t["src"]) for t in (project.get("audio") or [])}
        ok_audio = bool(project.get("audio"))
        if st_src:
            ok_audio = _os.path.basename(st_src) in proj_audio_bases
        checks.append({"name": "audio_present", "passed": ok_audio,
                       "detail": ("soundtrack present in project audio" if ok_audio else
                                  "soundtrack/audio from the brief is missing from project audio")})
    passed = all(c["passed"] for c in checks)
    return {"passed": passed, "checks": checks}


def split_clip(project, clip_id, at):
    """Split one clip into two at `at` seconds into its rendered duration.

    Returns (ok, new_project_or_error). The first part keeps the
    original clip's incoming transition; the second part continues
    with transition "none". The timeline total is unchanged, so
    texts and captions keep working. This is a pure function on a
    project dict: an agent gets a project, splits it, and stores
    the result with create_project (same id overwrites).
    """
    import copy
    if not isinstance(project, dict):
        return False, {"code": "INVALID_SCHEMA", "message": "project must be an object"}
    try:
        at = float(at)
    except (TypeError, ValueError):
        return False, {"code": "INVALID_SCHEMA", "message": "split time must be a number",
                       "fix": "pass at as seconds into the clip's rendered duration"}
    clips = project.get("clips") or []
    idx = next((i for i, c in enumerate(clips) if isinstance(c, dict) and c.get("id") == clip_id), None)
    if idx is None:
        return False, {"code": "INVALID_SCHEMA", "message": f"no clip with id {clip_id}",
                       "fix": "use a clip id from the project's clips list"}
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    resolved = info["clips"][idx]
    dur = float(resolved["duration"])
    if not 0 < at < dur:
        return False, {"code": "CLIP_OUT_OF_RANGE",
                       "message": f"split time {at}s outside clip duration {dur:.2f}s",
                       "fix": f"split between 0 and {dur:.2f} seconds into the clip"}
    orig = copy.deepcopy(clips[idx])
    first = copy.deepcopy(orig)
    second = copy.deepcopy(orig)
    first["id"] = f"{clip_id}-a"
    second["id"] = f"{clip_id}-b"
    second["transition"] = "none"
    second.pop("transition_duration", None)
    if orig.get("kind") == "video":
        speed = float(orig.get("speed", 1.0))
        if orig.get("speed_curve"):
            # Map the rendered split time back to a source time
            # through the curve, then split the curve itself.
            acc = 0.0
            src_at = None
            first_segs, second_segs = [], []
            for seg in orig["speed_curve"]:
                ss, se, sp = float(seg["start"]), float(seg["end"]), float(seg["speed"])
                rend = (se - ss) / sp
                if src_at is None and acc <= at <= acc + rend:
                    src = ss + (at - acc) * sp
                    src_at = src
                    if src > ss:
                        first_segs.append({"start": ss, "end": src, "speed": sp})
                    if src < se:
                        second_segs.append({"start": src, "end": se, "speed": sp})
                elif src_at is None:
                    first_segs.append(dict(seg))
                else:
                    second_segs.append(dict(seg))
                acc += rend
            if src_at is None:
                return False, {"code": "CLIP_OUT_OF_RANGE",
                               "message": "split time does not fall inside the speed curve",
                               "fix": "split between 0 and the clip's rendered duration"}
            first["speed_curve"] = first_segs
            second["speed_curve"] = second_segs
            first["trim_out"] = src_at
            second["trim_in"] = src_at
        elif orig.get("freeze_at") is not None:
            # A frozen clip split keeps the same frozen frame on both
            # parts; only the trim window changes (duration is trim
            # duration, so the split point is in trim seconds).
            first["trim_out"] = float(orig["trim_in"]) + at
            second["trim_in"] = float(orig["trim_in"]) + at
        else:
            src_at = float(orig.get("trim_in", 0)) + at * speed
            first["trim_out"] = src_at
            second["trim_in"] = src_at
        # loop_count / reverse on a split clip would change both
        # parts' meaning; reject them here with a fix, rather than
        # silently producing a wrong split.
        if orig.get("loop_count") is not None or orig.get("reverse"):
            return False, {"code": "INVALID_SCHEMA",
                           "message": "cannot split a clip with loop_count or reverse",
                           "fix": "remove loop_count/reverse first, split, then re-apply to a part if needed"}
    else:
        first["duration"] = at
        second["duration"] = dur - at
    new_project = copy.deepcopy(project)
    new_project["clips"] = clips[:idx] + [first, second] + clips[idx + 1:]
    errors2, info2 = validate_project(new_project)
    if errors2:
        return False, {"code": "INVALID_PROJECT", "errors": errors2}
    return True, new_project


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
