#!/usr/bin/env bash
#
# Create a stable code-signing identity, so the Accessibility grant survives
# a rebuild.
#
#   ./scripts/make_signing_cert.sh
#
# Why this exists
# ---------------
# macOS attaches a TCC grant (Accessibility, Microphone) to an app's *code
# identity*, not to its path. Ad-hoc signing -- `codesign --sign -`, the
# default when no identity is named -- computes that identity from a hash of
# the bundle's contents, so every rebuild produces a different app as far as
# macOS is concerned.
#
# The symptom is maddening and gives no clue: Aloud sits in the Accessibility
# list with its switch on, and still reports no access, because the grant
# belongs to the build before last. Re-granting fixes it until the next
# rebuild.
#
# A self-signed certificate makes the identity stable, so the grant is made
# once and then stays made.
set -uo pipefail

REPAIR=0
if [ "${1:-}" = "--repair" ]; then REPAIR=1; shift; fi

NAME="${1:-Aloud Dev}"
KEYCHAIN="${KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "macOS only."

# Does the identity actually sign? `find-identity` listing it is not the same
# question: a self-signed certificate that is not trusted for code signing
# lists perfectly well and then fails to build a chain. Believing the listing
# is what let a broken identity reach `make install`, where it fell back to
# ad-hoc and undid the entire point.
# Every SHA-1 hash currently listed for $NAME. More than one means duplicates,
# which is fatal in a way the error message does not make obvious:
#
#     Aloud Dev: ambiguous (matches "Aloud Dev" and "Aloud Dev")
#
# codesign refuses to choose, so a name that looks perfectly present stops
# working. Re-running a script that creates a certificate is how you get two.
identity_hashes() {
  security find-identity -v -p codesigning 2>/dev/null \
    | grep -F "\"$NAME\"" \
    | awk '{print $2}'
}

# Sign by hash, never by name: a hash cannot be ambiguous.
can_sign() {
  local probe status hash
  hash="$(identity_hashes | head -1)"
  if [ -z "$hash" ]; then
    LAST_SIGN_ERROR="no code-signing identity named '$NAME'"
    return 1
  fi
  probe="$(mktemp -t aloud-probe)"
  printf '#!/bin/sh\nexit 0\n' >"$probe"
  chmod +x "$probe"
  codesign --force --sign "$hash" "$probe" 2>"$probe.err"
  status=$?
  if [ $status -ne 0 ]; then
    LAST_SIGN_ERROR="$(cat "$probe.err")"
    [ -z "$LAST_SIGN_ERROR" ] && LAST_SIGN_ERROR="codesign exited $status with no message"
  fi
  rm -f "$probe" "$probe.err"
  return $status
}

# Remove every certificate and key called $NAME, so the next one is the only
# one. Deleting is safe: it is self-signed, used for nothing but this app, and
# recreated immediately below.
forget_duplicates() {
  local removed=0
  local err
  err="$(mktemp -t aloud-delete)"
  while security find-certificate -c "$NAME" >/dev/null 2>&1; do
    if security delete-identity -c "$NAME" >"$err" 2>&1 \
       || security delete-certificate -c "$NAME" >"$err" 2>&1; then
      removed=$((removed + 1))
      [ "$removed" -gt 20 ] && break
    else
      # Say why. Leaving duplicates in place makes every later step report a
      # different symptom of the same ambiguity.
      warn "Could not remove a '$NAME' certificate:"
      sed 's/^/    /' "$err" >&2
      break
    fi
  done
  rm -f "$err"
  [ "$removed" -gt 0 ] && info "Removed $removed old '$NAME' certificate(s)."
  return 0
}

# Re-apply the code-signing trust setting to a certificate already in the
# keychain. This is the step that fails most often -- it needs a password, so
# it is the one that gets cancelled or skipped.
repair_trust() {
  local pem
  pem="$(mktemp -t aloud-cert).pem"
  if ! security find-certificate -c "$NAME" -p >"$pem" 2>"$pem.err"; then
    warn "Could not export '$NAME' from the keychain:"
    sed 's/^/    /' "$pem.err" >&2
    rm -f "$pem" "$pem.err"
    return 1
  fi
  rm -f "$pem.err"
  warn "macOS will ask for your login password — that is the trust setting."
  security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$pem"
  local status=$?
  rm -f "$pem"
  return $status
}

LAST_SIGN_ERROR=""

# Duplicates first: no amount of repairing trust fixes an ambiguous name, and
# every other check below would keep reporting a different symptom.
COUNT="$(identity_hashes | wc -l | tr -d ' ')"
if [ "${COUNT:-0}" -gt 1 ]; then
  warn "There are $COUNT certificates called '$NAME'."
  warn "codesign refuses an ambiguous name, so all of them are unusable."
  forget_duplicates
  COUNT=0
fi

if [ "${COUNT:-0}" -ge 1 ]; then
  if [ "$REPAIR" = "0" ] && can_sign; then
    info "'$NAME' is present and codesign accepts it. Nothing to do."
    info "Build with it:  make install"
    exit 0
  fi

  info "'$NAME' exists but codesign will not use it:"
  printf '%s\n' "$LAST_SIGN_ERROR" | sed 's/^/    /'
  info "Re-applying the code-signing trust setting"
  repair_trust || warn "Could not set it automatically."

  if can_sign; then
    printf '\n\033[1;32m==>\033[0m Repaired. Now:  make install && make fix-permissions\n'
    exit 0
  fi
  printf '%s\n' "$LAST_SIGN_ERROR" | sed 's/^/    /' >&2
  die "Still unusable. Fix it in the GUI:
    Keychain Access > login > My Certificates > '$NAME'
    Double-click it, expand Trust, set 'Code Signing' to 'Always Trust'.
    Then re-run:  ./scripts/make_signing_cert.sh --repair"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A config file rather than -addext: LibreSSL ships as `openssl` on macOS and
# does not accept -addext on every version.
cat >"$WORK/cert.cnf" <<CONF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $NAME
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CONF

info "Generating a self-signed code-signing certificate: $NAME"
if ! openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -config "$WORK/cert.cnf" \
    -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>"$WORK/openssl.err"; then
  sed 's/^/    /' "$WORK/openssl.err" >&2
  die "openssl could not generate the certificate."
fi

# A real password, not an empty one: Apple's SecKeychainItemImport rejects
# empty-password PKCS#12 files with "MAC verification failed (wrong
# password?)", which reads as a corrupt file rather than the policy it is.
# The password never leaves this script.
PASSWORD="$(openssl rand -hex 16)"

# Apple's importer also predates OpenSSL 3's defaults (AES-256 + SHA-256) and
# cannot read them, so ask for the older algorithms. LibreSSL -- which is what
# /usr/bin/openssl is on macOS -- already produces these, but accepts being
# told. If a build accepts neither spelling, fall back to plain defaults.
package() {
  # Kept, not discarded: the last error hidden here was the empty-password
  # rejection, and it took a round trip to recover.
  openssl pkcs12 -export -out "$WORK/identity.p12" \
    -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
    -name "$NAME" -passout "pass:$PASSWORD" "$@" 2>"$WORK/pkcs12.err"
}

if package -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1; then
  :
elif package -legacy; then
  info "Used OpenSSL's -legacy packaging."
elif package; then
  warn "Packaged with this openssl's defaults; the import may not accept them."
else
  [ -s "$WORK/pkcs12.err" ] && sed 's/^/    /' "$WORK/pkcs12.err" >&2
  die "openssl could not package the certificate."
fi

info "Importing it into your login keychain"
# -A lets codesign use the private key without a prompt on every build.
security import "$WORK/identity.p12" -k "$KEYCHAIN" -P "$PASSWORD" -A \
  || die "Could not import the identity into $KEYCHAIN
    If this keeps failing, make the certificate by hand instead:
      Keychain Access > Certificate Assistant > Create a Certificate...
      Name: $NAME   Identity Type: Self Signed Root   Type: Code Signing"

info "Marking it trusted for code signing"
warn "macOS will ask for your login password — that is this step, and it is the only prompt."
if ! security add-trusted-cert -r trustRoot -p codeSign -k "$KEYCHAIN" "$WORK/cert.pem"; then
  warn "Could not set the trust setting automatically."
  warn "Open Keychain Access, find '$NAME' under login > My Certificates,"
  warn "double-click it, expand Trust, and set 'Code Signing' to 'Always Trust'."
fi

security find-identity -v -p codesigning | grep -qF "$NAME" \
  || die "The identity was created but is not listed for code signing yet.
    Set 'Code Signing' to 'Always Trust' on '$NAME' in Keychain Access, then re-run."

# The real test. find-identity listing it does not prove codesign will accept
# it -- an untrusted self-signed root lists fine and then fails to build a
# chain, which would only show up at the next `make install`.
info "Test-signing with it"
if can_sign; then
  printf '\n\033[1;32m==>\033[0m Ready. Sign with it every time:\n\n'
  printf '    CODESIGN_IDENTITY="%s" make install\n\n' "$NAME"
  printf '    install_app.sh also picks it up automatically now that it exists.\n'
  printf '    Grant Accessibility once more after the next install; it sticks from then on.\n'
else
  warn "The identity exists but codesign will not use it yet:"
  printf '%s\n' "$LAST_SIGN_ERROR" | sed 's/^/    /' >&2
  die "Open Keychain Access, find '$NAME' under login > My Certificates,
    double-click it, expand Trust, set 'Code Signing' to 'Always Trust',
    then re-run this script."
fi
