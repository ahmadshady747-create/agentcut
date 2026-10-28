# AgentCut — an agent-first video editor

AgentCut is not a human video editor. There is no timeline you drag clips
on. An agent describes a project in JSON, validates it, previews frames,
and renders a real MP4 with FFmpeg.

It is an original design. It is not a CapCut clone, it contains no CapCut
code, assets, templates or effects, and it does not depend on CapCut in
any way.

## Clone and quickstart (public repo)

```sh
git clone https://github.com/ahmadshady747-create/agentcut.git
cd agentcut
./run.sh validate examples/basic.json
./run.sh render examples/basic.json out.mp4   # 360x640, 8.0s, from the
                                              # synthetic media in data/test_media/
./run.sh serve                              # MCP server on stdio
```

A fresh clone needs only Python 3.10+ and `ffmpeg`/`ffprobe` on PATH;
there are no Python dependencies. The test suite runs in CI on every
push and pull request (`.github/workflows/test.yml`).

## Run it

```sh
cd ~/workspace/agentcut
./run.sh serve                              # MCP server on stdio
./run.sh validate examples/basic.json
./run.sh render examples/basic.json out.mp4
./run.sh probe path/to/clip.mp4
# The PYTHONPATH form (PYTHONPATH=src python3 -m agentcut ...) works too.
```

Requirements: Python 3.10+, `ffmpeg` and `ffprobe` on PATH. No Python
dependencies. No network access is used for media in v1: all `src` paths
are local files.

## Project format (v1)

```json
{
  "version": 1,
  "id": "my-first-cut",
  "title": "My first cut",
  "output": {"width": 360, "height": 640, "fps": 24},
  "clips": [
    {"id": "c1", "kind": "video", "src": "/path/clip1.mp4",
     "trim_in": 0, "trim_out": 3, "transition": "fade"},
    {"id": "c2", "kind": "image", "src": "/path/poster.png", "duration": 2}
  ],
  "texts": [
    {"text": "Hello", "start": 0.5, "end": 2.5, "size": 48,
     "x": "center", "y": 100}
  ],
  "captions": [
    {"text": "First line", "start": 0, "end": 2}
  ],
  "audio": [
    {"src": "/path/music.mp3", "kind": "music", "volume": 0.3},
    {"src": "/path/voice.mp3", "kind": "voice", "volume": 1.0}
  ]
}
```

Rules, enforced by validation before any render:
- `clips` must be non-empty and sequential. A video clip needs
  `trim_in`/`trim_out` inside the source duration; an image clip needs
  `duration`.
- Output width/height are 16..1920, fps is 1..60, total duration is
  at most 600 seconds in v1.
- Text `x` is a pixel number or `"center"`; `y` is a pixel number.
  Captions are rendered centred near the bottom.
- Audio `kind` is `"voice"` or `"music"`, with an optional `start`
  in seconds (default 0; a track starting at or after the timeline
  end is a validation error). When voice and music are both present, music is ducked
  with a real sidechain compressor keyed off the voice mix
  (threshold 0.02, ratio 8, attack 200ms, release 1000ms) — unless
  the project sets an explicit `ducking` object, which replaces
  the sidechain with deterministic volume automation (see Pro pack).
- Image clips may set `"ken_burns"` to `zoom_in`, `zoom_out`,
  `pan_left` or `pan_right` (default `none`). It is image-only;
  on a video clip, or with an unknown value, validation fails
  with a structured error.
- Transition `"fade"` means fade to/from black at clip boundaries
  (0.3s). Transition `"crossfade"` is a true crossfade: video via
  xfade and audio via acrossfade, 0.5s overlap, so a 3s + 3s timeline
  with a crossfade renders as 5.5s. Both clips in a crossfade must be
  longer than 0.6s, and text/caption times are in final-timeline
  coordinates (after overlaps).

## Examples that actually render

- `examples/basic.json` — 360x640 vertical, 8.0s, video + image + text + captions.
- `examples/crossfade.json` — true crossfade, 3s + 3s -> 5.5s.
- `examples/landscape.json` — 1280x720, 8.0s.
- `examples/square.json` — 720x720, 8.0s.
- `examples/srt.json` + `examples/captions.srt` — captions from an SRT file.

## MCP tools

- `probe_media` — ffprobe a local file: duration, size, streams.
- `create_project` — write a validated project JSON to the project store.
- `get_project` — read a project back by id.
- `list_projects` — list stored project ids.
- `patch_project` — merge-patch a project (top-level fields and lists
  replace; a clip with an existing id is replaced in place).
- `validate_project` — validate without rendering.
- `preview_project` — render a low-resolution proxy and extract frames
  plus a contact sheet.
- `render_project` — start an async render job.
- `get_job` — poll a render/preview job: queued, running, done, failed,
  with output path and ffprobe info on success, or a structured error.
  Jobs run as detached processes and survive the client closing
  stdio; a new connection can poll jobs from earlier sessions.

Every error is a JSON object with `code` and `message`, plus `field`
and `clip_id` when they apply. Error codes: `INVALID_SCHEMA`,
`FILE_NOT_FOUND`, `CLIP_OUT_OF_RANGE`, `EMPTY_TIMELINE`,
`INVALID_DIMENSION`, `DURATION_TOO_LONG`, `FONT_NOT_FOUND`,
`RENDER_FAILED`, `JOB_NOT_FOUND`, `PROJECT_NOT_FOUND`.

## Trust model (read this)

This server runs locally and trusts the agent that runs it, exactly like
other local MCP servers. It reads local files the agent names and runs
FFmpeg on them. Do not expose it unauthenticated over a network, and
do not point it at media you have not checked. That is a v1 boundary,
not a bug to work around silently: a hosted version needs a sandbox,
an allow-list, quotas and authentication first.

## Version

See `VERSION`. Changelog entries live in `STATE.md` round log.

Crop-fill: a clip may set `fit` to `contain` (default, pad with black bars) or `cover` (scale up and centre-crop to fill the frame). Ken Burns implies cover.

Transitions: `none`, `fade` (to/from black), and xfade transitions with acrossfaded audio (default 0.5s overlap; set `transition_duration` per incoming clip, max 2.0s, less than both clip durations): `crossfade`, `wipe_left`, `wipe_right`, `wipe_up`, `wipe_down`, `slide_left`, `slide_right`, `slide_up`, `slide_down`.

Text styling: texts/captions accept `color`, and `fade_in`/`fade_out` seconds (sum less than the text duration); texts/captions accept (#RRGGBB or a named colour) and `box` (true/false); texts also accept `position` presets (top_left, top_center, top_right, center, bottom_left, bottom_center, bottom_right) which override x/y.

Loudness: audio tracks accept `loudnorm` (true/false), single-pass normalisation to -16 LUFS before mixing.

Audio fades: tracks accept `fade_in`/`fade_out` in seconds (sum must be less than the timeline). Clip speed: video clips accept `speed` 0.25..4.0; effective duration is trim duration divided by speed.

Per-clip filters: a clip may set `brightness` (-1..1), `contrast` (0..2), `saturation` (0..3) or `grayscale` (true/false).

Caption styling: project fields `caption_color`, `caption_size` and `caption_position` are defaults for every caption (including SRT cues, which carry no styling of their own); a caption that sets its own value keeps it.

Poster: `./run.sh poster <json> <png> [seconds]` exports one full-resolution frame (default: middle of the timeline), or the `poster_frame` MCP tool.

Large images: image clips are pre-scaled to output size before the main filter graph (2x for Ken Burns), so the graph decodes small files; a failed pre-scale falls back to the original.

Robustness: image clips are probed (a video file passed as a still is rejected); images over 2560px on the long side are downscaled before the main scale.

## Producer: one call, any agent

AgentCut is built so that Muse, Claude, Cursor, or any other MCP
client can act as a video producer, not just as a renderer. The
producer call takes a short brief, not a full project:

```sh
./run.sh produce examples/producer-brief.json out.mp4
```

A brief needs only a format preset (`vertical`, `square` or
`landscape`), a list of media paths, and optionally a title, texts
or audio. Ready-made starting briefs live in `examples/`:
`brief-montage` (square photo montage), `brief-highlight`
(landscape video highlights), `brief-announcement` (vertical
announcement) and `brief-showcase` (square product-style showcase).
Copy one, swap in your own media paths, and produce. A brief
also accepts a `soundtrack` shortcut (a path, or a dict with
volume/fades) and `sections` — titled groups of clips that each
get an auto title card at the section's start. Image clips default to 3s, video clips default to their
full duration, and later clips crossfade by default. The call
validates, renders, and returns review frames from the finished
video, so the producing agent reviews its own frames, patches what
is wrong, and re-produces. For all three shapes at once, use
`./run.sh produce-variants <brief.json> <dir>` (MCP tool
`produce_variants`): vertical, square and landscape renders of
the same brief, each with its own review frames and `qa` block.
The produce result also carries a `qa` block:
duration/resolution/pix_fmt checks plus black-frame and silence
detection, reported as warnings (a silent image montage, for
example, is reported as silent throughout rather than failing).
AgentCut itself does not generate
footage, voices or music: the producing agent supplies local media,
and AgentCut turns it into a finished, checked video.

## Licence

AgentCut is licensed under the Business Source License 1.1 — see
[LICENSE](LICENSE). In short: you may copy, modify and use it,
including in production, except to provide a competing agent-first
video editor service to third parties. On the Change Date
(2030-10-28) this version converts to the Apache License, Version
2.0. BSL 1.1 is not an Open Source licence.

## Overlays (picture-in-picture)

A project may carry an `overlays` list on top of the main timeline.
Each overlay has an id, a kind (`video` or `image`), a local src, a
start second and a duration inside the timeline, a size as a
fraction of the output width (default 0.35), a position preset
(`top_left`, `top_center`, `top_right`, `center`, `bottom_left`,
`bottom_center`, `bottom_right`) or x/y pixel offsets, and an
opacity 0..1. Video overlays trim like clips and loop when shown
longer than their trimmed source. An overlay video may also set
`volume` (0..2, default 0.0 muted): its audio is then mixed in
at the overlay's start, for its shown duration, alongside the
project audio tracks. Volume above zero on an image overlay or
a silent video is a validation error with a fix hint. See
examples/overlay.json. Overlay validation errors carry a `fix`
hint, so an agent can correct the project directly.

## Overlay keyframes

An overlay may carry `keyframes`: a list of at least 2 entries with
a `time` in seconds since the overlay starts, plus any of `x`, `y`
(pixels), `scale` (fraction of output width) and `opacity` (0..1).
Values interpolate linearly between keyframes; a field a keyframe
omits keeps the previous keyframe's value, or the overlay's base
value. Position, opacity and scale all animate smoothly per
frame (scale uses FFmpeg scale eval=frame; note that a format
filter after a varying scale pins the stream to its first
frame's size, so the format conversion sits before the scale).
Scale keyframes on a looping video overlay are rejected —
shorten the overlay to its trimmed source duration, or drop the
scale keyframes — and so is chroma_key with scale keyframes,
because colorkey cannot handle per-frame size changes on this
FFmpeg build.

## Split, reverse, freeze, loop

Video clips accept `reverse` (play backwards), `freeze_at` (a
second inside the trim range; that frame holds for the whole
trim duration, silently) and `loop_count` 2..8 (repeat the
trimmed segment, video and audio). These three do not combine
with each other or with `speed` on one clip; validation says so
with a fix hint. `split_clip` (MCP tool, or `./run.sh split
<json> <clip_id> <seconds>`) splits one clip at a time inside
its rendered duration: the first part keeps the incoming
transition, the second part continues with none, and the
timeline total does not change. Store the returned project
with create_project to keep the split.

## Chroma key, masks, blurred fit

Clips and overlays accept `chroma_key` (a colour name such as
`green`, or a #RRGGBB value) with optional `chroma_similarity`
and `chroma_blend`: the keyed colour becomes transparent.
`mask_shape` is `rect` (mask_x, mask_y, mask_w, mask_h) or
`circle` (mask_x, mask_y centre, mask_r radius), with an
optional `mask_feather` in pixels for a soft edge; masked main
clips show black outside the mask. A clip may use
`fit: "blur"` instead of contain/cover: a blurred, darkened
copy of the clip fills the frame behind the contained clip.
Mask coordinates are frame pixels. Mask and chroma_key now
intersect on clips and overlays: the keyed colour stays
transparent inside the mask, and everything outside the mask
stays hidden. Do not combine mask with scale
keyframes (rejected with a fix).

## More transitions and effects

Besides crossfade, fade, wipe and slide, clips accept dissolve,
fade_black, fade_white, pixelize, radial, circle_open,
circle_close, smooth directions, diagonal directions, cover and
reveal directions, distance and rect_crop — all as incoming
transitions with an optional transition_duration. Clips and
overlays accept generic effects: `blur` (sigma), `sharpen`,
`vignette` (true/false), `hue` (degrees) and `pixelize` (block
size). Effect ranges are validated with fix hints.

## Speed curves and denoise

A video clip may carry `speed_curve`: segments with `start`,
`end` and `speed` that partition the trimmed source range
exactly, with no gaps or overlaps. The clip duration is the
sum of each segment's duration divided by its speed. A curve
does not combine with `speed`, `freeze_at`, `loop_count`,
`reverse` or blurred-background fit. `split_clip` on a curved
clip maps the rendered split time back through the curve and
splits the curve, keeping the total duration. Audio noise
reduction is `denoise: true` on a video clip or an audio
track (FFmpeg afftdn).

## Animated text and stroke

Texts (and captions) accept `animation`: `slide_in` (slides in
from the left), `slide_out` (slides out to the right),
`slide_in_out`, or `pop` (grows from half size to full size),
with `animation_duration` in seconds (default 1.0, must fit
the text window). `stroke` (0..10) and `stroke_color` add an
outline around the text.

## Export cap and stabilisation

Outputs go up to 3840 wide (and tall). A video clip may set
`stabilize: true` for real two-pass vidstab stabilisation:
a detect pass at the clip's final scale, then a transform
pass in the main render. Stabilisation does not combine
with freeze, loop, reverse, speed changes or blurred fit
on the same clip.

## Agent integration

AgentCut does not generate, transcribe or synthesise media:
the producing agent supplies plans, captions, audio and media
as local files, and AgentCut edits deterministically and
verifies the result. Integration tools: `timeline_summary`
(MCP tool, or `./run.sh summary <json>`) for reasoning about
a timeline; `check_brief` for verifying a produced project
against its brief (produce results also carry a
`conformance` block, and briefs may set `target_duration`
and `overlays`); and `snapshot_project` /
`rollback_project` / `list_snapshots` for safe
experimentation — snapshot, patch, preview, roll back on a
bad result. Validation errors on new features carry a
`fix` hint with the field and clip id.

## Templates

Ready projects and produce briefs an agent can copy:

- Blackboard: `examples/blackboard.json` (project) and
  `examples/brief-blackboard.json` (brief) — a synthetic dark
  board with a wooden frame (`data/test_media/board.png`) and
  chalk-style white/yellow texts, including an Arabic line.
- Animated slideshow: `examples/animated.json` and
  `examples/brief-animated.json` — Ken Burns images, dissolve
  and circle_open transitions, and a picture-in-picture overlay.

## Word reveal

A text may set `reveal: "word"`. At render time it expands into
cumulative word prefixes staggered across its window in
proportion to word length: only the first word is visible early,
the full text is visible by the window end. Reveal is word-only
by design — Arabic letters join, so per-character reveal would
break joining. Reveal cannot combine with `animation` on one text.

## Pro pack (1.32.0): easing, grading, ducking, chapters, delivery, review aids

- Easing keyframes: an overlay keyframe may set `easing` to
  `linear` (default), `ease_in`, `ease_out` or `ease_in_out`.
  It shapes scale, position and opacity interpolation between
  keyframes (FFmpeg time expressions on normalised segment
  time). Set it on either keyframe of a segment; if both set
  a non-linear easing, the later keyframe wins. Example:
  `{"time": 0, "x": 0, "easing": "ease_in"}, {"time": 4, "x": 200, "easing": "ease_in"}`.
  Unknown values fail validation with a fix hint.
- Colour grading: a clip may set `grade` to `cinematic`,
  `teal_orange`, `warm`, `cool` or `noir`. These are plain
  eq/colorbalance/saturation combinations (see CHANGELOG for
  each definition) — they are not film stocks and do not
  emulate any film process. A clip may also set `lut` to a
  local `.cube` file, rendered via FFmpeg lut3d; the file
  must exist and end in `.cube`. Grade and LUT may combine.
- Deterministic ducking: a project may set `ducking`, e.g.
  `{"voice_track": 1, "amount_db": 12, "fade": 0.25}` where
  `voice_track` is the index of the voice track in `audio`
  (or that track's src), `amount_db` is how far music drops
  (0..30 dB) and `fade` is the ramp time in seconds (0..2).
  Optional explicit `windows` (`[{start, end}]`) override
  the derived window (voice start + probed voice duration).
  This is volume automation from time windows, not sidechain
  compression and not level detection. Without `ducking`,
  voice+music still uses the sidechain compressor as before.
  See `examples/ducking.json` and `examples/brief-ducking.json`.
- Producer QA now also reports measured loudness (LUFS via
  loudnorm), sample-peak level (astats; warned above -1 dB)
  and freeze-frame segments (freezedetect) — warnings only,
  never changes to the render.
- Chapters: a project `chapters` list, e.g.
  `[{"title": "Intro", "start": 0}, {"title": "End", "start": 4}]`,
  is written into the output file's metadata and can be
  verified with `ffprobe -show_chapters`. Produce briefs
  accept `chapters`, and section briefs get chapters from
  section titles when no explicit list is given.
- Delivery presets: `youtube_1080`, `shorts_720` and
  `instagram_square` (size, fps, video bitrate and loudness
  target each — see CHANGELOG). Use `delivery_preset` in a
  produce brief, or `delivery_presets` (a list) in a
  produce-variants brief. Resolution rule, enforced and
  reported in `delivery_preset_applied`: an explicit
  width/height or format in the brief always wins over a
  preset's size; a preset never silently overrides an
  explicit request. Presets are delivery settings, not
  platform guarantees — platforms may re-encode on upload.
- Review aids: preview proxies draw title-safe (10% margin)
  and action-safe (5% margin) guide boxes by default, and
  may burn in a running timecode on request; the preview
  result flags both. Final renders have no guides and no
  timecode unless the project explicitly sets
  `timecode: true`.

## Arabic text

When a text contains Arabic characters, AgentCut uses Noto Naskh
Arabic (or Noto Sans Arabic as fallback) instead of DejaVu, which
has no Arabic glyphs. On this FFmpeg build (libfribidi +
libharfbuzz) Arabic shaping was verified by eye on rendered
frames: letters join correctly and word order is right-to-left.
On a build without that shaping support, Arabic may render
disconnected; the font rule still applies, but the shaping
result depends on the local FFmpeg build.
