"""OTIO (.otio) exporter tests (AgentCut v1.62.0).

Verifies the stdlib-only OpenTimelineIO exporter: clip order,
source_range math (including fps conversion), chapter markers,
transitions, multi-track export, speed/freeze/loop/speed_curve
mapping, audio tracks, relative media paths, error handling, the
export_otio MCP tool (34th tool) and the CLI. The round-trip test
uses the real opentimelineio library when it is installed and skips
otherwise (it is verification tooling, never a dependency).
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

from agentcut import __version__
from agentcut.otio import export_otio, project_to_otio
from agentcut.server import TOOLS, call_tool

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(BASE, "data", "test_media")
CLIP1 = os.path.join(MEDIA, "clip1.mp4")   # 5.0s video
CLIP2 = os.path.join(MEDIA, "clip2.mp4")   # 5.0s video
POSTER = os.path.join(MEDIA, "poster.png")

OTIO_AVAILABLE = importlib.util.find_spec("opentimelineio") is not None


def _project(clips, fps=24, chapters=None, audio=None, **extra):
    p = {"id": "otio-test", "version": 1,
         "output": {"width": 360, "height": 640, "fps": fps},
         "clips": clips}
    if chapters is not None:
        p["chapters"] = chapters
    if audio is not None:
        p["audio"] = audio
    p.update(extra)
    return p


def _v1(timeline):
    return timeline["tracks"]["children"][0]


def _v1_clips(timeline):
    return [c for c in _v1(timeline)["children"]
            if c["OTIO_SCHEMA"] == "Clip.2"]


def _v1_transitions(timeline):
    return [c for c in _v1(timeline)["children"]
            if c["OTIO_SCHEMA"] == "Transition.1"]


class TestOtioExporter(unittest.TestCase):

    def test_version_is_1_63_0(self):
        self.assertEqual(__version__, "1.63.0")
        with open(os.path.join(BASE, "VERSION")) as f:
            self.assertEqual(f.read().strip(), "1.63.0")

    def test_tool_count_is_34_and_export_otio_listed(self):
        names = [t["name"] for t in TOOLS]
        self.assertEqual(len(names), 34)
        self.assertIn("export_otio", names)

    def test_basic_example_clip_order_and_ranges(self):
        with open(os.path.join(BASE, "examples", "basic.json")) as fh:
            proj = json.load(fh)
        # basic.json uses relative media paths: run from the repo root.
        old = os.getcwd()
        try:
            os.chdir(BASE)
            timeline, info = project_to_otio(proj)
        finally:
            os.chdir(old)
        self.assertIsNotNone(timeline)
        self.assertEqual(info["total_seconds"], 8.0)
        self.assertEqual(info["total_frames"], 192)
        clips = _v1_clips(timeline)
        self.assertEqual([c["name"] for c in clips],
                         ["c1", "c2", "c3"])
        # c1: video trim 0..3 -> source [0, 72) at 24fps.
        c1 = clips[0]["source_range"]
        self.assertEqual(c1["start_time"]["value"], 0.0)
        self.assertEqual(c1["duration"]["value"], 72.0)
        # c2: image, 2s -> [0, 48).
        c2 = clips[1]["source_range"]
        self.assertEqual(c2["start_time"]["value"], 0.0)
        self.assertEqual(c2["duration"]["value"], 48.0)
        # c3: video trim 1..4 -> source [24, 96).
        c3 = clips[2]["source_range"]
        self.assertEqual(c3["start_time"]["value"], 24.0)
        self.assertEqual(c3["duration"]["value"], 72.0)

    def test_fps_conversion_30fps(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2.5},
            {"id": "c2", "kind": "image", "src": POSTER, "duration": 1.0},
        ], fps=30)
        timeline, info = project_to_otio(proj)
        self.assertIsNotNone(timeline)
        self.assertEqual(info["total_frames"], 105)  # 3.5s * 30
        clips = _v1_clips(timeline)
        self.assertEqual(clips[0]["source_range"]["duration"]["value"],
                         75.0)
        self.assertEqual(clips[1]["source_range"]["duration"]["value"],
                         30.0)
        self.assertEqual(clips[0]["source_range"]["duration"]["rate"],
                         30.0)

    def test_markers_from_chapters(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 5},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 5},
        ], chapters=[{"title": "Intro", "start": 0},
                     {"title": "Middle", "start": 5.0},
                     {"title": "End", "start": 9.0}])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["markers"], 3)
        markers = _v1(timeline)["markers"]
        self.assertEqual([m["name"] for m in markers],
                         ["Intro", "Middle", "End"])
        self.assertEqual(
            [m["marked_range"]["start_time"]["value"] for m in markers],
            [0.0, 120.0, 216.0])
        for m in markers:
            self.assertEqual(m["OTIO_SCHEMA"], "Marker.2")
            self.assertEqual(m["marked_range"]["duration"]["value"], 1.0)

    def test_crossfade_transition_timing_is_exact(self):
        # c1 4s, c2 4s with 1s crossfade: total 7s, cut at 3s.
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 4},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 4,
             "transition": "crossfade", "transition_duration": 1.0},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["total_frames"], 168)  # 7s * 24
        self.assertEqual(info["transitions"], 1)
        trs = _v1_transitions(timeline)
        self.assertEqual(len(trs), 1)
        self.assertEqual(trs[0]["transition_type"], "SMPTE_Dissolve")
        # AgentCut's xfade ends at the cut: in_offset 0, out 1s.
        self.assertEqual(trs[0]["in_offset"]["value"], 0.0)
        self.assertEqual(trs[0]["out_offset"]["value"], 24.0)
        # Outgoing clip shortened by the overlap: 4s - 1s = 3s.
        clips = _v1_clips(timeline)
        self.assertEqual(clips[0]["source_range"]["duration"]["value"],
                         72.0)
        self.assertEqual(clips[1]["source_range"]["duration"]["value"],
                         96.0)

    def test_wipe_substituted_by_dissolve_with_warning(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 4},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 4,
             "transition": "wipe_left", "transition_duration": 0.5},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["total_frames"], 180)  # 7.5s * 24
        trs = _v1_transitions(timeline)
        self.assertEqual(len(trs), 1)
        self.assertEqual(trs[0]["transition_type"], "SMPTE_Dissolve")
        self.assertEqual(trs[0]["out_offset"]["value"], 12.0)
        self.assertTrue(any("wipe_left" in w for w in info["warnings"]))

    def test_fade_and_custom_transitions_become_hard_cuts(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 2, "transition": "fade"},
            {"id": "c3", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2, "transition": "whip_pan",
             "transition_duration": 0.5},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(_v1_transitions(timeline), [])
        self.assertEqual(info["total_frames"], 144)  # 6s, no overlap
        self.assertTrue(any("fade" in w for w in info["warnings"]))
        self.assertTrue(any("whip_pan" in w for w in info["warnings"]))

    def test_track1_clips_become_v2_and_overlap_spills_to_v3(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 5},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 5},
            {"id": "o1", "kind": "image", "src": POSTER, "duration": 3,
             "track": 1, "start": 1.0},
            {"id": "o2", "kind": "image", "src": POSTER, "duration": 3,
             "track": 1, "start": 2.0},
            {"id": "o3", "kind": "image", "src": POSTER, "duration": 2,
             "track": 1, "start": 5.0},
        ])
        timeline, info = project_to_otio(proj)
        tracks = timeline["tracks"]["children"]
        self.assertEqual(info["tracks"], 3)
        self.assertEqual([t["name"] for t in tracks],
                         ["V1", "V2", "V3"])
        # V2: o1 (1-4s) then o3 (5-7s); V3: o2 (2-5s).
        v2 = [c["name"] for c in tracks[1]["children"]
              if c["OTIO_SCHEMA"] == "Clip.2"]
        v3 = [c["name"] for c in tracks[2]["children"]
              if c["OTIO_SCHEMA"] == "Clip.2"]
        self.assertEqual(v2, ["o1", "o3"])
        self.assertEqual(v3, ["o2"])
        # V2 starts with a 1s gap (24 frames).
        self.assertEqual(tracks[1]["children"][0]["OTIO_SCHEMA"],
                         "Gap.1")
        self.assertEqual(
            tracks[1]["children"][0]["source_range"]["duration"]
            ["value"], 24.0)

    def test_speed_maps_to_linear_time_warp(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 1, "trim_out": 3, "speed": 2.0},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["total_frames"], 24)  # 1s of track time
        clip = _v1_clips(timeline)[0]
        self.assertEqual(clip["source_range"]["start_time"]["value"],
                         24.0)
        self.assertEqual(clip["source_range"]["duration"]["value"],
                         24.0)
        self.assertEqual(len(clip["effects"]), 1)
        self.assertEqual(clip["effects"][0]["OTIO_SCHEMA"],
                         "LinearTimeWarp.1")
        self.assertEqual(clip["effects"][0]["time_scalar"], 2.0)

    def test_freeze_maps_to_freeze_frame(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 2, "freeze_at": 1.0},
        ])
        timeline, _info = project_to_otio(proj)
        clip = _v1_clips(timeline)[0]
        self.assertEqual(clip["source_range"]["start_time"]["value"],
                         24.0)
        self.assertEqual(clip["source_range"]["duration"]["value"],
                         48.0)
        self.assertEqual(clip["effects"][0]["OTIO_SCHEMA"],
                         "FreezeFrame.1")

    def test_loop_count_becomes_consecutive_clips(self):
        proj = _project([
            {"id": "a", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2, "loop_count": 3},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["total_frames"], 144)  # 6s
        clips = _v1_clips(timeline)
        self.assertEqual([c["name"] for c in clips],
                         ["a#loop1", "a#loop2", "a#loop3"])
        for c in clips:
            self.assertEqual(c["source_range"]["duration"]["value"],
                             48.0)

    def test_speed_curve_splits_into_warped_segments(self):
        proj = _project([
            {"id": "b", "kind": "video", "src": CLIP2,
             "trim_in": 0, "trim_out": 4,
             "speed_curve": [{"start": 0, "end": 2, "speed": 2.0},
                             {"start": 2, "end": 4, "speed": 0.5}]},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(info["total_frames"], 120)  # 1s + 4s
        clips = _v1_clips(timeline)
        self.assertEqual([c["name"] for c in clips],
                         ["b#curve1", "b#curve2"])
        self.assertEqual(clips[0]["source_range"]["duration"]["value"],
                         24.0)
        self.assertEqual(clips[0]["effects"][0]["time_scalar"], 2.0)
        self.assertEqual(clips[1]["source_range"]["start_time"]["value"],
                         48.0)
        self.assertEqual(clips[1]["source_range"]["duration"]["value"],
                         96.0)
        self.assertEqual(clips[1]["effects"][0]["time_scalar"], 0.5)

    def test_reverse_exports_forward_with_warning(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2, "reverse": True},
        ])
        timeline, info = project_to_otio(proj)
        self.assertEqual(len(_v1_clips(timeline)), 1)
        self.assertTrue(any("reversed" in w for w in info["warnings"]))

    def test_audio_track_export(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 5},
        ], audio=[{"kind": "music", "src": CLIP2, "volume": 0.3}])
        timeline, info = project_to_otio(proj)
        tracks = timeline["tracks"]["children"]
        audio = [t for t in tracks if t["kind"] == "Audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["name"], "A1 (music)")
        aclip = audio[0]["children"][0]
        self.assertEqual(aclip["OTIO_SCHEMA"], "Clip.2")
        # clip2 is 5s, timeline is 5s: full 120 frames.
        self.assertEqual(aclip["source_range"]["duration"]["value"],
                         120.0)

    def test_relative_media_paths_preserved(self):
        proj = {"id": "rel", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": [{"id": "c1", "kind": "video",
                           "src": "data/test_media/clip1.mp4",
                           "trim_in": 0, "trim_out": 2}]}
        old = os.getcwd()
        try:
            os.chdir(BASE)
            timeline, _info = project_to_otio(proj)
        finally:
            os.chdir(old)
        clip = _v1_clips(timeline)[0]
        ref = clip["media_references"]["DEFAULT_MEDIA"]
        self.assertEqual(ref["target_url"],
                         "data/test_media/clip1.mp4")
        self.assertFalse(os.path.isabs(ref["target_url"]))

    def test_not_carried_features_warned(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 3, "grade": "warm"},
        ], texts=[{"text": "Hi", "start": 0, "end": 1, "x": "center",
                   "y": 80, "size": 36}],
            captions=[{"text": "Hello", "start": 0, "end": 1}])
        _timeline, info = project_to_otio(proj)
        joined = " ".join(info["warnings"])
        self.assertIn("text overlays", joined)
        self.assertIn("captions", joined)
        self.assertIn("graded", joined)

    def test_empty_project_error(self):
        proj = {"id": "empty", "version": 1,
                "output": {"width": 360, "height": 640, "fps": 24},
                "clips": []}
        timeline, info = project_to_otio(proj)
        self.assertIsNone(timeline)
        self.assertEqual(info["code"], "INVALID_PROJECT")

    def test_invalid_project_error(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 99},
        ])
        timeline, info = project_to_otio(proj)
        self.assertIsNone(timeline)
        self.assertEqual(info["code"], "INVALID_PROJECT")
        self.assertTrue(info["errors"])

    def test_non_dict_project_error(self):
        timeline, info = project_to_otio("not a project")
        self.assertIsNone(timeline)
        self.assertEqual(info["code"], "INVALID_SCHEMA")

    def test_export_writes_valid_json_file(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2},
        ])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "t.otio")
            ok, info = export_otio(proj, out)
            self.assertTrue(ok)
            self.assertEqual(info["path"], out)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["OTIO_SCHEMA"], "Timeline.1")
            self.assertEqual(data["tracks"]["OTIO_SCHEMA"], "Stack.1")

    def test_mcp_tool_export_otio(self):
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2},
        ])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "mcp.otio")
            result = call_tool("export_otio",
                               {"project": proj, "output": out})
            payload = json.loads(result["content"][0]["text"])
            self.assertEqual(payload["path"], out)
            self.assertEqual(payload["total_frames"], 48)
            self.assertTrue(os.path.isfile(out))

    def test_mcp_tool_needs_id_or_project(self):
        result = call_tool("export_otio", {})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["code"], "INVALID_SCHEMA")

    def test_mcp_tool_unknown_id(self):
        result = call_tool("export_otio", {"id": "no-such-project"})
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["code"], "PROJECT_NOT_FOUND")

    def test_cli_otio_command(self):
        from agentcut.__main__ import main as cli_main
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 2},
        ])
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "p.json")
            out = os.path.join(d, "cli.otio")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(proj, fh)
            self.assertEqual(cli_main(["otio", src, out]), 0)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["OTIO_SCHEMA"], "Timeline.1")

    @unittest.skipUnless(OTIO_AVAILABLE,
                         "opentimelineio not installed (verification "
                         "tooling only; never a dependency)")
    def test_round_trip_through_real_otio_library(self):
        import opentimelineio as otio
        proj = _project([
            {"id": "c1", "kind": "video", "src": CLIP1,
             "trim_in": 0, "trim_out": 4},
            {"id": "c2", "kind": "video", "src": CLIP2,
             "trim_in": 0.5, "trim_out": 4.5,
             "transition": "crossfade", "transition_duration": 1.0},
            {"id": "c3", "kind": "image", "src": POSTER, "duration": 3},
        ], chapters=[{"title": "Intro", "start": 0},
                     {"title": "Second", "start": 6.0}])
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "rt.otio")
            ok, _info = export_otio(proj, out)
            self.assertTrue(ok)
            tl = otio.adapters.read_from_file(out)
        # Total: 4 + 4 - 1 + 3 = 10s = 240 frames at 24fps.
        self.assertEqual(tl.duration().value, 240.0)
        track = tl.tracks[0]
        clips = [c for c in track
                 if isinstance(c, otio.schema.Clip)]
        self.assertEqual([c.name for c in clips],
                         ["c1", "c2", "c3"])
        self.assertEqual(
            [c.source_range.duration.value for c in clips],
            [72.0, 96.0, 72.0])
        markers = list(track.markers)
        self.assertEqual([m.name for m in markers],
                         ["Intro", "Second"])
        self.assertEqual(
            [m.marked_range.start_time.value for m in markers],
            [0.0, 144.0])


if __name__ == "__main__":
    unittest.main()
