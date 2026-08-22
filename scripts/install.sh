#!/usr/bin/env bash
#
# One command, start to finish.
#
#   ./scripts/install.sh
#
# Does everything bootstrap + build + install would, and makes the decisions
# you would otherwise have to make yourself: which Python, which engine, which
# model. Safe to re-run — every step checks before it acts.
#
# What it cannot do is grant the two macOS permissions. Nothing can; macOS
# requires a human for those. It opens the right pane and tells you exactly
# what to click.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
ARCH="$(uname -m)"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }
rule()  { printf '\033[2m%s\033[0m\n' "────────────────────────────────────────────────────────"; }

[ "$(uname -s)" = "Darwin" ] || die "Aloud is macOS only."
[ -f scripts/bootstrap.sh ] || die "Run this from inside the Aloud folder."

rule
bold "Installing Aloud"
info "Architecture: $ARCH"
rule

# --- 1. Command Line Tools --------------------------------------------------
if ! xcode-select -p >/dev/null 2>&1; then
  warn "Xcode Command Line Tools are missing. Opening the installer."
  xcode-select --install || true
  die "Click Install, wait for it to finish, then run this script again."
fi

# --- 2. Homebrew ------------------------------------------------------------
# Not installed automatically: Homebrew's installer needs your password, and a
# script that surprises you with a sudo prompt is a script you should not trust.
if ! command -v brew >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$candidate" ] && eval "$("$candidate" shellenv)" && break
  done
fi
if ! command -v brew >/dev/null 2>&1; then
  if [ "$ARCH" = "arm64" ]; then
    die "Homebrew is required on Apple Silicon (for ffmpeg, and often Python).
    Install it, then run this script again:
      /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  fi
  warn "Homebrew not found. Continuing — it is only needed for optional extras on Intel."
fi

# --- 3. Pick a Python that the engine will actually accept ------------------
# Apple Silicon runs Parakeet, which needs 3.10+. macOS ships 3.9, so this is
# the single most common reason a first install fails.
if [ "$ARCH" = "arm64" ]; then NEED_MINOR=10; else NEED_MINOR=9; fi

usable() {
  [ -x "$(command -v "$1" 2>/dev/null)" ] || return 1
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $NEED_MINOR) else 1)" 2>/dev/null
}

PYTHON_BIN=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
  if usable "$candidate"; then PYTHON_BIN="$(command -v "$candidate")"; break; fi
done

if [ -z "$PYTHON_BIN" ] && command -v brew >/dev/null 2>&1; then
  for candidate in "$(brew --prefix)/bin/python3.13" "$(brew --prefix)/bin/python3.12"; do
    if [ -x "$candidate" ] && usable "$candidate"; then PYTHON_BIN="$candidate"; break; fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  command -v brew >/dev/null 2>&1 || die "Need Python 3.$NEED_MINOR+ and Homebrew is unavailable to install it."
  info "Installing Python 3.12 (Parakeet needs 3.10+, macOS ships 3.9)"
  brew install python@3.12
  PYTHON_BIN="$(brew --prefix)/bin/python3.12"
  usable "$PYTHON_BIN" || die "Installed Python 3.12 but cannot run it at $PYTHON_BIN"
fi
info "Using $("$PYTHON_BIN" --version) at $PYTHON_BIN"

# --- 4. Everything else -----------------------------------------------------
rule
info "Running bootstrap — dependencies, engine, and model"
info "The model download is the slow part (~2.4 GB on Apple Silicon). Let it run."
rule
PYTHON_BIN="$PYTHON_BIN" ./scripts/bootstrap.sh

rule
info "Building and installing Aloud.app"
rule
make install

# --- 5. The part that needs you --------------------------------------------
rule
bold "Almost there — two permissions, and macOS needs you for both"
rule
cat <<'NEXT'

  1. MICROPHONE
     Open Aloud, press "Start Dictation" once, and click Allow.

  2. ACCESSIBILITY  ← the one people get stuck on
     The Settings pane is opening now.

       a. Click the + button
       b. Go to Applications, select Aloud, click Open
       c. Make sure the switch beside it is ON
       d. QUIT ALOUD COMPLETELY (Cmd-Q) AND OPEN IT AGAIN

     Step (d) is not optional — the permission is only read at launch.

  Then: click into any text field, hold Right Option, speak, release.

NEXT
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" 2>/dev/null || true
open -a Aloud 2>/dev/null || open /Applications/Aloud.app 2>/dev/null || true

rule
info "If anything went wrong:  make diagnose"
info "To check the setup:      make doctor"
rule
