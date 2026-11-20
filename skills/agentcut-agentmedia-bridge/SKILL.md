---
name: agentcut-agentmedia-bridge
description: Connect AgentMedia audio/visual scene bundles (Whisper word timings, cloned voice narration, generated visuals) directly into AgentCut v3.2.0 editorial rendering. Use when the user mentions AgentMedia, bundle.json, prepare_scene_bundle, or Whisper word_timings.
---

# AgentCut AgentMedia Bridge

Hand off prepared narration, Whisper word timings, and beat visuals from AgentMedia into AgentCut with zero manual timeline assembly.

## When to Use

- AgentMedia has produced a `bundle.json` containing narration WAV/MP3 files, Whisper `word_timings`, and scene images.
- You need natural 2.5 to 4.0 second visual beats aligned to speech pauses.
- You are running batch multi-video production from prepared media folders.

## Steps

1. Call `validate_media_bundle` with `check_files=true` to verify every narration and image path exists before rendering.
2. Call `import_media_bundle` with `profile="low_spec"`, `parallel=true`, and `qc_audit=true`. AgentCut splits scenes into natural speech-pause beats via `auto_split_beats`, applies editorial choreography, renders scenes in parallel with atomic caching, and audits the finished MP4.
3. For multi-client or multi-episode folders, build a batch manifest and call `batch_produce` with `resume=true` and `qc_audit=true`.

## Gotchas

- Never pass raw unnormalized Arabic numerals to TTS upstream; normalize numbers in AgentMedia before generating Whisper timings.
- Keep `cache_scenes=true` enabled so tweaking a single beat re-renders only that scene chunk instead of the entire video.
- Check `get_cache_stats` periodically on HDD systems and trim with `clear_render_cache` when needed.
