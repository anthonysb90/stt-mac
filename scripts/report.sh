#!/usr/bin/env bash
#
# Collect everything needed to diagnose a launch failure, in one paste.
#
#   ./scripts/report.sh
#
# Runs the app three ways -- from source, from the built bundle, and through
# the CLI -- because which of those work narrows the cause down immediately:
# source only means packaging, none of them means the code, all of them means
# it is already fixed.
set -uo pipefail   # deliberately not -e: a failing section is the finding

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
APP="/Applications/Aloud.app"
CAPTURE_SECONDS=10

section() { printf '\n\033[1m── %s %s\033[0m\n' "$1" "$(printf '─%.0s' $(seq 1 $((56 - ${#1}))))"; }
indent()  { sed 's/^/    /'; }

printf '\033[1mALOUD DIAGNOSTIC REPORT\033[0m\n'
printf 'Paste this whole thing back.\n'

section "Machine"
{
  echo "macOS      $(sw_vers -productVersion 2>/dev/null || echo unknown)"
  echo "arch       $(uname -m)"
  echo "checkout   $REPO_ROOT"
} | indent

section "Which code is installed"
if [ -x .venv/bin/python ]; then
  PYTHONPATH="$REPO_ROOT/src" ./.venv/bin/python -m aloud --version 2>&1 | indent
  echo "    venv python  $(./.venv/bin/python --version 2>&1)"
else
  echo "    NO .venv — bootstrap has not run" | indent
fi
if [ -x "$APP/Contents/MacOS/Aloud" ]; then
  echo "    installed    $("$APP/Contents/MacOS/Aloud" --version 2>&1 | head -1)"
else
  echo "    installed    $APP is not there" | indent
fi

section "Setup check"
if [ -x .venv/bin/python ]; then
  PYTHONPATH="$REPO_ROOT/src" ./.venv/bin/python -m aloud doctor 2>&1 | indent
fi

section "1. From source — is the code itself sound?"
if [ -x .venv/bin/python ]; then
  # Launch it, give it a moment to fail, then stop it. Still running after the
  # timeout is the pass condition.
  PYTHONPATH="$REPO_ROOT/src" ./.venv/bin/python -m aloud run >/tmp/aloud-src.log 2>&1 &
  SRC_PID=$!
  sleep "$CAPTURE_SECONDS"
  if kill -0 "$SRC_PID" 2>/dev/null; then
    echo "    STARTED AND STAYED UP — the code is fine, the problem is packaging." 
    kill "$SRC_PID" 2>/dev/null
    wait "$SRC_PID" 2>/dev/null
  else
    echo "    EXITED — this is the real error:"
    tail -40 /tmp/aloud-src.log | indent
  fi
else
  echo "    skipped, no .venv" | indent
fi

section "2. From the bundle — does the built app start?"
if [ -x "$APP/Contents/MacOS/Aloud" ]; then
  "$APP/Contents/MacOS/Aloud" >/tmp/aloud-app.log 2>&1 &
  APP_PID=$!
  sleep "$CAPTURE_SECONDS"
  if kill -0 "$APP_PID" 2>/dev/null; then
    echo "    STARTED AND STAYED UP."
    kill "$APP_PID" 2>/dev/null
    wait "$APP_PID" 2>/dev/null
  else
    echo "    EXITED — this is the real error:"
    tail -40 /tmp/aloud-app.log | indent
  fi
else
  echo "    skipped, not installed" | indent
fi

section "Recorded launch failure"
LAUNCH_ERROR="$HOME/Library/Logs/Aloud/launch-error.txt"
if [ -f "$LAUNCH_ERROR" ]; then
  tail -40 "$LAUNCH_ERROR" | indent
else
  echo "    none recorded" | indent
fi

section "Log tail"
if [ -f "$HOME/Library/Logs/Aloud/aloud.log" ]; then
  tail -25 "$HOME/Library/Logs/Aloud/aloud.log" | indent
else
  echo "    no log yet" | indent
fi

section "Bundle contents"
if [ -d "$APP" ]; then
  {
    echo "signature  $(codesign -dv "$APP" 2>&1 | grep -i 'Signature\|Identifier' | tr '\n' ' ')"
    echo "executable $(file "$APP/Contents/MacOS/Aloud" 2>&1 | cut -d: -f2-)"
    echo "alias build: $([ -d "$APP/Contents/Resources/lib" ] && echo no || echo yes)"
  } | indent
fi

printf '\n\033[1m── end of report ─────────────────────────────────────────\033[0m\n'
