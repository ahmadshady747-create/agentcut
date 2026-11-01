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

import re

_TIME_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

# ASS colour: &HAABBGGRR (alpha, blue, green, red). Opaque white
# text, opaque black outline — the same look as caption drawtext.
# Karaoke highlight is opaque yellow over the same white base.
_ASS_WHITE = "&H00FFFFFF"
_ASS_BLACK = "&H00000000"
_ASS_YELLOW = "&H0000FFFF"


def _to_seconds(h, m, s, frac):
    frac = (frac + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 1000.0


def wrap_lines(text, size, max_width_px):
    """Greedy word-wrap of LOGICAL text into lines fitting
    max_width_px. Words are never split, so Arabic words stay
    whole and joined (BiDi is applied per line later, at render
    time — wrapping the logical text first is what keeps word
    order correct). A single word wider than max_width_px stays
    on its own line (it will still clip; the overflow warning
    still fires for it). Width uses the 0.6 x size per character
    estimate, the same estimator the renderer uses, so the
    estimator and the renderer agree. Returns at least one line.
    This is the single source of truth for wrapping; see
    wrap_subtitle_text for the video-width-based wrapper."""
    words = str(text).split()
    if not words:
        return [str(text)]
    lines, cur, cur_w = [], "", 0.0
    space_w = float(size) * 0.6
    for word in words:
        word_w = max(10.0, len(word) * float(size) * 0.6)
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


def wrap_subtitle_text(text, width, font_size, max_width_frac=0.9):
    """Wrap subtitle/caption text for a video of `width` pixels.

    The text block spans at most max_width_frac of the video
    width (default 0.9, the 1.36.0 caption rule); font_size is the
    caption font size in pixels. Arabic and English words are
    measured the same way (0.6 x font_size per character) and
    never split, so a long Arabic sentence wraps at word
    boundaries and a long English word stays whole. Returns a
    list of logical lines (BiDi is applied per line at render
    time, never here)."""
    return wrap_lines(text, font_size, float(width) * float(max_width_frac))


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


def cues_to_ass_text(cues, width=1280, height=720, font_name="Noto Naskh Arabic",
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


def export_ass(cues, path, width=1280, height=720, font_name="Noto Naskh Arabic",
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
