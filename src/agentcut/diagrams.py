"""Animated stroke diagrams (v1.38.0): a general, reusable element.

A project may carry a ``diagrams`` list. Each diagram is either
custom strokes supplied by the agent, or one of the built-in
parameterized templates below. AgentCut renders the diagram as a
progressive draw-on animation: strokes appear one after another,
in list order, over the diagram's draw window, and the final
frame is exactly the complete diagram (the animation is
monotonic — nothing ever disappears — so the held final frame
after the draw window is pixel-identical to the complete art).

Rendering is stdlib + FFmpeg only, like the rest of AgentCut.
The diagram is drawn into RGBA frames with a small stdlib PNG
writer's sibling rasterizer (no PIL, no new dependencies):
chalk strokes are stamped discs along a sampled path, with
slightly irregular edges and a deterministic grain. The
irregularity is baked into the path ONCE (a static function of
position along the stroke), so every frame of the animation
shows the same stroke shape — there is deliberately NO animated
jitter, which would read as vibration. Frames are piped as
rawvideo into FFmpeg and encoded to a temporary transparent
(PNG codec, RGBA) video; the main render composites that video
with the normal overlay filter and loops its last frames for
the hold after the draw window ends.

Artwork rules (serious, not childish): clean geometry, correct
proportions, one consistent chalk weight per stroke, subtle
chalk texture only, and two chalk colours — white for the
drawing, yellow for emphasis only. Labels are NEVER baked into
a diagram: templates take no text, and every label on screen
is an AgentCut text (drawtext), so Arabic labels keep the
BiDi-verified path and never become AI-generated image text.

Stroke schema (diagram-local coordinates, origin top-left):
  {"kind": "polyline", "points": [[x, y], ...], ...}
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
length, so longer strokes naturally take longer to draw),
"seed" (integer; defaults to the stroke's index).

Templates (see build_template): "chart" (rising chart with
axes, faint grid and an arrowed curve), "basket" (basket with
coins), "balance" (beam scale, tilt from two values),
"causes" (rows of badge + arrow). Templates take parameters
only — values, counts, colours — and return strokes; they
never take text.
"""

from __future__ import annotations

import math
import struct
import subprocess
import zlib

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


def _hash01(x, y, seed):
    """Deterministic grain in [0, 1) from integer coords + seed."""
    h = (x * 374761393 + y * 668265263 + seed * 974634211) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 0xFFFF


def _wobble(u, seed, amplitude):
    """Static edge irregularity at fraction u along a stroke.

    Integer frequencies (3 and 7 cycles over the stroke) so a
    closed shape joins seamlessly at u=0/1, and phases derived
    from the seed so every stroke wobbles differently. This is
    a function of position only — never of time — so the
    stroke shape is identical in every animation frame.
    """
    ph1 = (seed * 0.731) % (2 * math.pi)
    ph2 = (seed * 1.937 + 1.3) % (2 * math.pi)
    return amplitude * (0.65 * math.sin(2 * math.pi * 3 * u + ph1)
                        + 0.35 * math.sin(2 * math.pi * 7 * u + ph2))


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


def _apply_wobble(points, width, seed):
    """Offset sampled points perpendicular to the path by the
    static wobble, and return the wobbled point list."""
    if len(points) < 2:
        return list(points)
    total = 0.0
    seg_lens = []
    for a, b in zip(points, points[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        seg_lens.append(L)
        total += L
    if total == 0:
        return list(points)
    amp = min(1.2, max(0.6, width * 0.10))
    out = []
    walked = 0.0
    for i, pt in enumerate(points):
        if i > 0:
            walked += seg_lens[i - 1]
        u = walked / total
        # Perpendicular from the local direction.
        if i < len(points) - 1:
            dx, dy = points[i + 1][0] - pt[0], points[i + 1][1] - pt[1]
        else:
            dx, dy = pt[0] - points[i - 1][0], pt[1] - points[i - 1][1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        off = _wobble(u, seed, amp)
        out.append((pt[0] + nx * off, pt[1] + ny * off))
    return out


def _dense_line(a, b, step=1.0):
    """Dense points along a segment (for resampling)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(L / step))
    return [(a[0] + (b[0] - a[0]) * i / n,
             a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def stroke_to_points(stroke):
    """One stroke dict -> (points, color_rgb, width, alpha).

    Points are sampled ~width/3 px apart with the static wobble
    baked in. Arrowheads are part of the same stroke: the shaft
    points followed by the two barb point-lists (stamping discs
    only, so the jump back to the tip leaves no connecting
    line — the barbs meet the shaft exactly at the tip).
    """
    kind = stroke.get("kind")
    width = float(stroke.get("width", 7))
    step = max(1.0, width / 3.0)
    seed = int(stroke.get("seed", 0))
    if kind in ("polyline", "arrow"):
        raw = stroke["points"]
        dense = []
        for a, b in zip(raw, raw[1:]):
            seg = _dense_line((float(a[0]), float(a[1])),
                              (float(b[0]), float(b[1])))
            dense.extend(seg[:-1] if dense else seg)
        dense.append((float(raw[-1][0]), float(raw[-1][1])))
        points = _resample_walk(dense, step)
        points = _apply_wobble(points, width, seed)
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
                barb = _apply_wobble(barb, width, seed + 7)
                points.extend(barb)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "circle":
        cx, cy = float(stroke["center"][0]), float(stroke["center"][1])
        r = float(stroke["radius"])
        n = max(64, int(2 * math.pi * r / step))
        dense = [(cx + r * math.cos(2 * math.pi * i / n),
                  cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        points = _resample_walk(dense, step)
        points = _apply_wobble(points, width, seed)
        return points, resolve_color(stroke.get("color", "white")), width, float(stroke.get("alpha", 0.95))
    if kind == "ellipse":
        cx, cy = float(stroke["center"][0]), float(stroke["center"][1])
        rx, ry = float(stroke["radius_x"]), float(stroke["radius_y"])
        per = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
        n = max(64, int(per / step))
        dense = [(cx + rx * math.cos(2 * math.pi * i / n),
                  cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        points = _resample_walk(dense, step)
        points = _apply_wobble(points, width, seed)
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
        points = _apply_wobble(points, width, seed)
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
    """RGBA chalk canvas: stamped discs, max-alpha wins."""

    def __init__(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self.buf = bytearray(self.width * self.height * 4)

    def stamp(self, x, y, radius, color, alpha, seed):
        r = radius
        x0 = max(0, int(math.floor(x - r - 1)))
        x1 = min(self.width - 1, int(math.ceil(x + r + 1)))
        y0 = max(0, int(math.floor(y - r - 1)))
        y1 = min(self.height - 1, int(math.ceil(y + r + 1)))
        for yy in range(y0, y1 + 1):
            for xx in range(x0, x1 + 1):
                d = math.hypot(xx - x, yy - y)
                if d > r + 1:
                    continue
                edge = 1.0 if d <= r - 1 else max(0.0, (r + 1 - d) / 2)
                grain = 0.80 + 0.20 * _hash01(xx, yy, seed)
                v = alpha * edge * grain
                idx = (yy * self.width + xx) * 4
                old_a = self.buf[idx + 3] / 255.0
                if v > old_a:
                    self.buf[idx] = color[0]
                    self.buf[idx + 1] = color[1]
                    self.buf[idx + 2] = color[2]
                    self.buf[idx + 3] = int(round(v * 255))

    def draw_points(self, points, color, width, alpha, seed):
        radius = width / 2.0
        for (x, y) in points:
            self.stamp(x, y, radius, color, alpha, seed)

    def bytes(self):
        return bytes(self.buf)

    def opaque_pixel_count(self):
        """Pixels with any chalk on them (alpha > 0)."""
        return sum(1 for i in range(3, len(self.buf), 4) if self.buf[i] > 0)


def _prepare_strokes(strokes):
    """Stroke dicts -> list of (points, color, width, alpha, seed, weight)."""
    prepared = []
    for i, st in enumerate(strokes):
        st = dict(st)
        st.setdefault("seed", i)
        points, color, width, alpha = stroke_to_points(st)
        prepared.append((points, color, width, alpha, int(st["seed"]),
                         stroke_weight(st, points)))
    return prepared


def render_diagram_rgba(strokes, width, height, progress=1.0):
    """RGBA bytes of the diagram at draw progress 0..1.

    Progress maps onto strokes in order by weight: a stroke
    before the progress frontier is complete, the frontier
    stroke is partial, later strokes are absent. At progress
    1.0 this is exactly the complete diagram, and the
    animator's last frame is byte-identical to it (same
    stamps, same order).
    """
    prepared = _prepare_strokes(strokes)
    total = sum(p[5] for p in prepared)
    target = max(0.0, min(1.0, float(progress))) * total
    canvas = Canvas(width, height)
    acc = 0.0
    for points, color, wdt, alpha, seed, weight in prepared:
        if target <= acc:
            break
        take = target - acc
        n = len(points) if take >= weight else max(0, int(round(len(points) * take / weight))) if weight > 0 else 0
        canvas.draw_points(points[:n], color, wdt, alpha, seed)
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


class DiagramAnimator:
    """Incremental draw-on animator: each next_frame() stamps
    only the newly revealed points, so total raster work is one
    complete diagram, not one per frame. The final frame is
    byte-identical to render_diagram_rgba(..., 1.0)."""

    def __init__(self, strokes, width, height, fps, draw_seconds):
        self.width = int(width)
        self.height = int(height)
        self.prepared = _prepare_strokes(strokes)
        self.total_weight = sum(p[5] for p in self.prepared)
        self.frames = max(1, int(round(float(draw_seconds) * float(fps))))
        self.canvas = Canvas(self.width, self.height)
        self._frame = 0
        # Per-stroke drawn point counts, advanced monotonically.
        self._drawn = [0] * len(self.prepared)
        # Cumulative weight offsets per stroke.
        self._offsets = []
        acc = 0.0
        for p in self.prepared:
            self._offsets.append(acc)
            acc += p[5]

    def next_frame(self):
        """Next frame bytes, or None when the animation is done."""
        if self._frame >= self.frames:
            return None
        self._frame += 1
        if self._frame == self.frames:
            target = self.total_weight
        else:
            target = self.total_weight * self._frame / self.frames
        for i, (points, color, wdt, alpha, seed, weight) in enumerate(self.prepared):
            start = self._offsets[i]
            if target <= start:
                continue
            take = target - start
            want = len(points) if take >= weight else (max(0, int(round(len(points) * take / weight))) if weight > 0 else 0)
            if want > self._drawn[i]:
                self.canvas.draw_points(points[self._drawn[i]:want], color, wdt, alpha, seed)
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
# Built-in templates. Each returns (canvas_width, canvas_height,
# strokes). Geometry is fixed and proportional; parameters only
# change values/counts. No text is ever baked in — labels are
# AgentCut texts supplied by the calling project.
# ------------------------------------------------------------------

def _line(points, **kw):
    d = {"kind": "polyline", "points": points}
    d.update(kw)
    return d


def _arrow(points, **kw):
    d = {"kind": "arrow", "points": points}
    d.update(kw)
    return d


def _circle(center, radius, **kw):
    d = {"kind": "circle", "center": center, "radius": radius}
    d.update(kw)
    return d


def template_chart(params):
    """Rising chart: axes with arrowheads, faint grid, and an
    arrowed curve through `values` (2..8 numbers). Canvas
    900x560; plot area x 140..830, y 60..470."""
    values = params["values"]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    lo -= span * 0.10
    hi += span * 0.10
    span = hi - lo
    n = len(values)
    xs = [140 + (830 - 140) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    ys = [470 - (470 - 60) * (v - lo) / span for v in values]
    strokes = []
    # Hand order: axes first, then the faint grid, then the
    # curve. Grid lines are long, so without an explicit small
    # weight they would eat most of the draw window by
    # length-weighting (verified: grid-first took ~70% of the
    # window and the curve only appeared at the very end).
    strokes.append(_arrow([(90, 500), (90, 40)], color="white", width=7, head_size=30))
    strokes.append(_arrow([(90, 500), (850, 500)], color="white", width=7, head_size=30))
    for gy in (160, 260, 360):
        strokes.append(_line([(140, gy), (830, gy)], color="white", width=3, alpha=0.35, weight=50))
    # The curve itself (white), with its tip re-drawn in
    # yellow as a short arrow stroke over the last segment:
    # yellow is emphasis only, and the tip is the emphasis.
    pts = [[round(x), round(y)] for x, y in zip(xs, ys)]
    strokes.append(_line(pts, color="white", width=9))
    if len(pts) >= 2:
        strokes.append(_arrow(pts[-2:], color="yellow", width=9, head_size=40))
    return 900, 560, strokes


def template_basket(params):
    """Basket with `coins` (1..5) coins above the rim. Canvas
    700x560. Basket first, coins last, stacked in a pyramid."""
    coins = params.get("coins", 3)
    strokes = [
        _line([(140, 300), (560, 300)], color="white", width=8),
        _line([(140, 300), (235, 480)], color="white", width=8),
        _line([(560, 300), (465, 480)], color="white", width=8),
        _line([(235, 480), (465, 480)], color="white", width=8),
        _line([(250, 300), (290, 480)], color="white", width=5, alpha=0.85),
        _line([(450, 300), (410, 480)], color="white", width=5, alpha=0.85),
    ]
    # Pyramid slots: bottom row of up to 3, then a top row.
    slots = []
    if coins <= 3:
        slots = [(350 + (i - (coins - 1) / 2) * 140, 235) for i in range(coins)]
    else:
        slots = [(280, 235), (420, 235), (350, 235)]
        extra = coins - 3
        slots += [(350 + (i - (extra - 1) / 2) * 140, 150) for i in range(extra)]
    for (cx, cy) in slots:
        strokes.append(_circle([cx, cy], 58, color="white", width=8))
        strokes.append(_circle([cx, cy], 30, color="white", width=5, alpha=0.85))
    return 700, 560, strokes


def template_balance(params):
    """Beam scale. `left`/`right` values tilt the beam (heavier
    side lower, max ~14 degrees); equal values give a level
    beam. Canvas 900x560."""
    left = float(params.get("left", 1))
    right = float(params.get("right", 1))
    mx = max(left, right, 1e-9)
    tilt_deg = max(-14.0, min(14.0, (right - left) / mx * 14.0))
    tilt = math.radians(tilt_deg)
    cx, pivot_y = 450, 210
    half = 300
    dx, dy = half * math.cos(tilt), half * math.sin(tilt)
    # Note: positive tilt (right heavier) lowers the right end,
    # so the beam's right endpoint uses +dy in canvas coords
    # (y grows downward) when right > left; the formula above
    # gives tilt > 0 for right > left, and sin(tilt) > 0 moves
    # the right end down. Correct as drawn.
    lx, ly = cx - dx, pivot_y - dy
    rx, ry = cx + dx, pivot_y + dy
    strokes = [
        _line([(450, 60), (450, 210)], color="white", width=8),   # post
        _line([(330, 500), (570, 500)], color="white", width=8),  # base
        _line([(390, 500), (450, 210)], color="white", width=5, alpha=0.85),
        _line([(510, 500), (450, 210)], color="white", width=5, alpha=0.85),
        _line([(round(lx), round(ly)), (round(rx), round(ry))], color="white", width=8),  # beam
        _line([(cx - 40, pivot_y + 30), (cx + 40, pivot_y + 30)], color="white", width=6),
        _line([(cx - 40, pivot_y + 30), (cx, pivot_y)], color="white", width=6),
        _line([(cx + 40, pivot_y + 30), (cx, pivot_y)], color="white", width=6),
    ]
    for (ex, ey) in ((lx, ly), (rx, ry)):
        # Hangers + pan arc (shallow curve under the beam end).
        strokes.append(_line([(round(ex - 45), round(ey + 20)), (round(ex - 45), round(ey + 110))], color="white", width=5))
        strokes.append(_line([(round(ex + 45), round(ey + 20)), (round(ex + 45), round(ey + 110))], color="white", width=5))
        arc = []
        for i in range(25):
            t = i / 24
            ax = ex - 55 + 110 * t
            ay = ey + 110 + 42 * math.sin(math.pi * t)
            arc.append([round(ax), round(ay)])
        strokes.append(_line(arc, color="white", width=7))
    return 900, 560, strokes


def template_causes(params):
    """Causes panel: `rows` (1..4) rows, each a badge circle
    with a small inner mark plus a right-pointing arrow.
    Canvas 900x460; rows are evenly spaced and identical in
    size, so the panel reads as one clean unit."""
    rows = params.get("rows", 3)
    strokes = []
    top, gap = 70, 110
    for i in range(rows):
        y = top + i * gap
        strokes.append(_circle([90, y], 34, color="white", width=7))
        if i % 3 == 0:
            # Rising tick inside the badge.
            strokes.append(_line([(72, y + 8), (86, y - 4), (108, y - 14)], color="yellow", width=6))
        elif i % 3 == 1:
            # Level bar inside the badge.
            strokes.append(_line([(68, y), (112, y)], color="yellow", width=6))
        else:
            # Three dots inside the badge.
            for (dx, dy) in ((-14, 0), (0, -10), (0, 10)):
                strokes.append(_circle([90 + dx, y + dy], 7, color="yellow", width=5))
        strokes.append(_arrow([(160, y), (800, y)], color="white", width=8, head_size=36))
    return 900, 460, strokes


TEMPLATES = {
    "chart": template_chart,
    "basket": template_basket,
    "balance": template_balance,
    "causes": template_causes,
}


def build_template(name, params):
    """(canvas_w, canvas_h, strokes) for a template name + params.

    Raises ValueError naming the problem for unknown templates
    or bad parameters; validation in validate.py turns these
    into structured errors with fix hints."""
    fn = TEMPLATES.get(name)
    if fn is None:
        raise ValueError(f"unknown diagram template '{name}'")
    return fn(params or {})
