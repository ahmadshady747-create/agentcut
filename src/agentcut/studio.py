"""AgentCut Interactive Web Studio (Phase 4).

Generates a standalone, self-contained interactive HTML5/CSS3/JS Web Studio
for inspecting, scrubbing, and visualizing AgentCut video timelines, scenes,
narration audio, kinetic typography, and motion diagrams.
"""

import json
import math
import os
import sys
import webbrowser
from typing import Any, Dict, List, Optional, Tuple


def _extract_timeline_model(project_or_brief: Dict[str, Any], video_path: Optional[str] = None) -> Dict[str, Any]:
    """Parse brief or project dictionary into a unified timeline model."""
    data = project_or_brief
    title = data.get("title") or data.get("id") or "AgentCut Project"

    # Explainer brief vs Project format
    scenes_raw = data.get("scenes")
    is_explainer = scenes_raw is not None

    scenes = []
    current_time = 0.0

    if is_explainer:
        for idx, sc in enumerate(scenes_raw):
            dur = float(sc.get("duration", 5.0))
            start = current_time
            end = start + dur
            current_time = end

            diagram = sc.get("diagram")
            diag_type = diagram.get("template") or diagram.get("type") if diagram else None

            scenes.append({
                "id": sc.get("id", f"scene_{idx + 1}"),
                "title": sc.get("title", f"Scene {idx + 1}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(dur, 2),
                "narration_text": sc.get("narration_text") or sc.get("text") or "",
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
            scenes.append({
                "id": cl.get("id", f"clip_{idx + 1}"),
                "title": cl.get("id", f"Clip {idx + 1}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(dur, 2),
                "narration_text": cl.get("label") or "",
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

    return {
        "title": title,
        "total_duration": max(1.0, round(total_duration, 2)),
        "width": width,
        "height": height,
        "aspect_ratio": f"{width}x{height}",
        "fps": fps,
        "scenes": scenes,
        "music": data.get("music", "ambient"),
        "video_filename": video_rel_path,
        "is_explainer": is_explainer,
    }


def generate_studio_html(
    project_or_brief: Any,
    video_path: Optional[str] = None,
    output_html_path: Optional[str] = None,
) -> str:
    """Generate interactive single-page HTML Web Studio for a project or brief.

    Args:
        project_or_brief: Dict or file path to JSON brief/project.
        video_path: Optional rendered MP4 video path.
        output_html_path: Destination HTML file path. If None, writes alongside video or brief.

    Returns:
        Absolute path to the created HTML file.
    """
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

    # If video_path is provided, determine relative path to HTML
    video_src = ""
    if video_path and os.path.isfile(video_path):
        try:
            rel = os.path.relpath(video_path, os.path.dirname(os.path.abspath(output_html_path)))
            video_src = rel.replace("\\", "/")
        except ValueError:
            video_src = video_path.replace("\\", "/")

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
      --accent-cyan: #00e5ff;
      --accent-purple: #7928ca;
      --accent-gold: #ffb703;
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
      width: 380px;
      background: var(--bg-panel);
      border-left: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      padding: 20px;
      gap: 16px;
      overflow-y: auto;
    }}
    .inspector h3 {{ font-size: 15px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
    .prop-card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .prop-row {{ display: flex; justify-content: space-between; font-size: 13px; }}
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

    <!-- Inspector Sidebar -->
    <div class="inspector">
      <h3>Scene Inspector</h3>
      <div class="prop-card">
        <div class="prop-row"><span>Scene ID:</span><b id="insId">-</b></div>
        <div class="prop-row"><span>Title:</span><b id="insTitle">-</b></div>
        <div class="prop-row"><span>Time Window:</span><b id="insTime">-</b></div>
        <div class="prop-row"><span>Transition:</span><b id="insTransition">-</b></div>
        <div class="prop-row"><span>Kinetic Subtitles:</span><b id="insKinetic">-</b></div>
        <div class="prop-row"><span>Diagram:</span><b id="insDiagram">-</b></div>
      </div>

      <h3>Narration Script</h3>
      <div id="insScript" class="prop-script">Select a scene to view speech script</div>

      <h3>Quick Actions</h3>
      <button class="btn" onclick="copyReframeCommand()">📋 Copy 9:16 Reframe Command</button>
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
      <div>
        <span style="font-size: 12px; color: var(--text-muted);">Click anywhere on timeline to scrub</span>
      </div>
    </div>

    <div class="timeline-tracks" id="timelineBody" onclick="handleTimelineClick(event)">
      <!-- Ruler -->
      <div class="ruler" id="ruler"></div>

      <!-- Playhead -->
      <div id="playhead" class="playhead" style="left: 90px;"></div>

      <!-- Track 1: Scenes -->
      <div class="track">
        <div class="track-label">SCENES</div>
        <div class="track-body" id="sceneTrack"></div>
      </div>

      <!-- Track 2: Narration -->
      <div class="track">
        <div class="track-label">NARRATION</div>
        <div class="track-body" id="narrationTrack"></div>
      </div>

      <!-- Track 3: Diagrams -->
      <div class="track">
        <div class="track-label">MOTION</div>
        <div class="track-body" id="diagramTrack"></div>
      </div>

      <!-- Track 4: Audio Bed -->
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
    const video = document.getElementById('videoPlayer');

    function initTimeline() {{
      const ruler = document.getElementById('ruler');
      const sceneTrack = document.getElementById('sceneTrack');
      const narrationTrack = document.getElementById('narrationTrack');
      const diagramTrack = document.getElementById('diagramTrack');
      const audioTrack = document.getElementById('audioTrack');

      // Build ruler ticks
      const step = TOTAL_DUR > 30 ? 5 : 2;
      for (let t = 0; t <= TOTAL_DUR; t += step) {{
        const pct = (t / TOTAL_DUR) * 100;
        const tick = document.createElement('div');
        tick.className = 'ruler-tick';
        tick.style.left = pct + '%';
        tick.innerText = t + 's';
        ruler.appendChild(tick);
      }}

      // Build blocks
      MODEL.scenes.forEach((sc, idx) => {{
        const leftPct = (sc.start / TOTAL_DUR) * 100;
        const widthPct = (sc.duration / TOTAL_DUR) * 100;

        // Scene block
        const sb = document.createElement('div');
        sb.className = 'block block-scene' + (idx === 0 ? ' selected' : '');
        sb.style.left = leftPct + '%';
        sb.style.width = widthPct + '%';
        sb.innerText = sc.title;
        sb.onclick = (e) => {{ e.stopPropagation(); selectScene(idx); seekTo(sc.start); }};
        sceneTrack.appendChild(sb);

        // Narration block
        if (sc.narration_text) {{
          const nb = document.createElement('div');
          nb.className = 'block block-narration';
          nb.style.left = leftPct + '%';
          nb.style.width = widthPct + '%';
          nb.innerText = '🗣 ' + (sc.kinetic ? '⚡ ' : '') + sc.narration_text.substring(0, 30);
          narrationTrack.appendChild(nb);
        }}

        // Diagram block
        if (sc.diagram_type) {{
          const db = document.createElement('div');
          db.className = 'block block-diagram';
          db.style.left = leftPct + '%';
          db.style.width = widthPct + '%';
          db.innerText = '📊 ' + sc.diagram_type;
          diagramTrack.appendChild(db);
        }}
      }});

      // Audio bed block
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

      document.querySelectorAll('.viewport-controls .btn').forEach(b => {{
        b.classList.toggle('active', b.innerText.includes(ar));
      }});
    }}

    function selectScene(idx) {{
      selectedSceneIdx = idx;
      const sc = MODEL.scenes[idx];
      document.getElementById('insId').innerText = sc.id;
      document.getElementById('insTitle').innerText = sc.title;
      document.getElementById('insTime').innerText = sc.start + 's - ' + sc.end + 's (' + sc.duration + 's)';
      document.getElementById('insTransition').innerText = sc.transition;
      document.getElementById('insKinetic').innerText = sc.kinetic ? 'Yes (Karaoke)' : 'No';
      document.getElementById('insDiagram').innerText = sc.diagram_type || 'None';
      document.getElementById('insScript').innerText = sc.narration_text || '(No narration text)';

      const simTitle = document.getElementById('simTitle');
      if (simTitle) {{
        simTitle.innerText = sc.title;
        document.getElementById('simBadge').innerText = sc.id;
        document.getElementById('simText').innerText = sc.narration_text || '';
      }}

      document.querySelectorAll('.block-scene').forEach((b, i) => {{
        b.classList.toggle('selected', i === idx);
      }});
    }}

    function seekTo(t) {{
      currentTime = Math.max(0, Math.min(TOTAL_DUR, t));
      if (video) video.currentTime = currentTime;
      updatePlayhead();
      checkActiveScene();
    }}

    function seekRelative(delta) {{
      seekTo(currentTime + delta);
    }}

    function togglePlay() {{
      if (video) {{
        if (video.paused) video.play();
        else video.pause();
      }}
    }}

    function updatePlayhead() {{
      const pct = (currentTime / TOTAL_DUR);
      const trackWidth = document.getElementById('timelineBody').clientWidth - 90;
      const x = 90 + pct * trackWidth;
      document.getElementById('playhead').style.left = x + 'px';

      const mins = Math.floor(currentTime / 60);
      const secs = (currentTime % 60).toFixed(2);
      document.getElementById('timecode').innerText = 
        String(mins).padStart(2, '0') + ':' + String(secs).padStart(5, '0') + ' / 00:' + String(Math.floor(TOTAL_DUR)).padStart(2, '0') + '.00';
    }}

    function checkActiveScene() {{
      const cur = MODEL.scenes.findIndex(s => currentTime >= s.start && currentTime <= s.end);
      if (cur !== -1 && cur !== selectedSceneIdx) {{
        selectScene(cur);
      }}
    }}

    function handleTimelineClick(e) {{
      const rect = document.getElementById('timelineBody').getBoundingClientRect();
      const clickX = e.clientX - rect.left - 90;
      const trackWidth = rect.width - 90;
      if (clickX >= 0) {{
        const t = (clickX / trackWidth) * TOTAL_DUR;
        seekTo(t);
      }}
    }}

    function copyReframeCommand() {{
      const cmd = `python -m agentcut reframe "${{MODEL.title}}.mp4" "${{MODEL.title}}_vertical.mp4" --aspect 9:16 --mode blur_pad`;
      navigator.clipboard.writeText(cmd);
      alert('Copied to clipboard:\\n' + cmd);
    }}

    window.onload = initTimeline;
  </script>
</body>
</html>
"""
    with open(output_html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    return os.path.abspath(output_html_path)
