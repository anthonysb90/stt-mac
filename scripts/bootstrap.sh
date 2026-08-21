#!/usr/bin/env bash
#
# One-time setup for a fresh Mac. Safe to re-run.
#
#   ./scripts/bootstrap.sh
#
# Installs: Xcode Command Line Tools (prompted), a virtualenv with the Python
# dependencies, the whisper.cpp CLI, and a Whisper model sized for this
# machine's CPU.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
SUPPORT_DIR="$HOME/Library/Application Support/Aloud"
MODELS_DIR="$SUPPORT_DIR/models"
VENDOR_DIR="$SUPPORT_DIR/vendor"
ARCH="$(uname -m)"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "Aloud is macOS only."
info "Architecture: $ARCH"

# --- 1. Xcode Command Line Tools -------------------------------------------
# Not full Xcode -- this is the free ~2 GB toolchain that provides clang, git,
# codesign, and iconutil. Every path to a macOS app needs it.
if ! xcode-select -p >/dev/null 2>&1; then
  warn "Xcode Command Line Tools are missing. Launching the installer."
  xcode-select --install || true
  die "Re-run this script once the Command Line Tools finish installing."
fi
info "Command Line Tools: $(xcode-select -p)"

# --- 2. Python --------------------------------------------------------------
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python3 not found. Install Python 3.9+."
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
info "Python $PY_VERSION ($(command -v "$PYTHON_BIN"))"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "Python 3.9 or newer is required."

if [ ! -d .venv ]; then
  info "Creating .venv"
  "$PYTHON_BIN" -m venv .venv
fi
info "Installing Python dependencies"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements-dev.txt

# --- 3. whisper.cpp ---------------------------------------------------------
mkdir -p "$MODELS_DIR" "$VENDOR_DIR"

find_whisper() {
  for candidate in whisper-cli whisper-cpp; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  for candidate in "$VENDOR_DIR/whisper.cpp/build/bin/whisper-cli" \
                   /opt/homebrew/bin/whisper-cli /usr/local/bin/whisper-cli; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

build_whisper_from_source() {
  command -v cmake >/dev/null 2>&1 \
    || die "cmake is required to build whisper.cpp. Install Homebrew (https://brew.sh) then: brew install cmake"
  local src="$VENDOR_DIR/whisper.cpp"
  if [ -d "$src/.git" ]; then
    info "Updating whisper.cpp source"
    git -C "$src" pull --ff-only
  else
    info "Cloning whisper.cpp"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$src"
  fi
  info "Building whisper.cpp for $ARCH (this takes a few minutes)"
  cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$src/build" --config Release -j "$(sysctl -n hw.ncpu)"
}

if WHISPER_BIN="$(find_whisper)"; then
  info "whisper.cpp CLI: $WHISPER_BIN"
elif command -v brew >/dev/null 2>&1; then
  info "Installing whisper.cpp via Homebrew"
  brew install whisper-cpp || build_whisper_from_source
  WHISPER_BIN="$(find_whisper || true)"
else
  build_whisper_from_source
  WHISPER_BIN="$(find_whisper || true)"
fi
[ -n "${WHISPER_BIN:-}" ] || warn "Could not locate a whisper.cpp CLI; set engines.whisper_cpp.binary in the config."

# --- 4. Model ---------------------------------------------------------------
# Apple Silicon runs the model on the GPU and Neural Engine, so it can afford a
# larger one. Intel is CPU-only, where small.en is noticeably slower than
# real time for back-to-back dictation.
if [ "$ARCH" = "arm64" ]; then
  DEFAULT_MODEL="small.en"
else
  DEFAULT_MODEL="base.en"
fi
MODEL="${ALOUD_MODEL:-$DEFAULT_MODEL}"
./scripts/fetch_model.sh "$MODEL"

# --- 5. Report --------------------------------------------------------------
info "Running diagnostics"
PYTHONPATH="$REPO_ROOT/src" ./.venv/bin/python -m aloud doctor || true

cat <<'NEXT'

Next steps
  make dev-app     build Aloud.app (alias build) and open it
  make run         run in the terminal instead (permissions attach to the terminal)

The first launch will ask for Microphone access, and you must add Aloud under
System Settings > Privacy & Security > Accessibility by hand. Quit and reopen
the app after granting it.
NEXT
