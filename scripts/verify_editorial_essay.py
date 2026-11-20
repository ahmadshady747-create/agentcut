#!/usr/bin/env python3
"""End-to-end verification of the AgentCut 1.64.0 editorial pipeline.

Real assets only — no red circle, no 180 Hz sine tone. The script:

  1. Builds a 4-scene bundle with AgentMedia prepare_scene_bundle
     (voice_mode fast_draft: real Arabic speech via Edge/Google TTS,
     Whisper word timings, style_mode editorial: visual beats
     2.5-4.0 s with one illustration per beat, NO foreground
     cutout).
  2. Renders it once with produce_explainer at 720x1280, style
     editorial, from the bundle:
       - editorial_essay_test.mp4 (the deliverable)
  3. Writes a contact sheet + one frame per beat for the eye-check.
  4. Asserts, exiting non-zero on ANY failure:
       (a) the caption pill is present at the midpoint of EVERY
           caption cue — TOP-EDGE SCAN: the pill is black at 50%
           opacity with 12px padding, so its top edge is a sharp
           ~2x luminance step over ~2px at a known place (12px
           above the first text row). The check scans the 20 rows
           above the text for the sharpest 5-row step inside the
           text's horizontal extent (non-white pixels only, so
           the text strokes cannot fake or hide it). A pill reads
           0.44-0.56, a pill-free band 0.90-0.97 (measured on the
           run-4 frames + no-pill control bands). The earlier
           centre-vs-margins method FAILED on run 4's scene_03
           (ratio 0.87 with the pill visibly present): the
           background there is a dark wall left, a light strip
           right and a desk under the pill, so the margins sample
           different content than the centre. Black-background
           escape hatch as in 1.63.0.
       (b) real camera motion in EVERY beat: MAPD between two
           frames inside the beat (0.3 s in from each end, clear
           of the 0.25 s crossfades) > 1.0.
       (c) NO header and NO floating foreground: structural — no
           text in the top 18% of the frame, no text spanning the
           whole video, the essay title in no text item, no
           comp-subject overlay, no fg_src in any manifest scene
           — plus the beat frames for the eye-check.
       (d) the highlight card: structural — the scene-3 overlay
           lasts exactly 2.8 s and starts at the spoken trigger
           word ("سبعين" in the word_timings) or, when Whisper
           does not transcribe the word, mid-scene with the
           render warning; the card texts are inset by the
           0.25 s / 0.20 s fades. Card-on / card-off frames are
           saved for the eye-check.
       (e) the audio is real human speech, not a constant sine:
           RMS varies across 0.1 s windows (pauses exist) and the
           zero-crossing rate varies (same bars as 1.63.0).

Usage (run with the AgentMedia venv — faster-whisper lives
there, not in the system Python; AgentCut itself is stdlib
only, so it imports fine under the venv):
    ~/workspace/agentmedia/.venv/bin/python \
        scripts/verify_editorial_essay.py [--bundle-dir DIR]
        [--skip-render]
  --bundle-dir DIR  reuse an existing bundle manifest instead of
                    building a new one (faster re-runs).
  --skip-render     reuse the render in data/outputs/ and only
                    re-run the frame checks (structural checks
                    need the built project, so they are skipped).

Requires AgentMedia next to this repo (../agentmedia) or the
AGENTMEDIA_PATH environment variable. Stdlib + FFmpeg only.
"""

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from array import array
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

AGENTMEDIA = Path(os.environ.get(
    "AGENTMEDIA_PATH", str(REPO_ROOT.parent / "agentmedia")))
sys.path.insert(0, str(AGENTMEDIA / "src"))

OUT_DIR = REPO_ROOT / "data" / "outputs"
PROJECT_SLUG = "editorial_essay"

ESSAY_MP4 = OUT_DIR / "editorial_essay_test.mp4"
CONTACT_SHEET = OUT_DIR / "editorial_contact_sheet.png"
BEAT_FRAMES_DIR = OUT_DIR / "editorial_beat_frames"
REPORT_JSON = OUT_DIR / "verify_editorial_essay_report.json"

# Check thresholds (chosen from measured values, with margin).
# The pill darkens multiplicatively (black at ~50%), so its top
# edge reads ~0.5x (measured 0.44-0.56 on the run-4 cues) while
# a pill-free band reads 0.90-0.97 (no sharp step). Bar 0.80
# leaves margin both ways. MAPD 1.0 per beat: beats are 2.5-4 s
# and the camera moves the whole beat (zoom 1.0->1.18 or a full
# pan), so the accumulated change dwarfs the frozen baseline
# (~0.0-0.1).
PILL_RATIO = 0.80          # sharpest top-edge step (below/above)
PILL_BG_MIN = 8.0          # background above the edge below this:
                           # bg is black, the pill is unmeasurable
                           # (not a failure)
MAPD_BEAT_MIN = 1.0        # mean absolute pixel difference
RMS_CV_MIN = 0.25          # speech RMS varies; a sine's is ~0
QUIET_WINDOWS_MIN = 2      # 0.1 s windows near silence (pauses)
ZCR_STD_MIN = 50.0         # zero-crossing rate varies (per second)
CARD_DURATION = 2.8        # highlight card window (seconds)
CARD_TRIGGER = "سبعين"      # scene 3 trigger word
CARD_SCENE = "scene_03"


# ---------------------------------------------------------------- essay
# The 4 sentences and 8 shot prompts below are AHMAD'S WORDS from
# his 2026-11-17 brief, verbatim — they are the content under
# test, not filler. DO NOT INVENT OR PARAPHRASE THEM.
# Filled 2026-11-17 from Ahmad's brief, verbatim.
SCENES = [
    {
        "scene_id": "scene_01",
        "title": "الافتتاحية",
        "narration_text": (
            "نحن نعيش في عصرٍ يقدّس الحركة المستمرة، لكننا نادراً "
            "ما نسأل أنفسنا: إلى أين نركض؟"),
        "bg_query_or_prompt": (
            "A lone man standing still in the middle of a crowded "
            "blurred train station during rush hour, moody beam of "
            "light"),
        "beat_prompts": [
            "A lone man standing still in the middle of a crowded "
            "blurred train station during rush hour, moody beam of "
            "light",
            "Close up of an antique brass compass resting on a dark "
            "wooden desk in dim warm light",
        ],
        "visual_source": "illustration",
    },
    {
        "scene_id": "scene_02",
        "title": "التشخيص",
        "narration_text": (
            "الجدول المزدحم بالاجتماعات والمهام الصغيرة يمنحنا "
            "شعوراً زائفاً بالإنجاز، بينما تبقى أهدافنا الكبرى "
            "مؤجلةً منذ سنوات."),
        "bg_query_or_prompt": (
            "Overhead shot of a cluttered desk with scattered "
            "notebooks, coffee cup, and glowing laptop screen late "
            "at night"),
        "beat_prompts": [
            "Overhead shot of a cluttered desk with scattered "
            "notebooks, coffee cup, and glowing laptop screen late "
            "at night",
            "A vintage hourglass with golden sand slowly falling in "
            "dramatic side lighting against dark background",
        ],
        "visual_source": "illustration",
    },
    {
        "scene_id": "scene_03",
        "title": "البيانات والعمق",
        "narration_text": (
            "تشير الدراسات إلى أن أكثر من سبعين بالمئة من وقتنا "
            "اليومي يضيع في الاستجابة للضجيج، لا في صناعة القيمة."),
        "bg_query_or_prompt": (
            "Abstract visualization of glowing tangled threads "
            "unraveling into a single straight golden line in "
            "darkness"),
        "beat_prompts": [
            "Abstract visualization of glowing tangled threads "
            "unraveling into a single straight golden line in "
            "darkness",
            "A quiet library window overlooking rain falling on city "
            "lights at dusk, contemplative atmosphere",
        ],
        "visual_source": "illustration",
        "highlight_card": {
            "title": "70% من الوقت",
            "subtitle": "يُستهلك في ردّ الفعل لا في البناء العميق",
            "trigger_word": "سبعين",
        },
    },
    {
        "scene_id": "scene_04",
        "title": "الخاتمة",
        "narration_text": (
            "الهدوء ليس كسلاً؛ إنه المساحة الوحيدة التي تستطيع "
            "فيها أن ترى الطريق بوضوح قبل أن تخطو."),
        "bg_query_or_prompt": (
            "Minimalist silhouette of a thinker looking out from a "
            "dark archway toward a calm horizon at dawn"),
        "beat_prompts": [
            "Minimalist silhouette of a thinker looking out from a "
            "dark archway toward a calm horizon at dawn",
            "Single warm lamp illuminating an open blank journal on "
            "a clean dark table, peaceful atmosphere",
        ],
        "visual_source": "illustration",
    },
]


# ---------------------------------------------------------------- helpers

def log(msg):
    print(f"[verify] {msg}", flush=True)


def run_ffmpeg(args, timeout=120):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error"] + args,
        capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {' '.join(args)}\n"
            f"{proc.stderr.decode('utf-8', 'replace')[:500]}")
    return proc.stdout


def grab_frame(video, t, width, height):
    """One frame as raw rgb24 bytes, scaled to width x height."""
    return run_ffmpeg([
        "-ss", str(t), "-i", str(video),
        "-vf", f"scale={width}:{height}",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])


def save_frame(video, t, path, width=360, height=640):
    """One frame as a PNG file (for the eye-check)."""
    run_ffmpeg([
        "-ss", str(t), "-i", str(video),
        "-vf", f"scale={width}:{height}",
        "-frames:v", "1", str(path)])


def mapd(f1, f2):
    """Mean absolute pixel difference between two raw frames."""
    return sum(abs(a - b) for a, b in zip(f1, f2)) / len(f1)


def audio_stats(video):
    """Per-0.1s-window RMS + zero-crossing stats for the audio track."""
    raw = run_ffmpeg([
        "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
        "-f", "f32le", "-"], timeout=180)
    samples = array("f")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    win = 1600  # 0.1 s at 16 kHz
    n_win = len(samples) // win
    if n_win < 20:
        raise RuntimeError(
            f"audio too short: {len(samples) / 16000:.2f}s")
    rms_list, zcr_list = [], []
    for i in range(n_win):
        w = samples[i * win:(i + 1) * win]
        rms = math.sqrt(sum(v * v for v in w) / len(w))
        rms_list.append(rms)
        crossings = sum(
            1 for a, b in zip(w, w[1:]) if (a < 0) != (b < 0))
        zcr_list.append(crossings / 0.1)  # per second
    mean_rms = statistics.fmean(rms_list)
    rms_cv = (statistics.pstdev(rms_list) / mean_rms) if mean_rms > 0 else 0.0
    quiet = sum(1 for r in rms_list if r < 0.25 * mean_rms)
    return {
        "duration_sec": round(len(samples) / 16000, 2),
        "windows": n_win,
        "mean_rms": round(mean_rms, 4),
        "peak_rms": round(max(rms_list), 4),
        "rms_cv": round(rms_cv, 3),
        "quiet_windows": quiet,
        "zcr_std": round(statistics.pstdev(zcr_list), 1),
        "zcr_mean": round(statistics.fmean(zcr_list), 1),
    }


def parse_srt(path):
    """Parse an SRT file into [{start, end, text}] (scene-relative)."""
    from agentcut.srt import parse_srt_text
    return parse_srt_text(Path(path).read_text(encoding="utf-8"))


def caption_text_box(frame, width, height):
    """(top, bottom, x0, x1) of the white caption text in the
    lower part of the frame, or None when no caption is visible.
    The caption is the only white text down there (there is no
    header in editorial, and the highlight card sits higher)."""
    rows, xs = [], []
    for y in range(int(0.75 * height), int(0.98 * height)):
        n = 0
        row = y * width * 3
        for x in range(0, width, 2):
            o = row + x * 3
            if frame[o] > 200 and frame[o + 1] > 200 \
                    and frame[o + 2] > 180:
                n += 1
                xs.append(x)
        if n > 5:
            rows.append(y)
    if not rows:
        return None
    return min(rows), max(rows), min(xs), max(xs)


def _band_median_nonwhite(frame, width, y0, y1, x0, x1):
    """Median luminance of the NON-WHITE pixels in a frame band
    (white text strokes are excluded, so they can neither fake
    nor hide a luminance step)."""
    vals = []
    for y in range(max(0, y0), y1 + 1):
        row = y * width * 3
        for x in range(int(x0), int(x1), 2):
            o = row + x * 3
            r, g, b = frame[o], frame[o + 1], frame[o + 2]
            if r > 200 and g > 200 and b > 180:
                continue
            vals.append(0.299 * r + 0.587 * g + 0.114 * b)
    return statistics.median(vals) if vals else 0.0


def _norm_word(w):
    """Same normalisation as render.py's trigger lookup."""
    return re.sub(r'[\u064B-\u065F\u0670]', '', str(w)).strip(
        ".,!?;:()[]\"' ")


# ---------------------------------------------------------------- stages

def build_bundle():
    from agentmedia.scene_bundle import prepare_scene_bundle
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        raise SystemExit(
            "faster-whisper is not installed in this Python — run "
            "the script with the AgentMedia venv:\n"
            "  ~/workspace/agentmedia/.venv/bin/python "
            "scripts/verify_editorial_essay.py")
    bundle_dir = AGENTMEDIA / "data" / "bundles" / PROJECT_SLUG
    if bundle_dir.exists():
        log(f"removing stale bundle {bundle_dir}")
        shutil.rmtree(bundle_dir)
    log("building bundle (fast_draft: real TTS + Whisper + "
        "editorial beats)...")
    report = prepare_scene_bundle(
        PROJECT_SLUG, SCENES, voice_mode="fast_draft",
        style_mode="editorial")
    manifest_path = bundle_dir / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest, manifest_path, report


def check_bundle(manifest):
    """The bundle must be fully real AND editorial: TTS ok,
    timings, one image per beat, NO foreground cutout."""
    problems = []
    scenes = {s["scene_id"]: s for s in manifest.get("scenes") or []}
    if manifest.get("style_mode") != "editorial":
        problems.append(
            f"bundle style_mode is {manifest.get('style_mode')!r}, "
            "not 'editorial'")
    for spec in SCENES:
        sid = spec["scene_id"]
        s = scenes.get(sid)
        if s is None:
            problems.append(f"{sid}: missing from manifest")
            continue
        if s.get("status") != "ok":
            problems.append(
                f"{sid}: status {s.get('status')!r} "
                f"(warnings: {s.get('warnings')})")
            continue
        engine = str(s.get("engine") or "")
        if engine == "sine" or not engine:
            problems.append(f"{sid}: no real TTS engine ({engine!r})")
        if not s.get("word_timings"):
            problems.append(f"{sid}: no Whisper word_timings")
        beats = s.get("beats") or []
        if not beats:
            problems.append(f"{sid}: no beats in manifest")
        for bi, beat in enumerate(beats):
            bg = beat.get("bg_src")
            if not bg or not os.path.isfile(bg):
                problems.append(
                    f"{sid} beat {bi + 1}: no real beat image")
            elif beat.get("bg_reused_from"):
                # A borrowed image means the shot does not change
                # at this beat — the proof is degraded. Worded
                # as "no real beat image" so the one-rebuild
                # logic below fires (a Pollinations 503 is
                # usually transient); a second failure fails
                # the run loudly instead of shipping the reuse.
                problems.append(
                    f"{sid} beat {bi + 1}: no real beat image "
                    f"(borrowed from {beat['bg_reused_from']} "
                    f"after a fetch failure)")
        if s.get("fg_src"):
            problems.append(
                f"{sid}: fg_src present in editorial mode "
                "(foreground must be off)")
        cues = parse_srt(s["captions_src"])
        if len(cues) < 1:
            problems.append(f"{sid}: no caption cues")
        log(f"{sid}: engine={engine} dur={s.get('duration_sec')}s "
            f"beats={len(beats)} cues={len(cues)} "
            f"words={len(s.get('word_timings') or [])} "
            f"fg={'YES (BAD)' if s.get('fg_src') else 'no'}")
    return problems


def make_brief(manifest):
    """One editorial brief from the bundle."""
    from agentmedia.scene_bundle import to_agentcut_brief
    return to_agentcut_brief(
        manifest, title="وهم الانشغال", style="editorial",
        output={"width": 720, "height": 1280, "fps": 30},
        chapters=False, motion="gentle")


def render(brief, output_path):
    from agentcut.render import produce_explainer
    log(f"rendering {output_path.name} (720x1280, editorial)...")
    ok, info = produce_explainer(brief, str(output_path))
    if not ok:
        raise RuntimeError(f"render failed: {info}")
    log(f"rendered {output_path.name}: {info['duration']:.2f}s, "
        f"{len(info['scenes'])} scenes")
    return info


# ---------------------------------------------------------------- checks

def check_pills(info, manifest):
    """(a) Pill present at the midpoint of EVERY caption cue.

    Top-edge scan (full-res frame): the pill is black at 50%
    opacity with 12px padding, so its top edge is a sharp ~2x
    luminance step 12px above the first text row. Scan the 20
    rows above the text for the sharpest 5-row step inside the
    central 70% of the text's horizontal extent (non-white
    pixels only). A present pill reads 0.44-0.56 with the edge
    at offset -12..-14; a pill-free band reads 0.90-0.97
    (measured on the run-4 frames + no-pill control bands).
    Black-background escape hatch: the background above the
    edge below PILL_BG_MIN means the pill is unmeasurable
    there — reported, not a failure.
    """
    scenes = {s["scene_id"]: s for s in manifest.get("scenes") or []}
    starts = {s["id"]: float(s["start"]) for s in info["scenes"]}
    results, failures = [], []
    for spec in SCENES:
        sid = spec["scene_id"]
        cues = parse_srt(scenes[sid]["captions_src"])
        for ci, cue in enumerate(cues):
            t = starts[sid] + (cue["start"] + cue["end"]) / 2
            frame = grab_frame(ESSAY_MP4, t, 720, 1280)
            box = caption_text_box(frame, 720, 1280)
            if box is None:
                failures.append(f"{sid} cue {ci + 1}: no caption "
                                f"text visible at t={t:.2f}")
                continue
            top, bottom, tx0, tx1 = box
            span = tx1 - tx0
            ix0 = tx0 + 0.15 * span
            ix1 = tx1 - 0.15 * span
            best_y, best_r, best_above = None, 1.0, 0.0
            for y in range(top - 20, top + 1):
                above = _band_median_nonwhite(
                    frame, 720, y - 5, y - 1, ix0, ix1)
                below = _band_median_nonwhite(
                    frame, 720, y, y + 4, ix0, ix1)
                r = (below / above) if above > 0 else 1.0
                if r < best_r:
                    best_r, best_y, best_above = r, y, above
            if best_above < PILL_BG_MIN:
                results.append({"cue": f"{sid} cue {ci + 1}",
                                "t": round(t, 2),
                                "text_rows": [top, bottom],
                                "edge_y": best_y,
                                "above": round(best_above, 1),
                                "ratio": None, "pass": True,
                                "note": "bg black, pill unmeasurable"})
                log(f"(a) {sid} cue {ci + 1} @t={t:.2f}: bg black "
                    f"(above {best_above:.1f}) — pill unmeasurable")
                continue
            ratio = round(best_r, 2)
            passed = ratio < PILL_RATIO
            results.append({"cue": f"{sid} cue {ci + 1}", "t": round(t, 2),
                            "text_rows": [top, bottom],
                            "edge_y": best_y,
                            "edge_offset": best_y - top,
                            "above": round(best_above, 1),
                            "ratio": ratio, "pass": passed})
            log(f"(a) {sid} cue {ci + 1} @t={t:.2f}: sharpest step "
                f"at y={best_y} (offset {best_y - top:+d}, "
                f"above {best_above:.1f}, ratio {ratio}) "
                f"-> {'PILL PRESENT' if passed else 'NO PILL'}")
            if not passed:
                failures.append(f"{sid} cue {ci + 1}: ratio {ratio} "
                                f"(edge y={best_y}, above "
                                f"{best_above:.1f})")
    if not results:
        failures.append("no caption cues found at all")
    return results, failures


def check_motion_per_beat(info, manifest):
    """(b) Real camera motion in EVERY beat: MAPD between two
    frames inside the beat, 0.3 s in from each end (clear of
    the 0.25 s crossfades at the beat edges)."""
    scenes = {s["scene_id"]: s for s in manifest.get("scenes") or []}
    starts = {s["id"]: float(s["start"]) for s in info["scenes"]}
    results, failures = [], []
    for spec in SCENES:
        sid = spec["scene_id"]
        for bi, beat in enumerate(scenes[sid].get("beats") or []):
            b0 = starts[sid] + float(beat["start"])
            b1 = starts[sid] + float(beat["end"])
            t1, t2 = b0 + 0.3, b1 - 0.3
            if t2 - t1 < 0.5:
                t1, t2 = b0 + 0.15, b1 - 0.15
            f1 = grab_frame(ESSAY_MP4, t1, 180, 320)
            f2 = grab_frame(ESSAY_MP4, t2, 180, 320)
            value = round(mapd(f1, f2), 3)
            passed = value > MAPD_BEAT_MIN
            results.append({"beat": f"{sid} beat {bi + 1}",
                            "t1": round(t1, 2), "t2": round(t2, 2),
                            "mapd": value, "pass": passed})
            log(f"(b) {sid} beat {bi + 1} ({t1:.2f} vs {t2:.2f}): "
                f"MAPD {value} (bar {MAPD_BEAT_MIN}) "
                f"-> {'MOTION' if passed else 'BELOW BAR'}")
            if not passed:
                failures.append(f"{sid} beat {bi + 1}: MAPD {value}")
    if not results:
        failures.append("no beats found at all")
    return results, failures


def check_no_header(info, manifest):
    """(c) No header, no floating foreground (structural)."""
    project = info.get("project") or {}
    total = float(info.get("duration") or 0)
    failures = []
    texts = project.get("texts") or []
    for t in texts:
        if float(t.get("y", 0)) < 0.18 * 1280:
            failures.append(
                f"text {t.get('text')!r} sits in the header zone "
                f"(y={t.get('y')})")
        if float(t.get("end", 0)) - float(t.get("start", 0)) >= total - 0.1:
            failures.append(
                f"text {t.get('text')!r} spans the whole video "
                "(a fixed title)")
        if "وهم الانشغال" in str(t.get("text") or ""):
            failures.append("the essay title appears as on-screen text")
    for ov in project.get("overlays") or []:
        if str(ov.get("id") or "").startswith("comp-subject"):
            failures.append(f"floating subject overlay {ov['id']!r}")
    for s in manifest.get("scenes") or []:
        if s.get("fg_src"):
            failures.append(
                f"{s['scene_id']}: fg_src present in editorial mode")
    log(f"(c) structural: {len(texts)} text items "
        f"(all below the header zone, none spanning the video), "
        f"{len(project.get('overlays') or [])} overlays "
        f"(no floating subject), no fg_src in the manifest")
    return {"texts": len(texts),
            "overlays": len(project.get("overlays") or [])}, failures


def check_highlight_card(info, manifest):
    """(d) The scene-3 highlight card: exactly 2.8 s, starting at
    the spoken trigger word (or mid-scene with the warning when
    Whisper does not transcribe it). Returns the card-on/off
    times for the eye-check frames."""
    scenes = {s["scene_id"]: s for s in manifest.get("scenes") or []}
    starts = {s["id"]: float(s["start"]) for s in info["scenes"]}
    s3 = scenes[CARD_SCENE]
    dur = float(s3.get("duration_sec") or 0)
    trigger_t = None
    for w in s3.get("word_timings") or []:
        ww = _norm_word(w.get("word", ""))
        if ww and (ww == _norm_word(CARD_TRIGGER)
                   or _norm_word(CARD_TRIGGER) in ww):
            trigger_t = float(w["start"])
            break
    if trigger_t is not None:
        expected = starts[CARD_SCENE] + trigger_t
        mode = f"trigger word {CARD_TRIGGER!r} at {trigger_t:.2f}s"
    else:
        expected = starts[CARD_SCENE] + max(0.0, dur / 2.0 - CARD_DURATION / 2.0)
        mode = "mid-scene fallback (trigger word not transcribed)"
    project = info.get("project") or {}
    cards = [o for o in project.get("overlays") or []
             if o.get("id") == f"ed-card-{CARD_SCENE}"]
    failures = []
    if len(cards) != 1:
        failures.append(
            f"expected exactly 1 highlight card overlay, found "
            f"{len(cards)}")
        return {"mode": mode}, failures, None, None
    card = cards[0]
    if abs(float(card["duration"]) - CARD_DURATION) > 0.01:
        failures.append(
            f"card duration {card['duration']}s, expected "
            f"{CARD_DURATION}s")
    if abs(float(card["start"]) - expected) > 0.05:
        failures.append(
            f"card start {card['start']}s, expected {expected:.2f}s "
            f"({mode})")
    if trigger_t is None and not any(
            "trigger word" in str(w) for w in info.get("warnings") or []):
        failures.append("card placed mid-scene but no trigger-word "
                        "warning in the render warnings")
    # Card texts inset by the 0.25 s / 0.20 s fades.
    title = next((t for t in project.get("texts") or []
                  if t.get("text") == "70% من الوقت"), None)
    if title is None:
        failures.append("card title text '70% من الوقت' not found")
    else:
        if abs(float(title["start"]) - (float(card["start"]) + 0.25)) > 0.05:
            failures.append(
                f"card title starts {title['start']}s, expected "
                f"{float(card['start']) + 0.25:.2f}s")
        if abs(float(title["end"]) - (float(card["start"])
                                      + float(card["duration"]) - 0.20)) > 0.05:
            failures.append(
                f"card title ends {title['end']}s, expected "
                f"{float(card['start']) + float(card['duration']) - 0.20:.2f}s")
    card_on = float(card["start"]) + CARD_DURATION / 2.0
    card_off = float(card["start"]) + float(card["duration"]) + 0.5
    log(f"(d) card: start {card['start']}s, duration "
        f"{card['duration']}s ({mode})")
    return {"mode": mode, "start": card["start"],
            "duration": card["duration"]}, failures, card_on, card_off


def check_audio():
    """(e) Real speech, not a constant sine wave."""
    stats = audio_stats(ESSAY_MP4)
    log(f"(e) audio: {stats['duration_sec']}s, mean_rms "
        f"{stats['mean_rms']}, peak {stats['peak_rms']}, "
        f"rms_cv {stats['rms_cv']}, quiet windows "
        f"{stats['quiet_windows']}, zcr std {stats['zcr_std']}/s")
    failures = []
    if stats["peak_rms"] < 0.01:
        failures.append(f"audio nearly silent (peak {stats['peak_rms']})")
    if stats["rms_cv"] < RMS_CV_MIN:
        failures.append(
            f"RMS barely varies (cv {stats['rms_cv']}) — "
            "looks like a constant tone, not speech")
    if stats["quiet_windows"] < QUIET_WINDOWS_MIN:
        failures.append(
            f"only {stats['quiet_windows']} quiet windows — "
            "speech has pauses, a sine does not")
    if stats["zcr_std"] < ZCR_STD_MIN:
        failures.append(
            f"zero-crossing rate nearly constant (std "
            f"{stats['zcr_std']}/s) — looks like a pure tone")
    return stats, failures


def save_beat_frames(info, manifest, card_on, card_off):
    """One frame per beat (midpoint) + card on/off frames, for
    the eye-check."""
    if BEAT_FRAMES_DIR.exists():
        shutil.rmtree(BEAT_FRAMES_DIR)
    BEAT_FRAMES_DIR.mkdir(parents=True)
    scenes = {s["scene_id"]: s for s in manifest.get("scenes") or []}
    starts = {s["id"]: float(s["start"]) for s in info["scenes"]}
    saved = []
    for spec in SCENES:
        sid = spec["scene_id"]
        for bi, beat in enumerate(scenes[sid].get("beats") or []):
            t = starts[sid] + (float(beat["start"])
                               + float(beat["end"])) / 2
            path = BEAT_FRAMES_DIR / f"{sid}_beat{bi + 1:02d}_t{t:.2f}.png"
            save_frame(ESSAY_MP4, t, path)
            saved.append(str(path))
    if card_on is not None:
        path = BEAT_FRAMES_DIR / f"card_on_t{card_on:.2f}.png"
        save_frame(ESSAY_MP4, card_on, path)
        saved.append(str(path))
    if card_off is not None:
        path = BEAT_FRAMES_DIR / f"card_off_t{card_off:.2f}.png"
        save_frame(ESSAY_MP4, card_off, path)
        saved.append(str(path))
    log(f"beat frames: {len(saved)} saved to {BEAT_FRAMES_DIR}")
    return saved


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Verify the AgentCut 1.64.0 editorial pipeline "
                    "with real assets (the 'وهم الانشغال' essay).")
    ap.add_argument("--bundle-dir", default=None,
                    help="reuse an existing bundle dir")
    ap.add_argument("--skip-render", action="store_true",
                    help="reuse the existing render, only run the checks")
    args = ap.parse_args()

    if not SCENES:
        print("VERIFY BLOCKED: the essay content (Ahmad's 4 sentences "
              "+ 8 shot prompts, verbatim from his brief) is not yet "
              "filled into the SCENES block of this script.")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = []
    report = {"project": PROJECT_SLUG, "checks": {}}

    # Stage 1 — bundle (real assets) or reuse.
    if args.bundle_dir:
        manifest_path = Path(args.bundle_dir) / "bundle_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        log(f"reusing bundle {manifest_path}")
        bundle_problems = check_bundle(manifest)
    else:
        manifest, manifest_path, _rep = build_bundle()
        bundle_problems = check_bundle(manifest)
        # Image fetches hit a free API (Pollinations) that
        # occasionally 503s; a missing beat image alone is worth
        # one rebuild before failing. TTS/Whisper/cue failures
        # are not retried — they are deterministic, not transient.
        if bundle_problems and all(
                "no real beat image" in p for p in bundle_problems):
            log("image fetch failed (transient?) — rebuilding the "
                "bundle once")
            manifest, manifest_path, _rep = build_bundle()
            bundle_problems = check_bundle(manifest)
    if bundle_problems:
        for p in bundle_problems:
            log(f"BUNDLE FAIL: {p}")
        print("\nVERIFY FAILED: bundle is not fully real/editorial")
        return 1
    report["bundle"] = str(manifest_path)
    report["engines"] = {s["scene_id"]: s.get("engine")
                         for s in manifest.get("scenes") or []}
    report["beats"] = {s["scene_id"]: len(s.get("beats") or [])
                       for s in manifest.get("scenes") or []}

    # Stage 2 — one render from the bundle.
    structural = not args.skip_render
    if args.skip_render:
        if not ESSAY_MP4.is_file():
            print("VERIFY FAILED: --skip-render but the render is missing")
            return 1
        # Scene starts from the manifest: cumulative durations.
        starts, acc = [], 0.0
        for s in manifest.get("scenes") or []:
            starts.append({"id": s["scene_id"], "start": round(acc, 3)})
            acc += float(s.get("duration_sec") or 0)
        info = {"scenes": starts, "project": {"texts": [],
                                              "overlays": []},
                "duration": round(acc, 3), "warnings": []}
        log("--skip-render: structural checks (c) and (d) "
            "unavailable, frame checks only")
    else:
        brief = make_brief(manifest)
        info = render(brief, ESSAY_MP4)
        from agentcut.render import render_contact_sheet
        ok, cs_info = render_contact_sheet(
            str(ESSAY_MP4), str(CONTACT_SHEET))
        if not ok:
            failures.append(f"contact sheet failed: {cs_info}")
        else:
            log(f"contact sheet: {CONTACT_SHEET} "
                f"({len(cs_info.get('frames', []))} frames)")

    report["video"] = str(ESSAY_MP4)
    report["contact_sheet"] = str(CONTACT_SHEET)
    report["duration_sec"] = round(
        float(info["scenes"][-1]["start"])
        + float(manifest["scenes"][-1]["duration_sec"]), 2)

    # Stage 3 — checks.
    pills, f = check_pills(info, manifest)
    report["checks"]["pill_all_cues"] = pills
    failures += [f"(a) {x}" for x in f]

    motion, f = check_motion_per_beat(info, manifest)
    report["checks"]["motion_per_beat"] = motion
    failures += [f"(b) {x}" for x in f]

    card_on = card_off = None
    if structural:
        header, f = check_no_header(info, manifest)
        report["checks"]["no_header"] = header
        failures += [f"(c) {x}" for x in f]

        card, f, card_on, card_off = check_highlight_card(info, manifest)
        report["checks"]["highlight_card"] = card
        failures += [f"(d) {x}" for x in f]
    else:
        report["checks"]["no_header"] = {"skipped": True}
        report["checks"]["highlight_card"] = {"skipped": True}
        # Card on/off times from the manifest trigger, for frames.
        scenes = {s["scene_id"]: s
                  for s in manifest.get("scenes") or []}
        starts = {s["id"]: float(s["start"]) for s in info["scenes"]}
        s3 = scenes[CARD_SCENE]
        dur = float(s3.get("duration_sec") or 0)
        trig = next((float(w["start"]) for w in s3.get("word_timings") or []
                     if _norm_word(w.get("word", "")) == _norm_word(CARD_TRIGGER)
                     or _norm_word(CARD_TRIGGER) in _norm_word(w.get("word", ""))),
                    None)
        base = (starts[CARD_SCENE] + trig if trig is not None
                else starts[CARD_SCENE] + max(0.0, dur / 2.0 - CARD_DURATION / 2.0))
        card_on, card_off = base + 1.4, base + CARD_DURATION + 0.5

    audio, f = check_audio()
    report["checks"]["audio"] = audio
    failures += [f"(e) {x}" for x in f]

    frames = save_beat_frames(info, manifest, card_on, card_off)
    report["beat_frames"] = frames

    REPORT_JSON.write_text(json.dumps(report, indent=2,
                                      ensure_ascii=False),
                           encoding="utf-8")

    print()
    print("=" * 60)
    if failures:
        print("VERIFY FAILED")
        for fail in failures:
            print(f"  FAIL: {fail}")
        print("=" * 60)
        return 1
    print("VERIFY PASSED — all checks green:")
    print(f"  (a) pill on all {len(pills)} cues")
    print(f"  (b) motion in all {len(motion)} beats "
          f"(MAPD min {min(m['mapd'] for m in motion)})")
    if structural:
        print("  (c) no header, no floating fg (structural)")
        print(f"  (d) highlight card: {report['checks']['highlight_card']}")
    print(f"  (e) real speech: rms_cv {audio['rms_cv']}, "
          f"{audio['quiet_windows']} quiet windows, "
          f"zcr std {audio['zcr_std']}/s")
    print(f"  video: {ESSAY_MP4}")
    print(f"  contact sheet: {CONTACT_SHEET}")
    print(f"  beat frames: {BEAT_FRAMES_DIR}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
