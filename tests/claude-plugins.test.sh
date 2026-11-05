#!/usr/bin/env bash
# Runs scripts/claude-plugins.sh against a stub claude and checks what it adds and installs.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub claude: lists come from $SANDBOX/{markets,plugins} (one name per line; a plugin line "<id> off" is installed
# but disabled), add/install append to them and log the call; a plugin named "broken@m" fails to install, and
# "stuck@m" to enable. An added marketplace is named after its source's
# last part.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/claude" <<EOF
#!/usr/bin/env bash
echo "\$*" >>"$SANDBOX/calls"
case "\$2 \$3 \$4" in
  "marketplace list --json") jq -R '{name: .}' "$SANDBOX/markets" | jq -s . ;;
  "list --json "*) jq -R 'split(" ") | {id: .[0], enabled: (.[1] != "off")}' "$SANDBOX/plugins" | jq -s . ;;
  "marketplace add "*) echo "\${4##*/}" >>"$SANDBOX/markets" ;;
  "install "*) [ "\$3" = broken@m ] && exit 1; echo "\$3" >>"$SANDBOX/plugins" ;;
  "enable "*) [ "\$3" = stuck@m ] && exit 1; sed -i.bak "s/^\$3 off\$/\$3/" "$SANDBOX/plugins" ;;
esac
EOF
chmod +x "$SANDBOX/bin/claude"

cat >"$SANDBOX/plugins.txt" <<'EOF'
# a comment, then a blank line

have@official owner/official
new@official owner/official
other@extra owner/extra
EOF

# Runs the script; $1 is the wanted exit ("ok" or "fail").
plugins() {
  local want="$1" got
  : >"$SANDBOX/calls"
  if PATH="$SANDBOX/bin:$PATH" CLAUDE_CONFIG_DIR="$SANDBOX/cfg" "$REPO/scripts/claude-plugins.sh" "$SANDBOX/plugins.txt" >"$SANDBOX/out"; then got=ok; else got=fail; fi
  [ "$got" = "$want" ] || fail "wanted $want, got $got: $(cat "$SANDBOX/out")"
}
called() { grep -qxF -- "$1" "$SANDBOX/calls" || fail "not called: claude $1"; }
not_called() { if grep -qF -- "$1" "$SANDBOX/calls"; then fail "called: claude $1"; fi; }

# First run: missing marketplace added once, missing plugins installed, present one left alone.
echo official >"$SANDBOX/markets"
echo have@official >"$SANDBOX/plugins"
plugins ok
called "plugin marketplace add owner/extra"
not_called "marketplace add owner/official"
called "plugin install new@official --scope user"
called "plugin install other@extra --scope user"
not_called "install have@official"

# Second run: nothing to do.
plugins ok
not_called "add"
not_called "install"
grep -q "already installed" "$SANDBOX/out" || fail "second run did not say already installed"

# A CRLF plugins file (a Windows checkout) reads the same: no carriage return reaches a source or an id.
printf 'new@extra owner/extra\r\n' >"$SANDBOX/plugins.txt"
: >"$SANDBOX/markets"
: >"$SANDBOX/plugins"
plugins ok
called "plugin marketplace add owner/extra"
called "plugin install new@extra --scope user"

# CRLF in what claude lists (claude.exe on Windows) still matches: a present marketplace and plugin are left alone.
printf 'official\r\n' >"$SANDBOX/markets"
printf 'have@official\r\n' >"$SANDBOX/plugins"
echo 'have@official owner/official' >"$SANDBOX/plugins.txt"
plugins ok
not_called "add"
not_called "install"

# A failed install is reported, the rest still installs, and the exit is non-zero.
printf 'broken@m o/m\nlate@m o/m\n' >"$SANDBOX/plugins.txt"
plugins fail
called "plugin install late@m --scope user"
grep -q "broken@m: install failed" "$SANDBOX/out" || fail "failed install not reported"

# A disabled plugin the user's settings never mention (installed at another scope) gets enabled at user scope; one
# set to false there stays off; a failed enable is reported with a non-zero exit. No settings file reads as no entry.
mkdir -p "$SANDBOX/cfg"
printf 'never@m o/m\noff@m o/m\n' >"$SANDBOX/plugins.txt"
echo m >"$SANDBOX/markets"
printf 'never@m off\noff@m off\n' >"$SANDBOX/plugins"
echo '{"enabledPlugins": {"off@m": false}}' >"$SANDBOX/cfg/settings.json"
plugins ok
called "plugin enable never@m --scope user"
not_called "enable off@m"
not_called "install"
grep -q "never@m: enabled" "$SANDBOX/out" || fail "enable not reported"
grep -q "off@m: turned off in .*, left off" "$SANDBOX/out" || fail "plugin turned off on purpose not reported"
rm "$SANDBOX/cfg/settings.json"
echo 'stuck@m o/m' >"$SANDBOX/plugins.txt"
echo 'stuck@m off' >"$SANDBOX/plugins"
plugins fail
grep -q "stuck@m: enable failed" "$SANDBOX/out" || fail "failed enable not reported"

[ "$fails" -eq 0 ] || { echo "claude-plugins.test.sh: $fails failure(s)"; exit 1; }
echo "claude-plugins.test.sh: ok"
