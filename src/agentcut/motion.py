"""Motion graphics layer (v1.43.0): After Effects-style animated
vector elements.

AgentCut used to have motion but not motion graphics: camera moves,
kinetic text pops, and diagrams that draw in ~2s then freeze. This
module adds elements that stay ALIVE across their whole window:
shapes moving along keyframed paths, counting numbers, growing
bar charts, sweeping donuts, progressively drawn line charts, and
lower thirds that enter, hold and exit.

Rendering is stdlib + FFmpeg only, like the rest of AgentCut — no
new required dependency. Each element is rasterized to a
transparent RGBA video (PNG codec) over its own keyframe-union
bounding box at project fps, and the main graph overlays that
video above clips/images/diagrams and below texts/captions, so
captions stay readable. NO text is ever rasterized here: every
label, number and lower-third line goes through the existing
drawtext/ASS machinery (see render.py's derived motion texts),
so Arabic keeps the BiDi-verified path.

Rasterizer notes (why 1x, not supersampled): a full-frame RGBA
frame at 720x1280 is 3.7 MB; a 25s proof is 750 of them. 2x
supersampling would quadruple the bytes through the pipe for an
edge improvement nobody sees at video bitrates, so edges get
coverage-based anti-aliasing instead (1px feather on curves,
4x4 sub-sampling on polygon boundary pixels). Compositing is
straight-alpha source-over, deterministic: same spec, same bytes
(no time, no randomness, no dict-order dependence).

Design decisions (pinned here, see LOOP_LOG.md Round 0):
- `path` is NOT a separate kind: a polyline path IS a sequence of
  x/y keyframes (the author sets keyframe times for arc-length
  pacing). A separate kind would duplicate the shape machinery.
- Easing mirrors render.py `_interp_expr_eased`: a segment's
  easing comes from its END keyframe when non-linear, else its
  START keyframe, else linear. smoothstep is added.
- Keyframe times are ABSOLUTE timeline seconds, inside the
  element window. x/y are the shape CENTER. Rotation is degrees
  clockwise (screen coordinates, y down). Scale multiplies the
  geometry only — stroke width stays constant.
- The keyframe-union bbox may extend outside the frame by up to
  one bbox-size per side (slide-in/out from off-screen); further
  out, or fully outside, is a validation error.
"""

import math
import os
import subprocess

from .diagrams import resolve_color

EASINGS = ("linear", "smoothstep", "ease_in", "ease_out", "ease_in_out", "ease_out_back")

# Raster kinds: elements that produce a transparent video. Counter
# produces no video (it is a drawtext eif expression); labels and
# lower-third text are derived drawtext items in render.py.
RASTER_KINDS = ("shape", "bars", "donut", "pie", "line_chart",
                "lower_third")

MOTION_KINDS = RASTER_KINDS + ("counter",)


def apply_easing(name, u):
    """Easing curve f(u) for normalised segment time u in [0, 1].

    Same formulas as render.py `_easing_factor`, plus smoothstep
    (u^2 * (3 - 2u), the gentle default for motion graphics).
    All are monotonic, so an interpolated value stays between its
    keyframe endpoints and the keyframe-union bbox is exact.
    """
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    if name == "smoothstep":
        return u * u * (3.0 - 2.0 * u)
    if name == "ease_in":
        return u * u
    if name == "ease_out":
        return 1.0 - (1.0 - u) * (1.0 - u)
    if name == "ease_out_back":
        return 1.0 + 2.70158 * (u - 1.0) ** 3 + 1.70158 * (u - 1.0) ** 2
    if name == "ease_in_out":
        if u < 0.5:
            return 2.0 * u * u
        return 1.0 - 2.0 * (1.0 - u) * (1.0 - u)
    return u


def _field_points(keyframes, field, default):
    """(time, value, easing) points for one field, carried forward.

    A keyframe that omits the field does not move it; the field
    holds its previous value (or the element default before any
    keyframe sets it). The returned list always has at least one
    point, so interpolation never has an empty case.
    """
    pts = []
    cur = default
    for kf in keyframes:
        if field in kf:
            cur = float(kf[field])
            pts.append((float(kf["t"]), cur, kf.get("easing")))
    if not pts:
        t0 = float(keyframes[0]["t"]) if keyframes else 0.0
        pts.append((t0, float(default), None))
    return pts


def interp_field(points, t):
    """Interpolated value of one field at absolute time t."""
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        t0, v0, e0 = points[i]
        t1, v1, e1 = points[i + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            u = (t - t0) / (t1 - t0)
            easing = e1 if e1 and e1 != "linear" else (e0 or "linear")
            return v0 + (v1 - v0) * apply_easing(easing, u)
    return points[-1][1]


def shape_state(el, t):
    """Interpolated {x, y, scale, rotation, opacity} of a shape at t."""
    kfs = el["keyframes"]
    return {
        "x": interp_field(_field_points(kfs, "x", el.get("x", 0.0)), t),
        "y": interp_field(_field_points(kfs, "y", el.get("y", 0.0)), t),
        "scale": interp_field(
            _field_points(kfs, "scale", el.get("scale", 1.0)), t),
        "rotation": interp_field(
            _field_points(kfs, "rotation", el.get("rotation", 0.0)), t),
        "opacity": interp_field(
            _field_points(kfs, "opacity", el.get("opacity", 1.0)), t),
    }


def _rot(px, py, cos_t, sin_t):
    return (px * cos_t - py * sin_t, px * sin_t + py * cos_t)


def shape_vertices(el, state):
    """Polygon vertices of a shape element at `state`, absolute coords.

    Returns a list of (x, y) points (the outline, closed implicitly),
    or None for a circle (which is filled by the circle path, not a
    polygon). Rotation is about the shape center; scale multiplies
    the geometry. Local points (polygon/line kinds) are relative to
    the center.
    """
    kind = el["shape"]
    cx, cy = float(state["x"]), float(state["y"])
    s = float(state["scale"])
    th = math.radians(float(state["rotation"]))
    cos_t, sin_t = math.cos(th), math.sin(th)

    def place(px, py):
        rx, ry = _rot(px * s, py * s, cos_t, sin_t)
        return (cx + rx, cy + ry)

    if kind == "circle":
        return None
    if kind == "rect":
        w, h = float(el["w"]) / 2.0, float(el["h"]) / 2.0
        r = float(el.get("corner_radius") or 0.0)
        if r <= 0.0:
            return [place(-w, -h), place(w, -h), place(w, h), place(-w, h)]
        # Rounded rect: 6 arc points per corner (every 15 degrees).
        pts = []
        corners = [(w - r, h - r, 0.0), (-w + r, h - r, 90.0),
                   (-w + r, -h + r, 180.0), (w - r, -h + r, 270.0)]
        for ccx, ccy, a0 in corners:
            for k in range(7):
                a = math.radians(a0 + 15.0 * k)
                pts.append(place(ccx + r * math.cos(a),
                                 ccy + r * math.sin(a)))
        return pts
    if kind == "ellipse":
        rx, ry = float(el["w"]) / 2.0, float(el["h"]) / 2.0
        return [place(rx * math.cos(2.0 * math.pi * k / 48.0),
                      ry * math.sin(2.0 * math.pi * k / 48.0))
                for k in range(48)]
    if kind in ("line", "polygon"):
        pts = el.get("points") or []
        return [place(float(px), float(py)) for px, py in pts]
    return []


def shape_bbox(el):
    """Keyframe-union bbox (bx, by, bw, bh) in absolute frame coords.

    Sampled at every keyframe (interpolation is monotonic per
    segment, so extremes land on keyframes). Includes half the
    stroke width plus a 2px AA pad.
    """
    kfs = el["keyframes"]
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for kf in kfs:
        st = shape_state(el, float(kf["t"]))
        verts = shape_vertices(el, st)
        if el["shape"] == "circle":
            r = float(el["radius"]) * float(st["scale"])
            pts = [(st["x"] - r, st["y"] - r), (st["x"] + r, st["y"] + r)]
            for px, py in pts:
                min_x = min(min_x, px)
                max_x = max(max_x, px)
                min_y = min(min_y, py)
                max_y = max(max_y, py)
        else:
            for px, py in verts:
                min_x = min(min_x, px)
                max_x = max(max_x, px)
                min_y = min(min_y, py)
                max_y = max(max_y, py)
    pad = (float(el.get("stroke_width") or 0.0) / 2.0) + 2.0 \
        if el.get("stroke") else 2.0
    bx = int(math.floor(min_x - pad))
    by = int(math.floor(min_y - pad))
    bw = int(math.ceil(max_x + pad)) - bx
    bh = int(math.ceil(max_y + pad)) - by
    return (bx, by, max(1, bw), max(1, bh))


class MotionCanvas:
    """RGBA canvas with straight-alpha source-over compositing.

    Buffer layout is RGBA bytes, origin top-left. Fills use
    scanline spans: interior pixels are written by slice
    assignment when opaque, per-pixel blended otherwise; boundary
    pixels get coverage-based anti-aliasing (4x4 sub-sampling for
    polygons, a 1px analytic feather for circles).
    """

    def __init__(self, width, height):
        self.width = int(width)
        self.height = int(height)
        self.buf = bytearray(self.width * self.height * 4)

    def _blend_pixel(self, idx, r, g, b, a):
        buf = self.buf
        da = buf[idx + 3] / 255.0
        if da <= 0.0:
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
            buf[idx + 3] = int(round(a * 255.0))
            return
        out_a = a + da * (1.0 - a)
        if out_a <= 0.0:
            return
        ia = 1.0 - a
        buf[idx] = int(round((r * a + buf[idx] * da * ia) / out_a))
        buf[idx + 1] = int(round((g * a + buf[idx + 1] * da * ia) / out_a))
        buf[idx + 2] = int(round((b * a + buf[idx + 2] * da * ia) / out_a))
        buf[idx + 3] = int(round(out_a * 255.0))

    def _fill_span(self, y, xa, xb, rgb, alpha, cover=None):
        """Fill the horizontal span [xa, xb] on row y.

        cover(px) returns the coverage 0..1 of a boundary pixel;
        when None, boundary pixels are 4x4 sub-sampled against the
        polygon set by _fill_span_poly. Interior pixels are full
        coverage. Out-of-frame pixels are skipped.
        """
        w = self.width
        if y < 0 or y >= self.height or xb <= xa:
            return
        r, g, b = rgb
        row = y * w * 4
        # Pixel px covers [px, px+1]; it is fully inside when
        # px >= xa and px+1 <= xb.
        px0 = max(0, int(math.floor(xa)))
        px1 = min(w - 1, int(math.floor(xb)))
        if px1 < px0:
            return
        if alpha >= 1.0:
            full0 = int(math.ceil(xa))
            full1 = int(math.floor(xb)) - 1
            full0 = max(full0, 0)
            full1 = min(full1, w - 1)
            if full1 >= full0:
                pat = bytes((r, g, b, 255)) * (full1 - full0 + 1)
                self.buf[row + full0 * 4: row + (full1 + 1) * 4] = pat
            for px in range(px0, px1 + 1):
                if full0 <= px <= full1:
                    continue
                cov = 1.0 if cover is None else cover(px)
                if cov <= 0.0:
                    continue
                if cov >= 1.0:
                    idx = row + px * 4
                    self.buf[idx:idx + 4] = bytes((r, g, b, 255))
                else:
                    self._blend_pixel(row + px * 4, r, g, b, cov)
        else:
            for px in range(px0, px1 + 1):
                if px >= xa and px + 1 <= xb:
                    cov = 1.0
                elif cover is not None:
                    cov = cover(px)
                else:
                    # Exact 1D coverage of this pixel by the span.
                    cov = min(px + 1.0, xb) - max(float(px), xa)
                    cov = 0.0 if cov <= 0.0 else (1.0 if cov >= 1.0 else cov)
                if cov <= 0.0:
                    continue
                self._blend_pixel(row + px * 4, r, g, b, alpha * cov)

    def fill_polygon(self, points, rgb, alpha):
        """Even-odd scanline fill of a polygon (closed implicitly)."""
        if len(points) < 3 or alpha <= 0.0:
            return
        ys = [p[1] for p in points]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.height - 1, int(math.ceil(max(ys))))
        edges = [(points[i], points[(i + 1) % len(points)])
                 for i in range(len(points))]
        n = len(points)

        def inside(px, py):
            # Even-odd point-in-polygon for sub-sample points.
            c = False
            for i in range(n):
                x1, y1e = points[i]
                x2, y2e = points[(i + 1) % n]
                if (y1e > py) != (y2e > py):
                    xin = x1 + (py - y1e) * (x2 - x1) / (y2e - y1e)
                    if px < xin:
                        c = not c
            return c

        for y in range(y0, y1 + 1):
            yc = y + 0.5
            xs = []
            for (x1, y1e), (x2, y2e) in edges:
                if (y1e > yc) != (y2e > yc):
                    xs.append(x1 + (yc - y1e) * (x2 - x1) / (y2e - y1e))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                xa, xb = xs[k], xs[k + 1]
                if xb <= xa:
                    continue

                def cover(px, _y=y, _xa=xa, _xb=xb):
                    # 4x4 sub-sampling of this one boundary pixel.
                    hits = 0
                    for sy in range(4):
                        for sx in range(4):
                            if inside(px + (sx + 0.5) / 4.0,
                                      _y + (sy + 0.5) / 4.0):
                                hits += 1
                    return hits / 16.0

                self._fill_span(y, xa, xb, rgb, alpha, cover=cover)

    def fill_circle(self, cx, cy, radius, rgb, alpha):
        """Filled circle with a 1px analytic edge feather."""
        if radius <= 0.0 or alpha <= 0.0:
            return
        y0 = max(0, int(math.floor(cy - radius)))
        y1 = min(self.height - 1, int(math.ceil(cy + radius)))
        for y in range(y0, y1 + 1):
            dy = (y + 0.5) - cy
            if abs(dy) >= radius:
                continue
            hw = math.sqrt(max(0.0, radius * radius - dy * dy))
            xa, xb = cx - hw, cx + hw

            def cover(px, _y=y):
                d = math.hypot(px + 0.5 - cx, _y + 0.5 - cy)
                cov = 0.5 + (radius - d)
                return 0.0 if cov <= 0.0 else (1.0 if cov >= 1.0 else cov)

            self._fill_span(y, xa, xb, rgb, alpha, cover=cover)

    def stroke_polyline(self, points, rgb, width, alpha):
        """Capsule-SDF stroke along consecutive points.

        Per-pixel point-to-segment distance gives coverage
        clamp(0.5 + (r - d), 0, 1): continuous lines with no bead
        gaps. No gap rule (unlike diagrams): motion point lists
        are explicit paths, and long polygon edges must draw.
        """
        if len(points) < 2 or width <= 0.0 or alpha <= 0.0:
            return
        r = float(width) / 2.0
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            dx = x2 - x1
            dy = y2 - y1
            seg_len2 = dx * dx + dy * dy
            pad = r + 1.0
            x0 = max(0, int(math.floor(min(x1, x2) - pad)))
            x1i = min(self.width - 1, int(math.ceil(max(x1, x2) + pad)))
            y0 = max(0, int(math.floor(min(y1, y2) - pad)))
            y1i = min(self.height - 1, int(math.ceil(max(y1, y2) + pad)))
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
                    cov = 0.5 + (r - d)
                    if cov <= 0.0:
                        continue
                    if cov > 1.0:
                        cov = 1.0
                    self._blend_pixel((yy * self.width + xx) * 4,
                                      rgb[0], rgb[1], rgb[2],
                                      alpha * cov)

    def bytes(self):
        return bytes(self.buf)


def _draw_shape(canvas, el, state, ox, oy):
    """Draw one shape element at `state` onto canvas (origin ox, oy).

    ox/oy translate absolute frame coords to canvas coords.
    Fill first, then stroke, so the stroke sits on the fill edge.
    """
    opacity = float(state["opacity"])
    if opacity <= 0.0:
        return
    kind = el["shape"]
    fill = el.get("fill")
    stroke = el.get("stroke")
    sw = float(el.get("stroke_width") or 0.0)
    if kind == "circle":
        r = float(el["radius"]) * float(state["scale"])
        cx = float(state["x"]) - ox
        cy = float(state["y"]) - oy
        if fill is not None:
            canvas.fill_circle(cx, cy, r, fill, opacity)
        if stroke is not None and sw > 0:
            # Circle outline: 64-gon stroke through the same path.
            pts = [(cx + r * math.cos(2.0 * math.pi * k / 64.0),
                    cy + r * math.sin(2.0 * math.pi * k / 64.0))
                   for k in range(65)]
            canvas.stroke_polyline(pts, stroke, sw, opacity)
        return
    verts = shape_vertices(el, state)
    if not verts:
        return
    local = [(px - ox, py - oy) for px, py in verts]
    if kind == "line":
        if stroke is not None and sw > 0:
            canvas.stroke_polyline(local, stroke, sw, opacity)
        return
    if fill is not None:
        canvas.fill_polygon(local, fill, opacity)
    if stroke is not None and sw > 0:
        canvas.stroke_polyline(local + [local[0]], stroke, sw, opacity)


def element_frame_signature(el, t):
    """Hashable signature of an element's raster state at time t.

    Frames with an equal signature are pixel-identical, so the
    encoder reuses the previous frame bytes (holds and static
    elements cost one render, not one per frame).
    """
    if el["kind"] == "shape":
        st = shape_state(el, t)
        return tuple(round(float(st[k]), 6)
                     for k in ("x", "y", "scale", "rotation", "opacity"))
    if el["kind"] == "bars":
        return ("bars",) + tuple(round(p, 6)
                                 for p in bars_progress(el, t))
    if el["kind"] in ("donut", "pie"):
        return (el["kind"],) + tuple(round(p, 6)
                                     for p in donut_progress(el, t))
    if el["kind"] == "line_chart":
        return ("line_chart", round(line_chart_progress(el, t), 6))
    if el["kind"] == "lower_third":
        left, opacity = lower_third_state(el, t)
        return ("lower_third", round(left, 6), round(opacity, 6))
    return (el["kind"], round(float(t), 6))


def render_element_frame(el, t):
    """RGBA bytes of one element's frame at absolute time t."""
    bx, by, bw, bh = el["_bbox"]
    canvas = MotionCanvas(bw, bh)
    if el["kind"] == "shape":
        _draw_shape(canvas, el, shape_state(el, t), bx, by)
    elif el["kind"] == "bars":
        _draw_bars(canvas, el, t, bx, by)
    elif el["kind"] in ("donut", "pie"):
        _draw_donut(canvas, el, t, bx, by)
    elif el["kind"] == "line_chart":
        _draw_line_chart(canvas, el, t, bx, by)
    elif el["kind"] == "lower_third":
        _draw_lower_third(canvas, el, t, bx, by)
    return canvas.bytes()


# ------------------------------------------------------------------
# Charts: bars, donut/pie, line_chart (v1.43.0, Round 2).
#
# Charts use a TOP-LEFT anchor (x, y, w, h) — except donut/pie,
# which use a CENTER (x, y) + radius, since they are radial.
# Growth is staggered: bar/segment i starts at u = i*stagger and
# takes `grow` (both fractions of the window) to reach full size,
# eased with smoothstep, so the chart stays alive across its
# whole window instead of drawing once and freezing. Validation
# requires (n-1)*stagger + grow <= 1, so every bar finishes.
# Labels are NOT rasterized here: render.py derives drawtext
# items for them (see _motion_derived_texts), so Arabic labels
# keep the BiDi-verified path.
# ------------------------------------------------------------------

def _fmt_value(v):
    """Label text for a chart value: integers plain, else 1 decimal."""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.1f}"


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _window_u(el, t):
    span = float(el["end"]) - float(el["start"])
    if span <= 0:
        return 1.0
    return _clamp01((float(t) - float(el["start"])) / span)


def _staggered_progress(el, t, count):
    """Per-item growth 0..1 at time t (smoothstep-eased stagger)."""
    u = _window_u(el, t)
    stagger = float(el.get("stagger", 0.12))
    grow = float(el.get("grow", 0.5))
    out = []
    for i in range(count):
        p = (u - i * stagger) / grow if grow > 0 else 1.0
        out.append(apply_easing("smoothstep", _clamp01(p)))
    return out


def bars_layout(el):
    """Per-bar geometry (absolute coords): center x, width, unit.

    unit is pixels per value unit (chart height / max value), so
    bar i's full height is values[i] * unit from the baseline.
    """
    values = el["values"]
    n = len(values)
    slot_w = float(el["w"]) / n
    bar_w = slot_w * (1.0 - float(el.get("bar_gap", 0.2)))
    max_v = max(values)
    unit = float(el["h"]) / max_v if max_v > 0 else 0.0
    bars = []
    for i, v in enumerate(values):
        bars.append({
            "cx": float(el["x"]) + (i + 0.5) * slot_w,
            "bar_w": bar_w,
            "height": float(v) * unit,
            "value": float(v),
            "color": el["colors"][i % len(el["colors"])],
            "label": (el.get("labels") or [None] * n)[i],
        })
    return bars


def bars_progress(el, t):
    return _staggered_progress(el, t, len(el["values"]))


def donut_segments(el):
    """Per-segment geometry: start angle, full arc (degrees).

    Angles start at -90 (top) and run clockwise (screen coords,
    y down). A 2-degree gap separates segments.
    """
    values = el["values"]
    total = float(sum(values))
    segs = []
    acc = 0.0
    for i, v in enumerate(values):
        frac = float(v) / total if total > 0 else 0.0
        segs.append({
            "start_angle": -90.0 + 360.0 * acc,
            "arc": max(0.0, 360.0 * frac - 2.0),
            "value": float(v),
            "color": el["colors"][i % len(el["colors"])],
            "label": (el.get("labels") or [None] * len(values))[i],
        })
        acc += frac
    return segs


def donut_progress(el, t):
    return _staggered_progress(el, t, len(el["values"]))


def line_chart_points(el):
    """Polyline points (absolute coords) for the chart values."""
    values = el["values"]
    n = len(values)
    min_v, max_v = min(values), max(values)
    span = (max_v - min_v) if max_v > min_v else 1.0
    pts = []
    for i, v in enumerate(values):
        x = float(el["x"]) + (i * float(el["w"]) / (n - 1) if n > 1 else 0.0)
        y = float(el["y"]) + float(el["h"]) - \
            (float(v) - min_v) / span * float(el["h"])
        pts.append((x, y))
    return pts


def line_chart_progress(el, t):
    """Draw progress 0..1 at time t — LINEAR, constant pen speed."""
    return _window_u(el, t)


def _chart_bbox(el):
    """Raster bbox of a chart element (absolute frame coords)."""
    if el["kind"] in ("donut", "pie"):
        r = float(el["radius"]) + 2.0
        return (int(math.floor(float(el["x"]) - r)),
                int(math.floor(float(el["y"]) - r)),
                int(math.ceil(2.0 * r)), int(math.ceil(2.0 * r)))
    return (int(el["x"]), int(el["y"]), int(el["w"]), int(el["h"]))


def _draw_grid(canvas, x0, y0, w, h, ox, oy):
    """Faint horizontal grid lines + baseline for a chart area."""
    for frac in (0.25, 0.5, 0.75):
        gy = y0 + h * frac - oy
        canvas.stroke_polyline([(x0 - ox, gy), (x0 + w - ox, gy)],
                               (255, 255, 255), 2.0, 0.18)
    by = y0 + h - oy
    canvas.stroke_polyline([(x0 - ox, by), (x0 + w - ox, by)],
                           (255, 255, 255), 3.0, 0.6)


def _draw_bars(canvas, el, t, ox, oy):
    """Bars growing with stagger; grid + baseline underneath."""
    _draw_grid(canvas, float(el["x"]), float(el["y"]),
               float(el["w"]), float(el["h"]), ox, oy)
    baseline = float(el["y"]) + float(el["h"]) - oy
    for bar, p in zip(bars_layout(el), bars_progress(el, t)):
        if p <= 0.0:
            continue
        hgt = bar["height"] * p
        x0 = bar["cx"] - bar["bar_w"] / 2.0 - ox
        canvas.fill_polygon(
            [(x0, baseline), (x0 + bar["bar_w"], baseline),
             (x0 + bar["bar_w"], baseline - hgt), (x0, baseline - hgt)],
            bar["color"], 1.0)


def _arc_points(cx, cy, radius, a0_deg, a1_deg, steps):
    """Points along an arc (degrees, clockwise in screen coords)."""
    pts = []
    for k in range(steps + 1):
        a = math.radians(a0_deg + (a1_deg - a0_deg) * k / steps)
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return pts


def _draw_donut(canvas, el, t, ox, oy):
    """Segments sweeping in with stagger; 2-degree gaps."""
    cx = float(el["x"]) - ox
    cy = float(el["y"]) - oy
    outer = float(el["radius"])
    inner = outer * float(el.get("donut_ratio", 0.55)) \
        if el["kind"] == "donut" else 0.0
    for seg, p in zip(donut_segments(el), donut_progress(el, t)):
        if p <= 0.0 or seg["arc"] <= 0.0:
            continue
        a0 = seg["start_angle"] + 1.0
        a1 = seg["start_angle"] + 1.0 + seg["arc"] * p
        steps = max(2, int(seg["arc"] * p / 4.0))
        pts = _arc_points(cx, cy, outer, a0, a1, steps)
        if inner > 0.0:
            # Inner arc runs BACKWARD (a1 -> a0) so the polygon
            # is a simple ring sector. Reversing it (a0 -> a1)
            # self-intersects into a bowtie and even-odd fill
            # draws triangular spikes toward the center — caught
            # by eye on the R2 donut frames.
            pts += _arc_points(cx, cy, inner, a1, a0, steps)
        else:
            pts.append((cx, cy))
        canvas.fill_polygon(pts, seg["color"], 1.0)


def _draw_line_chart(canvas, el, t, ox, oy):
    """Progressive draw across the WHOLE window, with a head dot.

    Progress is linear in time (constant pen speed); the frontier
    point is found by arc length, so the head moves at a steady
    speed along the line, not per-point jumps.
    """
    _draw_grid(canvas, float(el["x"]), float(el["y"]),
               float(el["w"]), float(el["h"]), ox, oy)
    pts = [(px - ox, py - oy) for px, py in line_chart_points(el)]
    if len(pts) < 2:
        return
    # Arc lengths along the polyline.
    seg_lens = []
    total_len = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        ln = math.hypot(x2 - x1, y2 - y1)
        seg_lens.append(ln)
        total_len += ln
    p = line_chart_progress(el, t)
    target = p * total_len
    drawn = [pts[0]]
    acc = 0.0
    frontier = pts[0]
    for i, ln in enumerate(seg_lens):
        if acc + ln <= target:
            drawn.append(pts[i + 1])
            frontier = pts[i + 1]
            acc += ln
        else:
            rem = target - acc
            if ln > 0 and rem > 0:
                f = rem / ln
                fx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f
                fy = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f
                frontier = (fx, fy)
                drawn.append(frontier)
            break
    color = el.get("color") or (255, 255, 255)
    lw = float(el.get("line_width", 4))
    if len(drawn) >= 2:
        canvas.stroke_polyline(drawn, color, lw, 1.0)
    # Dots on reached points (point j shows once the head has
    # travelled the arc length up to it), head dot at the frontier.
    if el.get("show_points", True) and p > 0:
        reached = 0.0
        for i, pt in enumerate(pts[:-1]):
            if target >= reached - 1e-9:
                canvas.fill_circle(pt[0], pt[1], 4.0, (255, 255, 255), 1.0)
            reached += seg_lens[i]
    head_color = el.get("head_color") or (250, 204, 21)
    canvas.fill_circle(frontier[0], frontier[1], 7.0, head_color, 1.0)


# ------------------------------------------------------------------
# Counter and lower_third (v1.43.0, Round 3).
#
# The counter produces NO video: it is a live drawtext %{eif}
# expression evaluated by FFmpeg per frame (see
# counter_drawtext). The text goes INLINE with expansion=normal
# — never through a textfile (the textfile reader strips \:
# and breaks eif, and the BiDi wrap breaks expansion entirely;
# both verified by spike in Round 0). eif formats on this build
# are d/u/x/X only, so decimals is 0 or 1; decimals=1 uses two
# eif expressions (integer part . fractional part). from/to must
# be >= 0 (floor/trunc sign edge on negative values). A literal
# Latin % is impossible on this build (%% gives "Stray %" inline
# AND in textfile), so % is rejected in affixes — use \u066a
# (\u066a, the Arabic percent sign).
#
# Affix placement (verified by eye, Round 0 spikes): the counter
# string is built in LOGICAL order. An Arabic suffix goes BEFORE
# the number so it renders LEFT of the digits (correct Arabic
# placement); an Arabic prefix goes AFTER it so it renders RIGHT
# of the number. Latin affixes stay in Western order. A word
# affix gets a separating space; \u066a gets none.
#
# The lower third is a raster card (rounded rect) in the motion
# layer plus a derived drawtext line in render.py. The card
# slides in from the left and fades, holds, then slides out and
# fades; the text slides with the SAME linear motion (its x is a
# drawtext expression matching the card's left edge) and fades
# with the same ramps, so card and text stay in lockstep.
# ------------------------------------------------------------------

def _contains_arabic(text):
    """True when text contains any Arabic Unicode character.

    Mirrors validate.contains_arabic (kept local so this module
    does not import validate, which itself imports motion).
    """
    for ch in str(text):
        code = ord(ch)
        if (0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F
                or 0x08A0 <= code <= 0x08FF or 0xFB50 <= code <= 0xFDFF
                or 0xFE70 <= code <= 0xFEFF):
            return True
    return False


def _easing_expr_ffmpeg(name, u):
    """FFmpeg eval expression for an easing of u (u is an
    expression string evaluating to 0..1)."""
    if name == "smoothstep":
        return f"({u})*({u})*(3-2*({u}))"
    if name == "ease_in":
        return f"({u})*({u})"
    if name == "ease_out":
        return f"(1-(1-({u}))*(1-({u})))"
    if name == "ease_in_out":
        return (f"if(lt({u},0.5),2*({u})*({u}),"
                f"1-2*(1-({u}))*(1-({u})))")
    if name == "ease_out_back":
        return (f"(1+2.70158*pow(({u})-1,3)"
                f"+1.70158*pow(({u})-1,2))")
    return f"({u})"


def counter_value_expr(el):
    """FFmpeg eval expression for the counter value at time t."""
    start = float(el["start"])
    end = float(el["end"])
    u = f"(min(max((t-{start!r})/({end!r}-{start!r}),0),1))"
    eased = _easing_expr_ffmpeg(el.get("easing", "linear"), u)
    return (f"({float(el['from'])!r}+({float(el['to'])!r}"
            f"-{float(el['from'])!r})*{eased})")


def counter_drawtext(el):
    """The full inline drawtext text for a counter element."""
    v = counter_value_expr(el)
    if int(el.get("decimals", 0)) == 1:
        num = (rf"%{{eif\:floor({v})\:d}}"
               rf".%{{eif\:floor(({v}-floor({v}))*10)\:d}}")
    else:
        num = rf"%{{eif\:{v}\:d}}"
    prefix = el.get("prefix", "") or ""
    suffix = el.get("suffix", "") or ""
    before = []
    after = []
    if suffix:
        if _contains_arabic(suffix):
            before.append(suffix if suffix.strip() == "\u066a"
                          else suffix + " ")
        else:
            after.append(" " + suffix)
    if prefix:
        if _contains_arabic(prefix):
            after.append(" " + prefix)
        else:
            before.append(prefix + " ")
    return "".join(before) + num + "".join(after)


def _rounded_rect_points(x, y, w, h, r):
    """Polygon points for a rounded rect (7 arc points/corner).

    Same corner order as shape_vertices rect (bottom-right,
    bottom-left, top-left, top-right); the perimeter order is
    what matters for the even-odd fill.
    """
    if r <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts = []
    corners = [(x + w - r, y + h - r, 0.0), (x + r, y + h - r, 90.0),
               (x + r, y + r, 180.0), (x + w - r, y + r, 270.0)]
    for ccx, ccy, a0 in corners:
        for k in range(7):
            a = math.radians(a0 + 15.0 * k)
            pts.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
    return pts


def lower_third_state(el, t):
    """(left, opacity) of the lower-third card at absolute time t.

    The card slides in from fully off-screen left (left = -w)
    over `enter` seconds with a linear fade, holds at (x, 1),
    then slides back out over `exit` seconds with a linear fade.
    """
    start = float(el["start"])
    end = float(el["end"])
    x = float(el["x"])
    w = float(el["w"])
    enter = float(el.get("enter", 0.6))
    exit_ = float(el.get("exit", 0.6))
    off = -w
    if t < start:
        return (off, 0.0)
    if enter > 0 and t < start + enter:
        u = (t - start) / enter
        return (off + (x - off) * u, u)
    if exit_ <= 0 or t <= end - exit_:
        return (x, 1.0)
    u = (t - (end - exit_)) / exit_
    return (x - (x - off) * u, 1.0 - u)


def lower_third_bbox(el):
    """Union bbox of the lower-third card over its window.

    From fully off-screen left (left = -w) to the rest position
    (left = x); a 2px pad for the polygon edge AA.
    """
    x = float(el["x"])
    y = float(el["y"])
    w = float(el["w"])
    h = float(el["h"])
    bx = int(math.floor(-w)) - 2
    by = int(math.floor(y)) - 2
    bw = int(math.ceil(x + w)) - bx
    bh = int(math.ceil(y + h)) - by
    return (bx, by, max(1, bw), max(1, bh))


def _draw_lower_third(canvas, el, t, ox, oy):
    """Draw the lower-third card at absolute time t."""
    left, opacity = lower_third_state(el, t)
    if opacity <= 0.0:
        return
    x = left - ox
    y = float(el["y"]) - oy
    w = float(el["w"])
    h = float(el["h"])
    r = float(el.get("corner_radius", 16))
    color = el.get("card_color") or (0, 0, 0)
    alpha = opacity * float(el.get("card_opacity", 0.75))
    canvas.fill_polygon(_rounded_rect_points(x, y, w, h, r), color,
                        alpha)


def encode_motion_video(el, fps, out_path):
    """Encode one element's window to a transparent video.

    PNG codec, RGBA, at fps — the diagram pattern
    (encode_diagram_video): frames are piped as rawvideo to
    FFmpeg stdin, stderr goes to a sidecar file (never a pipe:
    the frames are written by hand, so communicate() must not
    be used). Identical consecutive frames are written from the
    cached bytes, not re-rendered. Raises RuntimeError on
    FFmpeg failure.
    """
    fps = float(fps)
    frames = max(1, int(round((float(el["end"]) - float(el["start"])) * fps)))
    bw, bh = int(el["_bbox"][2]), int(el["_bbox"][3])
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo",
           "-pix_fmt", "rgba", "-s", f"{bw}x{bh}",
           "-r", str(fps), "-i", "-", "-an", "-c:v", "png", out_path]
    err_path = out_path + ".stderr"
    prev_sig = None
    prev_bytes = None
    with open(err_path, "wb") as err_fh:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=err_fh)
        try:
            for i in range(frames):
                t = float(el["start"]) + i / fps
                sig = element_frame_signature(el, t)
                if sig == prev_sig and prev_bytes is not None:
                    frame = prev_bytes
                else:
                    frame = render_element_frame(el, t)
                    prev_sig = sig
                    prev_bytes = frame
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
        raise RuntimeError(
            f"motion video encode failed for '{el.get('id')}': "
            f"{err_text[-500:]}")
    return out_path
