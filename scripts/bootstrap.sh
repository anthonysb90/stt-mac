#!/usr/bin/env bash
#
# One-time setup for a fresh Mac. Safe to re-run.
#
#   ./scripts/bootstrap.sh                     # install the engine for this machine
#   ./scripts/bootstrap.sh --with-whisper-cpp  # also install the offline fallback
#
# The engine is chosen by architecture:
#   Apple Silicon -> Parakeet on MLX (needs Python 3.10+ and ffmpeg)
#   Intel         -> faster-whisper on CPU
# Both keep the model resident in the app, so there is no per-dictation load.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
ARCH="$(uname -m)"
WITH_WHISPER_CPP=0
[ "${1:-}" = "--with-whisper-cpp" ] && WITH_WHISPER_CPP=1

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "Aloud is macOS only."

if [ "$ARCH" = "arm64" ]; then
  ENGINE="Parakeet (MLX)"
  MIN_PY_MAJOR=3; MIN_PY_MINOR=10   # parakeet-mlx requires 3.10+
else
  ENGINE="faster-whisper (CPU)"
  MIN_PY_MAJOR=3; MIN_PY_MINOR=9
fi
info "Architecture: $ARCH — engine: $ENGINE"

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
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python3 not found."
PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)"; then
  die "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ is required for $ENGINE (found $PY_VERSION).
    Install a newer one and re-run, e.g.:
      brew install python@3.12
      PYTHON_BIN=\$(brew --prefix)/bin/python3.12 ./scripts/bootstrap.sh"
fi
info "Python $PY_VERSION ($(command -v "$PYTHON_BIN"))"

if [ ! -d .venv ]; then
  info "Creating .venv"
  "$PYTHON_BIN" -m venv .venv
fi

# --- 3. ffmpeg (Apple Silicon: parakeet-mlx decodes audio with it) ----------
if [ "$ARCH" = "arm64" ] && ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    info "Installing ffmpeg (required by parakeet-mlx)"
    brew install ffmpeg
  else
    warn "ffmpeg not found and Homebrew is unavailable. Install ffmpeg (https://brew.sh) or Parakeet will fail to decode audio."
  fi
fi

# --- 4. Python dependencies -------------------------------------------------
# Environment markers in requirements.txt install parakeet-mlx on arm64 and
# faster-whisper on x86_64, so this line is correct on both machines.
info "Installing Python dependencies (this pulls in the ML runtime; give it a minute)"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install -r requirements-dev.txt

# --- 5. whisper.cpp, the offline fallback (opt-in) -------------------------
if [ "$WITH_WHISPER_CPP" = "1" ]; then
  info "Installing the whisper.cpp fallback"
  if command -v whisper-cli >/dev/null 2>&1; then
    info "whisper.cpp CLI: $(command -v whisper-cli)"
  elif command -v brew >/dev/null 2>&1; then
    brew install whisper-cpp || warn "Homebrew could not install whisper-cpp; skipping."
  else
    warn "Homebrew is unavailable; skipping whisper.cpp."
  fi
  if [ "$ARCH" = "arm64" ]; then FALLBACK_MODEL="small.en"; else FALLBACK_MODEL="base.en"; fi
  ./scripts/fetch_model.sh "${ALOUD_MODEL:-$FALLBACK_MODEL}" || warn "Model download failed; skipping."
fi

# --- 6. Pre-download the engine's model ------------------------------------
# Doing this now means the first dictation is not a multi-gigabyte surprise.
info "Downloading and loading the model"
PYTHONPATH="$REPO_ROOT/src" ./.venv/bin/python -m aloud warm || warn "Warm-up failed; 'make doctor' will say why."

# --- 7. Report --------------------------------------------------------------
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
