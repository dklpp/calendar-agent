#!/usr/bin/env bash
# Builds CalendarAgent.app. No Xcode project needed.
#
#   ./build-app.sh           debug build
#   ./build-app.sh release   optimised, then drag the .app to /Applications
set -euo pipefail

CONFIG="${1:-debug}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/build/CalendarAgent.app"

swift build -c "$CONFIG" --package-path "$HERE"
BIN="$(swift build -c "$CONFIG" --package-path "$HERE" --show-bin-path)/CalendarAgent"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/CalendarAgent"
cp "$HERE/Resources/Info.plist" "$APP/Contents/Info.plist"

# Ad-hoc signature. Enough for personal use; the microphone prompt needs a
# signature of some kind, but not a paid Developer ID.
codesign --force --deep --sign - "$APP"

echo "Built $APP"
echo "Run it with: open '$APP'"
