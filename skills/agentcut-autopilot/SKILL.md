---
name: agentcut-autopilot
description: Run AgentCut v3.2.0 in single-call autopilot mode from raw Arabic or English text, messy JSON, or a partial brief. Use when the user wants fast end-to-end documentary video production without manually crafting timeline JSON or calling multi-step tools.
---

# AgentCut Autopilot

Turn raw prose, notes, or messy JSON into a cohesive, cinema-grade documentary blueprint or finished MP4 in one deterministic call.

## When to Use

- The user pastes a raw Arabic or English essay script and wants a video plan or rendered MP4.
- A lightweight LLM produced rough or malformed brief JSON that needs repair and polishing.
- You want the shortest, lowest-error path through AgentCut v3.2.0.

## Steps

1. Call `autopilot_produce` (MCP) or run `agentcut autopilot <input> [out.mp4] --look chiaroscuro_doc --profile low_spec`.
2. Inspect the returned `mode`:
   - If `mode == "blueprint"`, hand `visual_prompts.prompts` and `brief.scenes` to AgentMedia (or `agentcut tts`) to generate narration audio and images, then call `autopilot_produce` again with `output` set.
   - If `mode == "rendered"`, verify `auto_heal.qc.passed` is `true` and `craft_audit.passed` is `true`.
3. For final visual verification, generate a contact sheet with `render_contact_sheet` and inspect framing and Arabic subtitle legibility.

## Gotchas

- Never hand-code FFmpeg filters or pixel coordinates when `autopilot_produce` can handle them deterministically.
- On 8GB RAM CPU-only machines, always keep `profile="low_spec"` (or `"proxy"` for quick layout checks) and avoid heavy film grain.
- Always pass local file paths for narration audio before requesting a final render; otherwise autopilot safely returns a blueprint.
