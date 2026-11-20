---
name: agentcut-editorial-director
description: Direct human-feeling Arabic and English documentary video essays in AgentCut v3.2.0 using cinema color science, organic handheld camera motion, procedural soundscapes, and anti-robotic craft auditing. Use when the user asks to improve pacing, break robotic slideshow feel, or direct a cinematic essay.
---

# AgentCut Editorial Director

Elevate mechanical edits into human-directed documentary essays with varied rhythm, cohesive color science, and subtle sound design.

## When to Use

- The user says a video feels robotic, flat, or like automated slides.
- You are directing a philosophical, historical, social, or technical Arabic video essay.
- You need cohesive visual prompts, cinema color grades, chapter cards, or room tone.

## Steps

1. Convert raw prose with `direct_from_script` or upgrade an existing brief with `humanize_brief` using a cohesive `cinema_look` (`chiaroscuro_doc`, `kodak_warm`, `noir_slate`, `warm_archive`, `midnight_tech`, or `editorial_clean`).
2. Generate cohesive Style Bible image prompts via `generate_visual_prompts` so consecutive shots alternate scale (wide, medium, close-up, silhouette) and avoid text or cutout artifacts.
3. Run `audit_editorial_craft` before rendering and confirm a score of 85 or higher (varied beat durations, zero consecutive duplicate camera moves, mixed transitions, restrained `highlight_card` spacing).
4. Use `render_chapter_card` for major act dividers and `render_parallax_clip` when foreground RGBA layers are available.
5. Render with `produce_cinematic_auto_heal` so focal points, shot exposure matching, and live `room_tone` plus SFX mixing run automatically.

## Gotchas

- Editorial mode enforces full-sentence plain pill captions; never enable karaoke or floating cutouts in editorial briefs.
- Space `highlight_card` overlays at least two scenes apart so the frame stays clean and readable.
- Use `detect_focal_point` and `match_shot_exposure` before final export to prevent jarring bright-to-dark jumps between cuts.
