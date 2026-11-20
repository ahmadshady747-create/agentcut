---
name: agentcut-qc-healer
description: Run automated pre-render craft audits, post-render FFprobe/FFmpeg quality control, and closed-loop self-healing repairs on AgentCut videos. Use when verifying a finished MP4, debugging A/V sync drift, fixing clipping or black frames, or preparing client-ready exports.
---

# AgentCut QC Healer

Guarantee broadcast-clean, artifact-free video exports before delivering to YouTube, social platforms, or clients.

## When to Use

- A video render finished and needs objective technical verification before publishing.
- You suspect black frames, dead-air silence gaps, audio clipping, non-square SAR, or A/V sync drift.
- You want an automated retry loop that repairs fixable defects without human intervention.

## Steps

1. Before rendering, run `audit_editorial_craft` on the brief to catch metronomic beats, repetitive camera moves, or card spam.
2. Render through `produce_cinematic_auto_heal` so QC verification and audio loudness repairs run automatically.
3. On any standalone MP4, call `audit_video_quality` (or `agentcut qc <video.mp4> --expected-duration <sec>`) and check `passed`, `score`, `grade`, and `issues`.
4. If `SILENCE_GAPS` appears on raw talking-head or voiceover footage, tighten pauses with `smart_cut_silence`.
5. For visual inspection, call `render_contact_sheet` to review key timestamps at a glance.

## Gotchas

- Always verify `av_drift_ms` is under 150 ms and `sar` is `1:1` before publishing.
- If `AUDIO_CLIPPING` or `AUDIO_TOO_QUIET` fires, enable `master_loudnorm=true` and lower background music volume rather than manually guessing dB offsets.
- Run `detect_hardware_profile` before large batch renders to keep worker concurrency within available RAM.
