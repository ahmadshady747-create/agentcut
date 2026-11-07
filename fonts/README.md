# Bundled fonts (on-device portability)

AgentCut used to look for fonts only under absolute Linux paths
(`/usr/share/fonts/...`), which do not exist on Android/Termux.
This directory ships the two fonts the renderer needs, so a render
works wherever the repo (or a render pack) is unpacked, with no
system fonts installed:

- `NotoNaskhArabic-Regular.ttf` — Arabic text. Noto Naskh Arabic is
  the preferred Arabic font: DejaVu has no Arabic glyphs, so Arabic
  text rendered with DejaVu shows as missing-glyph boxes.
  Licence: SIL Open Font License 1.1, see `OFL.txt`.
- `NotoSansArabic-Bold.ttf` — Arabic DISPLAY face (v1.41.0). A bold,
  modern, geometric Arabic sans face for titles and display text,
  where the Noto Naskh book face reads as body copy. Select it
  with the `font: "noto-sans-bold"` option on a text/caption;
  the script default (Noto Naskh) is unchanged when no font
  is given. Licence: SIL Open Font License 1.1, see
  `NotoSansArabic-OFL.txt` (The Noto Project Authors).
  Note: Tajawal Bold was tried first and REJECTED by frame
  inspection — it lacks isolated presentation forms (e.g.
  U+FE8D) that FFmpeg drawtext's joining path needs, so
  common words rendered with tofu boxes. Noto Sans Arabic
  Bold (751 presentation forms) renders joined Arabic
  completely under the same path.
- `DejaVuSans.ttf` — Latin text and the review timecode.
  Licence: Bitstream Vera licence, see `DejaVu-LICENSE.txt`.

## Resolution order (see `src/agentcut/validate.py`)

1. `$AGENTCUT_FONT_DIR` (a directory you point at your own fonts),
2. this bundled `fonts/` directory next to the repo,
3. Termux `$PREFIX/share/fonts/...` when `$PREFIX` is set,
4. system `/usr/share/fonts/...` paths.

If a project contains Arabic text and no Arabic font is found in
any of those places, validation fails with `FONT_NOT_FOUND` and
names the fix — the renderer never silently renders tofu boxes.
Latin-only projects need a Latin font the same way.
