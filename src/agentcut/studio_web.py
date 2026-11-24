"""Lightweight Mobile-First Arabic Web Studio for AgentCut (v3.4.5).

Built strictly with Python's standard library (`http.server`, `json`, `os`).
Lets the user open AgentCut Studio from a phone or desktop browser, type an
Arabic script or topic, pick a Brand Kit (`tab_ezzay`, `reel_studio_dark`,
`arabic_documentary`, `midnight_tech`) and format (`vertical`, `landscape`,
`square`), preview the live interactive `setTime(t)` HTML animation with a
time-scrubbing slider, inspect the generated `spec.json` and preflight audit,
or trigger a full MP4 render.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from . import __version__
from .brand_kits import list_brand_kits
from .director_browser import script_to_spec
from .flat_reels import FORMATS, PALETTES, SCENE_TYPES, build_video_html, even_word_timings, render_video
from .icons import list_icons, list_illustrations
from .multi_export import render_thumbnail_html
from .preflight import doctor_environment, preflight_audit


def build_studio_ui_html() -> str:
    """Return the responsive RTL Arabic mobile/desktop web studio single-page app."""
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>استوديو أجينت كات v{__version__}</title>
<style>
:root{{--bg:#F7F0DC;--card:#FFFDF5;--ink:#221E17;--accent:#E4572E;--blue:#2E86AB;--marker:#FFD23F;--line:#E3D9BE}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);font-family:'Cairo',system-ui,sans-serif;padding:16px;line-height:1.5}}
.wrap{{max-width:960px;margin:0 auto;display:flex;flex-direction:column;gap:16px}}
.hdr{{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:3px solid var(--ink);border-radius:22px;padding:14px 20px;box-shadow:6px 6px 0 var(--ink)}}
.badge{{background:var(--accent);color:var(--bg);border:2.5px solid var(--ink);border-radius:999px;padding:4px 16px;font-weight:900;font-size:14px}}
.card{{background:var(--card);border:3px solid var(--ink);border-radius:24px;padding:18px;box-shadow:6px 6px 0 var(--ink);display:flex;flex-direction:column;gap:12px}}
label{{font-weight:800;font-size:15px}}
input,select,textarea{{width:100%;padding:11px 14px;border:2.5px solid var(--ink);border-radius:14px;background:#fff;font-family:inherit;font-size:15px;font-weight:700}}
textarea{{min-height:120px;resize:vertical}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.btns{{display:flex;flex-wrap:wrap;gap:10px}}
button{{flex:1;min-width:135px;padding:12px 16px;border:3px solid var(--ink);border-radius:16px;font-family:inherit;font-size:15px;font-weight:900;cursor:pointer;box-shadow:4px 4px 0 var(--ink)}}
.btn-primary{{background:var(--accent);color:#fff}}
.btn-sec{{background:var(--marker);color:var(--ink)}}
.btn-blue{{background:var(--blue);color:#fff}}
.preview-box{{width:100%;height:520px;border:3px solid var(--ink);border-radius:20px;overflow:hidden;background:#111}}
iframe{{width:100%;height:100%;border:0}}
pre{{background:#18181B;color:#F4F4F5;padding:14px;border-radius:16px;overflow:auto;max-height:260px;direction:ltr;text-align:left;font-size:13px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h2>استوديو أجينت كات</h2>
    <span class="badge">v{__version__} — مسار المتصفح</span>
  </div>
  <div class="card">
    <label>عنوان الفيديو</label>
    <input id="title" value="ليه بنحس بالبرد في الشتاء؟"/>
    <div class="row">
      <div>
        <label>حزمة الهوية (Brand Kit)</label>
        <select id="brand_kit">
          <option value="tab_ezzay">طب ازاي؟ (Cream Paper)</option>
          <option value="reel_studio_dark">Reel Studio Dark</option>
          <option value="arabic_documentary">وثائقي عربي تحريري</option>
          <option value="midnight_tech">Midnight Tech & AI</option>
        </select>
      </div>
      <div>
        <label>الأبعاد (Format)</label>
        <select id="format">
          <option value="vertical">ريل عمودي (9:16)</option>
          <option value="landscape">شرح أفقي (16:9)</option>
          <option value="square">مربع (1:1)</option>
        </select>
      </div>
    </div>
    <label>السكريبت العربي (جملة أو فقرة لكل مشهد)</label>
    <textarea id="script">جسمك درجة حرارته 37 طول السنة حتى في عز البرد.
طب ازاي؟
الجلد فيه مستقبلات حرارة بترسل إنذار فوري للمخ قبل ما حرارة الأعضاء تنزل.
الارتجاف يضاعف إنتاج الحرارة 5 مرات في دقائق.
إحساسك بالبرد مش ضعف، ده نظام إنذار مبكر بيحمي قلبك ومخك.</textarea>
    <div class="btns">
      <button class="btn-primary" onclick="generatePreview()">توليد ومعاينة حية</button>
      <button class="btn-sec" onclick="previewThumb()">معاينة الصورة المصغّرة</button>
      <button class="btn-blue" onclick="runPreflight()">فحص ما قبل الرندر</button>
    </div>
  </div>
  <div class="card">
    <label>شريط الزمن التفاعلي: <span id="t_lbl">0.8</span> ثانية</label>
    <input type="range" id="scrubber" min="0" max="15" step="0.1" value="0.8" oninput="scrubTime(this.value)"/>
    <div class="preview-box"><iframe id="stage_frame"></iframe></div>
  </div>
  <div class="card">
    <label>ملف spec.json وتقرير الفحص</label>
    <pre id="json_out">اضغط "توليد ومعاينة حية" لعرض المواصفات...</pre>
  </div>
</div>
<script>
let LAST_SPEC = null;
async function generatePreview(){{
  const payload = {{
    title: document.getElementById('title').value,
    brand_kit: document.getElementById('brand_kit').value,
    format: document.getElementById('format').value,
    script: document.getElementById('script').value
  }};
  const r = await fetch('/api/script-to-spec', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)}});
  const data = await r.json();
  LAST_SPEC = data.spec;
  document.getElementById('scrubber').max = data.total_duration || 12;
  document.getElementById('json_out').textContent = JSON.stringify(data.spec, null, 2);
  const frame = document.getElementById('stage_frame');
  frame.srcdoc = data.html;
  frame.onload = () => scrubTime(document.getElementById('scrubber').value);
}}
async function previewThumb(){{
  if (!LAST_SPEC) await generatePreview();
  const r = await fetch('/api/thumbnail-html', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{spec: LAST_SPEC}})}});
  const data = await r.json();
  document.getElementById('stage_frame').srcdoc = data.html;
}}
async function runPreflight(){{
  if (!LAST_SPEC) await generatePreview();
  const r = await fetch('/api/preflight', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{spec: LAST_SPEC}})}});
  const data = await r.json();
  document.getElementById('json_out').textContent = JSON.stringify(data, null, 2);
}}
function scrubTime(v){{
  document.getElementById('t_lbl').textContent = parseFloat(v).toFixed(1);
  const win = document.getElementById('stage_frame').contentWindow;
  if (win && typeof win.setTime === 'function') win.setTime(parseFloat(v));
}}
</script>
</body>
</html>"""


def handle_studio_api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Pure function API router for Studio HTTP endpoints (testable without opening sockets)."""
    data = body or {}
    if method == "GET" and path == "/api/status":
        return 200, {
            "ok": True,
            "version": __version__,
            "brand_kits": list_brand_kits(),
            "palettes": sorted(PALETTES),
            "formats": {k: list(v) for k, v in FORMATS.items()},
            "scene_types": list(SCENE_TYPES),
            "icon_count": len(list_icons()),
            "illustrations": list_illustrations(),
            "doctor": doctor_environment(probe_browser=False),
        }

    if method == "POST" and path == "/api/script-to-spec":
        script = str(data.get("script") or "")
        title = str(data.get("title") or "")
        brand_kit = str(data.get("brand_kit") or "tab_ezzay")
        fmt = str(data.get("format") or "vertical")
        spec = script_to_spec(
            script, title=title, brand_kit=brand_kit, format_name=fmt
        )
        # Build synthetic preview timings (1.85 wps natural Arabic pace)
        timings = []
        cursor = 0.0
        for sc in spec["scenes"]:
            words = sc["text"].split()
            dur = round(max(1.8, len(words) / 1.85), 3)
            timings.append({
                "id": sc["id"],
                "start": round(cursor, 3),
                "duration": dur,
                "words": even_word_timings(words, cursor, cursor + dur),
            })
            cursor += dur
        html = build_video_html(spec, timings, embed_font=False)
        return 200, {
            "ok": True,
            "spec": spec,
            "total_duration": round(cursor, 3),
            "html": html,
        }

    if method == "POST" and path == "/api/thumbnail-html":
        spec = data.get("spec") or {}
        fmt = str(data.get("format") or "landscape")
        html = render_thumbnail_html(spec, format_name=fmt, embed_font=False)
        return 200, {"ok": True, "format": fmt, "html": html}

    if method == "POST" and path == "/api/preflight":
        spec = data.get("spec") or {}
        base_dir = str(data.get("base_dir") or ".")
        res = preflight_audit(spec, base_dir=base_dir, require_audio_files=False)
        return 200, res

    if method == "POST" and path == "/api/render":
        spec = data.get("spec") or {}
        out_path = str(data.get("output_path") or "studio_output.mp4")
        base_dir = str(data.get("base_dir") or ".")
        ok, info = render_video(spec, out_path, base_dir=base_dir)
        return (200 if ok else 400), {"ok": ok, **info}

    return 404, {"ok": False, "error": f"Unknown endpoint: {method} {path}"}


class _StudioHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return  # Quiet studio server logs

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            raw = build_studio_ui_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        status, payload = handle_studio_api("GET", self.path)
        self._send_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        status, payload = handle_studio_api("POST", self.path, body)
        self._send_json(status, payload)


def run_studio_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local AgentCut Studio HTTP server."""
    srv = HTTPServer((host, int(port)), _StudioHandler)
    print(json.dumps({
        "studio": "running",
        "url": f"http://{host}:{port}",
        "version": __version__,
    }, ensure_ascii=False))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
