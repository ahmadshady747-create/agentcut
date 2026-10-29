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
