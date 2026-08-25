#!/usr/bin/env bash
#
# Build Aloud.app and install it, safely and verifiably.
#
#   ./scripts/install_app.sh
#
# Hand-assembled, not py2app. py2app's alias build symlinks
# Contents/Frameworks/Python.framework and Contents/MacOS/python back out to
# Homebrew and to this checkout. codesign refuses any symlink whose
# destination leaves the bundle ("invalid destination for symbolic link in
# bundle"), so that bundle can never verify -- and confirmed directly on
# hardware, macOS will not hold an Accessibility grant against a signature
# that does not verify. Neither copying nor deleting those two symlinks works
# either: CPython finds its standard library by resolving its own
# executable's symlink at startup (a copy breaks that), and py2app's own
# stub reads Contents/MacOS/python's path at launch and crashes if it is gone.
#
# Contents/MacOS/Aloud is a small C program (scripts/launcher.c) linked
# directly against this machine's Python and calling Py_BytesMain() -- the
# same C-API function the standard `python` executable's own main() calls --
# in-process. An earlier version of this launcher exec'd a copied
# interpreter instead; that broke Accessibility for a subtler reason, found
# only by testing on hardware: NSBundle.mainBundle() requires the *running*
# executable's own path to sit exactly at Contents/MacOS/<CFBundleExecutable>,
# and exec() replaces that path, so TCC ended up evaluating the wrong
# identity entirely (see scripts/launcher.c for the full story). Calling
# Py_BytesMain() in-process instead means this same binary keeps running for
# the app's whole lifetime, so its bundle identity never changes. It also
# means the .venv no longer needs to be copied into the bundle at all --
# nothing here is a symlink pointing outside the bundle, so codesign has
# nothing to object to either way.
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
command -v clang >/dev/null 2>&1 \
  || die "No clang — install the Xcode Command Line Tools: xcode-select --install"

# Nothing below touches /Applications until a new bundle has been built,
# signed and verified. Build first, prove it, then swap.

# --- 1. Assemble the bundle skeleton -----------------------------------
info "Assembling the bundle"
rm -rf build dist
mkdir -p "$BUILT/Contents/MacOS" "$BUILT/Contents/Resources"

ICONERR="$(mktemp -t aloud-icon)"
if ./scripts/make_icns.sh >"$ICONERR" 2>&1; then
  rm -f "$ICONERR"
else
  warn "Icon generation failed; the app will show a generic icon:"
  sed 's/^/    /' "$ICONERR" >&2
  rm -f "$ICONERR"
fi
if [ -f assets/Aloud.icns ]; then
  cp assets/Aloud.icns "$BUILT/Contents/Resources/Aloud.icns"
fi

./.venv/bin/python scripts/write_plist.py "$BUILT/Contents/Info.plist" \
  || die "Could not write Info.plist"

# --- 2. Work out how to link against this machine's Python -----------------
# Queried through .venv/bin/python3 itself, not assumed, so this adapts to
# whatever Homebrew python@3.12 install actually exists on this Mac. Writes
# build/aloud_embed_config.h (ALOUD_PYTHONHOME / ALOUD_PYTHONPATH, which the
# launcher setenv()s before calling Py_BytesMain) and prints CFLAGS/LDFLAGS
# for the compile step below. See scripts/python_embed_flags.py.
info "Finding this machine's Python"
mkdir -p build
HEADER="build/aloud_embed_config.h"
EMBEDERR="$(mktemp -t aloud-embed)"
if ! EMBED_VARS=$(./.venv/bin/python3 scripts/python_embed_flags.py "$HEADER" 2>"$EMBEDERR"); then
  printf '\033[1;31m==>\033[0m Could not work out how to link against Python:\n\n' >&2
  sed 's/^/    /' "$EMBEDERR" >&2
  rm -f "$EMBEDERR"
  exit 1
fi
rm -f "$EMBEDERR"
eval "$EMBED_VARS"
field "PYTHONHOME" "$PY_HOME"
field "PYTHONPATH" "$PY_PYTHONPATH"
field "link flags" "$LDFLAGS"

# --- 3. Compile the launcher, linked directly against Python --------------
# Calls Py_BytesMain() in-process -- the same function `python`'s own main()
# calls -- instead of exec-ing a separate interpreter. See scripts/launcher.c
# for why that distinction is what actually makes Accessibility work.
info "Compiling the launcher"
CLANGERR="$(mktemp -t aloud-clang)"
# shellcheck disable=SC2086  # CFLAGS/LDFLAGS are meant to word-split.
if ! clang -O2 -Wall -I build $CFLAGS -o "$BUILT/Contents/MacOS/Aloud" scripts/launcher.c $LDFLAGS 2>"$CLANGERR"; then
  printf '\033[1;31m==>\033[0m The launcher would not compile:\n\n' >&2
  sed 's/^/    /' "$CLANGERR" >&2
  rm -f "$CLANGERR"
  exit 1
fi
rm -f "$CLANGERR"

# --- 4. Sign ---------------------------------------------------------------
# Prefer a stable identity over ad-hoc. Ad-hoc signing derives the app's code
# identity from a hash of its contents, so every rebuild is a different app to
# macOS and the Accessibility grant made against the last build silently stops
# applying -- while still showing as enabled in System Settings, which is what
# makes it so hard to diagnose. scripts/make_signing_cert.sh creates one.
#
# Only Contents/MacOS/Aloud itself needs signing now -- it calls
# Py_BytesMain() in-process rather than exec-ing a separate interpreter
# binary, so it is the only Mach-O executable in the bundle, and it is the
# one running the whole time Aloud calls CGEventTapCreate.
#
# No --options runtime (no hardened runtime): MLX, faster-whisper
# and PyObjC's native extensions were never built expecting library
# validation or JIT-memory restrictions, and hardened runtime is what made
# the old py2app stub need allow-jit/allow-unsigned-executable-memory
# entitlements in the first place. Skipping it here avoids that class of
# problem entirely, same as it already works when `make run` executes this
# exact interpreter directly, unsigned-in-that-sense, from the checkout.
IDENTITY="${CODESIGN_IDENTITY:-}"
IDENTITY_LABEL="$IDENTITY"
if [ -z "$IDENTITY" ]; then
  for candidate in "Aloud Dev"; do
    # By hash, not by name. Two certificates sharing a name make codesign
    # refuse with "ambiguous (matches ... and ...)", and re-running a script
    # that creates one is all it takes to end up with two. A hash cannot be
    # ambiguous. ./scripts/make_signing_cert.sh clears duplicates.
    hashes="$(security find-identity -v -p codesigning 2>/dev/null \
      | grep -F "\"$candidate\"" | awk '{print $2}')"
    count="$(printf '%s' "$hashes" | grep -c . || true)"
    if [ "${count:-0}" -gt 1 ]; then
      warn "$count certificates are called '$candidate'; using the first."
      warn "Run ./scripts/make_signing_cert.sh to clear the duplicates."
    fi
    if [ -n "$hashes" ]; then
      IDENTITY="$(printf '%s\n' "$hashes" | head -1)"
      IDENTITY_LABEL="$candidate"
      info "Found a stable signing identity: $candidate"
      break
    fi
  done
fi
: "${IDENTITY:=-}"
: "${IDENTITY_LABEL:=$IDENTITY}"
info "Signing as: $IDENTITY_LABEL"

sign_one() {
  local what="$1" path="$2" err
  err="$(mktemp -t aloud-codesign)"
  if codesign --force --sign "$IDENTITY" "$path" 2>"$err"; then
    rm -f "$err"
    return 0
  fi
  printf '\n\033[1;31m==>\033[0m Signing %s failed. codesign said:\n\n' "$what" >&2
  sed 's/^/    /' "$err" >&2
  rm -f "$err"
  return 1
}

# Signs Contents/MacOS/Aloud (the main executable) and seals the resource
# envelope -- Info.plist and the icon -- in one standard operation. There is
# no separate nested binary to sign first: Python is linked in, not copied
# in as its own Mach-O file.
if ! sign_one "the bundle" "$BUILT"; then
  if [ "$IDENTITY" != "-" ]; then
    printf '\n    The identity "%s" is listed but codesign will not use it.\n' "$IDENTITY_LABEL" >&2
    printf '    Usually the certificate is not trusted for code signing yet:\n\n' >&2
    printf '        ./scripts/make_signing_cert.sh --repair\n\n' >&2
    printf '    Signing ad-hoc instead would leave you exactly where you started,\n' >&2
    printf '    so this stops here rather than installing something broken.\n' >&2
    exit 1
  fi
  warn "Ad-hoc signing failed; continuing, but permissions will not stick."
fi

# --- 5. Verify ---------------------------------------------------------
# Enforced, not just reported: unlike the old alias build, this one is
# expected to actually pass, because nothing in it is a symlink pointing
# outside the bundle. A failure here means something is genuinely wrong,
# not an inherent limitation -- worth stopping for.
info "Verifying the signature"
if VERIFY_OUT=$(codesign --verify --strict --verbose=2 "$BUILT" 2>&1); then
  field "signature" "verifies"
else
  printf '\n\033[1;31m==>\033[0m The signature does not verify:\n\n' >&2
  printf '%s\n' "$VERIFY_OUT" | sed 's/^/    /' >&2
  printf '\n    This build is not an alias build, so this is unexpected --\n' >&2
  printf '    macOS will not hold an Accessibility grant against it either way,\n' >&2
  printf '    so installing it would waste your time.\n' >&2
  exit 1
fi

checkpoint() {
  # Verifies $2 and reports which step just ran, so a single install
  # pinpoints exactly where a signature stops verifying instead of leaving
  # that to another round of guessing.
  local label="$1" target="$2"
  if OUT=$(codesign --verify --strict --verbose=2 "$target" 2>&1); then
    field "  [$label]" "verifies"
  else
    field "  [$label]" "BROKEN -- first divergence below"
    printf '%s\n' "$OUT" | grep -m1 "modified\|invalid\|resource" | sed 's/^/        /'
  fi
}

# --- 6. Replace ------------------------------------------------------------
# Only now, with a verified bundle in hand.
if [ -e "$INSTALLED" ]; then
  if [ -e "$INSTALLED/Aloud.app" ]; then
    warn "Found a nested bundle at $INSTALLED/Aloud.app — that is the cp bug."
  fi
  info "Removing the existing $INSTALLED"
  rm -rf "$INSTALLED" || die "Could not remove $INSTALLED"
fi

info "Installing to $INSTALLED (this also takes a while — it's a full copy)"
# ditto preserves metadata and never nests on an existing target.
ditto "$BUILT" "$INSTALLED" || die "Could not copy the bundle into /Applications"
checkpoint "after ditto" "$INSTALLED"

# --- 7. Prove it works -----------------------------------------------------
info "Inspecting the installed bundle"
PLIST="$INSTALLED/Contents/Info.plist"
for key in CFBundleName CFBundleExecutable CFBundleIdentifier LSUIElement; do
  value=$(/usr/libexec/PlistBuddy -c "Print :$key" "$PLIST" 2>/dev/null || echo "(not set)")
  field "$key" "$value"
done
field "Contents/MacOS" "$(ls "$INSTALLED/Contents/MacOS" 2>/dev/null | tr '\n' ' ')"
field "nested bundle?" "$([ -e "$INSTALLED/Aloud.app" ] && echo 'YES — still wrong' || echo 'no')"
field "symlinks in bundle" "$(find "$INSTALLED" -type l 2>/dev/null | wc -l | tr -d ' ') (should be 0)"
ICON=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$PLIST" 2>/dev/null || echo '')
if [ -n "$ICON" ] && [ -e "$INSTALLED/Contents/Resources/${ICON%.icns}.icns" ]; then
  field "icon" "$ICON"
else
  field "icon" "MISSING — macOS will fall back to a generic icon"
fi

# macOS caches app icons hard, and this bundle has been replaced many times.
# Without this the Dock and Finder keep showing whatever they cached first.
touch "$INSTALLED"
killall Dock >/dev/null 2>&1 || true
checkpoint "after touch + Dock restart" "$INSTALLED"

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
  checkpoint "after first launch" "$INSTALLED"
  printf '\n\033[1;32m==>\033[0m Installed and working: %s\n' "$INSTALLED"
  printf '    Open it from Launchpad, then grant Accessibility and add it to Login Items.\n'
  if [ "$IDENTITY" = "-" ]; then
    printf '\n'
    warn "Signed ad-hoc, so this build has a new code identity."
    warn "Any Accessibility grant you made for an earlier build no longer applies,"
    warn "even though System Settings still shows Aloud switched on."
    printf '    Clear the stale entry and grant it once more:\n\n'
    printf '        make fix-permissions\n\n'
    printf '    To stop this recurring, make a stable identity once:\n\n'
    printf '        ./scripts/make_signing_cert.sh\n'
  fi
else
  printf '\n\033[1;31m==>\033[0m The bundle does not start. The real error:\n\n' >&2
  printf '%s\n' "$OUT" | sed 's/^/    /' >&2
  sed 's/^/    /' "$ERRLOG" >&2
  printf '\n    The app still works from this folder in the meantime:  make run\n' >&2
  exit 1
fi
