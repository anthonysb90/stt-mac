#!/usr/bin/env bash
#
# Clear Aloud's stale Accessibility grant so it can be granted again cleanly.
#
#   ./scripts/fix_permissions.sh
#
# For the case where Aloud is listed under Privacy & Security > Accessibility
# with its switch ON, and still says it has no access. That is not a lie on
# either side: the switch belongs to a *previous build*. An ad-hoc signature
# is a hash of the bundle, so every rebuild is a different app to macOS, and
# the row you are looking at grants access to a binary that no longer exists.
#
# Removing the row is the fix, because it lets the next grant attach to the
# build you actually have. `./scripts/make_signing_cert.sh` stops it recurring.
set -uo pipefail

BUNDLE_ID="${BUNDLE_ID:-com.aloud.Aloud}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

[ "$(uname -s)" = "Darwin" ] || { echo "macOS only." >&2; exit 1; }

info "Quitting Aloud if it is running"
osascript -e 'tell application "Aloud" to quit' >/dev/null 2>&1 || true
pkill -f "Aloud.app/Contents/MacOS/Aloud" >/dev/null 2>&1 || true
sleep 1

info "Clearing the stale Accessibility entry for $BUNDLE_ID"
if tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1; then
  info "Cleared."
else
  warn "tccutil could not reset it (this is normal on some macOS versions)."
  warn "Remove the Aloud row by hand instead: select it and click the − button."
fi

cat <<'NEXT'

Now grant it once more, to the build you actually have:

  1. System Settings > Privacy & Security > Accessibility
  2. If an Aloud row is still there, select it and click −
  3. Click + , choose Applications > Aloud, and switch it ON
  4. Quit Aloud (Cmd-Q) and open it again — the permission is only read at launch

To stop this happening on every rebuild:

  ./scripts/make_signing_cert.sh

NEXT

info "Reopening Aloud"
open -a Aloud >/dev/null 2>&1 || warn "Could not open Aloud; launch it yourself."
