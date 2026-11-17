"""OTIO (.otio) exporter — AgentCut projects to OpenTimelineIO timelines.

OpenTimelineIO (OTIO) is an Apache-2.0 interchange format whose .otio
files are plain JSON. This module writes that JSON directly with the
stdlib ``json`` module: no OTIO library code is copied, no new
dependency is added (the zero-dependency rule stands: stdlib + FFmpeg
only). The schema strings below (Timeline.1, Clip.2, Marker.2, ...)
match what OTIO 0.18.1 itself writes; they were verified against the
real library during development (see the builder log).

HONEST BOUNDARY — read before using the export:
OTIO carries cuts, clip order, timing, transitions, markers and media
references ONLY. It does NOT carry AgentCut effects, colour grades,
treatments, texts, captions (ASS/SRT), diagrams, motion graphics,
overlays, Ken Burns/camera moves, reframes, SFX or adjustments. The
exporter moves the timeline STRUCTURE; the receiving editor (DaVinci
Resolve, Premiere, Final Cut via OTIO adapters, Kdenlive) rebuilds the
effects by hand. Captions can be imported separately as .srt. This is
a property of timeline interchange itself, not a limitation of this
exporter, and it is also recorded in the exported file's metadata.

Mapping notes (the decisions that keep the export exact):
- Time is converted from float seconds to OTIO RationalTime at the
  project's fps. Frame boundaries are quantized (round(start * fps)),
  never durations, so the OTIO total is exactly round(total * fps)
  with no drift across many clips. This also fixes the float-time
  issue at the interchange boundary: inside the .otio file, every
  time is an exact integer frame count.
- AgentCut's xfade transition ends AT the cut: the incoming clip
  starts transition_duration before the outgoing clip ends. The
  OTIO transition is therefore in_offset=0, out_offset=td, and the
  outgoing clip's source_range is shortened by td — the dissolve
  then covers exactly the AgentCut blend window and the total
  duration matches. A transition adds no duration in OTIO.
- Every overlapping xfade transition maps to SMPTE_Dissolve (the
  only transition type OTIO adapters widely honour). crossfade and
  dissolve map exactly; wipes, slides and the other xfade shapes
  keep their exact TIMING but their shape is substituted by a
  dissolve — each substitution is recorded in the export warnings.
- "fade" (fade to/from black on the clip itself) and the custom
  transitions (zoom_out, whip_pan, glitch, light_leak) have no OTIO
  equivalent: they export as a hard cut, with a warning.
- speed maps to a LinearTimeWarp effect (time_scalar = speed);
  speed_curve splits the clip into per-segment clips, each with its
  own warp; freeze_at maps to a FreezeFrame effect; loop_count maps
  to N consecutive clips of the same trim; reverse has no OTIO
  equivalent and exports forward, with a warning.
- Chapters become 1-frame markers on the first video track (OTIO
  0.18 has no markers on the Timeline object itself; track markers
  are what Resolve imports as timeline markers).
- Track-1 clips (composited overlays) become V2+ tracks, grouped so
  no two clips overlap on one track. Audio tracks become one audio
  track each. Clip-embedded audio is not exported as separate audio
  tracks: the media reference still points at the same file, so an
  editor can relink the audio if desired.
- Media references carry the src exactly as written in project.json
  (relative paths preserved). Keep the .otio next to the project, or
  relink the media in the receiving editor.

DaVinci Resolve import is UNVERIFIED in this environment (Resolve
cannot run here); the user must open the file in Resolve himself.
What IS verified: the files parse in the real OTIO library and
round-trip with matching clip order, durations and markers.
"""
from __future__ import annotations

import json
import os

from .validate import CUSTOM_TRANSITIONS, XFADE_TRANSITIONS, probe_media, validate_project

# Marker colours, cycled by chapter index (OTIO MarkerColor values).
_MARKER_COLORS = ("RED", "GREEN", "BLUE", "YELLOW",
                  "PURPLE", "ORANGE", "CYAN", "MAGENTA")

# Transitions that map to SMPTE_Dissolve exactly (a dissolve by any
# other name); every other xfade shape is a timing-exact substitution.
_EXACT_DISSOLVES = {"crossfade", "dissolve"}


def _frames(seconds, fps):
    """Float seconds -> integer frames at fps (nearest frame)."""
    return int(round(float(seconds) * fps))


def _rt(value, fps):
    return {"OTIO_SCHEMA": "RationalTime.1",
            "rate": float(fps), "value": float(value)}


def _tr(start, duration, fps):
    return {"OTIO_SCHEMA": "TimeRange.1",
            "duration": _rt(duration, fps),
            "start_time": _rt(start, fps)}


def _external_ref(target_url, avail_start, avail_duration, fps):
    return {"OTIO_SCHEMA": "ExternalReference.1",
            "metadata": {},
            "name": "",
            "available_range": _tr(avail_start, avail_duration, fps),
            "available_image_bounds": None,
            "target_url": target_url}


def _clip(name, source_range, media_ref, effects=None):
    return {"OTIO_SCHEMA": "Clip.2",
            "metadata": {},
            "name": name,
            "source_range": source_range,
            "effects": effects or [],
            "markers": [],
            "enabled": True,
            "color": None,
            "media_references": {"DEFAULT_MEDIA": media_ref},
            "active_media_reference_key": "DEFAULT_MEDIA"}


def _gap(duration, fps):
    return {"OTIO_SCHEMA": "Gap.1",
            "metadata": {},
            "name": "",
            "source_range": _tr(0, duration, fps),
            "effects": [],
            "markers": [],
            "enabled": True,
            "color": None}


def _transition(name, in_frames, out_frames, fps):
    return {"OTIO_SCHEMA": "Transition.1",
            "metadata": {},
            "name": name,
            "in_offset": _rt(in_frames, fps),
            "out_offset": _rt(out_frames, fps),
            "transition_type": "SMPTE_Dissolve"}


def _marker(name, color, start, fps):
    return {"OTIO_SCHEMA": "Marker.2",
            "metadata": {},
            "name": name,
            "color": color,
            "marked_range": _tr(start, 1, fps),
            "comment": ""}


def _track(name, kind, children, markers=None):
    return {"OTIO_SCHEMA": "Track.1",
            "metadata": {},
            "name": name,
            "source_range": None,
            "effects": [],
            "markers": markers or [],
            "enabled": True,
            "color": None,
            "children": children,
            "kind": kind}


def _warp_effect(speed):
    return {"OTIO_SCHEMA": "LinearTimeWarp.1",
            "metadata": {},
            "name": "speed",
            "effect_name": "LinearTimeWarp",
            "enabled": True,
            "time_scalar": float(speed)}


def _freeze_effect():
    return {"OTIO_SCHEMA": "FreezeFrame.1",
            "metadata": {},
            "name": "freeze",
            "effect_name": "FreezeFrame",
            "enabled": True,
            "time_scalar": 0.0}


class _Exporter:
    """Builds one OTIO Timeline dict from a validated project."""

    def __init__(self, project, info):
        self.project = project
        self.info = info
        self.fps = float(info["fps"])
        self.total = float(info["total_duration"])
        self.total_frames = _frames(self.total, self.fps)
        self.warnings = []
        self.clips_exported = 0
        self.transitions_exported = 0
        # Original src strings as written in project.json (relative
        # paths preserved); resolved clips carry absolute paths.
        self._orig_src = {}
        for c in project.get("clips", []) or []:
            if isinstance(c, dict) and c.get("id") and c.get("src"):
                self._orig_src[c["id"]] = c["src"]
        # Audio tracks: the resolved list is parallel to the raw
        # list on a valid project, so match by index.
        self._orig_audio_src = []
        for t in project.get("audio", []) or []:
            if isinstance(t, dict):
                self._orig_audio_src.append(t.get("src"))

    def _src_for(self, clip):
        cid = clip.get("id", "")
        if cid in self._orig_src:
            return self._orig_src[cid]
        # Beat-cut / split segments carry ids like "id#b1": the
        # media is the parent clip's.
        parent = cid.split("#")[0] if "#" in cid else None
        if parent and parent in self._orig_src:
            return self._orig_src[parent]
        return clip["src"]

    def _media_ref(self, clip, avail_frames):
        return _external_ref(self._src_for(clip), 0, avail_frames,
                             self.fps)

    def _expand_clip(self, clip, start_s, end_s):
        """Expand one resolved clip into OTIO Clip items covering
        the float window [start_s, end_s) in timeline seconds.
        Returns a list of Clip dicts (usually one; several for
        loop_count / speed_curve)."""
        fps = self.fps
        cid = clip.get("id", "clip")
        kind = clip.get("kind")
        start_f = _frames(start_s, fps)
        end_f = _frames(end_s, fps)
        extent = end_f - start_f
        if extent <= 0:
            self.warnings.append(
                f"clip '{cid}' is shorter than one frame at {fps:g} fps "
                f"and was skipped (sub-frame content cannot be carried)")
            return []
        if kind == "image":
            # A still holds for exactly its timeline extent.
            ref = self._media_ref(clip, extent)
            self.clips_exported += 1
            return [_clip(cid, _tr(0, extent, fps), ref)]
        # Video clip.
        trim_in = float(clip.get("trim_in", 0.0))
        trim_out = float(clip.get("trim_out", 0.0))
        probe = clip.get("probe") or {}
        src_dur = probe.get("duration")
        avail = _frames(src_dur, fps) if src_dur else extent
        if avail < 1:
            avail = extent
        ref = self._media_ref(clip, avail)
        base = trim_out - trim_in
        loop_count = clip.get("loop_count")
        if loop_count:
            items = []
            for li in range(int(loop_count)):
                # Clamp each loop to the window: a following xfade
                # may shorten (or, when td >= base, remove) the
                # last loop.
                lf = max(start_f, _frames(start_s + li * base, fps))
                rf = min(end_f,
                         _frames(start_s + (li + 1) * base, fps))
                if rf - lf <= 0:
                    continue
                items.append(_clip(
                    f"{cid}#loop{li + 1}",
                    _tr(_frames(trim_in, fps), rf - lf, fps), ref))
                self.clips_exported += 1
            return items
        curve = clip.get("speed_curve")
        if curve:
            items = []
            cursor = start_s
            for si, seg in enumerate(curve):
                ss = float(seg["start"])
                se = float(seg["end"])
                sp = float(seg["speed"])
                seg_dur = (se - ss) / sp
                seg_end = cursor + seg_dur
                # Clamp to the window: a following xfade may cut
                # the last segment short.
                sf = max(start_f, _frames(cursor, fps))
                ef = min(end_f, _frames(seg_end, fps))
                if ef - sf > 0:
                    items.append(_clip(
                        f"{cid}#curve{si + 1}",
                        _tr(_frames(ss, fps), ef - sf, fps), ref,
                        effects=[_warp_effect(sp)]))
                    self.clips_exported += 1
                cursor = seg_end
            return items
        if clip.get("freeze_at") is not None:
            freeze_at = float(clip["freeze_at"])
            self.clips_exported += 1
            return [_clip(cid,
                          _tr(_frames(freeze_at, fps), extent, fps),
                          ref, effects=[_freeze_effect()])]
        speed = float(clip.get("speed", 1.0))
        effects = [_warp_effect(speed)] if speed != 1.0 else []
        if clip.get("reverse"):
            self.warnings.append(
                f"clip '{cid}' is reversed in AgentCut; OTIO has no "
                f"reverse effect, so it exports playing forward")
        self.clips_exported += 1
        return [_clip(cid,
                      _tr(_frames(trim_in, fps), extent, fps),
                      ref, effects=effects)]

    def _transition_item(self, clip):
        """The OTIO Transition before `clip` (the incoming clip), or
        None. Returns the item and records warnings for substituted
        or dropped transitions."""
        name = clip.get("transition", "none")
        if name in XFADE_TRANSITIONS:
            td = float(clip.get("transition_duration", 0.5))
            td_f = _frames(td, self.fps)
            if td_f <= 0:
                self.warnings.append(
                    f"transition '{name}' on clip '{clip.get('id')}' "
                    f"is shorter than one frame and was dropped")
                return None
            if name not in _EXACT_DISSOLVES:
                self.warnings.append(
                    f"transition '{name}' on clip '{clip.get('id')}' "
                    f"has no OTIO equivalent; exported as a dissolve "
                    f"with the exact same timing ({td:g}s)")
            label = ("Dissolve" if name in _EXACT_DISSOLVES
                     else f"{name} (exported as dissolve)")
            # in_offset=0: AgentCut's xfade ends at the cut (see
            # module docstring). The caller counts the item when it
            # is actually appended (never before the first clip).
            return _transition(label, 0, td_f, self.fps)
        if name == "fade":
            self.warnings.append(
                f"transition 'fade' on clip '{clip.get('id')}' is a "
                f"fade to/from black, which OTIO cannot carry; "
                f"exported as a hard cut")
        elif name in CUSTOM_TRANSITIONS:
            self.warnings.append(
                f"transition '{name}' on clip '{clip.get('id')}' is "
                f"an AgentCut post-join effect, which OTIO cannot "
                f"carry; exported as a hard cut")
        return None

    def _not_carried_warnings(self):
        p = self.project
        counts = []
        for key, label in (("texts", "text overlays"),
                           ("captions", "captions"),
                           ("overlays", "overlays"),
                           ("diagrams", "diagrams"),
                           ("motion", "motion elements"),
                           ("adjustments", "adjustment layers")):
            items = p.get(key)
            if isinstance(items, list) and items:
                counts.append(f"{len(items)} {label}")
        clips = self.info["clips"]
        graded = sum(1 for c in clips if c.get("grade"))
        treated = sum(1 for c in clips if c.get("treatment"))
        moving = sum(1 for c in clips
                     if c.get("ken_burns", "none") != "none"
                     or c.get("camera"))
        sfxed = sum(1 for c in clips if c.get("sfx"))
        reframed = sum(1 for c in clips if "reframe" in c)
        if graded:
            counts.append(f"{graded} graded clip(s)")
        if treated:
            counts.append(f"{treated} treated clip(s)")
        if moving:
            counts.append(f"{moving} clip(s) with Ken Burns/camera "
                          f"(exported as static frames)")
        if sfxed:
            counts.append(f"{sfxed} clip(s) with SFX")
        if reframed:
            counts.append(f"{reframed} clip(s) with reframe")
        if p.get("captions_file"):
            counts.append("a captions file (import the .srt "
                          "separately in the editor)")
        if counts:
            self.warnings.append(
                "not carried by OTIO (structure only): " +
                "; ".join(counts))

    def build(self):
        resolved = self.info["clips"]
        starts = self.info["clip_starts"]
        fps = self.fps
        # Track-0 clips in timeline order (index, clip, start).
        main = [(i, c, float(starts[i]))
                for i, c in enumerate(resolved)
                if c.get("track", 0) == 0]
        items = []
        for pos, (i, clip, start) in enumerate(main):
            # Every clip's transition is inspected (warnings for
            # fade/custom even on the first clip); an OTIO
            # Transition item only exists between two clips.
            tr_item = self._transition_item(clip)
            if pos > 0 and tr_item is not None:
                items.append(tr_item)
                self.transitions_exported += 1
            if pos + 1 < len(main):
                end = main[pos + 1][2]
            else:
                end = self.total
            items.extend(self._expand_clip(clip, start, end))
        markers = []
        for ci, ch in enumerate(self.info.get("chapters") or []):
            markers.append(_marker(
                ch["title"],
                _MARKER_COLORS[ci % len(_MARKER_COLORS)],
                _frames(ch["start"], fps), fps))
        tracks = [_track("V1", "Video", items, markers=markers)]
        # Track-1 clips: greedy non-overlap grouping into V2+ tracks.
        t1 = [(c, float(starts[i]))
              for i, c in enumerate(resolved)
              if c.get("track", 0) == 1]
        t1.sort(key=lambda pair: pair[1])
        lanes = []  # each lane: [items, end_frames]
        for clip, start in t1:
            start_f = _frames(start, fps)
            end_f = _frames(start + float(clip["duration"]), fps)
            if end_f - start_f <= 0:
                self.warnings.append(
                    f"track-1 clip '{clip.get('id')}' is shorter "
                    f"than one frame and was skipped")
                continue
            placed = False
            for lane in lanes:
                if start_f >= lane[1]:
                    if start_f > lane[1]:
                        lane[0].append(_gap(start_f - lane[1], fps))
                    lane[0].extend(self._expand_clip(
                        clip, start, start + float(clip["duration"])))
                    lane[1] = end_f
                    placed = True
                    break
            if not placed:
                lane_items = []
                if start_f > 0:
                    lane_items.append(_gap(start_f, fps))
                lane_items.extend(self._expand_clip(
                    clip, start, start + float(clip["duration"])))
                lanes.append([lane_items, end_f])
        for li, (lane_items, _end) in enumerate(lanes):
            tracks.append(_track(f"V{li + 2}", "Video", lane_items))
        # Audio tracks: one OTIO audio track each.
        for ai, atrack in enumerate(self.info.get("audio") or []):
            src = atrack.get("src")
            start = float(atrack.get("start", 0))
            probe = probe_media(src) if src else None
            src_dur = probe.get("duration") if probe else None
            if src_dur is None:
                self.warnings.append(
                    f"audio track {ai + 1} ({src}) could not be "
                    f"probed and was skipped")
                continue
            avail = max(1, _frames(src_dur, fps))
            extent = _frames(min(src_dur, self.total - start), fps)
            if extent <= 0:
                continue
            start_f = _frames(start, fps)
            aitems = []
            if start_f > 0:
                aitems.append(_gap(start_f, fps))
            url = (self._orig_audio_src[ai]
                   if ai < len(self._orig_audio_src)
                   and self._orig_audio_src[ai] else src)
            ref = _external_ref(url, 0, avail, fps)
            aitems.append(_clip(os.path.basename(src or "audio"),
                                _tr(0, extent, fps), ref))
            self.clips_exported += 1
            kind = atrack.get("kind", "music")
            tracks.append(_track(f"A{ai + 1} ({kind})", "Audio",
                                 aitems))
        self._not_carried_warnings()
        try:
            from . import __version__ as _ver
        except Exception:
            _ver = "unknown"
        timeline = {
            "OTIO_SCHEMA": "Timeline.1",
            "metadata": {
                "agentcut_version": _ver,
                "agentcut_project_id": self.project.get("id", ""),
                "comment": ("Exported by AgentCut. OTIO carries cuts, "
                            "order, timing, transitions, markers and "
                            "media references only; effects, grades, "
                            "texts, captions, diagrams, motion and "
                            "other AgentCut features are not carried."),
            },
            "name": self.project.get("title")
                    or self.project.get("id", "AgentCut timeline"),
            "global_start_time": None,
            "tracks": {
                "OTIO_SCHEMA": "Stack.1",
                "metadata": {},
                "name": "tracks",
                "source_range": None,
                "effects": [],
                "markers": [],
                "enabled": True,
                "color": None,
                "children": tracks,
            },
        }
        info = {
            "fps": self.fps,
            "total_seconds": self.total,
            "total_frames": self.total_frames,
            "clips": self.clips_exported,
            "transitions": self.transitions_exported,
            "tracks": len(tracks),
            "markers": len(markers),
            "warnings": self.warnings,
        }
        return timeline, info


def project_to_otio(project):
    """Build the OTIO Timeline dict for a project dict.

    Validates the project first (same validator as render).
    Returns (timeline_dict, info) on success; on validation
    failure returns (None, {"code": "INVALID_PROJECT", "errors": ...}).
    """
    if not isinstance(project, dict):
        return None, {"code": "INVALID_SCHEMA",
                      "message": "project must be an object"}
    errors, info = validate_project(project)
    if errors:
        return None, {"code": "INVALID_PROJECT", "errors": errors}
    exporter = _Exporter(project, info)
    return exporter.build()


def export_otio(project, output_path):
    """Export a project dict to a .otio file.

    Returns (True, info) with info["path"] on success, or
    (False, error) with a structured code/message on failure.
    The write is atomic (temp file + os.replace).
    """
    timeline, info = project_to_otio(project)
    if timeline is None:
        return False, info
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(timeline, fh, indent=4, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp_path, output_path)
    info = dict(info)
    info["path"] = output_path
    return True, info
