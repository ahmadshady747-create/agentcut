## 1.31.0 — Feature package (templates, word reveal, Arabic font)
- Arabic text now renders with Noto Naskh Arabic when the text
  contains Arabic characters (DejaVu has no Arabic glyphs); other
  texts keep DejaVu. Shaping verified by eye on frames from this
  FFmpeg build (libfribidi + libharfbuzz): letters join and word
  order is correct RTL.
- New text field `reveal: "word"`: the text expands as cumulative
  word prefixes staggered across its window in proportion to word
  length, so only the first word shows early and the full text
  shows by the window end. Word reveal ONLY, never per-character:
  Arabic letters join, and splitting them would break joining.
  Reveal cannot combine with animation (rejected with a fix).
- New blackboard template: synthetic board background
  (data/test_media/board.png, dark board + wooden frame),
  examples/blackboard.json and examples/brief-blackboard.json
  with chalk-style white/yellow texts, including an Arabic
  word-reveal line.
- New animated template: examples/animated.json and
  examples/brief-animated.json combining Ken Burns images,
  dissolve/circle_open transitions and a picture-in-picture
  overlay.

## 1.30.0 — Loop 6 round 4 (optional overlay audio mixing)
- Post-audit test fix (no behaviour change): test_continuous_keyframe_scaling now generates its own green test image instead of relying on a file created by a later test, which made it fail in clean copies.
- Overlay videos may set `volume` (0..2, default 0.0 muted, so
  every existing project renders exactly as before). When set,
  the overlay's audio is mixed into the final amix at the
  overlay's start, for its shown duration: looping overlays
  loop their trimmed source audio, others are trimmed to the
  shown duration, so audio never outlives the overlay on
  screen. It is not ducked; it sits alongside the main mix.
- Validation rejects volume above zero on an image overlay or
  a silent video (detected via ffprobe) with the overlay id
  and a fix hint. Proven by a volumedetect test: silence
  before and after the overlay window, audible audio inside
  it, at the right times.

## 1.29.3 — Loop 6 round 3 (continuous keyframe scaling)
- Scale keyframes no longer split overlays into <=0.5s
  fixed-scale segments: one scale filter with eval=frame
  interpolates width/height linearly on frame time t, rounded
  to even numbers (trunc(.../2)*2). Proven by a test that
  distinguishes the two: overlay edges at t=1.0 and t=1.1
  differ (the old segmentation gave identical edges), plus a
  combined scale + position keyframes render.
- Two FFmpeg behaviours found and documented while proving it:
  scale eval=frame DOES vary per frame (showinfo: 72, 98, 126,
  ... px), but encoding its output directly hides the variation
  (ffmpeg auto-scales back to the encoder's first-frame size)
  — test it through the overlay filter or via showinfo, never
  through a direct encode; and a format filter AFTER a varying
  scale pins the stream to its first frame's size, so the
  format conversion now sits BEFORE the scale.
- Honest limit found in-round: colorkey fails while filtering
  when its input frame size changes every frame, so
  chroma_key + scale keyframes is now rejected in validation
  with a fix hint, like mask + scale keyframes before it,
  instead of rendering broken. Mask-only and chroma-only
  scaling paths are unaffected.

## 1.29.2 — Loop 6 round 2 (mask + chroma key intersection)
- Fixed: mask + chroma_key on one clip or overlay no longer
  replaces keyed transparency. The keyed alpha is taken apart
  (alphaextract, exact frame size), multiplied by the mask factor
  (and by the opacity factor when opacity keyframes vary) in a
  gray geq using lum(X,Y) — this build's geq has no a(X,Y), so
  the fix deliberately never uses it — and merged back
  (alphamerge). Keyed colour stays transparent inside the mask,
  everything outside the mask stays hidden. The same path also
  fixes chroma_key + opacity keyframes, which previously
  destroyed keying. Mask-only and chroma-only paths are
  unchanged. Proven by a pixel test: overlay red inside the
  mask, keyed green inside the mask showing the timeline, red
  outside the mask hidden, and the same for a main clip (the
  previously failing case: masked-out red showed red).

## 1.29.1 — Loop 6 round 1 (detached process jobs)
- Jobs are detached processes, not server threads: render/preview
  jobs now run as `agentcut worker` subprocesses (Popen with
  start_new_session=True, no stdio inheritance), so closing the
  client's stdio connection can no longer kill a queued job.
- Job state is atomic JSON on disk (data/jobs, runtime data only,
  never synced): written at queue, start, completion and failure
  via temp-file + rename. get_job reads disk state, so a NEW MCP
  connection can poll jobs from earlier sessions; a job whose
  worker died without finishing reports failed instead of staying
  "running" forever. Outputs stay atomic (temp file then move).

## 1.29.0 — Loop 5 integration round (agent depth, no in-tool AI)
- Agent = intelligence, AgentCut = deterministic execution +
  verification. Nothing in this round generates, recognises or
  synthesises anything: no speech recognition, no TTS, no
  generative media, no model downloads, no network AI services.
  The agent supplies plans, SRT, audio and media as local files.
- New: timeline_summary — segments with start/end/kind/
  transition, texts, captions, audio, overlays — for agent
  reasoning without re-reading a project. MCP tool
  timeline_summary + CLI ./run.sh summary.
- New: brief-conformance checking — clips used, title present,
  format, duration within target_duration, captions present,
  audio present. Produce results now carry a `conformance`
  block; standalone MCP tool check_brief for hand-built
  projects. Briefs may also set target_duration and overlays.
- New: versioned snapshots — MCP tools snapshot_project,
  rollback_project, list_snapshots. The experiment loop is:
  snapshot, patch, preview/render, roll back on a bad result,
  retry. patch_project already validates before saving, so a
  failed patch never corrupts the stored project.
- Produce feedback extended: produce results carry an overlay
  summary (placement, keyframe count) and QA warnings for
  invisible (opacity 0) or frame-dominating (size >= 0.9)
  overlays, instead of failing silently.
- New-feature validation errors carry field + clip id + a
  concrete `fix` hint, audited by a test battery.

## 1.28.0 — Loop 5 round 8 (3840 export, stabilisation)
- Export cap raised: output width/height now 16..3840 (was
  16..1920); the large-image guard rises with the output size
  so a 3840 export does not throw away detail. Verified with a
  real 3840x2160 yuv420p render.
- New video clip `stabilize` (true/false): real two-pass
  vidstab — a detect pass at the clip's final scale writes a
  transform file, and the main render transforms with it. A
  failed detect fails the render rather than silently shipping
  an unstabilized clip. Stabilize does not combine with
  freeze_at, loop_count, reverse, speed_curve, speed or fit
  blur (rejected with a fix), because stabilisation needs the
  clip's original frame order and timing.

## 1.27.0 — Loop 5 round 7 (animated text, stroke)
- New text fields: `animation` slide_in (from the left),
  slide_out (to the right), slide_in_out, or pop (fontsize grows
  from half size), plus `animation_duration` (default 1.0s, must
  fit the text window; slide_in_out needs less than half).
  Slide uses drawtext x with main-timeline time t; pop uses a
  per-frame fontsize expression — both verified against this
  FFmpeg build (fontsize expressions need quoting, fixed
  in-round).
- New text fields: `stroke` (outline width 0..10) and
  `stroke_color` (named colour or #RRGGBB, default black) via
  drawtext borderw/bordercolor.

## 1.26.0 — Loop 5 round 6 (speed curves, audio denoise)
- New: video clip `speed_curve` — segments with start, end and
  speed that partition the trimmed source range (first start =
  trim_in, last end = trim_out, contiguous, speeds 0.25..4.0).
  Each segment is trimmed, retimed and concatenated; the clip
  duration is the sum of segment durations divided by speed.
  Speed curves do not combine with speed, freeze_at, loop_count,
  reverse or fit blur (rejected with fixes). split_clip maps a
  rendered split time back through the curve and splits the
  curve itself, preserving the total.
- New: `denoise` (true/false) on video clips and audio tracks —
  FFmpeg afftdn FFT noise reduction on that audio.

## 1.25.0 — Loop 5 round 5 (more transitions and effects)
- New transitions (all plain FFmpeg xfade names, verified in this
  build): dissolve, fade_black, fade_white, pixelize, radial,
  circle_open, circle_close, smooth_left/right/up/down,
  diag_tl/tr/bl/br, cover_left/right/up/down,
  reveal_left/right/up/down, distance, rect_crop. They overlap
  like crossfade and take transition_duration.
- New per-clip and per-overlay effects under generic names:
  blur (gblur sigma 0..20), sharpen (unsharp 0..5), vignette
  (true/false), hue (degrees -180..180), pixelize (block size
  2..64). Ranges are validated with a fix hint.

## 1.24.0 — Loop 5 round 4 (chroma key, masks, blurred fit)
- New: `chroma_key` on clips and overlays (colour name or
  #RRGGBB, plus chroma_similarity/chroma_blend): the keyed colour
  becomes transparent — over black for main clips, over the main
  timeline for overlays.
- New: `mask_shape` rect (mask_x/mask_y/mask_w/mask_h) or circle
  (mask_x/mask_y centre, mask_r radius), plus mask_feather for a
  soft edge. Masked main clips composite over black; mask
  coordinates are frame pixels (output frame for clips, overlay
  frame for overlays). Mask does not preserve chroma-key
  transparency on one overlay (this FFmpeg build's geq has no
  a(X,Y) function, verified), so avoid mask + chroma_key on one
  overlay; mask + scale keyframes is rejected with a fix.
- New: clip `fit: "blur"` — blurred, darkened background filling
  the frame with the clip contained centred on top, for
  mismatched aspect ratios. Ken Burns + blur and blur + chroma
  are rejected with fixes. Blur clips skip the prescale step so
  the background is not baked from a padded image.

## 1.23.0 — Loop 5 round 3 (split / reverse / freeze / loop)
- New clip fields for video clips: `reverse` (play backwards,
  video + audio), `freeze_at` (hold the frame at that second for
  the whole trim duration, silent), `loop_count` 2..8 (repeat the
  trimmed segment, video + audio). Mutually exclusive combinations
  are rejected with a `fix` hint.
- New `split_clip`: split one clip into two at a time inside its
  rendered duration (first part keeps the incoming transition,
  second part continues with none, timeline total unchanged).
  MCP tool split_clip (now 13 tools) + CLI ./run.sh split.
  Splitting a clip with loop_count or reverse is rejected with a
  fix, rather than silently producing a wrong split.

## 1.22.0 — Loop 5 round 2 (overlay keyframes)
- New: overlay `keyframes` animate position (x/y pixels, smooth
  linear interpolation in main-timeline time), opacity (smooth via
  geq alpha) and scale over time. Scale keyframes split the overlay
  into <=0.5s fixed-scale segments, because FFmpeg crop/scale
  dimensions are fixed at filter configuration time; position stays
  smooth across segments. Keyframe times are relative to the
  overlay start, missing fields inherit the previous keyframe or
  the overlay base value, and errors carry a `fix` hint. Scale
  keyframes on a looping video overlay are rejected with a fix
  (shorten it, or drop scale keyframes).

## 1.21.0 — Loop 5 round 1 (overlays)
- New: project `overlays` — picture-in-picture video/image tracks
  above the main timeline, with start, duration, size (fraction of
  output width), position preset or x/y pixels, and opacity. Video
  overlays loop when shown longer than their trimmed source.
  Overlay audio is not mixed in v1; sound comes from project audio
  tracks. Overlay errors carry a concrete `fix` hint.
- Example: examples/overlay.json.

## 1.20.1 — 2026-10-28
- Licence: AgentCut is now licensed under the Business Source License
  1.1 (see LICENSE). Licensor Ahmad Mohammad; Change Date 2030-10-28;
  Change License Apache License, Version 2.0; production use is
  permitted except for a competing agent-first video editor service.

## 1.20.0 — 2026-10-28
- Multi-format export: produce_variants renders one brief as
  vertical, square and landscape in one call (MCP tool
  produce_variants, now 12 tools, and ./run.sh produce-variants),
  each variant a full produce run with review frames and QA.

## 1.19.0 — 2026-10-28
- Brief soundtrack shortcut: `soundtrack` as a path string or a
  dict (src + volume/start/fades/loudnorm) becomes a music track
  after any explicit audio list.
- Brief sections: `sections` (title + clips each) flattens to one
  timeline and adds an auto title card at each section's start,
  in final-timeline coordinates after crossfade overlaps. A brief
  with both sections and clips is a structured error.

## 1.18.0 — 2026-10-28
- Producer review loop formalised in SKILL.md, written
  agent-agnostically: brief -> produce -> review frames + qa ->
  patch (brief or stored project) -> re-produce -> final check,
  including how to judge each qa warning (warnings are not
  errors; silent audio is expected for a music-less montage).

## 1.17.0 — 2026-10-28
- Producer QA in produce_video output: duration, resolution and
  pix_fmt checks plus black-frame (blackdetect) and silence
  (silencedetect) detection, reported as qa.warnings, not errors.
  An image montage with no music track is correctly reported as
  silent throughout.

## 1.16.0 — 2026-10-28
- Producer templates: brief-montage, brief-highlight,
  brief-announcement and brief-showcase in examples/ (format, media,
  title, Ken Burns where it fits). The montage template is rendered
  in tests: square, 5.0s, yuv420p, 5 review frames.

## 1.15.0 — Loop 4 round 1 (producer)
- New: produce_video — a one-call producer. A short brief (format
  preset, media paths, optional title/texts/audio) builds, validates
  and renders a finished video and extracts review frames, so any
  agent can produce a video in one step instead of assembling a full
  project by hand. MCP tool produce_video + CLI ./run.sh produce.
- New example: examples/producer-brief.json.

## 1.14.1 — 2026-10-28 (Loop 3, audit fix)
- Fixed: a malformed MCP tools/call (missing required argument)
  raised KeyError and killed the server. The serve loop now returns
  a structured INVALID_SCHEMA error and stays alive; regression
  test feeds a bad get_job call followed by ping.

## 1.14.0 — 2026-10-28 (Loop 3)
- Faster large-image handling: image clips are pre-scaled to output
  size (2x for Ken Burns, cover/contain aware) before the main
  filter graph, so xfade chains decode small files; a failed
  pre-scale falls back to the original file. Kane montage render
  13.5s -> 10.1s, same 720x1280 25.5s yuv420p output.

## 1.13.0 — 2026-10-28 (Loop 3)
- Caption styling: project fields caption_color, caption_size and
  caption_position apply as defaults to every caption (list captions
  and SRT cues) that does not set its own value; captions now honour
  position instead of always sitting bottom-centre.

## 1.12.0 — 2026-10-28 (Loop 3)
- Loudness normalisation: audio tracks accept loudnorm (true/false),
  single-pass EBU R128-style normalisation to -16 LUFS before mixing.

## 1.11.0 — 2026-10-28 (Loop 3)
- Per-clip filters: clips accept brightness (-1..1), contrast
  (0..2), saturation (0..3) and grayscale (true/false), rendered
  via eq after scaling, for video, image and Ken Burns clips alike.

## 1.10.0 — 2026-10-28 (Loop 3)
- Transition duration: clips may set transition_duration (default
  0.5s for xfade transitions, 0.3s for fade, max 2.0s, must be less
  than both clip durations); timeline math follows it.
- Text fade: texts/captions accept fade_in/fade_out seconds (sum must
  be less than the text duration), rendered via a drawtext alpha
  expression.

## 1.9.0 — 2026-10-28 (Loop 3)
- Atomic render outputs: ffmpeg writes to a temp file in the output
  directory and it is moved into place only on success, so a failed
  render never leaves a partial file and never overwrites a previous
  good output at that path.

## 1.8.0 — 2026-10-28 (Loop 3)
- Public-repo polish: clone/quickstart section in README, GitHub
  Actions CI running the full test suite plus a basic render smoke
  test on every push and pull request (local file only, not pushed).

## 1.7.0 — 2026-10-28
- Robustness: image clips are now probed at validation (unreadable files and video files passed as stills are structured errors, not render crashes); images larger than 2560px on the long side are downscaled before the main scale so 8K photos do not blow up memory/time; dimensions must be real integers (booleans/floats rejected with a clear message); empty text overlays are rejected; corrupt-video probe errors say so. Tests 22/22 after an audit-caught fix: the still/video check first used ffprobe duration, which wrongly rejects JPEG photos (image2 probes JPEG at 0.04s, png_pipe probes PNG at N/A); it now checks the video codec instead, with a JPEG regression test, and the Kane montage re-verified.

## 1.6.0 — 2026-10-28
- Added: poster-frame export — new MCP tool `poster_frame` and CLI `./run.sh poster <json> <png> [seconds]` (default: middle of the timeline). The frame comes from a real render, so it matches the final video. Out-of-range times are structured CLIP_OUT_OF_RANGE errors. Also fixed: the MCP server now reports the real VERSION instead of a stale 0.2.0. Tools/list is now 10 tools. Tests 20/20.

## 1.5.0 — 2026-10-28
- Added: audio fades (`fade_in`/`fade_out` seconds on audio tracks, via afade; their sum must be less than the timeline) and clip speed (`speed` 0.25..4.0 on video clips only; effective duration = trim duration / speed, video via setpts and clip audio via atempo chains). Out-of-range speeds, speed on image clips, and over-long fades are structured validation errors. Tests 19/19.

## 1.4.0 — 2026-10-28
- Added: text styling. Texts/captions accept `color` (#RRGGBB hex or a named colour), `box` (true/false, default true), and texts accept `position` presets (top_left, top_center, top_right, center, bottom_left, bottom_center, bottom_right) which override x/y. Captions keep their bottom-centre placement and ignore position. Invalid values are structured validation errors with the allowed set. Tests 18/18.

## 1.3.0 — 2026-10-28
- Added: xfade transitions beyond crossfade: wipe_left/right/up/down and slide_left/right/up/down (0.5s overlap, audio still acrossfades). The old crossfade name still means xfade fade. First-clip and short-clip rules now apply to all xfade transitions, and validation errors list the allowed names. Tests 17/17.

## 1.2.0 — 2026-10-28
- Added: crop-fill for clips (`fit`: `contain` default, or `cover`). Contain scales down and pads with black bars (previous behaviour); cover scales up and centre-crops to fill the frame. Ken Burns already fills the frame, so it implies cover. Unknown fit values are structured validation errors. Tests 16/16.

## 1.1.0 — 2026-10-28
- Added: Ken Burns for image clips (`ken_burns`: zoom_in, zoom_out, pan_left, pan_right). Implemented with zoompan on a single input frame (a looped input would multiply frames); pre-scaled to 2x output so zooming never upscales past the output. Video clips with ken_burns, and unknown values, are structured validation errors. Tests 15/15.

## 1.0.1 — 2026-10-28
- Fixed: text overlays now use drawtext textfile=, so ASCII apostrophes,
  colons and percents render exactly (previously Ballon d'Or broke the
  render). Kane montage re-rendered with the exact ASCII text, verified.
- Fixed (post-loop hotfix): final scale out_range=tv + format=yuv420p +
  -pix_fmt yuv420p, so image montages no longer come out as yuvj444p
  that phone players refuse. Tests 14/14.

- 1.0.0 — Production release: closing audit passed all gates G1-G10 together (see STATE.md).
# AgentCut changelog

- 0.1.0 — MCP server (9 tools), project schema v1, validation, FFmpeg
  render, preview with frames/contact sheet. Example 360x640 8.0s.
- 0.2.0 — True crossfade (xfade + acrossfade, 0.5s); fade-to-black kept.
- 0.3.0 — Audio track start offsets (adelay + apad).
- 0.4.0 — Sidechain music ducking under voice (sidechaincompress).
- 0.5.0 — SRT file import into captions (`captions_file`).
- 0.6.0 — Verified 1280x720 and 720x720 example projects.
- 0.7.0 — Packaging: single `./run.sh` command, this changelog.
