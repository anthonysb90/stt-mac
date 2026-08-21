#!/usr/bin/env bash
#
# Build Aloud.app.
#
#   ./scripts/build_app.sh alias        # recommended: bundle runs against .venv
#   ./scripts/build_app.sh standalone   # experimental: self-contained bundle
#
# Use alias, including for the copy in /Applications. A standalone build has to
# copy the whole engine stack into the bundle -- MLX with its Metal shader
# libraries, or CTranslate2 with its native extensions -- and py2app has no
# recipe for either. When it fails it fails at launch, with a "Launch error"
# dialog and no traceback.
#
# Set CODESIGN_IDENTITY to sign with a real certificate. Without it the bundle
# is ad-hoc signed, which works but produces a new identity on every build --
# macOS then treats each build as a different app and drops the Accessibility
# grant. See docs/ARCHITECTURE.md for how to create a stable self-signed cert.
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-alias}"
APP="dist/Aloud.app"
IDENTITY="${CODESIGN_IDENTITY:--}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[ -x .venv/bin/python ] || die "No .venv found. Run ./scripts/bootstrap.sh first."

./scripts/make_icns.sh

info "Cleaning previous build"
rm -rf build dist

case "$MODE" in
  alias)
    info "Building alias bundle (runs against $PWD/.venv)"
    ./.venv/bin/python setup.py py2app -A >/dev/null
    ;;
  standalone)
    warn "Standalone builds bundle the whole engine stack and often fail at launch."
    warn "If you get a 'Launch error' dialog, build with 'alias' instead."
    info "Building standalone bundle"
    ./.venv/bin/python setup.py py2app >/dev/null
    ;;
  *) die "Unknown mode '$MODE'. Use 'alias' or 'standalone'." ;;
esac

[ -d "$APP" ] || die "py2app did not produce $APP"

# Signing must come last: any change to the bundle invalidates the signature.
info "Signing with identity: $IDENTITY"
codesign --force --deep --options runtime \
  --entitlements scripts/entitlements.plist \
  --sign "$IDENTITY" "$APP"
codesign --verify --verbose=2 "$APP" 2>&1 | sed 's/^/    /'

info "Built $APP"

# Fail loudly here rather than in a dialog with no traceback.
info "Checking that the bundle can start"
if ! "$APP/Contents/MacOS/Aloud" --version >/dev/null 2>"$PWD/build/launch-check.log"; then
  printf '\033[1;31m==>\033[0m The bundle does not start. The real error:\n\n' >&2
  sed 's/^/    /' "$PWD/build/launch-check.log" >&2
  printf '\n    Reproduce it yourself with:\n      %s/Contents/MacOS/Aloud\n' "$APP" >&2
  exit 1
fi
info "Bundle starts cleanly"

if [ "$MODE" = "alias" ]; then
  cat <<WHERE

    This bundle runs against $PWD/.venv.
    Keep this folder where it is -- moving or deleting it breaks the app.
WHERE
fi

if [ "$IDENTITY" = "-" ]; then
  cat <<'WARN'

    Ad-hoc signed. macOS will ask for Accessibility access again after every
    rebuild. To avoid that, create a self-signed code signing certificate
    (Keychain Access > Certificate Assistant > Create a Certificate, type
    "Code Signing") and export CODESIGN_IDENTITY with its name.
WARN
fi
