# AgentCut skill — for agents that edit video with AgentCut

AgentCut is an agent-first video editor. You do not open a timeline.
You write a project JSON, validate it, preview it, then render it.

## Start the server

```sh
cd ~/workspace/agentcut
./run.sh serve
```

It speaks MCP over stdio: initialize, tools/list, tools/call, ping.
Every tool result is a JSON string in a text content block. Every
failure is a JSON object with `code` and `message`.

## The workflow an agent must follow

- Probe every source first with `probe_media`. Never guess a duration.
- Build the project with sequential clips only. A video clip is a
  trimmed slice of one source; an image clip is a still held for a
  duration. There are no free-form tracks in v1.
- Call `validate_project` (or `create_project`, which validates too)
  and fix every error before rendering. Validation is cheap; a failed
  render is not.
- Call `preview_project`, look at the returned frames and contact
  sheet, and only then call `render_project` for the final MP4.
- Poll `get_job` until status is `done` or `failed`. Jobs are detached processes: closing stdio cannot kill them, and a new connection can poll them. Never present a
  queued or running job as a finished video.
- After `done`, verify the output with the probe data in the job
  result: duration, width, height, video and audio streams.

## Project shape (v1)

- `version`: always 1. `id`: `[A-Za-z0-9_-]+`.
- `output`: width 16..1920, height 16..1920, fps 1..60.
- `clips`: non-empty, sequential. Video clips need `trim_in` and
  `trim_out` inside the source duration. Image clips need `duration`.
  `transition` is `none`, `fade` (fade to/from black), or an xfade
  transition (`crossfade`, `wipe_*`, `slide_*`; overlap defaults to
  0.5s, set `transition_duration` per incoming clip, max 2.0s, less
  than both clip durations; text times are final-timeline
  coordinates after overlaps).
- `texts`: overlays with `text`, `start`, `end`, `size`, `x`
  (pixel number or `center`), `y` (pixels), optional `color`,
  `box`, `position` preset, `fade_in`/`fade_out` seconds.
- `captions`: like texts, but centred near the bottom by default.
  `captions_file` points at an SRT file whose cues are appended
  to `captions` and validated against the timeline. Project fields
  `caption_color`, `caption_size`, `caption_position` are defaults
  for every caption that does not set its own value.
- `audio`: `kind` `voice` or `music`, `volume` 0..2, optional
  `start` in seconds (default 0, must be before the timeline end),
  `fade_in`/`fade_out`, `loudnorm` (true/false, single-pass -16 LUFS).
  When voice and music are both present, music is ducked with a real
  sidechain compressor keyed off the voice mix — unless the
  project sets an explicit `ducking` object (see Pro pack), which
  replaces the sidechain with deterministic volume automation.
- Clips accept `brightness` (-1..1), `contrast` (0..2), `saturation`
  (0..3), `grayscale` (true/false). Renders are atomic: a failed
  render never leaves a partial file or overwrites a previous good
  output. Image clips are pre-scaled to output size before the main
  filter graph.
- Total duration at most 600 seconds. All `src` paths are local files.

## Patching

`patch_project` replaces top-level fields. For `clips`, a clip with an
existing id replaces that clip in place; a new id appends. To reorder
clips, send the full `clips` list in the order you want — patching
does not reorder on its own.

## Errors and what to do about them

- `FILE_NOT_FOUND`: the path does not exist from the server's working
  directory or the AgentCut directory. Fix the path, do not invent one.
- `CLIP_OUT_OF_RANGE`: trim or text/caption range exceeds the source
  or the timeline. Re-probe and correct the numbers.
- `EMPTY_TIMELINE`, `INVALID_SCHEMA`, `INVALID_DIMENSION`,
  `DURATION_TOO_LONG`: fix the project fields and re-validate.
- `RENDER_FAILED`: read the message, fix the project, re-validate,
  then render again. Never retry the same failing project unchanged.
- `PROJECT_NOT_FOUND`, `JOB_NOT_FOUND`: the id is wrong or the store
  was cleared. List projects or re-create the project.

## Limits you must respect

- Local files only in v1. No URLs, no downloads.
- Local trust only: the server trusts the agent running it. Do not
  expose it unauthenticated over a network.
- This is an original tool. It contains no CapCut code, assets or
  effects, and it does not import or export CapCut drafts in v1.

Ken Burns: an image clip may set `ken_burns` to `zoom_in`, `zoom_out`, `pan_left` or `pan_right`. It is rejected on video clips.

Crop-fill: clip `fit` is `contain` (default) or `cover` (fill the frame, centre-cropped).

Transitions: `none`, `fade`, `crossfade`, plus `wipe_left/right/up/down` and `slide_left/right/up/down` (all 0.5s xfade overlap).

Text styling: `color` (#RRGGBB or named colour), `box` true/false, and text `position` presets top_left/top_center/top_right/center/bottom_left/bottom_center/bottom_right.

Audio fades: track `fade_in`/`fade_out` seconds. Clip speed: video clip `speed` 0.25..4.0 (effective duration = trim / speed).

Poster frame: MCP tool `poster_frame` (id, optional time in seconds, default middle) or `./run.sh poster <json> <png> [seconds]`.

Robustness: image clips are probed; images over 2560px long side are downscaled first; dimensions must be integers; empty text is rejected.

## Producer workflow (any agent) — the review loop

This loop is the same in every MCP client. It does not depend on
any one assistant, chat app, or editor: any agent that can call
MCP tools can run it, and the brief (or the stored project) is
the only source of truth it edits.

1. Brief. Write down the goal, the audience, the format
   (`vertical`, `square` or `landscape`), the media paths, the
   title, and the music if any. A `soundtrack` shortcut covers
   the music in one field; `sections` (title + clips each) add
   an auto title card at each section's start. Start from a template brief in
   `examples/brief-*.json` when one fits, and swap in your own
   media paths. Probe every source first with `probe_media`;
   never guess a duration.
2. Produce. Call `produce_video` with that brief (or
   `./run.sh produce <brief.json> <out.mp4>`). For all three
   shapes at once, call `produce_variants` (or
   `./run.sh produce-variants <brief.json> <dir>`). The call validates,
   renders, and returns the finished video plus `review_frames`
   and a `qa` block. It blocks until the render completes; there
   is no job to poll for this call.
3. Review. Look at every review frame, not just the first:
   title legible, no unexpected black frames, no letterboxing
   surprise, transitions clean, text not cut off at the edges.
   Then read `qa.warnings` and judge each one, because warnings
   are not errors:
   - `duration` / `resolution` / `pix_fmt` warnings mean the
     output does not match the brief: fix the brief and
     re-produce.
   - A `black frames detected` warning names the segments: a
     fade-to-black transition explains short black segments;
     unexplained black means a bad clip or a bad trim.
   - An `audio is silent throughout` warning is expected for a
     montage with no music track, and is only a problem if
     sound was wanted — then add a soundtrack and re-produce.
   - `detection unavailable` warnings mean the check itself
     failed; the video may still be fine, but say so when you
     deliver it instead of claiming it was checked.
4. Patch. Change exactly what the review showed was wrong, in
   one place only:
   - Brief path (the normal producer path): edit the brief
     JSON — clip order, clip duration, text, `fit`, transition,
     soundtrack, or sections — and go back to step 2.
   - Stored-project path: edit the stored project with
     `patch_project`, then `render_project`, then poll `get_job`
     until `done` or `failed`; never present a queued or
     running job as a finished video.
   Do not patch the rendered MP4, and do not hand-edit a
   review frame and call it the video.
5. Final check. After the last re-produce, review the new
   frames and the new `qa` block again, and verify the output
   with its probe data: duration, width, height, video and
   audio streams, `yuv420p`. Only then deliver the MP4, with
   its duration, size and format. Do not deliver a video you
   have not frame-checked in its final form.

## Overlays

Use `overlays` for picture-in-picture: a second visual track above
the main timeline. Give each overlay an id, kind, src, start and
duration that fit inside the timeline, a size fraction, and a
position or x/y. A video overlay may set `volume` (default 0.0
muted) to mix its audio in during its window; volume on an image
or silent overlay is a validation error. Check the render's
frames at a second inside the overlay window to confirm
placement; if validation reports an overlay error, follow its
`fix` hint.

## Overlay keyframes

Animate an overlay with `keyframes` (time relative to the overlay
start, plus x, y, scale or opacity). At least 2 keyframes, times
strictly increasing, inside the overlay duration. Verify motion
by extracting frames at two times inside the animation and
checking the overlay's position, size or opacity in each.

## Split, reverse, freeze, loop

For a video clip, `reverse` plays it backwards, `freeze_at`
holds one frame, and `loop_count` repeats the trimmed segment.
Split a clip with the split_clip tool (project + clip id +
seconds into the clip's rendered duration); the returned
project has two clips in place of one and the same total
duration. Verify a freeze by comparing frames at two times,
a reverse by reversing twice and comparing with the original.

## Agent experiment loop (safe patches)

AgentCut is deterministic execution and verification; you, the
agent, supply the intelligence. Work in this loop:

1. Summarise first: call timeline_summary on the project
   instead of re-reading the whole JSON.
2. Snapshot before experimenting: snapshot_project, with a
   label such as "before-title-change".
3. Patch (patch_project validates before saving, so a bad
   patch never corrupts the stored project), then preview or
   render and review the frames and the qa warnings.
4. If the result is worse, rollback_project to the snapshot
   and try a different patch. The snapshot is kept, so
   rollback is repeatable.
5. For a produce brief, read the conformance block in the
   produce result: it checks clips used, title present,
   format, duration within target_duration, captions and
   audio. A failed check tells you exactly what to fix in
   the brief; do not deliver a video whose conformance
   failed without saying so.

Never ask AgentCut to generate, transcribe or synthesise:
supply SRT captions, audio and media as local files, and let
AgentCut edit and verify them.

## Pro pack (1.32.0)

- Easing: overlay keyframes accept `easing` (`linear` default,
  `ease_in`, `ease_out`, `ease_in_out`) for scale, position and
  opacity. Set it on either keyframe of a segment; the later
  keyframe wins if both set a non-linear easing. Unknown
  values fail validation with a fix hint.
- Grading: clips accept `grade` (`cinematic`, `teal_orange`,
  `warm`, `cool`, `noir` — documented filter combinations,
  not film stocks) and/or `lut` (a local `.cube` file that
  must exist). Copy `examples/grading.json` to start.
- Ducking: a project `ducking` object
  (`voice_track` index or src, `amount_db`, `fade`, optional
  `windows`) replaces the sidechain compressor with
  deterministic volume automation from the voice track's time
  windows. It is not sidechain compression and not level
  detection. Copy `examples/ducking.json` to start.
- Chapters: project `chapters` (`{title, start}`) are written
  into the output metadata; verify with
  `ffprobe -show_chapters`. Produce briefs accept `chapters`;
  section briefs get chapters from section titles.
- Delivery presets: `youtube_1080`, `shorts_720`,
  `instagram_square` via `delivery_preset` (produce) or
  `delivery_presets` (produce-variants). Explicit
  width/height or format in the brief always wins over a
  preset's size; check `delivery_preset_applied` in the
  result for what was actually applied. Presets are delivery
  settings, not platform guarantees.
- Review aids: previews draw title-safe/action-safe guides by
  default and can burn in timecode; the preview result flags
  both. Final renders have neither unless the project sets
  `timecode: true` explicitly.
- Producer QA now also reports `loudness` (LUFS), `audio_peak_db`
  (sample peak; warned above -1 dB) and `freeze_segments` /
  `freeze_duration`. All QA entries are warnings only.

## Templates, word reveal, Arabic text

- Templates to copy: blackboard (`examples/blackboard.json`,
  `examples/brief-blackboard.json`) and animated slideshow
  (`examples/animated.json`, `examples/brief-animated.json`).
- `reveal: "word"` on a text reveals it word by word across its
  window (proportional to word length). Never invent a
  per-character reveal: Arabic letters join, and splitting them
  breaks joining. Reveal and `animation` cannot combine.
- Arabic text automatically uses Noto Naskh Arabic when present
  (DejaVu has no Arabic glyphs). Shaping was verified on this
  FFmpeg build (libfribidi + libharfbuzz); on other builds,
  frame-check Arabic text before delivering it.
- Arabic word order (1.33.0): every Arabic text/caption is
  rendered wrapped in RLE...PDF with RLM before trailing
  punctuation, because this build otherwise renders digit-led
  lines LTR ("2 دولار" as "دولار 2"). Stored text stays in
  logical order; Latin-only lines are untouched. Never
  "fix" order by reversing tokens — verified to render
  byte-identical to the broken line on this build.
- Layer/text collisions (1.33.0): validate_project's info
  carries `warnings`; an overlay path crossing a text or
  caption region records `layer_text_overlap_warning`
  (overlay id, text, time range). Warnings only — they never
  block a render. Boxes are estimates (see CHANGELOG), so
  treat a warning as "check this frame", not as proof of a
  visible collision.
