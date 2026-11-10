"""AgentCut Plugin Architecture (Phase 4).

Allows extending AgentCut with custom motion elements and diagram templates
without modifying the core codebase.

Third-party motion plugins can register custom renderers, bounding box
calculators, and validation rules. Plugins can be loaded dynamically from
a workspace directory (`plugins/`), system path (`~/.agentcut/plugins/`),
or via Python entry points.
"""

import importlib.util
import math
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class MotionPlugin:
    """Base class for custom motion element plugins."""

    name: str = "custom_motion"

    def bbox(self, el: Dict[str, Any]) -> Tuple[int, int, int, int]:
        """Compute keyframe-union or static bounding box (bx, by, bw, bh)."""
        x = int(math.floor(float(el.get("x", 0))))
        y = int(math.floor(float(el.get("y", 0))))
        w = int(math.ceil(float(el.get("w", 100))))
        h = int(math.ceil(float(el.get("h", 100))))
        return (x, y, max(1, w), max(1, h))

    def render(self, canvas: Any, el: Dict[str, Any], t: float, ox: int, oy: int) -> None:
        """Render element frame at time t onto canvas with canvas offset (ox, oy)."""
        raise NotImplementedError("MotionPlugin subclasses must implement render()")

    def validate(
        self,
        el: Dict[str, Any],
        mid: str,
        start: float,
        end: float,
        width: int,
        height: int,
        fps: float,
        errors: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Validate element parameters and return resolved element dict."""
        rel = dict(el)
        rel["id"] = mid
        rel["kind"] = self.name
        rel["start"] = start
        rel["end"] = end

        try:
            bx, by, bw, bh = self.bbox(rel)
        except Exception as exc:
            errors.append({
                "code": "INVALID_SCHEMA",
                "message": f"Plugin '{self.name}' bbox calculation failed: {exc}",
                "field": "motion",
                "clip_id": mid,
            })
            return None

        rel["_bbox"] = (bx, by, bw, bh)
        rel["_frames"] = max(1, int(round((end - start) * fps)))
        return rel


# ----------------------------------------------------------------------
# Plugin Registries
# ----------------------------------------------------------------------

_MOTION_PLUGINS: Dict[str, MotionPlugin] = {}
_DIAGRAM_PLUGINS: Dict[str, Callable[[Dict[str, Any]], Tuple[int, int, List[Any]]]] = {}


def register_motion_plugin(name: str, plugin: MotionPlugin) -> None:
    """Register a custom motion plugin."""
    _MOTION_PLUGINS[name] = plugin


def get_motion_plugin(name: str) -> Optional[MotionPlugin]:
    """Retrieve a registered motion plugin by name."""
    return _MOTION_PLUGINS.get(name)


def list_motion_plugins() -> List[str]:
    """List all registered motion plugin names."""
    return sorted(_MOTION_PLUGINS.keys())


def register_diagram_plugin(
    name: str,
    builder_fn: Callable[[Dict[str, Any]], Tuple[int, int, List[Any]]],
) -> None:
    """Register a custom diagram template builder function."""
    _DIAGRAM_PLUGINS[name] = builder_fn


def get_diagram_plugin(name: str) -> Optional[Callable]:
    """Retrieve a registered diagram plugin builder function."""
    return _DIAGRAM_PLUGINS.get(name)


def list_diagram_plugins() -> List[str]:
    """List all registered diagram plugin names."""
    return sorted(_DIAGRAM_PLUGINS.keys())


def is_known_motion_kind(kind: str) -> bool:
    """Check if motion kind is known (built-in or plugin)."""
    from .motion import MOTION_KINDS
    return kind in MOTION_KINDS or kind in _MOTION_PLUGINS


def get_all_motion_kinds() -> Set[str]:
    """Return set of all motion kind names (built-ins + plugins)."""
    from .motion import MOTION_KINDS
    return set(MOTION_KINDS).union(_MOTION_PLUGINS.keys())


# ----------------------------------------------------------------------
# Built-in Reference Plugins
# ----------------------------------------------------------------------

def _parse_rgb(val: Any, default: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    """Parse color from list, tuple, color name, or hex string."""
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        try:
            return (int(val[0]), int(val[1]), int(val[2]))
        except (ValueError, TypeError):
            pass
    if isinstance(val, str):
        try:
            from .diagrams import resolve_color
            c = resolve_color(val)
            if c is not None:
                return c
        except Exception:
            pass
    return default


class ProgressBarPlugin(MotionPlugin):
    """Animated progress bar with capsule styling and smooth fill."""

    name = "progress_bar"

    def bbox(self, el: Dict[str, Any]) -> Tuple[int, int, int, int]:
        x = int(math.floor(float(el.get("x", 100))))
        y = int(math.floor(float(el.get("y", 100))))
        w = int(math.ceil(float(el.get("w", 600))))
        h = int(math.ceil(float(el.get("h", 32))))
        pad = 4
        return (x - pad, y - pad, w + pad * 2, h + pad * 2)

    def render(self, canvas: Any, el: Dict[str, Any], t: float, ox: int, oy: int) -> None:
        from .motion import _rounded_rect_points

        x = float(el.get("x", 100)) - ox
        y = float(el.get("y", 100)) - oy
        w = float(el.get("w", 600))
        h = float(el.get("h", 32))
        radius = float(el.get("radius", h / 2))

        start_t = float(el["start"])
        end_t = float(el["end"])
        dur = max(0.001, end_t - start_t)
        u = max(0.0, min(1.0, (t - start_t) / dur))

        # Ease: smoothstep
        progress = u * u * (3.0 - 2.0 * u)
        p_start = float(el.get("progress_start", 0.0))
        p_end = float(el.get("progress_end", 1.0))
        cur_progress = max(0.0, min(1.0, p_start + (p_end - p_start) * progress))

        bg_color = _parse_rgb(el.get("bg_color"), (30, 35, 45))
        bar_color = _parse_rgb(el.get("color"), (0, 200, 255))
        border_color = _parse_rgb(el.get("border_color"), (80, 90, 110))

        # Background track
        bg_pts = _rounded_rect_points(x, y, w, h, radius)
        canvas.fill_polygon(bg_pts, bg_color, 0.85)
        canvas.stroke_polyline(bg_pts + [bg_pts[0]], border_color, 1.5, 0.7)

        # Active Fill
        fill_w = max(radius * 2, w * cur_progress)
        if cur_progress > 0.02 and fill_w > 4:
            fill_pts = _rounded_rect_points(x, y, fill_w, h, radius)
            canvas.fill_polygon(fill_pts, bar_color, 0.95)


class BadgePlugin(MotionPlugin):
    """Animated glass badge with glowing border and indicator dot."""

    name = "badge"

    def bbox(self, el: Dict[str, Any]) -> Tuple[int, int, int, int]:
        x = int(math.floor(float(el.get("x", 100))))
        y = int(math.floor(float(el.get("y", 100))))
        w = int(math.ceil(float(el.get("w", 240))))
        h = int(math.ceil(float(el.get("h", 60))))
        pad = 8
        return (x - pad, y - pad, w + pad * 2, h + pad * 2)

    def render(self, canvas: Any, el: Dict[str, Any], t: float, ox: int, oy: int) -> None:
        from .motion import _rounded_rect_points

        x = float(el.get("x", 100)) - ox
        y = float(el.get("y", 100)) - oy
        w = float(el.get("w", 240))
        h = float(el.get("h", 60))
        radius = float(el.get("radius", 12))

        start_t = float(el["start"])
        end_t = float(el["end"])
        dur = max(0.001, end_t - start_t)
        u = max(0.0, min(1.0, (t - start_t) / dur))

        # Entrance bounce/scale
        opacity = min(1.0, u * 4.0)

        bg_color = _parse_rgb(el.get("bg_color"), (20, 25, 35))
        accent = _parse_rgb(el.get("accent_color"), (100, 240, 160))
        border_color = _parse_rgb(el.get("border_color"), (60, 70, 90))

        # Glass panel
        pts = _rounded_rect_points(x, y, w, h, radius)
        canvas.fill_polygon(pts, bg_color, 0.88 * opacity)
        canvas.stroke_polyline(pts + [pts[0]], border_color, 1.5, 0.8 * opacity)

        # Indicator dot
        dot_r = h * 0.16
        dot_cx = x + 16 + dot_r
        dot_cy = y + h / 2
        canvas.fill_circle(dot_cx, dot_cy, dot_r, accent, 0.95 * opacity)


# Auto-register reference plugins
register_motion_plugin("progress_bar", ProgressBarPlugin())
register_motion_plugin("badge", BadgePlugin())


# ----------------------------------------------------------------------
# Dynamic Discovery and Loading
# ----------------------------------------------------------------------

def load_plugin_file(file_path: str) -> bool:
    """Load a Python plugin file dynamically."""
    if not os.path.isfile(file_path) or not file_path.endswith(".py"):
        return False
    module_name = f"agentcut_plugin_{os.path.splitext(os.path.basename(file_path))[0]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
            return True
    except Exception as exc:
        print(f"Warning: Failed to load plugin {file_path}: {exc}", file=sys.stderr)
        return False
    return False


def load_plugins_from_directory(dir_path: str) -> List[str]:
    """Scan directory and load all plugin modules."""
    loaded = []
    if not os.path.isdir(dir_path):
        return loaded
    for fname in sorted(os.listdir(dir_path)):
        if fname.endswith(".py") and not fname.startswith("__"):
            fpath = os.path.join(dir_path, fname)
            if load_plugin_file(fpath):
                loaded.append(fpath)
    return loaded


def discover_plugins() -> List[str]:
    """Discover and load plugins from standard workspace and user locations."""
    loaded = []
    # 1. Custom ENV directory
    env_dir = os.environ.get("AGENTCUT_PLUGINS_DIR")
    if env_dir:
        loaded.extend(load_plugins_from_directory(env_dir))

    # 2. Local workspace plugins/
    cwd_plugins = os.path.join(os.getcwd(), "plugins")
    if os.path.isdir(cwd_plugins):
        loaded.extend(load_plugins_from_directory(cwd_plugins))

    # 3. User home ~/.agentcut/plugins
    home_plugins = os.path.expanduser("~/.agentcut/plugins")
    if os.path.isdir(home_plugins):
        loaded.extend(load_plugins_from_directory(home_plugins))

    return loaded
