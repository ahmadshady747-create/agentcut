# AgentCut 🎬

### The Autonomous Video Editing Engine Built for AI Agents.

[![Version](https://img.shields.io/badge/version-1.63.0-blue.svg)](VERSION)
[![CI Test Suite](https://github.com/ahmadshady747-create/agentcut/actions/workflows/test.yml/badge.svg)](https://github.com/ahmadshady747-create/agentcut/actions/workflows/test.yml)
[![Zero Python Dependencies](https://img.shields.io/badge/python%20dependencies-zero%20(stdlib%20only)-brightgreen.svg)](https://www.python.org/)
[![MCP Tools](https://img.shields.io/badge/MCP%20Tools-34%20tools-purple.svg)](src/agentcut/server.py)
[![Content Templates](https://img.shields.io/badge/templates-8%20genres-orange.svg)](src/agentcut/templates.py)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue.svg)](LICENSE)
[![Engine: FFmpeg](https://img.shields.io/badge/engine-FFmpeg%208.1%2B-green.svg)](https://ffmpeg.org/)

**AgentCut turns a written brief or high-level idea into a broadcast-grade, checked MP4 — no timeline to drag, no GUI to click, no footage to generate.** 

An AI agent or developer describes the video in JSON or selects a turnkey genre template. AgentCut validates it in milliseconds before a single frame renders, previews real frames, synthesizes neural voiceover narration with microsecond-aligned kinetic subtitles, composites motion diagrams, applies broadcast sound design with automatic music ducking, and exports production-ready video across any aspect ratio.

> **One sentence:** You describe what the video should convey; AgentCut designs, directs, voices, animates, and masters it — proving correctness with real frames and measurements.

---

## ⚡ Quickstart

### Standard Installation (`pip` / `uvx`)

```sh
# Clone and install in editable mode (standard packaging with pyproject.toml):
git clone https://github.com/ahmadshady747-create/agentcut.git
cd agentcut
pip install -e .

# Launch anywhere as standard CLI or MCP server:
agentcut templates
agentcut-mcp
```

### Direct CLI Commands

```sh
# 1. List all 8 turnkey content genres
agentcut templates

# 2. Render directly from a turnkey template (one command)
agentcut from-template finance_data_story examples/templates/finance_data_story.json out.mp4

# 3. Autonomous one-button explainer video
agentcut produce-explainer examples/one-button-explainer.json explainer.mp4

# 4. Native neural voiceover + microsecond synchronized SRT
agentcut tts "نص الشرح الصوتي المشكول بدقة" voice.mp3 --srt captions.srt

# 5. Smart multi-aspect reframing (16:9 -> 9:16 Shorts/Reels/TikTok)
agentcut reframe out.mp4 9:16 vertical.mp4 --mode blur_pad

# 6. Launch the interactive HTML5 Web Studio & Templates Gallery
agentcut studio examples/basic.json studio.html

# 7. Start the stdio Model Context Protocol (MCP) server (34 tools)
agentcut-mcp
```

**Requirements:** Python 3.10+, `ffmpeg` and `ffprobe` on `PATH`. Optional: `edge-tts` for neural speech synthesis (comes with built-in procedural audio fallback). **Zero Python dependencies outside the standard library.**

---

## ✨ The Visual Leap (`v1.60.0`): Studio-Grade Vector UI & Cinematic Pipeline

With **v1.60.0**, AgentCut elevates visual output from basic geometry boxes to commercial television aesthetics (*Vox*, *Bloomberg*, *الاقتصاد الكوكبي*) while strictly preserving our **Zero External Python Dependencies** constraint:

1. **Pure-Python RGBA Vector UI Engine (`src/agentcut/vector_ui.py`):**
   - **Glassmorphism Cards:** Sub-pixel anti-aliasing via signed distance fields (SDF), rounded corners (`border_radius`), vertical gradients, glowing rims (`border_color`), and soft drop shadows (`drop_shadow`).
   - **Rounded Chart Bars & Pill Badges:** Smooth top-curved bar charts and circular pill badges generated in milliseconds using pure Python `struct` + `zlib`.
   - **Deterministic Disk Caching:** Every vector asset is hashed and stored in `.agentcut_cache/` for instantaneous reuse.

2. **Cinematic B-Roll Engine (`src/agentcut/broll.py`):**
   - **Multi-Source Scene Backgrounds:** Accepts local video clips (`.mp4`, `.mov`), photos, or automated stock video queries via Pexels/Pixabay API.
   - **Depth of Field & Contrast Isolation:** Automated FFmpeg filter pipeline (`scale`, `crop`, gentle `zoompan`, `gblur=sigma=6.0`, `eq=brightness=-0.22`, `vignette=PI/4`) ensuring that background footage breathes life while foreground Arabic typography and vector cards maintain 100% legibility.

3. **Active Word Karaoke Captions (`karaoke_pill`):**
   - **Real-Time Word Highlighting:** Dynamic word-by-word tracking with active word 10% font enlargement (`\fscx110\fscy110`) and vibrant accent highlight.
   - **Strict Arabic BiDi & HarfBuzz Safety:** Subtitle run-ordering specifically tuned for RTL paragraph layout without character disconnections.

---

## 🔀 OTIO Export (`v1.61.0`): Projects Open in Real Editors

With **v1.61.0**, AgentCut is no longer an island: `export_otio`
(MCP tool, or `agentcut otio project.json timeline.otio` on the CLI)
writes an OpenTimelineIO (`.otio`) timeline that opens as an editable
timeline in DaVinci Resolve (free edition), Premiere, Final Cut (via
OTIO adapters) and Kdenlive. OTIO is Apache-2.0 and its format is
JSON, so the exporter writes it with the stdlib `json` module only —
no OTIO library code is copied, no new dependency is added.

What the export carries: cuts, clip order, exact timing (frame
quantized at the project fps, total duration matches exactly),
transitions (as dissolves — wipes and slides keep their timing with
the shape substituted), chapter markers, track-1 overlays as V2+
tracks, audio tracks, and media references (relative paths
preserved). Speed, speed curves, freeze frames and loops are mapped
to OTIO time warps and repeated clips.

What it does NOT carry (OTIO cannot hold it): effects, grades,
treatments, texts, captions, diagrams, motion graphics, overlays,
Ken Burns/camera moves, reframe, SFX and adjustments. The receiving
editor rebuilds those by hand; every dropped feature is listed in
the export warnings. Resolve import itself is unverified in this
environment — open the file in Resolve to confirm.

---

## 🎬 Cinematic, Fixed and Alive (`v1.63.0`): Bundle Karaoke, the Pill on Every Cue, Continuous Motion

With **v1.63.0** the cinematic compositor grows up: `produce_explainer`
takes a scene-level `word_timings` key — the Whisper timings from an
AgentMedia scene bundle feed the karaoke path directly (the explicit
key wins over in-tool TTS timings; malformed timings are rejected with
a clear error, out-of-range ends clamp to the scene duration). The
subtitle pill that vanished after the first cue is fixed — every cue
gets its own pill, sized to that cue's line width, with a 0.12s fade —
and the frame never freezes: the background Ken Burns is a smoothstep
zoom over the whole scene (the generated gradient moves too), the
foreground subject breathes (±2% scale, 4s period), and glass cards
slide up 18px with a spring ease + fade instead of hard-cutting in and
out. The default keyword card is now a real card: at least 60% of the
frame wide, a coloured accent bar, the keyword as the title, a short
subtitle line, and a soft shadow. Verified end to end with real assets
by `scripts/verify_real_cinematic.py` (AgentMedia bundle → two 720x1280
renders → pill, motion, speech and karaoke assertions).

---

## 🎬 3-Layer Cinematic Compositor (`v1.62.0`): From-Scratch Videos That Look Directed

With **v1.62.0**, `produce_explainer` and `produce_video` gain a
3-layer cinematic compositor (`src/agentcut/compositor.py`, stdlib
only). Pass `style: "cinematic"` in the brief — the blackboard stays
as the explicit opt-in classic, and existing briefs render as before
unless they opt in.

- **Layer 1 — background with depth**: per-scene B-roll (local files
  only, e.g. from AgentMedia) with soft depth-of-field blur,
  darkening and vignette so Arabic text stays legible, plus slow Ken
  Burns cycled per scene (max 1.12x). With no B-roll and no background
  file, a rich cinematic gradient background is generated — never a
  flat solid colour, never the black board.
- **Layer 2 — foreground subject**: a background-removed transparent
  PNG (from AgentMedia `remove_background`) placed centre or on a
  third, with a spring entrance and a measured punch-in every ~5s
  (absolute scale, max 1.15x base — never stacked on Ken Burns).
- **Layer 3 — analytical layer + Arabic text**: glass cards and
  MotionCanvas counters/charts as complements over the scene, with
  the Vertical Zone Manager enforced (header 5–18%, diagram 22–56%,
  callout 60–70%, subtitles 76–92%) so cards never collide with
  captions. The Arabic text path (HarfBuzz, BiDi, Noto Sans Arabic
  Bold) is untouched.

Design change: the 1.39.0 punch_in rejection is superseded for the
compositor's foreground-subject punch-ins ONLY (tool-generated
keyframes, absolute scale); a brief-level `punch_in` is still
rejected everywhere.

Also in 1.62.0: the parallel path's one-frame audio shift is fixed
(concat FILTER join — regression-tested with beep onsets); draft
renders place text correctly at `scale_factor < 1.0` (full renders
byte-identical to before); the scene cache is wired into the render
path (`cache_hits` in the result); cache and reframe share one API
each; and grain gets `treatment.grain_mode` (`off` | `frozen` |
`temporal`, default `frozen`). Measured on the 30s 720p benchmark:
temporal 124.5s / ~31 MB, frozen 50.8s / ~4.5 MB, off 70.0s — frozen
keeps the grain look and is the fastest, because the grain cost is
mostly in the x264 encoder and a frozen pattern is perfectly
predictable across frames. Grain is off by default in draft mode.

---

## 🎨 Turnkey Multi-Genre Content Templates Engine (`v1.50.0` - `v1.60.0`)

AgentCut provides **8 domain-tuned production templates** (`src/agentcut/templates.py`). Instead of handcrafting lower thirds, diagram geometries, color codes, transitions, and audio ducking depths, agents can invoke any turnkey genre:

| Template ID | Genre / Domain | Visual Look & Palette | Captions & Pacing | Diagrams & Motion Elements |
|---|---|---|---|---|
| **`finance_data_story`** | المال والاقتصاد وتحليل البيانات | Deep slate glassmorphism (`#0F172A`), gold/cyan accents | `karaoke_pill` active highlight, whip cuts | `line_chart`, `bars`, `comparison_cards`, live `counter` with `٪` |
| **`history_documentary`** | الوثائقيات والتاريخ والقصص | Warm cinematic grading, subtle film grain & vignette | Calm subtitles, `light_leak` / `crossfade` | Chronological `timeline`, quote cards (`style: "quote"`), slow Ken Burns |
| **`tech_code_explainer`** | التقنية والبرمجة والذكاء الاصطناعي | Modern dark developer theme (`#090D16`, emerald `#10B981`) | `typewriter` titles, `glitch` transitions | `process_flow` architecture cards, `progress_bar`, `badge` plugins |
| **`top_list_countdown`** | قوائم الترتيب وأفضل 5 (Top-N) | High-energy vibrant palette | `karaoke_pill` active highlight, punchy whoosh SFX | Numbered countdown ranking badges (`#5` ➔ `#1`), comparison cards |
| **`news_breaking_brief`** | الأخبار العاجلة والملخصات السريعة | Broadcast crimson/navy/white (`#EF4444`, `#0F172A`) | Fast cuts, boxed pill captions | Persistent bottom `progress_bar`, `lower_third` speaker banners, location `badge` |
| **`educational_lesson`** | الدروس التعليمية والكورسات المصغرة | Academic slate & indigo with structured chapters | `highlight_sweep` on keywords | Breakdown cards, `process_flow`, automated **Final Summary Card** |
| **`podcast_audiogram_quote`** | مقاطع البودكاست والاقتباسات | Spotlight dark backdrop, minimalist framing | Centered kinetic `quote` typography | Top speaker/topic `badge`, continuous audio progress indicator |
| **`product_promo_showcase`** | عروض المنتجات والشركات الناشئة SaaS | High-conversion triadic harmony, clean gradients | `karaoke_pill` dynamic pacing | 4-stage funnel (`Problem` ➔ `Solution` ➔ `Proof` ➔ `CTA Badge`) |

### Instant Brief Inheritance
Pass `"template": "<template_id>"` inside any brief. AgentCut automatically fills color palettes, layout zones, typography presets, ducking parameters, and diagram selections:

```json
{
  "template": "tech_code_explainer",
  "title": "سباق الذكاء الاصطناعي في 2026",
  "aspect": "9:16",
  "scenes": [
    {
      "title": "صراع العمالقة",
      "narration_text": "شَهِدَ عَامُ أَلْفَيْنِ وَسِتَّةٍ وَعِشْرِينَ قَفْزَةً غَيْرَ مَسْبُوقَةٍ فِي نَمَاذِجِ التَّفْكِيرِ الفَائِقِ.",
      "role": "hook"
    },
    {
      "title": "مقارنة الأداء",
      "narration_text": "تَفَوَّقَتِ النَّمَاذِجُ المَفْتُوحَةُ المَصْدَرِ عَلَى النَّمَاذِجِ المُغْلَقَةِ فِي الكَفَاءَةِ.",
      "diagram": {
        "template": "comparison_cards",
        "params": {
          "items": [
            {"title": "النماذج المفتوحة", "subtitle": "كفاءة تشغيل 85%"},
            {"title": "النماذج المغلقة", "subtitle": "دقة حوسبة 92%"}
          ]
        }
      },
      "role": "data_proof"
    }
  ]
}
```

---

## 🎙️ Broadcast Audio Mastering & Vocal Engineering

AgentCut delivers pristine acoustic quality compliant with streaming and broadcast delivery standards:

1. **Arabic Vocal Precision & Homograph Disambiguation:**
   - Spoken scripts automatically resolve ambiguous Arabic homographs via `auto_diacritize_arabic` (e.g. distinguishing `ذَهَبَ` from `ذَهَبْ`).
   - **Audio/Subtitle Decoupling:** Spoken narration retains rich Tashkeel for flawless neural TTS pronunciation, while on-screen subtitles and captions are cleaned automatically (`clean_captions=True`) to maintain modern readability.
2. **Broadcast Voice Mastering Filter Chain:**
   - Narration tracks are processed through a 5-stage vocal mastering chain:
     `highpass=f=80, equalizer=f=220:t=q:w=1.2:g=-2.5, equalizer=f=3800:t=q:w=1.0:g=3.0, treble=g=2.0:f=8000, acompressor=threshold=-18dB:ratio=3:attack=15:release=180:makeup=2.5dB`
3. **Master Loudness Normalization:**
   - Linear two-pass EBU R128 loudness normalization targeting **`-14.0 LUFS`** and **`-1.0 dBFS`** true peak with high-bitrate 192 kbps 48 kHz AAC encoding.
4. **Intelligent Dynamic Ducking:**
   - Ambient music beds automatically duck under spoken voice with configurable attenuation (`duck_amount_db`, default -14 dB) and gentle attack/release curves.

---

## 📐 Motion Graphics, Kinetic Subtitles & Layout Engine

- **HarfBuzz Pixel-Advance Measurement & Auto-Downscale:**
  Titles, scene headers, and keyword callouts are measured in-memory with HarfBuzz. If text width exceeds `86%` of frame width, AgentCut automatically scales down font size and wraps onto multiple lines cleanly—guaranteeing **zero horizontal clipping** across 720p, 1080p, and 4K.
- **Mixed-Script Font Fallback:**
  Arabic and Latin mixed texts (e.g., technical acronyms, API names, product labels) resolve glyph-by-glyph using unified Unicode font matching, completely eliminating missing glyph boxes (Tofu `☐`).
- **Kinetic Karaoke Subtitles (`libass`):**
  Word-by-word synchronized highlight with smooth spring transitions, custom outline weights, translucent background pills, and correct RTL bidirectional shaping.
- **Physics-Based Spring Animations:**
  Lower-third entrances, bar/donut expansions, counters, and callout pop-ins utilize damped harmonic spring physics (`stiffness=180, damping=14, mass=1`) for organic motion.
- **Glassmorphic Cards (`backdrop_blur`):**
  Dynamic regional boxblur (`16:2`), tinted slate scrim (`rgba(15,23,42,0.68)`), rounded borders, and specular highlights rendered via fast cached alpha masks.

---

## 🖥️ Interactive Web Studio GUI (`studio.py`)

Generate a single, zero-dependency, self-contained HTML5 file (`studio.html`) providing a complete visual inspection suite:

1. **🎬 Multi-Track Interactive Timeline:** Visual tracks for Scenes, Narration, Subtitles, Motion Graphics, and Audio Beds with synchronized scrubber and canvas preview.
2. **🎨 ProCraft Color Harmony & WCAG Inspector:** Real-time contrast ratio evaluation (AAA/AA badges) across background, card, primary, and accent colors.
3. **📊 Text Metrics & Pacing Analyzer:** Syllable count, character-to-word ratios, Arabic diacritic density, reading duration estimation, and WPM pace gauges.
4. **📐 Turnkey Templates Gallery:** Interactive visual browser for all 8 content genres with live color dots, WCAG badges, and one-click JSON brief generators.
5. **📦 Token-Minified Spec Viewer:** Instant minification and formatting tools designed for efficient AI agent prompt cycles.

---

## ⚡ Hardware Acceleration & Parallel Rendering

- **Universal GPU Detection:** Automatically discovers and routes encoding to the fastest available hardware acceleration:
  - **NVIDIA:** NVENC (`h264_nvenc`)
  - **Intel:** QuickSync (`h264_qsv`)
  - **Windows:** Media Foundation (`h264_mf`)
  - **macOS:** VideoToolbox (`h264_videotoolbox`)
  - **Software:** Highly tuned `libx264` (CPU fallback)
- **Parallel Chunk Rendering:** Explainer timelines partition into independent scenes, render across available CPU/GPU cores concurrently, and stitch via seamless FFmpeg concat with preserved cut transitions (`zoom_out`, `whip_pan`, `glitch`, `light_leak`).
- **Draft Mode:** Pass `draft: true` to generate ultra-fast low-resolution preview proxies for agent verification loops in seconds.

---

## 🧰 The 34 Model Context Protocol (MCP) Tools

AgentCut exposes **34 structured tools** over stdio MCP:

| Domain | Available MCP Tools |
|---|---|
| **Templates & Automation** | `list_content_templates`, `produce_from_template`, `produce_explainer`, `produce_video`, `produce_variants` |
| **Speech & Audio** | `synthesize_speech`, `list_voices` |
| **Design & ProCraft** | `generate_color_palette`, `analyze_text_metrics`, `generate_web_studio`, `generate_glass_card_preview` |
| **Video Production & Reframing** | `render_project`, `preview_project`, `validate_project`, `reframe_video`, `split_clip`, `timeline_summary`, `check_brief`, `probe_media` |
| **Inspection & QA** | `poster_frame`, `render_contact_sheet` (12-frame contact sheet grid) |
| **Project Management** | `create_project`, `get_project`, `list_projects`, `patch_project` |
| **Snapshots & Rollbacks** | `snapshot_project`, `rollback_project`, `list_snapshots` |
| **Asynchronous Jobs** | `start_render_job`, `get_job`, `get_job_status`, `cancel_job` |
| **Plugins** | `list_plugins` |
| **Interchange** | `export_otio` (OpenTimelineIO `.otio` export — opens in DaVinci Resolve, Premiere, Final Cut, Kdenlive) |

---

## 📐 Project JSON Schema (v1)

```json
{
  "version": 1,
  "id": "explainer-video",
  "output": {"width": 1080, "height": 1920, "fps": 30},
  "clips": [
    {
      "id": "c1",
      "kind": "video",
      "src": "/path/clip.mp4",
      "trim_in": 0,
      "trim_out": 4.5,
      "transition": "crossfade"
    },
    {
      "id": "c2",
      "kind": "image",
      "src": "/path/slide.png",
      "duration": 5.0,
      "camera": [
        {"t": 0, "x": 0.5, "y": 0.5, "scale": 1.0},
        {"t": 5, "x": 0.5, "y": 0.45, "scale": 1.15}
      ]
    }
  ],
  "texts": [
    {
      "text": "عنوان رئيسي مميز",
      "start": 0.5,
      "end": 4.0,
      "size": 54,
      "font": "noto-sans-bold",
      "animation": "pop_in"
    }
  ],
  "captions": [
    {"text": "مرحبا بكم في عالم صناعة المحتوى الذكي", "start": 0, "end": 3.2}
  ],
  "audio": [
    {"src": "/path/narration.mp3", "kind": "voice", "volume": 1.0},
    {"src": "/path/ambient.mp3", "kind": "music", "volume": 0.35, "ducking": {"amount_db": 14}}
  ]
}
```

---

## 🧪 Testing & Verification

AgentCut enforces a comprehensive test suite executed across every push and pull request:
- **481 automated tests** verifying mathematical pixel geometry, HarfBuzz text shaping, ASS karaoke timing, FFmpeg filter graph construction, EBU R128 loudness compliance, and template schema integrity.
- **Empirical Frame Auditing:** Contact sheet generation and pixel-difference assertions ensure zero visual artifacts, zero clipping, and 100% deterministic reproducibility.

```sh
# Run full unit & integration suite
pytest tests/ -v
```

---

## 🔒 Security & Trust Model

AgentCut runs entirely locally with zero telemetry and zero external network transmission of media or data. All assets, scripts, audio tracks, and output videos remain strictly on the host filesystem. An opt-in path sandbox (`AGENTCUT_SANDBOX=1`) restricts filesystem access to specified project directories for untrusted execution environments.

---

## 📜 License

AgentCut is licensed under the **Business Source License 1.1** — see [LICENSE](LICENSE). Free for personal, internal, and commercial use. On **2030-10-28**, this version automatically converts to the **Apache License, Version 2.0**.

---

**Built with pride for the autonomous agent era.**  
Maintained by [@ahmadshady747-create](https://github.com/ahmadshady747-create).
