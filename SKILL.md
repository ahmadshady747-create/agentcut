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
- Poll `get_job` until status is `done` or `failed`. Never present a
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
  sidechain compressor keyed off the voice mix.
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
