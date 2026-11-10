"""Animated stroke diagrams (v1.38.0): a general, reusable element.

A project may carry a ``diagrams`` list. Each diagram is either
custom strokes supplied by the agent, or one of the built-in
parameterized templates below. AgentCut renders the diagram as a
progressive draw-on animation: strokes appear one after another,
in list order, over the diagram's draw window, and the final
frame is exactly the complete diagram (the animation is
monotonic — nothing ever disappears — so the held final frame
after the draw window is pixel-identical to the complete art).

Rendering is stdlib + FFmpeg only, like the rest of AgentCut
(PIL, when already present, is used automatically for a 2x
supersampled path — never required). The diagram is drawn into
RGBA frames with a small stdlib rasterizer: strokes are
continuous capsule SDF segments along a sampled path (v1.41.0,
Fix 3 — clean vector lines; the stamped discs, their chalk
grain and the wobble are gone by design (v1.45.0, R4a). Points
are clean resampled vectors, so every frame of the animation
shows the same stroke shape — there is deliberately NO animated
jitter, which would read as vibration. Frames are piped as rawvideo into FFmpeg and encoded to a
temporary transparent (PNG codec, RGBA) video; the main render
composites that video with the normal overlay filter and loops
its last frames for the hold after the draw window ends.

Artwork rules (serious, not childish): clean geometry, correct
proportions, one consistent stroke weight per stroke, and two
colours — white for the drawing, yellow for emphasis only.
Labels are NEVER baked into a diagram: templates take no text,
and every label on screen
is an AgentCut text (drawtext), so Arabic labels keep the
BiDi-verified path and never become AI-generated image text.

Stroke schema (diagram-local coordinates, origin top-left):
  {"kind": "polyline", "points": [[x, y], ...], ...}
  {"kind": "line", "points": [[x1, y1], [x2, y2]], ...}
  {"kind": "arrow", "points": [[x, y], ...], "head_size": 36, ...}
  {"kind": "circle", "center": [x, y], "radius": r, ...}
  {"kind": "ellipse", "center": [x, y], "radius_x": rx, "radius_y": ry, ...}
  {"kind": "rect", "x": x, "y": y, "rect_width": w, "rect_height": h,
   "corner_radius": 0, ...}
  (rect geometry uses rect_width/rect_height because "width"
  already means the stroke width on every stroke kind.)
Common stroke fields: "color" ("white" default, "yellow", any
named colour or #RRGGBB), "width" (2..20, default 7),
"alpha" (0.2..1, default 0.95), "weight" (positive number;
a stroke's share of the draw window defaults to its path
length, so longer strokes naturally take longer to draw).
A "seed" field, when present on a stroke, is ignored (it used
to drive the deleted wobble/grain; old projects still validate).

Templates (see build_template): "chart" (rising chart with
axes, faint grid and an arrowed curve), "basket" (basket with
coins), "balance" (beam scale, tilt from two values),
"causes" (rows of badge + arrow), "primitives" (arbitrary
stroke lists — line, rect, circle, arrow, polyline — with an
explicit canvas, for template-style diagrams that need full
stroke control). Templates take parameters only — values,
counts, colours — and return strokes; they never take text.
"""

from __future__ import annotations

import math
import struct
import subprocess
import zlib

try:
    from PIL import Image as _PIL_IMAGE
except Exception:
    _PIL_IMAGE = None

CHALK_WHITE = (246, 246, 238)
CHALK_YELLOW = (250, 204, 21)
NAMED_COLORS = {
    "white": CHALK_WHITE, "yellow": CHALK_YELLOW,
    "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 255, 0),
    "blue": (0, 0, 255), "cyan": (0, 255, 255),
    "magenta": (255, 0, 255), "orange": (255, 165, 0),
    "purple": (128, 0, 128),
}


def resolve_color(color):
    """Colour name or #RRGGBB -> (r, g, b) tuple, or None."""
    if not isinstance(color, str):
        return None
    if color in NAMED_COLORS:
        return NAMED_COLORS[color]
    if len(color) == 7 and color.startswith("#"):
        try:
            return (int(color[1:3], 16), int(color[3:5], 16),
                    int(color[5:7], 16))
        except ValueError:
            return None
    return None


def _resample_walk(points, step):
    """Clean resample: points spaced ~step px along the polyline."""
    out = [points[0]]
    carry = 0.0
    px, py = points[0]
    for qx, qy in points[1:]:
        dx, dy = qx - px, qy - py
        seg = math.hypot(dx, dy)
        px, py = qx, qy
        if seg == 0:
            continue
        dist = carry + seg
        # Start of this segment in absolute walk coordinates.
        sx, sy = qx - dx, qy - dy
        while dist >= step:
            # Distance from the segment start to the next sample.
            back = dist - step
            t = (seg - back) / seg if seg else 0.0
            out.append((sx + dx * t, sy + dy * t))
            dist -= step
            # Next sample is measured from the just-emitted point.
            sx, sy = out[-1]
            dx, dy = qx - sx, qy - sy
            seg = math.hypot(dx, dy)
            if seg == 0:
                break
        carry = dist
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def _dense_line(a, b, step=1.0):
    """Dense points along a segment (for resampling)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(L / step))
    return [(a[0] + (b[0] - a[0]) * i / n,
             a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def stroke_to_points(stroke):
    """One stroke dict -> (points, color_rgb, width, alpha).

    Points are clean resampled vectors, sampled ~width/3 px
    apart (v1.45.0, R4a: no wobble, no grain). Arrowheads are
    part of the same stroke: the shaft points followed by the
    two barb point-lists (the jump back to the tip leaves no
    connecting line — the barbs meet the shaft exactly at the
    tip, and the SDF engine breaks segments at jumps).
    """
    kind = stroke.get("kind")
    width = float(stroke.get("width", 7))
    step = max(1.0, width / 3.0)
    if kind in ("polyline", "arrow", "line"):
        # "line" is a straight 2-point segment (validation
        # enforces exactly 2 points); it renders through the
        # same vector path as a polyline.
        raw = stroke["points"]
        dense = []
        for a, b in zip(raw, raw[1:]):
            seg = _dense_line((float(a[0]), float(a[1])),
                              (float(b[0]), float(b[1])))
            dense.extend(seg[:-1] if dense else seg)
        dense.append((float(raw[-1][0]), float(raw[-1][1])))
        points = _resample_walk(dense, step)
        if kind == "arrow":
            # Head barbs: two short segments back from the tip,
            # at ~145 degrees to the shaft direction.
            (x1, y1), (x0, y0) = points[-1], points[-2] if len(points) > 1 else points[-1]
            ang = math.atan2(y1 - y0, x1 - x0)
            head = float(stroke.get("head_size", 36))
            for sgn in (1, -1):
                ba = ang + sgn * 2.53
                barb = _dense_line((x1, y1),
                                   (x1 + head * math.cos(ba),
                                    y1 + head * math.sin(ba)))
                barb = _resample_walk(barb, step)
                points.extend(barb)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "circle":
        cx, cy = float(stroke["center"][0]), float(stroke["center"][1])
        r = float(stroke["radius"])
        n = max(64, int(2 * math.pi * r / step))
        dense = [(cx + r * math.cos(2 * math.pi * i / n),
                  cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        points = _resample_walk(dense, step)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "ellipse":
        cx, cy = float(stroke["center"][0]), float(stroke["center"][1])
        rx, ry = float(stroke["radius_x"]), float(stroke["radius_y"])
        per = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
        n = max(64, int(per / step))
        dense = [(cx + rx * math.cos(2 * math.pi * i / n),
                  cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        points = _resample_walk(dense, step)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "rect":
        x, y = float(stroke["x"]), float(stroke["y"])
        w, h = float(stroke["rect_width"]), float(stroke["rect_height"])
        cr = float(stroke.get("corner_radius", 0))
        if cr <= 0:
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
            dense = []
            for a, b in zip(corners, corners[1:]):
                seg = _dense_line(a, b)
                dense.extend(seg[:-1] if dense else seg)
            dense.append(corners[-1])
        else:
            cr = min(cr, w / 2, h / 2)
            dense = []
            # Rounded rectangle as arcs + straight runs.
            arcs = [
                ((x + w - cr, y + cr), -math.pi / 2, 0),
                ((x + w - cr, y + h - cr), 0, math.pi / 2),
                ((x + cr, y + h - cr), math.pi / 2, math.pi),
                ((x + cr, y + cr), math.pi, 3 * math.pi / 2),
            ]
            for (ccx, ccy), a0, a1 in arcs:
                m = max(8, int(abs(a1 - a0) * cr / step))
                for i in range(m + 1):
                    a = a0 + (a1 - a0) * i / m
                    dense.append((ccx + cr * math.cos(a), ccy + cr * math.sin(a)))
            # Close the perimeter along the top edge (the first
            # arc starts at the top edge's right end; without
            # this the top run would be missing).
            dense.append(dense[0])
            # Straight runs connect the arcs in order: the
            # polyline segments between consecutive arc ends
            # are exactly the four straight edges, and the
            # order top-right -> bottom-right -> bottom-left ->
            # top-left walks the perimeter clockwise.
        points = _resample_walk(dense, step)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "arc":
        # Arc: centre + radius + start/end angles in degrees.
        # 0 degrees is east; positive angles run clockwise on
        # screen (y grows downward), so 0 -> 90 draws the
        # bottom-right quadrant. A zero sweep (start == end)
        # means a full circle. Points follow the same clean
        # vector path (resample only) as every other stroke.
        cx, cy = float(stroke["center"][0]), float(stroke["center"][1])
        r = float(stroke["radius"])
        a0 = math.radians(float(stroke["start_angle"]))
        a1 = math.radians(float(stroke["end_angle"]))
        sweep = (a1 - a0) % (2 * math.pi)
        if sweep == 0:
            sweep = 2 * math.pi
        arc_len = r * sweep
        n = max(16, int(arc_len / step))
        dense = [(cx + r * math.cos(a0 + sweep * i / n),
                  cy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]
        points = _resample_walk(dense, step)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    raise ValueError(f"unknown stroke kind {kind!r}")


def stroke_weight(stroke, points):
    """Draw-window share of one stroke: explicit weight, else
    its path length (approximated by its sample count)."""
    w = stroke.get("weight")
    if w is not None:
        return float(w)
    return float(len(points))


class Canvas:
    """RGBA vector canvas: capsule SDF segments, max-alpha wins.

    ss is the buffer's supersample factor relative to output
    coordinates (1 = direct; 2 = the internal buffer of
    _ScaledCanvas). draw_segments divides the SDF ramp by ss so
    a supersampled edge downscales to the same 1-output-pixel
    ramp as the 1x engine, and scales the gap threshold by ss."""

    def __init__(self, width, height, ss=1):
        self.width = int(width)
        self.height = int(height)
        self.ss = int(ss)
        self.buf = bytearray(self.width * self.height * 4)

    def draw_segments(self, points, color, width, alpha):
        """Draw consecutive point pairs as capsule SDF segments
        (v1.41.0, Fix 3; the only line engine since v1.45.0, R4a
        — the stamped discs, their chalk grain and the wobble
        are deleted). For a segment P1-P2, per-pixel
        point-to-segment distance d gives coverage
        alpha * clamp(0.5 + (r - d) / ss, 0, 1): continuous
        lines with no bead gaps and no staircasing. Segments
        break at jumps over 15 output pixels (arrow-barb jumps
        of head_size 30-40px), so barbs never draw connecting
        lines. Points are clean resampled vectors — no wobble,
        no grain. Max-alpha wins, so re-drawing a segment is
        idempotent."""
        pts = [(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            return
        r = float(width) / 2.0
        ss = self.ss
        gap = 15.0 * ss
        buf = self.buf
        w = self.width
        h = self.height
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            dx = x2 - x1
            dy = y2 - y1
            seg_len2 = dx * dx + dy * dy
            if seg_len2 > gap * gap:
                continue
            pad = r + 1.0
            x0 = max(0, int(math.floor(min(x1, x2) - pad)))
            x1i = min(w - 1, int(math.ceil(max(x1, x2) + pad)))
            y0 = max(0, int(math.floor(min(y1, y2) - pad)))
            y1i = min(h - 1, int(math.ceil(max(y1, y2) + pad)))
            if x1i < x0 or y1i < y0:
                continue
            for yy in range(y0, y1i + 1):
                for xx in range(x0, x1i + 1):
                    px = xx + 0.5
                    py = yy + 0.5
                    if seg_len2 == 0:
                        d = math.hypot(px - x1, py - y1)
                    else:
                        t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
                        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                        d = math.hypot(px - (x1 + t * dx),
                                       py - (y1 + t * dy))
                    # Clamp the COVERAGE first, then scale by
                    # the stroke alpha (interior pixels land at
                    # exactly alpha).
                    cov = 0.5 + (r - d) / ss
                    if cov <= 0.0:
                        continue
                    if cov > 1.0:
                        cov = 1.0
                    v = alpha * cov
                    idx = (yy * w + xx) * 4
                    old_a = buf[idx + 3] / 255.0
                    if v > old_a:
                        buf[idx] = color[0]
                        buf[idx + 1] = color[1]
                        buf[idx + 2] = color[2]
                        buf[idx + 3] = int(round(v * 255))

    def bytes(self):
        return bytes(self.buf)

    def opaque_pixel_count(self):
        """Pixels with any ink on them (alpha > 0)."""
        return sum(1 for i in range(3, len(self.buf), 4) if self.buf[i] > 0)


class _ScaledCanvas:
    """2x supersampled canvas (v1.41.0, Fix 3, PIL auto-path).

    Draws into a 2x Canvas (same SDF engine, ss=2 so the edge
    ramp stays 1 output pixel wide) and downscales with PIL
    LANCZOS on bytes(). Only built when PIL is importable;
    otherwise _new_canvas returns a plain Canvas (stdlib path).
    No new REQUIRED dependency — the stdlib-only default stands
    wherever PIL is absent."""

    def __init__(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self._inner = Canvas(2 * self.width, 2 * self.height, ss=2)

    def draw_segments(self, points, color, width, alpha):
        pts = [(float(x) * 2.0, float(y) * 2.0) for x, y in points]
        self._inner.draw_segments(pts, color, float(width) * 2.0,
                                  alpha)

    def bytes(self):
        raw = self._inner.bytes()
        img = _PIL_IMAGE.frombytes(
            "RGBA", (2 * self.width, 2 * self.height), raw)
        return img.resize((self.width, self.height),
                          _PIL_IMAGE.LANCZOS).tobytes()

    def opaque_pixel_count(self):
        b = self.bytes()
        return sum(1 for i in range(3, len(b), 4) if b[i] > 0)


def _new_canvas(width, height):
    """PIL-aware diagram canvas: 2x supersampled when PIL is
    importable, else the stdlib 1x Canvas. Both paths preserve
    the animator-last-frame == direct-render property (each
    path self-consistent)."""
    if _PIL_IMAGE is not None:
        return _ScaledCanvas(width, height)
    return Canvas(width, height)


def _prepare_strokes(strokes):
    """Stroke dicts -> list of (points, color, width, alpha, weight).

    v1.45.0, R4a: the seed is gone with the wobble/grain — the
    tuple no longer carries one."""
    prepared = []
    for i, st in enumerate(strokes):
        points, color, width, alpha = stroke_to_points(st)
        prepared.append((points, color, width, alpha,
                         stroke_weight(st, points)))
    return prepared


def render_diagram_rgba(strokes, width, height, progress=1.0):
    """RGBA bytes of the diagram at draw progress 0..1.

    Progress maps onto strokes in order by weight: a stroke
    before the progress frontier is complete, the frontier
    stroke is partial, later strokes are absent. At progress
    1.0 this is exactly the complete diagram, and the
    animator's last frame is byte-identical to it (same
    segments, same order, same canvas path).
    """
    prepared = _prepare_strokes(strokes)
    total = sum(p[4] for p in prepared)
    target = max(0.0, min(1.0, float(progress))) * total
    canvas = _new_canvas(width, height)
    acc = 0.0
    for points, color, wdt, alpha, weight in prepared:
        if target <= acc:
            break
        take = target - acc
        n = len(points) if take >= weight else max(0, int(round(len(points) * take / weight))) if weight > 0 else 0
        canvas.draw_segments(points[:n], color, wdt, alpha)
        acc += weight
    return canvas.bytes()


def write_png_rgba(path, width, height, rgba):
    """Write RGBA bytes as a PNG (stdlib only)."""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw)))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def rounded_card_rgba(width, height, color, opacity):
    """RGBA bytes for a rounded translucent rectangle (v1.41.0,
    Fix 6): the static backdrop card composited UNDER a diagram
    for its whole window. Corner radius is 4% of the short side
    (min 8px); the edge falls off over 2px with a smoothstep, the
    same soft edge the stroke rasterizer uses, so card corners
    never show aliasing steps. color is an (r, g, b) tuple,
    opacity a 0..1 float; interior alpha is opacity x 255."""
    w, h = int(width), int(height)
    r = max(8, int(round(0.04 * min(w, h))))
    cx, cy = w / 2.0, h / 2.0
    hx, hy = w / 2.0 - r, h / 2.0 - r
    cr, cg, cb = (int(color[0]), int(color[1]), int(color[2]))
    a_full = max(0.0, min(1.0, float(opacity))) * 255.0
    out = bytearray(w * h * 4)
    for y in range(h):
        qy = abs(y + 0.5 - cy) - hy
        row = y * w * 4
        for x in range(w):
            qx = abs(x + 0.5 - cx) - hx
            ax, ay = (qx if qx > 0 else 0.0), (qy if qy > 0 else 0.0)
            d = math.sqrt(ax * ax + ay * ay) + min(max(qx, qy), 0.0) - r
            cov = 0.5 - d / 2.0
            if cov <= 0.0:
                continue
            if cov > 1.0:
                cov = 1.0
            o = row + x * 4
            out[o] = cr
            out[o + 1] = cg
            out[o + 2] = cb
            out[o + 3] = int(round(a_full * cov))
    return bytes(out)


def write_dual_tone_canvas(path, width, height):
    """Write a generated dual-tone canvas background (v1.41.0,
    Fix 6): a vertical gradient from deep slate at the top to a
    lighter slate-teal at the bottom. Used when an explainer
    brief sets background to the sentinel "canvas": a quiet,
    detailed-free backdrop that still reads as a designed
    surface (unlike a flat colour), with the look's vignette
    treatment supplying the radial falloff at render time."""
    w, h = int(width), int(height)
    top = (22, 32, 46)
    bottom = (36, 56, 74)
    rgba = bytearray(w * h * 4)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(round(top[0] + (bottom[0] - top[0]) * t))
        g = int(round(top[1] + (bottom[1] - top[1]) * t))
        b = int(round(top[2] + (bottom[2] - top[2]) * t))
        row = y * w * 4
        for x in range(w):
            o = row + x * 4
            rgba[o] = r
            rgba[o + 1] = g
            rgba[o + 2] = b
            rgba[o + 3] = 255
    return write_png_rgba(path, w, h, bytes(rgba))


class DiagramAnimator:
    """Incremental draw-on animator: each next_frame() draws
    only the newly revealed segments, so total raster work is
    one complete diagram, not one per frame. Stroke progress
    eases (smoothstep) so the pen accelerates and slows like a
    hand; the final frame is exact and byte-identical to
    render_diagram_rgba(..., 1.0). Incremental slices include
    the bridging segment (points[drawn-1:want]) because a
    segment spanning the slice boundary belongs to neither
    slice alone; re-drawing it is idempotent (max-alpha)."""

    def __init__(self, strokes, width, height, fps, draw_seconds):
        self.width = int(width)
        self.height = int(height)
        self.prepared = _prepare_strokes(strokes)
        self.total_weight = sum(p[4] for p in self.prepared)
        self.frames = max(1, int(round(float(draw_seconds) * float(fps))))
        self.canvas = _new_canvas(self.width, self.height)
        self._frame = 0
        # Per-stroke drawn point counts, advanced monotonically.
        self._drawn = [0] * len(self.prepared)
        # Cumulative weight offsets per stroke.
        self._offsets = []
        acc = 0.0
        for p in self.prepared:
            self._offsets.append(acc)
            acc += p[4]

    def next_frame(self):
        """Next frame bytes, or None when the animation is done."""
        if self._frame >= self.frames:
            return None
        self._frame += 1
        if self._frame == self.frames:
            target = self.total_weight
        else:
            # Delta: hand-like pacing — stroke progress follows a
            # smoothstep ease_in_out over the draw window, so the
            # pen accelerates gently and slows at stroke ends
            # instead of moving at a mechanical constant speed.
            # The final frame is exact (ease(1) = 1), so it stays
            # byte-identical to a direct full render.
            u = self._frame / self.frames
            target = self.total_weight * u * u * (3 - 2 * u)
        for i, (points, color, wdt, alpha, weight) in enumerate(self.prepared):
            start = self._offsets[i]
            if target <= start:
                continue
            take = target - start
            want = len(points) if take >= weight else (max(0, int(round(len(points) * take / weight))) if weight > 0 else 0)
            if want > self._drawn[i]:
                _lo = self._drawn[i] - 1 if self._drawn[i] > 0 else 0
                self.canvas.draw_segments(points[_lo:want], color, wdt,
                                          alpha)
                self._drawn[i] = want
        return self.canvas.bytes()

    def frame_count(self):
        return self.frames


def encode_diagram_video(diagram, fps, out_path):
    """Encode one diagram's draw animation to a transparent
    video (PNG codec, RGBA) at out_path. diagram is a resolved
    diagram dict (see validate.py): canvas_w/canvas_h, strokes,
    draw seconds. Raises RuntimeError on FFmpeg failure."""
    width, height = int(diagram["canvas_w"]), int(diagram["canvas_h"])
    animator = DiagramAnimator(diagram["strokes"], width, height,
                               fps, float(diagram["draw"]))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo",
           "-pix_fmt", "rgba", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-", "-an", "-c:v", "png", out_path]
    # stderr goes to a sidecar file, not a pipe: the frames are
    # written by hand to stdin, so communicate() (which would
    # flush an already-closed stdin and raise "flush of closed
    # file") must NOT be used here — write, close, wait, then
    # read the sidecar.
    import os
    err_path = out_path + ".stderr"
    with open(err_path, "wb") as err_fh:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=err_fh)
        try:
            while True:
                frame = animator.next_frame()
                if frame is None:
                    break
                proc.stdin.write(frame)
            proc.stdin.close()
            proc.wait(timeout=300)
        except Exception:
            proc.kill()
            raise
    err_text = ""
    try:
        with open(err_path, "rb") as fh:
            err_text = fh.read().decode(errors="replace")
    except OSError:
        pass
    try:
        os.remove(err_path)
    except OSError:
        pass
    if proc.returncode != 0:
        raise RuntimeError(f"diagram video encode failed for '{diagram.get('id')}': {err_text[-500:]}")
    return out_path


# ------------------------------------------------------------------
# Built-in templates (v1.45.0, R4b-3). Only `primitives` still
# builds strokes: it is the custom-stroke escape hatch (clean
# vectors, no chalk). The four legacy templates (chart, basket,
# balance, causes) are MOTION ADAPTERS now — they never build
# strokes. Their names stay in TEMPLATES so old projects still
# validate, but build_template raises for them and validate.py
# builds a motion element (line_chart / bars / comparison_cards)
# at the diagram's display rect instead. See _validate_diagrams.
# ------------------------------------------------------------------


def template_primitives(params):
    """General primitives template: arbitrary stroke lists
    (line, rect, circle, arrow, polyline) with an explicit canvas.

    params: {"strokes": [stroke dicts], "canvas": {"width", "height"}
    or [width, height]}. Canvas defaults to 900x560 and must be
    100..1920 per side (same bounds as custom diagrams).
    Strokes pass through unchanged here; validation in
    validate.py checks them against the canvas before they
    reach the renderer, exactly like custom strokes."""
    canvas = params.get("canvas") or {}
    if isinstance(canvas, (list, tuple)) and len(canvas) == 2:
        try:
            cw, ch = int(canvas[0]), int(canvas[1])
        except (TypeError, ValueError):
            raise ValueError("primitives canvas must be [width, height] numbers")
    elif isinstance(canvas, dict):
        try:
            cw = int(canvas.get("width", 900))
            ch = int(canvas.get("height", 560))
        except (TypeError, ValueError):
            raise ValueError("primitives canvas width/height must be integers")
    else:
        raise ValueError("primitives canvas must be [width, height] or {width, height}")
    if not 100 <= cw <= 1920 or not 100 <= ch <= 1920:
        raise ValueError("primitives canvas must be 100..1920 per side")
    strokes = params.get("strokes")
    if not isinstance(strokes, list) or not strokes:
        raise ValueError("primitives needs a non-empty strokes list")
    return cw, ch, list(strokes)


def template_timeline(params):
    """Timeline template (Phase 2): animated chronological roadmap/milestones diagram.

    params:
      steps: int 2..6 (default 3)
      direction: 'horizontal' (default) or 'vertical'
      color: stroke color (default 'white')
      accent_color: milestone accent color (default 'yellow')
      arrow: bool (default True)
    """
    params = params or {}
    try:
        steps = int(params.get("steps", 3))
    except (TypeError, ValueError):
        raise ValueError("timeline steps must be an integer")
    if not 2 <= steps <= 6:
        raise ValueError("timeline steps must be between 2 and 6")

    direction = str(params.get("direction", "horizontal")).lower()
    if direction not in ("horizontal", "vertical"):
        raise ValueError("timeline direction must be 'horizontal' or 'vertical'")

    color = params.get("color", "white")
    accent = params.get("accent_color", "yellow")
    has_arrow = bool(params.get("arrow", True))

    strokes = []
    if direction == "horizontal":
        cw, ch = 900, 560
        y_axis = 280
        start_x, end_x = 80, 820
        if has_arrow:
            strokes.append({
                "kind": "arrow",
                "points": [[start_x, y_axis], [end_x, y_axis]],
                "color": color,
                "width": 5,
                "head_size": 24,
            })
        else:
            strokes.append({
                "kind": "line",
                "points": [[start_x, y_axis], [end_x, y_axis]],
                "color": color,
                "width": 5,
            })

        span = 660
        step_delta = span / (steps - 1) if steps > 1 else span
        for i in range(steps):
            cx = int(round(120 + i * step_delta))
            strokes.append({
                "kind": "circle",
                "center": [cx, y_axis],
                "radius": 16,
                "color": accent,
                "width": 4,
            })
            strokes.append({
                "kind": "circle",
                "center": [cx, y_axis],
                "radius": 6,
                "color": color,
                "width": 3,
            })
            if i % 2 == 0:
                strokes.append({
                    "kind": "line",
                    "points": [[cx, y_axis - 20], [cx, y_axis - 75]],
                    "color": color,
                    "width": 3,
                })
                strokes.append({
                    "kind": "rect",
                    "x": cx - 55,
                    "y": y_axis - 145,
                    "rect_width": 110,
                    "rect_height": 65,
                    "corner_radius": 10,
                    "color": color,
                    "width": 3,
                })
            else:
                strokes.append({
                    "kind": "line",
                    "points": [[cx, y_axis + 20], [cx, y_axis + 75]],
                    "color": color,
                    "width": 3,
                })
                strokes.append({
                    "kind": "rect",
                    "x": cx - 55,
                    "y": y_axis + 80,
                    "rect_width": 110,
                    "rect_height": 65,
                    "corner_radius": 10,
                    "color": color,
                    "width": 3,
                })
    else:  # vertical
        cw, ch = 600, 800
        x_axis = 300
        start_y, end_y = 80, 720
        if has_arrow:
            strokes.append({
                "kind": "arrow",
                "points": [[x_axis, start_y], [x_axis, end_y]],
                "color": color,
                "width": 5,
                "head_size": 24,
            })
        else:
            strokes.append({
                "kind": "line",
                "points": [[x_axis, start_y], [x_axis, end_y]],
                "color": color,
                "width": 5,
            })

        span = 560
        step_delta = span / (steps - 1) if steps > 1 else span
        for i in range(steps):
            cy = int(round(120 + i * step_delta))
            strokes.append({
                "kind": "circle",
                "center": [x_axis, cy],
                "radius": 16,
                "color": accent,
                "width": 4,
            })
            strokes.append({
                "kind": "circle",
                "center": [x_axis, cy],
                "radius": 6,
                "color": color,
                "width": 3,
            })
            if i % 2 == 0:
                strokes.append({
                    "kind": "line",
                    "points": [[x_axis - 20, cy], [x_axis - 65, cy]],
                    "color": color,
                    "width": 3,
                })
                strokes.append({
                    "kind": "rect",
                    "x": x_axis - 190,
                    "y": cy - 35,
                    "rect_width": 120,
                    "rect_height": 70,
                    "corner_radius": 10,
                    "color": color,
                    "width": 3,
                })
            else:
                strokes.append({
                    "kind": "line",
                    "points": [[x_axis + 20, cy], [x_axis + 65, cy]],
                    "color": color,
                    "width": 3,
                })
                strokes.append({
                    "kind": "rect",
                    "x": x_axis + 70,
                    "y": cy - 35,
                    "rect_width": 120,
                    "rect_height": 70,
                    "corner_radius": 10,
                    "color": color,
                    "width": 3,
                })

    return cw, ch, strokes


def template_process_flow(params):
    """Process flow template (Phase 2): animated sequential workflow steps.

    params:
      steps: int 2..5 (default 3)
      color: stroke color (default 'white')
      accent_color: arrow/badge color (default 'yellow')
    """
    params = params or {}
    try:
        steps = int(params.get("steps", 3))
    except (TypeError, ValueError):
        raise ValueError("process_flow steps must be an integer")
    if not 2 <= steps <= 5:
        raise ValueError("process_flow steps must be between 2 and 5")

    color = params.get("color", "white")
    accent = params.get("accent_color", "yellow")

    cw, ch = 900, 500
    card_h = 130
    y_card = (ch - card_h) // 2

    total_w = 760
    gap = 40
    card_w = int((total_w - (steps - 1) * gap) / steps)
    start_x = (cw - total_w) // 2

    strokes = []
    for i in range(steps):
        x = start_x + i * (card_w + gap)
        strokes.append({
            "kind": "rect",
            "x": x,
            "y": y_card,
            "rect_width": card_w,
            "rect_height": card_h,
            "corner_radius": 12,
            "color": color,
            "width": 4,
        })
        strokes.append({
            "kind": "circle",
            "center": [x + 32, y_card + 32],
            "radius": 14,
            "color": accent,
            "width": 3,
        })
        if i < steps - 1:
            arr_start_x = x + card_w + 6
            arr_end_x = arr_start_x + gap - 12
            y_mid = y_card + card_h // 2
            strokes.append({
                "kind": "arrow",
                "points": [[arr_start_x, y_mid], [arr_end_x, y_mid]],
                "color": accent,
                "width": 4,
                "head_size": 18,
            })

    return cw, ch, strokes


# Legacy templates that are motion adapters now (v1.45.0,
# R4b-3): their names stay in TEMPLATES so old projects still
# validate, but they build no strokes — validate.py adapts them
# to motion elements (see _validate_diagrams). Only `primitives`
# still maps to a stroke builder.
ADAPTER_TEMPLATES = {"chart", "basket", "balance", "causes", "bars"}

TEMPLATES = {
    "chart": None,
    "basket": None,
    "balance": None,
    "causes": None,
    "bars": None,
    "primitives": template_primitives,
    "timeline": template_timeline,
    "process_flow": template_process_flow,
}


def build_template(name, params):
    """(canvas_w, canvas_h, strokes) for a template name + params.

    Raises ValueError naming the problem for unknown templates
    or bad parameters; validation in validate.py turns these
    into structured errors with fix hints. The four legacy
    templates (chart, basket, balance, causes) are motion
    adapters and raise here — they render as motion elements,
    never as strokes (see ADAPTER_TEMPLATES).
    """
    if name in ADAPTER_TEMPLATES:
        raise ValueError(
            f"template '{name}' is a motion adapter in 1.45.0: it "
            "renders as a motion element (line_chart / bars / "
            "comparison_cards), not as strokes — the diagram "
            "validator builds it")
    fn = TEMPLATES.get(name)
    if fn is None:
        try:
            from .plugins import get_diagram_plugin
            fn = get_diagram_plugin(name)
        except Exception:
            fn = None
    if fn is None:
        raise ValueError(f"unknown diagram template '{name}'")
    return fn(params or {})
