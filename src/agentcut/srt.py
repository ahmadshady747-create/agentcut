"""SRT parsing, subtitle wrapping and ASS export for AgentCut.

Only what captions need: cue index (optional), a time range, and
text. The parser joins a cue's lines into one logical string;
wrapping into display lines happens at render/validate time via
wrap_subtitle_text (shared helper: validate.wrap_caption_lines
delegates to it, so there is exactly one wrapping algorithm).
Malformed input raises ValueError with a human-readable message;
validation turns that into a structured INVALID_SCHEMA error.
"""
from __future__ import annotations

import ctypes
import os
import re

_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

# ASS colour: &HAABBGGRR (alpha, blue, green, red). Opaque white
# text, opaque black outline — the same look as caption drawtext.
# Karaoke highlight is opaque yellow over the same white base.
_ASS_WHITE = "&H00FFFFFF"
_ASS_BLACK = "&H00000000"
_ASS_YELLOW = "&H0000FFFF"


def _to_seconds(h, m, s, frac):
    frac = (frac + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 1000.0


# Relative-width weight table (v1.41.0, Fix 2): fractions of the
# font size per character, replacing the flat 0.6 estimate. R1's
# true ink measurements (Arabic words at size 60: ~0.35-0.55 x
# size per char) showed flat 0.6 overestimates every Arabic word
# and breaks lines far too early; narrow letters are far narrower
# than wide ones, and diacritics add no width at all (they sit on
# their base letter). Latin/digits keep ~0.55. Wrap is an
# estimate, not rendering — libass/drawtext lay out the line.
_ARABIC_WIDE = set("موشصضطظك")
_ARABIC_NARROW = set("الإألرزد")
_ARABIC_DIACRITICS = set(chr(c) for c in range(0x064B, 0x0653)) | {"\u0670"}


def _char_weight(ch):
    """Relative width of one character as a fraction of font size."""
    if ch in _ARABIC_DIACRITICS:
        return 0.0
    if ch == " ":
        return 0.30
    if ch in _ARABIC_WIDE:
        return 0.60
    if ch in _ARABIC_NARROW:
        return 0.25
    if ("\u0600" <= ch <= "\u06FF" or "\u0750" <= ch <= "\u077F"
            or "\u08A0" <= ch <= "\u08FF"
            or "\uFB50" <= ch <= "\uFDFF"
            or "\uFE70" <= ch <= "\uFEFF"):
        return 0.45
    return 0.55


def _word_width(word, size):
    """Estimated pixel width of a word at font `size`."""
    return max(10.0, sum(_char_weight(ch) for ch in word) * float(size))


# HarfBuzz (ctypes) shaped-text measurement (v1.46.1, R1).
# Moved here from render.py (v1.45.0, R2) so the wrapper and the
# validator can measure with the real render font: HarfBuzz shapes
# the text with the same engine FFmpeg's drawtext uses (same font,
# same shaping), and the shaped-advance sum is the width drawtext
# lays out. The 5-int32 hb_glyph_position_t (20-byte stride,
# HarfBuzz 8.x) is load-bearing: a 4-int32 struct reads garbage
# advances (probed empirically). The library is loaded lazily
# once; when it (or the font file) is missing, measure_text_width
# falls back to the 0.6 x size estimate. render.py keeps
# _measure_text_width as a thin delegate to this function.
_HB = None
_HB_TRIED = False


class _HBPosition(ctypes.Structure):
    _fields_ = [("x_advance", ctypes.c_int32),
                ("y_advance", ctypes.c_int32),
                ("x_offset", ctypes.c_int32),
                ("y_offset", ctypes.c_int32),
                ("var", ctypes.c_int32)]


def _load_harfbuzz():
    """Load HarfBuzz once (ctypes); None when unavailable."""
    global _HB, _HB_TRIED
    if _HB_TRIED:
        return _HB
    _HB_TRIED = True
    candidates = [
        "libharfbuzz.so.0",
        "libharfbuzz.so",
        "libharfbuzz.dylib",
        "libharfbuzz.0.dylib",
        "harfbuzz.dll",
        "libharfbuzz-0.dll",
        "libHarfBuzzSharp.dll",
    ]
    if os.name == "nt":
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        local_app = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("ProgramFiles", "")
        candidates.extend([
            os.path.join(base_dir, "harfbuzz.dll"),
            os.path.join(local_app, "agy", "bin", "harfbuzz.dll"),
            os.path.join(local_app, "Programs", "Git", "ucrt64", "bin", "libHarfBuzzSharp.dll"),
            os.path.join(prog_files, "Git", "ucrt64", "bin", "libHarfBuzzSharp.dll"),
            os.path.join(local_app, "Programs", "Git", "mingw64", "bin", "libharfbuzz-0.dll"),
            os.path.join(prog_files, "Git", "mingw64", "bin", "libharfbuzz-0.dll"),
        ])
    for cand in candidates:
        if not cand:
            continue
        try:
            hb = ctypes.CDLL(cand)
            hb.hb_blob_create.restype = ctypes.c_void_p
            hb.hb_blob_create.argtypes = [ctypes.c_char_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_void_p,
                                          ctypes.c_void_p]
            hb.hb_blob_destroy.restype = None
            hb.hb_blob_destroy.argtypes = [ctypes.c_void_p]
            hb.hb_face_create.restype = ctypes.c_void_p
            hb.hb_face_create.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            hb.hb_face_destroy.restype = None
            hb.hb_face_destroy.argtypes = [ctypes.c_void_p]
            hb.hb_face_get_upem.restype = ctypes.c_uint
            hb.hb_face_get_upem.argtypes = [ctypes.c_void_p]
            hb.hb_font_create.restype = ctypes.c_void_p
            hb.hb_font_create.argtypes = [ctypes.c_void_p]
            hb.hb_font_destroy.restype = None
            hb.hb_font_destroy.argtypes = [ctypes.c_void_p]
            hb.hb_ot_font_set_funcs.restype = None
            hb.hb_ot_font_set_funcs.argtypes = [ctypes.c_void_p]
            hb.hb_font_set_scale.restype = None
            hb.hb_font_set_scale.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int]
            hb.hb_buffer_create.restype = ctypes.c_void_p
            hb.hb_buffer_create.argtypes = []
            hb.hb_buffer_destroy.restype = None
            hb.hb_buffer_destroy.argtypes = [ctypes.c_void_p]
            hb.hb_buffer_add_utf8.restype = None
            hb.hb_buffer_add_utf8.argtypes = [ctypes.c_void_p,
                                              ctypes.c_char_p, ctypes.c_int,
                                              ctypes.c_uint, ctypes.c_int]
            hb.hb_buffer_guess_segment_properties.restype = None
            hb.hb_buffer_guess_segment_properties.argtypes = [ctypes.c_void_p]
            hb.hb_shape.restype = None
            hb.hb_shape.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_uint]
            hb.hb_buffer_get_length.restype = ctypes.c_uint
            hb.hb_buffer_get_length.argtypes = [ctypes.c_void_p]
            hb.hb_buffer_get_glyph_positions.restype = ctypes.POINTER(
                _HBPosition)
            hb.hb_buffer_get_glyph_positions.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
            _HB = hb
            break
        except Exception:
            continue
    return _HB


def measure_text_width(text, font, size, cache=None):
    """Shaped text width (px) at one size/font.

    Measured against the old cropdetect ink width on 12 Arabic+Latin
    words (sizes 48-60): the shaped width is always >= the ink width
    (ratio 1.020-1.208), so a bar or pill sized by this width always
    covers its text, with at most ~21% slack on short words
    (absorbed by the 12px padding). The direction does not matter
    for the total: advances sum the same in logical or display
    order, so the logical text is shaped directly. Returns the
    0.6 x size per-character estimate (never None) when HarfBuzz
    or the font file is unavailable, so callers need no fallback
    of their own. Results are cached by (text, font, size) when a
    cache dict is passed.
    """
    text = str(text)
    key = (text, font, size)
    if cache is not None and key in cache:
        return cache[key]
    fallback = max(10.0, len(text) * float(size) * 0.6)
    width = fallback
    hb = _load_harfbuzz()
    if hb is not None and font and os.path.isfile(font):
        blob = face = hbfont = buf = None
        try:
            with open(font, "rb") as fh:
                data = fh.read()
            # READONLY blob: no copy, no ownership — `data` must
            # stay alive until the face is destroyed (it is a
            # local, destroyed in the finally below).
            blob = hb.hb_blob_create(data, len(data), 1, None, None)
            face = hb.hb_face_create(blob, 0)
            hbfont = hb.hb_font_create(face)
            upem = hb.hb_face_get_upem(face)
            hb.hb_ot_font_set_funcs(hbfont)
            hb.hb_font_set_scale(hbfont, upem, upem)
            buf = hb.hb_buffer_create()
            raw = text.encode("utf-8")
            hb.hb_buffer_add_utf8(buf, raw, len(raw), 0, -1)
            hb.hb_buffer_guess_segment_properties(buf)
            hb.hb_shape(hbfont, buf, None, 0)
            count = ctypes.c_uint(0)
            pos = hb.hb_buffer_get_glyph_positions(buf,
                                                   ctypes.byref(count))
            total = 0
            for i in range(count.value):
                total += pos[i].x_advance
            width = total * float(size) / upem
        except Exception:
            width = fallback
        finally:
            if buf is not None:
                hb.hb_buffer_destroy(buf)
            if hbfont is not None:
                hb.hb_font_destroy(hbfont)
            if face is not None:
                hb.hb_face_destroy(face)
            if blob is not None:
                hb.hb_blob_destroy(blob)
    if cache is not None:
        cache[key] = width
    return width


def wrap_lines(text, size, max_width_px, measure=None):
    """Greedy word-wrap of LOGICAL text into lines fitting
    max_width_px. Words are never split, so Arabic words stay
    whole and joined (BiDi is applied per line later, at render
    time — wrapping the logical text first is what keeps word
    order correct). A single word wider than max_width_px stays
    on its own line (it will still clip; the overflow warning
    still fires for it). Width uses the relative-weight table
    above (v1.41.0, Fix 2) by default; pass measure=(word, size)
    -> px (e.g. HarfBuzz on the real render font) and the wrap
    packs exactly what the renderer lays out — the table was
    tuned for Noto Naskh Arabic and underestimates Noto Sans
    Arabic Bold by ~30% (v1.46.1, R0: the table packs a 785px
    line as 558px, which clipped at 720x1280). Returns at least
    one line. This is the single source of truth for wrapping;
    see wrap_subtitle_text for the video-width-based wrapper.
    """
    words = str(text).split()
    if not words:
        return [str(text)]
    if measure is None:
        def measure(word, size=size):
            return _word_width(word, size)
    lines, cur, cur_w = [], "", 0.0
    space_w = measure(" ", size)
    for word in words:
        word_w = measure(word, size)
        new_w = word_w if not cur else cur_w + space_w + word_w
        if cur and new_w > max_width_px:
            lines.append(cur)
            cur, cur_w = word, word_w
        else:
            cur = (cur + " " + word) if cur else word
            cur_w = new_w
    if cur:
        lines.append(cur)
    return lines


def wrap_subtitle_text(text, width, font_size, max_width_frac=0.8,
                       measure=None):
    """Wrap subtitle/caption text for a video of `width` pixels.

    The text block spans at most max_width_frac of the video
    width (default 0.8, the v1.41.0 Fix 2 cap — text never crowds
    the edges; the old 0.9 cap is still available explicitly).
    font_size is the caption font size in pixels. Words are
    measured with the relative-weight table and never split, so
    a long Arabic sentence wraps at word boundaries and a long
    English word stays whole. Pass measure=(word, size) -> px to
    wrap with the real render font instead of the table (see
    wrap_lines). Returns a list of logical lines (BiDi is applied
    per line at render time, never here)."""
    return wrap_lines(text, font_size, float(width) * float(max_width_frac),
                      measure=measure)


def batch_captions_by_window(cues, window_seconds=60.0):
    """Group cues into indexed time windows for batched rendering.

    Returns a list of {"index", "start", "end", "cues"} windows,
    each holding the cues whose start falls in that window
    (cues are assumed sorted by start; unsorted input is sorted
    first). Long projects render one ASS file (or one drawtext
    set) per window instead of one filter per cue for the whole
    timeline, so FFmpeg evaluates only the current window's
    captions per frame. A cue is never split across windows: it
    belongs to the window its start falls in, even if it ends
    past the window edge."""
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    ordered = sorted(cues or [], key=lambda c: float(c.get("start", 0)))
    windows = []
    for cue in ordered:
        start = float(cue.get("start", 0))
        idx = int(start // window_seconds)
        while len(windows) <= idx:
            wstart = len(windows) * window_seconds
            windows.append({"index": len(windows), "start": wstart,
                            "end": wstart + window_seconds, "cues": []})
        windows[idx]["cues"].append(cue)
    return [w for w in windows if w["cues"]]


def _ass_timestamp(seconds):
    """ASS time H:MM:SS.cc (centiseconds, truncated, not rounded)."""
    total_cs = int(float(seconds) * 100)
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text):
    """Escape ASS override syntax: backslash, braces, and newlines.

    A literal { or \\ would otherwise start (or break) an override
    tag; newlines become \\N (ASS hard line break)."""
    return (str(text).replace("\\", r"\\").replace("{", r"\{")
               .replace("}", r"\}").replace("\n", r"\N"))


def _karaoke_dialogue(cue, karaoke):
    """Build (style, name, body) for one cue's karaoke dialogue,
    or None for a plain (non-karaoke) dialogue.

    karaoke modes: "auto" (measured word_timings when present
    and matching the cue text, else a proportional split),
    "proportional" (always a proportional split), "measured"
    (word_timings only, else plain), None/False (never karaoke).
    Proportional splits are labelled with the dialogue Name
    'est' plus a header note: they are estimates from word
    lengths, never measured timings. Measured karaoke plays word
    highlights sequentially, so inter-word gaps are compressed
    (durations are differences, so absolute and cue-relative
    word_timings give identical \\k values). Word_timings whose
    words do not match the cue text fall back to proportional
    (auto) or plain (measured) — a mismatched timing is never
    silently applied to the wrong words."""
    if not karaoke:
        return None
    words = str(cue.get("text", "")).split()
    if not words:
        return None
    try:
        start = float(cue.get("start", 0))
        end = float(cue.get("end", 0))
    except (TypeError, ValueError):
        return None
    total_cs = int((end - start) * 100)
    if total_cs <= 0:
        return None
    timings = cue.get("word_timings")
    if isinstance(timings, list) and timings:
        twords = []
        ok = True
        for tw in timings:
            if not isinstance(tw, dict):
                ok = False
                break
            w = tw.get("word", tw.get("text"))
            try:
                ws, we = float(tw["start"]), float(tw["end"])
            except (KeyError, TypeError, ValueError):
                ok = False
                break
            if not isinstance(w, str) or not w or we <= ws:
                ok = False
                break
            twords.append((w, ws, we))
        if ok and [w for w, _, _ in twords] == words:
            parts = []
            for w, ws, we in twords:
                d = max(1, int(round((we - ws) * 100)))
                parts.append("{\\k%d}%s" % (d, _ass_escape(w)))
            return ("Karaoke", "", " ".join(parts))
        if karaoke == "measured":
            return None
    elif karaoke == "measured":
        return None
    # Proportional split by word length (largest remainder, so
    # the parts sum exactly to the cue duration).
    weights = [max(1, len(w)) for w in words]
    total_w = sum(weights)
    raw = [total_cs * w / total_w for w in weights]
    base = [max(1, int(r)) for r in raw]
    diff = total_cs - sum(base)
    order = sorted(range(len(words)),
                   key=lambda i: (raw[i] - base[i]), reverse=True)
    i = 0
    while diff > 0:
        base[order[i % len(words)]] += 1
        diff -= 1
        i += 1
    while diff < 0:
        j = max(range(len(words)), key=lambda k: base[k])
        if base[j] <= 1:
            break
        base[j] -= 1
        diff += 1
    parts = ["{\\k%d}%s" % (d, _ass_escape(w))
             for d, w in zip(base, words)]
    return ("Karaoke", "est", " ".join(parts))


def cues_to_ass_text(cues, width=1280, height=720, font_name="Noto Sans Arabic",
                     font_size=48, margin_v=120, fade_ms=120, shadow=1,
                     outline=2, karaoke="auto"):
    """Render cues as one unified ASS (Advanced SubStation Alpha)
    document (string). PlayRes matches the video size; margins
    leave 5% of the width on each side (the 90% caption rule) and
    margin_v pixels at the bottom (the caption baseline); the
    Default style is bottom-centre (alignment 2), opaque white
    text with an opaque black outline, matching caption drawtext.
    Delta upgrades (all additional, never replacing drawtext
    captions): every dialogue fades in/out with
    \\fad(fade_ms, fade_ms) by default (0 disables); the styles
    carry a soft outline plus a drop shadow (Shadow=1) by
    default; and dialogues are karaoke by default — a separate
    Karaoke style (yellow highlight over the white base) with
    \\k tags per word, measured from a cue's word_timings when
    present and matching, else a proportional split labelled
    'est' (see _karaoke_dialogue). karaoke=None disables karaoke
    (plain dialogues, fades/shadow still apply); "measured"
    restricts karaoke to measured timings only. ASS is an
    ADDITIONAL output: a caller renders it with a single
    subtitles filter instead of one drawtext filter per cue,
    which is the batching win on long projects. Raises
    ValueError when cues is empty or options are invalid."""
    cues = list(cues or [])
    if not cues:
        raise ValueError("ASS export needs at least one cue")
    try:
        fade_ms = float(fade_ms)
    except (TypeError, ValueError):
        raise ValueError("fade_ms must be a number")
    if fade_ms < 0:
        raise ValueError("fade_ms must be >= 0")
    if karaoke not in ("auto", "proportional", "measured", None, False):
        raise ValueError("karaoke must be 'auto', 'proportional', "
                         "'measured' or None")
    for name, val in (("shadow", shadow), ("outline", outline)):
        if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val <= 4:
            raise ValueError(f"{name} must be an integer 0..4")
    margin_lr = max(0, int(round(float(width) * 0.05)))
    fad = ("{\\fad(%d,%d)}" % (int(fade_ms), int(fade_ms))
           if fade_ms > 0 else "")
    dialogues = []
    any_karaoke = any_est = any_measured = False
    for cue in cues:
        k = _karaoke_dialogue(cue, karaoke)
        if k is not None:
            style, dname, body = k
            any_karaoke = True
            if dname == "est":
                any_est = True
            else:
                any_measured = True
        else:
            style, dname = "Default", ""
            body = _ass_escape(cue.get("text", ""))
        dialogues.append(
            f"Dialogue: 0,{_ass_timestamp(cue.get('start', 0))},"
            f"{_ass_timestamp(cue.get('end', 0))},{style},{dname},"
            f"0,0,0,,{fad}{body}")
    lines = [
        "[Script Info]",
        "; AgentCut ASS export (one unified subtitle file)",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 0",
    ]
    if any_est:
        lines.append("; NOTE: karaoke timings on dialogues named 'est' "
                     "are proportional estimates from word lengths, "
                     "not measured timings.")
    if any_measured:
        lines.append("; NOTE: measured karaoke plays word highlights "
                     "sequentially, so inter-word gaps are compressed.")
    lines += [
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{int(font_size)},{_ASS_WHITE},{_ASS_WHITE},{_ASS_BLACK},{_ASS_BLACK},0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{int(margin_v)},1",
    ]
    if any_karaoke:
        lines.append(
            f"Style: Karaoke,{font_name},{int(font_size)},{_ASS_YELLOW},{_ASS_WHITE},{_ASS_BLACK},{_ASS_BLACK},0,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{int(margin_v)},1")
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines += dialogues
    return "\n".join(lines) + "\n"


def export_ass(cues, path, width=1280, height=720, font_name="Noto Sans Arabic",
               font_size=48, margin_v=120, fade_ms=120, shadow=1,
               outline=2, karaoke="auto"):
    """Write cues to path as one unified ASS file. Returns path."""
    text = cues_to_ass_text(cues, width=width, height=height,
                            font_name=font_name, font_size=font_size,
                            margin_v=margin_v, fade_ms=fade_ms,
                            shadow=shadow, outline=outline,
                            karaoke=karaoke)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# AgentCut color names to ASS &H00BBGGRR& (alpha 00 = opaque).
# Drawtext accepts any FFmpeg color name; ASS needs hex, so the
# render path maps through this table. Unknown names raise
# ValueError loudly (a silent white fallback would hide a
# misspelled colour in the finished video).
_ASS_NAMED_COLORS = {
    "white": (255, 255, 255), "black": (0, 0, 0),
    "yellow": (255, 255, 0), "red": (255, 0, 0),
    "green": (0, 128, 0), "lime": (0, 255, 0),
    "blue": (0, 0, 255), "navy": (0, 0, 128),
    "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255),
    "orange": (255, 165, 0), "pink": (255, 192, 203),
    "purple": (128, 0, 128), "brown": (165, 42, 42),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "gold": (255, 215, 0),
    "teal": (0, 128, 128), "olive": (128, 128, 0),
    "maroon": (128, 0, 0), "coral": (255, 127, 80),
    "khaki": (240, 230, 140), "salmon": (250, 128, 114),
    "skyblue": (135, 206, 235), "beige": (245, 245, 220),
}


def _ass_hex(color):
    """AgentCut colour (name or #RRGGBB) to ASS &H00BBGGRR&."""
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
        except ValueError:
            raise ValueError(f"invalid hex colour: {color!r}")
        return f"&H00{b:02X}{g:02X}{r:02X}"
    rgb = _ASS_NAMED_COLORS.get(str(color).lower())
    if rgb is None:
        raise ValueError(
            f"unknown colour for ASS rendering: {color!r} "
            f"(use a named colour or #RRGGBB)")
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def build_karaoke_ass(events, width, height):
    """Build one unified ASS document for render-path word_timings.

    v1.41.0, Fix 2: this REPLACES per-word drawtext karaoke.
    Every Dialogue is one connected line (never one drawtext
    filter per word); libass + HarfBuzz + FriBidi shape the
    Arabic line as a single connected sentence, and the active
    word is highlighted INSIDE that line with a static colour tag. One Dialogue per word window (full line, word i
    in the highlight colour, the rest in the base colour), plus
    a plain base-colour Dialogue for any gap between word
    windows, so the line is covered for the whole chunk exactly
    as the drawtext base words covered it. NOT the t-transform: measured
    (ass-test 7, distinct colour per word) a t-transform highlight
    STICKS past its window — at t=2.5 all three words kept
    their \t colours — so \t gives cumulative karaoke, not
    current-word-only, and a reset tag placed before the next
    word cannot turn an earlier word white (tags affect only
    following text). NOT a scale tag either: a scaled word
    reflows its neighbours in libass (the line jumps at every
    word switch), while the drawtext pop grew in place; colour
    alone keeps the line layout identical across events, so
    only the colour changes. Emphasis/scale inputs are
    therefore accepted but have no visual effect in ASS.
    Event dicts: kind "karaoke" (words: [{word, start, end}],
    hi_color) or "plain" (text, base_color); both carry
    start, end, font (family, bold), size, base_color, align
    (7 top-left, 8 top-centre, 9 top-right), x, y. pos anchors
    the line: alignment 8 + pos(width/2, y) centres it exactly
    like drawtext x=center; alignment 7 + pos(x, y) puts its left
    edge at a numeric x. Styles are keyed by (family, bold, size,
    base colour, alignment), so one file serves mixed fonts.
    Outline 2 + shadow 1 match the exporter's caption look.
    RTL word order (v1.42.0, Fix 2 — the 1.41.0 model was WRONG,
    proven by v2 frames AND by a reproduction render):
    libass places tag-delimited runs LEFT-TO-RIGHT in emission
    order, while shaping words right-to-left WITHIN each run
    (a run reads in its string order). A single untagged line
    is laid out as one RTL paragraph and reads correctly —
    hold titles were correct in the v2 frames. But a tagged
    line is NOT one paragraph: the colour tags split it into
    runs, and the runs sit LTR. The 1.41.0 builder emitted
    tagged lines in REVERSE word order, which put the
    highlight in the right place (word1 rightmost, word4
    leftmost — its ass-tests 5-6 measured only highlight
    x-bboxes) while scrambling the word order at every index
    (v2 t=362 "الوكلاء المقود يمسكون", t=427 "الهجوم الحافة
    من المضاد"). A first 1.42.0 attempt — logical order +
    RLM anchors at line start and after every closing tag —
    did NOT fix it either: the reproduction render showed
    the highlight flipped to the WRONG side (word1 leftmost)
    with the word order still scrambled, while the plain hold
    line with the same RLM rendered correctly. RLM pins the
    paragraph direction; it does not reorder runs. The fix
    that the frames prove is RUN-ORDER emission: emit the
    runs in reverse reading order, each run's words in
    logical order — for the window highlighting word i:
    [words AFTER i, logical order] then the highlighted
    word, then [words BEFORE i, logical order]. The runs
    land LTR (last logical words leftmost), each run reads
    in its string order, and the whole line reads in
    logical order with the highlight on the right word.
    RLM anchors are kept (line start + after every closing
    colour tag) as paragraph-direction insurance: they are
    proven harmless (the hold line renders correctly with
    them) and protect lines whose first run starts with a
    tag. WrapStyle: 2 in the header (single-line titles
    must not wrap). Untagged lines (plain hold/gap/chunk
    events) are emitted in LOGICAL order with an RLM at
    line start (FriBidi reorders untagged runs itself;
    reversing them would double-reverse the line — found
    by v2 frame inspection in R11b). Latin-only lines are
    emitted in order with no RLM, byte-identical to before
    (LTR run placement + LTR words need no reordering).
    Spacing (v1.42.0 R7b — found in the v3 frames): a space
    emitted as the LEADING character of a run after a tag
    boundary (closing colour tag + RLM) is DROPPED by
    libass, so at every highlight index > 0 the highlighted
    word joined the word before it in logical order (16/26
    v3 kinetic frames: "الوكلاءيمسكون", "الهجومالمضاد").
    A TRAILING space survives (the after-run's trailing
    space rendered fine), so the gap between the highlighted
    word and the before-run is emitted INSIDE the
    highlighted run, before the closing colour tag. A space
    has no ink, so its colour is invisible and shaping is
    unchanged. The R1 tests could not catch this: the ASS
    text is identical up to whitespace placement, and the
    centroid/template checks tolerated the ~6px reflow.
    Verification (TestLoop142 R7b): rendered group count —
    4 word groups at min_gap=6 at every highlight index
    and the hold, with spans matching the hold.
    Raises ValueError on empty events or unknown colours.
    Verification (TestLoop142): ASS-text assertions pin the
    exact run-ordered emission for the scene-8 title at
    every highlight index, plus a rendered frame check that
    compares each kinetic window's ink mask against the
    plain hold line (the known-correct reference) and
    asserts the yellow highlight moves right-to-left.
    A centroid check alone CANNOT prove word order — the
    1.41.0 code passed one while scrambling the line."""

    RLM = "\u200f"
    events = list(events or [])
    if not events:
        raise ValueError("karaoke ASS needs at least one event")
    styles = {}
    order = []

    def _style_key(ev):
        key = (ev["font"][0], int(ev["font"][1]), int(ev["size"]),
               _ass_hex(ev["base_color"]), int(ev["align"]))
        if key not in styles:
            styles[key] = f"S{len(styles)}"
            order.append(key)
        return styles[key]

    dialogues = []
    for ev in events:
        style = _style_key(ev)
        base_hex = _ass_hex(ev["base_color"])
        pos = r"{\pos(%s,%s)}" % (int(round(float(ev["x"]))),
                                  int(round(float(ev["y"]))))
        if ev["kind"] == "plain":
            # Untagged: FriBidi reorders the line itself, so the
            # text stays in logical order. Reversing here would
            # double-reverse it and the line displays backwards
            # (found by v2 frame inspection in R11b). An RLM at
            # line start pins the paragraph direction for
            # Arabic lines; Latin lines are byte-identical.
            body = pos + (RLM if _ARABIC_RE.search(ev["text"]) else "") \
                + _ass_escape(ev["text"])
        elif ev["kind"] == "karaoke":
            hi_hex = _ass_hex(ev["hi_color"])
            chron = list(ev["words"])
            is_ar = any(_ARABIC_RE.search(w["word"]) for w in chron)
            # One Dialogue per word window (chronological file
            # order): the full line, with the window's word
            # highlighted and the rest base. Gaps between
            # windows get a plain base line, so coverage is
            # continuous across the chunk. For Arabic lines the
            # runs are emitted in reverse reading order (see
            # docstring): words after the highlighted word,
            # then the highlighted word, then the words before
            # it — each run's words in logical order. Latin
            # lines keep plain logical order.
            for ci, w in enumerate(chron):
                parts = [pos]
                if is_ar:
                    parts.append(RLM)
                if is_ar:
                    after = chron[ci + 1:]
                    before = chron[:ci]
                    for j, w2 in enumerate(after):
                        if j > 0:
                            parts.append(" ")
                        parts.append(_ass_escape(w2["word"]))
                    if after:
                        parts.append(" ")
                    parts.append(r"{\c&H%s&}" % hi_hex[2:])
                    parts.append(_ass_escape(w["word"]))
                    # R7b (Fix 2, kinetic-space loss): the gap
                    # between the highlighted word and the word
                    # before it in logical order must NOT be
                    # emitted as the LEADING space of the
                    # before-run — libass drops a space that is
                    # the first visible character after a tag
                    # boundary (closing colour tag + RLM), so the
                    # highlighted word joined its neighbour at
                    # every index > 0 in the v3 frames (16/26
                    # kinetic frames). A TRAILING space survives
                    # (the after-run's trailing space rendered
                    # fine), so the gap is emitted here, inside
                    # the highlighted run before the closing tag.
                    # A space has no ink, so its colour is
                    # invisible; the shaping is unchanged (the
                    # hold line has the same word + space).
                    if before:
                        parts.append(" ")
                    parts.append(r"{\c&H%s&}" % base_hex[2:])
                    parts.append(RLM)
                    for j, w2 in enumerate(before):
                        if j > 0:
                            parts.append(" ")
                        parts.append(_ass_escape(w2["word"]))
                else:
                    for j, w2 in enumerate(chron):
                        if j > 0:
                            parts.append(" ")
                        if j == ci:
                            parts.append(r"{\c&H%s&}" % hi_hex[2:])
                            parts.append(_ass_escape(w2["word"]))
                            parts.append(r"{\c&H%s&}" % base_hex[2:])
                        else:
                            parts.append(_ass_escape(w2["word"]))
                dialogues.append(
                    f"Dialogue: 0,{_ass_timestamp(w['start'])},"
                    f"{_ass_timestamp(w['end'])},{style},,0,0,0,,"
                    f"{''.join(parts)}")
            for ci in range(len(chron) - 1):
                _we = float(chron[ci]["end"])
                _ns = float(chron[ci + 1]["start"])
                if _we < _ns:
                    _gap = [pos]
                    # Untagged gap line: logical order (chron),
                    # NOT reversed — FriBidi reorders untagged
                    # runs itself — with an RLM at line start
                    # for Arabic lines.
                    if is_ar:
                        _gap.append(RLM)
                    for j, w2 in enumerate(chron):
                        if j > 0:
                            _gap.append(" ")
                        _gap.append(_ass_escape(w2["word"]))
                    dialogues.append(
                        f"Dialogue: 0,{_ass_timestamp(_we)},"
                        f"{_ass_timestamp(_ns)},{style},,0,0,0,,"
                        f"{''.join(_gap)}")
            continue
        else:
            raise ValueError(f"unknown karaoke event kind: {ev['kind']!r}")
        dialogues.append(
            f"Dialogue: 0,{_ass_timestamp(ev['start'])},"
            f"{_ass_timestamp(ev['end'])},{style},,0,0,0,,{body}")
    lines = [
        "[Script Info]",
        "; AgentCut render-path karaoke ASS (v1.42.0 Fix 2)",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for key in order:
        fam, bold, size, base_hex, align = key
        lines.append(
            f"Style: {styles[key]},{fam},{size},{base_hex},{base_hex},"
            f"{_ASS_BLACK},{_ASS_BLACK},{bold},0,0,0,100,100,0,0,1,2,1,"
            f"{align},0,0,0,1")
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines += dialogues
    return "\n".join(lines) + "\n"


def parse_srt_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if not blocks:
        raise ValueError("SRT file has no cues")
    cues = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines:
            raise ValueError(f"cue with no time range: {block[:80]!r}")
        m = _TIME_RE.match(lines[0])
        if not m:
            raise ValueError(f"bad SRT time range: {lines[0]!r}")
        start = _to_seconds(*m.groups()[:4])
        end = _to_seconds(*m.groups()[4:])
        cue_text = " ".join(lines[1:]).strip()
        if end <= start:
            raise ValueError(f"SRT cue ends before it starts: {lines[0]!r}")
        if not cue_text:
            raise ValueError(f"SRT cue has no text: {lines[0]!r}")
        cues.append({"text": cue_text, "start": start, "end": end})
    if not cues:
        raise ValueError("SRT file has no cues")
    return cues


def parse_srt_file(path):
    with open(path, encoding="utf-8") as fh:
        return parse_srt_text(fh.read())
