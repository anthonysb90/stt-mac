#!/usr/bin/env bash
#
# Build assets/Murmur.icns from assets/Murmur-1024.png.
# Uses sips and iconutil, both of which ship with the Command Line Tools.
set -euo pipefail

cd "$(dirname "$0")/.."
SOURCE="assets/Murmur-1024.png"
ICONSET="assets/Murmur.iconset"
OUTPUT="assets/Murmur.icns"

if [ ! -f "$SOURCE" ]; then
  printf '\033[1;34m==>\033[0m Generating %s\n' "$SOURCE"
  python3 scripts/make_icon_png.py --out "$SOURCE" --size 1024
fi

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# The exact set of sizes iconutil expects.
for spec in "16 16x16" "32 16x16@2x" "32 32x32" "64 32x32@2x" \
            "128 128x128" "256 128x128@2x" "256 256x256" "512 256x256@2x" \
            "512 512x512" "1024 512x512@2x"; do
  size="${spec%% *}"
  name="${spec##* }"
  sips -z "$size" "$size" "$SOURCE" --out "$ICONSET/icon_${name}.png" >/dev/null
done

iconutil --convert icns "$ICONSET" --output "$OUTPUT"
rm -rf "$ICONSET"
printf '\033[1;34m==>\033[0m Wrote %s\n' "$OUTPUT"
