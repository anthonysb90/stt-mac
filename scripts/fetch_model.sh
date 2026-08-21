#!/usr/bin/env bash
#
# Download a whisper.cpp ggml model.
#
#   ./scripts/fetch_model.sh [tiny.en|base.en|small.en|medium.en|large-v3-turbo]
#
# Models land in ~/Library/Application Support/Aloud/models and are picked up
# automatically (newest first) unless engines.whisper_cpp.model is set.
set -euo pipefail

MODEL="${1:-base.en}"
MODELS_DIR="$HOME/Library/Application Support/Aloud/models"
BASE_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
TARGET="$MODELS_DIR/ggml-${MODEL}.bin"

mkdir -p "$MODELS_DIR"

if [ -s "$TARGET" ]; then
  printf '\033[1;34m==>\033[0m Model already present: %s\n' "$TARGET"
  exit 0
fi

printf '\033[1;34m==>\033[0m Downloading ggml-%s.bin\n' "$MODEL"
if ! curl -fL --progress-bar -o "$TARGET.part" "$BASE_URL/ggml-${MODEL}.bin"; then
  rm -f "$TARGET.part"
  printf '\033[1;31m==>\033[0m Download failed. Check the model name.\n' >&2
  exit 1
fi
mv "$TARGET.part" "$TARGET"
printf '\033[1;34m==>\033[0m Saved %s (%s)\n' "$TARGET" "$(du -h "$TARGET" | cut -f1)"
