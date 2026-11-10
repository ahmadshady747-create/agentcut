"""AgentCut Interactive Web Studio (Enhanced with ProCraft UI & Analytics).

Generates a standalone, self-contained interactive HTML5/CSS3/JS Web Studio
for inspecting, scrubbing, and visualizing AgentCut video timelines, scenes,
narration audio, kinetic typography, harmonious color palettes, and text metrics.
Zero external dependencies.
"""

from __future__ import annotations

import json
import math
import os
import sys
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from .colors import generate_palette
from .json_tools import format_json, minify_json
from .text_metrics import analyze_text, estimate_reading_duration


def _extract_timeline_model(project_or_brief: Dict[str, Any], video_path: Optional[str] = None) -> Dict[str, Any]:
    """Parse brief or project dictionary into a unified timeline model with analytics."""
    data = project_or_brief
    title = data.get("title") or data.get("id") or "AgentCut Project"

    # Explainer brief vs Project format
    scenes_raw = data.get("scenes")
    is_explainer = scenes_raw is not None

    scenes = []
    current_time = 0.0
    all_narrations = []

    if is_explainer:
        for idx, sc in enumerate(scenes_raw):
            dur = float(sc.get("duration", 5.0))
            start = current_time
            end = start + dur
            current_time = end

            diagram = sc.get("diagram")
            diag_type = diagram.get("template") or diagram.get("type") if diagram else None
            ntxt = sc.get("narration_text") or sc.get("text") or ""
            if ntxt:
                all_narrations.append(ntxt)

            scenes.append({
                "id": sc.get("id", f"scene_{idx + 1}"),
                "title": sc.get("title", f"Scene {idx + 1}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(dur, 2),
                "narration_text": ntxt,
                "kinetic": bool(sc.get("kinetic") or data.get("captions_style") == "karaoke"),
                "transition": sc.get("transition", "none"),
                "diagram_type": diag_type,
                "diagram": diagram,
                "look": sc.get("look") or data.get("look", "cinematic"),
            })
        total_duration = current_time
        width = int(data.get("width", 1920))
        height = int(data.get("height", 1080))
        fps = float(data.get("fps", 30))
    else:
        # Standard AgentCut Project
        clips = data.get("clips", [])
        for idx, cl in enumerate(clips):
            dur = float(cl.get("duration", 3.0))
            start = current_time
            end = start + dur
            current_time = end
            ntxt = cl.get("label") or ""
            if ntxt:
                all_narrations.append(ntxt)
            scenes.append({
                "id": cl.get("id", f"clip_{idx + 1}"),
                "title": cl.get("id", f"Clip {idx + 1}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(dur, 2),
                "narration_text": ntxt,
                "kinetic": False,
                "transition": cl.get("transition", "none"),
                "diagram_type": None,
                "diagram": None,
                "look": "standard",
            })
        total_duration = current_time
        width = int(data.get("width", 1920))
        height = int(data.get("height", 1080))
        fps = float(data.get("fps", 30))

    # Probe actual video if provided
    video_rel_path = None
    if video_path and os.path.isfile(video_path):
        video_rel_path = os.path.basename(video_path)

    # Palette derivation (ProCraft color tools integration)
    brand_color = data.get("brand_color")
    if not brand_color and isinstance(data.get("theme"), dict):
        brand_color = data["theme"].get("brand_color")
    elif not brand_color and isinstance(data.get("theme"), str) and data["theme"].startswith("#"):
        brand_color = data["theme"]
    if not brand_color:
        brand_color = "#00e5ff"

    palette = generate_palette(brand_color, harmony="analogous", dark_mode=True)

    # Text metrics (ProCraft text tools integration)
    joined_script = "\n".join(all_narrations)
    text_metrics = analyze_text(joined_script)
    text_metrics["estimated_reading_duration"] = estimate_reading_duration(joined_script)
    effective_dur = max(1.0, round(total_duration, 2))
    pacing_diff = round(effective_dur - text_metrics["estimated_reading_duration"], 2)
    text_metrics["pacing_diff"] = pacing_diff
    text_metrics["pacing_status"] = "Optimal" if abs(pacing_diff) <= 3.0 else ("Fast" if pacing_diff < -3.0 else "Relaxed")

    return {
        "title": title,
        "total_duration": effective_dur,
        "width": width,
        "height": height,
        "aspect_ratio": f"{width}x{height}",
        "fps": fps,
        "scenes": scenes,
        "music": data.get("music", "ambient"),
        "video_filename": video_rel_path,
        "is_explainer": is_explainer,
        "palette": palette,
        "text_metrics": text_metrics,
        "minified_json": minify_json(data),
        "formatted_json": format_json(data),
    }


def generate_studio_html(
    project_or_brief: Any,
    video_path: Optional[str] = None,
    output_html_path: Optional[str] = None,
) -> str:
    """Generate interactive single-page HTML Web Studio for a project or brief."""
    if isinstance(project_or_brief, str):
        if not os.path.isfile(project_or_brief):
            raise FileNotFoundError(f"Project/brief file not found: {project_or_brief}")
        with open(project_or_brief, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        base_dir = os.path.dirname(os.path.abspath(project_or_brief))
        base_name = os.path.splitext(os.path.basename(project_or_brief))[0]
    else:
        data = project_or_brief
        base_dir = os.getcwd()
        base_name = data.get("id", "agentcut_studio")

    if output_html_path is None:
        output_html_path = os.path.join(base_dir, f"{base_name}_studio.html")

    model = _extract_timeline_model(data, video_path)
    model_json = json.dumps(model, ensure_ascii=False, indent=2)

    video_src = ""
    if video_path and os.path.isfile(video_path):
        try:
            rel = os.path.relpath(video_path, os.path.dirname(os.path.abspath(output_html_path)))
            video_src = rel.replace("\\", "/")
        except ValueError:
            video_src = video_path.replace("\\", "/")

    palette = model["palette"]
    metrics = model["text_metrics"]

    from .templates import list_content_templates
    all_tpls = list_content_templates()
    templates_json = json.dumps(all_tpls, ensure_ascii=False)

    cards_html_list = []
    for tpl in all_tpls:
        c_base = tpl["colors"]["base"]
        c_acc = tpl["colors"]["accent"]
        c_sec = tpl["colors"]["secondary"]
        cards_html_list.append(f"""
        <div class="tpl-card">
          <div class="tpl-header">
            <span class="tpl-title">{tpl['name_ar']}</span>
            <span class="tpl-badge">{tpl['category'].upper()} • {tpl['default_aspect']}</span>
          </div>
          <div class="tpl-desc">{tpl['description']}</div>
          <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
            <div class="tpl-swatches">
              <div class="tpl-dot" style="background:{c_base};" title="Base: {c_base}"></div>
              <div class="tpl-dot" style="background:{c_acc};" title="Accent: {c_acc}"></div>
              <div class="tpl-dot" style="background:{c_sec};" title="Secondary: {c_sec}"></div>
              <span style="font-size:11px; color:var(--text-muted); margin-left:4px;">WCAG {tpl['colors']['contrast_ratio']}:1</span>
            </div>
            <button class="btn" style="font-size:11px; padding:3px 8px;" onclick="copyTemplateBrief('{tpl['id']}')">📋 Copy Brief</button>
          </div>
        </div>
        """)
    templates_html = "\n".join(cards_html_list)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AgentCut Studio - {model['title']}</title>
  <style>
    :root {{
      --bg-dark: #0f1117;
      --bg-panel: #161922;
      --bg-card: #1e2230;
      --border-color: #2a3144;
      --accent-cyan: {palette['primary']};
      --accent-purple: {palette['secondary']};
      --accent-gold: {palette['accent']};
      --accent-green: #00f59b;
      --text-main: #f0f3f8;
      --text-muted: #8e99b0;
      --track-scene: #2d3748;
      --track-audio: #1e3a5f;
      --track-motion: #3b2a54;
      --playhead: #ff3366;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg-dark); color: var(--text-main); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}
    
    /* Top Header */
    header {{
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border-color);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 60px;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; }}
    .logo {{ background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple)); padding: 6px 12px; border-radius: 6px; font-weight: 800; font-size: 15px; color: #fff; }}
    .project-title {{ font-size: 16px; font-weight: 600; }}
    .badges {{ display: flex; gap: 8px; align-items: center; }}
    .pill {{ background: var(--bg-card); border: 1px solid var(--border-color); padding: 4px 10px; border-radius: 20px; font-size: 12px; color: var(--text-muted); }}
    .pill.active {{ border-color: var(--accent-cyan); color: var(--accent-cyan); }}

    /* Main Area: Preview & Inspector */
    .workspace {{
      display: flex;
      flex: 1;
      height: calc(100vh - 300px);
      background: #090a0f;
    }}
    .preview-pane {{
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 16px;
      position: relative;
    }}
    .viewport-box {{
      width: 100%;
      max-width: 800px;
      height: 100%;
      max-height: 450px;
      background: #000;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      position: relative;
      transition: all 0.3s ease;
    }}
    .viewport-box.aspect-9-16 {{
      max-width: 253px;
      max-height: 450px;
      border-color: var(--accent-cyan);
      box-shadow: 0 0 30px rgba(0, 229, 255, 0.2);
    }}
    .viewport-box.aspect-1-1 {{
      max-width: 450px;
      max-height: 450px;
      border-color: var(--accent-gold);
      box-shadow: 0 0 30px rgba(255, 183, 3, 0.2);
    }}
    video {{ width: 100%; height: 100%; object-fit: contain; }}
    .simulated-screen {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 30px;
      gap: 16px;
    }}
    .simulated-text {{ font-size: 20px; font-weight: 600; color: #fff; max-width: 90%; direction: rtl; }}
    .simulated-badge {{ background: var(--accent-purple); color: #fff; padding: 4px 12px; border-radius: 12px; font-size: 12px; }}

    /* Viewport Controls */
    .viewport-controls {{
      margin-top: 12px;
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .btn {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }}
    .btn:hover {{ background: #282f42; border-color: var(--accent-cyan); }}
    .btn.active {{ background: rgba(0, 229, 255, 0.15); border-color: var(--accent-cyan); color: var(--accent-cyan); }}

    /* Inspector Sidebar */
    .inspector {{
      width: 400px;
      background: var(--bg-panel);
      border-left: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    .tabs-header {{
      display: flex;
      border-bottom: 1px solid var(--border-color);
      background: #11131a;
    }}
    .tab-btn {{
      flex: 1;
      padding: 12px 6px;
      font-size: 12px;
      font-weight: 600;
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all 0.2s;
    }}
    .tab-btn:hover {{ color: var(--text-main); }}
    .tab-btn.active {{ color: var(--accent-cyan); border-bottom-color: var(--accent-cyan); background: rgba(0,229,255,0.05); }}
    .tab-content {{
      flex: 1;
      padding: 20px;
      overflow-y: auto;
      display: none;
      flex-direction: column;
      gap: 16px;
    }}
    .tab-content.active {{ display: flex; }}

    .prop-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .prop-row {{ display: flex; justify-content: space-between; font-size: 13px; align-items: center; }}
    .prop-row span:first-child {{ color: var(--text-muted); }}
    .prop-script {{
      background: #12141c;
      border: 1px solid var(--border-color);
      padding: 12px;
      border-radius: 6px;
      font-size: 14px;
      line-height: 1.6;
      direction: rtl;
      text-align: right;
    }}

    /* Color Swatches (ProCraft Integration) */
    .swatch-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
    .swatch-card {{ background: #12141c; border: 1px solid var(--border-color); border-radius: 6px; padding: 10px; display: flex; align-items: center; gap: 10px; }}
    .swatch-box {{ width: 28px; height: 28px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2); }}
    .swatch-info {{ display: flex; flex-direction: column; font-size: 11px; }}
    .wcag-badge {{ padding: 2px 8px; border-radius: 12px; font-weight: 700; font-size: 11px; }}

    /* Templates Gallery */
    .tpl-card {{ background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px; transition: all 0.2s; }}
    .tpl-card:hover {{ border-color: var(--accent-cyan); }}
    .tpl-header {{ display: flex; justify-content: space-between; align-items: center; }}
    .tpl-title {{ font-size: 13px; font-weight: 700; color: #fff; }}
    .tpl-badge {{ font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: rgba(0,229,255,0.1); color: var(--accent-cyan); }}
    .tpl-desc {{ font-size: 12px; color: var(--text-muted); line-height: 1.4; direction: rtl; text-align: right; }}
    .tpl-swatches {{ display: flex; gap: 6px; align-items: center; }}
    .tpl-dot {{ width: 14px; height: 14px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.2); }}
    .wcag-pass {{ background: rgba(0, 245, 155, 0.2); color: #00f59b; border: 1px solid #00f59b; }}

    /* JSON Viewer */
    .json-code {{ background: #0c0e14; border: 1px solid var(--border-color); padding: 12px; border-radius: 6px; font-family: monospace; font-size: 11px; color: #a5d6ff; max-height: 350px; overflow-y: auto; white-space: pre-wrap; }}

    /* Bottom Timeline */
    .timeline-container {{
      height: 240px;
      background: var(--bg-panel);
      border-top: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      user-select: none;
    }}
    .timeline-toolbar {{
      height: 44px;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
    }}
    .transport {{ display: flex; align-items: center; gap: 10px; }}
    .timecode {{ font-family: monospace; font-size: 16px; font-weight: 700; color: var(--accent-cyan); }}
    
    .timeline-tracks {{
      flex: 1;
      position: relative;
      overflow-x: auto;
      overflow-y: hidden;
      display: flex;
      flex-direction: column;
      background: #11131a;
    }}
    .ruler {{
      height: 24px;
      background: #171b26;
      border-bottom: 1px solid var(--border-color);
      position: relative;
      width: 100%;
    }}
    .ruler-tick {{
      position: absolute;
      top: 0;
      height: 100%;
      border-left: 1px solid var(--border-color);
      font-size: 10px;
      color: var(--text-muted);
      padding-left: 4px;
    }}

    .track {{
      height: 38px;
      position: relative;
      border-bottom: 1px solid rgba(42, 49, 68, 0.4);
      display: flex;
      align-items: center;
    }}
    .track-label {{
      position: sticky;
      left: 0;
      width: 90px;
      height: 100%;
      background: rgba(22, 25, 34, 0.95);
      border-right: 1px solid var(--border-color);
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      padding-left: 10px;
      z-index: 10;
    }}
    .track-body {{
      flex: 1;
      height: 100%;
      position: relative;
    }}
    .block {{
      position: absolute;
      height: 30px;
      top: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      padding: 0 8px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      transition: filter 0.15s, border-color 0.15s;
    }}
    .block:hover {{ filter: brightness(1.2); }}
    .block.selected {{ outline: 2px solid #fff; }}
    .block-scene {{ background: linear-gradient(90deg, #2b5876, #4e4376); border: 1px solid #4a6fa5; color: #fff; }}
    .block-narration {{ background: linear-gradient(90deg, #11998e, #38ef7d); border: 1px solid #20bf6b; color: #000; font-weight: 700; }}
    .block-diagram {{ background: linear-gradient(90deg, #ff8008, #ffc837); border: 1px solid #fa8231; color: #000; }}
    .block-audio {{ background: #1f2738; border: 1px dashed var(--accent-cyan); color: var(--accent-cyan); }}

    /* Playhead */
    .playhead {{
      position: absolute;
      top: 0;
      bottom: 0;
      width: 2px;
      background: var(--playhead);
      z-index: 50;
      pointer-events: none;
    }}
    .playhead::after {{
      content: "";
      position: absolute;
      top: 0;
      left: -6px;
      width: 14px;
      height: 14px;
      background: var(--playhead);
      clip-path: polygon(0% 0%, 100% 0%, 50% 100%);
    }}
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <div class="logo">AGENTCUT</div>
      <div class="project-title">{model['title']}</div>
    </div>
    <div class="badges">
      <div class="pill active">{model['aspect_ratio']}</div>
      <div class="pill">{model['fps']} FPS</div>
      <div class="pill">{len(model['scenes'])} Scenes</div>
      <div class="pill active">⏱ {model['total_duration']}s</div>
    </div>
  </header>

  <div class="workspace">
    <!-- Preview Window -->
    <div class="preview-pane">
      <div id="viewport" class="viewport-box">
        {f'<video id="videoPlayer" src="{video_src}" controls></video>' if video_src else '''
        <div class="simulated-screen">
          <div id="simBadge" class="simulated-badge">Scene 1</div>
          <div id="simTitle" style="font-size: 24px; font-weight: 700;">AgentCut Preview</div>
          <div id="simText" class="simulated-text">Scrub timeline below to inspect scenes</div>
        </div>
        '''}
      </div>

      <div class="viewport-controls">
        <button class="btn active" onclick="setAspect('16:9')">🖥 16:9 Landscape</button>
        <button class="btn" onclick="setAspect('9:16')">📱 9:16 Shorts/Reels</button>
        <button class="btn" onclick="setAspect('1:1')">⏹ 1:1 Square</button>
      </div>
    </div>

    <!-- Inspector Sidebar with ProCraft Tabs -->
    <div class="inspector">
      <div class="tabs-header">
        <button id="tabBtn-scene" class="tab-btn active" onclick="switchTab('scene')">🎬 Scene</button>
        <button id="tabBtn-palette" class="tab-btn" onclick="switchTab('palette')">🎨 Palette</button>
        <button id="tabBtn-metrics" class="tab-btn" onclick="switchTab('metrics')">📊 Metrics</button>
        <button id="tabBtn-templates" class="tab-btn" onclick="switchTab('templates')">📐 Templates</button>
        <button id="tabBtn-json" class="tab-btn" onclick="switchTab('json')">📦 JSON</button>
      </div>

      <!-- Tab 1: Scene Inspector -->
      <div id="tabContent-scene" class="tab-content active">
        <div class="prop-card">
          <div class="prop-row"><span>Scene ID:</span><b id="insId">-</b></div>
          <div class="prop-row"><span>Title:</span><b id="insTitle">-</b></div>
          <div class="prop-row"><span>Time Window:</span><b id="insTime">-</b></div>
          <div class="prop-row"><span>Transition:</span><b id="insTransition">-</b></div>
          <div class="prop-row"><span>Kinetic Subtitles:</span><b id="insKinetic">-</b></div>
          <div class="prop-row"><span>Diagram:</span><b id="insDiagram">-</b></div>
        </div>

        <h4 style="font-size: 13px; color: var(--text-muted); text-transform: uppercase;">Narration Script</h4>
        <div id="insScript" class="prop-script">Select a scene to view speech script</div>

        <button class="btn" style="margin-top: 10px;" onclick="copyReframeCommand()">📋 Copy 9:16 Reframe Command</button>
      </div>

      <!-- Tab 2: Color Palette & WCAG Readability -->
      <div id="tabContent-palette" class="tab-content">
        <div class="prop-card">
          <div class="prop-row"><span>Color Harmony:</span><b>{palette['harmony'].capitalize()}</b></div>
          <div class="prop-row"><span>WCAG Contrast:</span><b class="wcag-badge wcag-pass">{palette['contrast_ratio']}:1 ({palette['wcag_level']})</b></div>
        </div>

        <div class="swatch-grid">
          <div class="swatch-card">
            <div class="swatch-box" style="background: {palette['primary']};"></div>
            <div class="swatch-info"><span>Primary</span><b>{palette['primary']}</b></div>
          </div>
          <div class="swatch-card">
            <div class="swatch-box" style="background: {palette['secondary']};"></div>
            <div class="swatch-info"><span>Secondary</span><b>{palette['secondary']}</b></div>
          </div>
          <div class="swatch-card">
            <div class="swatch-box" style="background: {palette['accent']};"></div>
            <div class="swatch-info"><span>Accent</span><b>{palette['accent']}</b></div>
          </div>
          <div class="swatch-card">
            <div class="swatch-box" style="background: {palette['background']};"></div>
            <div class="swatch-info"><span>Background</span><b>{palette['background']}</b></div>
          </div>
        </div>

        <div class="prop-card" style="margin-top: 10px;">
          <div class="prop-row"><span>Text Primary:</span><span style="color: {palette['text_primary']}; font-weight:700;">{palette['text_primary']}</span></div>
          <div class="prop-row"><span>Text Muted:</span><span style="color: {palette['text_muted']}; font-weight:700;">{palette['text_muted']}</span></div>
          <div class="prop-row"><span>Surface Glass:</span><span style="font-size:11px;">{palette['surface']}</span></div>
        </div>
      </div>

      <!-- Tab 3: Text & Reading Pacing Metrics -->
      <div id="tabContent-metrics" class="tab-content">
        <div class="prop-card">
          <div class="prop-row"><span>Total Words:</span><b>{metrics['words']}</b></div>
          <div class="prop-row"><span>Total Characters:</span><b>{metrics['characters']}</b></div>
          <div class="prop-row"><span>Arabic Words:</span><b>{metrics['arabic_words']}</b></div>
          <div class="prop-row"><span>Latin Words:</span><b>{metrics['latin_words']}</b></div>
        </div>

        <div class="prop-card">
          <div class="prop-row"><span>Estimated Reading Time:</span><b>{metrics['estimated_reading_duration']}s</b></div>
          <div class="prop-row"><span>Total Video Duration:</span><b>{model['total_duration']}s</b></div>
          <div class="prop-row"><span>Pacing Delta:</span><b>{metrics['pacing_diff']}s</b></div>
          <div class="prop-row"><span>Pacing Health:</span><b style="color: #00f59b;">{metrics['pacing_status']}</b></div>
        </div>
      </div>

      <!-- Tab 4: Templates Gallery -->
      <div id="tabContent-templates" class="tab-content">
        <h4 style="font-size: 13px; color: var(--text-muted); text-transform: uppercase;">معرض القوالب الجاهزة / Turnkey Templates</h4>
        {templates_html}
      </div>

      <!-- Tab 5: JSON Spec & Token Tools -->
      <div id="tabContent-json" class="tab-content">
        <button class="btn" style="width: 100%; margin-bottom: 10px;" onclick="copyMinifiedJson()">⚡ Copy Minified JSON (Tokens Saved)</button>
        <pre class="json-code" id="rawJsonBlock">{model['formatted_json']}</pre>
      </div>
    </div>
  </div>

  <!-- Bottom Timeline -->
  <div class="timeline-container">
    <div class="timeline-toolbar">
      <div class="transport">
        <button class="btn" onclick="togglePlay()">⏯ Play / Pause</button>
        <button class="btn" onclick="seekRelative(-1)">-1s</button>
        <button class="btn" onclick="seekRelative(1)">+1s</button>
        <div id="timecode" class="timecode">00:00.00 / 00:{int(model['total_duration']):02d}.00</div>
      </div>
      <div style="display: flex; align-items: center; gap: 10px;">
        <span style="font-size: 12px; color: var(--text-muted);">Volume:</span>
        <input type="range" min="0" max="1" step="0.05" value="1" style="cursor:pointer;" oninput="setVolume(this.value)">
      </div>
    </div>

    <div class="timeline-tracks" id="timelineBody" onclick="handleTimelineClick(event)">
      <div class="ruler" id="ruler"></div>
      <div id="playhead" class="playhead" style="left: 90px;"></div>

      <div class="track">
        <div class="track-label">SCENES</div>
        <div class="track-body" id="sceneTrack"></div>
      </div>

      <div class="track">
        <div class="track-label">NARRATION</div>
        <div class="track-body" id="narrationTrack"></div>
      </div>

      <div class="track">
        <div class="track-label">MOTION</div>
        <div class="track-body" id="diagramTrack"></div>
      </div>

      <div class="track">
        <div class="track-label">AUDIO</div>
        <div class="track-body" id="audioTrack"></div>
      </div>
    </div>
  </div>

  <script>
    const MODEL = {model_json};
    const TOTAL_DUR = MODEL.total_duration;
    let currentTime = 0.0;
    let selectedSceneIdx = 0;
    let isPlaying = false;
    let playInterval = null;
    const video = document.getElementById('videoPlayer');

    function switchTab(tabName) {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      const btn = document.getElementById('tabBtn-' + tabName);
      const content = document.getElementById('tabContent-' + tabName);
      if (btn) btn.classList.add('active');
      if (content) content.classList.add('active');
    }}

    function initTimeline() {{
      const ruler = document.getElementById('ruler');
      const sceneTrack = document.getElementById('sceneTrack');
      const narrationTrack = document.getElementById('narrationTrack');
      const diagramTrack = document.getElementById('diagramTrack');
      const audioTrack = document.getElementById('audioTrack');

      const step = TOTAL_DUR > 30 ? 5 : 2;
      for (let t = 0; t <= TOTAL_DUR; t += step) {{
        const pct = (t / TOTAL_DUR) * 100;
        const tick = document.createElement('div');
        tick.className = 'ruler-tick';
        tick.style.left = pct + '%';
        tick.innerText = t + 's';
        ruler.appendChild(tick);
      }}

      MODEL.scenes.forEach((sc, idx) => {{
        const leftPct = (sc.start / TOTAL_DUR) * 100;
        const widthPct = (sc.duration / TOTAL_DUR) * 100;

        const sb = document.createElement('div');
        sb.className = 'block block-scene' + (idx === 0 ? ' selected' : '');
        sb.style.left = leftPct + '%';
        sb.style.width = widthPct + '%';
        sb.innerText = sc.title;
        sb.onclick = (e) => {{ e.stopPropagation(); selectScene(idx); seekTo(sc.start); }};
        sceneTrack.appendChild(sb);

        if (sc.narration_text) {{
          const nb = document.createElement('div');
          nb.className = 'block block-narration';
          nb.style.left = leftPct + '%';
          nb.style.width = widthPct + '%';
          nb.innerText = '🗣 ' + (sc.kinetic ? '⚡ ' : '') + sc.narration_text.substring(0, 30);
          narrationTrack.appendChild(nb);
        }}

        if (sc.diagram_type) {{
          const db = document.createElement('div');
          db.className = 'block block-diagram';
          db.style.left = leftPct + '%';
          db.style.width = widthPct + '%';
          db.innerText = '📊 ' + sc.diagram_type;
          diagramTrack.appendChild(db);
        }}
      }});

      const ab = document.createElement('div');
      ab.className = 'block block-audio';
      ab.style.left = '0%';
      ab.style.width = '100%';
      ab.innerText = '🎵 Ambient Bed: ' + (MODEL.music || 'Default Pad');
      audioTrack.appendChild(ab);

      selectScene(0);

      if (video) {{
        video.addEventListener('timeupdate', () => {{
          currentTime = video.currentTime;
          updatePlayhead();
          checkActiveScene();
        }});
      }}
    }}

    function setAspect(ar) {{
      const vp = document.getElementById('viewport');
      vp.className = 'viewport-box';
      if (ar === '9:16') vp.classList.add('aspect-9-16');
      if (ar === '1:1') vp.classList.add('aspect-1-1');
      document.querySelectorAll('.viewport-controls .btn').forEach(b => b.classList.remove('active'));
      event.target.classList.add('active');
    }}

    function selectScene(idx) {{
      if (idx < 0 || idx >= MODEL.scenes.length) return;
      selectedSceneIdx = idx;
      const sc = MODEL.scenes[idx];

      document.querySelectorAll('#sceneTrack .block').forEach((el, i) => {{
        if (i === idx) el.classList.add('selected');
        else el.classList.remove('selected');
      }});

      document.getElementById('insId').innerText = sc.id;
      document.getElementById('insTitle').innerText = sc.title;
      document.getElementById('insTime').innerText = sc.start + 's - ' + sc.end + 's (' + sc.duration + 's)';
      document.getElementById('insTransition').innerText = sc.transition;
      document.getElementById('insKinetic').innerText = sc.kinetic ? '✅ Karaoke Active' : 'Plain';
      document.getElementById('insDiagram').innerText = sc.diagram_type ? ('📊 ' + sc.diagram_type) : 'None';
      document.getElementById('insScript').innerText = sc.narration_text || 'No narration for this scene';

      const badge = document.getElementById('simBadge');
      const title = document.getElementById('simTitle');
      const text = document.getElementById('simText');
      if (badge) badge.innerText = sc.title;
      if (title) title.innerText = sc.title;
      if (text) text.innerText = sc.narration_text || 'Scene ' + (idx + 1);
    }}

    function checkActiveScene() {{
      const foundIdx = MODEL.scenes.findIndex(s => currentTime >= s.start && currentTime < s.end);
      if (foundIdx !== -1 && foundIdx !== selectedSceneIdx) {{
        selectScene(foundIdx);
      }}
    }}

    function updatePlayhead() {{
      const pct = Math.min(100, Math.max(0, (currentTime / TOTAL_DUR) * 100));
      const ph = document.getElementById('playhead');
      ph.style.left = `calc(90px + (100% - 90px) * ${{pct / 100}})`;

      const m = Math.floor(currentTime / 60);
      const s = (currentTime % 60).toFixed(2);
      document.getElementById('timecode').innerText =
        (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s + ' / 00:' + Math.floor(TOTAL_DUR).toString().padStart(2, '0') + '.00';
    }}

    function seekTo(sec) {{
      currentTime = Math.max(0, Math.min(TOTAL_DUR, sec));
      if (video) video.currentTime = currentTime;
      updatePlayhead();
      checkActiveScene();
    }}

    function seekRelative(delta) {{
      seekTo(currentTime + delta);
    }}

    function handleTimelineClick(e) {{
      const rect = document.getElementById('timelineBody').getBoundingClientRect();
      const trackX = e.clientX - rect.left - 90;
      const trackW = rect.width - 90;
      if (trackX >= 0 && trackW > 0) {{
        const targetSec = (trackX / trackW) * TOTAL_DUR;
        seekTo(targetSec);
      }}
    }}

    function togglePlay() {{
      if (video) {{
        if (video.paused) video.play();
        else video.pause();
        return;
      }}
      isPlaying = !isPlaying;
      if (isPlaying) {{
        playInterval = setInterval(() => {{
          currentTime += 0.1;
          if (currentTime >= TOTAL_DUR) {{
            currentTime = 0.0;
            togglePlay();
          }}
          updatePlayhead();
          checkActiveScene();
        }}, 100);
      }} else {{
        clearInterval(playInterval);
      }}
    }}

    function setVolume(v) {{
      if (video) video.volume = parseFloat(v);
    }}

    function copyReframeCommand() {{
      const cmd = `python -m agentcut reframe "${{MODEL.video_filename || 'input.mp4'}}" "vertical_916.mp4" --aspect 9:16 --mode blur_pad`;
      navigator.clipboard.writeText(cmd);
      alert('Copied 9:16 reframe command to clipboard!');
    }}

    function copyMinifiedJson() {{
      navigator.clipboard.writeText(MODEL.minified_json);
      alert('Copied token-minified JSON to clipboard!');
    }}

    const ALL_TEMPLATES = {templates_json};

    function copyTemplateBrief(tid) {{
      const t = ALL_TEMPLATES.find(x => x.id === tid);
      if (!t) return;
      const sample = {{
        template: t.id,
        title: t.name_ar,
        scenes: [
          {{ title: "المشهد الأول: المدخل", text: "نص المشهد الأول المشكول بدقة...", role: "hook" }},
          {{ title: "المشهد الثاني: التفاصيل", text: "نص المشهد الثاني مع المخطط التوضيحي...", role: "data_proof" }},
          {{ title: "المشهد الأخير: الخلاصة", text: "نص الخلاصة والدعوة للتفاعل...", role: "conclusion" }}
        ]
      }};
      navigator.clipboard.writeText(JSON.stringify(sample, null, 2));
      alert('Copied turnkey brief for ' + t.name_ar + ' to clipboard!');
    }}

    window.onload = initTimeline;
  </script>
</body>
</html>
"""

    with open(output_html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    return os.path.abspath(output_html_path)
