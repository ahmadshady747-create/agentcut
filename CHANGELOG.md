## 1.36.0 — Text & graphics polish (wrapped captions, shadows, scrims, overlay dressing, rounded text boxes)

User order 2026-10-29: improve the writing and the drawing
inside the rendered video — readability over bright panels,
clipped long captions, hard rectangular boxes, flat overlays.
All deterministic, no AI, no new dependencies. Tests 86/86
(77 prior + 9 new) in the main tree and in a fresh copy.
Example: examples/text-polish.json.
- Wrapped captions (headline fix): a caption cue wider than
  the frame is automatically word-wrapped into centred lines
  that fit within `caption_max_width` (default 0.9 of frame
  width), stacked upward from the caption baseline (top
  presets stack downward, center blocks centre), at
  `caption_line_spacing` (default 1.35). Wrapping happens on
  the logical text, then the existing BiDi transform applies
  per line, so Arabic words stay whole and joined and read in
  the correct order; cue timing is unchanged. Single-line
  captions render exactly as before (frame-proven identical
  across max-width settings). This resolves the root cause
  behind the 1.34.0 overflow warning for captions; the warning
  still fires for the one case wrapping cannot fix — a single
  word wider than the frame, which stays whole and clips
  (documented fallback, never a crash). The collision
  estimator now uses the wrapped block, so an overlay crossing
  a wrapped line warns.
- Text readability styling: texts/captions accept `shadow_x` /
  `shadow_y` (-20..20) and `shadow_color` (named or #RRGGBB),
  alongside the existing `stroke`/`stroke_color` and `box`.
  Captions get a subtle default shadow (2,2 black) so they
  read over bright panels — INTENTIONAL appearance change vs
  1.35.0: caption pixels now carry a 2px shadow where they
  did not; setting either shadow axis takes over both, so
  shadow_x=0 alone disables the caption default. Plain texts
  default to no shadow.
- Scrim gradients: optional project-level `scrims: {top,
  bottom}` — each side true (defaults opacity 0.6, depth
  0.35 of frame height) or {opacity 0..1, depth 0.05..0.6}.
  A black gradient fading to transparent, rendered with a
  geq alpha ramp, over the clips and under overlays and
  texts. Default off, so old projects render identically.
- Overlay dressing: optional per-overlay `border_width`
  (0..20) + `border_color`, `corner_radius` (0..200, rejected
  above half the smaller displayed side), and a drop shadow
  (`shadow_dx`/`shadow_dy` -100..100, `shadow_blur` 0..30,
  `shadow_opacity` 0..1). Border is drawn first so rounding
  rounds the border too; the shadow is a black blurred copy
  composited underneath at an offset. Shadow is active only
  when an offset or a blur is set — setting only
  shadow_opacity does nothing. All default off.
- Rounded text background boxes: texts/captions accept
  `box_radius` (0..100), `box_color` (default black) and
  `box_padding` (default 12), plus `box_opacity` (default
  0.5). A generated rounded-rect colour source (geq alpha
  mask, same corner math as overlay rounding) is overlaid
  under the text in place of drawtext's rectangular box,
  sized from the same width estimate the validator uses.
  Requires `box: true`; rejected with animation or fades
  (the pill would not move or fade with the text). Wrapped
  caption lines each get their own pill. INTENTIONAL
  appearance change only for texts that set box_radius.

## 1.35.0 — On-device rendering (Termux render packs + portable fonts)

User order 2026-10-29: make AgentCut work on the user's own
device and edit using its resources. The agent has no shell
channel to the phone and Muse does not host user MCP servers,
so the deliverable is user-driven: the agent builds a
self-contained render pack, the user renders it on their phone
and reports back. The on-phone trial itself cannot be performed
by the agent and stays listed as the remaining completion gate
(see docs/ON_DEVICE.md). Tests 77/77 (73 prior + 4 new) in the
main tree and in a fresh copy.
- Portable fonts: font discovery was absolute Linux paths only
  (/usr/share/fonts/...), which do not exist on Android/Termux,
  and the validation gate demanded DejaVu for any text — so an
  Arabic-only project failed on a Noto-only machine even though
  it could render, while an Arabic project with only DejaVu
  passed validation and then rendered tofu boxes. Now the order
  is $AGENTCUT_FONT_DIR, then the bundled fonts/ directory
  (NotoNaskhArabic-Regular under OFL-1.1, DejaVuSans under the
  Bitstream Vera licence, both licences shipped in fonts/),
  then Termux $PREFIX font dirs, then system paths. Font
  requirements are checked per text by script: Arabic text with
  no Arabic font, or Latin text with no Latin font, fails with
  FONT_NOT_FOUND naming the fix; the renderer never falls back
  to a Latin font for Arabic text (defence in depth: a
  structured FONT_NOT_FOUND render error). Latin fallback
  behaviour is unchanged. Tests: font env-dir-wins, bundled
  fonts found, Arabic-without-Arabic-font structured.
- Render packs: new CLI `./run.sh package <project.json>
  <outdir>` builds a self-contained directory — rewritten
  project.json (every media reference rewritten to a relative
  media/... path, basename collisions deduped), every referenced
  file (clips, LUTs, overlays, audio, captions SRT), a copy of
  the AgentCut source tree, the bundled fonts, an executable
  render.sh (preflights python3/ffmpeg/ffprobe, sets
  AGENTCUT_FONT_DIR from the pack, renders project.json to
  output.mp4) and a README. It renders with no network, no MCP
  server and no repo checkout. Invalid projects and missing
  files refuse to pack. Test: pack renders in a clean directory
  from another cwd under a restricted TMPDIR with no font env.
- Termux setup: scripts/setup-termux.sh (idempotent: pkg
  install python ffmpeg, version checks, drawtext/xfade/
  loudnorm filter checks with vidstab reported as optional,
  fonts into ~/.agentcut-fonts with a persistent bashrc export,
  a 1-second Arabic smoke render) and docs/ON_DEVICE.md, the
  Arabic on-device guide: pack workflow, full-repo workflow,
  and plain honesty about heat, throttling, foreground-only
  rendering and keeping the phone charging. Detached MCP jobs
  are unreliable on Android (the OS can kill background work),
  so on-device rendering is foreground CLI by design.
- Also fixed in passing: __init__.__version__ was stale at
  1.33.0 while VERSION said 1.34.0 (the server reads VERSION,
  so MCP reported correctly; now both say 1.35.0).

## 1.34.0 — Defect-fix loop (audit + fixes, no new features)

User order 2026-10-29: fix ALL defects in AgentCut code, do not
close until sure. Phase 1 audited every source file, the tests,
the docs and the examples, re-proved every documented limit, and
ran an adversarial edge-case checklist; Phase 2 fixed each
confirmed defect with a failing test/repro first. No new tools,
no AI features, no CapCut-like features. Tests 73/73 (62 prior +
11 new loop tests) in the main tree and in a fresh copy.
- Server crash fixed (F1): serve() died with AttributeError on
  a parsed non-object JSON line ([1,2,3]) and on non-dict
  tools/call params — a malformed payload killed the long-lived
  server and hung the client. Now: unparseable lines get a
  JSON-RPC parse error (-32700), non-object requests get
  invalid-request (-32600), non-dict params get a structured
  INVALID_SCHEMA tool error, and the server stays alive.
  Test: test_loop134_server_survives_non_object_json.
- Id validation fixed (F2): load endpoints passed ids straight
  into filesystem paths (only _save_project enforced
  [A-Za-z0-9_-]+), so traversal ids could read outside
  projects/jobs/snapshots, and list_snapshots would create
  directories for arbitrary ids. Every id-taking tool now
  rejects ids outside the pattern with INVALID_SCHEMA.
  Test: test_loop134_id_traversal_rejected.
- Text geometry validated (F3a): text size (non-numeric,
  negative, zero, >240) and text x/y (non-numeric, negative)
  used to pass validation and fail only inside FFmpeg. Now:
  size must be a number 8..240; text x must be "center" or a
  number >= 0; text y must be a number >= 0; captions must not
  set x/y at all (render ignores them — use position or
  caption_position). Test: test_loop134_text_geometry_validation.
- Silently ignored fields rejected (F3b): video clips must not
  set "duration" (duration comes from trim/speed) and image
  clips must not set trim_in/trim_out (duration is the length);
  both now fail validation with a fix hint. No example or test
  used either. Test: test_loop134_ignored_fields_rejected.
- Broken media rejected (F3c): ffprobe "succeeds" on garbage
  named .png (image2 probe, 0x0 dimensions) and an audio-only
  file validated as a video clip — both failed only at render
  time. Now: image and video media need width > 0 and height >
  0, and video clips need a video stream. Overlays get the same
  dimension check. Test: test_loop134_broken_media_rejected.
- Silent + loudnorm structured (F3d): loudnorm on digital
  silence measured -inf, emitted NaN, and the AAC encoder
  failed with a cryptic error. Render now pre-checks loudnorm
  tracks with loudnorm's own measurement and returns a
  structured error naming the track and the fix (remove
  loudnorm from the silent track). Note: volumedetect is NOT
  the detector — on this build it reports -91.0 dB, not -inf,
  for digital silence. Test: test_loop134_silent_loudnorm_structured.
- Text overflow warnings (F4): single-line centred drawtext
  clips both edges when a line is wider than the frame, with
  no signal. Validation now adds text_overflow_warning
  (estimated width, same approximation as the collision check)
  to info["warnings"], surfaced by validate, timeline_summary
  and produce QA. Warnings only, never blocking.
  Test: test_loop134_text_overflow_warning.
- Overlay box parity (F5): _overlay_box_at required BOTH x and
  y for explicit placement while render applies x/y per axis,
  so an x-only overlay's collision box was the preset box.
  Estimation is now per-axis, matching render.
  Test: test_loop134_overlay_box_per_axis.
- Validation purity (F6): validate_project mutated the
  caller's audio tracks (resolving srcs, coercing starts) when
  run from another directory. Tracks are now copied; resolved
  audio lives in info["audio"], the render uses it, and stored
  projects keep paths as given. Test:
  test_loop134_validate_does_not_mutate_audio (validates and
  renders from /tmp with relative paths).
- Structured brief/CLI numbers (F7): non-numeric produce-brief
  durations/speeds/trims and bad numeric CLI arguments used to
  escape as raw tracebacks outside the MCP path. Both now
  return/print structured errors. LUT filter paths escape
  backslashes. Tests: test_loop134_produce_bad_numbers_structured,
  test_loop134_cli_bad_numbers_no_traceback.
- Docs (F8): overlay precedence documented (explicit x/y win
  per axis over a position preset; texts keep the documented
  position-wins rule); the new warnings, the loudnorm-silent
  error, the stream/dimension requirements, SRT overlap
  behaviour (overlapping cues render stacked), and job/output
  persistence (no auto-cleanup by design; old data/jobs and
  data/outputs may be deleted by hand) are documented in
  README/SKILL.
- Deferred with reasons (see STATE.md): bool-as-number
  coercion, integer-field float truncation (<=1px), job/output
  auto-cleanup, %/# font coverage, chroma+scale keyframes,
  schedule-based ducking, sample-peak QA, grading presets,
  colourbalance-on-gray, 3840/600s caps. Verified not defects:
  all transition/animation/chapter boundaries, non-UTF8 and
  malformed SRT handling, render determinism (byte-identical
  re-renders), special-character texts (backslash, colon,
  quote, newline, percent), freeze warnings on still-image
  timelines (intentional, asserted by an existing test),
  preview guides/timecode never leaking into finals, detached
  job survival.

## 1.33.0 — Arabic BiDi fix + layer/text collision warnings

- Arabic BiDi display fix (render.py `bidi_display_text`, applied
  to EVERY rendered text overlay and caption at the point just
  before writing the temporary drawtext textfile — titles, pop
  texts, word-reveal prefixes and SRT cues alike; stored project
  text stays in logical order, only the rendered form changes;
  the review timecode drawtext is a separate filter and is NOT
  touched). Rule: a line containing an Arabic character
  (U+0600..U+06FF) is wrapped RLE (U+202B) ... PDF (U+202C) with
  RLM (U+200F) before a trailing punctuation run
  (!, ؟, ., ،, %, ٪); Latin-only lines are returned
  byte-identical, so non-Arabic renders are pixel-identical.
  Root cause, verified by frame inspection on this FFmpeg
  build (libfribidi + libharfbuzz): drawtext shapes Arabic
  letters (they join) but picks paragraph direction from the
  first strong character, so a digit-led line renders LTR —
  plain "2 دولار" shows 2 left / دولار right (read as
  "دولار 2"), plain "80٪ هواء!" puts ! on the far right, and a
  plain sentence's trailing ، sits on the far right. With the
  controls: "2 دولار" shows دولار left / 2 right, "80٪ هواء!"
  shows ! far left / 80٪ far right, and a sentence's ، sits
  far left; the title "اقتصاد القرد 2" renders byte-identical
  plain vs wrapped (already correct, controls are a safe
  no-op there), and the controls never render as boxes.
  TESTED AND REJECTED: programmatic token reversal. Feeding
  "دولار 2" for logical "2 دولار" renders byte-identical to
  the plain line (same PNG hash) — this build's shaper
  reorders directional runs itself, so token reversal is
  undone before painting and must NOT be used. Both % and ٪
  forms were frame-checked (order correct in both; see the
  font limit below for why ٪ is still the form to write).
  Tests: test_bidi_display_text_transform (unit, incl.
  idempotence and Latin byte-identity) and
  test_bidi_arabic_renders (render smoke); eye-verification
  is recorded in STATE.md, never claimed from the transform.
- Layer/text collision warnings (validate.py
  `check_layer_text_overlap`, primary place; surfaced in
  info["warnings"] so the validate_project MCP tool, the
  timeline_summary result and produce_video's QA warnings all
  carry them). The check estimates text boxes (x/y or
  position, size, width = text length x size x 0.6, height =
  size x 1.4), overlay boxes over time (position/size plus
  keyframe paths, linearly interpolated — easing ignored —
  sampled every 0.5s plus keyframe times and window
  boundaries; constant keyframe scales use the overlay's base
  size, mirroring render.py), and caption bottom-bar regions
  during their windows. An overlay path intersecting an
  active text region in the same time window records
  code "layer_text_overlap_warning" naming the overlay id,
  the overlapping text and the colliding time range.
  Warnings NEVER block rendering or validation (warnings
  only, never errors). Test: the old island layout (badge
  gliding at y=240 across keyword rows at y=340) produces
  the warning naming badge + keyword; the new layout
  (badge at y=1030) produces none. The approximation
  (estimated widths, sampled times, linear keyframe
  interpolation) is documented in the function and here.
- Known font limits, unchanged and restated: "%" (U+0025)
  and "#" (U+0023) are MISSING in both Noto Naskh Arabic and
  Noto Sans Arabic here (they render as tofu boxes); the
  Arabic percent sign ٪ (U+066A) DOES render in Noto Naskh
  Arabic. Write rendered % forms as ٪, and titles without #
  (e.g. "اقتصاد القرد 2"). Literal % in textfile content
  is safe since 1.32.1 (expansion=none) — it renders, as a
  tofu box in Arabic fonts, not as an empty overlay.

## 1.32.1 — Percent-caption fix (found in production)
- Text overlays (texts and SRT captions) now set drawtext
  `expansion=none`. FFmpeg expands %{...} sequences in textfile
  content too, so a stray % made the whole overlay render empty
  while the render itself succeeded: found while producing the
  island-short video, where the caption containing "80%" was
  invisible at every time in its window while the other three
  captions rendered, and reproduced minimally (a caption with
  "80%" invisible, the same caption without % visible, %% no
  help). Regression test test_percent_caption_renders covers it.
  The review timecode drawtext keeps normal expansion because it
  genuinely needs %{pts:hms}.
- Known font limit, unchanged: "#" renders as a missing-glyph box
  in both Noto Naskh Arabic and Noto Sans Arabic on this system,
  so a title needing "#" should be written without it (e.g.
  "اقتصاد القرد 2") until a font with # is chosen.

## 1.32.0 — Pro pack (easing, grading, ducking, chapters, delivery, review aids)
- Easing keyframes: overlay keyframes accept `easing` — `linear`
  (default, the old behaviour), `ease_in` (u^2, slow start),
  `ease_out` (1-(1-u)^2, slow end) or `ease_in_out` (slow both
  ends) — applied to scale, position and opacity via FFmpeg
  time expressions on normalised segment time. The segment's
  easing comes from its end keyframe when that keyframe sets a
  non-linear easing, else its start keyframe. An unknown easing
  value is a validation error with a fix hint, never a silent
  fall back to linear.
- Colour grading: per-clip `grade` presets — `cinematic`,
  `teal_orange`, `warm`, `cool`, `noir` — each a documented
  eq/colorbalance/saturation filter combination, not a claim
  of film emulation; and per-clip `lut`, a local .cube file
  rendered via lut3d (validated: must exist and end in .cube,
  with a fix hint). Grade and LUT may combine; the LUT runs in
  RGB (format conversion around lut3d) and back to yuv420p.
- Deterministic audio ducking: a project `ducking` object
  (`voice_track` as an audio-list index or a voice track's src,
  `amount_db` 0..30, `fade` 0..2s, optional explicit `windows`)
  replaces the automatic sidechain compressor for music tracks.
  Music gain follows the voice track's time windows — derived
  from the voice track's start plus its probed duration, or
  the explicit windows — dropping by amount_db with linear
  fades, evaluated per frame (volume eval=frame). This is
  volume automation from time windows, not sidechain
  compression and not level detection: it ducks on schedule
  even if the voice track is quiet, and never outside windows.
  Without a ducking object, voice+music still uses the
  sidechain compressor as before.
- Producer QA extended (warnings only, never changes to the
  render): measured loudness (loudnorm input_i/input_tp/
  input_lra, in LUFS/dB), sample-peak level (astats, warned
  above -1 dB as close to clipping — a sample peak, not a
  true-peak claim) and freeze-frame segments (freezedetect,
  with a still-frozen-at-EOF freeze closed at the file end),
  alongside the existing black/silence warnings.
- Chapters: a project `chapters` list ({title, start}) is
  written into the output file's metadata (ffmetadata, via
  -map_chapters) and verified with ffprobe; each chapter runs
  to the next chapter's start or the timeline end. Produce
  briefs accept `chapters` directly, and section briefs get
  chapters from their section titles when no explicit list is
  given. Produce results carry the resolved chapters.
- Delivery presets: `youtube_1080` (1920x1080, 30fps, 8M,
  -14 LUFS), `shorts_720` (720x1280, 30fps, 5M, -14 LUFS) and
  `instagram_square` (1080x1080, 30fps, 5M, -16 LUFS) for
  produce_video (`delivery_preset`) and produce_variants
  (`delivery_presets` list, or a single `delivery_preset`).
  Preset sizes are delivery settings, not platform guarantees
  (platforms may re-encode on upload). Precedence, enforced
  and reported in the result: explicit width/height >
  explicit format > delivery preset > default vertical;
  explicit fps > preset fps > 24. A preset never silently
  overrides an explicit size: the result's
  delivery_preset_applied block records the size source.
  Preset bitrate replaces crf 23 with a target bitrate;
  preset loudness is the target used by loudnorm tracks.
- Review aids: preview proxies carry title-safe (10% margin,
  red) and action-safe (5% margin, yellow) drawbox guides by
  default, flagged in the preview result (guides, timecode,
  safe_areas); previews may also burn in a running timecode
  (drawtext %{pts:hms}) on request. Final renders have
  neither unless the project explicitly sets timecode: true —
  that explicit choice does reach the final render.
- Examples: examples/grading.json (grade + LUT project),
  examples/ducking.json (ducking project),
  examples/brief-grading.json, examples/brief-ducking.json,
  and examples/test-lut.cube (a gentle warm LUT). All use
  synthetic test media only.

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
