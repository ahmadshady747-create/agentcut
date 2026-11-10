# AgentCut 🎬

### The video editor built for agents, not for hands.

[![CI](https://github.com/ahmadshady747-create/agentcut/actions/workflows/test.yml/badge.svg)](https://github.com/ahmadshady747-create/agentcut/actions/workflows/test.yml)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/engine-FFmpeg-green.svg)](https://ffmpeg.org/)

**AgentCut turns a written brief into a finished, checked MP4 — no timeline to drag, no GUI to click, no footage to generate.** An agent describes the video in JSON, validates it before a single frame renders, previews its own frames, and delivers a real video with chapters, captions, diagrams, music and sound design.

> **One sentence:** you write what the video should be; AgentCut makes sure it actually is — then proves it with frames and measurements.

---

## ⚡ Quickstart

```sh
git clone https://github.com/ahmadshady747-create/agentcut.git
cd agentcut
./run.sh validate examples/basic.json                  # validate first, always
./run.sh render examples/basic.json out.mp4           # render full video
./run.sh produce-explainer brief.json out.mp4         # autonomous one-button explainer
./run.sh tts "نص الشرح الصوتي" voice.mp3 --srt out.srt # native neural TTS narration
./run.sh reframe out.mp4 9:16 vertical.mp4            # smart multi-aspect reframing
./run.sh studio examples/basic.json studio.html       # visual timeline inspector
./run.sh palette "#3B82F6" --harmony triadic          # harmonious palette & WCAG contrast
./run.sh text-metrics "نص السيناريو"                  # reading pacing & text composition
./run.sh minify-json examples/basic.json              # token-minified compact JSON
./run.sh serve                                         # MCP server on stdio (28 tools)
```

**Requirements:** Python 3.10+, `ffmpeg` and `ffprobe` on PATH. Optional: `edge-tts` for neural speech synthesis. **Zero network calls for media rendering.** All sources are local files.

---

## 🎯 Why agent-first?

Human editors are built for eyes and hands: drag, scrub, eyeball, repeat. An agent has none of those — so AgentCut gives it what it *can* use:

- **A written spec, not a canvas.** One `project.json` is the whole video: clips, texts, captions, audio, overlays, diagrams, camera, grading.
- **Validation before rendering.** Bad projects fail in seconds with a structured error — `code`, `field`, `clip_id` and a `fix` hint — instead of after a ten-minute render with a cryptic FFmpeg message.
- **A verify loop the agent can close alone.** Render → inspect real frames → patch → re-render. No human needs to watch and report back.
- **Determinism.** The same project renders the same video, so retries and series are safe.
- **Local and free.** FFmpeg on your machine: no per-minute fees, no watermark, no footage leaving your disk.

AgentCut is an original design. It is **not** a CapCut clone, contains no CapCut code, assets, templates or effects, and does not depend on CapCut in any way.

---

## 🪄 One button, one video: `produce_explainer`

The flagship call. One brief in, one finished explainer out — no hand-written `project.json`:

```sh
./run.sh produce-explainer examples/one-button-explainer.json out.mp4
```

```json
{
  "id": "strawman",
  "style": "blackboard",
  "output": {"width": 720, "height": 1280, "fps": 30},
  "background": "/path/to/board_image.png",
  "title": "مغالطة رجل القش",
  "chapters": true,
  "motion": "gentle",
  "scenes": [
    {"id": "s1", "title": "ما هي؟",
     "narration": "/path/voice_s1.mp3", "captions": "/path/s1.srt",
     "diagram": {"template": "causes", "params": {"rows": 2}, "draw": 3.5},
     "keywords": [{"text": "رجل القش", "start": 5.0, "end": 9.0, "sfx": "pop"}]}
  ]
}
```

Scene durations come from the narration audio itself, SRT cues are merged into one captions track, diagrams draw inside their scenes, chapters land on scene starts, and motion stays gentle — `punch_in` is rejected by design. Add `look` (`clean`, `cinematic`, `vintage`, `bold`, `still`) for a full treatment + grade + motion profile on any background.

For simpler jobs, `produce_video` takes a short brief (format preset + media + title + audio) and `produce_variants` renders vertical, square and landscape from one brief.

---

## ✨ What it can do

### 🎙️ Native TTS & Autonomous Voiceovers
- **Zero-setup neural speech synthesis:** Built-in integration with `edge-tts` (multilingual neural voices, natural Arabic accents `ar-EG-SalmaNeural`, `ar-SA-HamedNeural`, English `en-US-ChristopherNeural`, etc.) with graceful automatic fallback to system SAPI / pyttsx3.
- **Microsecond SRT & word-level sync:** Every TTS generation exports synchronized SRT captions with word-level and sentence-level timecodes, automatically piped into scene narration and kinetic subtitles without manual timing.
- **Autonomous scene narration in `produce_explainer`:** When a brief scene provides `narration_text` instead of a pre-recorded audio file, AgentCut synthesizes the voice, measures its exact length to set scene duration, generates cues, and renders the voice track automatically.

### 💥 Kinetic Subtitles & Explainer Diagrams
- **Modern kinetic karaoke ASS engine:** Multi-theme subtitle styling (`beast`, `neon`, `gold`, `minimal`, `clean`) with dynamic word-by-word highlight synchronization, font scaling, outlines, dropshadows, and full BiDi / HarfBuzz Arabic text shaping.
- **Timeline & process-flow diagrams:** Two declarative explainer diagram templates:
  - `timeline`: Milestone nodes connected along a directional progress line with dates, titles, and step highlights.
  - `process_flow`: Numbered sequential step cards with directional chevron connectors and animated progress indicators.
- **Cinematic transitions:** Built-in post-join transition effects (`glitch`, `zoom_out`, `whip_pan`, `light_leak`) straddling cuts to elevate storytelling pace.

### ⚡ GPU Acceleration & Parallel Rendering
- **Automatic hardware encoder detection:** Transparently inspects available encoders and selects GPU-accelerated pipelines — Intel QSV (`h264_qsv`), Windows Media Foundation (`h264_mf`), NVIDIA NVENC (`h264_nvenc`), Apple VideoToolbox (`h264_videotoolbox`), or graceful CPU (`libx264`) fallback.
- **Parallel scene/chunk rendering:** `render_parallel_chunks` partitions independent timeline scenes into concurrent rendering workers and stitches them via fast FFmpeg concat demuxer, achieving massive speedups on multi-core workstations.
- **Draft mode:** `draft: true` flag renders instant low-resolution, high-speed preview proxies for immediate agent self-verification loops.

### 📐 Smart Multi-Aspect Auto-Reframe
- **One video, all platforms:** Convert landscape (16:9) to vertical (9:16 Shorts/Reels/TikTok), square (1:1), or portrait (4:5) in a single call.
- **Multiple framing modes:**
  - `blur_pad`: Centers the content over a dynamically scaled, blurred, and darkened version of itself (standard high-end social media look).
  - `crop`: Centers and crops cleanly to the target aspect ratio without distortion.
  - `fit`: Preserves full dimensions with cinematic black bars (letterbox/pillarbox).
- **Batch variant export:** `reframe_to_variants` exports all desired aspect ratios simultaneously with GPU acceleration.

### 🧩 Dynamic Plugin Architecture
- **Extensible motion graphics:** Subclass `MotionPlugin` in `agentcut.plugins` to create bespoke procedural motion graphic elements (e.g. animated `progress_bar`, `badge`, lower thirds, or tickers).
- **Custom diagram templates:** Register custom domain-specific diagram generators with `register_diagram_plugin` and use them directly in any brief or timeline JSON.
- **Zero-configuration auto-discovery:** Plugins placed in `./plugins/` or `~/.agentcut/plugins/` are automatically discovered, registered, and validated on startup.

### 🖥️ Interactive Web Studio
- **Standalone zero-dependency HTML5 inspector:** Generate a single, fully offline HTML file (`studio.html`) containing an interactive multi-track timeline visualization (Scenes, Narration, Motion graphics, Audio bed).
- **Synchronized playback & scrubber:** Interactive time scrubber, HTML5 canvas preview, and playhead synced across all tracks.
- **Aspect ratio simulator:** Switch instant viewport simulation between 16:9, 9:16, and 1:1.
- **Bilingual & RTL-native:** Full native Arabic RTL layout support, inspector drawer for scene metadata, SRT subtitle cues, and motion element parameters.

### 🎥 Timeline & editing
Sequential clips (video trims, image durations), transitions (`crossfade`, `fade`, wipes, slides, `dissolve`, `zoom`, `wipe_tl`, `squeeze_h` and more), split / reverse / freeze / loop, speed and speed curves, overlays with keyframes, masks, chroma key, blurred-background fit (`blur`, `blur_pad`), two-pass stabilization, and outputs up to 3840 wide with timelines up to 600 seconds.

### 🎬 Camera & motion — new in 1.40.0
- **Camera paths:** `camera: [{t, x, y, scale}]` — a smooth multi-stop pan/zoom glide on any clip, capped at 1.25× so it never feels like the old 50% zoom.
- **Parallax:** overlays drift with `depth` 0..1 against camera motion — foreground faster than background.
- **Motion continuity:** clips sharing a `scene_id` chain their scales across cuts, so a scene glides instead of jumping.
- **Gentle by default:** Ken Burns is a 12% eased drift, punch-ins are absolute (never stacked), and stills get automatic micro-motion unless you say otherwise.

### 🎞️ Motion graphics — new in 1.43.0
- **A layer that stays alive:** project-level `motion` — keyframed shapes (rect/circle/ellipse/line/polygon) with easing, counting numbers, growing bars, sweeping donut/pie segments, a line chart that draws across its whole window with a moving head dot, and lower thirds that slide in, hold and slide out. Pure-stdlib vector rasterizer, zero new dependencies; transparent frames composited above video/diagrams, below captions.
- **Arabic-correct by construction:** counters use a live drawtext `%{eif}` expression (decimals 0/1, Arabic ٪ suffix renders left of the digits); chart labels and lower-third text go through the normal drawtext/BiDi path, never hand-rasterized.
- **Deterministic:** the same motion spec renders byte-identical frames; projects without `motion` are byte-identical to 1.42.0.

### ✂️ Editing application — new in 1.44.0
- **Cut on real analysis, not on guesses:** AgentCut does no analysis and no AI — it consumes the JSON files AgentMedia's tools produce (`detect_beats`, `track_object`, `analyze_reframe`) and applies them deterministically. Project-level `beat_cut: {file, every, window}` splits clips at beat times (segments under 0.2s are dropped, total duration unchanged); overlays and texts accept `track: {file, offset_x, offset_y}` and follow the tracked box every frame (hold first/last outside the track); video clips accept `reframe: {windows_file, target_aspect}` and crop per frame to keep the subject framed.
- **Text that enters like motion:** seven presets on texts/captions — `typewriter` (whole words only, never splits an Arabic word), `slide_in`, `pop_in`, `fade_up`, `kinetic_words`, `highlight_sweep`, `underline_swipe` — all on the shared easing system (`ease_out_back` added). Named styles `display`, `subtitle`, `quote` join the frozen `title`/`heading`/`label`/`caption`/`card`; project-level `typography: {default_style}` opts a whole project in. One shared resolver serves titles, captions, pops, lower thirds and motion labels.
- **Transitions with a point of view:** `zoom_out`, `whip_pan`, `glitch`, `light_leak` as real post-join effects straddling each cut (audio stays plain concat).
- **A second track and adjustment layers:** clips accept `track: 0|1` (track 1 is a full-frame overlay at an explicit `start`, never extends the total); project-level `adjustments: [{start, end, grade?, effects?}]` grade or effect the base video for their window only.
- **Byte-identical when unused:** every new field is opt-in; projects without them render exactly as 1.43.0 did. Test suite: **311/311**.

### ✨ Prune + spring/glass/pacing/contact-sheet — new in 1.45.0
- **Chalk is gone, for real:** the 1.41.0 docstring claimed it, the stamp/grain code was still there — now it is deleted. The old diagram templates (`chart`, `balance`, `basket`, `causes`) are backward-compatible adapters to Motion Layer elements (`line_chart`, `bars`, `donut`, `comparison_cards` — a new vector card row); old projects still validate and render via motion. `produce_explainer` generates motion-only scenes.
- **Word measurement without subprocesses:** `_measure_word_ink` (cropdetect, 2 FFmpeg calls per word) is deleted, replaced by in-memory HarfBuzz shaping (`_measure_text_width`) — shaped-advance sums within 1.02–1.21x of ink width, always ≥ ink. Kinetic chunk widths are proven by the same regression tests.
- **Warm SFX, rounded cards:** sine SFX are replaced by pink-noise whooshes (80–500 Hz, linear afade envelope — the prompt's exponential decayed too fast and left whooshes silent early) and bandpassed noise clicks/dings; only the sub-thud keeps a sine, by design. All boxed texts get rounded pills by default (radius 16, HarfBuzz-measured width); animated/fading/tracked texts keep the sharp drawtext box, which a static pill cannot follow. **Deliberate break:** `examples/basic.json` no longer renders byte-identically to 1.44.0 — the prompt requires ALL sharp boxes replaced.
- **Spring physics:** `spring_ease(t, stiffness=180, damping=14, mass=1)` (~15% overshoot, settled by u≈0.7, verified on a real render) is the default for lower-third entrances, bar/donut growth, counters, `pop_in` and `slide_in`; `cubic_bezier` (CSS-ease as a degree-4 polynomial fit, monotonic, max error 0.022 — the degree-5 fit was rejected, not monotonic) joins the easing system. The spring `u` is clamped to [0,1] in FFmpeg expressions — unclamped, `exp(-7u)` overflowed for late elements (`fontsize overflow` at t=0 for a pop_in starting at 22.7s).
- **Glassmorphism:** `backdrop_blur: true` (default on diagram cards and motion charts) crops the card region, blurs it (boxblur 16:2), tints it `rgba(15,23,42,0.68)`, masks it to a rounded rect (radius 20) and adds a 2px white border at 0.22 alpha. The mask is a pre-rendered PNG + `alphamerge`, not geq — the geq version took 198s for one card vs 9s (22x); the adapter-card render dropped from 461s to 26s.
- **5–7s pacing in `produce_explainer`:** long scenes (≥13s) get a visual beat — 0–5s lower-third scene title (spring), 5–13s the diagram as a live chart in a glass card, 13–20s a counter showing scene progress % (a real derived number), repeating every 20s. Short scenes keep the pre-1.45.0 layout. `pacing: false` opts a brief out.
- **Agent self-inspection:** `render_contact_sheet` (23rd MCP tool) + CLI `contact-sheet <video> <out.png> [project.json]` extracts 12 key frames (chapter starts, diagram completions, keyword/motion/text starts, else evenly spaced) into one 4x3 grid PNG with MM:SS timestamps.
- **Six explainer-video defects fixed:** the default Arabic font is unified to Noto Sans Arabic Bold (drawtext + Noto Naskh corrupted ال into a لا-like form in captions — proven to be drawtext-specific, not HarfBuzz or wrapping; libass was already correct); texts on shapes/cards force `box=0`; multi-line captions get one unified background and reveal lines stay at fixed y; lower thirds right-align Arabic and auto-size to the text (`clamp(text_w + 80, 220, 0.82W)`); `produce_explainer` emits no floating shapes; the master encode was already crf 17 / 192k (the 799 kbps quoted was the phone copy).
- **Performance, measured honestly:** a 10s/300-frame lower third renders in ~1.25s on the Python side (frame cache + bytearray slices); the full encode is ~2.66s. The intermediate-codec change (rawvideo / PNG no-compression) was measured and REJECTED — 3x faster to encode but 107x larger files, which would fill the 512 MB /tmp tmpfs. Dynamic-crop `reveal_mask` was re-proven infeasible on FFmpeg 8.1 (crop w/h frozen at the first frame's value). The first 30s of the real explainer project renders in 534s (0.056x realtime), extrapolating to ~69 min for 233s vs the 113.6-min 1.44.0 baseline — a 1.6x speedup, still far from the <60s target, which the current filter-graph architecture cannot reach. Test suite: **351/351**. *(Note: the 533.8s baseline was later corrected in 1.46.0 — it was a contended outlier; the true 1.45.0 time is ~235s.)*

### ⚡ Performance — new in 1.46.0
- **Zoompan at output resolution:** Ken Burns and image-camera paths no longer upscale stills to 2880x5120 and zoompan at 1440x2560 — they zoompan at the OUTPUT resolution with a 1x cover pre-scale and no lanczos downscale. The video-camera branch is untouched (already output-resolution). Pixel-equivalence: board.png mean 0.147 / max 20; a detailed testsrc2 worst case mean 0.71–1.62 / max 189 — a sub-visible sharpness loss on detailed stills at max zoom, not a motion or framing change (drift smoothness unchanged, eye-checked identical). The Ken Burns look survives.
- **Pre-rendered masks:** the per-frame shadow geq is replaced by a cached 1-frame PNG mask + overlay (pixel-equivalence mean 0.82 / max 4); the per-frame pill geq is replaced by a cached pill PNG (byte-identical, mean 0 / max 0). The vignette mask was investigated and REJECTED — the vignette filter multiplies luma directly (Y*f, including the black offset) and no blend-toward-black overlay reproduces it (measured mean 3.84 / max 11, a systematic error). Explicit enable-gating of the glass boxblur, `_mask_geq` and the overlay shadow gblur was measured and NOT implemented (~0s saving — FFmpeg's pull-based scheduling already skips them outside their windows, and `_mask_geq` is already window-bound).
- **Grain, measured on the real benchmark:** temporal grain alls=10 (the default, unchanged) costs ~53s of the 114.5s benchmark (46%) — it is encoder-side (x264 medium on incompressible temporal noise), not filter-side. alls=3 costs ~33s; static (frozen pattern) 40.1s; off 61.8s. Static grain being faster than off is reported as measured (single run) — the frozen pattern is perfectly predictable across frames. A static-grain option (`treatment.grain_mode`) is NOT implemented without an order.
- **Baseline corrected:** the 1.45.0 R12 baseline of 533.8s does not reproduce (re-runs: 232.2s / 238.9s, mean ~235.5s) — it was a contended outlier. Measured: 235.5s → 202.7s (zoompan) → 114.5s (pill PNG), a 2.06x speedup; the full 233s extrapolates to ~15 min. <60s for a full render is still not achievable with the current filter-graph architecture.
- **Byte-identity:** projects without the changed features render byte-identically (a no-feature project: md5 3a8cc46a… on both trees). `examples/basic.json` (default boxed text → pill) has a different bitstream but pixel-identical decoded frames (mean 0 / max 0) — x264 decides differently for a PNG input vs a colour+geq source; the picture is unchanged. Test suite: **351/351**.

### 🚀 Next-Gen Phases 1–4 & Core Audit Fixes — new in 1.50.0
- **Phases 1–4 from ROADMAP.md completed:** Expanded MCP tools from 23 to 28 with native neural TTS (`tts.py`), multi-aspect auto-reframing (`reframe.py`), visual & motion plugins (`plugins.py`), and a standalone interactive web studio (`studio.py`). Strict Zero External Python Dependencies constraint preserved (`Python stdlib + FFmpeg` only).
- **Six core audit bug fixes:**
  1. Horizontal text clipping prevention with HarfBuzz pixel-advance measurement and auto-downscaling/wrapping for Arabic titles and headers.
  2. Diagram template localization (`balance` Arabic labels `"اليمين"`/`"اليسار"` and `causes` cards vertical centering with custom items).
  3. Line chart peak value boundary clamping preventing card escape and title collisions.
  4. Broadcast voice mastering filter chain (`highpass`, parametric EQs, treble boost, vocal compression) and master loudness normalization target (`-14.0 LUFS` / `-1.0 dBFS`).
  5. Scene transition preservation in parallel chunked explainer renders.
  6. Offline procedural speech fallback (`lavfi` modulated cadence audio + word timings) and Arabic homograph auto-diacritization.

### ✏️ Caption fix — new in 1.46.1
- **SRT captions clipped at 720x1280 in 1.45.0/1.46.0, fixed:** the wrap used a static weight table tuned for Noto Naskh Arabic while the renderer uses Noto Sans Arabic Bold (1.305x wider at the same size) — it packed a 785px line as 558px. The wrap now measures with HarfBuzz on the real render font (`measure_text_width` moved to `srt.py`; `wrap_caption_lines` takes `font_path`; the drawtext font resolver feeds it). The Bold unification stays; only the measurement changed. Proven on fresh frames: caption margins 38/40px at 360p, **116/118px at 720p** (beats 1.44.0's 62px), 149/150px at 1080p. The ASS karaoke path was verified unaffected (no Python wrap; libass lays out the line). `examples/basic.json` (no captions) renders byte-identically across 1.46.0/1.46.1 (md5 0843b0f3…). New regression test class TestLoop1461 (4 tests). Test suite: **355/355**.

### 🔧 Refactoring — new in 1.42.0
- **Arabic that stays joined and in order:** every drawtext carries `text_shaping=1` explicitly; both bundled Arabic fonts were verified (complete GSUB/GPOS, لا ligature present) and no Presentation Forms reshaping exists anywhere — clean UTF-8 only. Kinetic word-highlight is one ASS line in run order with RLM anchors at every tag boundary (`WrapStyle: 2`, RTL alignment), proven at every highlight index by a template-matching regression test.
- **A zone manager, not a pile:** strict non-overlapping bands (9:16 and 16:9) — Header 5–18%, Diagram 22–56%, Callout 60–70%, Subtitle 76–92%. Callouts auto-shift below an active diagram box with ≥40px padding; moves are reported in `info["zone_moves"]`.
- **No stagnation:** diagrams hold `hold_seconds` (default 8s, 0..60) then fade out over 1.0s instead of freezing for the whole scene; still-image clips longer than 12s get automatic multi-stop sub-cuts (wide → zoom → pan → wide, 8–12s per shot); the big main title shrinks to a small top badge after 6s (`shrink_after`, `shrink_size`, `shrink_y`).
- **SFX discipline:** auto-SFX only on chapter transitions, diagram reveals and major callout pop-ins, with a 15s cooldown (chapter > diagram > callout priority on ties), attenuated to −16 dB by default (`sfx_attenuation_db` 0..30; explicit `sfx_volume` always wins), and pitch alternating 1.03/0.97. The plan is visible in `info["sfx_plan"]` (kept, dropped with reasons).
- **Encoding, verified and kept:** master stays crf 17 / preset medium / bt709 / faststart / AAC 192k at 48 kHz. **Phone copy** (for delivery links): `-b:v 800k -maxrate 1000k -bufsize 2000k -preset medium -b:a 128k` — 938 kbps measured (ABR, no -crf; the earlier CRF variant `-b:v 800k -maxrate 1000k -crf 21` ran at 7.0 Mbps on grainy/karaoke content because CRF mode ignores -b:v and -maxrate without -bufsize is unenforced).

### 🎨 Life & typography — new in 1.41.0
- **Sound that is heard:** a `music_bed_inaudible` QA warning when the bed sits >18 dB under narration, plus `duck_amount_db` (explicit ducking > `duck_amount_db` > auto sidechain > plain mix) and denser produce-path SFX.
- **Titles that stay:** kinetic titles hold past their first 5 seconds by default (`hold: false` opts out).
- **Charts that read as charts:** grid values, area fill, and rounded translucent diagram cards, with labels as text, never baked art.
- **A real display font:** Noto Sans Arabic Bold (`font: "noto-sans-bold"`) with a documented type scale; black boxes are opt-in, not the house look.
- **Collision-proof layout:** `resolve_overlaps: true` moves pops below diagrams/labels they overlap, and reports the moves.
- **Mastering:** crf 17, preset medium, bt709 end to end, faststart, linear two-pass loudnorm (−14 default, −16 selectable), AAC 192k at 48 kHz.
- **Native ASS karaoke:** word-highlight renders as one connected libass line, never one drawtext filter per word.
- **Capsule SDF diagrams** (no beading, no staircasing) and **4× sub-pixel camera** on images (2× on video).
- **Modern look:** `produce_explainer` look `modern` — dual-tone canvas, card titles, diagram cards, 10% safe-margin zoning.

### ✍️ Text & captions
Word-reveal texts, animated texts (`slide_in`, `slide_out`, `pop`), kinetic word-highlight captions with measured word widths, ASS export with fades and karaoke, wrapped captions, scrims, rounded pills, borders, shadows and safe-area warnings. Word-highlight now renders as one unified ASS line through libass (per-word drawtext karaoke is gone); display text takes `font: "noto-sans-bold"` for the bold sans face. **Arabic is first-class:** Noto Naskh Arabic shaping verified by eye, BiDi-correct line order (digit-led lines included), word-only reveal so letters stay joined. Write percents as ٪ (U+066A) — `%` and `#` have no glyph in the Arabic fonts here, and neither do `-`, `–` or `—` in Noto Naskh (verified by cmap inspection; `noto-sans-bold` has the hyphen, DejaVu has all three).

### 🎵 Audio
Voice + music with a real sidechain compressor, or deterministic ducking windows, or `auto_duck` — with one exact precedence: an explicit `ducking` object always wins, otherwise voice + music gets the sidechain, and `auto_duck: false` passes music through untouched. `duck_amount_db` (0..30) sits between explicit ducking and the sidechain, and a `music_bed_inaudible` QA warning fires when the bed's effective level under ducking sits >18 dB below narration. Per-track loudnorm (−16 LUFS), a final-master option (−14 LUFS, linear two-pass, −16 selectable via `output.loudnorm_i`), procedural SFX (`pop`, `whoosh`, `cash`, `click`, `ding`, `thud`) fired at element starts under the 1.42.0 discipline — 15s cooldown, −16 dB attenuation by default (`sfx_attenuation_db` 0..30, explicit `sfx_volume` wins), alternating pitch; the plan is in `info["sfx_plan"]`. AAC 192k at 48 kHz, and producer QA reporting loudness, peak and freeze segments.

### ✏️ Diagrams that draw themselves
Stroke diagrams (`polyline`, `arrow`, `circle`, `ellipse`, `rect`, `line`, `arc`) or templates (`chart`, `basket`, `balance`, `causes`, `primitives`) that draw stroke-by-stroke with eased progress — then hold the finished frame for `hold_seconds` (default 8s, 0..60) and fade out over 1.0s, never frozen for the whole scene. Lines are continuous capsule SDF segments (no beading, no staircasing); charts take grid values and an area fill, and any diagram takes a rounded translucent `card` behind it. Labels are always AgentCut text, never baked into the art, so Arabic stays on the verified BiDi path.

### 📦 Delivery & verification
Chapters in file metadata, delivery presets (`youtube_1080`, `shorts_720`, `instagram_square`) that never override your explicit settings, poster-frame export, preview proxies with title-safe / action-safe guides and timecode, and a `qa` block on every produce result (duration, resolution, pixel format, black-frame and silence checks — warnings, never silent failures).

### 📱 On your own phone
The agent can't run code on your phone, so it builds a self-contained render pack — project, media, source, fonts, `render.sh` — and you render it in Termux with no network, no server, no checkout. Full guide: [`docs/ON_DEVICE.md`](docs/ON_DEVICE.md).

---

## 🧰 MCP tools (28)

**Projects:** `create_project`, `get_project`, `list_projects`, `patch_project`, `validate_project`, `preview_project`, `render_project`, `poster_frame`, `render_contact_sheet`, `generate_web_studio`
**Produce:** `produce_video`, `produce_explainer`, `produce_variants`, `split_clip`, `timeline_summary`, `check_brief`, `reframe_video`
**Speech & Audio:** `synthesize_speech`, `list_voices`
**Design & Analytics:** `generate_color_palette`, `analyze_text_metrics`
**Snapshots:** `snapshot_project`, `rollback_project`, `list_snapshots`
**Jobs:** `get_job` (detached jobs that survive the client closing stdio), `start_render_job`, `get_job_status`, `cancel_job` (in-process `bg_` jobs)
**Media:** `probe_media`
**Plugins:** `list_plugins`

Every error is a JSON object with `code` and `message`, plus `field` and `clip_id` when they apply. The full agent guide lives in [`SKILL.md`](SKILL.md).

---

## 📐 Project format (v1)

```json
{
  "version": 1,
  "id": "my-first-cut",
  "output": {"width": 720, "height": 1280, "fps": 30},
  "clips": [
    {"id": "c1", "kind": "video", "src": "/path/clip1.mp4",
     "trim_in": 0, "trim_out": 3, "transition": "crossfade"},
    {"id": "c2", "kind": "image", "src": "/path/poster.png", "duration": 2,
     "camera": [{"t": 0, "x": 0.5, "y": 0.5, "scale": 1.0},
                {"t": 2, "x": 0.5, "y": 0.4, "scale": 1.2}]}
  ],
  "texts": [{"text": "Hello", "start": 0.5, "end": 2.5, "size": 48}],
  "captions": [{"text": "First line", "start": 0, "end": 2}],
  "audio": [{"src": "/path/voice.mp3", "kind": "voice", "volume": 1.0}]
}
```

Rules that matter: video clips need `trim_in`/`trim_out` inside the source duration; image clips need `duration`; output is 16..3840 per side, fps 1..60, total at most 600s; captions must not set `x`/`y` (use `position`); warnings (overlap, overflow, static shots, safe-area) never block a render — errors do.

---

## 🗂️ Examples that actually render

- `examples/basic.json` — vertical 360×640, 8.0s, video + image + text + captions
- `examples/one-button-explainer.json` — the one-button producer, repo media only
- `examples/stroke-diagram.json` — chart + basket diagrams over a board still
- `examples/visual-rhythm.json` — SFX, micro-motion, punch-in, active Arabic caption
- `examples/text-polish.json` — wrapped captions, scrims, pill titles, dressed overlays
- `examples/blackboard.json` / `examples/animated.json` — board and slideshow templates

---

## 🔒 Trust model

This server runs locally and trusts the agent that runs it, exactly like other local MCP servers. It reads local files the agent names and runs FFmpeg on them. Do not expose it unauthenticated over a network, and do not point it at media you have not checked. A hosted version would need a sandbox, an allow-list, quotas and authentication first. An opt-in path sandbox exists (`AGENTCUT_SANDBOX=1`); segment caching is a qualified Python-only API, not a general render path.

---

## 📜 Licence

AgentCut is licensed under the **Business Source License 1.1** — see [LICENSE](LICENSE). In short: you may copy, modify and use it, including in production, except to provide a competing agent-first video editor service to third parties. On the Change Date (**2030-10-28**) this version converts to the **Apache License, Version 2.0**. BSL 1.1 is not an Open Source licence.

---

## 📌 Version & Roadmap

**Current: 1.50.0 — Next-Gen Phases 1-4 & ProCraft Integration Complete** — Features native neural TTS voiceovers, ASS kinetic subtitles, timeline/process diagrams, GPU hardware acceleration & parallel rendering, multi-aspect auto-reframing (`blur_pad`/`crop`/`fit`), dynamic motion & diagram plugin architecture, interactive zero-dependency HTML5 Web Studio inspector, ProCraft color harmonies & WCAG evaluation, text reading metrics & script diffing, token minification, deterministic scene caching, and 28 MCP tools. Total test suite: **392/392 passed**, run in CI on every push and pull request. See [docs/ROADMAP.md](docs/ROADMAP.md) for full architectural roadmap and test verification history. Full history: [CHANGELOG.md](CHANGELOG.md).
