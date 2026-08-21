#!/usr/bin/env bash
#
# Build Murmur.app.
#
#   ./scripts/build_app.sh alias        # dev build: bundle points at this checkout
#   ./scripts/build_app.sh standalone   # distributable: self-contained bundle
#
# Set CODESIGN_IDENTITY to sign with a real certificate. Without it the bundle
# is ad-hoc signed, which works but produces a new identity on every build --
# macOS then treats each build as a different app and drops the Accessibility
# grant. See docs/ARCHITECTURE.md for how to create a stable self-signed cert.
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-alias}"
APP="dist/Murmur.app"
IDENTITY="${CODESIGN_IDENTITY:--}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[ -x .venv/bin/python ] || die "No .venv found. Run ./scripts/bootstrap.sh first."

./scripts/make_icns.sh

info "Cleaning previous build"
rm -rf build dist

case "$MODE" in
  alias)      info "Building alias bundle"; ./.venv/bin/python setup.py py2app -A >/dev/null ;;
  standalone) info "Building standalone bundle"; ./.venv/bin/python setup.py py2app >/dev/null ;;
  *)          die "Unknown mode '$MODE'. Use 'alias' or 'standalone'." ;;
esac

[ -d "$APP" ] || die "py2app did not produce $APP"

# Signing must come last: any change to the bundle invalidates the signature.
info "Signing with identity: $IDENTITY"
codesign --force --deep --options runtime \
  --entitlements scripts/entitlements.plist \
  --sign "$IDENTITY" "$APP"
codesign --verify --verbose=2 "$APP" 2>&1 | sed 's/^/    /'

info "Built $APP"
if [ "$IDENTITY" = "-" ]; then
  cat <<'WARN'

    Ad-hoc signed. macOS will ask for Accessibility access again after every
    rebuild. To avoid that, create a self-signed code signing certificate
    (Keychain Access > Certificate Assistant > Create a Certificate, type
    "Code Signing") and export CODESIGN_IDENTITY with its name.
WARN
fi
