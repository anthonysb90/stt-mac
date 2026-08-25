#!/usr/bin/env bash
#
# Run Aloud at login without an app bundle.
#
#   ./scripts/login_item.sh install
#   ./scripts/login_item.sh remove
#   ./scripts/login_item.sh status
#
# A LaunchAgent starting the app straight from this checkout. No py2app, no
# bundle, nothing to go wrong at launch — it is the same command `make run`
# uses, minus the Terminal window.
#
# The trade is where macOS attaches permissions. With a bundle they attach to
# Aloud.app; here they attach to the Python binary in .venv, so that is what
# you add under Accessibility. It works and it is stable, but the entry in the
# list says "Python", which is why the bundle is still the better answer when
# it works.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
LABEL="com.aloud.Aloud"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$HOME/Library/Logs/Aloud"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

case "${1:-status}" in

install)
  [ -x "$PYTHON" ] || die "No .venv here — run ./scripts/bootstrap.sh first."
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"

  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>aloud</string>
        <string>run</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$REPO_ROOT/src</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>$REPO_ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <!-- Restart if it dies, but not in a tight loop if it dies immediately. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/launchagent.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchagent.log</string>
</dict>
</plist>
PLISTEOF

  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  LOADERR="$(mktemp -t aloud-launchctl)"
  if ! launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>"$LOADERR" \
     && ! launchctl load -w "$PLIST" 2>>"$LOADERR"; then
    # launchctl's reasons are specific and worth reading -- a bad plist and a
    # denied session report quite differently.
    sed 's/^/    /' "$LOADERR" >&2
    rm -f "$LOADERR"
    die "launchctl refused to load $PLIST"
  fi
  rm -f "$LOADERR"

  info "Installed and started."
  info "Log: $LOG_DIR/launchagent.log"
  cat <<'NEXT'

    Grant Accessibility to the Python binary running it:
      System Settings > Privacy & Security > Accessibility > +
      Press Cmd-Shift-G and paste the path this prints:
NEXT
  printf '        %s\n\n' "$PYTHON"
  ;;

remove)
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
    || launchctl unload -w "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  info "Removed. Aloud will not start at login."
  ;;

status)
  if [ -f "$PLIST" ]; then
    info "Installed: $PLIST"
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null \
      | grep -E "state|pid|last exit" | sed 's/^/    /' \
      || echo "    not currently loaded"
  else
    info "Not installed. Run: ./scripts/login_item.sh install"
  fi
  ;;

*)
  die "Usage: $0 [install|remove|status]"
  ;;
esac
