#!/usr/bin/env bash
# Runs adapters/claude/build-epic/launch.sh (~/.agents/bin/build-epic) against a stub claude, and checks the exact
# argv it starts Claude Code with.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH="$REPO/adapters/claude/build-epic/launch.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Stub claude: writes each argument on its own line to $SANDBOX/argv.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/claude" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" >"$SANDBOX/argv"
EOF
chmod +x "$SANDBOX/bin/claude"

tail_args='--strict-mcp-config
--mcp-config
{"mcpServers":{}}
--tools
Bash,Read,Write,Edit,Agent,SendMessage,TaskStop,AskUserQuestion,ToolSearch,Monitor'

# Runs the launcher with arguments $2... and checks claude got prompt $1, then the fixed options.
expect() {
  local want="$1"
  shift
  rm -f "$SANDBOX/argv"
  PATH="$SANDBOX/bin:$PATH" "$LAUNCH" "$@" || { fail "launch $* exited non-zero"; return; }
  [ "$(cat "$SANDBOX/argv")" = "$want"$'\n'"$tail_args" ] ||
    fail "launch $*: claude got $(cat "$SANDBOX/argv" 2>/dev/null || echo nothing)"
}

expect "/build-epic #402 --into baseline-402 --parallel 3" '#402' --into baseline-402 --parallel 3
expect "/build-epic https://github.com/o/r/issues/7" https://github.com/o/r/issues/7
# A plain-text epic in one quoted word stays one prompt; options inside it are not claude's.
expect "/build-epic Add due dates --explore" "Add due dates" --explore
expect "/build-epic"

# Without claude on PATH it says so and exits 127.
code=0
out="$(PATH="/usr/bin:/bin" "$LAUNCH" '#1' 2>&1)" || code=$?
if [ "$code" -ne 127 ] || ! grep -q "claude not on PATH" <<<"$out"; then fail "launch without claude: exit $code, $out"; fi

[ "$fails" -eq 0 ] || { echo "launcher.test.sh: $fails failure(s)"; exit 1; }
echo "launcher.test.sh: ok"
