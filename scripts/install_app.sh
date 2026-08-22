#!/usr/bin/env bash
#
# Build Aloud.app and install it, safely and verifiably.
#
#   ./scripts/install_app.sh
#
# Exists because `cp -R dist/Aloud.app /Applications/` is wrong when the
# destination already exists: cp copies *into* it, leaving the old bundle in
# place with the new one nested inside. This removes the old one first, then
# proves the result actually starts before claiming success.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
BUILT="dist/Aloud.app"
INSTALLED="/Applications/Aloud.app"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }
field() { printf '    %-22s %s\n' "$1" "$2"; }

[ -x .venv/bin/python ] || die "No .venv — run ./scripts/bootstrap.sh first."

# --- 1. Clear out whatever is there, including a nested mistake ------------
if [ -e "$INSTALLED" ]; then
  if [ -e "$INSTALLED/Aloud.app" ]; then
    warn "Found a nested bundle at $INSTALLED/Aloud.app — that is the cp bug."
  fi
  info "Removing the existing $INSTALLED"
  rm -rf "$INSTALLED" || die "Could not remove $INSTALLED"
fi

# --- 2. Build --------------------------------------------------------------
info "Building"
rm -rf build dist
./scripts/make_icns.sh >/dev/null 2>&1 || warn "Icon generation failed; continuing without one."
./.venv/bin/python setup.py py2app -A >/tmp/aloud-build.log 2>&1 || {
  printf '\033[1;31m==>\033[0m Build failed:\n' >&2
  tail -30 /tmp/aloud-build.log | sed 's/^/    /' >&2
  exit 1
}
[ -d "$BUILT" ] || die "py2app did not produce $BUILT"

# --- 3. Sign ---------------------------------------------------------------
IDENTITY="${CODESIGN_IDENTITY:--}"
info "Signing as: $IDENTITY"
codesign --force --deep --options runtime \
  --entitlements scripts/entitlements.plist \
  --sign "$IDENTITY" "$BUILT" >/dev/null 2>&1 \
  || warn "Signing failed; the app will still run but permissions may not stick."

# --- 4. Install ------------------------------------------------------------
info "Installing to $INSTALLED"
# ditto preserves symlinks and metadata, and never nests on an existing target.
ditto "$BUILT" "$INSTALLED" || die "Could not copy the bundle into /Applications"

# --- 5. Prove it works -----------------------------------------------------
info "Inspecting the installed bundle"
PLIST="$INSTALLED/Contents/Info.plist"
for key in CFBundleName CFBundleExecutable CFBundleIdentifier LSUIElement; do
  value=$(/usr/libexec/PlistBuddy -c "Print :$key" "$PLIST" 2>/dev/null || echo "(not set)")
  field "$key" "$value"
done
field "PyRuntimeLocations" "$(/usr/libexec/PlistBuddy -c 'Print :PyRuntimeLocations' "$PLIST" 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g')"
field "Contents/MacOS" "$(ls "$INSTALLED/Contents/MacOS" 2>/dev/null | tr '\n' ' ')"
field "nested bundle?" "$([ -e "$INSTALLED/Aloud.app" ] && echo 'YES — still wrong' || echo 'no')"

info "Starting the bundle to check it runs"
# stdout and stderr are kept apart on purpose. Libraries under faster-whisper
# chatter on stderr at shutdown -- tqdm leaves a multiprocessing semaphore
# behind, which Python reports as "leaked semaphore objects" -- and folding
# that into the version string made a perfectly good install read as broken.
# Only the exit status decides whether this worked.
ERRLOG=$(mktemp -t aloud-verify)
if OUT=$("$INSTALLED/Contents/MacOS/Aloud" --version 2>"$ERRLOG"); then
  field "version" "$(printf '%s' "$OUT" | tail -1)"
  if [ -s "$ERRLOG" ]; then
    field "notes on stderr" "$(wc -l <"$ERRLOG" | tr -d ' ') line(s), harmless — $ERRLOG"
  fi
  printf '\n\033[1;32m==>\033[0m Installed and working: %s\n' "$INSTALLED"
  printf '    Open it from Launchpad, then grant Accessibility and add it to Login Items.\n'
else
  printf '\n\033[1;31m==>\033[0m The bundle does not start. The real error:\n\n' >&2
  printf '%s\n' "$OUT" | sed 's/^/    /' >&2
  sed 's/^/    /' "$ERRLOG" >&2
  printf '\n    The app still works from this folder in the meantime:  make run\n' >&2
  exit 1
fi
