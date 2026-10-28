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
  (threshold 0.02, ratio 8, attack 200ms, release 1000ms).
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
