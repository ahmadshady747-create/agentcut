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
