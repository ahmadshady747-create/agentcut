"""Live Deterministic SVG Vector Charts for AgentCut (v3.4.2).

Builds palette-aware, deterministic SVG charts driven exclusively by
`window.setTime(t)` — zero CSS transitions/animations, zero external
dependencies.

Supported chart generators:
- `build_line_chart_svg(chart_spec)`: animated polyline path drawing via
  `stroke-dashoffset`, soft area fill reveal, and popping data-point nodes
  with live numeric labels.
- `build_donut_chart_svg(chart_spec)`: multi-segment ring chart with
  animated `stroke-dashoffset` arc sweep and live center total/percentage
  count-up.
- `build_grouped_bars_svg(chart_spec)`: side-by-side comparative vertical
  bar groups (`A` vs `B` across categories) that grow from the baseline
  with live value readouts.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_COLOR_VARS: dict[str, str] = {
    "accent": "var(--accent)",
    "blue": "var(--blue)",
    "marker": "var(--marker)",
    "ink": "var(--ink)",
    "muted": "var(--muted)",
}


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=False)


def _attr(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _resolve_color(name: str, fallback: str = "accent") -> str:
    return _COLOR_VARS.get(str(name).strip().lower(), _COLOR_VARS[fallback])


def build_line_chart_svg(
    chart: dict[str, Any],
    width: int = 560,
    height: int = 320,
) -> str:
    """Build a deterministic SVG line chart with progressive path drawing.

    chart: {
      points: [{label, value}] or [number, ...],
      labels: [str, ...] (optional when points are numbers),
      unit: str (optional suffix e.g. '%', '°'),
      color: 'accent'|'blue'|'marker'
    }
    """
    raw_pts = chart.get("points") or chart.get("values") or []
    labels = chart.get("labels") or []
    unit = str(chart.get("unit", ""))
    color_var = _resolve_color(chart.get("color", "accent"))

    parsed: list[tuple[str, float]] = []
    for idx, pt in enumerate(raw_pts):
        if isinstance(pt, dict):
            lbl = str(pt.get("label", labels[idx] if idx < len(labels) else f"{idx + 1}"))
            val = float(pt.get("value", 0.0))
        else:
            lbl = str(labels[idx] if idx < len(labels) else f"{idx + 1}")
            val = float(pt)
        parsed.append((lbl, val))

    if len(parsed) < 2:
        parsed = [("1", 10.0), ("2", 45.0), ("3", 85.0)]

    vals = [v for _, v in parsed]
    v_min = min(0.0, min(vals))
    v_max = max(vals)
    span = max(1e-3, v_max - v_min)

    pad_l, pad_r, pad_t, pad_b = 46, 36, 42, 54
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    coords: list[tuple[float, float, str, float]] = []
    n = len(parsed)
    for i, (lbl, val) in enumerate(parsed):
        x = pad_l + (plot_w * i / max(1, n - 1))
        norm = (val - v_min) / span
        y = pad_t + plot_h * (1.0 - norm)
        coords.append((round(x, 2), round(y, 2), lbl, val))

    # Compute exact polyline path length for deterministic stroke-dashoffset
    path_len = 0.0
    for (x1, y1, _, _), (x2, y2, _, _) in zip(coords, coords[1:]):
        path_len += math.hypot(x2 - x1, y2 - y1)
    path_len = round(max(1.0, path_len), 2)

    pts_str = " ".join(f"{x},{y}" for x, y, _, _ in coords)
    base_y = pad_t + plot_h
    area_pts = f"{coords[0][0]},{base_y} " + pts_str + f" {coords[-1][0]},{base_y}"

    # Horizontal reference grid lines
    grid_lines: list[str] = []
    for g in range(4):
        gy = round(pad_t + (plot_h * g / 3), 2)
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{gy}" x2="{width - pad_r}" y2="{gy}" '
            'stroke="var(--line)" stroke-width="2" stroke-dasharray="6 6"/>'
        )

    nodes_svg: list[str] = []
    for i, (x, y, lbl, val) in enumerate(coords):
        delay = round(0.18 + 0.55 * (i / max(1, n - 1)), 3)
        val_txt = f"{int(val) if val == int(val) else round(val, 1)}{unit}"
        nodes_svg.append(
            f'<g class="chart-dot" data-delay="{delay}">'
            f'<circle cx="{x}" cy="{y}" r="8" fill="var(--card)" '
            f'stroke="{color_var}" stroke-width="4"/>'
            f'<text x="{x}" y="{y - 16}" font-size="20" font-weight="900" '
            'text-anchor="middle" fill="var(--ink)">'
            f"{_esc(val_txt)}</text>"
            f'<text x="{x}" y="{base_y + 32}" font-size="19" font-weight="700" '
            'text-anchor="middle" fill="var(--muted)">'
            f"{_esc(lbl)}</text></g>"
        )

    return (
        f'<svg class="vector-chart chart-line-svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="{height}">'
        + "".join(grid_lines)
        + f'<polygon class="chart-area" points="{area_pts}" fill="{color_var}" opacity="0"/>'
        + f'<polyline class="chart-line-path" points="{pts_str}" fill="none" '
          f'stroke="{color_var}" stroke-width="6" stroke-linecap="round" '
          f'stroke-linejoin="round" stroke-dasharray="{path_len}" '
          f'stroke-dashoffset="{path_len}" data-len="{path_len}" data-delay="0.12"/>'
        + "".join(nodes_svg)
        + "</svg>"
    )


def build_donut_chart_svg(
    chart: dict[str, Any],
    size: int = 310,
) -> str:
    """Build a deterministic SVG donut chart with arc sweep & center counter.

    chart: {
      segments: [{label, value, color?}],
      center_value: str (optional override, e.g. '85%'),
      center_label: str (optional subtitle inside ring)
    }
    """
    segs = chart.get("segments") or []
    if not segs:
        segs = [
            {"label": "الأساسي", "value": 72, "color": "accent"},
            {"label": "المتبقي", "value": 28, "color": "blue"},
        ]
    total = sum(max(0.0, float(s.get("value", 0.0))) for s in segs) or 100.0
    cx = cy = size // 2
    r = int(size * 0.34)
    circ = round(2.0 * math.pi * r, 2)

    palette_cycle = ("accent", "blue", "marker", "ink")
    arcs_svg: list[str] = []
    legend_html: list[str] = []
    offset_accum = 0.0

    for idx, seg in enumerate(segs):
        val = max(0.0, float(seg.get("value", 0.0)))
        frac = val / total
        seg_len = round(circ * frac, 2)
        c_name = seg.get("color") or palette_cycle[idx % len(palette_cycle)]
        c_var = _resolve_color(c_name)
        delay = round(0.14 + idx * 0.16, 3)
        arcs_svg.append(
            f'<circle class="donut-seg" cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="none" stroke="{c_var}" stroke-width="28" '
            'stroke-linecap="round" '
            f'transform="rotate({round(-90 + 360 * offset_accum, 2)} {cx} {cy})" '
            f'stroke-dasharray="0 {circ}" '
            f'data-circ="{circ}" data-seglen="{seg_len}" data-delay="{delay}"/>'
        )
        offset_accum += frac
        pct = int(round(frac * 100))
        legend_html.append(
            f'<div class="donut-leg-item anim" data-delay="{delay}">'
            f'<span class="donut-swatch" style="background:{c_var}"></span>'
            f'<span class="donut-leg-lbl">{_esc(seg.get("label", ""))}</span>'
            f'<span class="donut-leg-val">{pct}%</span></div>'
        )

    first_pct = int(round((float(segs[0].get("value", 0.0)) / total) * 100))
    c_val = str(chart.get("center_value") or f"{first_pct}%")
    c_lbl = str(chart.get("center_label") or segs[0].get("label", ""))
    num_digits = "".join(ch for ch in c_val if ch.isdigit() or ch == ".") or str(first_pct)
    suf = "%" if "%" in c_val else ""

    return (
        '<div class="donut-layout">'
        f'<div class="donut-ring-wrap"><svg class="vector-chart chart-donut-svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="var(--line)" stroke-width="28"/>'
        + "".join(arcs_svg)
        + "</svg>"
        + '<div class="donut-center">'
        + f'<div class="donut-center-val bigstat" data-target="{_attr(num_digits)}" '
          f'data-dec="0" data-pre="" data-suf="{_attr(suf)}">{_esc(c_val)}</div>'
        + (f'<div class="donut-center-lbl">{_esc(c_lbl)}</div>' if c_lbl else "")
        + "</div></div>"
        + '<div class="donut-legend">' + "".join(legend_html) + "</div></div>"
    )


def build_grouped_bars_svg(
    chart: dict[str, Any],
    width: int = 560,
    height: int = 310,
) -> str:
    """Build a deterministic SVG grouped vertical bar chart.

    chart: {
      groups: [{label, values: [v1, v2], colors?: ['blue', 'accent']}],
      series: ['قبل', 'بعد'],
      unit: str
    }
    """
    groups = chart.get("groups") or [
        {"label": "المرحلة 1", "values": [35, 80]},
        {"label": "المرحلة 2", "values": [50, 95]},
    ]
    series = chart.get("series") or []
    unit = str(chart.get("unit", ""))

    all_vals = [
        float(v)
        for g in groups
        for v in (g.get("values") or [0])
    ] or [100.0]
    v_max = max(1.0, max(all_vals))

    pad_l, pad_r, pad_t, pad_b = 34, 34, 42, 52
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    base_y = pad_t + plot_h

    n_g = max(1, len(groups))
    group_w = plot_w / n_g
    default_colors = ("blue", "accent", "marker")

    bars_svg: list[str] = []
    for gi, grp in enumerate(groups):
        g_vals = [float(v) for v in (grp.get("values") or [0])]
        g_cols = grp.get("colors") or default_colors
        m = max(1, len(g_vals))
        bar_w = min(44.0, (group_w * 0.68) / m)
        total_bw = bar_w * m + 8 * (m - 1)
        gx_start = pad_l + gi * group_w + (group_w - total_bw) / 2.0

        for bi, val in enumerate(g_vals):
            target_h = round(plot_h * min(1.0, max(0.0, val / v_max)), 2)
            bx = round(gx_start + bi * (bar_w + 8), 2)
            c_var = _resolve_color(g_cols[bi % len(g_cols)])
            delay = round(0.15 + gi * 0.14 + bi * 0.08, 3)
            val_txt = f"{int(val) if val == int(val) else round(val, 1)}{unit}"
            bars_svg.append(
                f'<g class="gbar-group">'
                f'<rect class="gbar-rect" x="{bx}" y="{base_y}" '
                f'width="{round(bar_w, 1)}" height="0" rx="6" '
                f'fill="{c_var}" stroke="var(--ink)" stroke-width="2.4" '
                f'data-basey="{base_y}" data-th="{target_h}" data-delay="{delay}"/>'
                f'<text class="gbar-val" x="{round(bx + bar_w / 2, 1)}" '
                f'y="{round(base_y - target_h - 10, 1)}" font-size="18" '
                'font-weight="900" text-anchor="middle" fill="var(--ink)" '
                f'data-delay="{delay}">{_esc(val_txt)}</text></g>'
            )

        gx_mid = round(pad_l + (gi + 0.5) * group_w, 1)
        bars_svg.append(
            f'<text x="{gx_mid}" y="{base_y + 32}" font-size="20" '
            'font-weight="800" text-anchor="middle" fill="var(--ink)">'
            f'{_esc(grp.get("label", ""))}</text>'
        )

    legend_html = ""
    if series:
        pills = []
        for si, s_name in enumerate(series):
            c_var = _resolve_color(default_colors[si % len(default_colors)])
            pills.append(
                f'<span class="gbar-pill"><span class="donut-swatch" '
                f'style="background:{c_var}"></span>{_esc(s_name)}</span>'
            )
        legend_html = '<div class="gbar-legend">' + "".join(pills) + "</div>"

    return (
        legend_html
        + f'<svg class="vector-chart chart-gbars-svg" viewBox="0 0 {width} {height}" '
          f'width="100%" height="{height}">'
        + f'<line x1="{pad_l}" y1="{base_y}" x2="{width - pad_r}" y2="{base_y}" '
          'stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>'
        + "".join(bars_svg)
        + "</svg>"
    )
