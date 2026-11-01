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
./run.sh validate examples/basic.json   # validate first, always
./run.sh render examples/basic.json out.mp4
./run.sh serve                          # MCP server on stdio
```

**Requirements:** Python 3.10+, `ffmpeg` and `ffprobe` on PATH. **Zero Python dependencies. Zero network calls for media.** All sources are local files.

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

### 🎥 Timeline & editing
Sequential clips (video trims, image durations), transitions (`crossfade`, `fade`, wipes, slides, `dissolve`, `zoom`, `wipe_tl`, `squeeze_h` and more), split / reverse / freeze / loop, speed and speed curves, overlays with keyframes, masks, chroma key, blurred-background fit (`blur`, `blur_pad`), two-pass stabilization, and outputs up to 3840 wide with timelines up to 600 seconds.

### 🎬 Camera & motion — new in 1.40.0
- **Camera paths:** `camera: [{t, x, y, scale}]` — a smooth multi-stop pan/zoom glide on any clip, capped at 1.25× so it never feels like the old 50% zoom.
- **Parallax:** overlays drift with `depth` 0..1 against camera motion — foreground faster than background.
- **Motion continuity:** clips sharing a `scene_id` chain their scales across cuts, so a scene glides instead of jumping.
- **Gentle by default:** Ken Burns is a 12% eased drift, punch-ins are absolute (never stacked), and stills get automatic micro-motion unless you say otherwise.

### ✍️ Text & captions
Word-reveal texts, animated texts (`slide_in`, `slide_out`, `pop`), kinetic word-highlight captions with measured word widths, ASS export with fades and karaoke, wrapped captions, scrims, rounded pills, borders, shadows and safe-area warnings. **Arabic is first-class:** Noto Naskh Arabic shaping verified by eye, BiDi-correct line order (digit-led lines included), word-only reveal so letters stay joined. Write percents as ٪ (U+066A) — `%` and `#` have no glyph in the Arabic fonts here.

### 🎵 Audio
Voice + music with a real sidechain compressor, or deterministic ducking windows, or `auto_duck` — with one exact precedence: an explicit `ducking` object always wins, otherwise voice + music gets the sidechain, and `auto_duck: false` passes music through untouched. Per-track loudnorm (−16 LUFS), a final-master option (−14 LUFS), procedural SFX (`pop`, `whoosh`, `cash`, `click`, `ding`, `thud`) fired at element starts, and producer QA reporting loudness, peak and freeze segments.

### ✏️ Diagrams that draw themselves
Stroke diagrams (`polyline`, `arrow`, `circle`, `ellipse`, `rect`, `line`, `arc`) or templates (`chart`, `basket`, `balance`, `causes`, `primitives`) that draw stroke-by-stroke with feathered chalk edges and eased progress — then hold the finished frame exactly. Labels are always AgentCut text, never baked into the art, so Arabic stays on the verified BiDi path.

### 📦 Delivery & verification
Chapters in file metadata, delivery presets (`youtube_1080`, `shorts_720`, `instagram_square`) that never override your explicit settings, poster-frame export, preview proxies with title-safe / action-safe guides and timecode, and a `qa` block on every produce result (duration, resolution, pixel format, black-frame and silence checks — warnings, never silent failures).

### 📱 On your own phone
The agent can't run code on your phone, so it builds a self-contained render pack — project, media, source, fonts, `render.sh` — and you render it in Termux with no network, no server, no checkout. Full guide: [`docs/ON_DEVICE.md`](docs/ON_DEVICE.md).

---

## 🧰 MCP tools (22)

**Projects:** `create_project`, `get_project`, `list_projects`, `patch_project`, `validate_project`, `preview_project`, `render_project`, `poster_frame`
**Produce:** `produce_video`, `produce_explainer`, `produce_variants`, `split_clip`, `timeline_summary`, `check_brief`
**Snapshots:** `snapshot_project`, `rollback_project`, `list_snapshots`
**Jobs:** `get_job` (detached jobs that survive the client closing stdio), `start_render_job`, `get_job_status`, `cancel_job` (in-process `bg_` jobs)
**Media:** `probe_media`

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

## 📌 Version

**Current: 1.40.0** — general visual craft (camera paths, parallax, kinetic text, treatment, continuity, new transitions, look profiles) on top of the architectural refactoring and the cinematic/audio polish. Test suite: **159/159**, run in CI on every push and pull request. Full history: [CHANGELOG.md](CHANGELOG.md).
