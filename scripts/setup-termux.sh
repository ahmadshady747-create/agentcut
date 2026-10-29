#!/bin/sh
# AgentCut on-device setup (Termux on Android, or any Linux with
# python3 + ffmpeg already installed). Idempotent: safe to run
# twice; it never duplicates the shell export line and never
# re-downloads fonts that are already present.
#
# What it does:
#   1. Termux only: pkg update + pkg install python ffmpeg.
#   2. Checks python3 and ffmpeg/ffprobe versions.
#   3. Checks the FFmpeg filters AgentCut needs (drawtext, xfade,
#      loudnorm required; vidstab optional — only used when a
#      clip asks for stabilisation).
#   4. Fonts: copies the repo's bundled fonts/ into
#      ~/.agentcut-fonts when present; otherwise tries a download
#      and, failing that, prints the manual fallback (copy the
#      fonts/ directory out of any render pack).
#   5. Adds AGENTCUT_FONT_DIR to ~/.bashrc once, if missing.
#   6. Smoke test: renders a 1-second clip with one Arabic text
#      line, proving python + ffmpeg + drawtext + Arabic font.
#
# Render packs (./run.sh package) do NOT need this script: a pack
# carries its own code and fonts and needs only `pkg install
# python ffmpeg` on the phone. This script is for running the
# full repo on the device.
set -u
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FONT_HOME="$HOME/.agentcut-fonts"
BASHRC="$HOME/.bashrc"

say() { echo "setup: $*"; }
warn() { echo "setup: WARNING: $*" >&2; }

# 1. Termux packages (skipped when `pkg` does not exist).
if command -v pkg >/dev/null 2>&1; then
    say "Termux detected: updating package lists and installing python + ffmpeg (one-time, may take a while)..."
    pkg update -y || warn "pkg update failed; continuing with installed packages"
    pkg install -y python ffmpeg || { echo "setup: ERROR: pkg install python ffmpeg failed" >&2; exit 1; }
else
    say "No Termux pkg command; assuming a Linux host with python3 + ffmpeg already installed."
fi

# 2. Version checks.
command -v python3 >/dev/null 2>&1 || { echo "setup: ERROR: python3 not found" >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { echo "setup: ERROR: ffmpeg not found" >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "setup: ERROR: ffprobe not found (ships with ffmpeg)" >&2; exit 1; }
say "python: $(python3 --version 2>&1)"
say "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"

# 3. Filter checks.
FILTERS=$(ffmpeg -hide_banner -filters 2>/dev/null)
for f in drawtext xfade loudnorm; do
    echo "$FILTERS" | grep -q " $f " || { echo "setup: ERROR: ffmpeg build lacks required filter: $f" >&2; exit 1; }
done
say "required filters present: drawtext, xfade, loudnorm"
if echo "$FILTERS" | grep -q " vidstab"; then
    say "optional filter present: vidstab (stabilisation available)"
else
    warn "ffmpeg build lacks vidstab: clips asking for stabilisation will fail; everything else works"
fi

# 4. Fonts.
mkdir -p "$FONT_HOME"
if [ -f "$REPO_DIR/fonts/NotoNaskhArabic-Regular.ttf" ]; then
    cp "$REPO_DIR"/fonts/*.ttf "$FONT_HOME"/ 2>/dev/null || true
    say "fonts copied from repo fonts/ to $FONT_HOME"
elif [ -f "$FONT_HOME/NotoNaskhArabic-Regular.ttf" ]; then
    say "fonts already present in $FONT_HOME"
else
    say "repo fonts/ not found; trying a download (needs network)..."
    DL_OK=0
    BASE_URL="https://github.com/notofonts/Noto-Fonts/raw/main/hinted/ttf/NotoNaskhArabic/NotoNaskhArabic-Regular.ttf"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$FONT_HOME/NotoNaskhArabic-Regular.ttf" "$BASE_URL" && DL_OK=1 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$FONT_HOME/NotoNaskhArabic-Regular.ttf" "$BASE_URL" && DL_OK=1 || true
    fi
    if [ "$DL_OK" -eq 1 ]; then
        say "downloaded NotoNaskhArabic-Regular.ttf to $FONT_HOME"
    else
        warn "could not obtain fonts automatically."
        warn "Manual fallback: copy the fonts/ directory out of any AgentCut render pack into $FONT_HOME"
        warn "(render packs always bundle their fonts, so a pack render never needs this step)."
    fi
fi

# 5. Persistent font dir for future shells (once only).
EXPORT_LINE="export AGENTCUT_FONT_DIR=\"$FONT_HOME\""
if [ -f "$BASHRC" ] && grep -qxF "$EXPORT_LINE" "$BASHRC"; then
    say "AGENTCUT_FONT_DIR already exported in $BASHRC"
else
    echo "$EXPORT_LINE" >> "$BASHRC"
    say "added AGENTCUT_FONT_DIR to $BASHRC (reopen Termux or: source ~/.bashrc)"
fi
export AGENTCUT_FONT_DIR="$FONT_HOME"

# 6. Smoke test: 1-second image clip with one Arabic text line.
SMOKE_DIR=$(mktemp -d 2>/dev/null || echo "$HOME/.agentcut-smoke")
mkdir -p "$SMOKE_DIR"
cat > "$SMOKE_DIR/smoke.json" <<'EOF'
{"id": "smoke", "version": 1,
 "output": {"width": 360, "height": 640, "fps": 30},
 "clips": [{"id": "c1", "kind": "image", "src": "data/test_media/pattern.jpg", "duration": 1}],
 "texts": [{"text": "مرحبا 2 دولار", "size": 48, "y": 100, "start": 0, "end": 1}]}
EOF
cd "$REPO_DIR" || exit 1
if PYTHONPATH=src python3 -m agentcut render "$SMOKE_DIR/smoke.json" "$SMOKE_DIR/smoke.mp4" >/dev/null 2>&1; then
    say "smoke test OK: $SMOKE_DIR/smoke.mp4 (python + ffmpeg + drawtext + Arabic font all work)"
else
    echo "setup: ERROR: smoke render failed; run it verbosely to see why:" >&2
    echo "setup:   cd $REPO_DIR && PYTHONPATH=src python3 -m agentcut render $SMOKE_DIR/smoke.json $SMOKE_DIR/smoke.mp4" >&2
    exit 1
fi
say "setup complete. Full repo runs from: $REPO_DIR"
