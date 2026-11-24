# AgentCut v3.4.5 — Deterministic Browser-Rendered Arabic & English Video Engine

AgentCut v3.4.5 is a 100% browser-rendered motion-graphics video engine for vertical reels (`9:16`), long-form explainers (`16:9`), and square videos (`1:1`).
Every visual pixel — typography, 104 hand-drawn flat SVG icons, 6 multi-layer composite illustrations, live SVG vector charts, camera moves, and kinetic Arabic captions — is rendered deterministically in headless Chromium via `window.setTime(t)`.
FFmpeg is used **strictly** as a dumb encoder (`encode_frames` / `encode_png_stream_pipe` with `-14 LUFS` linear loudnorm) and audio mixer/synthesizer (`mix_audio` + `soundscape.py`).

## Highlights (v3.4.5)

- **Acoustic Beat-Sync (`beat_sync.py`):** Extracts 25ms RMS energy envelopes, vocal emphasis peaks, and natural breath pauses from real narration audio so icons, cards, and micro-SFX pulse on the exact millisecond of vocal emphasis.
- **23 Scene Templates & Live Vector Charts (`flat_reels.py`, `charts.py`):** `intro`, `question`, `concept`, `process`, `statistic`, `comparison`, `conclusion`, `timeline`, `cycle`, `layers`, `callout`, `chapter`, `code`, `split`, `checklist`, `chart_line`, `chart_donut`, `chart_bars`, `before_after`, `funnel`, `matrix_2x2`, `terminal_demo`, and `chapter_toc`.
- **104 Flat SVG Icons + 6 Composite Illustrations (`icons.py`):** Palette-aware vector art with deterministic inner motion classes (`svg-spin`, `svg-pulse`, `svg-wave`, `svg-rise`, `svg-tick`).
- **4 Smart Arabic Caption Styles (`captions.py`):** `word_pop`, `highlighter_bar`, `karaoke_pill`, `editorial_box` with balanced Arabic line chunking (zero 1-word orphans), keyword emphasis, and mixed-script Latin bidi isolation.
- **4 Channel Brand Kits & Multi-Platform Export (`brand_kits.py`, `multi_export.py`):** `tab_ezzay`, `reel_studio_dark`, `arabic_documentary`, and `midnight_tech`. Export `16:9` + `9:16` + `1:1` + high-contrast `Thumbnail PNG` from a single `spec.json`.
- **Surgical Scene Patching & Constrained-Hardware Pipe Mode (`scene_patch.py`, `browser_render.py`):** SHA-256 scene fingerprinting, instant 3-second A/B hook variant generation, `/dev/shm` RAM-disk scratch routing, and direct memory-to-FFmpeg `image2pipe` PNG streaming.
- **Smart Script Director, Preflight Auditor & Mobile Web Studio (`director_browser.py`, `preflight.py`, `studio_web.py`):** Convert raw Arabic text into a varied `spec.json`, audit narration pace (WPS & script-trim recommendations — never audio speed-up), and launch a responsive RTL mobile web studio (`agentcut studio`).

## CLI Quickstart

```bash
# 1. Check environment readiness
PYTHONPATH=src python3 -m agentcut doctor

# 2. Convert raw Arabic script to spec.json
PYTHONPATH=src python3 -m agentcut script-to-spec "جسمك حرارته 37 طول السنة. طب ازاي؟ الجلد بيرسل إنذار مبكر للمخ." spec.json --brand-kit tab_ezzay

# 3. Run pre-render preflight audit
PYTHONPATH=src python3 -m agentcut preflight examples/reel_winter_60s.json --no-audio

# 4. Render video or multi-platform bundle
PYTHONPATH=src python3 -m agentcut render examples/reel_winter_60s.json out.mp4
PYTHONPATH=src python3 -m agentcut multi-export examples/reel_winter_60s.json dist/

# 5. Launch Mobile/Desktop Web Studio
PYTHONPATH=src python3 -m agentcut studio --port 8765
```
