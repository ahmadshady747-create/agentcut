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
- Background jobs are a second, lighter option: `start_render_job`
  (stored id or project dict), poll `get_job_status`, cancel with
  `cancel_job`. They live only in this server process, and a running
  one cannot be interrupted (cancel reports `cancelled: false`).
- After `done`, verify the output with the probe data in the job
  result: duration, width, height, video and audio streams.

## On-device rendering (render packs) vs VM rendering

Two workflows, and they must not be mixed up:

- VM rendering (this MCP server, as above): the agent renders
  and verifies directly. This is the default.
- On-device rendering: the agent CANNOT run anything on the
  user's phone — there is no shell channel to it, and Muse
  does not host user MCP servers. The agent builds the project
  exactly as usual, then runs `./run.sh package <project.json>
  <outdir>` and delivers the pack (archive or link). The user
  unpacks it on their device (Termux: `pkg install python
  ffmpeg`), runs `./render.sh` inside it, and reports the
  result back. The pack is self-contained: rewritten project,
  every media file, AgentCut source, bundled fonts, `render.sh`.
  It needs no network, no MCP server and no repo checkout.
- Never present a pack as a rendered video: until the user
  reports a successful on-device render, the video is
  undelivered. Never claim an on-phone trial you did not see.
- Fonts on device resolve as: `$AGENTCUT_FONT_DIR`, bundled
  `fonts/`, Termux `$PREFIX` font dirs, system paths. Arabic
  text with no Arabic font fails with `FONT_NOT_FOUND` — that
  error is the correct outcome, not something to work around
  by switching fonts silently.
- Detached MCP jobs are unreliable on Android (the OS can kill
  background work); on-device rendering is foreground CLI by
  design. Full user guide: `docs/ON_DEVICE.md`.

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
- `texts`: overlays with `text`, `start`, `end`, `size`
  (8..240 pixels), `x` (pixel number >= 0 or `center`), `y`
  (pixels >= 0), optional `color`, `box`, `position` preset
  (overrides x/y), `fade_in`/`fade_out` seconds.
- `captions`: like texts, but centred near the bottom by default,
  and they must not set x/y (use `position` or
  `caption_position` instead). Overlapping SRT cues render
  stacked; a caption wider than the frame triggers a
  `text_overflow_warning` (warnings never block — split the
  caption or shrink it).
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

Ken Burns: an image clip may set `ken_burns` to `zoom_in`, `zoom_out`, `pan_left` or `pan_right`. It is rejected on video clips. Since 1.37.2 the range is a gentle 12% drift (1.00->1.12, pans at a constant 1.12x) on a smoothstep ease — not a dramatic zoom — because the old 50% range read as vibration and grotesque zoom in the 1.37.0 rhythm proof.

Crop-fill: clip `fit` is `contain` (default) or `cover` (fill the frame, centre-cropped).

Transitions: `none`, `fade`, `crossfade`, plus `wipe_left/right/up/down` and `slide_left/right/up/down` (all 0.5s xfade overlap).

Text styling: `color` (#RRGGBB or named colour), `box` true/false, `size` 8..240, and text `position` presets top_left/top_center/top_right/center/bottom_left/bottom_center/bottom_right (a text `position` overrides its x/y; for overlays the reverse holds — explicit x/y win per axis over a preset). Shadow: `shadow_x`/`shadow_y` (-20..20) and `shadow_color`; captions default to a subtle (2,2) black shadow, plain texts to none (setting either axis takes over both). Rounded pill background: `box_radius` (0..100), `box_color`, `box_opacity` (0.5), `box_padding` (12); requires `box: true`, refuses animation and fades. Captions wrap automatically: a cue wider than the frame becomes centred lines within `caption_max_width` (default 0.9) at `caption_line_spacing` (default 1.35), stacked upward from the baseline; logical-text wrapping, BiDi per line, timing unchanged; a single over-wide word stays whole and clips with a warning. Scrims: project `scrims: {top, bottom}`, each `true` or `{opacity, depth}` (defaults 0.6, 0.35); black gradient over clips, under overlays and texts; default off. Example: `examples/text-polish.json`.

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
position or x/y (explicit x/y win per axis over a preset). A video overlay may set `volume` (default 0.0
muted) to mix its audio in during its window; volume on an image
or silent overlay is a validation error. Dressing (all default
off): `border_width` (0..20) + `border_color`, `corner_radius`
(0..200, at most half the smaller displayed side), and a drop
shadow (`shadow_dx`/`shadow_dy`, `shadow_blur`, `shadow_opacity`) —
the shadow activates only when an offset or a blur is set.
Check the render's
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
  `warm`, `cool`, `noir`, `vintage`, `crisp` — documented
  filter combinations, not film stocks) and/or `lut` (a local
  `.cube` file that must exist). Copy `examples/grading.json`
  to start.
- Ducking: a project `ducking` object
  (`voice_track` index or src, `amount_db`, `fade`, optional
  `windows`) replaces the sidechain compressor with
  deterministic volume automation from the voice track's time
  windows. It is not sidechain compression and not level
  detection. Copy `examples/ducking.json` to start.
  The auto path has a switch, `auto_duck` (default true):
  explicit `ducking` always wins; otherwise voice + music
  + no explicit ducking gets the sidechain; `false` disables
  only the auto path. `output.master_loudnorm: true` masters
  the finished mix at -14 LUFS (single pass, near -14 not
  exact); silent timelines skip it, and it disqualifies
  segment caching.
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

## Visual rhythm, part 1 (1.37.0)

- SFX: put `sfx` on a text, caption, clip or overlay and
  AgentCut fires it at that element's start — never compute
  an adelay yourself. Built-ins: `pop` (text/badge
  appearance), `whoosh` (transitions, slide texts), `cash`
  and `chime` (the same metallic ring, for money figures
  and key numbers), `click` (labels, scene switches),
  `ding` (titles), `thud` (explosive entrances). Or give a
  local audio file path. `sfx_volume` defaults to the
  plan volume (0..2); an explicit `sfx_volume` always
  wins. 1.42.0 discipline: `compute_sfx_plan` keeps only
  chapter-transition, diagram-reveal and major-callout
  SFX, with a 15s cooldown (ties: chapter > diagram >
  callout), attenuation `sfx_attenuation_db` (default 16,
  0..30; auto volume 0.8 * 10^(-att/20) ≈ 0.127 at the
  default), and pitch alternating 1.03 / 0.97 by kept
  index. The plan is in `info["sfx_plan"]` (kept, dropped
  with reasons). SFX alone still produce a valid audio
  stream. 1.37.1: timing holds with `default_image_motion`
  on and off (built-ins are time-gated; file SFX use adelay
  alone) — the 1.37.0 code played every SFX from t=0
  under motion because atrim after adelay discarded the
  delay silence.
- Micro-motion: `default_image_motion: true` on the project
  animates stills automatically (zoom_in on even clip
  indices, zoom_out on odd ones); a clip's own `ken_burns`
  always wins, and blurred-fit stills are excluded.
  `punch_in` on a clip is true (mid-clip, 1.20x) or
  `{at, factor}` with factor 1.18..1.25 and `at` inside the
  clip — a centre scale jump at a frame boundary, no gap.
  Since 1.37.2 punch_in WINS over Ken Burns and never stacks
  with it: a punched clip is static until its punch time,
  then holds its punch factor, so the factor is the TOTAL
  scale (max 1.25x). A clip with both an explicit
  `ken_burns` and `punch_in` warns as
  `punch_in_overrides_ken_burns` (warnings only);
  `timeline_summary` reports such clips as
  `effective_ken_burns: "none (punch_in)"`.
- Static shots: a still longer than 4.5s with no ken_burns,
  punch_in, default motion or animated overlay warns as
  `static_shot_warning` in validate warnings, the timeline
  summary and produce QA. Warnings only — add motion or
  accept the frozen frame.
- Active captions: a caption's `word_timings` must list
  every caption word once, in order, with absolute
  start/end inside the caption window (AgentCut computes
  nothing here — timings come from transcription). It
  renders in 2–4 word chunks with the current word in
  `highlight_color` (default yellow); `highlight_mode:
  "chunk"` highlights the whole active chunk instead.
  Per-word mode is the shipped behaviour: widths are
  measured with FFmpeg, so the highlight covers its word
  exactly, Arabic letters stay joined and RTL order holds.
  word_timings cannot combine with reveal, animation or
  fades. A chunk wider than the frame warns
  (`active_chunk_overflow_warning`) — shrink the size or
  widen the frame for long active chunks.

## One-button board explainer (1.39.0)

For a finished board explainer, do NOT hand-write a
project.json. Call `produce_explainer` (MCP tool, or
`./run.sh produce-explainer <brief.json> <mp4>`) with
one brief — this is the button:

```json
{
  "id": "strawman",
  "style": "blackboard",
  "output": {"width": 720, "height": 1280, "fps": 30},
  "background": "/path/to/board_image.png",
  "title": "مغالطة رجل القش",
  "chapters": true,
  "motion": "gentle",
  "music": null,
  "scenes": [
    {"id": "s1", "title": "ما هي؟",
     "narration": "/path/voice_s1.mp3", "captions": "/path/s1.srt",
     "diagram": {"template": "causes", "params": {"rows": 2}, "draw": 3.5, "size": 0.72, "x": 101, "y": 330},
     "keywords": [{"text": "رجل القش", "start": 5.0, "end": 9.0, "sfx": "pop"}]}
  ]
}
```

Derived, never hand-assembled: scene durations from
ffprobe of the narration audio; scenes back-to-back in
list order; total = sum of scenes. Per-scene SRT cues
are scene-relative and merged into one captions track
offset by the scene start. A scene diagram defaults to
the whole scene window; an explicit diagram start/end
is a scene-relative offset inside the scene (outside =
structured error). The brief title is fixed across the
video; scene titles show during their scene; keyword
texts show at their scene-relative windows with SFX via
the normal text SFX mechanism (a keyword may also set
`x`, `y`, `size`, `color` or `position` for placement).
`chapters: true` = one
chapter per scene title at the scene start. Blackboard
style = scrims top+bottom on; `motion: "gentle"` =
`default_image_motion` on (≤1.12x only). `punch_in`
anywhere in the brief is rejected with a fix hint.
Optional `music` = a music track ducked 12 dB over the
narration spans. Errors are structured with fix hints
for missing narration/captions, unmeasurable narration,
a diagram outside its scene, and unknown templates.
Runnable example (repo media only):
`examples/one-button-explainer.json`.

## Animated stroke diagrams (1.38.0)

Use `diagrams` for hand-drawn educational art that draws
itself on screen — a general element, not a one-video
effect. A diagram is either custom strokes or one built-in
template, placed like an overlay (`size` fraction of the
output width, `position` preset, or x/y pixel offsets
winning per axis) over a `start`..`end` window. It draws
stroke by stroke, in list order, over `draw` seconds
(default: min(4s, window), must fit inside the window),
then holds the complete diagram for `hold_seconds`
(default 8.0s, 0..60 — 1.42.0: a diagram no longer
freezes for the whole scene) and fades it out over 1.0s
(the card fades with it). The final frame equals the
complete diagram exactly (tested).

- Custom strokes: `{"kind": "polyline"|"arrow"|"line"|
  "circle"|"ellipse"|"rect"|"arc", ...}` in diagram-local
  coordinates (canvas default 900x560, `canvas: {width,
  height}` 100..1920). Stroke fields: `color` (`white`
  default, `yellow` for emphasis only, any named colour or
  #RRGGBB), `width` (2..20, 7 is the standard chalk
  weight), `alpha` (0.2..1, 0.95 solid; faint grid lines
  use ~0.35), `weight` (share of the draw window; default
  is path length, so long strokes naturally take longer —
  give long faint lines a small explicit weight or they
  will eat the window), `seed` (defaults to the stroke
  index). Polyline/arrow take `points` ([x, y] pairs
  inside the canvas); arrow takes `head_size` (10..100,
  default 36). Circle takes `center` + `radius`, ellipse
  `center` + `radius_x`/`radius_y`, rect `x`/`y` +
  `rect_width`/`rect_height` (NOT `width` — that is the
  stroke width) + optional `corner_radius`. Arc takes
  `center` + `radius` + `start_angle`/`end_angle` in
  degrees (0 = east, positive = clockwise; zero sweep =
  full circle). All geometry must fit inside the canvas,
  or validation fails with a fix hint.
- Templates (parameters only, never text — labels are
  always AgentCut texts, never baked into a diagram):
  `chart` (`values`: 2..8 numbers; axes with arrowheads,
  faint grid, arrowed rising curve, yellow tip),
  `basket` (`coins`: 1..5; basket with coins in a
  pyramid), `balance` (`left`/`right` numbers; beam
  tilts toward the heavier side, level when equal),
  `causes` (`rows`: 1..4; badge + arrow per row).
  A template diagram must not set `canvas`, and takes
  only its own parameters — unknown parameters fail
  validation. Example: `examples/stroke-diagram.json`
  (chart + basket over a board still).
- Artwork rules: clean geometry, one consistent chalk
  weight per stroke, subtle chalk texture with slightly
  irregular edges that are STATIC per stroke (baked into
  the path once — never animated jitter, which reads as
  vibration), white chalk with yellow for emphasis only.
  Diagrams are data, not files: render packs carry them
  verbatim in project.json and regenerate the animation
  at render time, so no media file is collected for them.
  A diagram drawing over a still counts as motion for the
  static-shot warning, and a diagram overlapping a text
  warns as `layer_text_overlap_warning` with a
  `diagram_id` (warnings only).
- `timeline_summary` reports diagrams with `start`,
  `end`, `draw` and `draw_end`, plus `source`, `template`
  and stroke count, so you can reason about draw timing
  without re-reading the project.

## Delta upgrade (unreleased, audit pending)

- `fit: "blur_pad"` is the blurred-background fill with a
  boxblur kernel instead of gblur; all blur exclusions
  apply to both names.
- ASS export (additional, never replacing drawtext):
  `\fad(120,120)` fades by default, soft outline + drop
  shadow, and karaoke by default (Karaoke style, yellow
  highlight; measured `\k` tags from `word_timings`, else
  a proportional split labelled `est` — estimates, never
  measured timings). `karaoke=None` gives plain dialogues.
- Diagram stamps feather with a smoothstep falloff and
  stroke progress eases (smoothstep); the final frame
  stays byte-identical to a direct full render.
- `render_project_cached` stays a Python API (not an MCP
  tool or CLI): qualified subset only. The concat demuxer
  stays rejected (proven one-frame audio gap).
- Smooth camera is verified, not rewritten: Ken Burns
  keeps 2x pre-scale, smoothstep easing, fixed fps, even
  dimensions. Element keyframe easing defaults stay
  `linear` — flipping them would change existing output.

## General visual craft (1.40.0)

All of these work on video, image, solid/gradient and board
clips alike — none is tied to a style.

- Camera path: clip `camera: [{t, x, y, scale}]`, multi-stop
  pan/zoom, smoothstep, even-size-safe. Wins over
  `ken_burns` (warning); rejects `punch_in`, blur fit,
  `stabilize`, `speed_curve`; 1.25x max by construction.
- Parallax: overlay `depth` 0..1 drifts with camera/Ken
  Burns motion; warns and does nothing on a motionless
  clip.
- Kinetic text: any text takes `word_timings` + highlight
  colour + `emphasis` pop (`emphasis_factor` 1.0..1.5);
  `style` presets `title`/`heading`/`label`/`caption`
  (size ratios, shadow, stroke, spacing). Explicit values
  always win.
- Treatment: clip- and project-level
  `treatment: {grain 0..30, vignette 0..1, shadow 0..1}`.
- Continuity: clips sharing a `scene_id` chain scales
  (N's end = N+1's start, 1.25x max); punch end feeds the
  chain, static clips reset it. Warns on a motionless
  same-scene clip after a zoomed one.
- Transitions: `zoom`, `wipe_tl`, `squeeze_h` join the
  xfade set. circlecrop as "iris" was tried and rejected
  (midpoint renders black on this build) — do not offer it.
- Produce look: `produce_explainer` brief `look` — clean,
  cinematic, vintage, bold, still — treatment + grade +
  scene-title preset + motion default, on any background.
  Brief fields always win; unknown looks are rejected
  with the known list.
- Defaults: plain texts now default to the (2,2) shadow
  when neither axis is set (mirroring captions); texts
  crossing the 5% action-safe rect warn only, never error.

## Life & typography package (1.41.0)

Built from a real verdict on a 10-minute video ("voice + dead
silence, bad writing, beginner typography, no charts, no
life"). Every item below is general, none video-specific.

- Sound: `music_bed_inaudible` QA warning when the bed's
  effective level under ducking is >18 dB below narration
  (the old video measured 22.84 dB). `duck_amount_db` 0..30,
  precedence: explicit `ducking` > `duck_amount_db` > auto
  sidechain > plain mix. Diagrams take `sfx`; explainer
  brief takes `sfx_density: rich/minimal` and passes
  `duck_amount_db` through when music exists. A bed at 0.3
  under 12 dB ducking is inaudible on phone speakers by
  measurement — never ship that mix.
- Titles persist: kinetic/text base words hold to the next
  chunk's start or the text's end by default; `hold: false`
  restores chunk-window ends. Text path only; captions stay
  timed cues.
- Charts: `y_grid_values` + `fill` (translucent comb under
  the curve); grid labels are drawtext, never baked art;
  values are caller-supplied and must be labelled
  illustrative unless attributed. Diagrams take `card`
  (bool), `card_color`, `card_opacity` (default black 0.45).
- Display font: texts take `font` (registry key). Arabic
  display face is `noto-sans-bold` (Noto Sans Arabic Bold,
  OFL, in fonts/ with its licence). Tajawal Bold was tried
  and rejected: it lacks isolated presentation forms and
  this FFmpeg drawtext joins via FriBidi forms, not GSUB —
  it renders tofu. Unknown font names fail loudly; Arabic
  never falls back to Latin. Type scale (fractions of frame
  height): title 0.055, heading 0.044, label 0.030, caption
  0.028, card 0.055; weight comes from the bold file. Box
  is opt-in: title/heading/label ship box-free, `card` is
  the opt-in rounded translucent backdrop.
- Collision-proof layout: `resolve_overlaps: true` cascades
  a keyword pop downward (+16px steps, max 20) past texts,
  captions and diagram boxes in shared windows; moves land
  in `info["overlap_moves"]`, the project is never mutated,
  and warnings are computed on the resolved layout (a
  resolved project reports zero overlap warnings). New
  `text_text_overlap_warning` for pop-vs-label and
  title-vs-pop. Preset-positioned pops warn, never move.
- Mastering: crf 17, preset medium (veryfast only for
  preview callers), bt709 final node + metadata, +faststart,
  resolution VBV caps. Master loudnorm is linear two-pass,
  default I=-14, `output.loudnorm_i` selects -16; per-track
  linear + measured; sidechain exactly
  threshold=0.03:ratio=4:attack=40:release=500:knee=4;
  AAC 192k at 48000 Hz. `output.color_pipeline: rgb`
  (gbrp internal, single final conversion) is opt-in; the
  default stays `yuv` — a default flip needs a migration
  round, not a silent change.
- ASS karaoke: per-word drawtext karaoke is gone. Any item
  with `word_timings` renders as ONE .ass file through the
  ass filter (libass present in this FFmpeg build). One
  Dialogue per word window, full line, active word coloured
  inside the line; plain base Dialogues cover gaps and
  holds; chunk mode is plain highlight Dialogues. No `\t`
  (sticks past its window) and no scale tags (reflow
  neighbours). ORDER RULE — get this wrong and titles read
  backwards: TAGGED lines are emitted in reverse word order
  (libass does not reorder across tags); UNTAGGED plain/gap
  lines stay in logical order (FriBidi reorders them; a
  second reversal displays them backwards — this shipped
  once in the AI-race rebuild render 1, was caught by its
  frame gate, fixed, and re-rendered).
- Wrap: relative-width table (narrow 0.25, connected 0.45,
  wide 0.60, space 0.30, diacritics 0.0, Latin 0.55) replaces
  flat 0.6 x size; wrapped text caps at 80% of video width.
- Diagrams: capsule SDF segments (coverage clamp(0.5 + r -
  d), 15px gap break for arrow barbs); Pillow, when present,
  is used automatically at 2x + LANCZOS, else stdlib SDF.
  Chalk grain is gone by design.
- Camera: image camera/Ken Burns pre-scale 4x, zoompan at
  2Wx2H, lanczos + accurate_rnd downscale (0.25 output px
  rounding); video camera stays 2x. Measured: 4x is the only
  factor with zero plateaus on a slow pan.
- Modern look: explainer `look: modern` — vignette 0.4,
  card titles, diagram cards by default, 10% safe-margin
  zoning (titles/diagrams inside, captions in the bottom
  band below diagrams). Background sentinel `canvas`
  generates a dual-tone slate gradient PNG beside the
  output. Combine with real photo backgrounds where they
  serve the scene; do not use flat gradients as the whole
  scene.

## Refactoring package (1.42.0)

Built from six defects observed in a delivered 10-minute
video (AI-race v2): mangled Arabic ligatures in captions,
scrambled kinetic word order, a callout touching a
diagram card, frozen diagrams and one-minute stills, SFX
spam, and a soft phone copy. Every fix is general, none
video-specific; the rebuilt video is the proof.

- Ligatures: `text_shaping=1` is explicit on every
  drawtext. Both bundled Arabic fonts were verified with
  fontTools (complete GSUB/GPOS, لا ligature present);
  there is no Presentation Forms reshaping anywhere —
  clean UTF-8 only. Seven words (عملاقة، التالي، بالسوق،
  بالمئة، الرسالة، بالطاقة، فالسؤال) proven clean in
  rendered frames. Tajawal stays rejected (see 1.41.0).
- Word order: never split an Arabic phrase into per-word
  drawtext filters. ASS colour highlighting anchors every
  tag boundary with RLM (U+200F) — at line start and after
  every closing colour tag — with WrapStyle: 2 and RTL
  alignment. TAGGED lines are emitted in reverse word
  order (libass does not reorder across tags); UNTAGGED
  lines stay in logical order. Proven by a
  template-matching regression test: "الهجوم المضاد من
  الحافة" and "أربعة وثمانين بالمئة" keep correct order
  at EVERY highlight index — sample frames INSIDE every
  kinetic window, never hold frames only.
- Zone manager: strict non-overlapping bands (fractions
  of frame height, 9:16 and 16:9) — Header 5–18%,
  Diagram 22–56%, Callout 60–70%, Subtitle 76–92%.
  Callouts auto-shift below an active diagram box with
  ≥40px padding; the moves are in `info["zone_moves"]`
  (zone wins over overlap moves). Caption anchor is
  clamped to ≤0.92H when `resolve_overlaps` is true.
  Titles, diagrams and captions warn, never move.
- No stagnation: diagrams hold `hold_seconds` (default
  8.0, 0..60) then fade out over 1.0s. Still-image clips
  longer than 12s get automatic multi-stop sub-cuts
  (N = max(1, round(D/10)) glides, wide 1.0 → zoom 1.18 →
  pan 1.12 → pan 1.12 → wide 1.0, pan flips per clip
  index) unless the clip has an explicit camera, punch
  or ken_burns, or a blur fit; `info["auto_subcuts"]`
  lists them. Opt out per project (`auto_subcuts:
  false`) or per clip (`auto_subcuts: false`). The big
  main title shows only in the first 6s, then shrinks
  to a small top badge (`shrink_after` ≥0.5s, `shrink_size`,
  `shrink_y`; defaults half-size at y=24).
- SFX discipline: see the SFX bullet above (15s
  cooldown, chapter > diagram > callout priority,
  `sfx_attenuation_db` default 16, pitch 1.03/0.97,
  `info["sfx_plan"]`). On the v2 project the plan keeps
  17 of 24 entries with a 15.30s minimum gap; measured
  against real narration, the pop peaks ~13–17 dB below
  the voice.
- Encoding: master verified and kept — crf 17, preset
  medium, bt709, +faststart, AAC 192k at 48 kHz, VBV
  caps scaled by resolution (below 1080p: 8M/16M).
  Phone copy (delivery, e.g. for a storage link):
  `-b:v 800k -maxrate 1000k -bufsize 2000k -preset medium
  -b:a 128k` — 938 kbps measured (ABR, no -crf; the earlier
  CRF variant `-b:v 800k -maxrate 1000k -crf 21` ran at
  7.0 Mbps on grainy/karaoke content because CRF mode ignores
  -b:v and -maxrate without -bufsize is unenforced). The master
  is never re-encoded to make the phone copy; the phone copy is
  a separate encode.

## Motion graphics (1.43.0)

Project-level `motion` (absolute start/end, like `diagrams`).
Seven kinds; no new MCP tool — create/patch/validate/render
already carry `project.json`.

- `shape`: rect/rounded rect/circle/ellipse/line/polygon.
  Keyframed x, y, scale, rotation, opacity. Easing per
  segment: linear, smoothstep, ease_in, ease_out,
  ease_in_out (a segment's easing comes from its END
  keyframe when non-linear, else its START keyframe, else
  linear). x/y are the shape CENTER; rotation is degrees
  clockwise; scale multiplies geometry only, stroke width
  stays constant. Keyframe times are ABSOLUTE timeline
  seconds inside the element window.
- `counter`: a number counting up/down across its window.
  Rendered as a live drawtext `%{eif}` expression (inline,
  expansion=normal, never a textfile). decimals is 0 or 1
  (eif formats integers only on this build); from/to must be
  >= 0; prefix/suffix strings; a literal Latin `%` is
  impossible — use ٪ (U+066A). An Arabic suffix renders LEFT
  of the digits, an Arabic prefix RIGHT; Latin affixes stay
  in Western order. x/y are the CENTER of the counter.
- `bars`: values list (2..8 numbers >= 0, one > 0), labels
  one per value, colors single or list (8-colour palette
  default), stagger (0..0.5, default 0.12) and grow
  (0.1..1, default 0.5) with (n-1)*stagger + grow <= 1 so
  every bar finishes; grid, bar_gap. Bars grow with
  smoothstep stagger; the value label appears when its bar
  completes, the category label at its stagger start.
- `donut`/`pie`: values + labels + colors as bars; segments
  sweep in from the top, clockwise, with 2-degree gaps.
  donut_ratio 0..0.9 (default 0.55); pie takes no
  donut_ratio. Segment label ("label: value") appears when
  its sweep completes.
- `line_chart`: values list; the line draws progressively
  across the WHOLE window at constant pen speed, with a
  moving yellow head dot and white dots on reached points;
  a value label appears when the head reaches its point.
- `lower_third`: rounded-rect card + text. Card slides in
  from the left and fades over `enter` (0..2s, default 0.6),
  holds, slides out and fades over `exit` (0..2s, default
  0.6); enter + exit must fit the window. The text slides
  with the SAME linear motion and fades with the same ramps
  (card and text stay in lockstep); the card is the box, so
  the text has none. The card's rest position (x, y, w, h)
  must sit fully inside the frame; card_color, card_opacity
  (default 0.75), corner_radius (default 16, <= min(w,h)/2).
- `path` is NOT a separate kind: a polyline path IS a
  sequence of x/y keyframes — set keyframe times for
  arc-length pacing.
- Compositing: one transparent PNG-codec video per element
  at project fps, overlaid ABOVE video/images/diagrams and
  BELOW captions/texts. Identical consecutive frames are
  cached (holds and static elements cost one render).
  The rasterizer is 1x with coverage-based edge AA (NOT
  2x supersampled: 720x1280 RGBA x 750 frames is ~2.8 GB
  through the pipe; 2x would quadruple it).
- Validation: structured errors with fix hints for unknown
  kind, keyframe times outside the window, keyframes out of
  order, geometry outside the frame, duplicate ids, invalid
  easing. Charts must sit fully inside the frame (they do
  not slide); shapes and lower-thirds may extend outside by
  at most one bbox-size per side (slide-in/out).
- Determinism: the same motion spec renders byte-identical
  frames (pinned by a test). Projects without `motion`
  render byte-identically to 1.42.0.
- Limits: derived motion labels are NOT covered by the
  collision/zone checks — their placement is the author's
  responsibility. Counter affixes and chart labels share
  the font-coverage rule: Noto Naskh Arabic has no glyph
  for `-`, `–`, `—` (nor `%`/`#`) — avoid them in Arabic
  text, or use `font: "noto-sans-bold"` (has the hyphen).
  A line-chart value label's box can partly cover the head
  dot at the frontier.

## Editing application + typography system (1.44.0)

AgentCut does NO analysis and NO AI. These features consume local JSON files produced by AgentMedia tools (`detect_beats`, `track_object`, `analyze_reframe`) and apply them deterministically — same input file, same output.

- `beat_cut` (project-level): `{file, every=1, window=[start, end]}` — splits clips at selected beats from the beats file (beats in `beats`, at least 2). Segments shorter than 0.2s are dropped, never made; total duration unchanged. Rejects `speed_curve`/`loop_count`/`camera`/`punch_in`/`freeze_at` on split clips. Cut times are listed in `info["beat_cuts"]`.
- `track` on overlays and texts: `{file, offset_x=0, offset_y=0}` — position sampled every frame from the track file (AgentMedia `track_object` shape: `{frame_size, track: [{t, x, y, w, h}]}`), linear interpolation, hold first/last point outside the track. Overlays anchor top-left at the point; texts centre on the box centre. Keyframes + `track` on one element is a validation error; no overlap with the element window is an error, partial coverage is a warning.
- `reframe` on video clips only: `{windows_file, target_aspect}` — per-frame crop windows (AgentMedia `analyze_reframe` shape); crop is inserted before trim, dimensions forced even for yuv420p. All windows must share one crop size; partial coverage is a warning (hold nearest window). Rejects `camera`/`stabilize`/blur fits/`freeze_at`/`reverse`/`speed_curve` on the same clip.
- Text `preset` (texts/captions): `typewriter` (whole words only — never splits an Arabic word mid-joining), `slide_in`, `pop_in`, `fade_up`, `kinetic_words`, `highlight_sweep`, `underline_swipe`. `reveal_mask` is rejected (animated crop is not feasible on this FFmpeg build). `preset` + `word_timings`/`animation`/`reveal` is rejected. `animation` gains `pop_in`.
- Typography styles: named `style` on texts/captions — `display` (0.075, white, noto-sans-bold, shadow 3,3, rounded black box 0.55), `subtitle` (0.038, white, noto-sans-bold, no box), `quote` (0.042, yellow, noto-sans-bold, rounded black box 0.35). The old `title`/`heading`/`label`/`caption`/`card` values are byte-frozen — never change their resolved values. Project-level `typography: {default_style}` applies to items without an explicit style (opt-in only; absent = old behaviour). `style` on lower thirds and `label_style` on motion charts/counters ride the same shared resolver — one system for all text surfaces.
- Custom `transition` on clips: `zoom_out`, `whip_pan`, `glitch`, `light_leak` — a post-join effect pass straddling each cut (audio stays plain concat, unlike xfade). Duration < both clip durations, default 0.5; the first clip cannot carry one. Existing xfade names are untouched.
- Multi-track: clips accept `track: 0|1` (default 0). Track 1 clips are full-frame overlays at an explicit numeric `start` (required); they never extend the total duration (error if they run past it); `transition` on track 1 is an error.
- `adjustments` (project-level): `[{start, end, grade?, effects?}]` — `grade` must be a `GRADE_PRESETS` name, `effects` reuse the effects vocabulary, at least one required. Applies to the base video only (overlays/texts/motion sit above, unaffected).
- Easing: `ease_out_back` added to the shared easing system (validate, render, motion) — pop_in overshoots to ~1.06x at mid-entrance.
- Byte-identity: every new feature is opt-in; projects without the new fields render byte-identically to 1.43.0.

## Prune + spring/glass/pacing/contact-sheet (1.45.0)

- Old diagram templates (`chart`, `balance`, `basket`, `causes`) are now adapters to Motion Layer elements (`line_chart`, `bars`, `donut`, `comparison_cards` — a new vector card row with staggered pop-in, accent bars and label/value derived texts). Old projects still validate and render via motion. `comparison_cards` takes 2–4 `{label, value?}` cards; a basket with 1 coin renders as a static circle (motion bars needs ≥2 values). The chalk stamp/grain code is deleted; strokes are clean vectors. Motion charts accept `draw` (staggered growth over `draw` seconds, then hold) and `fade_out`/`fade_start` (raster kinds only). Chart value labels have no background box (a pill would overlap the chart).
- Word measurement is HarfBuzz (`_measure_text_width`, in-memory, shaped advances) — `_measure_word_ink` (cropdetect) is deleted. The sweep bar and kinetic widths use it.
- SFX are pink-noise whooshes (80–500 Hz, linear afade) and bandpassed noise clicks/dings; only the sub-thud keeps a sine. Phone preset is ABR: `-b:v 800k -maxrate 1000k -bufsize 2000k -preset medium -b:a 128k` (938 kbps measured; the CRF variant ran 7.0 Mbps because CRF ignores -b:v and -maxrate without -bufsize is unenforced).
- Boxed texts get rounded pills by default (radius 16, HarfBuzz-measured width). Animated/fading/tracked texts keep the sharp drawtext box. Texts on motion shapes/cards force `box=0`. Multi-line captions get one unified background; reveal lines stay at fixed y. **Byte-identity deliberately broken for boxed texts** (`examples/basic.json` md5 changed) — the prompt requires ALL sharp boxes replaced.
- Spring easing: `spring` (k=180, c=14, m=1; ~15% overshoot, settled by u≈0.7) is the default for lower-third entrances, bar/donut growth, counters, `pop_in` and `slide_in`; `cubic_bezier` (CSS-ease, degree-4 polynomial fit, monotonic) is available. The spring `u` is clamped to [0,1] in FFmpeg expressions. `shape_bbox` samples quarter points of every segment (spring/ease_out_back overshoot breaks keyframes-only sampling).
- Glassmorphism: motion charts and diagram cards accept `backdrop_blur` (default true) — boxblur 16:2 + `rgba(15,23,42,0.68)` tint + rounded-rect PNG mask via `alphamerge` + 2px white border at 0.22 alpha, radius 20. Fast (pre-rendered cached masks); the geq version was 22x slower.
- Arabic default font is Noto Sans Arabic Bold everywhere (drawtext + Noto Naskh corrupted ال into a لا-like form in captions; the defect is drawtext-specific — libass was already correct). Lower thirds right-align Arabic and auto-size (`clamp(text_w + 80, 220, 0.82W)`); an opt-in `accent_color` draws a 6px bar on the text side (right for Arabic, left for Latin).
- `produce_explainer` pacing (default on): long scenes (≥13s) get lower-third (0–5s) + diagram chart (5–13s) + progress counter (13–20s, scene index/total as ٪), repeating every 20s. Short scenes keep the old layout. `pacing: false` opts out. `produce_explainer` emits no motion shapes by default.
- `render_contact_sheet` (23rd MCP tool) + CLI `contact-sheet <video> <out.png> [project.json]`: 12 key frames in a 4x3 grid PNG with MM:SS labels (smart timestamps from the project timeline when given).
- `reveal_mask` stays dropped (dynamic crop re-proven infeasible: crop w/h frozen at the first frame's value on FFmpeg 8.1). `_mask_geq` is kept for explicit mask projects (the explainer uses none); the glass geq was the bottleneck and is replaced.
- Byte-identity: projects without new fields render byte-identically to 1.44.0, EXCEPT boxed texts (see above) and the phone preset (docs only).

## Performance (1.46.0)

- Ken Burns and image-camera paths zoompan at OUTPUT resolution (1x cover pre-scale, no lanczos downscale). The video-camera branch is unchanged. Pixel-equivalence vs the 4x path: board.png mean 0.147 / max 20; detailed testsrc2 mean 0.71–1.62 / max 189 (sub-visible sharpness loss on detailed stills at max zoom; motion/framing unchanged).
- Shadow geq replaced by a cached 1-frame PNG mask + overlay (`/tmp/agentcut-treatment-masks`, inline geq fallback on failure). Pill geq replaced by a cached pill PNG (`/tmp/agentcut-pill-masks`, per-frame geq fallback when argv is unavailable). The vignette mask was investigated and REJECTED (the vignette filter's direct-Y multiply cannot be reproduced by a blend overlay — measured systematic error). Explicit enable-gating of glass boxblur / `_mask_geq` / overlay shadow gblur was measured and NOT implemented (~0s — pull-based scheduling already skips them).
- Grain (default temporal alls=10, unchanged): measured on the real 30s benchmark — alls=10 costs ~53s (46%, encoder-side), alls=3 ~33s, static (frozen) 40.1s, off 61.8s. Static being faster than off is a single-run measurement, reported as such. No `treatment.grain_mode` option exists yet.
- Baseline corrected: the 1.45.0 R12 figure of 533.8s was a contended outlier; the true 1.45.0 30s time is ~235.5s. 1.46.0: 235.5s → 114.5s (2.06x); the full 233s extrapolates to ~15 min. <60s for a full render is not achievable with the current filter-graph architecture.
- Byte-identity: projects without the changed features render byte-identically (no-feature project md5 3a8cc46a… on both trees). `examples/basic.json` (boxed text → pill) has a different bitstream but pixel-identical decoded frames (mean 0 / max 0).

## Caption wrap (1.46.1)

- SRT captions clipped at 720x1280 in 1.45.0/1.46.0 because the wrap used the static weight table (tuned for Noto Naskh Arabic) while the renderer uses Noto Sans Arabic Bold, 1.305x wider at the same size. The wrap now measures with HarfBuzz on the real render font: `srt.measure_text_width()` is the single implementation (moved from render.py; render keeps `_measure_text_width` as a delegate), `wrap_lines`/`wrap_subtitle_text` take `measure=(word, size) -> px`, `validate.wrap_caption_lines` takes `font_path`, and render.py's wrapping stage + reveal expansion pass the drawtext font. The Bold unification stays.
- Proven margins on the reference cue (fresh frames): 38/40px at 360p, 116/118px at 720p (beats 1.44.0's 62px), 149/150px at 1080p. The ASS karaoke path is unaffected (no Python wrap; libass lays out the line). `examples/basic.json` (no captions) is byte-identical across 1.46.0/1.46.1 (md5 0843b0f3…).
- Regression test class TestLoop1461 (wrap-measured-matches-render + SRT caption fits at 360p/720p/1080p, incl. the inline caption at 720p).
