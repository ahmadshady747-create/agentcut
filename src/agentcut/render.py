"""FFmpeg rendering for AgentCut v1: sequential clips, text, audio.

Design note: clips are sequential, not free-form tracks, in v1. That is
deliberate — a sequential timeline is fully deterministic for an agent
to reason about, and every later track feature is an addition to this
core, not a replacement of it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from .srt import build_karaoke_ass, measure_text_width, wrap_subtitle_text
from .validate import (CUSTOM_TRANSITIONS, GRADE_PRESETS,
                       SFX_NAMES, TEXT_PRESETS, TEXT_STYLES,
                       XFADE_TRANSITIONS,
                       bundled_fonts_dir,
                       chunk_word_timings,
                       effective_ken_burns, find_bundled_font, find_font,
                       find_font_for_text,
                       probe_media, resolve_path, validate_project,
                       wrap_caption_lines)


# Text overlays use drawtext textfile=, not text=. A literal apostrophe,
# colon or percent in text= breaks FFmpeg's filter parser no matter how
# it is backslash-escaped (verified 2026-10-28: single and double
# backslash both failed); a text file carries the exact characters.
# Textfile alone does NOT save a literal percent: drawtext expands
# %{...} sequences in textfile content too, and a stray % makes the
# whole overlay render empty (verified 2026-10-28: a caption with
# "80%" rendered nothing, the same caption without % rendered fine,
# and %% did not help). Text overlays therefore also set
# expansion=none; the review timecode drawtext below keeps normal
# expansion because it genuinely needs %{pts:hms}.


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


def _diagram_card_rect(dg, width, height, dw):
    """Numeric display rect (bx, by, bw, bh) of a diagram's
    backdrop card (v1.45.0, R7, glassmorphism).

    The card image (canvas_w x canvas_h) is scaled to dw wide
    (scale=dw:-2, aspect preserved, height rounded even), and
    placed at the diagram's explicit x/y or its OVERLAY_POS.
    Resolves the position expression to numbers (the FFmpeg
    overlay_w/overlay_h in OVERLAY_POS are dw/dh here). The
    diagram card does not move with the camera (no camera terms
    are applied to diagrams), so a static rect is correct.
    """
    bw = int(dw)
    cw = float(dg.get("canvas_w") or 1)
    ch = float(dg.get("canvas_h") or 1)
    bh = _even(round(bw * ch / cw)) if cw > 0 else bw
    bh = max(2, int(bh))
    if "x" in dg and "y" in dg:
        return (int(dg["x"]), int(dg["y"]), bw, bh)
    pos = dg.get("position", "bottom_right")
    if pos == "top_left":
        return (20, 20, bw, bh)
    if pos == "top_center":
        return ((width - bw) // 2, 20, bw, bh)
    if pos == "top_right":
        return (width - bw - 20, 20, bw, bh)
    if pos == "center":
        return ((width - bw) // 2, (height - bh) // 2, bw, bh)
    if pos == "bottom_left":
        return (20, height - bh - 20, bw, bh)
    if pos == "bottom_center":
        return ((width - bw) // 2, height - bh - 20, bw, bh)
    return (width - bw - 20, height - bh - 20, bw, bh)


# --- Hardware encoder detection + smart errors + temp hygiene
# (architectural refactoring; defaults unchanged) ----------------

HW_ENCODERS_PREFERENCE = (
    "h264_nvenc",
    "h264_videotoolbox",
    "h264_qsv",
    "h264_amf",
    "h264_mf",
    "h264_vaapi",
)


def detect_hw_encoders():
    """H.264 hardware encoders this FFmpeg build offers, in
    preference order (nvenc > videotoolbox > vaapi). Listing alone
    does not prove an encoder runs (a driver may be missing), so
    callers that need certainty use encoder_smoke_test; render
    fallback covers the rest. Returns [] when FFmpeg is absent or
    lists nothing (the expected result on a CPU-only VM)."""
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=30)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    found = set()
    for line in proc.stdout.splitlines():
        for token in line.split():
            if token in HW_ENCODERS_PREFERENCE:
                found.add(token)
    return [name for name in HW_ENCODERS_PREFERENCE if name in found]


def encoder_smoke_test(codec, timeout=60):
    """True when FFmpeg can encode one null frame with `codec`.

    A fast, real probe: listing an encoder is not proof it runs.
    Used by tests and by operators; the render path itself does
    not smoke-test on every render (detection + libx264 fallback
    on failure is cheaper and covers the same cases)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "nullsrc=s=64x64:d=0.1", "-c:v", codec,
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return False
    return proc.returncode == 0


def choose_video_codec(preference="libx264"):
    """Resolve a video codec request: "libx264" stays libx264
    (the deterministic default), "auto" picks the best detected
    hardware encoder that actually encodes a probe frame
    (listing an encoder is not proof it runs — this build lists
    nvenc/vaapi with no GPU behind them), or libx264 when none
    works, and any other name passes through unchanged (the
    render fallback still applies if it fails at render time)."""
    if preference == "auto":
        for name in detect_hw_encoders():
            if encoder_smoke_test(name):
                return name
        return "libx264"
    return preference


_ERROR_HINTS = ("error", "failed", "invalid", "no such", "not found",
                "unknown", "cannot", "unable", "denied", "mismatch",
                "required", "missing", "unsupported", "could not")


def extract_ffmpeg_error(stderr_text, max_lines=25):
    """The actually-failing lines from FFmpeg stderr.

    FFmpeg logs are long and mostly progress chatter; the old code
    returned the last 2000 characters raw, which usually cut a
    message in half. This returns the error-bearing lines from the
    tail (they carry the filter/node address, e.g.
    "[Parsed_xfade_... @ 0x...]"), capped at max_lines, plus the
    very last line for context when it is not already included.
    Falls back to the raw tail when no line looks like an error.
    Pure string work: no FFmpeg call, safe on any input."""
    if not stderr_text:
        return "ffmpeg failed with no output"
    lines = [ln for ln in stderr_text.splitlines() if ln.strip()]
    if not lines:
        return "ffmpeg failed with no output"
    tail = lines[-80:]
    picked = [ln for ln in tail
              if any(hint in ln.lower() for hint in _ERROR_HINTS)]
    if not picked:
        picked = lines[-10:]
    elif lines[-1] not in picked:
        picked.append(lines[-1])
    return "\n".join(picked[-max_lines:])


_TEMP_DIR_REGISTRY = set()
_TEMP_HANDLERS_INSTALLED = False
_TEMP_PREFIXES = ("agentcut-", "agentcut_tmp_", ".agentcut-tmp-")


def register_temp_dir(path):
    """Track a temp dir for signal-time cleanup (best-effort)."""
    if path:
        _TEMP_DIR_REGISTRY.add(path)


def unregister_temp_dir(path):
    _TEMP_DIR_REGISTRY.discard(path)


def _temp_signal_handler(signum, _frame):
    """SIGINT/SIGTERM: remove this process's temp dirs, then
    restore the default disposition and re-raise the signal, so
    the process still dies by the signal it received — cleanup
    never swallows an interrupt."""
    for path in list(_TEMP_DIR_REGISTRY):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except (OSError, ValueError, AttributeError):
        pass


def install_signal_handlers():
    """Install the temp-cleanup signal handlers once, best-effort.

    Idempotent. When signals cannot be installed (not the main
    thread, or a platform without SIGTERM), this silently does
    nothing: the finally blocks in render_project still clean up
    on normal and error exits."""
    global _TEMP_HANDLERS_INSTALLED
    if _TEMP_HANDLERS_INSTALLED:
        return
    try:
        signal.signal(signal.SIGINT, _temp_signal_handler)
        signal.signal(signal.SIGTERM, _temp_signal_handler)
        _TEMP_HANDLERS_INSTALLED = True
    except (ValueError, OSError, AttributeError):
        pass


def cleanup_stale_temp_dirs(tmp_root=None, max_age_seconds=7200):
    """Remove AgentCut temp dirs/files older than max_age_seconds
    (default 2 hours) from the temp root.

    ONLY names starting with agentcut-, agentcut_tmp_ or
    .agentcut-tmp- are ever removed — the prefix allowlist is
    the safety property; nothing else in the temp directory is
    touched. Returns the list of removed paths. Fresh dirs (an
    in-flight render's working dirs) are always kept."""
    root = tmp_root or tempfile.gettempdir()
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    now = time.time()
    removed = []
    for name in entries:
        if not name.startswith(_TEMP_PREFIXES):
            continue
        path = os.path.join(root, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age < max_age_seconds:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
            removed.append(path)
        except OSError:
            continue
    return removed


# --- Built-in procedural SFX (v1.37.0, timing fixed v1.37.1,
# re-voiced v1.45.0, R3) ------------------------------------------
# Deterministic lavfi sources, generated at render time as lavfi
# inputs (not filter sources — see the SFX block in
# build_ffmpeg_args): no pre-generated files, no new dependencies.
# Each entry is a spec dict: kind "noise" (anoisesrc chain) or
# kind "sine" (aevalsrc expression, thud only), plus the duration
# in seconds. Noise entries carry a fixed seed (anoisesrc seed=-1
# is random; a fixed seed makes the render byte-deterministic —
# probed: the same chain twice gives identical md5) and a gain
# that normalises the chain to the old design level (the pop
# peaks at -18 dBFS at volume 0.127, -9 at 0.35, exactly as the
# sine pop did; probed per name in
# ~/workspace/agentcut-145-logs/probe_sfx_noise.py).
#
# v1.45.0, R3: the harsh high sines are gone. pop/whoosh are
# warm low-passed pink noise (whoosh 80-500 Hz with an afade
# swell/decay); click/ding/cash/chime are soft bandpassed
# white-noise ticks and shimmers (no more 1.5-3.5 kHz sine
# rings). The sub-thud stays a low sine BY DESIGN: a thud IS a
# low-frequency pulse (70 -> 45 Hz fall), and the harsh part was
# the high sines, not this. "cash" and "chime" are now DIFFERENT
# shimmers (2.6 kHz vs 3.2 kHz) instead of sharing one source.
# Envelope note: the prompt asks for an exponential afade, but
# the exponential curve decays too fast for this engine's own
# tests — the whoosh went silent 0.44s before its nominal end
# (silence-onset test) and the ding's mean fell to -36 dB
# (mean-volume test). A linear (tri) afade keeps the level up
# until ~dur (silence onsets within 0.02s of start+dur, probed)
# and the means 5-8 dB above the thresholds, so the fades are
# linear; the swell/decay SHAPE the prompt wants is kept.
#
# v1.37.1 timing: sine built-ins are NOT delayed with adelay.
# They are generated as full-timeline sources whose expression
# is gated to the scheduled window (see _gated_sfx_expression).
# Root cause of the 1.37.0 defect, pinned by frame-level
# inspection (ashowinfo) on this FFmpeg build: adelay's delay
# exists only as leading silence frames (audio at pts = start,
# silence at pts 0..start), and atrim after adelay DISCARDS
# those silence frames — after adelay+atrim only one NOPTS
# silence frame plus the audio frames (still stamped pts =
# start) reach amix, and amix mixes by arrival order, so the
# audio lands at position 0. With plain stills this usually
# happened not to matter (the silence frames survived often
# enough); with a Ken Burns graph it broke on most runs (7/8 in
# an isolated reproduction), and for file inputs it broke
# deterministically (12/12). Dropping atrim (and the apad/
# asetpts that only made sense with it) removes the hazard for
# file SFX, tracks and overlay audio too; for built-ins the gate
# removes the delay from timestamps altogether, so timing is
# carried by the samples themselves and cannot be re-based away.
# Noise built-ins DO use adelay (like file SFX): they are short
# sources (duration = the SFX length), and adelay alone — no
# atrim after it — preserves the leading silence (verified by
# the 1.37.1 ashowinfo work and by the SFX timing tests).
SFX_SOURCES = {
    # Warm fast burst: low-passed pink noise, gone in ~0.3s.
    "pop": {"kind": "noise", "color": "pink", "duration": 0.30, "seed": 11,
            "filters": ("highpass=f=80,lowpass=f=800,"
                        "afade=t=out:st=0:d=0.30:curve=tri"),
            "gain": 5.6},
    # Warm air sweep: pink noise 80-500 Hz, swelling in over
    # 0.25s and decaying out over 0.75s (exponential afade).
    "whoosh": {"kind": "noise", "color": "pink", "duration": 1.00, "seed": 23,
               "filters": ("highpass=f=80,lowpass=f=500,"
                           "afade=t=in:st=0:d=0.25:curve=tri,"
                           "afade=t=out:st=0.25:d=0.75:curve=tri"),
               "gain": 5.0},
    # Soft high tick: bandpassed white noise at 2.5 kHz.
    "click": {"kind": "noise", "color": "white", "duration": 0.12, "seed": 37,
              "filters": ("bandpass=f=2500:width=1,"
                          "afade=t=out:st=0:d=0.12:curve=tri"),
              "gain": 1.0},
    # Soft bell-ish shimmer: bandpassed white noise at 1.76 kHz.
    "ding": {"kind": "noise", "color": "white", "duration": 0.90, "seed": 41,
             "filters": ("bandpass=f=1760:width=2,"
                         "afade=t=out:st=0:d=0.90:curve=tri"),
             "gain": 1.5},
    # Soft shimmer: bandpassed white noise at 2.6 kHz.
    "cash": {"kind": "noise", "color": "white", "duration": 1.20, "seed": 53,
             "filters": ("bandpass=f=2600:width=1,"
                         "afade=t=out:st=0:d=1.20:curve=tri"),
             "gain": 1.0},
    # Soft shimmer, brighter than cash: 3.2 kHz.
    "chime": {"kind": "noise", "color": "white", "duration": 1.20, "seed": 67,
              "filters": ("bandpass=f=3200:width=1,"
                          "afade=t=out:st=0:d=1.20:curve=tri"),
              "gain": 1.0},
    # Deep pulse: a falling low sine (70 Hz -> ~45 Hz), ~0.7s.
    # The only sine left, by design (see the note above).
    "thud": {"kind": "sine", "duration": 0.70,
             "expr": ("sin(2*PI*(70*t-20*t*t))*exp(-6*t)"
                      "+0.5*sin(2*PI*55*t)*exp(-8*t)")},
}


def _gated_sfx_expression(expr, start, duration):
    """Full-timeline aevalsrc expression for one built-in SFX.

    The expression is zero outside [start, start+duration) and,
    inside the window, the original expression with its time
    variable re-based to (t - start), so the SFX sounds exactly
    as it did at t=0 in 1.37.0. aevalsrc's t is the timeline
    clock for a source that starts at 0, so the gate is
    deterministic and immune to any timestamp re-basing
    downstream: even if a filter shifted the stream's pts, the
    samples themselves are silent outside the window. The
    expression must be single-quoted in the aevalsrc option
    (aevalsrc='...') because its if() commas would otherwise be
    parsed as filter-option separators. Only standalone t is
    replaced (regex \\bt\\b): no SFX expression contains another
    t-bearing word, and 'exp'/'sin'/'PI' are untouched."""
    inner = re.sub(r"\bt\b", f"(t-{start!r})", expr)
    return (f"if(lt(t,{start!r}),0,"
            f"if(lt(t,{start + duration!r}),{inner},0))")


def _pitch_sfx_expression(expr, factor):
    """Scale the pitch of a built-in SFX expression by factor.

    Only frequency constants inside sin(2*PI*...) terms are
    scaled — numbers followed by *t while inside a sin call.
    Decay envelopes (exp(-18*t)), amplitudes (0.6*sin(...))
    and durations are untouched, so the burst keeps its shape
    and length and only its pitch moves. factor 1.0 returns
    the expression unchanged (the old graph, byte-identical).
    A tiny state machine, not a regex: the whoosh/thud chirps
    nest parens inside sin(2*PI*(...)), which a flat pattern
    cannot match reliably.
    """
    if factor == 1.0:
        return expr
    out = []
    i = 0
    n = len(expr)
    depth = 0
    sin_depth = None
    while i < n:
        if expr.startswith("sin(2*PI*", i):
            out.append("sin(2*PI*")
            i += len("sin(2*PI*")
            depth += 1
            sin_depth = depth
            continue
        ch = expr[i]
        if ch == "(":
            depth += 1
            out.append(ch)
            i += 1
            continue
        if ch == ")":
            if sin_depth is not None and depth == sin_depth:
                sin_depth = None
            depth -= 1
            out.append(ch)
            i += 1
            continue
        if sin_depth is not None and (ch.isdigit() or ch == "."):
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            if expr[j:j + 2] == "*t":
                out.append(repr(round(float(expr[i:j]) * factor, 3)))
                i = j
                continue
            out.append(expr[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# --- Arabic BiDi display transform (v1.33.0) -------------------------
# Verified fact about this FFmpeg build (libfribidi + libharfbuzz,
# 2026-10-28, frame inspection, not assumed): drawtext shapes Arabic
# letters correctly (they join) but chooses the paragraph direction
# from the first strong character. A line starting with a digit or a
# Latin run therefore renders LTR: "2 دولار" shows 2 on the left and
# دولار on the right, which an RTL reader reads as "دولار 2"; a
# trailing ، or ! lands on the far right instead of the far left.
# Pure-Arabic lines starting with an Arabic letter were already
# correct, which is why single words and Arabic-led sentences
# passed earlier checks while digit-led keywords failed.
#
# The fix is Unicode embedding controls, applied at render time
# only (stored project text stays in logical order):
#   RLE (U+202B) ... PDF (U+202C) forces an RTL paragraph, and an
#   RLM (U+200F) before a trailing punctuation run (!, ؟, ., ،,
#   %, ٪) pins that punctuation to the left end of the line.
# Frame evidence (2026-10-28, Noto Naskh Arabic, yellow on black):
#   plain "2 دولار" -> 2 left, دولار right (WRONG);
#   RLE "2 دولار"  -> دولار left, 2 right (CORRECT);
#   plain "80٪ هواء!" -> ! far right (WRONG);
#   RLE "80٪ هواء" + RLM + "!" -> ! far left, 80٪ far right (CORRECT);
#   plain sentence ending "،" -> ، far right (WRONG);
#   RLE sentence + RLM + "،" -> ، far left (CORRECT);
#   title "اقتصاد القرد 2" -> identical pixels plain vs RLE
#   (already correct; the controls are a safe no-op there).
#   Controls render as zero-width controls, never as boxes.
# The alternative that was TESTED AND REJECTED: programmatic token
# reversal. Feeding "دولار 2" for logical "2 دولار" renders
# byte-identical to the plain line (same PNG hash) — this build's
# shaper reorders directional runs itself, so reversing tokens is
# undone before painting. Token reversal must NOT be used here.
# Only lines containing an Arabic character (U+0600..U+06FF) are
# wrapped; Latin-only lines are returned byte-identical, so
# non-Arabic renders are pixel-identical to before. The review
# timecode drawtext is NOT passed through this function (it is a
# separate filter using %{pts:hms}, not a textfile overlay).
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_TRAILING_PUNCT_RE = re.compile(r"([!؟.،%٪]+)$")
_RLE = "\u202b"
_PDF = "\u202c"
_RLM = "\u200f"


def bidi_display_text(text):
    """Render form of one text/caption line (logical order in, display
    form out). Arabic lines get RLE...PDF with RLM before trailing
    punctuation; every other line is returned unchanged."""
    s = str(text)
    if "\n" in s:
        return "\n".join(bidi_display_text(line) for line in s.split("\n"))
    if not _ARABIC_RE.search(s):
        return s
    if s.startswith(_RLE) and s.endswith(_PDF):
        return s
    m = _TRAILING_PUNCT_RE.search(s)
    if m:
        core, punct = s[:m.start()], m.group(1)
        return _RLE + core + _RLM + punct + _PDF
    return _RLE + s + _PDF


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


def _mask_factor_expr(mask):
    """Mask value as a 0..1 expression in pixel coordinates X, Y.

    Coordinates are pixels in the frame the filter sees (output frame
    for clips, overlay frame for overlays). Feathering softens the
    edge linearly over the feather width.
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
        return f"({fx})*({fy})"
    cx, cy, r = int(mask["mask_x"]), int(mask["mask_y"]), int(mask["mask_r"])
    if f > 0:
        return f"clip(({r}+{f}-hypot(X-{cx},Y-{cy}))/{f},0,1)"
    return f"if(lt(hypot(X-{cx},Y-{cy}),{r}),1,0)"


def _mask_geq(mask):
    """geq filter that keeps only the mask region, softly if feathered.

    This SETS the alpha channel to the mask value: it does not
    multiply by any incoming alpha (this build's geq has no a(X,Y)
    function, verified in Loop 5). Mask + chroma-key intersection is
    therefore done elsewhere, via alphaextract (see _alpha_intersect
    usage in the clip and overlay chains), never via this filter.
    """
    return (f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='({_mask_factor_expr(mask)})*255'")


def _rounded_alpha_factor(radius):
    """0..1 alpha factor for a rounded rectangle (v1.36.0), as a
    geq expression in pixel coords (W,H = plane size). Each corner
    keeps full alpha inside its circle and feathers linearly over
    2px outside it, so corners are not staircased at full
    resolution. Used for overlay corner rounding (on the extracted
    alpha) and for rounded text-box backgrounds (on a colour
    source). The radius is clamped by validation to half the
    smaller side, so corner circles never overlap."""
    r = int(radius)

    def corner(cx, cy, in_x, in_y):
        dist = f"hypot(X-({cx}),Y-({cy}))"
        inside = f"({in_x})*({in_y})"
        return (f"((1-{inside})+{inside}"
                f"*clip(({r}+2-{dist})/2,0,1))")

    tl = corner(str(r), str(r), f"lt(X,{r})", f"lt(Y,{r})")
    tr = corner(f"W-{r}", str(r), f"gte(X,W-{r})", f"lt(Y,{r})")
    bl = corner(str(r), f"H-{r}", f"lt(X,{r})", f"gte(Y,H-{r})")
    br = corner(f"W-{r}", f"H-{r}", f"gte(X,W-{r})", f"gte(Y,H-{r})")
    return f"({tl})*({tr})*({bl})*({br})"


def _rounded_mask_png(bw, bh, radius, path):
    """Write a grayscale (L) PNG mask of a rounded rect.

    White (255) inside the rounded rect, black (0) outside,
    with a 2px feather at the edge (the same soft edge the
    geq rounded mask used). Used by the glass card
    (_emit_glass_card) via alphamerge — a pre-rendered mask
    is ~20x faster than the geq rounded mask it replaces
    (probe_glass_fast.py: 9.0s vs 198.2s for a 576x358 card).
    PIL (ImageDraw.rounded_rectangle) is used when available;
    otherwise a stdlib signed-distance loop (slower, same
    result). The mask is deterministic for (bw, bh, radius).
    """
    bw, bh, radius = int(bw), int(bh), int(radius)
    r = min(radius, bw // 2, bh // 2)
    try:
        from PIL import Image as _Im, ImageDraw as _Dr
        img = _Im.new("L", (bw, bh), 0)
        d = _Dr.Draw(img)
        d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=r,
                            fill=255)
        img.save(path)
        return path
    except Exception:
        pass
    # Stdlib fallback: signed-distance coverage per pixel
    # (same math as diagrams.rounded_card_rgba, grayscale).
    import struct as _st
    import zlib as _zl
    w, h = bw, bh
    cx, cy = w / 2.0, h / 2.0
    hx, hy = w / 2.0 - r, h / 2.0 - r
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        qy = abs(y + 0.5 - cy) - hy
        for x in range(w):
            qx = abs(x + 0.5 - cx) - hx
            ax, ay = (qx if qx > 0 else 0.0), (qy if qy > 0 else 0.0)
            d = math.sqrt(ax * ax + ay * ay) + min(max(qx, qy), 0.0) - r
            cov = 0.5 - d / 2.0
            cov = 0.0 if cov <= 0.0 else (1.0 if cov >= 1.0 else cov)
            raw.append(int(round(cov * 255.0)))

    def _chunk(typ, data):
        c = _st.pack(">I", len(data)) + typ + data
        return c + _st.pack(">I", _zl.crc32(typ + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", _st.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
           + _chunk(b"IDAT", _zl.compress(bytes(raw)))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def _glass_mask_path(bw, bh, radius, border=False):
    """Cached path to a rounded-rect mask PNG (v1.45.0, R7).

    Masks are deterministic for (bw, bh, radius, border) and
    cached under the system temp dir, so repeated renders (and
    repeated cards of the same size in one render) share one
    file. The border mask is (bw+4, bh+4) with radius+2 (the
    2px white rim around the card).
    """
    if border:
        bw, bh, radius = int(bw) + 4, int(bh) + 4, int(radius) + 2
        name = f"glass_bmask_{bw}x{bh}_r{radius}.png"
    else:
        bw, bh, radius = int(bw), int(bh), int(radius)
        name = f"glass_mask_{bw}x{bh}_r{radius}.png"
    d = os.path.join(tempfile.gettempdir(), "agentcut-glass-masks")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    if not os.path.isfile(path):
        _rounded_mask_png(bw, bh, radius, path)
    return path


def _emit_glass_card(filters, vcur, overlay_index, bx, by, bw, bh,
                     radius, fps, afmt, start, end, argv=None,
                     fade_start=None):
    """Append a glassmorphism backdrop-blur card (v1.45.0, R7).

    Crops the (bx,by,bw,bh) region from the current video,
    blurs it (boxblur 16:2 — the prompt's allowed alternative
    to gblur sigma=18, and ~20x faster), tints it dark
    (rgba(15,23,42,0.68)), masks it to a rounded rect
    (radius 20) via a pre-rendered PNG mask + alphamerge
    (NOT geq — the geq rounded mask on a large card was the
    render bottleneck, 198s vs 9s in probe_glass_fast.py),
    and adds a thin white border (rgba(255,255,255,0.22)).
    The border is 2px (the prompt's 1.5px is not
    representable in integer pixel coordinates; 2px is the
    nearest symmetric choice). Returns the new vcur. The card
    is composited UNDER the caller's content (the motion
    element / diagram card is overlaid after this returns),
    so the frosted background shows through the element's
    transparency. argv (the -i list) is needed to add the
    mask PNGs as inputs; when None the geq mask is used as
    a fallback (slow, but correct — used only by direct
    callers that do not go through build_ffmpeg_args).
    fade_start (v1.45.0, R12 fix): when given, the card fades
    out over 1.0s starting at that main-timeline time, in
    lockstep with the element/diagram it backs — otherwise
    the frosted card stays visible after its element fades
    (fix4: ~172 bright px at t=5.0). The tint chain runs on
    the main timeline (its source is the split video), so its
    fade uses fade_start; the border chain runs on a looped
    input timeline (setpts-reset), so its fade uses
    fade_start - start.
    """
    br = min(int(radius), bw // 2, bh // 2)
    gi = overlay_index
    # Fade strings (v1.45.0, R12 fix): the tint chain runs on
    # the main timeline, the border chain on a looped input
    # timeline — see the docstring.
    _fade_main = (f",fade=t=out:st={fade_start}:d=1.0:alpha=1"
                  if fade_start is not None else "")
    _fade_in = (f",fade=t=out:st={fade_start - start}:d=1.0:alpha=1"
                if fade_start is not None else "")
    # Split the current video: one branch continues, one is
    # the glass source. The split labels must be unique per
    # card (several glass cards can stack in one graph).
    filters.append(f"[{vcur}]split=2[{vcur}_b][vgs{gi}]")
    filters.append(
        f"[vgs{gi}]crop={bw}:{bh}:{bx}:{by},boxblur=16:2,"
        f"format=rgba[vgb{gi}]")
    filters.append(
        f"color=c=0x0f172a@0.68:s={bw}x{bh}:r={fps},"
        f"format=rgba[vtint{gi}]")
    filters.append(
        f"[vgb{gi}][vtint{gi}]overlay=0:0:format=rgb[vgt{gi}]")
    if argv is not None:
        # Fast path: pre-rendered PNG masks + alphamerge.
        _mp = _glass_mask_path(bw, bh, br, border=False)
        _midx = _input_count(argv)
        argv += ["-i", _mp]
        filters.append(
            f"[{_midx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
            f"trim=end={end - start},setpts=PTS-STARTPTS,"
            f"format=gray[vgmask{gi}]")
        filters.append(f"[vgt{gi}][vgmask{gi}]alphamerge{_fade_main}[vgm{gi}]")
        _bp = _glass_mask_path(bw, bh, br, border=True)
        _bidx = _input_count(argv)
        argv += ["-i", _bp]
        filters.append(
            f"color=c=white:s={bw + 4}x{bh + 4}:r={fps},"
            f"format=rgba[vgbrw{gi}]")
        filters.append(
            f"[{_bidx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
            f"trim=end={end - start},setpts=PTS-STARTPTS,"
            f"format=gray[vgbmask{gi}]")
        filters.append(
            f"[vgbrw{gi}][vgbmask{gi}]alphamerge,"
            f"colorchannelmixer=aa=0.22{_fade_in}[vgbr{gi}]")
    else:
        # Fallback (no argv): the geq rounded mask. Slow on
        # large cards, but correct; only used by direct
        # callers that bypass build_ffmpeg_args.
        filters.append(
            f"[vgt{gi}]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='{_rounded_alpha_factor(br)}*255'{_fade_main}[vgm{gi}]")
        filters.append(
            f"color=c=white@0.22:s={bw + 4}x{bh + 4}:r={fps},"
            f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='{_rounded_alpha_factor(br + 2)}*255*0.22'{_fade_in}[vgbr{gi}]")
    filters.append(
        f"[{vcur}_b][vgbr{gi}]overlay=x={bx - 2}:y={by - 2}:"
        f"eof_action=pass:enable='between(t,{start},{end})'[vgbd{gi}]")
    filters.append(
        f"[vgbd{gi}][vgm{gi}]overlay=x={bx}:y={by}:"
        f"eof_action=pass:enable='between(t,{start},{end})'[vglass{gi}]")
    return f"vglass{gi}"


def _pill_png_path(bw, bh, radius, color, opacity):
    """Cached path to a pre-rendered pill PNG (v1.46.0, R4).

    The pill is a static rounded-rect (colour + rounded alpha
    + opacity) — it never changes per frame — so rendering it
    once as an RGBA PNG and looping it as an input replaces
    the per-frame colour-source + geq that _emit_text_pill
    used to build. Measured (30s, 900f, one 198x91 pill):
    the per-frame geq costs ~7s while the looped PNG costs
    ~0.5s (the colour source + overlay alone cost ~0.6s, so
    the geq is the cost, not the overlay). Generated with
    FFmpeg itself using the SAME geq expression, so the PNG
    is pixel-identical to the per-frame version by
    construction. Returns None on generation failure (the
    caller then falls back to the per-frame geq).
    """
    br = min(int(radius), int(bw) // 2, int(bh) // 2)
    _c = "".join(ch for ch in str(color) if ch.isalnum())[:16]
    name = (f"pill_{int(bw)}x{int(bh)}_r{br}_{_c}_"
            f"o{float(opacity):.2f}.png")
    d = os.path.join(tempfile.gettempdir(), "agentcut-pill-masks")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    path = os.path.join(d, name)
    if os.path.isfile(path):
        return path
    vf = (f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
          f"a='{_rounded_alpha_factor(br)}*255*{float(opacity)}'")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"color=c={_ff_color(color)}:s={int(bw)}x{int(bh)}",
             "-vf", vf, "-frames:v", "1", path],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if proc.returncode != 0 or not os.path.isfile(path):
        return None
    return path


def _emit_text_pill(filters, vcur, overlay_index, bx, by, bw, bh,
                    radius, color, opacity, fps, afmt, start, end,
                    argv=None):
    """Append a rounded-rect text background (pill) overlay.

    A colour source masked by the rounded-rect geq factor,
    overlaid under the text. This is THE background mechanism
    for text/caption boxes since v1.45.0 (R5): the default
    sharp drawtext box is gone, and every boxed text gets a
    pill (radius 16 by default, the item's box_radius when
    set). With radius 0 the geq factor is 1, so the pill is
    a sharp rect — the sharp background is still available,
    through this mechanism, never through drawtext box=1.
    (bx, by) is the pill's top-left (already padded and
    clamped); (bw, bh) its size. Returns the new vcur.

    Since v1.46.0 (R4) the pill is a pre-rendered PNG
    (_pill_png_path) looped as an input when argv is given —
    the per-frame geq cost ~7s per pill per 30s, while the
    looped PNG costs ~0.5s. When argv is None (direct
    callers that bypass build_ffmpeg_args) or PNG generation
    fails, the per-frame geq is used as a fallback.
    """
    br = min(int(radius), bw // 2, bh // 2)
    _pp = _pill_png_path(bw, bh, br, color, opacity) if argv is not None else None
    if _pp is not None:
        _pidx = _input_count(argv)
        argv += ["-i", _pp]
        filters.append(
            f"[{_pidx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
            f"trim=end={end - start},setpts=PTS-STARTPTS,"
            f"format=rgba[box{overlay_index}]")
    else:
        filters.append(
            f"color=c={_ff_color(color)}:s={bw}x{bh}:r={fps},"
            f"format={afmt},geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='{_rounded_alpha_factor(br)}*255*{opacity}'[box{overlay_index}]")
    filters.append(
        f"[{vcur}][box{overlay_index}]overlay=x={bx}:y={by}:"
        f"eof_action=pass:enable='between(t,{start},{end})'[vbox{overlay_index}]")
    return f"vbox{overlay_index}"


def _ff_color(color):
    """FFmpeg colour literal for a validated colour: named colours
    pass through, #RRGGBB becomes 0xRRGGBB."""
    if isinstance(color, str) and color.startswith("#"):
        return "0x" + color[1:]
    return color


def _scale_part(fit, width, height, chain_scale_out=None):
    """Scale (and pad/crop) string for a clip at the output size.

    chain_scale_out is (out_range, out_color_matrix) or None:
    when given, the scale filter forces that output range /
    colorspace (segment renders use it so a segment's scale
    reproduces the direct graph's scale output exactly, instead
    of negotiating a different format in isolation — see
    render_project_cached). None keeps the old behaviour."""
    suffix = ""
    if chain_scale_out:
        suffix = (f":out_range={chain_scale_out[0]}"
                  f":out_color_matrix={chain_scale_out[1]}")
    if fit == "cover":
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase{suffix},"
                f"crop={width}:{height}")
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease{suffix},"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")


def _chain_scale_suffix(chain_scale_out):
    """Suffix for a clip-chain scale filter, or '' when unset."""
    if not chain_scale_out:
        return ""
    return (f":out_range={chain_scale_out[0]}"
            f":out_color_matrix={chain_scale_out[1]}")


def _blur_filter(fit):
    """Blur kernel for a blurred-background fit (delta).

    'blur' keeps the original gblur fill; 'blur_pad' uses a
    boxblur kernel (split + scale + boxblur + overlay). Both
    share the cover/contain composite and the -0.2 darkening.
    """
    if fit == "blur_pad":
        return "boxblur=lr=20:lp=3:cr=20:cp=3"
    return "gblur=sigma=20"


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


def _easing_factor(easing, u):
    """Easing curve f(u) for normalised segment time u in [0, 1].

    linear is the old behaviour. ease_in grows as u^2 (slow start,
    fast end), ease_out as 1-(1-u)^2 (fast start, slow end), and
    ease_in_out as 2u^2 below the midpoint and 1-2(1-u)^2 above it
    (slow at both ends, fast in the middle). spring is the
    underdamped response (k=180, c=14, m=1; ~15% overshoot,
    settled by u~0.7) — the same closed form as motion's
    spring_ease. cubic_bezier is the CSS-ease curve as a
    degree-4 polynomial fit, clamped to [0, 1] (the degree-5
    fit was rejected: not monotonic). All are plain FFmpeg
    expressions, evaluated per frame.
    """
    if easing == "spring":
        # R12 fix: u MUST be clamped to [0,1] before the
        # exponential. The callers pass u=(t-start)/dur, and
        # FFmpeg evaluates the fontsize/position expression
        # for t < start too (the if() guard only bounds the
        # upper end), where u is very negative and
        # exp(-7*u)=exp(+large) overflows — a late pop_in
        # (start=22.7) failed the whole render with
        # "fontsize overflow" at t=0 (u=-45.4, exp(317)).
        # Clamping keeps exp(-7*u) in (0,1] and the spring
        # in its designed [0, ~1.146] range.
        _uc = f"(min(max({u},0),1))"
        return (f"(1-exp(-7*{_uc})*(cos(11.4477*{_uc})"
                f"+0.6114*sin(11.4477*{_uc})))")
    if easing == "cubic_bezier":
        _p = (f"(4.214797*pow({u},4)-8.825972*pow({u},3)"
              f"+4.624145*pow({u},2)+1.034968*({u})-0.02652)")
        return f"(min(max({_p},0),1))"
    if easing == "ease_in":
        return f"({u})*({u})"
    if easing == "ease_out":
        return f"(1-(1-({u}))*(1-({u})))"
    if easing == "ease_in_out":
        return (f"if(lt({u},0.5),2*({u})*({u}),"
                f"1-2*(1-({u}))*(1-({u})))")
    if easing == "ease_out_back":
        # Overshoot ease (v1.44.0, F4b): f(0)=0, f(1)=1, peak
        # ~1.088 at u=0.5 — the pop_in entrance. Constants are
        # the standard c1=1.70158, c3=c1+1.
        return (f"(1+2.70158*pow(({u})-1,3)"
                f"+1.70158*pow(({u})-1,2))")
    return f"({u})"


def _interp_expr_eased(points, var):
    """Piecewise-eased expression for `var` over (time, value, easing)
    points, sorted by strictly increasing time.

    The easing for a segment is taken from the segment's END
    keyframe when that keyframe sets a non-linear easing, else
    from its START keyframe, else linear. So setting easing on
    either keyframe of a segment shapes that segment, and when
    both set a non-linear easing the later keyframe wins. Outside
    the range the first/last value holds, exactly like the
    linear version.
    """
    if len(points) == 1:
        return repr(float(points[0][1]))
    expr = repr(float(points[-1][1]))
    for i in range(len(points) - 2, -1, -1):
        t0, v0, e0 = points[i]
        t1, v1, e1 = points[i + 1]
        easing = e1 if e1 and e1 != "linear" else (e0 or "linear")
        u = f"(({var})-{t0!r})/({t1!r}-{t0!r})"
        factor = _easing_factor(easing, u)
        seg = f"{v0!r}+({v1!r}-{v0!r})*({factor})"
        expr = f"if(lt({var},{t1!r}),{seg},{expr})"
    return f"if(lt({var},{points[0][0]!r}),{points[0][1]!r},{expr})"


def _track_interp_expr(points, var, key):
    """FFmpeg eval expression for one linearly interpolated track
    coordinate at time `var` (v1.44.0, F2).

    points are sorted {t, x, y, w, h} dicts (the loader dedupes
    equal times, so no segment divides by zero). The time is
    clamped to [t0, t_last] with min/max, so the first/last
    point is HELD outside the track instead of extrapolating.
    Same nested-if shape as _interp_expr_eased, but strictly
    linear: a track is measured data, not a designed ease.
    """
    t0 = float(points[0]["t"])
    t1 = float(points[-1]["t"])
    vc = f"min(max({var},{t0!r}),{t1!r})"
    expr = repr(float(points[-1][key]))
    for i in range(len(points) - 2, -1, -1):
        ta = float(points[i]["t"]); va = float(points[i][key])
        tb = float(points[i + 1]["t"]); vb = float(points[i + 1][key])
        seg = f"{va!r}+({vb!r}-{va!r})*((({vc})-{ta!r})/({tb!r}-{ta!r}))"
        expr = f"if(lt({vc},{tb!r}),{seg},{expr})"
    return expr


def _track_data_for(item, info):
    """The validated track data dict for a text/caption item
    (v1.44.0, F2), looked up in info["data_files"] by the
    resolved file path. None when the item has no track or the
    data is missing (validation guarantees it is there).
    """
    track = item.get("track") or {}
    path = resolve_path(track.get("file", ""))
    entry = (info.get("data_files") or {}).get(path) or {}
    return entry.get("data")


def _track_overlay_expr(ov, info, width, height, ostart):
    """(x_expr, y_expr) for a track-following overlay (v1.44.0,
    F2). The overlay's TOP-LEFT corner sits on the tracked
    box's top-left corner, scaled from the track frame_size to
    the output size, plus offset_x/offset_y (output pixels).
    Sampled in the overlay's source time (t - start + trim_in).
    """
    data = (info.get("data_files") or {}).get(ov["track_file"], {}).get("data")
    pts = data["track"]
    fw = float(data["frame_size"]["width"])
    fh = float(data["frame_size"]["height"])
    sx = width / fw
    sy = height / fh
    # Offsets are element properties (see validate.py): the
    # shared data_files entry holds pure file data only.
    _off = ov.get("track_offset") or {}
    ox = float(_off.get("x", 0))
    oy = float(_off.get("y", 0))
    var = f"(t-{ostart!r}+{float(ov.get('trim_in', 0))!r})"
    x_expr = f"({_track_interp_expr(pts, var, 'x')})*{sx!r}+{ox!r}"
    y_expr = f"({_track_interp_expr(pts, var, 'y')})*{sy!r}+{oy!r}"
    return x_expr, y_expr


def _track_text_expr(item, info, width, height, start):
    """(x_expr, y_expr) for a track-following text/caption
    (v1.44.0, F2). The text CENTRES on the tracked box's centre
    (x + w/2, y + h/2), scaled from the track frame_size to the
    output size, plus offset_x/offset_y (output pixels); the
    drawtext text_w/text_h re-centre it. Sampled in the text's
    source time (t - start; reveal-expanded word items use the
    original item's start via _track_base_start).
    """
    data = _track_data_for(item, info)
    pts = data["track"]
    fw = float(data["frame_size"]["width"])
    fh = float(data["frame_size"]["height"])
    sx = width / fw
    sy = height / fh
    # Offsets are element properties: the text's own track
    # spec carries them (the shared entry holds file data only).
    _spec = item.get("track") or {}
    ox = float(_spec.get("offset_x", 0))
    oy = float(_spec.get("offset_y", 0))
    base = float(item.get("_track_base_start", start))
    var = f"(t-{base!r})"
    cx = (f"(({_track_interp_expr(pts, var, 'x')})"
          f"+({_track_interp_expr(pts, var, 'w')})/2)*{sx!r}+{ox!r}")
    cy = (f"(({_track_interp_expr(pts, var, 'y')})"
          f"+({_track_interp_expr(pts, var, 'h')})/2)*{sy!r}+{oy!r}")
    return f"({cx})-text_w/2", f"({cy})-text_h/2"


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


def _camera_interp_expr(keyframes, field, var_t):
    """Smoothstep-interpolated expression for one camera field.

    keyframes are validated {t, x, y, scale} dicts, var_t is an
    FFmpeg expression for clip-local seconds (on/fps for zoompan
    image clips, in/fps for video clips). Between keyframes the
    value eases with the same smoothstep the Ken Burns drift
    uses (slow start, faster middle, slow landing); outside the
    range the first/last value holds, so a path that starts
    after 0 holds its first framing until then."""
    if len(keyframes) == 1:
        return repr(float(keyframes[0][field]))
    expr = repr(float(keyframes[-1][field]))
    for i in range(len(keyframes) - 2, -1, -1):
        t0 = float(keyframes[i]["t"])
        v0 = float(keyframes[i][field])
        t1 = float(keyframes[i + 1]["t"])
        v1 = float(keyframes[i + 1][field])
        u = f"(({var_t})-{t0!r})/({t1!r}-{t0!r})"
        seg = f"{v0!r}+({v1!r}-{v0!r})*({u})*({u})*(3-2*({u}))"
        expr = f"if(lt({var_t},{t1!r}),{seg},{expr})"
    return (f"if(lt({var_t},{float(keyframes[0]['t'])!r}),"
            f"{float(keyframes[0][field])!r},{expr})")


def _camera_zoompan_filter(keyframes, var_t, width, height, d, fps):
    """The zoompan filter for a camera path (v1.40.0).

    The crop centre follows the interpolated (x, y) centre
    fractions and the crop size follows the interpolated scale.
    zoompan's own x/y expressions may reference zoom, so the
    centre is clamped to the zoom-dependent valid range
    [1/(2*zoom), 1-1/(2*zoom)] per axis: a keyframe at the frame
    edge (x=0 or 1) stays a valid crop at every zoom instead of
    running off the frame. Scales are validated 1.0..1.25, so
    the 1.37.2 comfort max holds by construction. width/height
    are zoompan's OUTPUT size: since v1.46.0 both image and
    video paths pass output size directly (the 4x pre-scale /
    2x zoompan of v1.41.0 Fix 4 was removed — measured ~4x
    slower for a sub-visible sharpness gain; see the camera
    branch in the clip chain)."""
    z = _camera_interp_expr(keyframes, "scale", var_t)
    fx = _camera_interp_expr(keyframes, "x", var_t)
    fy = _camera_interp_expr(keyframes, "y", var_t)
    x_expr = (f"(min(max({fx},1/(2*zoom)),1-1/(2*zoom)))*iw"
              f"-(iw/zoom)/2")
    y_expr = (f"(min(max({fy},1/(2*zoom)),1-1/(2*zoom)))*ih"
              f"-(ih/zoom)/2")
    return (f"zoompan=z='{z}':x='{x_expr}':y='{y_expr}':"
            f"d={d}:s={width}x{height}:fps={fps}")


def _treatment_has_shadow(treatment):
    """True when a treatment dict sets shadow > 0."""
    if not isinstance(treatment, dict):
        return False
    try:
        return float(treatment.get("shadow", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _treatment_mask_path(treatment, width, height):
    """Cached path to a shadow mask PNG (v1.46.0, R3).

    The mask is a black RGBA frame whose ALPHA is the shadow
    darkening (sh * f, where f is the smoothstepped
    distance-to-edge falloff): overlaying it on a video
    multiplies every pixel by (1 - sh*f), exactly as the
    per-frame shadow geq would, but the mask is rendered
    ONCE (1 frame, cached under the system temp dir) instead
    of evaluated per frame. Generated with FFmpeg itself, so
    the factor is exact by construction: the real shadow geq
    expression is applied to a white frame and the resulting
    brightness is converted to an alpha channel
    (a = 255 - r). Returns None when the treatment sets no
    shadow, or when generation fails (the call sites then
    fall back to the inline shadow geq, so the effect is
    never silently dropped).

    VIGNETTE is deliberately NOT in this mask: the vignette
    filter multiplies luma DIRECTLY (Y*f, including the
    limited-range black offset — measured: a white frame and
    a grey frame give the same corner factor ~0.235, so the
    filter does not scale above black), while a
    blend-toward-black overlay gives Y*f + 16*(1-f). No
    single alpha reproduces the direct multiply for all
    brightnesses, so a vignette mask is inexact by up to
    ~11 levels at vig=1.0 (mean 3.84) and ~5 at vig=0.4.
    The vignette therefore stays the built-in per-frame
    filter (see _treatment_filters). The shadow geq, by
    contrast, multiplies RGB (where black is 0), so the
    overlay reproduces it exactly (measured mean 0.82,
    max 4 — rounding-level).
    """
    if not _treatment_has_shadow(treatment):
        return None
    sh = float(treatment.get("shadow", 0) or 0)
    w, h = int(width), int(height)
    name = f"shadow_{w}x{h}_s{sh:.3f}.png"
    d = os.path.join(tempfile.gettempdir(), "agentcut-treatment-masks")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    path = os.path.join(d, name)
    if os.path.isfile(path):
        return path
    # No a() term: this build's geq has no a(X,Y) function
    # (verified: "Unknown function in 'a(X,Y)'"). The shadow
    # geq multiplies the white frame by (1 - sh*f), exactly
    # as the inline geq multiplies the video (in RGBA, where
    # black is 0, so the multiply and the blend agree).
    dd = "min(min(X,W-1-X),min(Y,H-1-Y))"
    u = f"min(max(1-({dd})/(0.12*min(W,H)),0),1)"
    f = f"({u})*({u})*(3-2*({u}))"
    vf = (f"format=rgba,geq=r='255*(1-{sh}*({f}))':"
          f"g='255*(1-{sh}*({f}))':"
          f"b='255*(1-{sh}*({f}))',"
          f"geq=r=0:g=0:b=0:a='255-r(X,Y)'")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"nullsrc=s={w}x{h}",
             "-vf", vf, "-frames:v", "1", path],
            capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if proc.returncode != 0 or not os.path.isfile(path):
        return None
    return path


def _treatment_shadow_inline(treatment, _fmt="yuv420p", _afmt="rgba"):
    """Inline shadow geq (v1.40.0 behaviour). FALLBACK ONLY:
    used when the pre-rendered mask from _treatment_mask_path
    is unavailable, so a mask-generation failure never
    silently drops the effect. Shadow is a soft inner edge
    shadow via geq — distance-to-edge falloff over 12% of the
    smaller side, smoothstepped — which needs an alpha format,
    so the chain converts to rgba (gbrap in rgb mode) and
    back.
    """
    if not isinstance(treatment, dict):
        return ""
    sh = float(treatment.get("shadow", 0) or 0)
    if sh <= 0:
        return ""
    d = "min(min(X,W-1-X),min(Y,H-1-Y))"
    u = f"min(max(1-({d})/(0.12*min(W,H)),0),1)"
    f = f"({u})*({u})*(3-2*({u}))"
    return (f"format={_afmt},geq=r='r(X,Y)*(1-{sh}*({f}))':"
            f"g='g(X,Y)*(1-{sh}*({f}))':"
            f"b='b(X,Y)*(1-{sh}*({f}))',format={_fmt}")


def _treatment_filters(treatment, _fmt="yuv420p", _afmt="rgba"):
    """FFmpeg filter chain for a treatment dict (v1.40.0).

    Returns "" when nothing is set (all default off, so old
    projects render identically). Grain is temporal noise
    (alls is the integer strength, allf=t so it shimmers per
    frame); vignette maps strength 0..1 to the lens angle
    PI/40..PI/4 (measured on this build on flat grey:
    corner/centre 0.99 at PI/40, 0.33 at PI/4). Since v1.46.0
    (R3) the SHADOW is NOT here: it is applied as a
    pre-rendered 1-frame mask + overlay via
    _treatment_mask_path (see the call sites in
    build_ffmpeg_args), because the shadow geq is evaluated
    per frame while the mask is rendered once. The vignette
    stays a per-frame filter — a mask cannot reproduce it
    exactly (see _treatment_mask_path). Grain stays per-frame
    by design — it must shimmer, and it is nearly free as a
    filter (its cost is encoder-side)."""
    if not isinstance(treatment, dict):
        return ""
    parts = []
    grain = float(treatment.get("grain", 0) or 0)
    g = int(round(grain))
    if g > 0:
        parts.append(f"noise=alls={g}:allf=t")
    vig = float(treatment.get("vignette", 0) or 0)
    if vig > 0:
        import math as _math
        angle = _math.pi * (0.025 + 0.225 * vig)
        parts.append(f"vignette=angle={angle!r}")
    return ",".join(parts)


def _bitrate_kbps(value):
    """Bitrate (string like '8M'/'5000k' or integer kbps) -> kbps int."""
    if isinstance(value, int):
        return int(value)
    s = str(value).strip().lower()
    if s.endswith("m"):
        return int(round(float(s[:-1]) * 1000))
    if s.endswith("k"):
        return int(round(float(s[:-1])))
    return int(round(float(s)))


def _ffmetadata_escape(text):
    """Escape a chapter title for an ffmetadata file."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace("#", "\\#").replace("=", "\\="))


# HarfBuzz shaped-text measurement lives in srt.py (v1.46.1, R1):
# measure_text_width() there is the single implementation (moved
# from here so the wrapper and the validator can measure with the
# real render font). _measure_text_width stays as a thin delegate
# so existing callers and tests keep importing it from render.
def _measure_text_width(text, font, size, cache=None):
    """Shaped text width (px) at one size/font — delegates to
    srt.measure_text_width (same behaviour, same fallback)."""
    return measure_text_width(text, font, size, cache)


def _default_caption_top(height, size, n_lines=1, line_spacing=1.35,
                         zone_clamp=False):
    """Top y of a no-position caption block (v1.42.0, Fix 3).

    The old default anchors the LAST line's top at height-120
    and stacks earlier lines upward. With zone_clamp (project
    resolve_overlaps: true) the whole block is pulled up so its
    bottom stays inside the subtitle zone (<= 0.92*height) —
    the same clamp validate.py's _text_box applies to the
    estimated caption box, so validation and rendering agree.
    Without it the anchor is exactly the old height-120.
    """
    est_h = (n_lines - 1) * size * line_spacing + size * 1.4
    top = float(height - 120) - (n_lines - 1) * size * line_spacing
    if zone_clamp:
        top = min(top, 0.92 * height - est_h)
    return top


def _active_caption_y(item, size, height, zone_clamp=False):
    """Single-line y for an active caption chunk (v1.37.0).

    Mirrors the normal caption bands numerically: top bands at
    40, bottom bands just above the frame bottom, centre centred,
    and the no-position default at height-120 (the old caption
    default). Active chunks are single-line by design, so one y
    serves the whole chunk."""
    pos = item.get("position")
    if pos in ("top_left", "top_center", "top_right"):
        return 40.0
    if pos in ("bottom_left", "bottom_center", "bottom_right"):
        return float(height - 40 - size * 1.4)
    if pos == "center":
        return float((height - size * 1.4) / 2)
    return _default_caption_top(height, size, 1, 1.35, zone_clamp)


def _expand_text_preset(item):
    """Expand a named text preset into animation/reveal/sweep
    fields (v1.44.0, F4 typography system).

    Presets are the entrance; styles are the look — the preset
    sets HOW the text arrives, _resolve_text_style sets what it
    looks like, and the two never fight. Validation rejects a
    preset combined with an explicit animation/reveal/
    word_timings, so expansion never overwrites an author's
    explicit entrance. Returns the item unchanged when it has
    no (valid) preset; else a copy with the preset popped.
    highlight_sweep forces the drawtext box and the rounded
    pill off: the sweeping bar IS the background, so a box
    behind it would double up and hide the sweep."""
    preset = item.get("preset")
    if preset not in TEXT_PRESETS:
        return item
    out = dict(item)
    out.pop("preset", None)
    if preset == "typewriter":
        # Whole words only (the reveal-word expansion below):
        # Arabic letters join, so a per-character reveal would
        # break joining; word reveal keeps every word intact.
        out["reveal"] = "word"
    elif preset == "slide_in":
        out["animation"] = "slide_in"
        out["animation_duration"] = 0.5
        out["fade_in"] = 0.5
    elif preset == "pop_in":
        out["animation"] = "pop_in"
        out["animation_duration"] = 0.5
    elif preset == "fade_up":
        out["animation"] = "fade_up"
        out["animation_duration"] = 0.5
        out["fade_in"] = 0.5
    elif preset == "kinetic_words":
        out["reveal"] = "word"
        out["_kinetic_words"] = True
    elif preset == "highlight_sweep":
        out["_sweep"] = "highlight"
        out["box"] = False
        out["box_radius"] = 0
    elif preset == "underline_swipe":
        out["_sweep"] = "underline"
    return out


def _resolve_text_style(item, height, default_style=None):
    """Fill a text/caption's unset fields from its style preset
    (v1.40.0). Only missing keys are filled — an explicit size,
    colour, box or shadow always wins, so old projects (which
    never set style) render identically. Shadow fills only when
    neither axis is set, mirroring the drawtext loop's rule that
    setting either axis takes over both. A project-level
    default_style (v1.44.0, F4 typography profile) applies when
    the item has no valid style of its own; an explicit item
    style always wins over the project default, and absent
    typography (default_style None) is the old behaviour.
    Returns the item itself when it has no (valid) style, else
    a copy."""
    style = TEXT_STYLES.get(item.get("style")) \
        if isinstance(item.get("style"), str) else None
    if style is None and isinstance(default_style, str):
        style = TEXT_STYLES.get(default_style)
    if style is None:
        return item
    out = dict(item)
    if "size" not in out:
        out["size"] = int(round(style["size_frac"] * height))
    if "color" not in out:
        out["color"] = style["color"]
    if "box" not in out:
        out["box"] = style["box"]
    if "shadow_x" not in out and "shadow_y" not in out:
        out["shadow_x"], out["shadow_y"] = style["shadow"]
    # v1.41.0: card-style fields fill the same way, so the
    # card preset carries its rounded translucent backdrop
    # without the caller spelling out box geometry.
    for _k in ("box_radius", "box_color", "box_opacity", "font"):
        if _k not in out and _k in style:
            out[_k] = style[_k]
    return out


# ASS style font mapping (v1.41.0, Fix 2): registry key or
# resolved font path basename to (libass family name, Bold flag).
# libass resolves families through fontconfig + the ass filter's
# fontsdir (bundled fonts/), so these must be family names, not
# file paths. Bold -1 is ASS's "true"; 0 is "false".
_ASS_FONT_BY_REGISTRY = {
    "noto-naskh": ("Noto Naskh Arabic", 0),
    "noto-sans-bold": ("Noto Sans Arabic", -1),
    "dejavu": ("DejaVu Sans", 0),
}
_ASS_FONT_BY_BASENAME = (
    ("NotoSansArabic-Bold", ("Noto Sans Arabic", -1)),
    ("NotoSansArabic", ("Noto Sans Arabic", 0)),
    ("NotoNaskhArabic", ("Noto Naskh Arabic", 0)),
    ("DejaVuSans-Bold", ("DejaVu Sans", -1)),
    ("DejaVuSans", ("DejaVu Sans", 0)),
)


def _ass_font_for(item, sample_text):
    """(family, bold) for an ASS event from a text/caption item.

    A named bundled font wins (same rule as drawtext); else the
    script-detected font's basename maps to its family; else the
    sample text's script decides (Arabic -> Noto Sans Arabic
    (Bold), else DejaVu). A missing named font cannot reach here:
    validation rejects it with FONT_NOT_FOUND before the graph
    builds."""
    _reg = item.get("font")
    if isinstance(_reg, str) and _reg in _ASS_FONT_BY_REGISTRY:
        return _ASS_FONT_BY_REGISTRY[_reg]
    _path = find_font_for_text(sample_text) if sample_text else None
    if _path:
        _base = os.path.basename(_path)
        for _frag, _fam in _ASS_FONT_BY_BASENAME:
            if _frag in _base:
                return _fam
    if sample_text and _ARABIC_RE.search(sample_text):
        return ("Noto Sans Arabic", -1)
    return ("DejaVu Sans", 0)


def _ass_pos_for_text(item, size, width, height, kin_y):
    """(alignment, x, y) for an ASS title event, mirroring the
    old kinetic drawtext anchors: a numeric x is the line's left
    edge (alignment 7, top-left); left/right presets anchor at
    the 40px margins (7 / 9); centre/centre presets and absent
    x centre the line (alignment 8, top-centre, x = width/2).
    kin_y is the line's top (same band logic as drawtext y)."""
    _pos = item.get("position")
    _xv = item.get("x", "center")
    if isinstance(_xv, (int, float)):
        return (7, float(_xv), kin_y)
    if _pos in ("top_left", "bottom_left"):
        return (7, 40.0, kin_y)
    if _pos in ("top_right", "bottom_right"):
        return (9, float(width - 40), kin_y)
    return (8, float(width) / 2, kin_y)


def _background_rects(info, width, height):
    """(x, y, w, h, start, end) rects that act as a text's background
    (v1.45.0, R4c-2, Defect 2): motion shapes (rect/circle/ellipse)
    and lower_third cards use their keyframe-union bbox
    (el["_bbox"] — the same rect the compositor overlays); diagram
    cards use their display rect (size fraction x canvas aspect,
    same geometry as the card compositing). A text whose centre
    falls inside one of these during its window gets box=0 — the
    shape/card IS the background, so a drawtext box (or rounded
    pill) inside it is a nested sharp black box. Charts and
    counters are NOT backgrounds (transparent videos / text
    themselves), and line shapes are strokes, not backgrounds.
    The motion bbox is the union over keyframes, so a moving
    shape's rect is conservative (it covers the whole path); the
    time-window check keeps a text off a shape that is never
    there with it.
    """
    rects = []
    for el in info.get("motion", []) or []:
        if not isinstance(el, dict):
            continue
        kind = el.get("kind")
        if kind == "lower_third":
            pass
        elif kind == "shape" and el.get("shape") in ("rect", "circle",
                                                     "ellipse"):
            pass
        else:
            continue
        bb = el.get("_bbox")
        if not bb or len(bb) != 4:
            continue
        try:
            bx, by, bw, bh = (float(v) for v in bb)
            st, en = float(el["start"]), float(el["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if bw <= 0 or bh <= 0 or en <= st:
            continue
        rects.append((bx, by, bw, bh, st, en))
    for dg in info.get("diagrams", []) or []:
        if not isinstance(dg, dict) or not dg.get("card"):
            continue
        try:
            dw = _even(float(dg["size"]) * width)
            cw, ch = float(dg["canvas_w"]), float(dg["canvas_h"])
            dh = dw * ch / cw if cw > 0 else 0.0
            st, en = float(dg["start"]), float(dg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if dw <= 0 or dh <= 0 or en <= st:
            continue
        if "x" in dg and "y" in dg:
            try:
                dx, dy = float(dg["x"]), float(dg["y"])
            except (TypeError, ValueError):
                continue
        else:
            pos = dg.get("position", "bottom_right")
            if pos in ("bottom_left", "top_left"):
                dx = 20.0
            elif pos in ("bottom_right", "top_right"):
                dx = width - dw - 20.0
            else:
                dx = (width - dw) / 2
            if pos in ("top_left", "top_center", "top_right"):
                dy = 20.0
            elif pos == "center":
                dy = (height - dh) / 2
            else:
                dy = height - dh - 20.0
        rects.append((dx, dy, dw, dh, st, en))
    return rects


def _pos_expr_value(expr, width, height, tw, th):
    """Numerically evaluate one POS_MAP expression (w/h are the
    output size, tw/th the text width/height). Only the five
    forms POS_MAP uses are recognised; anything else is 0."""
    expr = str(expr)
    if expr == "(w-text_w)/2":
        return (width - tw) / 2
    if expr == "(h-text_h)/2":
        return (height - th) / 2
    if expr == "w-text_w-40":
        return width - tw - 40.0
    if expr == "h-text_h-40":
        return height - th - 40.0
    try:
        return float(expr)
    except (TypeError, ValueError):
        return 0.0


def _text_on_background(item, is_caption, width, height, size, bg_rects,
                        width_cache, zone_clamp=False):
    """True when a text/caption item sits on a background rect
    (Defect 2): its centre falls inside a background rect whose
    time window overlaps the item's window. The centre test (not
    any-pixel overlap) is deliberate: a long caption crossing a
    shape's edge is mostly over the board and keeps its box; a
    text centred on a shape is ON the shape. Counters and tracked
    texts are skipped (a counter's width is dynamic; a tracked
    text's position is sampled per frame, not static). The
    position rules mirror the drawtext loop exactly (POS_MAP,
    _wrap_y, _default_caption_top, the "(<cx>-text_w/2)"
    motion-label form); the width is the HarfBuzz shaped width
    and the height the caption line-height factor (size*1.4) —
    the centre is what matters, so the approximation is fine.
    """
    if item.get("_counter") or item.get("track"):
        return False
    try:
        start = float(item.get("start", 0))
        end = float(item.get("end", 0))
    except (TypeError, ValueError):
        return False
    text = str(item.get("text", ""))
    if not text or not bg_rects:
        return False
    if item.get("font"):
        _mf = find_bundled_font(item["font"])
    else:
        _mf = find_font_for_text(text)
    tw = _measure_text_width(text, _mf, size, width_cache)
    th = float(size) * 1.4
    pos = item.get("position")
    if is_caption:
        if "_wrap_y" in item:
            top = float(item["_wrap_y"])
            _xe = (POS_MAP[item["position"]][0]
                   if pos in POS_MAP else "(w-text_w)/2")
            left = _pos_expr_value(_xe, width, height, tw, th)
        elif pos in POS_MAP:
            _xe, _ye = POS_MAP[pos]
            left = _pos_expr_value(_xe, width, height, tw, th)
            top = _pos_expr_value(_ye, width, height, tw, th)
        else:
            left = (width - tw) / 2
            top = _default_caption_top(height, size, 1, 1.35, zone_clamp)
    elif pos in POS_MAP:
        _xe, _ye = POS_MAP[pos]
        left = _pos_expr_value(_xe, width, height, tw, th)
        top = _pos_expr_value(_ye, width, height, tw, th)
    else:
        xv = item.get("x", "center")
        if xv == "center":
            left = (width - tw) / 2
        else:
            try:
                left = float(xv)
            except (TypeError, ValueError):
                _m = re.match(r"^\(([\d.]+)-text_w/2\)$", str(xv))
                left = (float(_m.group(1)) - tw / 2
                        if _m else (width - tw) / 2)
        try:
            top = float(item.get("y", 100))
        except (TypeError, ValueError):
            top = 100.0
    cx, cy = left + tw / 2, top + th / 2
    for (bx, by, bw, bh, bs, be) in bg_rects:
        if start >= be or bs >= end:
            continue
        if bx - 4 <= cx <= bx + bw + 4 and by - 4 <= cy <= by + bh + 4:
            return True
    return False


def _esc_filter_arg(value):
    """Escape a path for embedding in a filter option value."""
    return (str(value).replace("\\", "\\\\").replace(":", "\\:")
            .replace(",", "\\,").replace("'", "\\'"))


_FILTER_SCRIPT_FLAG = None


def _filter_script_flag():
    """Return the filter script CLI flag supported by the host FFmpeg build.

    FFmpeg 7+ / 8+ / 9+ removed -filter_complex_script in favour of -/filter_complex.
    Older versions support -filter_complex_script.
    """
    global _FILTER_SCRIPT_FLAG
    if _FILTER_SCRIPT_FLAG is None:
        try:
            p = subprocess.run(["ffmpeg", "-v", "error", "-/filter_complex"],
                               capture_output=True, text=True, timeout=2)
            if "Unrecognized option" not in p.stderr:
                _FILTER_SCRIPT_FLAG = "-/filter_complex"
            else:
                _FILTER_SCRIPT_FLAG = "-filter_complex_script"
        except Exception:
            _FILTER_SCRIPT_FLAG = "-filter_complex_script"
    return _FILTER_SCRIPT_FLAG


def _rgb_hex(rgb):
    """(r, g, b) tuple -> #RRGGBB string for drawtext colors."""
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _motion_derived_texts(info, width, height):
    """Drawtext items for motion-graphics labels (v1.43.0, Round 2).

    Chart value/category labels are plain text items through the
    normal drawtext path (textfile + BiDi), never baked into the
    motion video — so Arabic labels shape correctly. Each label
    appears when its bar/segment/point finishes animating and
    stays to the element end. x is a centred expression string
    ("(cx-text_w/2)"), which the text loop passes through
    verbatim; y is clamped into the frame.
    """
    from .motion import (bars_layout, donut_segments, line_chart_points,
                         _fmt_value)
    items = []
    for el in info.get("motion", []) or []:
        kind = el.get("kind")
        start = float(el["start"])
        end = float(el["end"])
        window = end - start
        if kind == "counter":
            # A live number: the drawtext text is an %{eif}
            # expression built by motion.counter_drawtext (R3).
            # x/y are the CENTER of the counter.
            from .motion import counter_drawtext
            # One system for all text surfaces (v1.44.0, F4):
            # a label_style resolves through the shared
            # _resolve_text_style in the main text loop, so the
            # counter's look comes from the same style table as
            # titles and captions. Validate normalizes
            # size/colour/box defaults onto the element, so an
            # explicit value cannot be told from a default: when
            # a style is present the STYLE wins and those fields
            # are omitted for the resolver to fill; without a
            # style the item is built exactly as before
            # (byte-identical).
            _cstyle = el.get("label_style") \
                if isinstance(el.get("label_style"), str) else None
            csize = (int(round(TEXT_STYLES[_cstyle]["size_frac"] * height))
                     if _cstyle in TEXT_STYLES
                     else int(el.get("size", 48)))
            # Rounded pill (v1.45.0, R5): a counter's display
            # value is live, so the pill is sized to the WIDEST
            # value it can show (the wider of from/to, formatted
            # with the element's decimals and affixes, in the
            # same before/after order as counter_drawtext).
            # _counter_cx is the numeric centre (the x
            # expression above is "(cx-text_w/2)").
            from .motion import _contains_arabic
            _dec = int(el.get("decimals", 0))
            _pfx = el.get("prefix", "") or ""
            _sfx = el.get("suffix", "") or ""

            def _fmt_num(v):
                if _dec == 1:
                    return f"{float(v):.1f}"
                return str(int(float(v)))

            def _display(v):
                _num = _fmt_num(v)
                _before, _after = [], []
                if _sfx:
                    if _contains_arabic(_sfx):
                        _before.append(_sfx if _sfx.strip() == "\u066a"
                                      else _sfx + " ")
                    else:
                        _after.append(" " + _sfx)
                if _pfx:
                    if _contains_arabic(_pfx):
                        _after.append(" " + _pfx)
                    else:
                        _before.append(_pfx + " ")
                return "".join(_before) + _num + "".join(_after)

            _citem = {
                "text": "", "_counter": True,
                "_counter_expr": counter_drawtext(el),
                "_counter_affixes": (el.get("prefix", "") or "")
                + (el.get("suffix", "") or ""),
                "_counter_max_text": max(
                    (_display(el.get("from", 0)),
                     _display(el.get("to", 0))),
                    key=len),
                "_counter_cx": float(el["x"]),
                "start": start, "end": end,
                "x": f"({float(el['x']):.1f}-text_w/2)",
                "y": int(round(float(el["y"]) - csize / 2))}
            # Fade with the element (v1.45.0, R12 fix): a counter
            # on a fading element (an adapted diagram's progress
            # counter, or an explicit fade_out) must fade over
            # the same window, or it stays visible after the
            # element is gone. The label fade below does the same
            # for chart labels.
            if el.get("fade_out"):
                _fo = float(el["fade_out"])
                _fs = (float(el["fade_start"])
                       if el.get("fade_start") is not None
                       else end - _fo)
                _citem["end"] = _fs + _fo
                _citem["fade_out"] = _fo
            if _cstyle is not None:
                _citem["style"] = _cstyle
            if _cstyle is None:
                _citem["size"] = csize
                _citem["color"] = el.get("color", "white")
                _citem["box"] = bool(el.get("box", True))
            items.append(_citem)
            continue
        if kind == "lower_third":
            # The text slides with the card (same linear delta)
            # and fades with the same ramps, so card and text
            # stay in lockstep; the card itself is the box, so
            # the text has none. tx is the text's left edge at
            # rest. For Arabic text (v1.45.0, R4c-4, Defect 4)
            # the text is RIGHT-aligned in the card (its right
            # edge 24px from the card's right edge), because a
            # left-aligned Arabic word in a wide card reads as
            # a stray word at the far left; Latin text stays
            # left-aligned at 24px from the card's left edge.
            x0 = float(el["x"])
            w0 = float(el["w"])
            h0 = float(el["h"])
            y0 = float(el["y"])
            enter = float(el.get("enter", 0.6))
            exit_ = float(el.get("exit", 0.6))
            # The style's size drives the card geometry (the
            # text is vertically centred in the card at its
            # rendered size). Validate normalizes a size default
            # onto the element, so when a style is present the
            # STYLE wins (see the counter note above).
            _ltstyle = el.get("style") \
                if isinstance(el.get("style"), str) else None
            tsize = (int(round(TEXT_STYLES[_ltstyle]["size_frac"] * height))
                     if _ltstyle in TEXT_STYLES
                     else int(el.get("size", 36)))
            from .motion import _contains_arabic
            if _contains_arabic(el["text"]):
                _ltfont = None
                if el.get("font"):
                    from .validate import find_bundled_font
                    _ltfont = find_bundled_font(el["font"])
                if _ltfont is None:
                    from .validate import find_font_for_text
                    _ltfont = find_font_for_text(str(el["text"]))
                _ltw = _measure_text_width(str(el["text"]), _ltfont,
                                           tsize)
                tx = x0 + w0 - _ltw - 24.0
            else:
                tx = x0 + 24.0
            ty = y0 + (h0 - tsize) / 2.0
            slide = x0 + w0
            # Spring entrance (v1.45.0, R6): the text slides
            # with the card's spring (same overshoot, so card
            # and text stay in lockstep), and its opacity
            # follows the spring too (clamped to [0, 1]).
            # The exit stays linear. The opacity is carried as
            # _alpha_expr (the drawtext loop uses it instead of
            # the linear fade_in/fade_out ramp); the position
            # is the x expression below.
            from .motion import _easing_expr_ffmpeg
            _x_in = f"{tx:.1f}"
            _a_in = "1"
            if enter > 0:
                _u_in = f"((t-{start:.3f})/{enter:.3f})"
                _spr = _easing_expr_ffmpeg("spring", _u_in)
                _x_in = f"{tx:.1f}-{slide:.1f}*(1-{_spr})"
                _a_in = f"(min({_spr},1))"
            _x_out = f"{tx:.1f}"
            _a_out = "1"
            if exit_ > 0:
                _u_out = f"((t-{end - exit_:.3f})/{exit_:.3f})"
                _x_out = f"{tx:.1f}-{slide:.1f}*{_u_out}"
                _a_out = f"(1-{_u_out})"
            if enter > 0 and exit_ > 0:
                x_expr = (f"if(lt(t,{start + enter:.3f}),{_x_in},"
                          f"if(gt(t,{end - exit_:.3f}),{_x_out},{tx:.1f}))")
                _alpha_expr = (
                    f"if(lt(t,{start + enter:.3f}),{_a_in},"
                    f"if(gt(t,{end - exit_:.3f}),{_a_out},1))")
            elif enter > 0:
                x_expr = (f"if(lt(t,{start + enter:.3f}),{_x_in},"
                          f"{tx:.1f})")
                _alpha_expr = (
                    f"if(lt(t,{start + enter:.3f}),{_a_in},1)")
            elif exit_ > 0:
                x_expr = (f"if(gt(t,{end - exit_:.3f}),{_x_out},"
                          f"{tx:.1f})")
                _alpha_expr = (
                    f"if(gt(t,{end - exit_:.3f}),{_a_out},1)")
            else:
                x_expr = f"{tx:.1f}"
                _alpha_expr = "1"
            lt_item = {"text": str(el["text"]), "start": start,
                       "end": end,
                       "x": x_expr, "y": int(round(ty)), "box": False,
                       "_alpha_expr": _alpha_expr}
            if _ltstyle is not None:
                lt_item["style"] = _ltstyle
            if _ltstyle is None:
                lt_item["size"] = tsize
                lt_item["color"] = el.get("color", "white")
            if el.get("font"):
                lt_item["font"] = el["font"]
            items.append(lt_item)
            continue
        # The label_style's size drives the label geometry (y
        # offsets are size-relative). Validate normalizes
        # label_size/label_color defaults onto the element, so
        # when a style is present the STYLE wins (see the
        # counter note above).
        _lstyle = el.get("label_style") \
            if isinstance(el.get("label_style"), str) else None
        size = (int(round(TEXT_STYLES[_lstyle]["size_frac"] * height))
                if _lstyle in TEXT_STYLES
                else int(el.get("label_size", 28)))
        color = _rgb_hex(el.get("label_color") or (255, 255, 255))
        stagger = float(el.get("stagger", 0.12))
        grow = float(el.get("grow", 0.5))

        def _label(text, cx, y, when, size_ov=None, color_ov=None,
                 box_ov=None):
            # size/color/box default to the chart label's (the
            # enclosing label_size/label_color, box=True); the
            # comparison-cards value passes its own (value_size,
            # the card accent colour, box=False — the card is the
            # box). A style, when present, wins over all of them
            # (see the counter note above).
            if not text:
                return
            _size = size_ov if size_ov is not None else size
            _color = color_ov if color_ov is not None else color
            _box = box_ov if box_ov is not None else True
            cx = min(max(float(cx), 80.0), float(width) - 80.0)
            y = min(max(float(y), 4.0), float(height) - _size - 4.0)
            _litem = {
                "text": str(text), "start": when, "end": end,
                "x": f"({cx:.1f}-text_w/2)", "y": int(round(y))}
            # Fade with the element (v1.45.0, R12 fix): a chart
            # label on a fading element must fade over the same
            # window as the element's video. Without this, an
            # adapted diagram's value labels stayed visible after
            # the chart faded (fix4: 157 bright px at t=5.0).
            # The label's end moves to the fade end and it gets
            # the element's fade_out, so the drawtext alpha ramp
            # covers exactly the fade window.
            if el.get("fade_out"):
                _fo = float(el["fade_out"])
                _fs = (float(el["fade_start"])
                       if el.get("fade_start") is not None
                       else end - _fo)
                _litem["end"] = _fs + _fo
                _litem["fade_out"] = _fo
            if _lstyle is not None:
                _litem["style"] = _lstyle
            if _lstyle is None:
                _litem["size"] = _size
                _litem["color"] = _color
                _litem["box"] = _box
            items.append(_litem)

        # v1.45.0, R4b-2: when a chart has a draw duration,
        # its staggered progress runs over `draw`, not the whole
        # window — so the labels appear on the draw clock, not
        # the window clock.
        eff_window = (float(el["draw"]) if el.get("draw")
                      else window)
        if kind == "bars":
            baseline = float(el["y"]) + float(el["h"])
            for i, bar in enumerate(bars_layout(el)):
                if bar["label"]:
                    _label(bar["label"], bar["cx"], baseline + 10,
                           start + i * stagger * eff_window)
                # Value label: NO background box (box_ov=False).
                # The rounded pill (v1.45.0, R5) is size*1.4 +
                # padding tall and would overlap the bar's top
                # by ~13px, dimming it — the label is white text
                # over the dark board and reads fine bare.
                _label(_fmt_value(bar["value"]), bar["cx"],
                       baseline - bar["height"] - size - 10,
                       start + (i * stagger + grow) * eff_window,
                       box_ov=False)
        elif kind in ("donut", "pie"):
            cx0, cy0 = float(el["x"]), float(el["y"])
            radius = float(el["radius"])
            for i, seg in enumerate(donut_segments(el)):
                mid = math.radians(seg["start_angle"] + seg["arc"] / 2.0)
                lx = cx0 + (radius + 40.0) * math.cos(mid)
                ly = cy0 + (radius + 40.0) * math.sin(mid)
                text = _fmt_value(seg["value"])
                if seg["label"]:
                    text = f"{seg['label']}: {text}"
                _label(text, lx, ly - size / 2.0,
                       start + (i * stagger + grow) * eff_window)
        elif kind == "line_chart":
            pts = line_chart_points(el)
            # Arc lengths along the polyline: the label for point
            # i appears when the head reaches it.
            seg_lens = [math.hypot(pts[j + 1][0] - pts[j][0],
                                   pts[j + 1][1] - pts[j][1])
                        for j in range(len(pts) - 1)]
            total_len = sum(seg_lens) or 1.0
            acc = 0.0
            for i, (px, py) in enumerate(pts):
                # No background box (box_ov=False): the pill
                # would overlap the line below the label (same
                # R5 overlap as the bars value label).
                _chart_lbl_y = max(float(el["y"]) + 10, py - size - 12)
                _label(_fmt_value(el["values"][i]), px, _chart_lbl_y,
                       start + (acc / total_len) * eff_window,
                       box_ov=False)
                if i < len(seg_lens):
                    acc += seg_lens[i]
        elif kind == "comparison_cards":
            # v1.45.0, R4b-1: the label sits near the top of its
            # card (below the accent bar) and the value below it,
            # both centred on the card. Each pair appears when its
            # card finishes growing (p == 1), so the drawn card is
            # always at full size underneath. The value has no box
            # (the card is the box) and takes the card's accent
            # colour at value_size.
            from .motion import cards_layout
            value_size = int(el.get("value_size", 44))
            for i, card in enumerate(cards_layout(el)):
                when = start + (i * stagger + grow) * eff_window
                # No box on the label either: the card body is
                # the box (a drawtext box inside a card would be
                # a nested sharp box — the thing this package
                # removes).
                if card["value"] is None:
                    _label(card["label"], card["cx"],
                           card["y0"] + card["h"] * 0.45, when,
                           size_ov=36,
                           box_ov=False)
                else:
                    _label(card["label"], card["cx"],
                           card["y0"] + card["h"] * 0.16, when,
                           box_ov=False)
                    vtext = (_fmt_value(card["value"])
                             if isinstance(card["value"], (int, float))
                             else str(card["value"]))
                    _label(vtext, card["cx"],
                           card["y0"] + card["h"] * 0.48, when,
                           size_ov=value_size,
                           color_ov=_rgb_hex(card["color"]),
                           box_ov=False)
    return items


def build_ffmpeg_args(project, info, output_path, scale_factor=1.0,
                      metadata_path=None, guides=False,
                      review_timecode=False, filter_script_dir=None,
                      video_codec="libx264", video_crf=None,
                      audio_codec="aac", final_range_convert=True,
                      t_override=None, chain_scale_out=None,
                      master_loudnorm_measured=None,
                      skip_master_loudnorm=False,
                      audio_mix_path=None, video_preset=None):
    """Return (argv, total_duration, text_dir) for a validated project.

    metadata_path is an ffmetadata file carrying chapters (or None).
    guides draws title-safe/action-safe boxes (preview only, never
    a final render unless the caller asks). review_timecode burns
    a running timecode into this render; final renders pass False
    here unless the project itself sets timecode (see
    render_project), so a normal final render is unaffected.

    Refactoring options (all default to the old behaviour, so a
    plain call produces exactly the old argv):
    filter_script_dir writes the filter graph to
    filter_complex.txt in that directory and passes
    -filter_complex_script instead of the inline -filter_complex
    string — the graph content is identical, only the transport
    changes, which avoids argv length limits on very long
    projects. video_codec / video_crf / audio_codec select the
    output codecs (segment caching uses lossless libx264 crf 0 +
    pcm_f32le; an explicit video_bitrate still wins over crf, as
    before). final_range_convert=False replaces the final
    out_range=tv conversion with a passthrough, so a segment's
    frames match the chain-native frames exactly (the assembly
    pass converts once, as a direct render does). t_override
    replaces ONLY the -t output flag (segment/assembly renders
    pass total + one frame so the fps filter's EOF-flushed
    boundary frame survives -t: with -t exactly at the clip
    duration that frame is dropped — verified: a 2.51s clip
    yields 60 frames with -t 2.51 but 61 without it); every
    other use of total (SFX durations, fade windows, silence
    padding) still uses total, so the timeline is unchanged and
    -t slack can never add content beyond the graph's EOF.
    chain_scale_out is (out_range, out_color_matrix) or None:
    when given, every clip-chain scale filter forces that
    output range/colorspace. Segment renders pass it so their
    scales reproduce the direct graph's scale outputs (a
    single-clip render negotiates a different format than the
    multi-clip direct graph — measured: image segments decoded
    as yuvj420p pulled the assembly's scales to bt470bg/pc
    and re-converted video frames with a second rounding).
    None keeps the old behaviour.
    v1.41.0 mastering (Fix 5): master_loudnorm_measured is
    the _run_loudness dict for the finished mix; when the
    project requests master loudnorm AND measured values
    are given, the master stage is the stable LINEAR form
    (constant gain, no pumping). skip_master_loudnorm=True
    omits the master stage (used for the measurement pass
    itself and when the mix is unmeasurable, e.g. silence —
    skipping is safer than single-pass loudnorm on silence,
    which emits NaN samples). Requesting master loudnorm
    with neither measured values nor skip raises ValueError:
    render_project always measures first, so production
    renders never hit this; direct graph builders must pass
    one of the two. audio_mix_path, when set, returns an
    argv mapping ONLY the mixed audio to that path as
    pcm_s16le (video maps and encoder args omitted, but
    [vfinal] is consumed by a nullsink — FFmpeg errors on
    unconnected filter outputs, so the video graph still
    runs, decode + filter without x264 encode; a mastered
    render therefore costs roughly one extra video-filter
    pass). video_preset selects the x264 preset (default
    medium; veryfast is for preview callers that pass it
    explicitly).
    """
    width = _even(info["width"] * scale_factor)
    height = _even(info["height"] * scale_factor)
    fps = info["fps"]
    # v1.42.0 (Fix 3): zone manager. When the project opts in
    # with resolve_overlaps: true, no-position captions use
    # the zone-clamped anchor (block bottom <= 0.92*height)
    # and keyword pops are moved into the callout band (see
    # the moves loop below); otherwise every anchor is exactly
    # the old one.
    zone_clamp = project.get("resolve_overlaps") is True
    # v1.41.0 (Fix 1): internal colour pipeline. "yuv"
    # (default) keeps the historical yuv420p/rgba chain;
    # "rgb" runs the whole internal graph in full-range
    # gbrp/gbrap with a single final conversion to
    # yuv420p at the final node (which is unchanged and
    # converts from either internal format).
    _rgb = info.get("color_pipeline", "yuv") == "rgb"
    _fmt = "gbrp" if _rgb else "yuv420p"
    _afmt = "gbrap" if _rgb else "rgba"
    _yuvafmt = "gbrap" if _rgb else "yuva420p"
    _lutfmt = "gbrp" if _rgb else "rgb24"
    clips = info["clips"]
    total = info["total_duration"]
    argv = ["ffmpeg", "-y", "-v", "error"]
    # Inputs: clips first, then audio tracks.
    for _ci, clip in enumerate(clips):
        if clip["kind"] == "image" and (effective_ken_burns(clip, _ci, project) not in (None, "none")
                                       or clip.get("camera")):
            # Ken Burns uses zoompan on a SINGLE input frame: zoompan
            # itself generates d output frames. A looped input would
            # make zoompan emit d frames per input frame and blow up
            # the clip duration. A camera path (v1.40.0) is the same
            # zoompan mechanism, so it takes the same branch.
            pass
        elif clip["kind"] == "image":
            argv += ["-loop", "1", "-t", str(clip["duration"])]
        argv += ["-i", clip["src"]]
    # Resolved audio from validation (1.34.0): validate no longer
    # mutates the project, so the render must use info["audio"]
    # (resolved srcs, coerced starts); fall back to the project's
    # own list only for hand-built info dicts without it.
    audio_tracks = info.get("audio") or project.get("audio") or []
    for track in audio_tracks:
        argv += ["-i", track["src"]]
    overlays = info.get("overlays", []) or []
    # Overlay segments: exactly one per overlay (Loop 6 round 3).
    # Scale keyframes used to split an overlay into <=0.5s
    # fixed-scale segments; they now use scale eval=frame, which
    # evaluates width/height per frame (verified via showinfo:
    # 50, 52, 54, 56... px across frames, and via overlay edges
    # growing smoothly). Note for future readers: do NOT test
    # eval=frame by encoding its output directly — ffmpeg
    # auto-inserts a scale back to the encoder's first-frame size,
    # which hides the variation. Test it through the overlay
    # filter (which accepts per-frame sizes) or via showinfo.
    overlay_segments = []  # (overlay, seg_start, seg_end, video_index, audio_index|None)
    for ov in overlays:
        seg_start, seg_end = 0.0, float(ov["duration"])
        idx = _input_count(argv)
        if ov["kind"] == "image":
            argv += ["-loop", "1", "-t", str(seg_end - seg_start)]
        argv += ["-i", ov["src"]]
        audio_idx = None
        if ov["kind"] == "video" and float(ov.get("volume") or 0.0) > 0:
            # A second input for the same file, so its audio stream
            # can be mixed independently of its video stream.
            audio_idx = _input_count(argv)
            argv += ["-i", ov["src"]]
        overlay_segments.append((ov, seg_start, seg_end, idx, audio_idx))
    # Animated stroke diagrams (v1.38.0): transparent draw-on
    # videos generated at render time by render_project (see
    # diagrams.py). They enter as ordinary video inputs here,
    # BEFORE the metadata input, so the chapters index computed
    # below still lands exactly on the metadata input. A diagram
    # without a generated video (hand-built info dicts) is
    # skipped — render_project always generates one, so a skip
    # here means the caller bypassed it, never a silent drop in
    # a real render.
    diagram_segments = []  # (diagram, video_index)
    for dg in info.get("diagrams", []) or []:
        if not dg.get("_video"):
            continue
        idx = _input_count(argv)
        argv += ["-i", dg["_video"]]
        diagram_segments.append((dg, idx))
    # v1.41.0 Fix 6: backdrop cards enter as ordinary image
    # inputs, next to their diagrams and before the metadata
    # input, so the chapters index still lands exactly on it.
    card_segments = []  # (diagram, image_index)
    for dg in info.get("diagrams", []) or []:
        if not dg.get("_card"):
            continue
        # An adapted diagram has a card but no video (the motion
        # element renders instead); a stroke diagram needs both.
        if not dg.get("_video") and not dg.get("adapted_to_motion"):
            continue
        idx = _input_count(argv)
        argv += ["-i", dg["_card"]]
        card_segments.append((dg, idx))
    # Motion graphics (v1.43.0): transparent element videos
    # generated at render time by render_project (see motion.py).
    # They enter as ordinary video inputs here, before the
    # metadata input, so the chapters index computed below still
    # lands exactly on the metadata input. An element without a
    # generated video (hand-built info dicts, or the counter
    # kind, which has no video) is skipped — render_project
    # always generates one, so a skip here means the caller
    # bypassed it, never a silent drop in a real render.
    motion_segments = []  # (element, video_index)
    for el in info.get("motion", []) or []:
        if not el.get("_video"):
            continue
        idx = _input_count(argv)
        argv += ["-i", el["_video"]]
        motion_segments.append((el, idx))
    metadata_idx = None
    if metadata_path:
        # Chapters live in an ffmetadata input appended after all
        # media inputs, so its index is known exactly here.
        metadata_idx = _input_count(argv)
        argv += ["-i", metadata_path]
    filters = []
    # Clip-chain scale output force (segment renders only; None
    # keeps the old behaviour everywhere else).
    cs = _chain_scale_suffix(chain_scale_out)
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
            if clip.get("reframe_file"):
                # Reframe (v1.44.0, F3): crop the SOURCE before
                # trim, with per-frame x/y sampled from the
                # AgentMedia windows at source time t (the crop
                # filter's t is the input timestamp, which trim
                # has not reset yet); then scale to the exact
                # output size — fit is ignored, the crop aspect
                # already matches the target. Crop offsets are
                # rounded to even pixels for yuv420p (<=1px).
                # Validation rejects camera/stabilize/blur fits/
                # freeze_at/reverse/speed_curve with reframe, so
                # `pre` here is always the plain trim; linear
                # speed still works (the crop is at source time).
                _rf = (info.get("data_files") or {}).get(
                    clip["reframe_file"], {}).get("data")
                _rw = _rf["windows"]
                _cw = int(_rw[0]["w"]); _ch = int(_rw[0]["h"])
                _rx = f"2*trunc(({_track_interp_expr(_rw, 't', 'x')})/2)"
                _ry = f"2*trunc(({_track_interp_expr(_rw, 't', 'y')})/2)"
                vf = (f"[{i}:v]crop={_cw}:{_ch}:x='{_rx}':y='{_ry}',"
                      f"{pre},scale={width}:{height}{cs},"
                      f"fps={fps},format={_fmt}")
            elif clip.get("camera"):
                # Camera path on video (v1.40.0): 2x pre-scale
                # + zoompan at output size (the spec's minimum
                # for video clips; stills use a 1x cover
                # pre-scale since v1.46.0, see below), but the
                # crop follows the multi-stop path.
                # zoompan d=1 emits one frame per input frame
                # and `in` counts the fps-normalised stream,
                # so in/fps is clip-local seconds. Validation
                # rejects blur fits, stabilize and speed_curve
                # with camera, so the plain pre (trim/speed/
                # freeze/reverse/loop) is the whole input here;
                # framing is cover by construction, like KB.
                vf = (f"[{i}:v]{pre},scale={2 * width}:{2 * height}:"
                      f"force_original_aspect_ratio=increase,"
                      f"crop={2 * width}:{2 * height},fps={fps},"
                      f"{_camera_zoompan_filter(clip['camera'], f'(in/{fps})', width, height, 1, fps)},"
                      f"setpts=PTS-STARTPTS,format={_fmt}")
            elif clip.get("_stab_trf"):
                # Two-pass vidstab: pass 1 (detect) ran in
                # render_project at this same scale; pass 2
                # transforms the scaled frames.
                vf = (f"[{i}:v]trim=start={clip['trim_in']}:end={clip['trim_out']},"
                      f"setpts=PTS-STARTPTS,"
                      f"{_scale_part(clip.get('fit'), width, height, chain_scale_out)},"
                      f"vidstabtransform=input='{_esc_filter_arg(clip['_stab_trf'])}',"
                      f"fps={fps},format={_fmt}")
            elif clip.get("fit") in ("blur", "blur_pad"):
                # Blurred background: the same stream twice — once
                # scaled to cover and blurred/darkened as the
                # background, once scaled to contain as the
                # foreground, composited centred.
                filters.append(f"[{i}:v]{pre},split=2[ba{i}][bb{i}]")
                filters.append(
                    f"[ba{i}]scale={width}:{height}:"
                    f"force_original_aspect_ratio=increase{cs},"
                    f"crop={width}:{height},{_blur_filter(clip.get('fit'))},"
                    f"eq=brightness=-0.2[bg{i}]")
                filters.append(
                    f"[bb{i}]scale={width}:{height}:"
                    f"force_original_aspect_ratio=decrease{cs}[fg{i}]")
                vf = (f"[bg{i}][fg{i}]overlay=(main_w-overlay_w)/2:"
                      f"(main_h-overlay_h)/2,fps={fps},format={_fmt}")
            elif clip.get("fit") == "cover":
                vf = (f"[{i}:v]{pre},scale={width}:{height}:"
                      f"force_original_aspect_ratio=increase{cs},"
                      f"crop={width}:{height},"
                      f"fps={fps},format={_fmt}")
            else:
                vf = (f"[{i}:v]{pre},scale={width}:{height}:"
                      f"force_original_aspect_ratio=decrease{cs},"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                      f"fps={fps},format={_fmt}")
        elif clip.get("camera"):
            # Camera path on a still (v1.40.0): zoompan on a
            # single input frame generates d frames, exactly
            # like Ken Burns, but along the multi-stop path.
            # Camera wins over ken_burns (branch order) and
            # over default motion; the input loop above skips
            # -loop for camera images for the same reason.
            frames = max(1, int(round(dur * fps)))
            # v1.46.0: zoompan runs at OUTPUT resolution
            # (s=WxH) with a 1x cover pre-scale and no
            # lanczos downscale. The 4x pre-scale / 2x
            # zoompan of v1.41.0 Fix 4 cost ~8.5s/150f vs
            # ~2.0s at output res (board.png, medium/crf
            # 17), and the pixel difference is mean 0.14
            # levels on flat sources (max 20), mean ~1.5
            # at max zoom on a worst-case detailed test
            # pattern. The 4x supersampling bought
            # sharpness on detailed stills, not motion
            # smoothness — zoompan interpolates sub-pixel,
            # and video camera clips already use output-res
            # zoompan. The 1x cover pre-scale is kept: it
            # normalises the source aspect to the output
            # aspect (zoompan would otherwise stretch a
            # mismatched source), and it is a no-op when
            # the source already matches. Framing is
            # unchanged: crop fractions are relative to
            # iw/ih.
            pre = (f"{downscale}scale={width}:{height}:"
                   f"force_original_aspect_ratio=increase,"
                   f"crop={width}:{height}")
            vf = (f"[{i}:v]{pre},"
                  f"{_camera_zoompan_filter(clip['camera'], f'(on/{fps})', width, height, frames, fps)},"
                  f"setpts=PTS-STARTPTS,format={_fmt}")
        elif effective_ken_burns(clip, i, project) not in (None, "none"):
            # v1.37.2: the branch is driven by the EFFECTIVE motion
            # alone. A clip with punch_in has effective motion
            # "none" (punch wins over Ken Burns), so it falls
            # through to the still-image branch below and the
            # punch post-scale is its only motion — never stacked.
            kb = effective_ken_burns(clip, i, project)
            frames = max(1, int(round(dur * fps)))
            # v1.46.0: zoompan at OUTPUT resolution (see the
            # camera branch above for the measurement note).
            # 1x cover pre-scale only (aspect normalisation,
            # no-op when the source already matches); no 4x
            # pre-scale, no lanczos downscale.
            pre = (f"{downscale}scale={width}:{height}:"
                   f"force_original_aspect_ratio=increase,"
                   f"crop={width}:{height}")
            # Gentle drift (v1.37.2, intentional change): the old
            # 1.0->1.5 zoom (50%) read as vibration and grotesque
            # zoom in the 1.37.0 rhythm proof, especially stacked
            # with punch-in (up to 1.8x). Ken Burns is now a
            # subtle 12% drift, eased with a smoothstep curve
            # (slow start, faster middle, slow landing) instead of
            # a mechanical linear ramp, so motion starts and lands
            # softly. s is the smoothstep of the frame progress.
            _p = f"on/{frames}"
            _s = f"({_p})*({_p})*(3-2*({_p}))"
            # v1.40.0: continuity — a same-scene clip whose
            # scales were re-based in validation uses the
            # parameterized z (start + (end-start)*s, which
            # reproduces the legacy expressions at the legacy
            # defaults); otherwise the legacy expressions run
            # byte-identical to before.
            _cs = _ce = None
            if "_kb_start" in clip and "_kb_end" in clip:
                _cs = float(clip["_kb_start"])
                _ce = float(clip["_kb_end"])
            if kb == "zoom_in":
                z = f"{_cs!r}+({_ce!r}-{_cs!r})*{_s}" if _cs is not None else f"1+0.12*{_s}"
                x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif kb == "zoom_out":
                z = f"{_cs!r}+({_ce!r}-{_cs!r})*{_s}" if _cs is not None else f"1.12-0.12*{_s}"
                x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif kb == "pan_left":
                z = f"{_cs!r}" if _cs is not None else "1.12"
                x_expr, y_expr = f"(iw-iw/zoom)*{_s}", "(ih-ih/zoom)/2"
            else:  # pan_right
                z = f"{_cs!r}" if _cs is not None else "1.12"
                x_expr, y_expr = f"(iw-iw/zoom)*(1-{_s})", "(ih-ih/zoom)/2"
            vf = (f"[{i}:v]{pre},zoompan=z='{z}':x='{x_expr}':y='{y_expr}':"
                  f"d={frames}:s={width}x{height}:fps={fps},"
                  f"setpts=PTS-STARTPTS,format={_fmt}")
        elif clip.get("fit") in ("blur", "blur_pad"):
            filters.append(f"[{i}:v]{downscale}split=2[ba{i}][bb{i}]")
            filters.append(
                f"[ba{i}]scale={width}:{height}:"
                f"force_original_aspect_ratio=increase{cs},"
                f"crop={width}:{height},{_blur_filter(clip.get('fit'))},"
                f"eq=brightness=-0.2[bg{i}]")
            filters.append(
                f"[bb{i}]scale={width}:{height}:"
                f"force_original_aspect_ratio=decrease{cs}[fg{i}]")
            vf = (f"[bg{i}][fg{i}]overlay=(main_w-overlay_w)/2:"
                  f"(main_h-overlay_h)/2,fps={fps},format={_fmt}")
        elif clip.get("fit") == "cover":
            vf = (f"[{i}:v]{downscale}scale={width}:{height}:"
                  f"force_original_aspect_ratio=increase{cs},"
                  f"crop={width}:{height},"
                  f"fps={fps},format={_fmt}")
        else:
            vf = (f"[{i}:v]{downscale}scale={width}:{height}:"
                  f"force_original_aspect_ratio=decrease{cs},"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                  f"fps={fps},format={_fmt}")
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
                                 f"force_original_aspect_ratio=increase{cs},"
                                 f"crop={width}:{height}")
                else:
                    seg_scale = (f"scale={width}:{height}:"
                                 f"force_original_aspect_ratio=decrease{cs},"
                                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
                filters.append(f"[{i}:v]{seg_pre},{seg_scale},fps={fps},format={_fmt}[sc{si}]")
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
        # Pro pack: colour grade preset and/or .cube LUT, applied
        # after the clip's other colour filters. Presets are the
        # documented filter combinations in GRADE_PRESETS, not
        # film emulation. A LUT runs through lut3d in RGB: the
        # chain converts to rgb24 for the LUT and back to
        # yuv420p, because lut3d needs RGB input on this build.
        if clip.get("grade") in GRADE_PRESETS:
            vf += "," + GRADE_PRESETS[clip["grade"]]
        if clip.get("lut_resolved") or clip.get("lut"):
            lut_path = clip.get("lut_resolved") or clip["lut"]
            # Backslashes in a LUT path must be doubled inside
            # the filter's single quotes, or FFmpeg reads them as
            # escapes (1.34.0; quote characters are still rejected
            # in validation). On Windows colons must be escaped too.
            lut_esc = _esc_filter_arg(lut_path)
            vf += (f",format={_lutfmt},lut3d=file='{lut_esc}',"
                   f"format={_fmt}")
        # General image treatment (v1.40.0): after colour
        # (grade/LUT), before fades, so fades operate on the
        # treated image. All default off, so old clips render
        # identically. Since v1.46.0 (R3) _treatment_filters
        # returns only the grain; vignette/shadow ride on a
        # pre-rendered 1-frame mask overlaid after the chain
        # closes (below, before vlabels.append) — all three
        # are multiplicative, so the order vs fades does not
        # change the pixels. If mask generation fails, the
        # inline vignette/shadow filters run instead.
        _tr = _treatment_filters(clip.get("treatment"), _fmt, _afmt)
        _tmask = _treatment_mask_path(clip.get("treatment"),
                                      width, height)
        if _tmask is None and _treatment_has_shadow(
                clip.get("treatment")):
            _inline = _treatment_shadow_inline(
                clip.get("treatment"), _fmt, _afmt)
            if _inline:
                _tr = f"{_tr},{_inline}" if _tr else _inline
        if _tr:
            vf += "," + _tr
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
        both_mc = bool(clip.get("mask")) and bool(clip.get("chroma_key_hex"))
        if clip.get("mask") and not both_mc:
            # Geq only changes alpha, and yuv420p has no alpha, so a
            # masked main clip must be composited over black below;
            # the chain stays rgba here on purpose. When chroma key
            # is also present the mask is applied AFTER keying via
            # the alpha-intersection below, never here: applying the
            # mask first would be undone by colorkey, which resets
            # non-keyed alpha to opaque.
            vf += f",format={_afmt}," + _mask_geq(clip["mask"])
        filters.append(f"{vf}[v{i}]")
        vlabel = f"v{i}"
        if clip.get("mask") and not clip.get("chroma_key_hex"):
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={dur}[bgm{i}]")
            filters.append(
                f"[bgm{i}][v{i}]overlay=0:0,format={_fmt}[vout{i}]")
            vlabel = f"vout{i}"
        if clip.get("chroma_key_hex"):
            # Key out the colour, then composite over black: a
            # chroma-keyed main clip shows the background through
            # the keyed area instead of a green rectangle.
            sim = float(clip.get("chroma_similarity", 0.3))
            blend = float(clip.get("chroma_blend", 0.1))
            filters.append(
                f"[v{i}]format={_afmt},"
                f"colorkey=0x{clip['chroma_key_hex']}:similarity={sim}:blend={blend},"
                f"format={_yuvafmt}[vck{i}]")
            keyed_label = f"vck{i}"
            if both_mc:
                # Alpha intersection (Loop 6 round 2): the keyed
                # alpha times the mask factor. alphaextract gives a
                # gray stream at exactly this frame size, so no
                # dimension guessing is involved; geq's lum(X,Y)
                # works on gray streams on this build even though
                # a(X,Y) does not exist. Final alpha is 0 for keyed
                # colour inside the mask, 0 outside the mask, and
                # opaque only for kept content inside the mask.
                filters.append(f"[vck{i}]split=2[vckrgb{i}][vckkey{i}]")
                filters.append(
                    f"[vckkey{i}]alphaextract,"
                    f"geq=lum='lum(X,Y)*({_mask_factor_expr(clip['mask'])})'[vckam{i}]")
                filters.append(f"[vckrgb{i}][vckam{i}]alphamerge[vckm{i}]")
                keyed_label = f"vckm{i}"
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={dur}[bgc{i}]")
            filters.append(
                f"[bgc{i}][{keyed_label}]overlay=0:0,format={_fmt}[vout{i}]")
            vlabel = f"vout{i}"
        if _tmask is not None:
            # v1.46.0 (R3): vignette/shadow as a pre-rendered
            # 1-frame mask + overlay. The mask is a black RGBA
            # frame whose alpha is the combined darkening, so
            # overlaying it multiplies the video by exactly the
            # factor the per-frame vignette filter + shadow geq
            # would apply. Looped for the clip duration; applied
            # to the final clip output (after fades/mask/chroma
            # key — all multiplicative, so the order does not
            # change the pixels). Punch-in below reads vlabel,
            # so it correctly operates on the masked stream.
            _midx = _input_count(argv)
            argv += ["-i", _tmask]
            filters.append(
                f"[{_midx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
                f"trim=end={dur},setpts=PTS-STARTPTS,format=rgba[trm{i}]")
            filters.append(
                f"[{vlabel}][trm{i}]overlay=0:0:eof_action=pass[vt{i}]")
            vlabel = f"vt{i}"
        vlabels.append(vlabel)
        if clip.get("punch_at") is not None:
            # Punch-in (v1.37.0, redefined v1.37.2): a
            # frame-boundary scale jump toward the panel centre
            # at punch_at (clip-local seconds), with no gap — the
            # same stream, scaled up by punch_factor after that
            # time and cropped back to the constant output size.
            # scale eval=frame makes the size time-varying, crop
            # pins the output size, so concat/xfade downstream
            # always see a constant frame size. Dimensions are
            # truncated to even, as yuv420p requires. v1.37.2
            # rule: punch_factor is the TOTAL scale, never a
            # multiplier on Ken Burns — a punched clip never
            # also zooms (effective_ken_burns returns "none" for
            # it), so the stream entering this filter is
            # un-zoomed: 1.0x before punch_at, punch_factor
            # after, held to clip end.
            _pa = float(clip["punch_at"])
            _pf = float(clip.get("punch_factor", 1.20))
            _sw = (f"trunc(if(lt(t,{_pa!r}),{width},{width}*{_pf})/2)*2")
            _sh = (f"trunc(if(lt(t,{_pa!r}),{height},{height}*{_pf})/2)*2")
            filters.append(
                f"[{vlabel}]scale=w='{_sw}':h='{_sh}':eval=frame,"
                f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,"
                f"fps={fps},format={_fmt}[vp{i}]")
            vlabels[-1] = f"vp{i}"
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
    # Delta fix (found by the delta proof render): clip chains
    # can carry different link SARs — scale with
    # force_original_aspect_ratio rounds integer dimensions
    # and compensates with SAR (e.g. 5120:5121 for a 640x360
    # source covering 720x1280), while zoompan outputs square
    # pixels — and concat/xfade refuse to join mismatched
    # SARs. Every join input is therefore normalised to
    # square pixels first (setsar is metadata-only; pixels
    # are untouched). Single-clip projects bypass the join
    # entirely, so their chains are unaffected.
    # Multi-track (v1.44.0, F6a): only track 0 clips join
    # the base timeline; track 1 clips are composited over it
    # below. vjoin is keyed by clip index (labels are per-clip),
    # so for a track-0-only project this loop is identical to
    # before, filter for filter.
    _joined_idx = [i for i, _c in enumerate(clips) if _c.get("track", 0) == 0]
    vjoin = {}
    for i in _joined_idx:
        filters.append(f"[{vlabels[i]}]setsar=1[vs{i}]")
        vjoin[i] = f"vs{i}"
    vcur_chain = vjoin[_joined_idx[0]]
    cur_dur = clips[_joined_idx[0]]["duration"]
    _custom_cuts = []
    for i in _joined_idx[1:]:
        dur = clips[i]["duration"]
        if clips[i].get("transition") in XFADE_TRANSITIONS:
            xname = XFADE_TRANSITIONS[clips[i]["transition"]]
            xtd = float(clips[i].get("transition_duration", 0.5))
            filters.append(
                f"[{vcur_chain}][{vjoin[i]}]xfade=transition={xname}:duration={xtd:.3f}:"
                f"offset={cur_dur - xtd:.3f}[x{i}]")
            vcur_chain = f"x{i}"
            cur_dur += dur - xtd
        else:
            if clips[i].get("transition") in CUSTOM_TRANSITIONS:
                _custom_cuts.append((cur_dur, clips[i]["transition"],
                                     float(clips[i].get("transition_duration", 0.5)), i))
            filters.append(f"[{vcur_chain}][{vjoin[i]}]concat=n=2:v=1:a=0[c{i}]")
            vcur_chain = f"c{i}"
            cur_dur += dur
    acur_chain = f"a{_joined_idx[0]}"
    cur_dur_a = clips[_joined_idx[0]]["duration"]
    for i in _joined_idx[1:]:
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
    # Custom transitions (v1.44.0, F5): a post-join effect
    # pass on the joined base video, before scrims/overlays/texts
    # (so scrims and graphics sit above the effect, as they do
    # above plain cuts). Each effect straddles its cut in the
    # window [cut - td/2, cut + td/2]; windows cannot overlap
    # (validation: td < both clip durations, so the gap between
    # adjacent windows is dur - td_left/2 - td_right/2 > 0), so
    # the effects chain sequentially, each gated to its window
    # and identity outside it. The join itself is plain concat,
    # so total duration is unchanged (unlike xfade). Audio is
    # plain concat too, by design: an acrossfade consumes td of
    # duration (output = len1 + len2 - d), which would shift all
    # later audio early against the concat video join; the audio
    # therefore cuts hard at the cut point while the visual
    # effect straddles it. zoom_out does NOT use pad (pad after
    # a size-changing scale misplaces the picture and fills green
    # on this FFmpeg 8.1 build -- verified 2026-11-06); it
    # composites the scaled stream onto a finite black canvas
    # with overlay instead. No-op when no clip carries a custom
    # transition, so old projects render byte-identically.
    for _cut, _kind, _td, _ci in _custom_cuts:
        _w0 = _cut - _td / 2.0
        _w1 = _cut + _td / 2.0
        _u = f"(t-{_w0:.3f})/{_td:.3f}"
        _win = f"between(t,{_w0:.3f},{_w1:.3f})"
        if _kind == "zoom_out":
            # Scale pulse dipping to 0.75x at the cut (sine bump,
            # 1.0 at both window edges) over a black canvas.
            _s = f"if({_win},1-0.25*sin(3.14159265*{_u}),1)"
            filters.append(
                f"[{vcur_chain}]scale=w='trunc(iw*({_s})/2)*2':"
                f"h='trunc(ih*({_s})/2)*2':eval=frame[zsc{_ci}]")
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:"
                f"d={total:.3f}[zbg{_ci}]")
            filters.append(
                f"[zbg{_ci}][zsc{_ci}]overlay=x='(main_w-overlay_w)/2':"
                f"y='(main_h-overlay_h)/2':eof_action=pass,"
                f"fps={fps},format={_fmt}[cz{_ci}]")
            vcur_chain = f"cz{_ci}"
        elif _kind == "whip_pan":
            # The frame slides sideways with motion blur: pad 2x
            # wide (content left, black right), crop window
            # slides 0 -> W -> 0 across the window (clip A exits
            # left, black at the cut, clip B enters from the
            # right), gblur gated to the window.
            _x = f"if({_win},{width}*(1-abs(2*{_u}-1)),0)"
            filters.append(
                f"[{vcur_chain}]pad={2 * width}:{height}:0:0:black,"
                f"crop={width}:{height}:x='{_x}':y=0,"
                f"gblur=sigma=8:enable='{_win}'[cw{_ci}]")
            vcur_chain = f"cw{_ci}"
        elif _kind == "glitch":
            # Two stepped rgbashift slices (flicker) plus temporal
            # noise across the window; constant params, enable
            # windows do the timing (no per-frame expressions).
            filters.append(
                f"[{vcur_chain}]rgbashift=rh=10:rv=0:gh=0:gv=0:bh=-10:bv=0:"
                f"enable='between(t,{_w0:.3f},{_w0 + 0.4 * _td:.3f})',"
                f"rgbashift=rh=-8:rv=2:gh=0:gv=0:bh=8:bv=-2:"
                f"enable='between(t,{_w0 + 0.4 * _td:.3f},{_w0 + 0.7 * _td:.3f})',"
                f"noise=alls=25:allf=t:enable='{_win}'[cg{_ci}]")
            vcur_chain = f"cg{_ci}"
        elif _kind == "light_leak":
            # A warm radial blob (geq alpha falloff from the frame
            # centre) drifting left -> right across the window,
            # overlaid with an enable gate.
            _lx = f"if({_win},{-0.35 * width:.3f}+{0.7 * width:.3f}*{_u},0)"
            _a = (f"255*0.6*clip(1-sqrt(pow(X-{width / 2:.3f},2)"
                  f"+pow(Y-{height / 2:.3f},2))/({0.7 * width:.3f}),0,1)")
            filters.append(
                f"color=c=0xff9500:s={width}x{height}:r={fps},"
                f"format={_afmt},geq=r='255':g='150':b='0':a='{_a}'[leak{_ci}]")
            filters.append(
                f"[{vcur_chain}][leak{_ci}]overlay=x='{_lx}':y=0:"
                f"enable='{_win}':eof_action=pass[cl{_ci}]")
            vcur_chain = f"cl{_ci}"
    # Multi-track compositing (v1.44.0, F6a): each track 1
    # clip is composited full-frame over the joined base video
    # at its absolute start (its chain already ends at PTS 0,
    # so setpts shifts it to timeline time; enable gates the
    # window). This is part of the base picture: scrims,
    # treatment and adjustments below apply to the composited
    # result, while overlays/texts/motion sit above it.
    for _ti, _tc in enumerate(clips):
        if _tc.get("track", 0) != 1:
            continue
        _ts = float(_tc.get("start", 0.0))
        _te = _ts + float(_tc["duration"])
        filters.append(f"[{vlabels[_ti]}]setsar=1[vt{_ti}]")
        filters.append(f"[vt{_ti}]setpts=PTS+{_ts}/TB[vtp{_ti}]")
        filters.append(
            f"[{vcur_chain}][vtp{_ti}]overlay=0:0:"
            f"enable='between(t,{_ts:.3f},{_te:.3f})':eof_action=pass[vtc{_ti}]")
        vcur_chain = f"vtc{_ti}"
    # Gradient scrims (v1.36.0): optional project-level top/bottom
    # black gradients, the standard Shorts readability technique.
    # A scrim is a black colour source whose alpha ramps from full
    # opacity at the frame edge to transparent over `depth` of the
    # height (geq, same alpha-expression pattern as the mask geq).
    # Scrims sit OVER the clips and UNDER overlays and texts (they
    # are applied here, before both), so text over a bright panel
    # stays readable without dimming the middle of the frame.
    # Default off: scrims absent (or false) changes nothing.
    scrims = project.get("scrims") or {}
    if isinstance(scrims, dict):
        for _side in ("top", "bottom"):
            _sv = scrims.get(_side)
            if not _sv:
                continue
            if _sv is True:
                _op, _depth = 0.6, 0.35
            else:
                _op = float(_sv.get("opacity", 0.6))
                _depth = float(_sv.get("depth", 0.35))
            _dpx = max(1, int(height * _depth))
            if _side == "bottom":
                _aexpr = f"clip((Y-(H-{_dpx}))/{_dpx},0,1)*255*{_op}"
            else:
                _aexpr = f"clip(({_dpx}-Y)/{_dpx},0,1)*255*{_op}"
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps},format={_afmt},"
                f"geq=r='0':g='0':b='0':a='{_aexpr}'[scrim{_side}]")
            filters.append(
                f"[{vcur_chain}][scrim{_side}]overlay=0:0:"
                f"eof_action=pass[vscrim{_side}]")
            vcur_chain = f"vscrim{_side}"
    # Overlays (Loop 5 round 1): picture-in-picture tracks above the
    # main timeline. Each overlay is scaled to a fraction of the
    # output width, delayed to its timeline start, and composited
    # with overlay= only while it is on screen (enable between
    # start and end, eof_action=pass so the main timeline continues
    # after the overlay stream ends). Overlay audio is mixed only
    # when the overlay sets volume > 0 (default 0.0, muted, for
    # backward compatibility); see the audio section below.
    OVERLAY_POS = {
        "top_left": ("20", "20"),
        "top_center": ("(main_w-overlay_w)/2", "20"),
        "top_right": ("main_w-overlay_w-20", "20"),
        "center": ("(main_w-overlay_w)/2", "(main_h-overlay_h)/2"),
        "bottom_left": ("20", "main_h-overlay_h-20"),
        "bottom_center": ("(main_w-overlay_w)/2", "main_h-overlay_h-20"),
        "bottom_right": ("main_w-overlay_w-20", "main_h-overlay_h-20"),
    }
    # General image treatment, project level (v1.40.0): on the
    # joined timeline after scrims, before overlays/texts, so
    # graphics stay clean and grain covers the whole picture.
    # Since v1.46.0 (R3) _treatment_filters returns only the
    # grain; vignette/shadow ride on a pre-rendered 1-frame
    # mask overlaid on the joined timeline (same factor, one
    # overlay instead of a per-frame filter/geq). If mask
    # generation fails, the inline filters run instead.
    _ptr = _treatment_filters(project.get("treatment"), _fmt, _afmt)
    if _ptr:
        filters.append(f"[{vcur_chain}]{_ptr}[vtr]")
        vcur_chain = "vtr"
    _ptmask = _treatment_mask_path(project.get("treatment"),
                                   width, height)
    if _ptmask is not None:
        _pmidx = _input_count(argv)
        argv += ["-i", _ptmask]
        filters.append(
            f"[{_pmidx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
            f"trim=end={total},setpts=PTS-STARTPTS,format=rgba[ptrm]")
        filters.append(
            f"[{vcur_chain}][ptrm]overlay=0:0:eof_action=pass[vptr]")
        vcur_chain = "vptr"
    elif _treatment_has_shadow(project.get("treatment")):
        _pinline = _treatment_shadow_inline(
            project.get("treatment"), _fmt, _afmt)
        if _pinline:
            filters.append(f"[{vcur_chain}]{_pinline}[vtr]")
            vcur_chain = "vtr"
    # Adjustment layers (v1.44.0, F6b): grade/effects windows
    # over the composited base video only (overlays/texts/motion
    # sit above, unaffected). Split/trim/overlay: the window is
    # trimmed (timestamps kept, so it aligns), graded/effected,
    # and overlaid back with an enable gate. Windows apply in
    # list order; overlapping windows compound.
    for _ai, _adj in enumerate(info.get("adjustments") or []):
        _as = float(_adj["start"]); _ae = float(_adj["end"])
        _ag = ""
        if _adj.get("grade") in GRADE_PRESETS:
            _ag += "," + GRADE_PRESETS[_adj["grade"]]
        _ag += _effect_chain(_adj.get("effects") or {})
        filters.append(f"[{vcur_chain}]split=2[vbase{_ai}][vgrd{_ai}]")
        filters.append(
            f"[vgrd{_ai}]trim=start={_as:.3f}:end={_ae:.3f}{_ag},"
            f"fps={fps},format={_fmt}[vgw{_ai}]")
        filters.append(
            f"[vbase{_ai}][vgw{_ai}]overlay=0:0:"
            f"enable='between(t,{_as:.3f},{_ae:.3f})':eof_action=pass[vadj{_ai}]")
        vcur_chain = f"vadj{_ai}"
    vcur = vcur_chain
    for si, (ov, seg_start, seg_end, idx, _audio_idx) in enumerate(overlay_segments):
        ostart = float(ov["start"])
        odur = float(ov["duration"])
        kfs = ov.get("keyframes_resolved")
        abs_start = ostart + seg_start
        abs_end = ostart + seg_end
        if ov["kind"] == "video":
            if kfs and len({f["scale"] for f in kfs}) > 1:
                # Scale keyframes show the trimmed source limited to
                # the overlay duration (looping excluded by
                # validation for this combination); the scale itself
                # now varies per frame via eval=frame below.
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
            # Continuous scaling (Loop 6 round 3): one scale filter
            # with eval=frame, width/height interpolated linearly on
            # frame time t (overlay-relative: the chain resets PTS
            # before this point), rounded to even numbers with
            # trunc(.../2)*2 to keep chroma formats valid. The
            # overlay filter accepts per-frame sizes, so the
            # varying stream flows through untouched — BUT only if
            # format=rgba comes BEFORE the scale: a format filter
            # after a varying scale pins the stream to its first
            # frame's size (verified: edges stuck at the t=0 size
            # with format after, growing smoothly with format
            # before). The source size is constant, so format
            # before the scale pins nothing.
            w_expr = _interp_expr_eased([(f["time"], f["scale"], f.get("easing", "linear")) for f in kfs], "t")
            _probe = ov.get("probe") or {}
            _iw, _ih = _probe.get("width"), _probe.get("height")
            if _iw and _ih:
                h_expr = f"trunc(({w_expr})*{width}*{_ih}/{_iw}/2)*2"
            else:
                h_expr = f"trunc(({w_expr})*{width}/2)*2"
            chain += (f",format={_afmt},"
                      f"scale=w='trunc(({w_expr})*{width}/2)*2':"
                      f"h='{h_expr}':eval=frame")
        else:
            ow = _even(float(ov["size"]) * width)
            chain += f",scale={ow}:-2,format={_afmt}"
        if ov.get("chroma_key_hex"):
            sim = float(ov.get("chroma_similarity", 0.3))
            blend = float(ov.get("chroma_blend", 0.1))
            chain += (f",colorkey=0x{ov['chroma_key_hex']}:similarity={sim}:blend={blend}")
        op_vary = bool(kfs) and len({f["opacity"] for f in kfs}) > 1
        # Alpha intersection (Loop 6 round 2): when chroma key meets
        # a mask (or varying opacity), the old chain replaced the
        # alpha channel — first the mask geq, then the opacity geq —
        # so keyed colour reappeared inside the mask and opacity
        # keyframes destroyed keying. Instead, take the keyed alpha
        # apart (alphaextract, exact frame size, no guessing),
        # multiply it by the mask factor and/or the opacity factor
        # in a gray geq (lum(X,Y) works on this build; a(X,Y) does
        # not), and merge it back (alphamerge). Final alpha is then
        # keyed-alpha x mask x opacity, exactly the intersection.
        if ov.get("chroma_key_hex") and (ov.get("mask") or op_vary):
            filters.append(chain + f"[ovk{si}]")
            filters.append(f"[ovk{si}]split=2[ovrgb{si}][ovkeya{si}]")
            factors = []
            if ov.get("mask"):
                factors.append(f"({_mask_factor_expr(ov['mask'])})")
            if op_vary:
                op_expr = _interp_expr_eased([(f["time"], f["opacity"], f.get("easing", "linear")) for f in kfs],
                                       f"({seg_start!r}+N/{fps})")
                factors.append(f"({op_expr})")
            alpha_expr = "lum(X,Y)" + "".join(f"*{f}" for f in factors)
            filters.append(
                f"[ovkeya{si}]alphaextract,"
                f"geq=lum='{alpha_expr}'[ovam{si}]")
            filters.append(f"[ovrgb{si}][ovam{si}]alphamerge[ovfin{si}]")
            cur = f"ovfin{si}"
            eff = _effect_chain(ov)
            if eff:
                filters.append(f"[ovfin{si}]{eff}[ove{si}]")
                cur = f"ove{si}"
            if not op_vary and float(ov.get("opacity", 1.0)) < 1.0:
                # Constant opacity multiplies alpha (safe, unlike geq).
                filters.append(
                    f"[{cur}]colorchannelmixer=aa={float(ov['opacity'])}[ovop{si}]")
                cur = f"ovop{si}"
            filters.append(f"[{cur}]setpts=PTS+{abs_start}/TB[ov{si}]")
        else:
            if ov.get("mask"):
                chain += "," + _mask_geq(ov["mask"])
            chain += _effect_chain(ov)
            if op_vary:
                # Smooth opacity via geq: N counts frames of this segment
                # stream (fps-normalised), so overlay time is
                # seg_start + N/fps. Verified against FFmpeg: geq's T
                # does not track segment time, N does.
                op_expr = _interp_expr_eased([(f["time"], f["opacity"], f.get("easing", "linear")) for f in kfs],
                                       f"({seg_start!r}+N/{fps})")
                chain += (f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                          f"a='255*({op_expr})'")
            elif float(ov.get("opacity", 1.0)) < 1.0:
                chain += f",colorchannelmixer=aa={float(ov['opacity'])}"
            chain += f",setpts=PTS+{abs_start}/TB[ov{si}]"
            filters.append(chain)
        # Overlay dressing (v1.36.0): optional border, rounded
        # corners and drop shadow, all default off so old overlays
        # render identically. Border is drawn first so the
        # rounding mask rounds the border corners too; the mask
        # uses the same alphaextract/geq/alphamerge pattern as the
        # chroma-key intersection. Dressing applies to the
        # setpts-shifted stream, so timing is untouched.
        ov_label = f"ov{si}"
        bw_dress = int(ov.get("border_width", 0) or 0)
        if bw_dress > 0:
            filters.append(
                f"[{ov_label}]drawbox=x=0:y=0:w=iw:h=ih:"
                f"color={_ff_color(ov.get('border_color', 'black'))}:"
                f"t={bw_dress}[ovb{si}]")
            ov_label = f"ovb{si}"
        cr_dress = int(ov.get("corner_radius", 0) or 0)
        if cr_dress > 0:
            filters.append(f"[{ov_label}]split=2[ovdrgb{si}][ovdkey{si}]")
            filters.append(
                f"[ovdkey{si}]alphaextract,"
                f"geq=lum='{_rounded_alpha_factor(cr_dress)}*lum(X,Y)'[ovdm{si}]")
            filters.append(f"[ovdrgb{si}][ovdm{si}]alphamerge[ovdr{si}]")
            ov_label = f"ovdr{si}"
        if ov.get("track_file"):
            # Follow-track (v1.44.0, F2): the overlay's top-left
            # corner is sampled from the AgentMedia track every
            # frame (see _track_overlay_expr).
            x_expr, y_expr = _track_overlay_expr(
                ov, info, width, height, ostart)
        elif kfs:
            # Smooth position: overlay x/y expressions use main
            # timeline time t (verified: overlay honours t).
            x_expr = _interp_expr_eased([(ostart + f["time"], f["x"], f.get("easing", "linear")) for f in kfs], "t")
            y_expr = _interp_expr_eased([(ostart + f["time"], f["y"], f.get("easing", "linear")) for f in kfs], "t")
        else:
            px, py = OVERLAY_POS[ov.get("position", "bottom_right")]
            x_expr = str(ov["x"]) if "x" in ov else px
            y_expr = str(ov["y"]) if "y" in ov else py
        # Parallax (v1.40.0): an overlay with depth > 0 drifts
        # with the camera/background motion of the clip under
        # its start — foreground faster than background, depth 1
        # glued to the content, depth 0 the old screen-pinned
        # behaviour. The term is a translation in output pixels:
        # for a camera path it is W*z(tau)*(fx0-fx(tau))
        # horizontally (and H analogously vertically), where
        # fx/z are the path's interpolated centre/scale at
        # clip-local time tau = t - clip_start; for a Ken Burns
        # pan it is the closed form -/+depth*W*0.12*smoothstep
        # (tau/dur) (a pan moves content by exactly 0.12*W at
        # z=1.12). Zoom-only motion has a constant centre, so
        # it yields no positional parallax, by design. The term
        # adds to keyframed positions too, and flows into the
        # drop shadow below (which reuses x_expr/y_expr).
        # Outside the clip (an overlay spanning past its clip)
        # the camera interpolation holds its last value and
        # the pan u clamps to [0,1], so the term freezes
        # instead of extrapolating. The clip under an overlay
        # is the last clip whose start is <= the overlay start
        # (during an xfade overlap that is the incoming, on-top
        # clip).
        _depth = float(ov.get("depth", 0) or 0)
        if _depth > 0:
            _starts = info.get("clip_starts") or []
            _ci = 0
            for _j, _cs in enumerate(_starts):
                if _cs <= ostart:
                    _ci = _j
            if clips and _ci < len(clips):
                _cclip = clips[_ci]
                _cstart = _starts[_ci] if _ci < len(_starts) else 0.0
                _tau = f"(t-{_cstart!r})"
                _term_x = _term_y = None
                if _cclip.get("camera"):
                    _cam = _cclip["camera"]
                    _fx = _camera_interp_expr(_cam, "x", _tau)
                    _fy = _camera_interp_expr(_cam, "y", _tau)
                    _fz = _camera_interp_expr(_cam, "scale", _tau)
                    _term_x = f"{_depth!r}*{width}*({_fz})*({float(_cam[0]['x'])!r}-({_fx}))"
                    _term_y = f"{_depth!r}*{height}*({_fz})*({float(_cam[0]['y'])!r}-({_fy}))"
                else:
                    _kb = effective_ken_burns(_cclip, _ci, project)
                    if _kb in ("pan_left", "pan_right"):
                        _cdur = float(_cclip["duration"])
                        _u = f"min(max(({_tau})/({_cdur!r}),0),1)"
                        _s = f"({_u})*({_u})*(3-2*({_u}))"
                        _sign = "-" if _kb == "pan_left" else ""
                        _term_x = f"{_sign}{_depth!r}*{width}*0.12*({_s})"
                if _term_x is not None:
                    x_expr = f"({x_expr})+({_term_x})"
                if _term_y is not None:
                    y_expr = f"({y_expr})+({_term_y})"
        # Drop shadow: a black, blurred copy of the dressed stream
        # composited underneath at an offset. Active only when an
        # offset or a blur is set (and opacity > 0); setting only
        # shadow_opacity does nothing, by design — see validation.
        sh_dx = int(ov.get("shadow_dx", 0) or 0)
        sh_dy = int(ov.get("shadow_dy", 0) or 0)
        sh_blur = float(ov.get("shadow_blur", 0) or 0)
        sh_op = float(ov.get("shadow_opacity", 0.5))
        sh_active = (sh_dx != 0 or sh_dy != 0 or sh_blur > 0) and sh_op > 0
        if sh_active:
            filters.append(f"[{ov_label}]split=2[ovm{si}][ovsh{si}]")
            sh_chain = f"[ovsh{si}]lutrgb=r=0:g=0:b=0"
            if sh_blur > 0:
                sh_chain += f",gblur=sigma={sh_blur}"
            if sh_op < 1.0:
                sh_chain += f",colorchannelmixer=aa={sh_op}"
            sh_chain += f"[ovshb{si}]"
            filters.append(sh_chain)
            filters.append(
                f"[{vcur}][ovshb{si}]overlay="
                f"x='({x_expr}){sh_dx:+d}':y='({y_expr}){sh_dy:+d}':"
                f"eof_action=pass:enable='between(t,{abs_start},{abs_end})'[vsh{si}]")
            vcur = f"vsh{si}"
            ov_label = f"ovm{si}"
        filters.append(
            f"[{vcur}][{ov_label}]overlay=x='{x_expr}':y='{y_expr}':"
            f"eof_action=pass:enable='between(t,{abs_start},{abs_end})'[vov{si}]")
        vcur = f"vov{si}"
    # Animated stroke diagrams (v1.38.0): the draw video holds
    # only the draw frames, so the hold after the draw ends is
    # a freeze on the LAST frame, not a replay: loop with
    # size=1 at the last frame index repeats that one frame
    # forever (frames before it pass through once), then trim
    # cuts the stream to the display window. Looping the whole
    # draw sequence (the video-overlay pattern) would RE-DRAW
    # the diagram after every draw window — verified by eye
    # on the example (a finished basket reverted to its rim).
    # Placement mirrors overlays: size is a fraction of the
    # output width, aspect from the diagram canvas, preset
    # position or per-axis x/y.
    _card_by_id = {id(dg): cidx for dg, cidx in card_segments}
    for di, (dg, idx) in enumerate(diagram_segments):
        dstart = float(dg["start"])
        dend = float(dg["end"])
        disp_dur = dend - dstart
        draw_frames = max(1, int(round(float(dg["draw"]) * fps)))
        dw = _even(float(dg["size"]) * width)
        # Max diagram hold (v1.42.0, Fix 4a): after the draw
        # the diagram holds for hold_seconds (default 8.0) and
        # then fades out over 1.0s, on the diagram AND its card.
        # The fade sits after the timeline setpts, so its start
        # is in timeline seconds. When the window ends before
        # the fade would start, no fade is added.
        _fade_start = dstart + float(dg["draw"]) + float(
            dg.get("hold_seconds", 8.0))
        _fade = (f",fade=t=out:st={_fade_start}:d=1.0:alpha=1"
                 if _fade_start < dend else "")
        filters.append(
            f"[{idx}:v]fps={fps},loop=loop=-1:size=1:start={draw_frames - 1},"
            f"trim=end={disp_dur},setpts=PTS-STARTPTS,"
            f"scale={dw}:-2,format={_afmt},setpts=PTS+{dstart}/TB{_fade}[dg{di}]")
        px, py = OVERLAY_POS[dg.get("position", "bottom_right")]
        x_expr = str(dg["x"]) if "x" in dg else px
        y_expr = str(dg["y"]) if "y" in dg else py
        # v1.41.0 Fix 6: the static card goes down FIRST, at
        # the same place and size as its diagram (the card PNG
        # shares the diagram canvas, so dw:-2 matches exactly),
        # then the animated diagram draws on top of it.
        # Glassmorphism (v1.45.0, R7): when the diagram has a
        # card with backdrop_blur (the default), a frosted card
        # goes down BEFORE the card image, so the blurred
        # background shows through the card's 0.45 opacity.
        if id(dg) in _card_by_id:
            cidx = _card_by_id[id(dg)]
            if dg.get("backdrop_blur", True):
                _cbx, _cby, _cbw, _cbh = _diagram_card_rect(
                    dg, width, height, dw)
                vcur = _emit_glass_card(filters, vcur, 1000 + di,
                                        _cbx, _cby, _cbw, _cbh,
                                        20, fps, _afmt, dstart, dend,
                                        argv,
                                        fade_start=(_fade_start
                                                    if _fade_start < dend
                                                    else None))
            filters.append(
                f"[{cidx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
                f"trim=end={disp_dur},setpts=PTS-STARTPTS,"
                f"scale={dw}:-2,format={_afmt},setpts=PTS+{dstart}/TB{_fade}[cdg{di}]")
            filters.append(
                f"[{vcur}][cdg{di}]overlay=x='{x_expr}':y='{y_expr}':"
                f"eof_action=pass:enable='between(t,{dstart},{dend})'[vcdg{di}]")
            vcur = f"vcdg{di}"
        filters.append(
            f"[{vcur}][dg{di}]overlay=x='{x_expr}':y='{y_expr}':"
            f"eof_action=pass:enable='between(t,{dstart},{dend})'[vdg{di}]")
        vcur = f"vdg{di}"
    # Adapted template diagrams (v1.45.0, R4b-3): no diagram
    # video (the motion element renders instead), but the
    # backdrop card still composites under the motion element
    # at the diagram's place and size, with the same hold+fade.
    # This runs BEFORE the motion compositing below, so the
    # motion element lands on top of its card.
    _acard = 0
    for dg, cidx in card_segments:
        if dg.get("_video"):
            continue
        dstart = float(dg["start"])
        dend = float(dg["end"])
        disp_dur = dend - dstart
        dw = _even(float(dg["size"]) * width)
        _fade_start = dstart + float(dg["draw"]) + float(
            dg.get("hold_seconds", 8.0))
        _fade = (f",fade=t=out:st={_fade_start}:d=1.0:alpha=1"
                 if _fade_start < dend else "")
        # Glassmorphism (v1.45.0, R7): a frosted card under
        # the adapted diagram's backdrop card (same as the
        # stroke-diagram card above; the adapted card's canvas
        # IS the display size, so the rect matches exactly).
        if dg.get("backdrop_blur", True):
            _cbx, _cby, _cbw, _cbh = _diagram_card_rect(
                dg, width, height, dw)
            vcur = _emit_glass_card(filters, vcur, 2000 + _acard,
                                    _cbx, _cby, _cbw, _cbh,
                                    20, fps, _afmt, dstart, dend,
                                    argv,
                                    fade_start=(_fade_start
                                                if _fade_start < dend
                                                else None))
        filters.append(
            f"[{cidx}:v]fps={fps},loop=loop=-1:size=1:start=0,"
            f"trim=end={disp_dur},setpts=PTS-STARTPTS,"
            f"scale={dw}:-2,format={_afmt},setpts=PTS+{dstart}/TB{_fade}[cdga{_acard}]")
        px, py = OVERLAY_POS[dg.get("position", "bottom_right")]
        x_expr = str(dg["x"]) if "x" in dg else px
        y_expr = str(dg["y"]) if "y" in dg else py
        filters.append(
            f"[{vcur}][cdga{_acard}]overlay=x='{x_expr}':y='{y_expr}':"
            f"eof_action=pass:enable='between(t,{dstart},{dend})'[vcdga{_acard}]")
        vcur = f"vcdga{_acard}"
        _acard += 1
    # Motion graphics (v1.43.0): each element video is its
    # keyframe-union bbox, rendered at full resolution and scaled
    # here to the (possibly preview-scaled) bbox, then overlaid
    # at the scaled bbox origin for exactly its window.
    # Layering: ABOVE diagrams/cards, BELOW texts and captions
    # (the ASS/drawtext stages come later), so captions stay
    # readable over motion elements.
    for mi, (el, idx) in enumerate(motion_segments):
        mstart = float(el["start"])
        mend = float(el["end"])
        mdur = mend - mstart
        mbx, mby, mbw, mbh = el["_bbox"]
        sbx = int(round(mbx * scale_factor))
        sby = int(round(mby * scale_factor))
        sbw = max(1, int(round(mbw * scale_factor)))
        sbh = max(1, int(round(mbh * scale_factor)))
        # Glassmorphism (v1.45.0, R7): a frosted backdrop-blur
        # card behind the chart, ON by default (backdrop_blur).
        # It is composited BEFORE the motion element, so the
        # chart draws on top of the frosted card. The card uses
        # the element's (scaled) bbox; the radius is 20.
        # v1.45.0, R4b-2: fade_out fades the element's video over
        # fade_out seconds, starting at fade_start when given,
        # else at end - fade_out (so it completes exactly at the
        # window end). The fade sits after setpts, so its start
        # is in timeline seconds — the same pattern as the
        # diagram fade. The derived labels fade over the same
        # window (handled in _motion_derived_texts, v1.45.0 R12
        # fix). The glass card fades with the element (same
        # window) via fade_start below.
        _mfade_start = None
        if el.get("fade_out"):
            _fo = float(el["fade_out"])
            _mfade_start = (float(el["fade_start"])
                            if el.get("fade_start") is not None
                            else mend - _fo)
        if el.get("backdrop_blur", True) and el.get("kind") in (
                "bars", "donut", "pie", "line_chart",
                "comparison_cards"):
            vcur = _emit_glass_card(filters, vcur, mi, sbx, sby,
                                    sbw, sbh, 20, fps, _afmt,
                                    mstart, mend, argv,
                                    fade_start=_mfade_start)
        _mfade = ""
        if el.get("fade_out"):
            _fo = float(el["fade_out"])
            _fs = _mfade_start
            _mfade = f",fade=t=out:st={_fs}:d={_fo}:alpha=1"
        # Performance (v1.45.0, R12 / Extension #2): the motion
        # video is already rendered at the bbox size (mbw, mbh),
        # so when the scaled size equals it (scale_factor == 1,
        # the full-resolution render) the scale is a no-op and
        # is skipped — it still costs a full-frame pass. The
        # format conversion stays (it is needed for the alpha).
        _mscale = (f"scale={sbw}:{sbh},"
                   if (sbw != mbw or sbh != mbh) else "")
        filters.append(
            f"[{idx}:v]fps={fps},trim=end={mdur},setpts=PTS-STARTPTS,"
            f"{_mscale}format={_afmt},setpts=PTS+{mstart}/TB{_mfade}[mo{mi}]")
        filters.append(
            f"[{vcur}][mo{mi}]overlay=x={sbx}:y={sby}:"
            f"eof_action=pass:enable='between(t,{mstart},{mend})'[vmo{mi}]")
        vcur = f"vmo{mi}"
    # Text overlays, then captions, on the concatenated video.
    # Text goes through files (see note above), created here and cleaned
    # up by render_project after ffmpeg exits.
    font = info.get("font") or find_font()
    # Motion-graphics labels (v1.43.0, Round 2): chart value /
    # category labels are derived drawtext items (see
    # _motion_derived_texts), so they join the text list below.
    _motion_texts = _motion_derived_texts(info, width, height)
    all_overlays = (project.get("texts") or []) + (info.get("captions", project.get("captions") or [])) + _motion_texts
    text_dir = None
    if all_overlays:
        text_dir = tempfile.mkdtemp(prefix="agentcut-text-")
    overlay_index = 0
    merged_captions = info.get("captions", project.get("captions") or [])
    # Word reveal: one text becomes cumulative prefixes, staggered
    # across its window in proportion to word length, so the full
    # text is visible by the window end. Prefixes (not characters)
    # are the unit on purpose: Arabic letters join, so a per-letter
    # reveal would break joining; word reveal keeps every word
    # intact in every language. Fades are dropped on reveal
    # prefixes (the reveal itself is the entrance).
    render_items = []
    ass_events = []
    # Caption wrap settings (v1.36.0), hoisted here so the
    # reveal expansion (R4c-3b) can wrap the full text once
    # before it splits into word prefixes; the wrapping stage
    # below reuses them.
    cap_maxw = float(project.get("caption_max_width", 0.8))
    cap_spacing = float(project.get("caption_line_spacing", 1.35))
    # Multi-line block ids (v1.45.0, R4c-3): a counter shared
    # by the reveal expansion and the wrapping stage, so a
    # reveal prefix block and a wrapped block never collide.
    _block_seq = 0
    # Collision-proof layout (v1.41.0): validate computed
    # overlap_moves for keyword pops; apply them to a copy
    # of the project texts so the render matches the
    # validated layout (the project itself is never mutated).
    # Zone moves (v1.42.0, Fix 3) apply AFTER overlap moves,
    # so the zone wins on conflict.
    _render_texts = [dict(t) if isinstance(t, dict) else t
                     for t in (project.get("texts") or [])]
    for _mv in ((info.get("overlap_moves") or [])
                + (info.get("zone_moves") or [])):
        _idx = _mv.get("index")
        if (isinstance(_idx, int) and 0 <= _idx < len(_render_texts)
                and isinstance(_render_texts[_idx], dict)):
            _render_texts[_idx]["y"] = _mv["new_y"]
    # Motion labels join after the author's texts, so the
    # overlap/zone moves (indexed by author-text position) are
    # unaffected by them.
    _render_texts = _render_texts + [dict(t) for t in _motion_texts]
    # Title badge (v1.42.0, Fix 4c): a text with shrink_after
    # renders twice — big until shrink_after (with a 0.3s
    # fade-out so the hand-off is a crossfade, not a cut),
    # then as a small top badge for the rest of its window
    # (fade_in 0.3s). Defaults: badge size is half the title
    # size (min 8), badge y is 24 (inside the top safe strip).
    # The badge drops the big window's animation, reveal and
    # word timings (validation forbids the last two); it keeps
    # colour, font and box. Expansion happens here, after the
    # moves (the big window keeps any moved y) and before the
    # reveal/ASS expansion below, so the badge is a plain text.
    _expanded_texts = []
    for _t in _render_texts:
        if not isinstance(_t, dict) or "shrink_after" not in _t:
            _expanded_texts.append(_t)
            continue
        _sa = float(_t["shrink_after"])
        _start = float(_t.get("start", 0))
        _end = float(_t.get("end", total))
        _big = dict(_t)
        _big["end"] = _sa
        _big["fade_out"] = min(0.3, max(0.0, (_sa - _start) / 2))
        _big.pop("shrink_after", None)
        _big.pop("shrink_size", None)
        _big.pop("shrink_y", None)
        _badge = dict(_t)
        _badge["start"] = _sa
        _badge["size"] = _t.get("shrink_size") if _t.get(
            "shrink_size") is not None else max(
                8, int(float(_t.get("size", 48)) // 2))
        _badge["y"] = _t.get("shrink_y", 24)
        _badge.pop("position", None)
        _badge["fade_in"] = 0.3
        # The badge keeps the project's fade_out (it owns the
        # window end); only the big window's fade-out is the
        # 0.3s crossfade above.
        for _k in ("animation", "animation_duration", "reveal",
                   "word_timings", "highlight_color", "highlight_mode",
                   "shrink_after", "shrink_size", "shrink_y"):
            _badge.pop(_k, None)
        _expanded_texts.append(_big)
        _expanded_texts.append(_badge)
    _render_texts = _expanded_texts
    for _item, _is_cap in ([(t, False) for t in _render_texts]
                           + [(c, True) for c in merged_captions]):
        # v1.44.0, F4: presets expand first (entrance), then
        # the style resolves (look), so expanded word items
        # inherit the resolved size/colour. The project
        # typography profile's default_style applies to items
        # without an explicit style; absent typography is the
        # old behaviour.
        _item = _expand_text_preset(_item)
        _item = _resolve_text_style(
            _item, height,
            (info.get("typography") or {}).get("default_style"))
        # Active word-timed captions (v1.37.0): the agent supplies
        # per-word timings; AgentCut renders short chunks (2–4
        # words) with the current word highlighted. Chunk mode
        # renders each chunk as one caption in the highlight
        # colour. Word mode renders every word twice — once in
        # the base colour for the whole chunk window, once in the
        # highlight colour for just that word's window, both at
        # the same measured x — so the highlight can never drift
        # off its word. Arabic joining is safe by construction:
        # joining happens inside a word, never across the space
        # between words, so per-word drawtext shapes each word
        # exactly as it shapes inside a sentence.
        # Active word-timed captions (v1.41.0, Fix 2): word_timings
        # items are rendered as ONE unified .ass file through the
        # ass filter (see build_karaoke_ass), never as per-word
        # drawtext filters. libass shapes each chunk as a single
        # connected sentence with the current word highlighted
        # inside it (one Dialogue per word window, colour only —
        # see build_karaoke_ass for why not \t/scale); chunk
        # mode renders each chunk as one plain event in the
        # highlight colour. No ink measurement is needed: libass
        # lays the line out itself.
        if _is_cap and _item.get("word_timings"):
            _wt = _item["word_timings"]
            _chunks = chunk_word_timings(_wt)
            _size = float(_item.get("size", 36))
            _cap_y = _active_caption_y(_item, _size, height, zone_clamp)
            _font = _ass_font_for(_item, _item.get("text", ""))
            _base_color = _item.get("color", "white")
            _hi_color = _item.get("highlight_color", "yellow")
            for _ch in _chunks:
                _cstart = float(_ch[0]["start"])
                _cend = float(_ch[-1]["end"])
                if _item.get("highlight_mode", "word") == "chunk":
                    ass_events.append({
                        "kind": "plain", "start": _cstart, "end": _cend,
                        "font": _font, "size": int(round(_size)),
                        "base_color": _hi_color, "align": 8,
                        "x": float(width) / 2, "y": _cap_y,
                        "text": " ".join(w["word"] for w in _ch)})
                else:
                    ass_events.append({
                        "kind": "karaoke", "start": _cstart, "end": _cend,
                        "font": _font, "size": int(round(_size)),
                        "base_color": _base_color, "hi_color": _hi_color,
                        "align": 8,
                        "x": float(width) / 2, "y": _cap_y,
                        "words": [{"word": str(w["word"]),
                                   "start": float(w["start"]),
                                   "end": float(w["end"])} for w in _ch]})
            continue
        # Kinetic text (v1.40.0): the caption word-mode
        # mechanism generalized to any text. Same chunking,
        # measured ink widths and RTL reversal, but anchored to
        # the text's own position: numeric x wins, position
        # presets map to left/centre/right anchors (40px margins,
        # mirroring the pill geometry), x == "center" (or absent)
        # centres the chunk; y mirrors the text bands (top 40,
        # bottom height-40-size*1.4, centre centred, else the
        # item y, default 100). The highlight word pops to
        # size * emphasis_factor (default 1.25 with emphasis,
        # else 1.0), re-centred on its slot so it grows in place
        # instead of shoving its neighbours. Validation rejects
        # word_timings combined with reveal/animation/fades, so
        # this branch never meets those. Like captions, the
        # expanded items drop word_timings and position (the
        # drawtext loop would otherwise override their x/y with
        # the preset) and drop box_radius fields (the pill is
        # static and cannot follow a moving highlight).
        # Kinetic text (v1.41.0, Fix 2): same ASS mechanism as
        # captions, anchored to the text's own position (numeric
        # x wins, presets map to anchors, centre centres; y
        # mirrors the text bands). Word mode renders one karaoke
        # event per chunk; hold (default true) adds a plain
        # base-colour event from the chunk end to the next chunk
        # start (or the text end), so the title persists past
        # its first 5s (R3). Chunk mode renders plain highlight
        # events only. Highlight is colour-only in ASS (no pop:
        # see build_karaoke_ass — emphasis is accepted but has
        # no visual effect here). Validation rejects word_timings
        # combined with reveal/animation/fades, so this branch
        # never meets those.
        if not _is_cap and _item.get("word_timings"):
            _wt = _item["word_timings"]
            _chunks = chunk_word_timings(_wt)
            _size = float(_item.get("size", 48))
            _pos = _item.get("position")
            if _pos in ("top_left", "top_center", "top_right"):
                _kin_y = 40.0
            elif _pos in ("bottom_left", "bottom_center", "bottom_right"):
                _kin_y = float(height - 40 - _size * 1.4)
            elif _pos == "center":
                _kin_y = float((height - _size * 1.4) / 2)
            else:
                _kin_y = float(_item.get("y", 100))
            _font = _ass_font_for(_item, _item.get("text", ""))
            _base_color = _item.get("color", "white")
            _hi_color = _item.get("highlight_color", "yellow")
            _align, _ax, _ay = _ass_pos_for_text(
                _item, _size, width, height, _kin_y)
            for _ci, _ch in enumerate(_chunks):
                _cstart = float(_ch[0]["start"])
                _cend = float(_ch[-1]["end"])
                if _item.get("highlight_mode", "word") == "chunk":
                    ass_events.append({
                        "kind": "plain", "start": _cstart, "end": _cend,
                        "font": _font, "size": int(round(_size)),
                        "base_color": _hi_color, "align": _align,
                        "x": _ax, "y": _ay,
                        "text": " ".join(w["word"] for w in _ch)})
                    continue
                ass_events.append({
                    "kind": "karaoke", "start": _cstart, "end": _cend,
                    "font": _font, "size": int(round(_size)),
                    "base_color": _base_color, "hi_color": _hi_color,
                    "align": _align,
                    "x": _ax, "y": _ay,
                    "words": [{"word": str(w["word"]),
                               "start": float(w["start"]),
                               "end": float(w["end"])} for w in _ch]})
                if _item.get("hold", True):
                    if _ci + 1 < len(_chunks):
                        _base_end = float(_chunks[_ci + 1][0]["start"])
                    else:
                        _base_end = float(_item["end"])
                    if _base_end > _cend:
                        ass_events.append({
                            "kind": "plain", "start": _cend,
                            "end": _base_end, "font": _font,
                            "size": int(round(_size)),
                            "base_color": _base_color, "align": _align,
                            "x": _ax, "y": _ay,
                            "text": " ".join(w["word"] for w in _ch)})
            continue
        if _item.get("reveal") == "word":
            words = str(_item["text"]).split()
            if len(words) > 1:
                _start = float(_item.get("start", 0))
                _end = float(_item.get("end", total))
                _window = _end - _start
                _weights = [len(w) for w in words]
                _total_w = sum(_weights)
                _acc = 0
                # Multi-line caption reveal (v1.45.0, R4c-3b,
                # Defect 3): wrap the FULL text ONCE, then each
                # word prefix becomes one sub-item PER non-empty
                # line, at the fixed y of that line in the full
                # block. Without this the prefix is one item
                # whose y jumps when it grows from 1 line to 2
                # (the wrapping stage re-centres it on its
                # current line count), and at the boundary frame
                # both the 1-line and 2-line prefixes are enabled
                # at overlapping y. The y_i here use the FULL
                # line count, so line 0 never moves.
                _full_lines = []
                if _is_cap:
                    # v1.46.1, R1: wrap with the real render font
                    # (find_font_for_text resolves the same font
                    # the drawtext loop uses), so the wrap packs
                    # what the renderer lays out.
                    _full_lines = wrap_caption_lines(
                        str(_item["text"]), float(_item.get("size", 36)),
                        width * cap_maxw,
                        font_path=find_font_for_text(
                            str(_item["text"])))
                if len(_full_lines) > 1:
                    _rsize = float(_item.get("size", 36))
                    _rlh = _rsize * cap_spacing
                    _rpos = _item.get("position")
                    _rn = len(_full_lines)
                    _rys = []
                    for _li in range(_rn):
                        if _rpos in ("top_left", "top_center", "top_right"):
                            _rys.append(40 + _li * _rlh)
                        elif _rpos in ("bottom_left", "bottom_center", "bottom_right"):
                            _rys.append((height - 40 - _rsize * 1.4)
                                        - (_rn - 1 - _li) * _rlh)
                        elif _rpos == "center":
                            _rbh = (_rn - 1) * _rlh + _rsize * 1.4
                            _rys.append((height - _rbh) / 2 + _li * _rlh)
                        else:
                            _rys.append(_default_caption_top(
                                height, _rsize, _rn, cap_spacing,
                                zone_clamp) + _li * _rlh)
                    # Which full line each word belongs to.
                    _words_in_line = [len(_ln.split())
                                      for _ln in _full_lines]
                    _word_line = []
                    _li = 0
                    for _wi in range(len(words)):
                        while (_li + 1 < _rn and
                               _wi >= sum(_words_in_line[:_li + 1])):
                            _li += 1
                        _word_line.append(_li)
                    _block_seq += 1
                    _rblk = _block_seq
                    for _k, _w in enumerate(words):
                        _sstart = _start + _window * (_acc / _total_w)
                        _acc += _weights[_k]
                        _send = _end if _k == len(words) - 1 else \
                            _start + _window * (_acc / _total_w)
                        # The prefix's lines: for each full line,
                        # the words of that line up to word k.
                        _prefix_lines = []
                        for _li in range(_rn):
                            _lw = [words[_wi] for _wi in range(len(words))
                                   if _word_line[_wi] == _li and _wi <= _k]
                            if _lw:
                                _prefix_lines.append((_li, " ".join(_lw)))
                        for _li, _ln in _prefix_lines:
                            _sub = dict(_item)
                            if "track" in _item:
                                _sub["_track_base_start"] = _start
                            _sub["text"] = _ln
                            _sub["start"] = _sstart
                            _sub["end"] = _send
                            _sub.pop("reveal", None)
                            _sub.pop("animation", None)
                            _sub.pop("fade_in", None)
                            _sub.pop("fade_out", None)
                            if _item.get("_kinetic_words"):
                                _sub["animation"] = "fade_up"
                                _sub["animation_duration"] = 0.3
                                _sub["fade_in"] = 0.3
                            _sub["_wrap_y"] = _rys[_li]
                            _sub["_block_id"] = _rblk
                            # The block is the VISIBLE prefix:
                            # its height/width hug the lines
                            # shown in this step (the background
                            # grows as the prefix grows).
                            _sub["_block_n"] = len(_prefix_lines)
                            _sub["_block_top"] = _rys[_prefix_lines[0][0]]
                            _sub["_block_lh"] = _rlh
                            _sub["_block_texts"] = [
                                _ln for _, _ln in _prefix_lines]
                            _sub["_block_first"] = (
                                _li == _prefix_lines[0][0])
                            render_items.append((_sub, _is_cap))
                    continue
                for _k, _w in enumerate(words):
                    _sub = dict(_item)
                    if "track" in _item:
                        # The track is sampled in the ORIGINAL
                        # item's source time (t - start), not the
                        # word's window start, or the text would
                        # jump between words.
                        _sub["_track_base_start"] = _start
                    _sub["text"] = " ".join(words[:_k + 1])
                    _sub["start"] = _start + _window * (_acc / _total_w)
                    _acc += _weights[_k]
                    _sub["end"] = _end if _k == len(words) - 1 else \
                        _start + _window * (_acc / _total_w)
                    _sub.pop("reveal", None)
                    _sub.pop("animation", None)
                    _sub.pop("fade_in", None)
                    _sub.pop("fade_out", None)
                    if _item.get("_kinetic_words"):
                        # Kinetic words (v1.44.0, F4): each word
                        # rises + fades in at its own staggered
                        # window start, on the shared easing
                        # system (fade_up). The reveal itself is
                        # the word's arrival; the rise marks it.
                        _sub["animation"] = "fade_up"
                        _sub["animation_duration"] = 0.3
                        _sub["fade_in"] = 0.3
                    render_items.append((_sub, _is_cap))
                continue
        render_items.append((_item, _is_cap))
    # Caption wrapping (v1.36.0): each caption item becomes one
    # item per wrapped line (same window, styling and timing).
    # Lines are wrapped on the LOGICAL text; the BiDi display
    # transform is applied per line later, at textfile write, so
    # word order stays correct. Only y changes per line: wrapped
    # blocks stack upward from the old single-line top (bottom
    # positions and the default), downward from the top (top
    # positions), or centred (center). Single-line captions get
    # no _wrap_y key and render exactly as before.
    wrapped_items = []
    for _item, _is_cap in render_items:
        if not _is_cap:
            wrapped_items.append((_item, _is_cap))
            continue
        _size = float(_item.get("size", 36))
        # v1.46.1, R1: wrap with the real render font
        # (find_font_for_text resolves the same font the drawtext
        # loop uses), so the wrap packs what the renderer lays out.
        _lines = wrap_caption_lines(str(_item["text"]), _size,
                                    width * cap_maxw,
                                    font_path=find_font_for_text(
                                        str(_item["text"])))
        if len(_lines) <= 1:
            wrapped_items.append((_item, _is_cap))
            continue
        _lh = _size * cap_spacing
        _pos = _item.get("position")
        # Multi-line block (v1.45.0, R4c-3a, Defect 3): the lines
        # share one unified background (see the drawtext loop),
        # because per-line drawtext boxes overlap — the line
        # spacing (size*1.35) is smaller than a box's height
        # (text height + 2*12 border), so two per-line boxes
        # merge into one stepped black block. The block id, the
        # first line's y (block top), the line height and the
        # line strings are carried on every line item; the first
        # line emits the background, the rest only their text.
        _block_seq += 1
        _block_top = None
        for _li, _ln in enumerate(_lines):
            _sub = dict(_item)
            _sub["text"] = _ln
            if _pos in ("top_left", "top_center", "top_right"):
                _sub["_wrap_y"] = 40 + _li * _lh
            elif _pos in ("bottom_left", "bottom_center", "bottom_right"):
                _sub["_wrap_y"] = (height - 40 - _size * 1.4) - (len(_lines) - 1 - _li) * _lh
            elif _pos == "center":
                _block_h = (len(_lines) - 1) * _lh + _size * 1.4
                _sub["_wrap_y"] = (height - _block_h) / 2 + _li * _lh
            else:
                _sub["_wrap_y"] = _default_caption_top(
                    height, _size, len(_lines), cap_spacing,
                    zone_clamp) + _li * _lh
            if _block_top is None:
                _block_top = float(_sub["_wrap_y"])
            _sub["_block_id"] = _block_seq
            _sub["_block_n"] = len(_lines)
            _sub["_block_top"] = _block_top
            _sub["_block_lh"] = _lh
            _sub["_block_texts"] = list(_lines)
            _sub["_block_first"] = (_li == 0)
            wrapped_items.append((_sub, _is_cap))
    render_items = wrapped_items
    # Native ASS karaoke stage (v1.41.0, Fix 2): all word_timings
    # items became ass_events above. One unified .ass file goes
    # through a single ass filter FIRST, before drawtext texts,
    # so ASS karaoke renders BELOW drawtext texts (documented
    # layering; rebuild titles and pops sit at different y, so
    # it is irrelevant there). text_dir always exists here when
    # ass_events does (texts/captions create it), but the guard
    # keeps a texts-free project from crashing the stage.
    if ass_events:
        if text_dir is None:
            text_dir = tempfile.mkdtemp(prefix="agentcut-text-")
        _ass_path = os.path.join(text_dir, "karaoke.ass")
        with open(_ass_path, "w", encoding="utf-8") as _af:
            _af.write(build_karaoke_ass(ass_events, width, height))
        _ass_filter = f"ass=filename='{_esc_filter_arg(_ass_path)}'"
        _fonts_dir = bundled_fonts_dir()
        if _fonts_dir:
            _ass_filter += f":fontsdir='{_esc_filter_arg(_fonts_dir)}'"
        filters.append(f"[{vcur}]{_ass_filter}[vass]")
        vcur = "vass"
    # Ink-width cache for sweep bars (v1.44.0, F4): one
    # measurement per (text, font, size), shared by every
    # highlight/underline item in this render.
    _width_cache = {}
    # Background rects (v1.45.0, R4c-2, Defect 2): shapes/cards a
    # text can sit on; computed once, shared by every item.
    _bg_rects = _background_rects(info, width, height)
    for item, is_caption in render_items:
        size = item.get("size", 36 if is_caption else 48)
        if not is_caption and not item.get("_counter") and item.get("text"):
            _txt = str(item["text"])
            _item_f = find_bundled_font(item["font"]) if item.get("font") else find_font_for_text(_txt)
            if _item_f:
                _mw = _measure_text_width(_txt, _item_f, size, _width_cache)
                if _mw > width * 0.86:
                    size = max(28, int(size * (width * 0.86) / _mw))
                    item["size"] = size
                    _mw2 = _measure_text_width(_txt, _item_f, size, _width_cache)
                    if _mw2 > width * 0.88 and "\n" not in _txt:
                        _lines = wrap_subtitle_text(_txt, width, size, max_width_frac=0.86)
                        if len(_lines) > 1:
                            item["text"] = "\n".join(_lines)
        if is_caption:
            if "_wrap_y" in item:
                if item.get("position"):
                    x_expr, _ = POS_MAP[item["position"]]
                else:
                    x_expr = "(w-text_w)/2"
                y_expr = str(item["_wrap_y"])
            elif item.get("position"):
                x_expr, y_expr = POS_MAP[item["position"]]
            else:
                x_expr = "(w-text_w)/2"
                y_expr = str(_default_caption_top(height, size, 1, 1.35,
                                                  zone_clamp))
        else:
            x = item.get("x", "center")
            x_expr = "(w-text_w)/2" if x == "center" else str(x)
            y_expr = str(item.get("y", 100))
        start = float(item.get("start", 0))
        end = float(item.get("end", total))
        if not is_caption and item.get("position"):
            x_expr, y_expr = POS_MAP[item["position"]]
        if item.get("track"):
            # Follow-track (v1.44.0, F2): the text centres on
            # the tracked box's centre, sampled every frame.
            x_expr, y_expr = _track_text_expr(
                item, info, width, height, start)
        color = item.get("color", "white")
        if isinstance(color, str) and color.startswith("#"):
            color = "0x" + color[1:]
        box = 1 if item.get("box", True) else 0
        box_part = ":box=1:boxcolor=black@0.5:boxborderw=12" if box else ":box=0"
        # Defect 2 (v1.45.0, R4c-2): a text centred on a shape or
        # card gets no drawtext box and no pill — the shape/card
        # IS the background. Forced even for an explicit box=true:
        # a box inside a shape is always the reported defect
        # ("اضغط" on the yellow circle, "project.json" in the
        # blue card). The pill block below honours _on_bg too.
        _on_bg = _text_on_background(item, is_caption, width, height,
                                      size, _bg_rects, _width_cache,
                                      zone_clamp)
        if _on_bg:
            box = 0
            box_part = ":box=0"
        # Multi-line block (R4c-3a): a line in a block never gets
        # its own drawtext box — the block has one unified
        # background (emitted below, on the first line).
        _in_block = "_block_id" in item
        if _in_block:
            box = 0
            box_part = ":box=0"
        # Rounded pills (v1.45.0, R5): the default boxed text
        # gets a rounded pill (the _emit_text_pill overlay
        # below), NOT the sharp drawtext box. The drawtext box
        # survives only where a static pill cannot follow the
        # text: an animated text (its x/y move), a fading text
        # (its alpha ramps — the pill is a separate overlay
        # that would not fade), or a tracked text (its x/y are
        # sampled from the track every frame). Those keep the
        # sharp box, which is part of the drawtext filter and
        # moves/fades with the text. Counters are pills too
        # (their branch below emits one sized to the max
        # display value); the _counter flag is excluded here
        # only because its pill is emitted in its own branch.
        fi = float(item.get("fade_in", 0) or 0)
        fo = float(item.get("fade_out", 0) or 0)
        _use_pill = (bool(box) and not _on_bg and not _in_block
                     and not item.get("animation") and not fi
                     and not fo and not item.get("track"))
        if _use_pill:
            box_part = ":box=0"
        alpha_part = ""
        if item.get("_alpha_expr"):
            # Custom alpha expression (v1.45.0, R6): the
            # lower-third text's opacity follows its card's
            # spring entrance, not a linear fade ramp. It
            # replaces the fade_in/fade_out alpha entirely.
            alpha_part = f":alpha='{item['_alpha_expr']}'"
        elif fi > 0 and fo > 0:
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
            # Spring entrance (v1.45.0, R6): the text slides in
            # with a spring overshoot (~15% past its rest x,
            # settling by the end of the window) instead of the
            # old linear ramp. The exit stays linear — a spring
            # exit would overshoot off-screen, which is
            # invisible, so there is nothing to gain.
            _su = _easing_factor("spring", f"(t-{start})/{ad}")
            in_expr = (f"({x_expr})*{_su}-text_w*(1-{_su})")
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
        if anim == "pop_in":
            # Scale pop with the spring (v1.45.0, R6): 0.3x at
            # u=0, ~1.10x at the spring peak (u~0.27), 1.0x at
            # u=1 — a livelier pop than the old ease_out_back
            # (~1.06x). Same formula as the shared easing
            # system (_easing_factor / motion.apply_easing).
            _pu = f"(t-{start})/{ad}"
            _ps = _easing_factor("spring", _pu)
            size_out = (f"if(lt(t,{start + ad}),"
                        f"{size}*(0.3+0.7*{_ps}),{size})")
        # fade_up: the text rises from 40px below, decelerating
        # (ease_out quadratic), and fades in over the same
        # window (fade_in is set by the preset). y_out defaults
        # to y_expr; only fade_up moves it.
        y_out = y_expr
        if anim == "fade_up":
            _fu = f"(t-{start})/{ad}"
            y_out = (f"if(lt(t,{start + ad}),"
                     f"({y_expr})+40*pow(1-{_fu},2),{y_expr})")
        stroke = int(item.get("stroke", 0))
        stroke_part = ""
        if stroke > 0:
            sc = item.get("stroke_color", "black")
            if isinstance(sc, str) and sc.startswith("#"):
                sc = "0x" + sc[1:]
            stroke_part = f":borderw={stroke}:bordercolor={sc}"
        # Text shadow (v1.36.0, extended v1.40.0): drawtext
        # shadowx/shadowy. When neither axis is set, captions
        # AND plain texts get a subtle default shadow (2,2) so
        # they read over bright panels — the v1.40.0 defaults
        # upgrade (plain texts used to default to no shadow).
        # Setting either axis takes over both (the missing axis
        # reads as 0), so shadow_x=0 alone disables the default.
        if "shadow_x" in item or "shadow_y" in item:
            sx = int(item.get("shadow_x", 0))
            sy = int(item.get("shadow_y", 0))
        else:
            sx, sy = 2, 2
        shadow_part = ""
        if sx or sy:
            shadow_part = (f":shadowcolor={_ff_color(item.get('shadow_color', 'black'))}"
                           f":shadowx={sx}:shadowy={sy}")
        if item.get("_counter"):
            # Counter (v1.43.0, R3): the number is a live %{eif}
            # expression evaluated by FFmpeg per frame, so the
            # text goes INLINE with expansion=normal — never
            # through a textfile (the textfile reader strips \:
            # and breaks eif; the BiDi wrap breaks expansion
            # entirely — both verified by spike in Round 0).
            if item.get("font"):
                item_font = find_bundled_font(item["font"])
            else:
                item_font = find_font_for_text(
                    item.get("_counter_affixes") or "0")
            if item_font is None:
                raise ValueError(
                    "FONT_NOT_FOUND: no usable font for the counter "
                    f"{item.get('_counter_affixes', '')!r}; Arabic "
                    "text needs Noto Sans Arabic, Latin text needs "
                    "DejaVuSans (bundled fonts/ or $AGENTCUT_FONT_DIR)")
            # Counter pill (v1.45.0, R5): a boxed counter gets
            # the rounded pill, sized to the widest value it
            # can show (_counter_max_text, centred on the
            # counter's numeric centre). box_part is already
            # ":box=0" when _use_pill is set.
            if _use_pill:
                _cmax = str(item.get("_counter_max_text") or "0")
                _cw = _measure_text_width(_cmax, item_font, size,
                                          _width_cache)
                _cpad = int(item.get("box_padding", 12))
                _cbw = int(_cw + 2 * _cpad + 0.5)
                _cbh = int(float(size) * 1.4 + 2 * _cpad + 0.5)
                _ccx = float(item.get("_counter_cx", 0.0))
                _cbx = max(0, int(_ccx - _cbw / 2))
                _cby = max(0, int(float(item.get("y", 0)) - _cpad))
                _crad = item.get("box_radius")
                _crad = 16 if _crad is None else int(_crad)
                vcur = _emit_text_pill(
                    filters, vcur, overlay_index, _cbx, _cby,
                    _cbw, _cbh, _crad,
                    item.get("box_color", "black"),
                    float(item.get("box_opacity", 0.5)),
                    fps, _afmt, start, end, argv)
            filters.append(
                f"[{vcur}]drawtext=fontfile='{_esc_filter_arg(item_font)}':"
                f"text='{item['_counter_expr']}':"
                f"fontsize='{size_out}':fontcolor={color}{box_part}"
                f"{alpha_part}{stroke_part}{shadow_part}:"
                f"x='{x_out}':y='{y_expr}':expansion=normal:"
                f"text_shaping=1:"
                f"enable='between(t,{start},{end})'"
                f"[vout{overlay_index}]")
            vcur = f"vout{overlay_index}"
            overlay_index += 1
            continue
        text_path = os.path.join(text_dir, f"text_{overlay_index}.txt")
        with open(text_path, "w", encoding="utf-8") as fh:
            # BiDi display form (v1.33.0): stored text stays in
            # logical order; only the textfile content is wrapped
            # (see bidi_display_text). Reveal prefixes are already
            # expanded into render_items above, so they are covered
            # here exactly like titles, pop texts and SRT cues.
            fh.write(bidi_display_text(str(item["text"])))
        # Font by script: Arabic text gets Noto Sans Arabic (Bold)
        # when present, everything else keeps the validated default
        # font. v1.41.0: an explicit font option wins over script
        # detection (tajawal-bold for display titles); a named
        # font that cannot be found fails loudly, never tofu.
        # Deliberately NO fallback to the Latin font for Arabic text
        # (v1.35.0): DejaVu has no Arabic glyphs, so falling back
        # rendered silent tofu boxes. Validation normally rejects
        # such projects first; this raise is the defence in depth
        # for hand-built info dicts, and render_project turns it
        # into a structured FONT_NOT_FOUND error.
        if item.get("font"):
            item_font = find_bundled_font(item["font"])
        else:
            item_font = find_font_for_text(str(item["text"]))
        if item_font is None:
            raise ValueError(
                f"FONT_NOT_FOUND: no usable font for text "
                f"{str(item['text'])[:40]!r}"
                + (f" (requested font '{item['font']}')" if item.get("font") else "")
                + f"; Arabic text needs Noto Sans "
                "Arabic, Latin text needs DejaVuSans (bundled fonts/ or "
                "$AGENTCUT_FONT_DIR)")
        # Unified block background (v1.45.0, R4c-3a, Defect 3):
        # one background for the whole multi-line caption block,
        # emitted on the first line (before its drawtext, so it
        # sits below all the lines). Geometry: the union of the
        # lines' rects (each line centred/left/right by the same
        # position rule) plus the 12px box padding, from the
        # first line's top to the last line's bottom. Rounded
        # when the item has box_radius (the same geq mask as the
        # per-line pill — with radius 0 the factor is 1, a sharp
        # rect); colour/opacity from box_color/box_opacity
        # (default black@0.5, the old per-line box look).
        if _in_block and item.get("_block_first"):
            _blines = list(item.get("_block_texts") or [item["text"]])
            _bwidths = [_measure_text_width(
                _ln, item_font, size, _width_cache) for _ln in _blines]
            _bpos = item.get("position")
            _bx_expr = (POS_MAP[_bpos][0] if _bpos in POS_MAP
                        else "(w-text_w)/2")
            _blefts = [_pos_expr_value(_bx_expr, width, height,
                                       _bw, float(size) * 1.4)
                       for _bw in _bwidths]
            _bleft = min(_blefts)
            _bright = max(_l + _w for _l, _w in zip(_blefts, _bwidths))
            _bpad = 12
            _bbx = max(0, int(_bleft - _bpad))
            _bby = max(0, int(float(item["_block_top"]) - _bpad))
            _bbw = int(_bright - _bleft + 2 * _bpad + 0.5)
            _bbh = int((int(item["_block_n"]) - 1)
                       * float(item["_block_lh"]) + float(size) * 1.4
                       + 2 * _bpad + 0.5)
            _bbw = max(1, min(_bbw, width - _bbx))
            _bbh = max(1, min(_bbh, height - _bby))
            _bbr = item.get("box_radius")
            _bbr = 16 if _bbr is None else int(_bbr)
            vcur = _emit_text_pill(
                filters, vcur, overlay_index, _bbx, _bby,
                _bbw, _bbh, _bbr, item.get("box_color", "black"),
                float(item.get("box_opacity", 0.5)),
                fps, _afmt, start, end, argv)
        # Rounded text background (v1.36.0 pill, v1.45.0 R5
        # default): a generated rounded-rect colour source
        # overlaid UNDER the text, in place of drawtext's
        # rectangular box. Since R5 the pill IS the default
        # boxed-text background (see _use_pill above): the
        # sharp drawtext box is gone except for animated /
        # fading / tracked texts, which keep it because a
        # static pill cannot follow them. Geometry mirrors
        # the drawtext position (same width, same preset
        # positions) so the pill hugs the text; the width is
        # the shaped (HarfBuzz) width, not the old 0.6xsize
        # estimate (which ran far too wide for Arabic). The
        # radius is the item's box_radius when set, else 16
        # (a soft rounded card); 0 gives a sharp rect through
        # this same mechanism. Validation forbids combining a
        # box_radius with animation or fades (the pill would
        # not move or fade with the text) — and _use_pill
        # excludes those items from the pill entirely.
        if _use_pill:
            box_part = ":box=0"
            est_w = _measure_text_width(str(item["text"]), item_font,
                                        size, _width_cache)
            est_h = float(size) * 1.4
            pad = int(item.get("box_padding", 12))
            bw = int(est_w + 2 * pad + 0.5)
            bh = int(est_h + 2 * pad + 0.5)
            _brad = item.get("box_radius")
            _brad = 16 if _brad is None else int(_brad)
            pos = item.get("position")
            if is_caption:
                if "_wrap_y" in item:
                    top = float(item["_wrap_y"])
                elif pos in ("top_left", "top_center", "top_right"):
                    top = 40.0
                elif pos in ("bottom_left", "bottom_center", "bottom_right"):
                    top = height - est_h - 40.0
                elif pos == "center":
                    top = (height - est_h) / 2
                else:
                    top = _default_caption_top(height, size, 1, 1.35,
                                               zone_clamp)
                if pos in ("top_left", "bottom_left"):
                    left = 40.0
                elif pos in ("top_right", "bottom_right"):
                    left = width - est_w - 40.0
                else:
                    left = (width - est_w) / 2
            else:
                if pos in ("top_left", "top_center", "top_right"):
                    top = 40.0
                elif pos in ("bottom_left", "bottom_center", "bottom_right"):
                    top = height - est_h - 40.0
                elif pos == "center":
                    top = (height - est_h) / 2
                else:
                    top = float(item.get("y", 100))
                if pos in ("top_left", "bottom_left"):
                    left = 40.0
                elif pos in ("top_right", "bottom_right"):
                    left = width - est_w - 40.0
                elif pos:
                    left = (width - est_w) / 2
                else:
                    xv = item.get("x", "center")
                    if xv == "center":
                        left = (width - est_w) / 2
                    else:
                        try:
                            left = float(xv)
                        except (TypeError, ValueError):
                            # Expression x (motion labels centre
                            # on "(<cx>-text_w/2)"): centre the
                            # pill on that point; anything else
                            # falls back to frame centre.
                            _m = re.match(
                                r"^\(([\d.]+)-text_w/2\)$",
                                str(xv))
                            left = (float(_m.group(1)) - est_w / 2
                                    if _m else (width - est_w) / 2)
            bx = max(0, int(left - pad))
            by = max(0, int(top - pad))
            vcur = _emit_text_pill(
                filters, vcur, overlay_index, bx, by, bw, bh,
                _brad, item.get("box_color", "black"),
                float(item.get("box_opacity", 0.5)),
                fps, _afmt, start, end, argv)
        # Highlight sweep / underline swipe (v1.44.0, F4): a bar
        # behind (highlight) or under (underline) the line that
        # sweeps in over the first 0.6s, then holds. The bar hugs
        # the shaped text width (HarfBuzz, v1.45.0 R2 — always
        # >= the true ink width, so it never splits a word and
        # never under-covers the text): the whole line is one
        # bar. Arabic (RTL) sweeps right-to-left; Latin sweeps
        # left-to-right. Built as a colour source scaled with
        # eval=frame and overlaid (NOT a drawbox: drawbox w/h
        # are config-time on this build) AFTER the pill (if any)
        # and BEFORE the drawtext, so it sits below the text.
        if item.get("_sweep") in ("highlight", "underline"):
            _sw_text = str(item["text"])
            _sw_rtl = bool(_ARABIC_RE.search(_sw_text))
            _sw_dur = min(0.6, max(0.1, end - start))
            # v1.45.0, R2: HarfBuzz shaped width (never None, no
            # canvas limit — the old >=2300 guard was for the
            # cropdetect canvas, not a real line width).
            _ink = _measure_text_width(_sw_text, item_font, size,
                                       _width_cache)
            _pad = 12
            _bar_w = float(_ink) + 2 * _pad
            if is_caption:
                if "_wrap_y" in item:
                    _sw_top = float(item["_wrap_y"])
                elif item.get("position") in ("top_left", "top_center", "top_right"):
                    _sw_top = 40.0
                elif item.get("position") in ("bottom_left", "bottom_center", "bottom_right"):
                    _sw_top = height - float(size) * 1.4 - 40.0
                elif item.get("position") == "center":
                    _sw_top = (height - float(size) * 1.4) / 2
                else:
                    _sw_top = _default_caption_top(height, size, 1, 1.35,
                                                   zone_clamp)
                if item.get("position") in ("top_left", "bottom_left"):
                    _sw_left = 40.0
                elif item.get("position") in ("top_right", "bottom_right"):
                    _sw_left = width - float(_ink) - 40.0
                else:
                    _sw_left = (width - float(_ink)) / 2
            else:
                _pos = item.get("position")
                if _pos in ("top_left", "top_center", "top_right"):
                    _sw_top = 40.0
                elif _pos in ("bottom_left", "bottom_center", "bottom_right"):
                    _sw_top = height - float(size) * 1.4 - 40.0
                elif _pos == "center":
                    _sw_top = (height - float(size) * 1.4) / 2
                else:
                    _sw_top = float(item.get("y", 100))
                if _pos in ("top_left", "bottom_left"):
                    _sw_left = 40.0
                elif _pos in ("top_right", "bottom_right"):
                    _sw_left = width - float(_ink) - 40.0
                elif _pos:
                    _sw_left = (width - float(_ink)) / 2
                else:
                    _xv = item.get("x", "center")
                    if _xv == "center":
                        _sw_left = (width - float(_ink)) / 2
                    else:
                        try:
                            _sw_left = float(_xv)
                        except (TypeError, ValueError):
                            _m = re.match(
                                r"^\(([\d.]+)-text_w/2\)$",
                                str(_xv))
                            _sw_left = (float(_m.group(1))
                                        - float(_ink) / 2
                                        if _m
                                        else (width - float(_ink)) / 2)
            if item.get("_sweep") == "highlight":
                _sw_h = float(size) * 1.4 + 2 * 8
                _sw_y = max(0, int(_sw_top - 8))
                _sw_color = _ff_color(item.get("highlight_color", "yellow"))
                _sw_alpha = 0.5
            else:
                _sw_h = 6
                _sw_y = int(_sw_top + float(size) * 1.15)
                _sw_color = _ff_color(item.get(
                    "underline_color",
                    item.get("highlight_color", "yellow")))
                _sw_alpha = 0.9
            # The bar is a colour source scaled with
            # eval=frame: drawbox w/h are evaluated ONCE at
            # config time on this FFmpeg 8.1 build (proven:
            # w='if(lt(t,0.6),100,300)' stays 300 from t=0.1),
            # so a drawbox can never grow. scale w animates per
            # frame (proven by the R5 zoom_out and the R7
            # /tmp/r7probe bar_ltr/bar_rtl probes); the overlay
            # reads the scaled width as overlay_w, so the RTL
            # bar keeps its right edge fixed as it grows.
            # v1.45.0, R1: clamp u to [0,1] BEFORE the smoothstep.
            # The bar is a colour-source input scaled with
            # eval=frame, so its w expression is evaluated for
            # EVERY frame from t=0 — including frames before the
            # sweep starts, where u=(t-start)/dur is NEGATIVE.
            # The smoothstep polynomial u^2*(3-2u) is unbounded
            # for negative u (u=-100 -> 2,030,000), so a late
            # sweep (start=60) asked scale for an 860,720,000px
            # wide image and the render died with "Picture size
            # ... is invalid" (explainer-video production).
            # Clamped, the width is 2px before the sweep, grows
            # through the same smoothstep during it, and is the
            # full bar width after — byte-identical for t>=start.
            _sw_x0 = max(0, int(_sw_left - _pad))
            _sw_x1 = _sw_x0 + int(_bar_w)
            _su = f"min(1,max(0,(t-{start})/{_sw_dur}))"
            _w_expr = (f"max(2.0,{_bar_w:.1f}*"
                       f"({_su}*{_su}*(3-2*{_su})))")
            if _sw_rtl:
                _x_expr_sw = f"{_sw_x1}-overlay_w"
            else:
                _x_expr_sw = f"{_sw_x0}"
            filters.append(
                f"color=c={_sw_color}@{_sw_alpha}:"
                f"s={int(_bar_w)}x{int(_sw_h)}:r={fps},"
                f"format={_afmt},scale=w='{_w_expr}':"
                f"h={int(_sw_h)}:eval=frame[barS{overlay_index}]")
            filters.append(
                f"[{vcur}][barS{overlay_index}]overlay="
                f"x='{_x_expr_sw}':y={_sw_y}:"
                f"eof_action=pass:enable='between(t,{start},{end})'"
                f"[vsw{overlay_index}]")
            vcur = f"vsw{overlay_index}"
        filters.append(
            f"[{vcur}]drawtext=fontfile='{_esc_filter_arg(item_font)}':textfile='{_esc_filter_arg(text_path)}':"
            f"fontsize='{size_out}':fontcolor={color}{box_part}{alpha_part}{stroke_part}{shadow_part}:"
            f"x='{x_out}':y='{y_out}':expansion=none:text_shaping=1:"
            f"enable='between(t,{start},{end})'[vout{overlay_index}]")
        vcur = f"vout{overlay_index}"
        overlay_index += 1
    # Pro pack review aids (preview only, unless the project itself
    # asks for timecode — see render_project). Guides are drawbox
    # outlines at the action-safe (5% margin) and title-safe (10%
    # margin) rectangles; timecode is a running drawtext clock.
    # They are drawn on top of everything else, so a preview frame
    # shows exactly where safe areas and the clock sit — and a
    # final render without them is pixel-identical to before.
    # Timecode is effective when the caller asks for it OR the
    # project itself sets timecode: true, so the graph builder
    # alone honours an explicit project choice.
    review_timecode = bool(review_timecode or project.get("timecode"))
    if guides or review_timecode:
        cur = vcur
        if guides:
            filters.append(
                f"[{cur}]drawbox=x='iw*0.05':y='ih*0.05':w='iw*0.9':"
                f"h='ih*0.9':color=yellow@0.9:t=2[vguide1]")
            cur = "vguide1"
            filters.append(
                f"[{cur}]drawbox=x='iw*0.1':y='ih*0.1':w='iw*0.8':"
                f"h='ih*0.8':color=red@0.9:t=2[vguide2]")
            cur = "vguide2"
        if review_timecode:
            tc_font = info.get("font") or find_font()
            if tc_font is None:
                raise ValueError(
                    "FONT_NOT_FOUND: no Latin font for the review timecode "
                    "(bundled fonts/ or $AGENTCUT_FONT_DIR)")
            filters.append(
                f"[{cur}]drawtext=fontfile='{_esc_filter_arg(tc_font)}':"
                f"text='%{{pts\\:hms}}':fontsize=28:fontcolor=yellow:"
                f"box=1:boxcolor=black@0.5:boxborderw=8:x=20:y=20:"
                f"text_shaping=1[vtc]")
            cur = "vtc"
        vcur = cur
    # vcur is the last stage so far: overlay output when overlays exist
    # (and no texts), the last text output when texts exist, or the
    # main chain when neither exists. Resetting to vcur_chain here
    # would silently drop the overlay stage.
    vout = vcur
    # Audio tracks. When voice and music are both present WITHOUT
    # an explicit ducking object, music is ducked with a real
    # sidechain compressor keyed off the voice mix (FFmpeg
    # sidechaincompress), not a flat volume cut: music drops while
    # voice is speaking and returns when it stops. WITH an
    # explicit ducking object, the sidechain is replaced by
    # deterministic volume automation (below): music gain follows
    # the voice track's time windows, dropping by amount_db with
    # smooth fades, regardless of the voice audio's level.
    has_voice = any(t.get("kind") == "voice" for t in audio_tracks)
    has_music = any(t.get("kind") == "music" for t in audio_tracks)
    duck = info.get("ducking")
    # Delta: explicit auto-duck switch (precedence contract in
    # validate.py). Default True is the pre-delta behaviour;
    # False disables only the auto sidechain path.
    auto_duck = info.get("auto_duck", True)
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
            # v1.41.0: stable LINEAR per-track normalisation.
            # The source is measured first (_run_loudness) and
            # the linear form applies a constant gain, so no
            # time-varying pumping. Measured I/TP/thresh are
            # shifted by the track's static volume (20*log10);
            # LRA is gain-invariant. Fade windows are ignored
            # in the adjustment (documented approximation —
            # still strictly better than single-pass, whose
            # gain varies with the signal). An unmeasurable
            # track (silence, unreadable file) gets NO
            # loudnorm stage: single-pass loudnorm on silence
            # emits NaN samples (v1.34.0 guard), and the
            # master stage, when enabled, still normalises
            # the mix.
            lt = info.get("loudness_target", -16.0)
            _meas, _mok = _run_loudness(resolve_path(track["src"]))
            _vals = (_meas or {})
            _mi = _vals.get("input_i")
            _mtp = _vals.get("input_tp")
            _mlra = _vals.get("input_lra")
            _mth = _vals.get("input_thresh")
            import math as _math
            if (_mok and _mi is not None and _mtp is not None
                    and _mlra is not None and _mth is not None
                    and all(_math.isfinite(v) for v in
                            (_mi, _mtp, _mlra, _mth))
                    and _mi > -70):
                _g = 20 * _math.log10(vol) if vol > 0 else 0.0
                loudnorm_part = (
                    f",loudnorm=I={lt}:TP=-1.5:LRA=11:linear=true:"
                    f"measured_I={_mi + _g:.2f}:"
                    f"measured_TP={_mtp + _g:.2f}:"
                    f"measured_LRA={_mlra:.2f}:"
                    f"measured_thresh={_mth + _g:.2f}")
        dn_part = ",afftdn" if track.get("denoise") else ""
        # v1.37.1: NO apad/atrim/asetpts after adelay (see the SFX
        # note above — atrim after adelay discards the delay
        # silence and the track lands at 0). adelay's own silence
        # frames carry the delay as samples; the clip-silence
        # chain guarantees the mix is `total` long and -t
        # truncates any track that runs past it. afade windows
        # still align: after adelay, frame pts are timeline pts.
        voice_chain = ""
        if track.get("kind") == "voice":
            voice_chain = (
                ",highpass=f=80,"
                "equalizer=f=220:t=q:w=1.2:g=-2.5,"
                "equalizer=f=3800:t=q:w=1.0:g=3.0,"
                "treble=g=2.0:f=8000,"
                "acompressor=threshold=-18dB:ratio=3:attack=15:release=180:makeup=2.5dB"
            )
        filters.append(
            f"[{base + j}:a]aresample=48000,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={start_ms}|{start_ms},"
            f"volume={vol}{voice_chain}{fade_parts}{loudnorm_part}{dn_part}[t{j}]")
    if duck:
        # Deterministic ducking: one volume-automation filter per
        # music track. Gain is 1 outside every voice window, the
        # ducked factor inside it, ramping linearly over `fade`
        # seconds before the window (down) and after it (up).
        # The volume filter evaluates the expression per frame
        # (eval=frame) on main-timeline time t, so the automation
        # is sample-accurate to the stated windows.
        import math as _math
        g = 10 ** (-float(duck["amount_db"]) / 20.0)
        fade = float(duck["fade"])
        factors = []
        for w in duck["windows"]:
            s, e = float(w["start"]), float(w["end"])
            if fade <= 0:
                factors.append(
                    f"if(lt(t,{s!r}),1,if(lt(t,{e!r}),{g!r},1))")
            else:
                factors.append(
                    f"if(lt(t,{s - fade!r}),1,"
                    f"if(lt(t,{s!r}),1-(1-{g!r})*(t-({s - fade!r}))/{fade!r},"
                    f"if(lt(t,{e!r}),{g!r},"
                    f"if(lt(t,{e + fade!r}),{g!r}+(1-{g!r})*(t-({e!r}))/{fade!r},1))))")
        gain_expr = "*".join(factors) if factors else "1"
        for j, track in enumerate(audio_tracks):
            if track.get("kind") != "music":
                mix_inputs.append(f"[t{j}]")
                continue
            filters.append(
                f"[t{j}]volume='{gain_expr}':eval=frame[td{j}]")
            mix_inputs.append(f"[td{j}]")
    elif auto_duck and has_voice and has_music:
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
                f"[t{j}][voicemix]sidechaincompress=threshold=0.03:"
                f"ratio=4:attack=40:release=500:knee=4:makeup=1[td{j}]")
            mix_inputs.append(f"[td{j}]")
    else:
        for j in range(len(audio_tracks)):
            mix_inputs.append(f"[t{j}]")
    # Overlay audio (Loop 6 round 4): a sounding overlay is mixed
    # in at its timeline start, for its shown duration, at its
    # volume. It is not ducked: it sits alongside the main mix
    # like a sound effect. Looping overlays loop their trimmed
    # source audio; otherwise the audio is trimmed to exactly the
    # shown duration, so it never outlives the overlay on screen.
    for si, (ov, seg_start, seg_end, idx, audio_idx) in enumerate(overlay_segments):
        if audio_idx is None:
            continue
        ovol = float(ov.get("volume") or 0.0)
        ostart = float(ov["start"])
        odur = float(ov["duration"])
        tin = float(ov["trim_in"])
        tout = float(ov["trim_out"])
        src_dur = tout - tin
        if odur > src_dur + 0.01:
            samples = max(1, int(round(src_dur * 48000)))
            pre = (f"atrim=start={tin}:end={tout},asetpts=PTS-STARTPTS,"
                   f"aresample=48000,aloop=loop=-1:size={samples}:start=0,"
                   f"atrim=end={odur},asetpts=PTS-STARTPTS")
        else:
            pre = (f"atrim=start={tin}:end={tin + odur},"
                   f"asetpts=PTS-STARTPTS,aresample=48000")
        start_ms = int(round(ostart * 1000))
        # v1.37.1: NO apad/atrim/asetpts after adelay, for the same
        # reason as audio tracks (see the SFX note above).
        filters.append(
            f"[{audio_idx}:a]{pre},"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={start_ms}|{start_ms},"
            f"volume={ovol}[ova{si}]")
        mix_inputs.append(f"[ova{si}]")
    # Track 1 audio (v1.44.0, F6a): every track 1 clip's audio
    # chain a[i] is mixed in at its absolute start, at its
    # volume (default 1.0, explicit volume wins). The mix is
    # UNCONDITIONAL: the audio join skips track 1 clips, so a
    # skipped a[i] would be an unconnected filtergraph output
    # (render failure). Image clips and silent videos carry
    # generated silence in a[i], which mixes as a no-op.
    for _ti, _tc in enumerate(clips):
        if _tc.get("track", 0) != 1:
            continue
        _tvol = float(_tc.get("volume", 1.0))
        _tms = int(round(float(_tc.get("start", 0.0)) * 1000))
        filters.append(
            f"[a{_ti}]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={_tms}|{_tms},"
            f"volume={_tvol}[t1a{_ti}]")
        mix_inputs.append(f"[t1a{_ti}]")
    # Procedural SFX (v1.37.0, timing fixed v1.37.1): each
    # element's sfx fires at its element start — text/caption
    # start, clip (transition) start, overlay start — mixed into
    # the final audio by the agent's field alone. Built-ins are
    # gated full-timeline lavfi inputs (no adelay at all); file
    # paths become extra inputs appended here (after the
    # metadata input, so no earlier index shifts) with adelay
    # alone. SFX mix even when the project has no other audio:
    # the clip-silence chain in mix_inputs guarantees a valid
    # audio stream.
    # SFX discipline (v1.42.0, Fix 5): when validate computed
    # an sfx_plan, only its kept entries fire (15s cooldown,
    # attenuated volume, alternating pitch); a hand-built info
    # dict without a plan falls back to the old collect-all
    # behaviour (every sfx, volume 0.35 default, pitch 1.0),
    # so direct graph builders render exactly as before.
    sfx_entries = []
    _plan = info.get("sfx_plan") if isinstance(info, dict) else None
    if isinstance(_plan, dict) and "kept" in _plan:
        for _kept in _plan["kept"]:
            sfx_entries.append((float(_kept["start"]), _kept["name"],
                                float(_kept["volume"]),
                                float(_kept.get("pitch", 1.0))))
    else:
        _clip_starts = info.get("clip_starts") or []
        for _ci, _clip in enumerate(clips):
            if _clip.get("sfx"):
                _st = _clip_starts[_ci] if _ci < len(_clip_starts) else 0.0
                sfx_entries.append((_st, _clip["sfx"],
                                    float(_clip.get("sfx_volume", 0.35)),
                                    1.0))
        for _item in (project.get("texts") or []):
            if isinstance(_item, dict) and _item.get("sfx"):
                sfx_entries.append((float(_item.get("start", 0)),
                                    _item["sfx"],
                                    float(_item.get("sfx_volume", 0.35)),
                                    1.0))
        for _cap in merged_captions:
            if isinstance(_cap, dict) and _cap.get("sfx"):
                sfx_entries.append((float(_cap.get("start", 0)), _cap["sfx"],
                                    float(_cap.get("sfx_volume", 0.35)),
                                    1.0))
        for _ov in overlays:
            if _ov.get("sfx"):
                sfx_entries.append((float(_ov["start"]), _ov["sfx"],
                                    float(_ov.get("sfx_volume", 0.35)),
                                    1.0))
        for _dg in (info.get("diagrams") or []):
            if isinstance(_dg, dict) and _dg.get("sfx"):
                sfx_entries.append((float(_dg["start"]), _dg["sfx"],
                                    float(_dg.get("sfx_volume", 0.35)),
                                    1.0))
    for _si, (_st, _name, _vol, _pitch) in enumerate(sfx_entries):
        _ms = int(round(float(_st) * 1000))
        if _name in SFX_SOURCES:
            _spec = SFX_SOURCES[_name]
            _dur = _spec["duration"]
            _sfx_idx = _input_count(argv)
            if _spec["kind"] == "noise":
                # Noise built-ins (v1.45.0, R3): short anoisesrc
                # sources, adelay-delayed like file SFX (adelay
                # alone preserves the leading silence — see the
                # root-cause note at SFX_SOURCES). Fixed seed:
                # the same name renders the same noise every
                # time. Pitch is atempo BEFORE adelay, so the
                # stretch applies to the short source only.
                # The gain normalises the chain to the design
                # level (see SFX_SOURCES); pitch 1.0 omits
                # atempo (old graph unchanged).
                _atempo = (f"atempo={_pitch},"
                           if _pitch != 1.0 else "")
                argv += ["-f", "lavfi", "-i",
                         f"anoisesrc=color={_spec['color']}"
                         f":duration={_dur}:seed={_spec['seed']}"
                         f":sample_rate=48000"]
                filters.append(
                    f"[{_sfx_idx}:a]pan=stereo|c0=c0|c1=c0,"
                    f"aresample=48000,{_spec['filters']},"
                    f"volume={_spec['gain']},{_atempo}"
                    f"aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"volume={_vol},adelay={_ms}|{_ms}[sfx{_si}]")
            else:
                # Sine built-ins (thud only, v1.45.0): full-
                # timeline gated lavfi INPUTS, not adelay-delayed
                # short sources. The input is appended here,
                # after the metadata input, so no earlier input
                # index shifts. The gated expression
                # (single-quoted: its if() commas are not option
                # separators) is silent outside the window, so no
                # adelay, apad, atrim or asetpts is needed or
                # wanted — see the root-cause note at
                # SFX_SOURCES. The source duration is the whole
                # timeline, so the stream is exactly `total` long.
                # Pitch (v1.42.0): the expression's frequencies
                # are scaled BEFORE gating, so the burst stays at
                # its scheduled start (an atempo/asetrate after
                # the gate would move it).
                _gated = _gated_sfx_expression(
                    _pitch_sfx_expression(_spec["expr"], _pitch),
                    float(_st), _dur)
                argv += ["-f", "lavfi", "-i",
                         f"aevalsrc='{_gated}':duration={total}:sample_rate=48000"]
                filters.append(
                    f"[{_sfx_idx}:a]pan=stereo|c0=c0|c1=c0,aresample=48000,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"volume={_vol}[sfx{_si}]")
        else:
            # File SFX (v1.37.1): adelay alone. Its leading
            # silence frames ARE the delay; atrim after adelay
            # discards them (verified by ashowinfo) and amix
            # then mixes the audio at position 0. The
            # clip-silence chain guarantees the mix length and
            # -t truncates any file that runs past `total`, so
            # no pad/trim is needed here either. Pitch
            # (v1.42.0): atempo BEFORE adelay, so the stretch
            # applies to the short file only, never to the
            # delay; pitch 1.0 omits it (old graph unchanged).
            _sfx_path = resolve_path(_name)
            _sfx_idx = _input_count(argv)
            argv += ["-i", _sfx_path]
            _atempo = f"atempo={_pitch}," if _pitch != 1.0 else ""
            filters.append(
                f"[{_sfx_idx}:a]aresample=48000,{_atempo}"
                f"aformat=sample_rates=48000:channel_layouts=stereo,"
                f"volume={_vol},adelay={_ms}|{_ms}[sfx{_si}]")
        mix_inputs.append(f"[sfx{_si}]")
    if len(mix_inputs) == 1:
        aout = acur_chain
    else:
        filters.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:"
            f"duration=longest:normalize=0[aout]")
        aout = "aout"
    # v1.41.0 (Fix 5): final-master loudnorm, LINEAR mode.
    # render_project measures the finished mix first and
    # passes it in; the linear form applies a constant gain,
    # so the master never pumps. The target is
    # output.loudnorm_i (default -14; -16 selectable).
    # Applied only when the project has audio tracks: on an
    # all-silence timeline loudnorm measures -inf and fails,
    # so silent projects skip mastering rather than erroring
    # (and render_project skips it when the mix itself is
    # unmeasurable).
    if info.get("master_loudnorm") and audio_tracks:
        if skip_master_loudnorm:
            pass
        elif master_loudnorm_measured is None:
            raise ValueError(
                "master loudnorm requires measured values: pass "
                "master_loudnorm_measured (from _run_loudness on "
                "the finished mix) or skip_master_loudnorm=True")
        else:
            _mm = master_loudnorm_measured
            _mi = info.get("loudnorm_i", -14.0)
            filters.append(
                f"[{aout}]loudnorm=I={_mi:g}:TP=-1.5:LRA=11:linear=true:"
                f"measured_I={_mm['input_i']:.2f}:"
                f"measured_TP={_mm['input_tp']:.2f}:"
                f"measured_LRA={_mm['input_lra']:.2f}:"
                f"measured_thresh={_mm['input_thresh']:.2f}[aoutm]")
            aout = "aoutm"
    # Final range conversion (v1.41.0, Fix 1): the ONLY
    # yuv conversion in the graph, at the final node, with
    # the bt709 matrix and lanczos + accurate rounding +
    # full chroma interpolation. -pix_fmt alone can leave
    # yuvj420p (full range) from JPEG inputs; this stage
    # converts to limited-range yuv420p, the format phone
    # players expect. With final_range_convert=False the
    # stage is a passthrough, so segment frames stay exactly
    # the chain-native frames.
    if final_range_convert:
        filters.append(
            f"[{vout}]scale=out_color_matrix=bt709:out_range=tv:"
            f"flags=lanczos+accurate_rnd+full_chroma_int,"
            f"format=yuv420p[vfinal]")
    else:
        filters.append(f"[{vout}]null[vfinal]")
    filter_complex = ";".join(filters)
    if audio_mix_path is not None:
        # Measurement pass: [vfinal] must still be consumed
        # (FFmpeg errors on unconnected filter outputs, so
        # merely omitting the video map fails) — nullsink
        # consumes it without encoding. The video graph
        # therefore still runs (decode + filter, no x264
        # encode), so a mastered render costs roughly one
        # extra video-filter pass; the audio measurement
        # itself is exact because the audio chain is
        # resolution-independent.
        filter_complex += ";[vfinal]nullsink"
    if filter_script_dir:
        os.makedirs(filter_script_dir, exist_ok=True)
        script_path = os.path.join(filter_script_dir, "filter_complex.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(filter_complex)
        argv += [_filter_script_flag(), script_path]
    else:
        argv += ["-filter_complex", filter_complex]
    if audio_mix_path is not None:
        # Measurement pass: only the mixed audio, as PCM.
        # The video filter graph is pruned by FFmpeg since
        # [vfinal] is unmapped, so no video is decoded.
        argv += ["-map", f"[{aout}]", "-c:a", "pcm_s16le",
                 "-ar", "48000",
                 "-t", str(t_override if t_override is not None else total),
                 audio_mix_path]
        return argv, total, text_dir
    argv += ["-map", "[vfinal]", "-map", f"[{aout}]",
             "-c:v", video_codec, "-preset",
             video_preset if video_preset is not None else "medium"]
    # v1.41.0 (Fix 1): quality-first encoding defaults.
    # A preset (or an explicit output.video_bitrate) sets a
    # target bitrate instead of the default constant-quality
    # crf 17. Without it, crf 17 is unchanged, so every
    # existing project renders at the new quality. An
    # explicit video_crf replaces the crf 17 default (a
    # video_bitrate still wins over crf, as before).
    # CRF encodes also carry a resolution-scaled VBV cap
    # (maxrate/bufsize) so bitrate spikes stay playable:
    # 1080p tier 16M/32M, 4K tier 40M/80M, below 1080p 8M/16M
    # (tiers by pixel count, so a 720x1280 portrait render
    # is below-1080p, not 1080p).
    _pixels = width * height
    if _pixels >= 3840 * 2160:
        _cap_m, _buf_m = 40, 80
    elif _pixels >= 1920 * 1080:
        _cap_m, _buf_m = 16, 32
    else:
        _cap_m, _buf_m = 8, 16
    if info.get("video_bitrate"):
        kbps = _bitrate_kbps(info["video_bitrate"])
        argv += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                 "-bufsize", f"{2 * kbps}k"]
    elif video_crf is not None:
        argv += ["-crf", str(video_crf),
                 "-maxrate", f"{_cap_m}M", "-bufsize", f"{_buf_m}M"]
    else:
        argv += ["-crf", "17",
                 "-maxrate", f"{_cap_m}M", "-bufsize", f"{_buf_m}M"]
    argv += [
             # Force the widely playable pixel format. Without this,
             # image inputs (JPEG yuvj) can leak through xfade as
             # yuvj444p / High 4:4:4, which most phone players refuse.
             "-pix_fmt", "yuv420p"]
    if final_range_convert:
        # Output colour metadata — final renders ONLY, for
        # the same reason as -x264-params below: segment
        # renders must keep chain-native flags (unknown),
        # or the assembly's forced scales convert them and
        # direct-vs-cached pixels diverge.
        argv += ["-colorspace", "bt709", "-color_primaries", "bt709",
                 "-color_trc", "bt709"]
    argv += ["-movflags", "+faststart",
             "-c:a", audio_codec]
    if video_codec == "libx264" and final_range_convert:
        # This FFmpeg 8.1.2 build ignores -color_primaries /
        # -color_trc for libx264 (verified: probe shows
        # unknown); the x264 private params land correctly.
        # Final renders ONLY: segment renders (which pass
        # final_range_convert=False) must keep chain-native
        # flags (unknown), or the assembly's forced scales
        # convert them and direct-vs-cached pixels diverge
        # (v1.41.0 regression, caught by the segment tests).
        argv += ["-x264-params",
                 "colorprim=bt709:transfer=bt709:colormatrix=bt709"]
    if audio_codec == "aac":
        argv += ["-b:a", "192k", "-ar", "48000"]
    # -t slack (see t_override doc): total truncates the timeline
    # exactly, but the fps filter's EOF-flushed boundary frame is
    # dropped when -t equals the timeline end, so callers that
    # must keep every chain frame pass a slightly larger -t.
    argv += ["-t", str(t_override if t_override is not None else total)]
    if metadata_idx is not None:
        # Chapters (and only chapters: -map_chapters takes the
        # chapter list from the ffmetadata input; the output's
        # own title/tags still come from the media inputs).
        argv += ["-map_metadata", str(metadata_idx),
                 "-map_chapters", str(metadata_idx)]
    argv += [output_path]
    return argv, total, text_dir


def _remove_quietly(path):
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def render_project(project, output_path, scale_factor=1.0, timeout=600,
                   guides=False, review_timecode=False,
                   video_codec="libx264", video_crf=None,
                   audio_codec="aac", final_range_convert=True,
                   use_filter_script=True, t_override=None,
                   chain_scale_out=None, video_preset=None):
    """Render a project dict. Returns (ok, info_or_error).

    Atomic output: ffmpeg writes to a temp file in the output
    directory, and it is moved into place only on success. A failed
    render therefore never leaves a partial file at output_path, and
    never overwrites a previous good output at that path.

    guides draws safe-area boxes (preview only in practice: callers
    pass False for final renders). Timecode is burned in when
    review_timecode is True OR the project itself sets
    timecode: true; otherwise a final render has no timecode.

    Refactoring options (defaults preserve the old behaviour):
    video_codec "auto" picks the best detected hardware encoder
    (choose_video_codec) and falls back to libx264 with one retry
    when a hardware encode fails; any other non-libx264 codec
    gets the same single libx264 retry. video_crf / audio_codec /
    final_range_convert pass through to build_ffmpeg_args (used
    by segment caching). use_filter_script writes the filter
    graph to a temp file and passes -filter_complex_script
    (identical graph, no argv length limit); False keeps the
    inline -filter_complex string. Signal handlers clean temp
    dirs on SIGINT/SIGTERM (best-effort, main thread only).
    t_override passes through to build_ffmpeg_args (see there);
    it affects only the -t flag, never the timeline.
    chain_scale_out passes through to build_ffmpeg_args (see
    there); segment caching uses it for segment renders.
    """
    install_signal_handlers()
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    # Loudnorm on digital silence (1.34.0): single-pass loudnorm
    # measures -inf, emits NaN samples, and the AAC encoder fails
    # with a cryptic "Input contains (near) NaN" error. Check
    # loudnorm tracks up front with loudnorm's own measurement
    # (one fast pass per loudnorm track, at render time only so
    # validation stays fast) and return a structured error naming
    # the track and the fix, instead of a failed render.
    # volumedetect is NOT the detector here: on this build it
    # reports -91.0 dB (not -inf) for digital silence, while
    # loudnorm's input_i is genuinely -inf — verified 2026-10-29.
    # Non-silent tracks are untouched; an unmeasurable track is
    # left alone and the normal render reports whatever is wrong.
    for track in info.get("audio") or []:
        if not track.get("loudnorm"):
            continue
        loud, loud_ok = _run_loudness(track["src"])
        if not loud_ok:
            continue
        input_i = loud.get("input_i")
        if input_i is None or input_i == float("-inf"):
            return False, {"code": "INVALID_PROJECT",
                           "message": (f"loudnorm audio track {track['src']} is digitally silent; "
                                       "loudnorm cannot measure silence and the render would fail"),
                           "fix": "remove loudnorm from this silent track, or replace it with a non-silent audio file"}
    # Stabilisation pass 1 (vidstabdetect) for stabilized clips:
    # the transform file is computed at the clip's final scale, so
    # pass 2 in the main graph sees identical frames. A failed
    # detect fails the render rather than silently shipping an
    # unstabilized clip.
    stab_dir = None
    stab_clips = [c for c in info["clips"] if c.get("stabilize") and c["kind"] == "video"]
    if stab_clips:
        stab_dir = tempfile.mkdtemp(prefix="agentcut-stab-")
        register_temp_dir(stab_dir)
        tw = _even(info["width"] * scale_factor)
        th = _even(info["height"] * scale_factor)
        for si, clip in enumerate(stab_clips):
            trf = os.path.join(stab_dir, f"stab_{si}.trf")
            vf = (f"trim=start={clip['trim_in']}:end={clip['trim_out']},"
                  f"setpts=PTS-STARTPTS,"
                  f"{_scale_part(clip.get('fit'), tw, th)},"
                  f"vidstabdetect=result='{_esc_filter_arg(trf)}'")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", clip["src"],
                 "-vf", vf, "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=300)
            if proc.returncode != 0 or not os.path.isfile(trf):
                if stab_dir:
                    shutil.rmtree(stab_dir, ignore_errors=True)
                return False, {"code": "RENDER_FAILED",
                               "message": f"stabilisation detect failed for clip {clip.get('id')}: {extract_ffmpeg_error(proc.stderr, max_lines=10)}"}
            clip["_stab_trf"] = trf
    # Pre-scale image clips before the main filter graph: the main
    # graph (especially xfade chains) then decodes small files instead
    # of full-size photos, and the >2560px guard becomes a no-op.
    # A failed pre-scale falls back to the original file, so this can
    # never break a render that would otherwise work.
    prescale_dir = None
    image_clips = [(ci, c) for ci, c in enumerate(info["clips"]) if c["kind"] == "image"]
    if image_clips:
        prescale_dir = tempfile.mkdtemp(prefix="agentcut-prescale-")
        register_temp_dir(prescale_dir)
        tw = _even(info["width"] * scale_factor)
        th = _even(info["height"] * scale_factor)
        for ci, clip in image_clips:
            if clip.get("fit") in ("blur", "blur_pad"):
                # Blurred-background clips need the unpadded source
                # in the main graph (background cover + foreground
                # contain); a contain prescale would bake black bars
                # into the background.
                continue
            if clip.get("camera") or effective_ken_burns(clip, ci, project) not in (None, "none"):
                # Camera paths (v1.40.0) need a COVER prescale
                # like Ken Burns: their main-graph chain zooms
                # from the prescaled frame, and a contain
                # prescale would bake black bars into that frame
                # (caught by the round-1 camera render:
                # letterboxed frames with a dark mean instead
                # of the cover crop). Since v1.46.0 the
                # main-graph chain zooms at output resolution
                # (1x), so the prescale is 1x too — the same
                # as the cover branch below. The main-graph 1x
                # cover scale is then a no-op on the already-1x
                # frame, so framing is identical to rendering
                # from the original.
                pw, ph = tw, th
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
    # Pro pack chapters: write the ffmetadata file the render
    # maps chapters from (deleted in the finally below, like the
    # text/stab/prescale temp dirs). No chapters means no extra
    # input and no metadata mapping at all.
    meta_dir = None
    metadata_path = None
    if info.get("chapters"):
        meta_dir = tempfile.mkdtemp(prefix="agentcut-meta-")
        register_temp_dir(meta_dir)
        metadata_path = os.path.join(meta_dir, "chapters.txt")
        lines = [";FFMETADATA1"]
        for ch in info["chapters"]:
            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={int(round(float(ch['start']) * 1000))}")
            lines.append(f"END={int(round(float(ch['end']) * 1000))}")
            lines.append(f"title={_ffmetadata_escape(ch['title'])}")
        with open(metadata_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    # Animated stroke diagrams (v1.38.0): generate each
    # diagram's transparent draw video before the main graph
    # is built (build_ffmpeg_args composites them as inputs).
    # A failed encode is a structured render error, never a
    # render with a missing diagram.
    diagram_dir = None
    if info.get("diagrams"):
        from .diagrams import encode_diagram_video
        diagram_dir = tempfile.mkdtemp(prefix="agentcut-diagrams-")
        register_temp_dir(diagram_dir)
        for di, dg in enumerate(info["diagrams"]):
            if not dg.get("adapted_to_motion"):
                # v1.45.0, R4b-3: an adapted template diagram has
                # no strokes and no video — the motion element
                # renders it. Its card (below) still generates.
                dg_video = os.path.join(diagram_dir, f"diagram_{di}.mov")
                try:
                    encode_diagram_video(dg, info["fps"], dg_video)
                except RuntimeError as exc:
                    _remove_quietly(tmp_path)
                    shutil.rmtree(diagram_dir, ignore_errors=True)
                    if prescale_dir:
                        shutil.rmtree(prescale_dir, ignore_errors=True)
                    if stab_dir:
                        shutil.rmtree(stab_dir, ignore_errors=True)
                    if meta_dir:
                        shutil.rmtree(meta_dir, ignore_errors=True)
                    return False, {"code": "RENDER_FAILED", "message": str(exc)}
                dg["_video"] = dg_video
            # v1.41.0 Fix 6: static backdrop card, generated at
            # the diagram's canvas size so the graph's dw:-2
            # scale lands it exactly under the diagram. For an
            # adapted diagram the canvas IS the display size
            # (set by the adapter), so the card lands exactly
            # under the motion element.
            if dg.get("card"):
                from .diagrams import (resolve_color, rounded_card_rgba,
                                       write_png_rgba)
                _cc = resolve_color(dg.get("card_color", "black")) or (0, 0, 0)
                _co = float(dg.get("card_opacity", 0.45))
                _card_path = os.path.join(diagram_dir, f"card_{di}.png")
                write_png_rgba(_card_path, int(dg["canvas_w"]),
                               int(dg["canvas_h"]),
                               rounded_card_rgba(int(dg["canvas_w"]),
                                                 int(dg["canvas_h"]),
                                                 _cc, _co))
                dg["_card"] = _card_path
    # Motion graphics (v1.43.0): generate each raster element's
    # transparent animation video before the main graph is built
    # (build_ffmpeg_args composites them as inputs, above
    # diagrams and below texts). A failed encode is a structured
    # render error, never a render with a missing element.
    # Counter elements produce no video (they are a drawtext
    # eif expression), so they are skipped here and rendered by
    # the text loop instead.
    motion_dir = None
    _motion_els = [el for el in (info.get("motion") or [])
                   if el.get("kind") in ("shape", "bars", "donut", "pie",
                                         "line_chart", "lower_third",
                                         "comparison_cards")]
    if _motion_els:
        from .motion import encode_motion_video
        motion_dir = tempfile.mkdtemp(prefix="agentcut-motion-")
        register_temp_dir(motion_dir)
        for mi, el in enumerate(_motion_els):
            el_video = os.path.join(motion_dir, f"motion_{mi}.mov")
            try:
                encode_motion_video(el, info["fps"], el_video)
            except RuntimeError as exc:
                _remove_quietly(tmp_path)
                shutil.rmtree(motion_dir, ignore_errors=True)
                if diagram_dir:
                    shutil.rmtree(diagram_dir, ignore_errors=True)
                if prescale_dir:
                    shutil.rmtree(prescale_dir, ignore_errors=True)
                if stab_dir:
                    shutil.rmtree(stab_dir, ignore_errors=True)
                if meta_dir:
                    shutil.rmtree(meta_dir, ignore_errors=True)
                return False, {"code": "RENDER_FAILED", "message": str(exc)}
            el["_video"] = el_video
    filter_dir = None
    if use_filter_script:
        filter_dir = tempfile.mkdtemp(prefix="agentcut-filter-")
        register_temp_dir(filter_dir)
    effective_timecode = bool(review_timecode or project.get("timecode"))

    def _build(codec, measured=None, skip_master=False,
               mix_path=None):
        """Build argv for `codec`. Returns (True, (argv, total,
        text_dir)) or (False, structured_error) for font failures
        (defence in depth behind validation); other ValueErrors
        still propagate, as before."""
        try:
            built = build_ffmpeg_args(
                project, info, tmp_path, scale_factor,
                metadata_path=metadata_path, guides=guides,
                review_timecode=effective_timecode,
                filter_script_dir=filter_dir,
                video_codec=codec, video_crf=video_crf,
                audio_codec=audio_codec,
                final_range_convert=final_range_convert,
                t_override=t_override,
                chain_scale_out=chain_scale_out,
                master_loudnorm_measured=measured,
                skip_master_loudnorm=skip_master,
                audio_mix_path=mix_path,
                video_preset=video_preset)
        except ValueError as exc:
            if str(exc).startswith("FONT_NOT_FOUND"):
                return False, {"code": "INVALID_PROJECT", "message": str(exc),
                               "fix": "use the bundled fonts/ directory, or set "
                                      "AGENTCUT_FONT_DIR to a directory with the "
                                      "needed font"}
            raise
        return True, built

    codec_used = choose_video_codec(video_codec)
    # v1.41.0 (Fix 5): master loudnorm is measured FIRST.
    # A mix-only pass (audio graph only — the video graph
    # is pruned because [vfinal] is unmapped, so this is
    # fast even for long timelines) renders the finished
    # mix to a temp WAV, which _run_loudness measures; the
    # final graph then uses the LINEAR form with those
    # values. If the mix is unmeasurable (silence), the
    # master stage is SKIPPED, never single-passed (NaN
    # risk, v1.34.0 guard). The temp WAV lives beside the
    # output (workspace, not the 512M /tmp tmpfs) and is
    # always removed.
    master_measured = None
    master_skip = False
    if info.get("master_loudnorm") and (info.get("audio") or []):
        _mfd, _mix_path = tempfile.mkstemp(prefix="agentcut-mix-",
                                           suffix=".wav",
                                           dir=os.path.dirname(tmp_path))
        os.close(_mfd)
        try:
            _mix_ok, _mix_built = _build(codec_used, skip_master=True,
                                         mix_path=_mix_path)
            if _mix_ok:
                _mix_argv, _, _mix_td = _mix_built
                if _mix_td:
                    shutil.rmtree(_mix_td, ignore_errors=True)
                _mproc = subprocess.run(_mix_argv, capture_output=True,
                                        text=True, timeout=timeout)
                if _mproc.returncode == 0:
                    _meas, _mok = _run_loudness(_mix_path)
                    if _mok and _meas:
                        import math as _math2
                        _iv = (_meas.get("input_i"),
                               _meas.get("input_tp"),
                               _meas.get("input_lra"),
                               _meas.get("input_thresh"))
                        if (all(v is not None and _math2.isfinite(v)
                                for v in _iv) and _iv[0] > -70):
                            master_measured = _meas
        finally:
            _remove_quietly(_mix_path)
        master_skip = master_measured is None
    built_ok, built = _build(codec_used, measured=master_measured,
                             skip_master=master_skip)
    if not built_ok:
        _remove_quietly(tmp_path)
        if diagram_dir:
            shutil.rmtree(diagram_dir, ignore_errors=True)
        if motion_dir:
            shutil.rmtree(motion_dir, ignore_errors=True)
        if filter_dir:
            shutil.rmtree(filter_dir, ignore_errors=True)
            unregister_temp_dir(filter_dir)
        return False, built
    argv, total, text_dir = built
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 and codec_used != "libx264":
            # Hardware fallback: the detected encoder failed at
            # runtime (missing driver, unsupported profile, ...).
            # Rebuild the identical graph with libx264 and retry
            # exactly once; a second failure is reported, never
            # retried again. The first build's text/filter dirs
            # are removed first (the rebuild writes fresh ones),
            # and the finally below cleans the rebuild's dirs.
            if text_dir:
                shutil.rmtree(text_dir, ignore_errors=True)
            if filter_dir:
                shutil.rmtree(filter_dir, ignore_errors=True)
                unregister_temp_dir(filter_dir)
                filter_dir = tempfile.mkdtemp(prefix="agentcut-filter-")
                register_temp_dir(filter_dir)
            rebuilt_ok, rebuilt = _build("libx264",
                                         measured=master_measured,
                                         skip_master=master_skip)
            if not rebuilt_ok:
                _remove_quietly(tmp_path)
                return False, rebuilt
            argv, total, text_dir = rebuilt
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _remove_quietly(tmp_path)
        return False, {"code": "RENDER_FAILED", "message": "render timed out"}
    finally:
        if text_dir:
            shutil.rmtree(text_dir, ignore_errors=True)
        if prescale_dir:
            shutil.rmtree(prescale_dir, ignore_errors=True)
            unregister_temp_dir(prescale_dir)
        if stab_dir:
            shutil.rmtree(stab_dir, ignore_errors=True)
            unregister_temp_dir(stab_dir)
        if meta_dir:
            shutil.rmtree(meta_dir, ignore_errors=True)
            unregister_temp_dir(meta_dir)
        if diagram_dir:
            shutil.rmtree(diagram_dir, ignore_errors=True)
            unregister_temp_dir(diagram_dir)
        if motion_dir:
            shutil.rmtree(motion_dir, ignore_errors=True)
            unregister_temp_dir(motion_dir)
        if filter_dir:
            shutil.rmtree(filter_dir, ignore_errors=True)
            unregister_temp_dir(filter_dir)
    if proc.returncode != 0:
        _remove_quietly(tmp_path)
        return False, {"code": "RENDER_FAILED", "message": extract_ffmpeg_error(proc.stderr)}
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


# --- Segment caching (architectural refactoring) ----------------

# Bump this whenever the segment encoding changes (codec, range
# handling, audio format, t slack, chain scale force): the version
# is part of every fingerprint, so old segments are never reused
# after a change.
# v3 (2026-10-31): chain scales forced to the direct graph's
# common format (see _first_clip_chain_format); v2 segments were
# unforced (image segments came out pc) and must not be reused.
# v2 (2026-10-31): chain-native ranges (final_range_convert=False)
# + multi-clip filter-concat assembly; v1 segments were tv-converted
# for a demuxer join and must not be reused by v2.
SEGMENT_CACHE_VERSION = "agentcut-segment-v3"
SEGMENT_CONTAINER = "mkv"
_SEGMENT_VOLATILE_KEYS = {"probe", "_stab_trf", "_video"}


def _file_stat_fingerprint(path):
    """Identity + staleness of one file: absolute path, mtime_ns,
    size. mtime_ns (not seconds) so a rewrite within the same
    second still invalidates; size so a same-mtime rewrite of
    different length still invalidates. Returns None when the
    file cannot be stat'ed (caller treats that as uncacheable)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"path": os.path.abspath(path),
            "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def _first_clip_chain_format(info):
    """(out_range, out_color_matrix) the direct graph's clip-chain
    scales produce, predicted from the FIRST clip alone: the
    concat filter negotiates one common format, and FFmpeg's
    negotiation follows the first input. An image first clip
    decodes to RGB, and its scale outputs unknown/unknown
    (measured on the direct graph: rgb24 gbr/pc -> yuv420p
    unknown/unknown). A video first clip keeps its probed
    flags (a phone bt709/tv clip negotiates bt709/tv; an
    unknown-flag clip negotiates unknown). Values are
    normalised to scale-filter spellings (jpeg->pc,
    mpeg->tv, bt601->bt470bg); anything unrecognised falls
    back to unknown, which preserves the input (no
    conversion) — the safe default. Segment renders force
    their scales to this format so segment pixels equal the
    direct chain's pixels exactly; without the force, a
    single-clip segment render negotiates differently (image
    segments came out pc and decoded as yuvj420p, pulling the
    assembly's scales to bt470bg/pc and re-converting video
    frames with a second 8-bit rounding)."""
    clips = info.get("clips") or []
    if not clips:
        return ("unknown", "2")
    first = clips[0]
    if first.get("kind") != "video":
        return ("unknown", "2")
    probe = first.get("probe") or {}
    raw = probe.get("raw") or {}
    stream = None
    for st in raw.get("streams", []) or []:
        if st.get("codec_type") == "video":
            stream = st
            break
    if not stream:
        return ("unknown", "2")
    r = str(stream.get("color_range") or "unknown").lower()
    c = str(stream.get("color_space") or "unknown").lower()
    r = {"jpeg": "pc", "mpeg": "tv", "full": "pc", "limited": "tv"}.get(r, r)
    c = {"bt601": "bt470bg"}.get(c, c)
    if r not in ("tv", "pc", "unknown"):
        r = "unknown"
    # The scale filter's out_color_matrix has no "unknown" name
    # (it accepts auto/-1 and the named spaces); the numeric
    # value 2 is UNSPECIFIED, which is what ffprobe reports as
    # "unknown" and what the direct graph's scales output.
    if c not in ("bt709", "fcc", "bt470bg", "smpte170m",
                 "smpte240m", "bt2020nc", "bt2020c", "smpte2085",
                 "chromium", "ycgco"):
        c = "2"
    return (r, c)


def segment_fingerprint(clip, index, project, info, scale_factor):
    """SHA-256 hex identifying one clip's rendered segment, or
    None when the clip cannot be fingerprinted.

    The fingerprint covers EVERYTHING that affects the segment's
    frames: the clip dict (minus volatile keys), the clip's
    timeline index (default_image_motion alternates by index),
    the source file's stat, a LUT file's stat when present, a
    reframe data file's stat when present (v1.44.0: the crop
    windows live in that file, so a rewritten JSON must
    invalidate the cache), the output geometry/fps, the scale
    factor, and the default_image_motion flag. probe is
    deliberately EXCLUDED:
    it is derived from the source file, whose stat is already
    fingerprinted, and it must never carry anything volatile.
    A stale cache can therefore only win if a file changed
    without changing mtime_ns or size AND the clip settings are
    identical — i.e. the file is byte-identical for rendering
    purposes. The version marker lets a future format change
    invalidate every old segment at once."""
    clean = {k: v for k, v in clip.items()
             if k not in _SEGMENT_VOLATILE_KEYS}
    src_stat = _file_stat_fingerprint(clip["src"])
    if src_stat is None:
        return None
    lut_stat = None
    if clip.get("lut"):
        lut_stat = _file_stat_fingerprint(resolve_path(clip["lut"]))
        if lut_stat is None:
            return None
    reframe_stat = None
    if clip.get("reframe_file"):
        reframe_stat = _file_stat_fingerprint(clip["reframe_file"])
        if reframe_stat is None:
            return None
    payload = {
        "version": SEGMENT_CACHE_VERSION,
        "clip": clean,
        "index": index,
        "src": src_stat,
        "lut": lut_stat,
        "reframe": reframe_stat,
        "width": info["width"], "height": info["height"],
        "fps": info["fps"],
        "scale_factor": scale_factor,
        "color_pipeline": info.get("color_pipeline", "yuv"),
        "default_image_motion": bool((project or {}).get("default_image_motion")),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def segment_cache_qualification(project, info):
    """(qualifies, reason) for segment caching a validated project.

    Qualifies only when every clip is transition-free and
    non-stabilized. Transitions join clips inside the graph
    (xfade/fade need both neighbours at once, so the clips are
    not independently renderable segments); stabilized clips
    recompute a vidstab transform per render, which is not a
    cacheable unit. Everything else — texts, captions, overlays,
    diagrams, audio tracks, ducking, scrims, chapters — is
    timeline-level and applies identically in the assembly pass,
    so it does not affect qualification, except final-master
    loudnorm (see below). A project that does not qualify is
    not an error: render_project_cached falls back to a plain
    render_project with a note."""
    for clip in info["clips"]:
        if clip.get("transition") not in (None, "none"):
            return False, (f"clip {clip.get('id')} uses transition "
                           f"{clip.get('transition')!r}; segment caching "
                           f"needs transition-free clips")
        if clip.get("stabilize"):
            return False, (f"clip {clip.get('id')} uses stabilization; "
                           f"its transform is recomputed per render")
    if info.get("master_loudnorm"):
        # Delta: final-master loudnorm measures the finished mix
        # as a whole; applying it per segment would master each
        # segment separately, which is not the same mix. Such
        # projects fall back to a plain render (correct output,
        # just uncached).
        return False, ("output.master_loudnorm is set; final-master "
                       "loudnorm measures the whole mix, so segment "
                       "caching is skipped")
    return True, ""


def _concat_escape(path):
    """Escape one path for an ffmpeg concat-demuxer list file."""
    return path.replace("'", "'\\''")


def _join_segments(segment_paths, joined_path, timeout=600):
    """Join rendered segments into one file with the concat
    demuxer, stream copy (no re-encode: frames and samples are
    already final). Returns (ok, error_or_None). The list file
    is written next to the output and removed afterwards.

    NOTE: render_project_cached does NOT use this join for its
    assembly (it uses multi-clip assembly instead). Measured
    2026-10-31: the demuxer offsets each segment by the file
    duration, and a segment's video stream runs one frame
    longer than its audio (the t slack), so the next
    segment's audio starts one frame late and the assembly's
    atrim then cuts the last frame of tail audio (0.032s at
    24fps). Kept as a utility for callers that join
    same-duration streams, where the gap cannot occur."""
    list_path = joined_path + ".list.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for path in segment_paths:
                f.write(f"file '{_concat_escape(path)}'\n")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", list_path, "-map", "0:v", "-map", "0:a?", "-c", "copy",
             joined_path],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"code": "RENDER_FAILED",
                       "message": "segment join timed out"}
    finally:
        _remove_quietly(list_path)
    if proc.returncode != 0:
        return False, {"code": "RENDER_FAILED",
                       "message": f"segment join failed: "
                                  f"{extract_ffmpeg_error(proc.stderr, max_lines=10)}"}
    return True, None


def render_project_cached(project, output_path, scale_factor=1.0,
                          timeout=600, cache_dir=None, use_cache=True,
                          video_codec="libx264", video_crf=None,
                          audio_codec="aac", final_range_convert=True,
                          use_filter_script=True, video_preset=None):
    """Render a project with per-clip segment caching.

    Returns (ok, info_or_error) like render_project, plus
    cached (bool), cache_note (str), segments_reused and
    segments_rendered counts on success. The video_codec /
    video_crf / audio_codec / final_range_convert /
    use_filter_script arguments pass through to the ASSEMBLY
    render (and to the plain-render fallback); segment renders
    always use lossless libx264 crf 0 + pcm_f32le with
    chain-native ranges (final_range_convert=False),
    regardless of these arguments. Segment bytes therefore
    never depend on a user setting: the fingerprint (clip
    settings + source stats + output geometry + scale factor)
    covers everything that can change a segment, so a stale
    segment cannot win.

    How it works: each transition-free clip is rendered once
    as a lossless segment (libx264 crf 0 + pcm_f32le,
    chain-native ranges, so segment frames are exactly the
    clip chain's output and segment audio samples are
    exactly the resampler's float output — s16 would
    quantize them by ~1 LSB) into
    <cache_dir>/seg_<sha256>.mkv. Segment and assembly
    renders pass -t slack of one frame (see t_override):
    without it the fps filter's EOF-flushed boundary frame
    is dropped and a cached render comes out one frame
    short. Unchanged clips reuse their cached segment; the
    assembly is then a project whose clips ARE the segment
    files (one video clip per segment, trimmed to the
    original clip duration), carrying the original texts,
    captions, overlays, diagrams, audio, ducking, scrims,
    chapters and output settings. Default cache_dir is
    .agentcut_cache next to the output file; it is a pure
    cache (safe to delete any time) and must be excluded
    from any sync set. Segment scales are forced to the
    direct graph's common chain format (see
    _first_clip_chain_format): without the force, a
    single-clip segment render negotiates a different scale
    output than the multi-clip direct graph, and the
    assembly's scales then re-convert with a second 8-bit
    rounding (measured: up to 8 levels on saturated video
    colours). With the force, segment pixels equal direct
    chain pixels, and the assembly graph is the direct graph
    with segment files standing in for original sources.

    Why multi-clip assembly, not a demuxer join: the
    segments are first joined with the concat demuxer
    (stream copy) during development, and it was measured
    to shift audio by one video frame at every boundary
    (0.032s at 24fps): a segment's video stream runs one
    frame longer than its audio (the t slack), and the
    demuxer offsets the next segment by the file duration,
    so the next segment's audio starts a frame late and
    the assembly's atrim then cuts the last frame of tail
    audio. The concat FILTER joins by frame/sample count,
    not container duration, so it aligns audio exactly —
    and it is also what the direct graph uses, so the
    assembly graph is structurally the direct graph with
    segment files standing in for original sources. With
    chain-native segments the assembly's concat sees the
    same mixed ranges the direct graph's concat sees (pc
    for images, unknown for video), and the assembly's
    final range conversion therefore reproduces the direct
    render's conversion exactly. Each segment file holds
    one track, so its range flag is uniform — there is no
    mixed-range problem inside a segment file.

    Safety: a cache problem never fails a render — an
    unusable cache dir, an unstatable source, or a
    non-qualifying project falls back to plain render_project
    with cached=False and a note. A segment that fails to
    render IS a render failure (the same clip would fail a
    direct render too) and is reported, not hidden behind a
    fallback. Segments are written to a temp file and moved
    into place atomically, so a crashed render never leaves a
    partial segment for a later render to reuse."""
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}

    def _plain(note):
        ok, rinfo = render_project(project, output_path,
                                   scale_factor=scale_factor,
                                   timeout=timeout,
                                   video_codec=video_codec,
                                   video_crf=video_crf,
                                   audio_codec=audio_codec,
                                   final_range_convert=final_range_convert,
                                   use_filter_script=use_filter_script,
                                   video_preset=video_preset)
        if ok:
            rinfo = dict(rinfo, cached=False, cache_note=note,
                         segments_reused=0, segments_rendered=0)
        return ok, rinfo

    qualifies, reason = segment_cache_qualification(project, info)
    if not qualifies or not use_cache:
        return _plain(reason or "cache disabled")
    if cache_dir is None:
        cache_dir = os.path.join(
            os.path.dirname(os.path.abspath(output_path)), ".agentcut_cache")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as exc:
        return _plain(f"cache dir unavailable: {exc}")

    segment_paths = []
    reused = 0
    rendered = 0
    for ci, clip in enumerate(info["clips"]):
        fp = segment_fingerprint(clip, ci, project, info, scale_factor)
        if fp is None:
            return _plain(f"clip {clip.get('id')} cannot be fingerprinted")
        seg_path = os.path.join(cache_dir, f"seg_{fp}.{SEGMENT_CONTAINER}")
        if os.path.isfile(seg_path) and os.path.getsize(seg_path) > 0:
            reused += 1
        else:
            # Resolve the clip's effective motion into the
            # segment project: default_image_motion alternates
            # by timeline index, but a segment project has one
            # clip at index 0, so an explicit ken_burns carries
            # the original index's motion into the segment.
            # The segment clip comes from the RAW project clip
            # (matched by id), not the validated info clip:
            # info clips carry resolved fields like probe that
            # a fresh validation round must not see.
            raw_by_id = {c.get("id"): c for c in project["clips"]
                         if isinstance(c, dict)}
            if clip.get("id") in raw_by_id:
                seg_clip = copy.deepcopy(raw_by_id[clip["id"]])
            else:
                seg_clip = {k: v for k, v in copy.deepcopy(clip).items()
                            if k not in _SEGMENT_VOLATILE_KEYS}
            eff = effective_ken_burns(clip, ci, project)
            if eff not in (None, "none") and not clip.get("ken_burns"):
                seg_clip["ken_burns"] = eff
            seg_project = {
                "id": f"segment-{ci}",
                "version": 1,
                "clips": [seg_clip],
                "output": {"width": info["width"],
                           "height": info["height"],
                           "fps": info["fps"]},
            }
            if project.get("default_image_motion") is not None:
                seg_project["default_image_motion"] = \
                    project["default_image_motion"]
            fd, tmp_seg = tempfile.mkstemp(prefix="agentcut-seg-",
                                           suffix=f".{SEGMENT_CONTAINER}",
                                           dir=cache_dir)
            os.close(fd)
            ok, err = render_project(
                seg_project, tmp_seg, scale_factor=scale_factor,
                timeout=timeout, video_codec="libx264", video_crf=0,
                audio_codec="pcm_f32le", final_range_convert=False,
                chain_scale_out=_first_clip_chain_format(info),
                t_override=float(clip["duration"]) + 1.0 / info["fps"])
            if not ok:
                _remove_quietly(tmp_seg)
                return False, err
            try:
                os.replace(tmp_seg, seg_path)
            except OSError:
                _remove_quietly(tmp_seg)
                return False, {"code": "RENDER_FAILED",
                               "message": f"cannot store segment {seg_path}"}
            rendered += 1
        segment_paths.append(seg_path)

    # Multi-clip assembly: each segment file becomes one video clip,
    # trimmed to the original clip's timeline duration. The assembly
    # graph then joins them with the concat filter — exactly like
    # the direct graph — so audio aligns by sample count and the
    # final range conversion sees the same mixed input as direct.
    # (The concat-demuxer join in _join_segments is kept as a
    # utility but is NOT used here: it offsets by container
    # duration and shifts audio a frame late at every boundary.)
    assembly = dict(project)
    assembly["clips"] = [{"id": f"segment-{ci}", "kind": "video",
                          "src": segment_paths[ci],
                          "trim_in": 0,
                          "trim_out": info["clips"][ci]["duration"]}
                         for ci in range(len(segment_paths))]
    ok, rinfo = render_project(assembly, output_path,
                               scale_factor=scale_factor,
                               timeout=timeout,
                               video_codec=video_codec,
                               video_crf=video_crf,
                               audio_codec=audio_codec,
                               final_range_convert=final_range_convert,
                               use_filter_script=use_filter_script,
                               video_preset=video_preset,
                               t_override=info["total_duration"] + 1.0 / info["fps"])
    if ok:
        rinfo = dict(rinfo, cached=True, cache_note="",
                     segments_reused=reused, segments_rendered=rendered)
    return ok, rinfo


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


def render_contact_sheet(video_path, output_path, project=None,
                         columns=4, rows=3):
    """Extract 12 key frames from a rendered video into a 4x3
    grid PNG with MM:SS timestamps (v1.45.0, R9) — the agent's
    self-inspection sheet.

    video_path is a rendered video file; output_path is the
    PNG to write; project (optional) is a project dict (or a
    path to one) whose timeline picks the SMART timestamps:
    chapter starts, diagram completions (start + draw),
    motion-element starts/ends and text starts. Without a
    project (or when it yields fewer than 12 moments) the
    remaining slots are evenly spaced across the duration.
    Each thumbnail is scaled to a quarter of the video width
    (a 720-wide video gives 180px thumbnails, 4 across =
    720px wide; a 1280-wide video gives 320px thumbnails)
    and stamped MM:SS (drawtext, bottom-left, boxed so it
    reads on any background). The grid is a single PNG via
    xstack. Returns (ok, info) where info carries output,
    frames [{time, label}], width, height and the smart
    moments used. A missing/unreadable video or a video
    with no measurable duration is a structured error, not
    a traceback.
    """
    from .validate import probe_media
    src = resolve_path(video_path) if isinstance(video_path, str) else None
    if not src or not os.path.isfile(src):
        return False, {"code": "FILE_NOT_FOUND",
                       "message": f"contact sheet video not found: {video_path}",
                       "fix": "pass a rendered video file path"}
    probe = probe_media(src)
    if probe is None or probe.get("duration") is None \
            or float(probe["duration"]) <= 0:
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"contact sheet video has no measurable duration: {video_path}",
                       "fix": "pass a rendered video ffprobe can read"}
    dur = float(probe["duration"])
    vw = int(probe.get("width") or 720)
    vh = int(probe.get("height") or 1280)
    n = int(columns) * int(rows)
    # Smart moments from the project timeline (if any).
    moments = []
    proj = None
    if isinstance(project, dict):
        proj = project
    elif isinstance(project, str) and os.path.isfile(resolve_path(project)):
        try:
            with open(resolve_path(project), encoding="utf-8") as fh:
                proj = json.load(fh)
        except Exception:
            proj = None
    if isinstance(proj, dict):
        for ch in (proj.get("chapters") or []):
            if isinstance(ch, dict) and ch.get("start") is not None:
                moments.append(float(ch["start"]))
        for dg in (proj.get("diagrams") or []):
            if not isinstance(dg, dict):
                continue
            try:
                ds = float(dg.get("start", 0))
                dd = float(dg.get("draw", 0) or 0)
                moments.append(min(ds + dd, dur))
            except (TypeError, ValueError):
                pass
        for el in (proj.get("motion") or []):
            if not isinstance(el, dict):
                continue
            try:
                moments.append(float(el.get("start", 0)))
                moments.append(float(el.get("end", 0)))
            except (TypeError, ValueError):
                pass
        for tx in (proj.get("texts") or []):
            if not isinstance(tx, dict):
                continue
            try:
                moments.append(float(tx.get("start", 0)))
            except (TypeError, ValueError):
                pass
    # Clamp to [0, dur), dedupe within 1s, sort.
    moments = sorted({round(m, 3) for m in moments
                      if 0 <= m < dur})
    deduped = []
    for m in moments:
        if not deduped or m - deduped[-1] >= 1.0:
            deduped.append(m)
    moments = deduped
    if len(moments) > n:
        # Spread the pick across the whole video, not just
        # the first 12 moments.
        step = len(moments) / n
        moments = [moments[min(len(moments) - 1, int(i * step))]
                   for i in range(n)]
        moments = sorted(set(moments))
    if len(moments) < n:
        # Fill the remaining slots evenly across the duration.
        need = n - len(moments)
        fill = [round(dur * (i + 1) / (need + 1), 3) for i in range(need)]
        moments = sorted(set(moments + fill))
        # If dedupe still left us short (a fill collided with
        # a smart moment), top up with evenly spaced times.
        while len(moments) < n:
            cand = round(dur * len(moments) / n, 3)
            if cand not in moments:
                moments.append(cand)
            moments = sorted(moments)
            if len(moments) >= n:
                break
            cand = round(dur * (len(moments) + 0.5) / n, 3)
            if cand not in moments:
                moments.append(cand)
            moments = sorted(moments)
    moments = sorted(moments)[:n]
    # Extract the frames (one fast seek per frame, like the
    # preview path) into a temp dir.
    tmp = tempfile.mkdtemp(prefix="agentcut-contact-")
    frames = []
    try:
        for k, t in enumerate(moments):
            fp = os.path.join(tmp, f"f{k:02d}.png")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", str(t),
                 "-i", src, "-frames:v", "1", fp],
                capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and os.path.isfile(fp):
                mm = int(t // 60)
                ss = int(t % 60)
                frames.append({"time": t, "label": f"{mm:02d}:{ss:02d}",
                               "path": fp})
        if len(frames) < n:
            return False, {"code": "RENDER_FAILED",
                           "message": f"contact sheet extracted {len(frames)}/{n} frames from {video_path}",
                           "fix": "check the video is a readable render"}
        # Thumbnail size: a quarter of the video width, aspect
        # preserved. The grid is columns x rows via xstack.
        # The MM:SS label goes through a textfile (not text=):
        # the colon in "00:07" would be parsed as an option
        # separator inside a filter description ("No option
        # name near '00:fontsize=...'").
        tw = max(2, (vw // int(columns)) // 2 * 2)
        th = max(2, int(round(tw * vh / vw)) // 2 * 2)
        font = find_font_for_text("00:00") or find_font()
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for f in frames:
            cmd += ["-i", f["path"]]
        parts = []
        for i, f in enumerate(frames):
            lf = os.path.join(tmp, f"label{i:02d}.txt")
            with open(lf, "w", encoding="utf-8") as fh:
                fh.write(f["label"])
            parts.append(
                f"[{i}:v]scale={tw}:{th},drawtext=fontfile='{_esc_filter_arg(font)}':"
                f"textfile='{_esc_filter_arg(lf)}':fontsize=28:fontcolor=white:"
                f"box=1:boxcolor=black@0.6:boxborderw=8:"
                f"x=8:y=h-44[v{i}]")
        layout = "|".join(
            f"{(i % int(columns)) * tw}_{(i // int(columns)) * th}"
            for i in range(len(frames)))
        parts.append(
            f"{''.join(f'[v{i}]' for i in range(len(frames)))}"
            f"xstack=inputs={len(frames)}:layout={layout}[out]")
        cmd += ["-filter_complex", ";".join(parts),
                "-map", "[out]", "-frames:v", "1", output_path]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=120)
        if proc.returncode != 0 or not os.path.isfile(output_path):
            return False, {"code": "RENDER_FAILED",
                           "message": "contact sheet grid failed: "
                                      + proc.stderr[-400:],
                           "fix": "check the video frames extract cleanly"}
        return True, {"output": output_path,
                      "frames": [{"time": f["time"], "label": f["label"]}
                                 for f in frames],
                      "width": tw * int(columns),
                      "height": th * int(rows),
                      "video": src, "duration": dur}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def preview_project(project, out_dir, scale_factor=0.5, guides=True,
                    timecode=False):
    """Render a proxy and extract frames + a contact sheet.

    Pro pack review aids: the proxy carries title-safe/action-safe
    guide boxes by default (guides=True) and, when timecode=True
    (or the project itself sets timecode), a running timecode
    burn-in. Both are preview-only in effect: the result flags
    them, and a final render_project call with default arguments
    has neither, so previewing can never leak guides or a clock
    into a delivery. A project that explicitly sets timecode:
    true DOES get timecode in its final render — that is the
    user's explicit choice, not a preview leak.
    """
    os.makedirs(out_dir, exist_ok=True)
    proxy = os.path.join(out_dir, "proxy.mp4")
    ok, info = render_project(project, proxy, scale_factor=scale_factor,
                              guides=guides, review_timecode=timecode)
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
    effective_timecode = bool(timecode or project.get("timecode"))
    return True, {"proxy": proxy, "frames": frames,
                  "contact_sheet": contact if os.path.isfile(contact) else None,
                  "duration": total,
                  "guides": bool(guides),
                  "timecode": effective_timecode,
                  "safe_areas": ({"action_safe": "5% margin",
                                  "title_safe": "10% margin"}
                                 if guides else None)}


FORMATS = {
    "vertical": (720, 1280),
    "square": (720, 720),
    "landscape": (1280, 720),
}

# Pro pack delivery presets. Each maps to a size, fps, video
# bitrate and loudness target. These are delivery settings for
# the render, not platform guarantees: a platform may still
# re-encode on upload. A brief's explicit size (format, or
# width/height) always wins over a preset's size — see
# produce_video — and the result records which source won, so a
# preset can never silently override an explicit request.
DELIVERY_PRESETS = {
    "youtube_1080": {"width": 1920, "height": 1080, "fps": 30,
                     "video_bitrate": "8M", "loudness_target": -14.0},
    "shorts_720": {"width": 720, "height": 1280, "fps": 30,
                   "video_bitrate": "5M", "loudness_target": -14.0},
    "instagram_square": {"width": 1080, "height": 1080, "fps": 30,
                         "video_bitrate": "5M", "loudness_target": -16.0},
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



def bed_audibility_gap(voice_lufs, music_lufs, music_volume, duck_db):
    """Voice-to-bed gap in dB for a deterministic ducking mix.

    bed_effective = music_lufs + 20*log10(music_volume) - duck_db;
    gap = voice_lufs - bed_effective. A muted bed (volume 0)
    gives an infinite gap. Pure arithmetic, no ffmpeg, so it is
    unit-testable without audio files.
    """
    import math
    if music_volume <= 0:
        return float("inf")
    bed = music_lufs + 20.0 * math.log10(music_volume) - duck_db
    return voice_lufs - bed


def check_music_bed(info):
    """Producer QA helper (v1.41.0): is the music bed actually
    audible under narration, or only present in the file?

    Returns None when the mix has no deterministic ducking (the
    sidechain path's depth varies with the voice, so no honest
    number exists), when voice/music tracks are missing, or when
    either input's loudness cannot be measured. Otherwise a dict
    with voice_lufs, music_lufs, music_volume, duck_db,
    bed_lufs, gap_db and warn (gap > 18 dB — the threshold the
    AI-race mix crossed at 22.8 dB and the ear confirmed as
    silence). The warning threshold is deliberately below the
    old mix's gap and above any healthy bed (a bed 6–12 dB
    under narration is audible; 18+ dB under is not, on phone
    speakers especially for low-frequency beds).
    """
    duck = (info or {}).get("ducking")
    if not duck:
        return None
    tracks = (info or {}).get("audio") or []
    voice = next((t for t in tracks if t.get("kind") == "voice"), None)
    music = next((t for t in tracks if t.get("kind") == "music"), None)
    if voice is None or music is None:
        return None
    v, vok = _run_loudness(resolve_path(voice["src"]))
    m, mok = _run_loudness(resolve_path(music["src"]))
    if not vok or not mok or v.get("input_i") is None \
            or m.get("input_i") is None:
        return None
    vol = float(music.get("volume", 1.0))
    duck_db = float(duck["amount_db"])
    gap = bed_audibility_gap(v["input_i"], m["input_i"], vol, duck_db)
    import math as _math
    bed = (m["input_i"] + 20.0 * _math.log10(vol) - duck_db) \
        if vol > 0 else float("-inf")
    return {"voice_lufs": v["input_i"], "music_lufs": m["input_i"],
            "music_volume": vol, "duck_db": duck_db, "bed_lufs": bed,
            "gap_db": gap, "warn": bool(gap > 18.0)}


def _run_loudness(path, timeout=60):
    """Measured loudness via loudnorm print_format=json.

    Returns (dict_or_None, ok). The dict carries input_i (LUFS),
    input_tp (true peak dBTP as reported by loudnorm), input_lra
    and input_thresh as floats when parseable. This measures the
    finished file; it never changes it.
    """
    cmd = ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None, False
    text = proc.stderr or ""
    start = text.find("{", text.find("Parsed_loudnorm"))
    if start < 0:
        return None, False
    end = text.find("}", start)
    if end < 0:
        return None, False
    try:
        raw = json.loads(text[start:end + 1])
    except Exception:
        return None, False
    out = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh",
                "output_i", "output_tp", "target_offset"):
        try:
            out[key] = float(raw[key])
        except (KeyError, TypeError, ValueError):
            out[key] = None
    return out, True


def _run_peak(path, timeout=60):
    """Maximum sample-peak level in dBFS via astats (overall).

    Returns (peak_or_None, ok). astats reports a sample peak, not
    a true peak — the QA field is named audio_peak_db and the
    warning is worded as a near-clipping warning, not a
    true-peak claim.
    """
    cmd = ["ffmpeg", "-i", path, "-af", "astats=metadata=1:reset=0",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None, False
    peaks = []
    for m in re.finditer(r"Peak level dB:\s*(-?[0-9.]+)", proc.stderr or ""):
        try:
            peaks.append(float(m.group(1)))
        except ValueError:
            pass
    if not peaks:
        return None, False
    return max(peaks), True


def _run_freeze(path, timeout=60):
    """Frozen-frame segments via freezedetect (warnings only).

    Returns (segments, total, ok) in the same shape as _run_detect.
    Unlike blackdetect, freezedetect reports freeze_start and
    freeze_end as separate metadata events — and a video that is
    still frozen at EOF never emits a freeze_end at all. So the
    parser tracks an open freeze and closes it at the file
    duration, exactly like the silence parser in _run_detect.
    """
    cmd = ["ffmpeg", "-v", "info", "-i", path,
           "-vf", "freezedetect=noise=-60dB:d=1",
           "-an", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return [], 0.0, False
    text = proc.stderr or ""
    # File duration for the EOF-close case (from the caller's
    # perspective the freeze runs to the end of the file).
    dur_match = None
    try:
        import json as _json
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path], capture_output=True, text=True, timeout=30)
        dur_match = float(_json.loads(probe.stdout).get("format", {}).get("duration") or 0.0)
    except Exception:
        dur_match = 0.0
    segs = []
    open_start = None
    open_end = None
    for m in re.finditer(r"freeze_(start|end|duration):\s*([0-9.]+)", text):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            open_start, open_end = val, None
        elif kind == "end":
            open_end = val
        elif kind == "duration" and open_start is not None:
            en = open_end if open_end is not None else open_start + val
            segs.append({"start": open_start, "end": en, "duration": val})
            open_start, open_end = None, None
    if open_start is not None and dur_match:
        segs.append({"start": open_start, "end": dur_match,
                     "duration": max(0.0, dur_match - open_start)})
    return segs, sum(s["duration"] for s in segs), True


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
    # Pro pack: a delivery preset supplies size/fps/bitrate/
    # loudness, but an explicit size always wins over it. The
    # precedence is: explicit width/height > explicit format >
    # delivery preset > default (vertical). fps follows the same
    # rule (explicit fps > preset fps > 24). The applied preset
    # and the size source are returned in the result, so the
    # override is visible, never silent.
    preset_name = brief.get("delivery_preset")
    preset = None
    if preset_name is not None:
        if not isinstance(preset_name, str) or preset_name not in DELIVERY_PRESETS:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"delivery_preset must be one of {sorted(DELIVERY_PRESETS)}",
                           "fix": "set delivery_preset to one of: " + ", ".join(sorted(DELIVERY_PRESETS)) + ", or remove it"}
        preset = DELIVERY_PRESETS[preset_name]
    fmt = brief.get("format")
    if fmt is not None and fmt not in FORMATS:
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"format must be one of {sorted(FORMATS)}"}
    explicit_size = None
    if brief.get("width") is not None or brief.get("height") is not None:
        ew, eh = brief.get("width"), brief.get("height")
        if type(ew) is not int or type(eh) is not int or not 16 <= ew <= 3840 or not 16 <= eh <= 3840:
            return False, {"code": "INVALID_DIMENSION",
                           "message": "brief width/height must both be integers 16..3840",
                           "fix": "set both width and height, e.g. width 1080 and height 1080, or remove them"}
        explicit_size = (ew, eh)
    if explicit_size is not None:
        width, height = explicit_size
        size_source = "explicit width/height"
    elif fmt is not None:
        width, height = FORMATS[fmt]
        size_source = "format"
    elif preset is not None:
        width, height = preset["width"], preset["height"]
        size_source = "delivery_preset"
    else:
        width, height = FORMATS["vertical"]
        size_source = "default"
    if brief.get("fps") is not None:
        fps_value = brief["fps"]
    elif preset is not None:
        fps_value = preset["fps"]
    else:
        fps_value = 24
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
            # A non-numeric duration used to raise ValueError out
            # of produce_video (a raw traceback on the CLI path);
            # it is a structured brief error now (1.34.0).
            try:
                clip["duration"] = float(item.get("duration", 3))
            except (TypeError, ValueError):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"clip {i} duration must be a number",
                               "fix": "set duration to seconds, e.g. 3, or remove it for the 3s default"}
        else:
            if item.get("trim_in") is not None:
                clip["trim_in"] = item["trim_in"]
            if item.get("trim_out") is not None:
                clip["trim_out"] = item["trim_out"]
        if i > 0:
            clip["transition"] = item.get("transition", "crossfade")
        for key in ("ken_burns", "fit", "speed", "brightness", "contrast",
                    "saturation", "grayscale", "transition_duration",
                    "grade", "lut", "punch_in", "sfx", "sfx_volume"):
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
            # Non-numeric trim/speed used to raise out of
            # produce_video (raw traceback on the CLI path);
            # structured brief error now (1.34.0). Validation
            # below would reject these too, but the arithmetic
            # runs first, so it must not crash.
            try:
                dur = (float(clip.get("trim_out", info_p["duration"]))
                       - float(clip.get("trim_in", 0)))
                if "speed" in clip:
                    dur = dur / float(clip["speed"])
            except (TypeError, ValueError, ZeroDivisionError):
                return False, {"code": "INVALID_SCHEMA",
                               "message": "video clip trim_in/trim_out/speed must be numbers (speed != 0)",
                               "fix": "set trim_in/trim_out to seconds inside the source and speed to 0.25..4.0"}
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
    try:
        _fps_int = int(fps_value)
    except (TypeError, ValueError):
        _fps_int = fps_value  # validation below reports it properly
    output_block = {"width": width, "height": height,
                    "fps": _fps_int}
    if preset is not None or brief.get("video_bitrate") is not None:
        output_block["video_bitrate"] = (brief.get("video_bitrate")
                                         if brief.get("video_bitrate") is not None
                                         else preset["video_bitrate"])
    if preset is not None or brief.get("loudness_target") is not None:
        output_block["loudness_target"] = brief.get("loudness_target") if brief.get("loudness_target") is not None else preset["loudness_target"]
    project = {
        "id": brief.get("id", "produced"),
        "title": title or "Produced video",
        "version": 1,
        "output": output_block,
        "clips": clips,
        "texts": texts,
        "captions": brief.get("captions") or [],
        "audio": brief_audio,
    }
    # Pro pack brief passthrough: chapters (explicit list, or
    # section titles when no explicit list), ducking, and
    # timecode all land on the project, where validation and
    # rendering already handle them.
    if brief.get("chapters") is not None:
        project["chapters"] = brief["chapters"]
    elif sections is not None and section_info:
        project["chapters"] = [{"title": s["title"], "start": s["start"]}
                               for s in section_info]
    if brief.get("ducking") is not None:
        project["ducking"] = brief["ducking"]
    if brief.get("timecode") is not None:
        project["timecode"] = brief["timecode"]
    if brief.get("overlays"):
        project["overlays"] = brief["overlays"]
    if brief.get("default_image_motion") is not None:
        project["default_image_motion"] = brief["default_image_motion"]
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
    # Pro pack QA: measured loudness, sample-peak clipping warning
    # and freeze-frame detection, alongside black/silence. All are
    # warnings only: QA never changes the render it checked.
    loudness, loud_ok = _run_loudness(output_path) if has_audio else (None, False)
    if has_audio and not loud_ok:
        warnings.append("loudness measurement unavailable")
    peak_db, peak_ok = _run_peak(output_path) if has_audio else (None, False)
    if has_audio and not peak_ok:
        warnings.append("audio peak measurement unavailable")
    elif peak_db is not None and peak_db > -1.0:
        warnings.append(f"audio peak {peak_db:.1f} dB is close to clipping (above -1 dB)")
    freeze_segs, freeze_total, freeze_ok = _run_freeze(output_path)
    if not freeze_ok:
        warnings.append("freeze detection unavailable")
    elif freeze_total > 0:
        warnings.append(f"frozen frames detected: {freeze_total:.1f}s in {len(freeze_segs)} segment(s)")
    qa = {
        "duration_expected": total, "duration_actual": actual_duration,
        "width_expected": width, "height_expected": height,
        "width_actual": actual_w, "height_actual": actual_h,
        "pix_fmt": actual_pix, "has_audio": has_audio,
        "black_segments": black_segs, "black_duration": black_total,
        "silence_segments": sil_segs, "silence_duration": sil_total,
        "loudness": loudness,
        "loudness_target": (output_block.get("loudness_target")
                            if preset is not None or brief.get("loudness_target") is not None else None),
        "audio_peak_db": peak_db,
        "freeze_segments": freeze_segs, "freeze_duration": freeze_total,
        "warnings": warnings,
    }
    # Layer/text collision warnings from validation (v1.33.0):
    # warnings only, merged into QA so a produce run surfaces them
    # exactly like the validate_project tool does.
    for _w in (info.get("warnings") or []):
        warnings.append(f"{_w.get('code')}: {_w.get('message')}")
    # v1.41.0: inaudible-bed warning. A bed at volume 0.3 under
    # 12 dB of ducking sits ~22 dB under narration (measured on
    # the AI-race mix) — present in the file, silent to the ear.
    # The check measures both inputs and only fires for
    # deterministic ducking; the sidechain path varies too much
    # for an honest number, so it is skipped, not guessed.
    _bed = check_music_bed(info)
    if _bed is not None and _bed["warn"]:
        warnings.append(
            f"music_bed_inaudible: bed sits {_bed['gap_db']:.1f} dB under "
            f"narration (voice {_bed['voice_lufs']:.1f} LUFS, bed "
            f"{_bed['bed_lufs']:.1f} LUFS effective at volume "
            f"{_bed['music_volume']} with {_bed['duck_db']} dB ducking); "
            f"raise the bed volume or lower duck_amount_db")
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
        "chapters": info.get("chapters", []),
        "delivery_preset": preset_name,
        "delivery_preset_applied": ({"name": preset_name,
                                     "size_source": size_source,
                                     "width": width, "height": height,
                                     "fps": project["output"]["fps"],
                                     "video_bitrate": output_block.get("video_bitrate"),
                                     "loudness_target": output_block.get("loudness_target")}
                                    if preset is not None else None),
        "project": project,
    }


def _explainer_find_punch_in(obj, where="brief"):
    """First punch_in occurrence in a brief, or None.

    Scans dicts/lists recursively so a punch_in hidden in a
    scene, a diagram or a keyword is caught exactly like a
    top-level one: the one-button path never accepts it.
    """
    if isinstance(obj, dict):
        if "punch_in" in obj:
            return where
        for key, val in obj.items():
            found = _explainer_find_punch_in(val, f"{where}.{key}")
            if found:
                return found
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            found = _explainer_find_punch_in(val, f"{where}[{i}]")
            if found:
                return found
    return None


# v1.40.0: one-button look profiles. A look is orthogonal to
# `style` (which stays blackboard-only for scrims) and applies
# to ANY background — photo, solid, gradient or board. Each
# profile combines clip treatment + clip grade + scene-title
# text preset + motion default; a brief field always wins over
# its look counterpart (brief motion beats look motion; scene
# titles use the look's preset only when the look sets one, so
# a look without a title_style keeps the 52/yellow defaults).
EXPLAINER_LOOKS = {
    # Exactly today's defaults, spelled out.
    "clean": {},
    # Film feel: grain + vignette, cinematic grade, preset titles.
    "cinematic": {"treatment": {"grain": 8, "vignette": 0.4},
                  "grade": "cinematic", "title_style": "title"},
    # Aged film: heavier grain + vignette, vintage grade.
    "vintage": {"treatment": {"grain": 15, "vignette": 0.6},
                "grade": "vintage", "title_style": "title"},
    # Titles pop (preset size + shadow); footage untouched.
    "bold": {"title_style": "title"},
    # No motion at all; everything else default.
    "still": {"motion": "none"},
    # v1.41.0 Fix 6: modern explainer layout — subtle radial
    # vignette, card titles (rounded translucent backdrop, no
    # black box behind every line), backdrop cards under
    # diagrams by default, and 10% safe-margin zoning (titles
    # stacked in the top zone, diagrams clamped into the middle
    # zone above the bottom caption band, never overlapping).
    # The dual-tone canvas background itself is a separate
    # choice: brief background "canvas" generates one (any
    # look), so modern also works over photos and boards.
    "modern": {"treatment": {"vignette": 0.4},
               "title_style": "card",
               "diagram_card": True, "safe_margins": True},
}


def _explainer_pacing_beats(start, dur, title, si, nscenes,
                            width, height):
    """Visual-beat motion elements for one LONG explainer scene
    (v1.45.0, R8).

    The prompt's Visual Beat Interval is 5.0..7.0s: in a long
    scene the automatic schedule alternates visual events so
    no stretch of the scene is static for more than ~7s.
    Beat windows (prompt): 0-5s opening lower_third (scene
    title, spring entrance); 5-13s live motion chart in a
    glass card (the scene diagram — constrained to this
    window by the caller, not here); 13-20s counter (scene
    progress %) or keyword highlight. The cycle repeats
    every 20s for scenes longer than 20s (lower_third,
    chart, counter), each beat clamped to the scene end.
    Only the lower_third and the counter are built here;
    the chart is the scene's diagram (see produce_explainer).
    Returns a list of motion elements (possibly empty).
    A scene is "long" (pacing applies) when dur >= 13s —
    shorter scenes keep the pre-pacing behaviour (static
    scene title, diagram spanning the whole scene), which
    the existing produce_explainer tests rely on.
    """
    beats = []
    if dur < 13.0:
        return beats
    n = int(nscenes) if nscenes else 1
    # Progress % at the end of this scene (a real derived
    # number: scene index / total scenes).
    pct = int(round(100.0 * (si + 1) / max(1, n)))
    cycle = 0
    while True:
        base = start + cycle * 20.0
        if base >= start + dur:
            break
        # Beat A (0-5s of the cycle): lower_third scene title.
        a0, a1 = base, min(base + 5.0, start + dur)
        if a1 > a0 and isinstance(title, str) and title.strip():
            beats.append({
                "id": f"pace-lt-{si + 1}-{cycle + 1}",
                "kind": "lower_third", "text": title,
                "x": 60, "y": height - 200, "h": 90,
                "start": round(a0, 3), "end": round(a1, 3),
                "enter": 0.6, "exit": 0.6})
        # Beat C (13-20s of the cycle): progress counter.
        # The suffix is ٪ (U+066A, Arabic percent sign), NOT
        # % — % is a filter-breaking character in drawtext
        # and validation rejects it (see the fix hint for
        # motion.suffix).
        c0, c1 = base + 13.0, min(base + 20.0, start + dur)
        if c1 > c0:
            beats.append({
                "id": f"pace-ct-{si + 1}-{cycle + 1}",
                "kind": "counter", "from": 0, "to": pct,
                "suffix": "٪", "x": 360, "y": 600,
                "size": 72, "color": "yellow",
                "start": round(c0, 3), "end": round(c1, 3)})
        cycle += 1
    return beats


def _render_explainer_parallel(project, scenes, output_path, scale_factor=1.0,
                               video_codec="auto", video_preset=None, timeout=600):
    """Render multiple explainer scenes concurrently across CPU/GPU cores (Phase 3),
    concatenating them with ffmpeg concat demuxer (-c copy) for maximum rendering speed.
    """
    import concurrent.futures
    import shutil

    tmp_dir = tempfile.mkdtemp(prefix="agentcut-parallel-")
    try:
        chunk_files = []
        futures = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(scenes))) as executor:
            for si, scene in enumerate(scenes):
                s_start = scene["start"]
                s_end = scene["end"]
                s_dur = scene["duration"]

                sc_captions = []
                for c in project.get("captions", []):
                    if s_start <= c["start"] < s_end:
                        rebased_c = dict(c)
                        rebased_c["start"] = max(0.0, round(c["start"] - s_start, 3))
                        rebased_c["end"] = max(rebased_c["start"] + 0.1, min(s_dur, round(c["end"] - s_start, 3)))
                        if "word_timings" in c:
                            rebased_c["word_timings"] = [
                                {
                                    "word": wt["word"],
                                    "start": max(0.0, round(wt["start"] - s_start, 3)),
                                    "end": min(s_dur, round(wt["end"] - s_start, 3)),
                                }
                                for wt in c["word_timings"]
                            ]
                        sc_captions.append(rebased_c)

                sc_texts = []
                for t in project.get("texts", []):
                    if t.get("start", 0.0) == 0.0 and t.get("end", 0.0) >= s_end:
                        rebased_t = dict(t)
                        rebased_t["start"] = 0.0
                        rebased_t["end"] = s_dur
                        sc_texts.append(rebased_t)
                    elif s_start <= t.get("start", 0.0) < s_end:
                        rebased_t = dict(t)
                        rebased_t["start"] = max(0.0, round(t["start"] - s_start, 3))
                        rebased_t["end"] = max(rebased_t["start"] + 0.1, min(s_dur, round(t["end"] - s_start, 3)))
                        sc_texts.append(rebased_t)

                sc_diagrams = []
                for d in project.get("diagrams", []):
                    if s_start <= d.get("start", 0.0) < s_end:
                        rebased_d = dict(d)
                        rebased_d["start"] = max(0.0, round(d["start"] - s_start, 3))
                        rebased_d["end"] = max(rebased_d["start"] + 0.1, min(s_dur, round(d["end"] - s_start, 3)))
                        sc_diagrams.append(rebased_d)

                sc_motion = []
                for m in project.get("motion", []):
                    if s_start <= m.get("start", 0.0) < s_end:
                        rebased_m = dict(m)
                        rebased_m["start"] = max(0.0, round(m["start"] - s_start, 3))
                        rebased_m["end"] = max(rebased_m["start"] + 0.1, min(s_dur, round(m["end"] - s_start, 3)))
                        sc_motion.append(rebased_m)

                clip_i = dict(project["clips"][si])
                clip_i["transition"] = "none"
                clip_i.pop("transition_duration", None)

                chunk_proj = {
                    "id": f"{project.get('id', 'proj')}_chunk_{si}",
                    "version": 1,
                    "output": dict(project["output"]),
                    "clips": [clip_i],
                    "audio": [{"src": scene["narration"], "kind": "voice", "volume": 1.0, "start": 0.0}],
                    "captions": sc_captions,
                    "texts": sc_texts,
                    "diagrams": sc_diagrams,
                    "motion": sc_motion,
                    "scrims": project.get("scrims", {}),
                    "default_image_motion": project.get("default_image_motion", False),
                }
                chunk_proj["output"]["master_loudnorm"] = True
                chunk_proj["output"]["loudnorm_i"] = -14.0

                chunk_out = os.path.join(tmp_dir, f"chunk_{si:03d}.mp4")
                chunk_files.append(chunk_out)

                future = executor.submit(
                    render_project,
                    chunk_proj,
                    chunk_out,
                    scale_factor=scale_factor,
                    video_codec=video_codec,
                    video_preset=video_preset,
                    timeout=max(120, int(s_dur * 20)),
                )
                futures.append((si, future))

            for si, f in futures:
                ok, res = f.result()
                if not ok:
                    return False, res

        list_file = os.path.join(tmp_dir, "concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as lf:
            for cf in chunk_files:
                norm_p = cf.replace("\\", "/")
                lf.write(f"file '{norm_p}'\n")

        joined_path = os.path.join(tmp_dir, "joined.mp4")
        concat_cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            joined_path,
        ]
        proc = subprocess.run(concat_cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.isfile(joined_path):
            return False, {"code": "CONCAT_FAILED", "message": f"FFmpeg concat demuxer failed: {proc.stderr}"}

        # Apply scene transitions if specified (Bug 5 fix)
        cuts_to_apply = []
        cur_time = 0.0
        for si, scene in enumerate(scenes):
            s_dur = scene["duration"]
            if si > 0:
                clip_trans = project["clips"][si].get("transition")
                if clip_trans and clip_trans != "none":
                    td = float(project["clips"][si].get("transition_duration", 0.5))
                    cuts_to_apply.append((cur_time, clip_trans, td, si))
            cur_time += s_dur

        if cuts_to_apply:
            trans_filters = []
            vcur_chain = "0:v"
            p_out = project.get("output", {})
            width = int(p_out.get("width", 720))
            height = int(p_out.get("height", 1280))
            fps = int(p_out.get("fps", 30))
            total = cur_time
            _afmt = "rgba"
            _fmt = "yuv420p"

            for _cut, _kind, _td, _ci in cuts_to_apply:
                _w0 = _cut - _td / 2.0
                _w1 = _cut + _td / 2.0
                _u = f"(t-{_w0:.3f})/{_td:.3f}"
                _win = f"between(t,{_w0:.3f},{_w1:.3f})"
                if _kind == "zoom_out":
                    _s = f"if({_win},1-0.25*sin(3.14159265*{_u}),1)"
                    trans_filters.append(
                        f"[{vcur_chain}]scale=w='trunc(iw*({_s})/2)*2':"
                        f"h='trunc(ih*({_s})/2)*2':eval=frame[zsc{_ci}]")
                    trans_filters.append(
                        f"color=c=black:s={width}x{height}:r={fps}:"
                        f"d={total:.3f}[zbg{_ci}]")
                    trans_filters.append(
                        f"[zbg{_ci}][zsc{_ci}]overlay=x='(main_w-overlay_w)/2':"
                        f"y='(main_h-overlay_h)/2':eof_action=pass,"
                        f"fps={fps},format={_fmt}[cz{_ci}]")
                    vcur_chain = f"cz{_ci}"
                elif _kind == "whip_pan":
                    _x = f"if({_win},{width}*(1-abs(2*{_u}-1)),0)"
                    trans_filters.append(
                        f"[{vcur_chain}]pad={2 * width}:{height}:0:0:black,"
                        f"crop={width}:{height}:x='{_x}':y=0,"
                        f"gblur=sigma=8:enable='{_win}'[cw{_ci}]")
                    vcur_chain = f"cw{_ci}"
                elif _kind == "glitch":
                    trans_filters.append(
                        f"[{vcur_chain}]rgbashift=rh=10:rv=0:gh=0:gv=0:bh=-10:bv=0:"
                        f"enable='between(t,{_w0:.3f},{_w0 + 0.4 * _td:.3f})',"
                        f"rgbashift=rh=-8:rv=2:gh=0:gv=0:bh=8:bv=-2:"
                        f"enable='between(t,{_w0 + 0.4 * _td:.3f},{_w0 + 0.7 * _td:.3f})',"
                        f"noise=alls=25:allf=t:enable='{_win}'[cg{_ci}]")
                    vcur_chain = f"cg{_ci}"
                elif _kind == "light_leak":
                    _lx = f"if({_win},{-0.35 * width:.3f}+{0.7 * width:.3f}*{_u},0)"
                    _a = (f"255*0.6*clip(1-sqrt(pow(X-{width / 2:.3f},2)"
                          f"+pow(Y-{height / 2:.3f},2))/({0.7 * width:.3f}),0,1)")
                    trans_filters.append(
                        f"color=c=0xff9500:s={width}x{height}:r={fps},"
                        f"format={_afmt},geq=r='255':g='150':b='0':a='{_a}'[leak{_ci}]")
                    trans_filters.append(
                        f"[{vcur_chain}][leak{_ci}]overlay=x='{_lx}':y=0:"
                        f"enable='{_win}':eof_action=pass[cl{_ci}]")
                    vcur_chain = f"cl{_ci}"
                else:
                    _s = f"if({_win},abs(2*{_u}-1),1)"
                    trans_filters.append(f"[{vcur_chain}]colorlevels=rimax='{_s}':gimax='{_s}':bimax='{_s}'[cf{_ci}]")
                    vcur_chain = f"cf{_ci}"

            trans_out = os.path.join(tmp_dir, "transitioned.mp4")
            fc_str = ";".join(trans_filters)
            t_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", joined_path,
                "-filter_complex", fc_str,
                "-map", f"[{vcur_chain}]",
                "-map", "0:a?",
                "-c:v", video_codec,
                "-preset", video_preset or "fast",
                "-c:a", "copy",
                trans_out,
            ]
            t_proc = subprocess.run(t_cmd, capture_output=True, text=True)
            if t_proc.returncode == 0 and os.path.isfile(trans_out):
                joined_path = trans_out

        music_tracks = [a for a in project.get("audio", []) if a.get("kind") == "music"]
        if music_tracks:
            music_src = music_tracks[0]["src"]
            final_tmp = os.path.join(tmp_dir, "final_mix.mp4")
            mix_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", joined_path,
                "-i", music_src,
                "-filter_complex",
                "[1:a]volume=0.18[bg];[0:a][bg]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14.0:TP=-1.0:LRA=11[a]",
                "-map", "0:v",
                "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                final_tmp,
            ]
            proc = subprocess.run(mix_cmd, capture_output=True, text=True)
            if proc.returncode == 0 and os.path.isfile(final_tmp):
                shutil.move(final_tmp, output_path)
            else:
                shutil.move(joined_path, output_path)
        else:
            shutil.move(joined_path, output_path)

        return True, {"output": output_path, "parallel_chunks": len(scenes)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def produce_explainer(brief, output_path, review_dir=None):
    """One-button board explainer (v1.39.0): one brief in, one
    finished video out. No hand-written project.json: every
    timeline fact is DERIVED here from the brief.

    Brief shape (verbatim — this is the button):
      {"id": "strawman", "style": "blackboard", "look": "cinematic",
       "output": {"width": 720, "height": 1280, "fps": 30},
       "background": "/path/to/board_image.png",
       "title": "مغالطة رجل القش", "chapters": true,
       "motion": "gentle", "music": null,
       "scenes": [{"id": "s1", "title": "ما هي؟",
         "narration": "/path/voice_s1.mp3",
         "captions": "/path/s1.srt",
         "diagram": {"template": "causes", "params": {"rows": 2},
                     "draw": 3.5, "size": 0.72, "x": 101, "y": 330},
         "keywords": [{"text": "رجل القش", "start": 5.0,
                       "end": 9.0, "sfx": "pop"}]}]}
    Rules, all enforced with structured errors + fix hints,
    never a raw traceback:
    - Scene clip durations come from ffprobe of the narration
      audio, never from hand-supplied durations; scenes play
      back-to-back in list order, total = sum of scenes.
    - Per-scene SRT cues are scene-relative (they start at 0
      in their own file) and are offset by the scene start
      into one merged captions track, clamped to the scene.
    - A scene diagram defaults to the whole scene window
      (start/end = scene span, draw = min(4s, window) via
      validation); an explicit diagram start/end is a
      SCENE-RELATIVE offset and must stay inside the scene.
      Placement mirrors overlays (size/x/y/position).
    - The brief title is fixed across the whole video (top,
      white); each scene title shows during its scene
      (below it, yellow); keyword texts show at their
      scene-relative windows with the existing text SFX
      mechanism when "sfx" is given.
    - chapters=true writes one chapter per scene title at
      the scene start; chapters=false/absent writes none.
    - style "blackboard" (or absent) turns scrims top+bottom
      on; motion "gentle" (or absent) turns
      default_image_motion on. punch_in ANYWHERE in the
      brief is rejected: gentle motion only, zero punch-ins.
    - look (v1.40.0, optional) picks a profile from
      EXPLAINER_LOOKS — clean, cinematic, vintage, bold,
      still, modern — combining clip treatment + grade +
      scene-title preset + motion default, on ANY background.
      Modern (v1.41.0, Fix 6) adds card titles, backdrop cards
      under diagrams, and 10% safe-margin zoning. A brief
      field always wins over its look counterpart: brief
      motion beats look motion, and scene titles use the
      look's preset only when the look sets one (otherwise
      the 52/yellow defaults stay); keywords keep their
      explicit size/colour. Unknown looks are rejected with
      the known list. Background "canvas" (any look) generates
      a dual-tone canvas beside the output instead of reading
      a file.
    - Optional music: when "music" names a file it is added
      as a music track with deterministic 12 dB ducking
      windows = the narration spans.
    - Pacing (v1.45.0, R8): "pacing" defaults to true. In a
      LONG scene (>= 13s) the visual beat is 5.0..7.0s: 0-5s
      the scene title appears as a lower_third (spring
      entrance) instead of the static yellow text; 5-13s the
      scene diagram is the live motion chart in a glass card
      (an explicit diagram start/end in the brief always
      wins over the 5-13s window); 13-20s a counter shows
      the scene's progress % (a real derived number: scene
      index / total scenes). The cycle repeats every 20s
      for scenes longer than 20s. Short scenes (< 13s)
      keep the pre-1.45.0 layout (static title, diagram
      spanning the whole scene). pacing=false restores the
      old layout for every scene.
    Returns (ok, info_or_error); info carries output,
    duration, the derived scenes list, chapters, review
    frames and the built project (callers strip it, as
    produce_video does, before printing).
    """
    from .diagrams import TEMPLATES
    from .srt import parse_srt_file
    if not isinstance(brief, dict):
        return False, {"code": "INVALID_SCHEMA",
                       "message": "explainer brief must be an object",
                       "fix": "pass a brief object with id, background and scenes"}
    punched = _explainer_find_punch_in(brief)
    if punched is not None:
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"punch_in is not allowed in a one-button explainer (found at {punched})",
                       "fix": "remove punch_in from the brief; gentle motion (default_image_motion) is the only motion in this path"}
    style = brief.get("style", "blackboard")
    if style != "blackboard":
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"explainer style must be 'blackboard', got {style!r}",
                       "fix": "set style to 'blackboard', or remove it for the blackboard default"}
    # v1.40.0: look profiles (EXPLAINER_LOOKS above). Unknown
    # looks are rejected with the known list; a brief field
    # always wins over its look counterpart (motion below).
    look_name = brief.get("look")
    if look_name is not None and (not isinstance(look_name, str)
                                  or look_name not in EXPLAINER_LOOKS):
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"explainer look must be one of {', '.join(sorted(EXPLAINER_LOOKS))}, got {look_name!r}",
                       "fix": "set look to one of: " + ", ".join(sorted(EXPLAINER_LOOKS)) + ", or remove it for the default look"}
    look = EXPLAINER_LOOKS.get(look_name, {}) if look_name else {}
    motion = brief.get("motion", look.get("motion", "gentle"))
    if motion not in ("gentle", "none"):
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"explainer motion must be 'gentle' or 'none', got {motion!r}",
                       "fix": "set motion to 'gentle' for default_image_motion, or 'none' for a static board"}
    out = brief.get("output") or {}
    if not isinstance(out, dict):
        return False, {"code": "INVALID_SCHEMA",
                       "message": "output must be an object {width, height, fps}",
                       "fix": "set output to e.g. {\"width\": 720, \"height\": 1280, \"fps\": 30}"}
    width, height = out.get("width", 720), out.get("height", 1280)
    fps = out.get("fps", 30)
    if (type(width) is not int or type(height) is not int
            or not 16 <= width <= 3840 or not 16 <= height <= 3840):
        return False, {"code": "INVALID_DIMENSION",
                       "message": "output width/height must both be integers 16..3840",
                       "fix": "set output width and height, e.g. 720 and 1280, or remove output for the 720x1280 default"}
    background = brief.get("background")
    if not isinstance(background, str) or not background:
        return False, {"code": "INVALID_SCHEMA",
                       "message": "explainer brief needs a background board image path",
                       "fix": "set background to a local board image, e.g. data/test_media/board.png"}
    if background == "canvas":
        # v1.41.0 Fix 6: the sentinel "canvas" generates a
        # dual-tone canvas beside the output (it must outlive
        # this call, since the project references it by path).
        from .diagrams import write_dual_tone_canvas
        _canvas_path = os.path.join(
            os.path.dirname(os.path.abspath(output_path)),
            f"{brief.get('id', 'explainer')}-modern-canvas.png")
        write_dual_tone_canvas(_canvas_path, width, height)
        background = _canvas_path
    elif not os.path.isfile(resolve_path(background)):
        return False, {"code": "FILE_NOT_FOUND",
                       "message": f"background image not found: {background}",
                       "fix": "fix the background path to an existing local image file"}
    title = brief.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        return False, {"code": "INVALID_SCHEMA",
                       "message": "title must be a non-empty string",
                       "fix": "set title to the video title, or remove it"}
    brief_transition = brief.get("transition", "none")
    if brief_transition is not None and brief_transition != "none":
        from .validate import CUSTOM_TRANSITIONS, XFADE_TRANSITIONS
        all_trans = set(XFADE_TRANSITIONS) | set(CUSTOM_TRANSITIONS)
        if brief_transition not in all_trans:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"unknown explainer transition '{brief_transition}'",
                           "fix": f"set transition to one of: {', '.join(sorted(all_trans))} or 'none'"}
    captions_style = brief.get("captions_style", "plain")
    if captions_style not in ("plain", "karaoke"):
        return False, {"code": "INVALID_SCHEMA",
                       "message": f"captions_style must be 'plain' or 'karaoke', got {captions_style!r}",
                       "fix": "set captions_style to 'karaoke' for word-by-word active highlighting, or 'plain'"}
    scenes_in = brief.get("scenes")
    if not isinstance(scenes_in, list) or not scenes_in:
        return False, {"code": "EMPTY_TIMELINE",
                       "message": "explainer brief needs a non-empty scenes list",
                       "fix": "give scenes as a list of {id, title, narration, captions} objects"}
    music = brief.get("music")
    ambient_file = None
    if music is not None:
        if not isinstance(music, str) or not music:
            return False, {"code": "INVALID_SCHEMA",
                           "message": "music must be a local audio file path, 'ambient', or null",
                           "fix": "set music to an audio file path, 'ambient', or null for no music"}
        if music in ("ambient", "procedural"):
            from .tts import generate_ambient_audio
            tts_dir = output_path + ".tts"
            os.makedirs(tts_dir, exist_ok=True)
            ambient_file = os.path.join(tts_dir, "ambient_bg.mp3")
            music = ambient_file
        elif not os.path.isfile(resolve_path(music)):
            return False, {"code": "FILE_NOT_FOUND",
                           "message": f"music file not found: {music}",
                           "fix": "fix the music path to an existing local audio file, or set music to null"}
    # v1.41.0: SFX density. "minimal" (default) is today's
    # behaviour — only explicitly-set SFX fire. "rich" adds
    # scene-transition whooshes, keyword pops and diagram
    # chimes, so a long explainer has audible beats throughout
    # instead of one pop per video.
    sfx_density = brief.get("sfx_density", "minimal")
    if sfx_density not in ("minimal", "rich", "auto"):
        return False, {"code": "INVALID_SCHEMA",
                       "message": "sfx_density must be 'minimal', 'rich', or 'auto'",
                       "fix": "set sfx_density to 'rich' or 'auto' for scene-transition whoosh + keyword pops + diagram chimes, or remove it for the minimal default"}
    rich = (sfx_density in ("rich", "auto"))
    # v1.45.0, R8: visual pacing (Visual Beat Interval
    # 5.0..7.0s). Default ON: long scenes (>= 13s) get
    # lower_third + chart + counter beats so no stretch is
    # static for more than ~7s. pacing=false restores the
    # pre-1.45.0 behaviour (static scene title, diagram
    # spanning the whole scene) for every scene. Short
    # scenes (< 13s) always keep the old behaviour, even
    # with pacing on — the existing produce_explainer tests
    # (4s/3s scenes) rely on it.
    pacing = brief.get("pacing", True)
    if not isinstance(pacing, bool):
        return False, {"code": "INVALID_SCHEMA",
                       "message": "explainer pacing must be true or false",
                       "fix": "set pacing to true for the 5-7s visual beat (default), or false for the pre-1.45.0 static layout"}
    # Derive the timeline: probe every narration first, so a
    # bad scene fails before any render starts.
    scenes, clips, texts, captions, diagrams, audio = [], [], [], [], [], []
    motion_els = []
    # v1.41.0 Fix 6 safe margins (modern look only): the title
    # stack is zoned — fixed title at or below the 10% line,
    # scene title below it, keywords below that — so diagrams
    # clamp into the middle zone above the caption band.
    _safe = bool(look.get("safe_margins"))
    _fixed_y = max(80, int(round(0.10 * height))) if _safe else 80
    _ts = TEXT_STYLES.get(look.get("title_style")) \
        if isinstance(look.get("title_style"), str) else None
    _scene_title_size = int(round(_ts["size_frac"] * height)) \
        if _ts else 52
    _scene_y = max(190, _fixed_y + int(round(60 * 1.4)) + 16) \
        if _safe else 190
    total = 0.0
    for si, scene in enumerate(scenes_in):
        if not isinstance(scene, dict):
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"scene {si} must be an object",
                           "fix": "make each scene an object with id, title, narration and captions"}
        sid = scene.get("id") or f"s{si + 1}"
        if not isinstance(sid, str) or not sid:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"scene {si} needs a non-empty string id",
                           "fix": f"give scene {si} an id, e.g. 's{si + 1}'"}
        scene_word_timings = None
        narration = scene.get("narration")
        if not isinstance(narration, str) or not narration:
            narration_text = (scene.get("narration_text")
                              or scene.get("speech_text")
                              or scene.get("text"))
            if isinstance(narration_text, str) and narration_text.strip():
                from .tts import synthesize_speech
                tts_dir = output_path + ".tts"
                os.makedirs(tts_dir, exist_ok=True)
                audio_path = os.path.join(tts_dir, f"{sid}.mp3")
                auto_srt_path = os.path.join(tts_dir, f"{sid}.srt")
                voice = scene.get("voice") or brief.get("voice")
                try:
                    tts_res = synthesize_speech(narration_text, audio_path, auto_srt_path, voice=voice)
                    narration = audio_path
                    scene_word_timings = tts_res.get("word_timings")
                    if not scene.get("captions"):
                        scene["captions"] = auto_srt_path
                except Exception as exc:
                    return False, {"code": "TTS_FAILED",
                                   "message": f"scene {sid} TTS synthesis failed: {exc}",
                                   "fix": "verify network connection and edge-tts installation, or provide a narration audio file"}
            else:
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} needs a narration audio path or narration_text",
                               "fix": f"set scene {sid} narration to a local audio file or provide narration_text"}
        if not os.path.isfile(resolve_path(narration)):
            return False, {"code": "FILE_NOT_FOUND",
                           "message": f"scene {sid} narration not found: {narration}",
                           "fix": f"fix scene {sid} narration to an existing local audio file"}
        probe = probe_media(narration)
        if probe is None or probe.get("duration") is None or float(probe["duration"]) <= 0:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"scene {sid} narration has no measurable duration: {narration}",
                           "fix": f"replace scene {sid} narration with an audio file ffprobe can read"}
        dur = float(probe["duration"])
        srt_path = scene.get("captions")
        if not isinstance(srt_path, str) or not srt_path:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"scene {sid} needs a captions SRT path",
                           "fix": f"set scene {sid} captions to a local .srt file"}
        if not os.path.isfile(resolve_path(srt_path)):
            return False, {"code": "FILE_NOT_FOUND",
                           "message": f"scene {sid} captions not found: {srt_path}",
                           "fix": f"fix scene {sid} captions to an existing local .srt file"}
        try:
            cues = parse_srt_file(resolve_path(srt_path))
        except Exception as exc:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"scene {sid} captions cannot be parsed: {exc}",
                           "fix": f"fix scene {sid} captions as a valid SRT file"}
        start, end = total, total + dur
        is_karaoke = (captions_style == "karaoke"
                      or scene.get("captions_style") == "karaoke"
                      or bool(scene.get("kinetic")))
        for cue in cues:
            if cue["start"] >= dur:
                continue
            c_start = round(start + cue["start"], 3)
            c_end = round(start + min(cue["end"], dur), 3)
            cap_entry = {"text": cue["text"], "start": c_start, "end": c_end}
            if is_karaoke and scene_word_timings:
                cue_words = []
                for wt in scene_word_timings:
                    if cue["start"] - 0.05 <= wt["start"] and wt["end"] <= cue["end"] + 0.05:
                        cue_words.append({
                            "word": wt["word"],
                            "start": round(start + wt["start"], 3),
                            "end": round(start + min(wt["end"], dur), 3),
                        })
                if cue_words and [w["word"] for w in cue_words] == str(cue["text"]).split():
                    cap_entry["word_timings"] = cue_words
                    cap_entry["highlight_color"] = scene.get("highlight_color") or brief.get("highlight_color", "yellow")
                    cap_entry["highlight_mode"] = scene.get("highlight_mode", "word")
            captions.append(cap_entry)
        scene_trans = "none"
        if si > 0:
            scene_trans = scene.get("transition") or brief_transition or "none"
        clip_entry = {"id": sid, "kind": "image", "src": background,
                      "duration": dur, "transition": scene_trans}
        if scene_trans != "none":
            clip_entry["transition_duration"] = float(
                scene.get("transition_duration", brief.get("transition_duration", 0.5))
            )
        clips.append(clip_entry)
        # v1.40.0: look treatment/grade ride on every scene clip,
        # whatever the background is (photo, solid, board).
        if look.get("treatment"):
            clips[-1]["treatment"] = dict(look["treatment"])
        if look.get("grade"):
            clips[-1]["grade"] = look["grade"]
        # v1.41.0, rich SFX: every scene after the first opens
        # with a whoosh, so scene changes are heard, not just seen.
        if rich and si > 0:
            clips[-1]["sfx"] = "whoosh"
        audio.append({"src": narration, "kind": "voice", "volume": 1.0,
                      "start": start})
        stitle = scene.get("title")
        # v1.45.0, R8: a long paced scene shows its title as
        # a lower_third (beat 0, spring) instead of the static
        # yellow text — the static text would duplicate it.
        # Short scenes and pacing=false keep the static text.
        _long_paced = bool(pacing) and dur >= 13.0
        if _long_paced:
            motion_els.extend(_explainer_pacing_beats(
                start, dur, stitle, si, len(scenes_in), width, height))
        if isinstance(stitle, str) and stitle.strip() and not _long_paced:
            st_text = stitle.strip()
            st_size = _scene_title_size if look.get("title_style") else 52
            st_font = find_font_for_text(st_text)
            if st_font:
                _smw = _measure_text_width(st_text, st_font, st_size)
                if _smw > width * 0.86:
                    st_size = max(28, int(st_size * (width * 0.86) / _smw))
                    _smw2 = _measure_text_width(st_text, st_font, st_size)
                    if _smw2 > width * 0.88 and "\n" not in st_text:
                        _slines = wrap_subtitle_text(st_text, width, st_size, max_width_frac=0.86)
                        if len(_slines) > 1:
                            st_text = "\n".join(_slines)
            if look.get("title_style"):
                st_entry = {"text": st_text, "start": start, "end": end,
                            "x": "center", "y": _scene_y,
                            "style": look["title_style"]}
                if st_size < _scene_title_size:
                    st_entry["size"] = st_size
                texts.append(st_entry)
            else:
                texts.append({"text": st_text, "start": start, "end": end,
                              "size": st_size, "x": "center", "y": _scene_y,
                              "color": "yellow", "box": False})
        # v1.41.0 Fix 6: this scene's content-stack bottom —
        # scene title if present, else the fixed title — and
        # the keyword line below it. Diagrams clamp below this.
        _title_bottom = (_scene_y + int(round(_scene_title_size * 1.4))
                         if isinstance(stitle, str) and stitle.strip()
                         else _fixed_y + int(round(60 * 1.4)))
        _kw_y = max(260, _title_bottom + 16) if _safe else 260
        _content_bottom = (_kw_y + int(round(56 * 1.4))
                           if scene.get("keywords") else _title_bottom)
        dg = scene.get("diagram")
        if dg is not None:
            if not isinstance(dg, dict):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} diagram must be an object",
                               "fix": f"give scene {sid} diagram as {{template, params}} or {{strokes, ...}}"}
            if dg.get("template") is not None and dg["template"] not in TEMPLATES:
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} diagram: unknown template '{dg['template']}'",
                               "fix": "set template to one of: " + ", ".join(sorted(TEMPLATES))}
            rel_start = dg.get("start", 0.0)
            rel_end = dg.get("end", dur)
            # v1.45.0, R8: a long paced scene's diagram is the
            # beat-1 chart (5-13s in a glass card), not the
            # whole scene — unless the brief gave an explicit
            # window, which always wins. Short scenes keep the
            # whole-scene default (the e2e tests rely on it).
            if _long_paced and "start" not in dg and "end" not in dg:
                rel_start, rel_end = 5.0, min(13.0, dur)
            try:
                rel_start, rel_end = float(rel_start), float(rel_end)
            except (TypeError, ValueError):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} diagram start/end must be numbers (scene-relative seconds)",
                               "fix": f"set scene {sid} diagram start/end inside 0..{dur:.2f}, or remove them for the whole scene"}
            if rel_start < 0 or rel_end <= rel_start or rel_end > dur + 1e-9:
                return False, {"code": "CLIP_OUT_OF_RANGE",
                               "message": f"scene {sid} diagram window {rel_start}..{rel_end}s is outside its scene (0..{dur:.2f}s)",
                               "fix": f"keep scene {sid} diagram start/end inside 0..{dur:.2f} with end after start, or remove them for the whole scene"}
            ndg = {"id": dg.get("id") or f"{sid}-diagram",
                   "start": round(start + rel_start, 3),
                   "end": round(start + rel_end, 3)}
            for key in ("template", "params", "strokes", "canvas",
                        "draw", "size", "x", "y", "position",
                        "sfx", "sfx_volume",
                        "card", "card_color", "card_opacity"):
                if key in dg:
                    ndg[key] = dg[key]
            if rich and dg.get("sfx") is None:
                ndg["sfx"] = "ding"
            if look.get("diagram_card") and "card" not in dg:
                ndg["card"] = True
            if _safe:
                # Clamp explicitly placed diagrams into the
                # 10% safe zone: horizontally inside [0.1W,
                # 0.9W], vertically below the content stack
                # and above the bottom caption band (which
                # starts at H-120). Preset-positioned diagrams
                # keep their preset: their pixel rect is a
                # filter expression, not a number, so it
                # cannot be clamped here (documented limit).
                # A malformed x/y is left for validation to
                # report with its structured error.
                try:
                    _dw = _even(float(ndg.get("size", 0.5)) * width)
                    _cw, _ch = 900, 560
                    if ndg.get("template") is not None:
                        try:
                            _cw, _ch, _ = TEMPLATES[ndg["template"]](
                                ndg.get("params") or {})
                        except Exception:
                            pass
                    elif isinstance(ndg.get("canvas"), dict):
                        _cw = int(ndg["canvas"].get("width", 900))
                        _ch = int(ndg["canvas"].get("height", 560))
                    _dh = _even(_dw * _ch / _cw) if _cw else _dw
                    _top = max(int(round(0.10 * height)),
                               _content_bottom + 16)
                    _bot = (height - 120) - 16
                    if "x" in ndg:
                        _lo = int(round(0.10 * width))
                        _hi = int(round(0.90 * width)) - _dw
                        ndg["x"] = _lo if _hi < _lo else min(
                            max(int(ndg["x"]), _lo), _hi)
                    if "y" in ndg:
                        ndg["y"] = _top if _bot < _top else min(
                            max(int(ndg["y"]), _top), _bot)
                except (TypeError, ValueError):
                    pass
            diagrams.append(ndg)
        for ki, kw in enumerate(scene.get("keywords") or []):
            if not isinstance(kw, dict) or not kw.get("text"):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} keyword {ki} needs a text string",
                               "fix": f"give scene {sid} keywords as {{text, start, end}} with scene-relative seconds"}
            try:
                ks, ke = float(kw["start"]), float(kw["end"])
            except (KeyError, TypeError, ValueError):
                return False, {"code": "INVALID_SCHEMA",
                               "message": f"scene {sid} keyword {ki} needs numeric start/end (scene-relative seconds)",
                               "fix": f"set scene {sid} keyword start/end inside 0..{dur:.2f}"}
            if ks < 0 or ke <= ks or ke > dur + 1e-9:
                return False, {"code": "CLIP_OUT_OF_RANGE",
                               "message": f"scene {sid} keyword window {ks}..{ke}s is outside its scene (0..{dur:.2f}s)",
                               "fix": f"keep scene {sid} keyword start/end inside 0..{dur:.2f} with end after start"}
            kw_text = str(kw["text"])
            kw_size = int(kw.get("size", 56))
            kw_font = find_font_for_text(kw_text)
            if kw_font:
                _kmw = _measure_text_width(kw_text, kw_font, kw_size)
                if _kmw > width * 0.86:
                    kw_size = max(28, int(kw_size * (width * 0.86) / _kmw))
                    _kmw2 = _measure_text_width(kw_text, kw_font, kw_size)
                    if _kmw2 > width * 0.88 and "\n" not in kw_text:
                        _klines = wrap_subtitle_text(kw_text, width, kw_size, max_width_frac=0.86)
                        if len(_klines) > 1:
                            kw_text = "\n".join(_klines)
            ktext = {"text": kw_text, "start": round(start + ks, 3),
                     "end": round(start + ke, 3), "size": kw_size,
                     "x": "center", "y": _kw_y, "color": "yellow",
                     "box": False}
            # Optional placement overrides (validated with the
            # rest of the project): a keyword may sit above or
            # below its diagram instead of at the default spot.
            for key in ("x", "y", "size", "color", "position"):
                if key in kw:
                    ktext[key] = kw[key]
            if kw.get("sfx") is not None:
                ktext["sfx"] = kw["sfx"]
            if kw.get("sfx_volume") is not None:
                ktext["sfx_volume"] = kw["sfx_volume"]
            if rich and kw.get("sfx") is None:
                ktext["sfx"] = "pop"
            texts.append(ktext)
        scenes.append({"id": sid, "title": stitle, "start": round(start, 3),
                       "end": round(end, 3), "duration": round(dur, 3),
                       "narration": narration, "captions": srt_path})
        total = end
    if title:
        t_text = str(title).strip()
        t_size = 60
        t_font = find_font_for_text(t_text)
        if t_font:
            _tmw = _measure_text_width(t_text, t_font, t_size)
            if _tmw > width * 0.86:
                t_size = max(28, int(t_size * (width * 0.86) / _tmw))
                _tmw2 = _measure_text_width(t_text, t_font, t_size)
                if _tmw2 > width * 0.88 and "\n" not in t_text:
                    _tlines = wrap_subtitle_text(t_text, width, t_size, max_width_frac=0.86)
                    if len(_tlines) > 1:
                        t_text = "\n".join(_tlines)
        texts.insert(0, {"text": t_text, "start": 0.0, "end": round(total, 3),
                         "size": t_size, "x": "center", "y": _fixed_y,
                         "color": "white", "box": False})
    project = {
        "id": brief.get("id", "explainer"),
        "title": title or "Explainer",
        "version": 1,
        "output": {"width": width, "height": height, "fps": fps,
                   "master_loudnorm": True, "loudnorm_i": -14.0},
        "clips": clips,
        "texts": texts,
        "captions": captions,
        "audio": audio,
        "diagrams": diagrams,
        "motion": motion_els,
        "scrims": {"top": True, "bottom": True},
        "default_image_motion": motion == "gentle",
    }
    if brief.get("chapters"):
        project["chapters"] = [{"title": s["title"] or s["id"],
                                "start": s["start"]} for s in scenes]
    if ambient_file is not None:
        from .tts import generate_ambient_audio
        generate_ambient_audio(ambient_file, duration=max(10.0, total + 5.0))
    if music is not None:
        project["audio"].append({"src": music, "kind": "music",
                                 "volume": 1.0, "start": 0.0})
        if brief.get("duck_amount_db") is not None:
            # Gentler duck, chosen by the brief: the explicit
            # 12 dB ducking below would override it (explicit
            # wins), so it is only set when no gentler depth
            # was asked for. Validation of the value happens
            # with the project (number 0..30, voice+music).
            project["duck_amount_db"] = brief["duck_amount_db"]
        else:
            project["ducking"] = {"voice_track": 0, "amount_db": 12.0,
                                  "fade": 0.25,
                                  "windows": [{"start": s["start"], "end": s["end"]}
                                              for s in scenes]}
    errors, info = validate_project(project)
    if errors:
        return False, {"code": "INVALID_PROJECT", "errors": errors}
    # v1.41.0: bed audibility, same check as produce QA (the
    # explainer result has no qa block, so warnings ride here).
    explainer_warnings = []
    _bed = check_music_bed(info)
    if _bed is not None and _bed["warn"]:
        explainer_warnings.append(
            f"music_bed_inaudible: bed sits {_bed['gap_db']:.1f} dB under "
            f"narration; raise the music volume or set "
            f"duck_amount_db lower")
    # A board explainer is long (tens of seconds at full
    # frame size, plus diagram videos): the render timeout
    # scales with the derived duration instead of the
    # 600s default, which a 78s proof timed out on under
    # load (the failed run left no partial file — renders
    # are atomic — so the retry is still one finished
    # video from one call).
    # Phase 3: Hardware acceleration, Parallel chunking, and Ultra-Fast Draft Mode
    video_codec = brief.get("video_codec", "auto")
    is_draft = bool(brief.get("draft") or brief.get("mode") == "draft")
    scale_factor = 0.5 if is_draft else float(brief.get("scale_factor", 1.0))
    video_preset = "ultrafast" if is_draft else brief.get("video_preset")
    is_parallel = bool(brief.get("parallel") or brief.get("render_mode") == "parallel")

    timeout = max(600, int(total * 20))
    if is_parallel and len(scenes) >= 2:
        ok, rinfo = _render_explainer_parallel(
            project, scenes, output_path, scale_factor=scale_factor,
            video_codec=video_codec, video_preset=video_preset, timeout=timeout)
    else:
        ok, rinfo = render_project(
            project, output_path, scale_factor=scale_factor,
            video_codec=video_codec, video_preset=video_preset, timeout=timeout)
    if not ok:
        return False, rinfo
    if review_dir is None:
        review_dir = output_path + ".review"
    os.makedirs(review_dir, exist_ok=True)
    frames = []
    for k, t in enumerate(sorted({0.0, total * 0.5, total * 0.99})):
        frame = os.path.join(review_dir, f"frame_{k}.png")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", output_path,
             "-frames:v", "1", frame],
            capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and os.path.isfile(frame):
            frames.append({"time": t, "path": frame})
    return True, {
        "output": output_path,
        "duration": info["total_duration"],
        "width": int(width * scale_factor), "height": int(height * scale_factor), "fps": fps,
        "look": look_name,
        "draft": is_draft,
        "parallel": is_parallel and len(scenes) >= 2,
        "scenes": scenes,
        "chapters": info.get("chapters", []),
        "warnings": explainer_warnings,
        "review_frames": frames,
        "review_dir": review_dir,
        "project": project,
    }


def produce_variants(brief, output_dir):
    """Render one brief in several shapes in one call.

    Without delivery fields, the classic behaviour: vertical,
    square and landscape renders of the same brief, each a full
    produce_video run (validate, render, review frames, QA).
    Outputs are <output_dir>/<brief-id>-<format>.mp4.

    Pro pack: a brief may instead name delivery presets —
    delivery_presets: ["youtube_1080", "shorts_720",
    "instagram_square"] (or a single delivery_preset, which
    renders just that preset). Each preset is a full
    produce_video run with that preset, and the variant key is
    the preset name. An explicit format/width/height in the
    brief still wins over a preset's size inside each variant
    (see produce_video), and the variant result records it.
    Returns (ok, info) where info["variants"] maps format or
    preset name -> produce info (project stripped). On a variant
    failure the call stops and reports that variant; variants
    already produced stay on disk (each is atomic).
    """
    import copy
    if not isinstance(brief, dict):
        return False, {"code": "INVALID_SCHEMA", "message": "produce brief must be an object"}
    os.makedirs(output_dir, exist_ok=True)
    base_id = brief.get("id", "produced")
    if brief.get("delivery_presets") is not None:
        preset_list = brief["delivery_presets"]
        if not isinstance(preset_list, list) or not preset_list:
            return False, {"code": "INVALID_SCHEMA",
                           "message": "delivery_presets must be a non-empty list of preset names",
                           "fix": "set delivery_presets to e.g. ['youtube_1080', 'shorts_720'], or remove it"}
        bad = [p for p in preset_list if p not in DELIVERY_PRESETS]
        if bad:
            return False, {"code": "INVALID_SCHEMA",
                           "message": f"unknown delivery preset(s): {', '.join(bad)}; known: {', '.join(sorted(DELIVERY_PRESETS))}",
                           "fix": "use delivery presets from: " + ", ".join(sorted(DELIVERY_PRESETS))}
        jobs = [(name, name) for name in preset_list]
    elif brief.get("delivery_preset") is not None:
        jobs = [(brief["delivery_preset"], brief["delivery_preset"])]
    else:
        jobs = [(fmt, fmt) for fmt in ("vertical", "square", "landscape")]
    variants = {}
    for key, _preset_marker in jobs:
        variant = copy.deepcopy(brief)
        variant.pop("delivery_presets", None)
        if key in DELIVERY_PRESETS:
            variant["delivery_preset"] = key
        else:
            variant["format"] = key
            variant.pop("delivery_preset", None)
        out = os.path.join(output_dir, f"{base_id}-{key}.mp4")
        ok, info = produce_video(variant, out)
        if not ok:
            return False, {"code": info.get("code", "RENDER_FAILED"),
                           "message": info.get("message") or info,
                           "format": key, "variants": variants}
        info = dict(info)
        info.pop("project", None)
        variants[key] = info
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
               "transition": clip.get("transition", "none"),
               "track": int(clip.get("track", 0))}
        for fname in ("speed", "speed_curve", "reverse", "freeze_at",
                      "loop_count", "ken_burns", "fit", "stabilize",
                      "treatment", "scene_id"):
            if fname in clip:
                seg[fname] = clip[fname]
        # v1.40.0: the camera path, when present, is the clip's
        # motion; it wins over Ken Burns, recorded plainly like
        # punch below.
        if clip.get("camera"):
            seg["camera"] = [{"t": round(float(k["t"]), 3),
                              "x": float(k["x"]), "y": float(k["y"]),
                              "scale": float(k["scale"])}
                             for k in clip["camera"]]
        if clip.get("_continuity"):
            seg["continuity_from_previous"] = True
        # v1.37.0: the motion actually applied (default motion
        # fills in for clips without an explicit ken_burns),
        # punch-in and element SFX, so the agent can reason from
        # the summary instead of re-reading the project.
        _eff = effective_ken_burns(clip, i, project)
        if clip.get("camera"):
            seg["effective_ken_burns"] = "none (camera)"
        elif clip.get("punch_at") is not None:
            # v1.37.2: punch wins over Ken Burns, recorded plainly.
            seg["effective_ken_burns"] = "none (punch_in)"
        elif _eff != clip.get("ken_burns", "none"):
            seg["effective_ken_burns"] = _eff
        if clip.get("punch_at") is not None:
            seg["punch_at"] = clip["punch_at"]
            seg["punch_factor"] = clip.get("punch_factor", 1.20)
        if clip.get("sfx"):
            seg["sfx"] = clip["sfx"]
        segments.append(seg)
    texts = [{"text": t["text"], "start": float(t.get("start", 0)),
              "end": float(t.get("end", info["total_duration"]))}
             for t in (project.get("texts") or [])]
    # v1.40.0: style preset + kinetic flag, when present, so the
    # agent can reason about which titles are kinetic.
    for _t, _tt in zip(texts, project.get("texts") or []):
        if isinstance(_tt, dict):
            if _tt.get("style"):
                _t["style"] = _tt["style"]
            if _tt.get("word_timings"):
                _t["kinetic"] = True
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
    # v1.40.0: parallax depth, when set, so the agent can reason
    # about which overlays drift with camera motion.
    for _o, _ov in zip(overlays, info.get("overlays", [])):
        if "depth" in _ov:
            _o["depth"] = float(_ov["depth"])
    # v1.38.0: stroke diagrams — the draw window (start + draw)
    # is when the animation runs; the diagram holds complete
    # from draw end until the window end.
    diagrams = [{"id": d["id"], "source": d.get("source"),
                 "template": d.get("template"),
                 "strokes": len(d.get("strokes") or []),
                 "start": float(d["start"]), "end": float(d["end"]),
                 "draw": float(d["draw"]),
                 "draw_end": round(float(d["start"]) + float(d["draw"]), 3),
                 "size": float(d.get("size", 0.5)),
                 "position": d.get("position", "bottom_right")}
                for d in info.get("diagrams", [])]
    return True, {"id": project["id"],
                  "total_duration": round(info["total_duration"], 3),
                  "width": info["width"], "height": info["height"],
                  "fps": info["fps"], "segments": segments,
                  "texts": texts, "captions": captions,
                  "audio": audio, "overlays": overlays,
                  "diagrams": diagrams,
                  "custom_transitions": info.get("custom_transitions", []),
                  "adjustments": info.get("adjustments", []),
                  "chapters": info.get("chapters", []),
                  "warnings": info.get("warnings", []),
                  "ducking": info.get("ducking"),
                  "treatment": project.get("treatment"),
                  "default_image_motion": bool(project.get("default_image_motion"))}


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


def _pid_alive(pid):
    """True if a pid exists (a worker that died leaves no process)."""
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                try:
                    code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                        return code.value == STILL_ACTIVE
                    return True
                finally:
                    kernel32.CloseHandle(handle)
            return kernel32.GetLastError() == 5
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class JobStore:
    """Async render/preview jobs stored as JSON files under a directory.

    Jobs run as detached subprocesses (Loop 6 round 1), not as threads
    of the server: a client may close stdio right after queueing a
    job and the worker keeps running. Job state lives on disk, so a
    NEW server connection can poll jobs from earlier sessions.
    """

    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, job_id):
        return os.path.join(self.directory, f"{job_id}.json")

    def create(self, kind, project):
        job_id = uuid.uuid4().hex[:12]
        record = {"id": job_id, "kind": kind, "status": "queued",
                  "project": project, "output": None, "error": None}
        self._write(record)
        return job_id

    def get(self, job_id):
        path = self._path(job_id)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            record = json.load(fh)
        # A worker that died without writing a final state would
        # otherwise leave a job stuck in "running" forever. Its pid
        # is gone, so the job is failed, not running.
        if (record.get("status") in ("queued", "running")
                and record.get("pid")
                and not _pid_alive(record["pid"])):
            record["status"] = "failed"
            record["error"] = {"code": "RENDER_FAILED",
                               "message": "job worker exited before completing"}
            self._write(record)
        return record

    def _write(self, record):
        # Atomic state file: write beside the target, then rename, so
        # a reader never sees a half-written job record.
        path = self._path(record["id"])
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)

    def run_async(self, job_id, out_dir):
        record = self.get(job_id)
        if record is None:
            return
        record["status"] = "running"
        self._write(record)
        # Detached worker: a new session, no stdio inheritance, so
        # closing the client's stdio pipe cannot kill it. It runs
        # `agentcut worker` from this same source tree (PYTHONPATH
        # pointed at src/) with the spawning server's working
        # directory, so relative media paths resolve identically.
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
        if sys.platform == "win32":
            flags = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=flags,
                close_fds=True, cwd=os.getcwd(), env=env)
        else:
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
                close_fds=True, cwd=os.getcwd(), env=env)
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentcut", "worker",
             self.directory, job_id, out_dir],
            **popen_kwargs)
        record["pid"] = proc.pid
        self._write(record)


def run_job_worker(jobs_dir, job_id, out_dir):
    """Detached worker entry point: run one job and write its state.

    Reads the job record from disk (never from the spawning server's
    memory), runs the render or preview, and writes the final state
    atomically. Returns True on a completed job, False otherwise.
    """
    store = JobStore(jobs_dir)
    record = store.get(job_id)
    if record is None:
        return False
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
        ok = False
    store._write(record)
    return ok


from .reframe import reframe_to_variants, reframe_video

