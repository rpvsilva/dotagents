#!/usr/bin/env bash
# Runs scripts/mcp-servers.sh against a throwaway $HOME and a stub claude, and checks each tool's config.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MCP="$REPO/scripts/mcp-servers.sh"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fails=0

fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

# Passes when jq filter $2 is true on file $1.
check() { jq -e "$2" "$1" >/dev/null || fail "$3"; }

# Stub claude: mcp add-json/remove edit $HOME/.claude.json like the real one, and log the call.
mkdir "$SANDBOX/bin"
cat >"$SANDBOX/bin/claude" <<EOF
#!/usr/bin/env bash
echo "\$*" >>"$SANDBOX/calls"
f="\$HOME/.claude.json"; [ -e "\$f" ] || echo '{}' >"\$f"
case "\$2" in
  add-json) jq --arg n "\$3" --argjson s "\$4" '.mcpServers[\$n] = \$s' "\$f" >"\$f.tmp" ;;
  remove) jq --arg n "\$3" 'del(.mcpServers[\$n])' "\$f" >"\$f.tmp" ;;
esac
mv "\$f.tmp" "\$f"
EOF
chmod +x "$SANDBOX/bin/claude"

cat >"$SANDBOX/servers.json" <<'EOF'
{ "mcpServers": {
  "local": { "type": "stdio", "command": "${HOME}/bin/srv", "args": ["--x"], "env": { "KEY": "${T_KEY}" } },
  "remote": { "type": "http", "url": "https://example.test/mcp", "headers": { "Authorization": "Bearer ${T_TOKEN}" } } } }
EOF

home="$SANDBOX/home"
mkdir -p "$home/.codex" "$home/.cursor"
printf 'model = "o3"\n' >"$home/.codex/config.toml"
echo '{"mcpServers": {"mine": {"command": "x"}}}' >"$home/.cursor/mcp.json"
printf 'T_KEY=k1\nT_TOKEN=t1\n' >"$SANDBOX/mcp.env"

# Runs the script for tool $1 with the secrets file $2; output in $SANDBOX/out.
mcp() {
  HOME="$home" PATH="$SANDBOX/bin:$PATH" DOTAGENTS_MCP_ENV="$2" "$MCP" "$1" "$SANDBOX/servers.json" "$SANDBOX/backup" >"$SANDBOX/out" \
    || fail "$1: exited non-zero: $(cat "$SANDBOX/out")"
}

# First run: every tool gets both servers with variables filled; other servers and keys survive.
: >"$SANDBOX/calls"
for t in claude cursor codex; do mcp "$t" "$SANDBOX/mcp.env"; done
c="$home/.claude.json"
check "$c" ".mcpServers.local == {type: \"stdio\", command: \"$home/bin/srv\", args: [\"--x\"], env: {KEY: \"k1\"}}" "claude: local server wrong"
check "$c" '.mcpServers.remote.headers.Authorization == "Bearer t1"' "claude: header not filled"
grep -q "add-json local {.*} --scope user" "$SANDBOX/calls" || fail "claude: not added at user scope"
u="$home/.cursor/mcp.json"
check "$u" '.mcpServers.mine.command == "x"' "cursor: existing server lost"
check "$u" '.mcpServers.local.env.KEY == "k1" and (.mcpServers.local | has("type") | not)' "cursor: local server wrong"
check "$u" '.mcpServers.remote.url == "https://example.test/mcp"' "cursor: remote server missing"
toml="$home/.codex/config.toml"
head -1 "$toml" | grep -qx 'model = "o3"' || fail "codex: existing key lost"
grep -qx '\[mcp_servers."local"\]' "$toml" || fail "codex: local table missing"
grep -qx 'env = { "KEY" = "k1" }' "$toml" || fail "codex: env not written"
grep -qx 'http_headers = { "Authorization" = "Bearer t1" }' "$toml" || fail "codex: headers not written"
[ -n "$(find "$SANDBOX/backup" -name '*config.toml')" ] || fail "codex: original not backed up"

# Second run: no claude calls, files unchanged.
cp "$toml" "$SANDBOX/toml1"
cp "$u" "$SANDBOX/cursor1"
: >"$SANDBOX/calls"
for t in claude cursor codex; do mcp "$t" "$SANDBOX/mcp.env"; done
[ ! -s "$SANDBOX/calls" ] || fail "second run called claude: $(cat "$SANDBOX/calls")"
cmp -s "$toml" "$SANDBOX/toml1" || fail "codex: second run changed config.toml"
cmp -s "$u" "$SANDBOX/cursor1" || fail "cursor: second run changed mcp.json"

# Changed secret: claude server replaced, codex block rewritten in place, not doubled.
printf 'T_KEY=k2\nT_TOKEN=t1\n' >"$SANDBOX/mcp.env"
: >"$SANDBOX/calls"
mcp claude "$SANDBOX/mcp.env"
mcp codex "$SANDBOX/mcp.env"
grep -q "^mcp remove local --scope user" "$SANDBOX/calls" || fail "claude: changed server not removed first"
check "$c" '.mcpServers.local.env.KEY == "k2"' "claude: changed server not updated"
[ "$(grep -c '^\[mcp_servers."local"\]' "$toml")" = 1 ] || fail "codex: block doubled"
grep -qx 'env = { "KEY" = "k2" }' "$toml" || fail "codex: block not updated"

# Unset variable: that server is skipped and named; the rest installs.
printf 'T_KEY=k3\n' >"$SANDBOX/partial.env"
mcp cursor "$SANDBOX/partial.env"
grep -q "remote: skipped, T_TOKEN not set" "$SANDBOX/out" || fail "unset variable not reported"
check "$u" '.mcpServers.local.env.KEY == "k3"' "partial: set server not installed"

# New Cursor file is private, since it holds secrets.
rm "$u"
mcp cursor "$SANDBOX/mcp.env"
[ "$(stat -f %Lp "$u" 2>/dev/null || stat -c %a "$u")" = 600 ] || fail "cursor: new mcp.json is not 600"

[ "$fails" -eq 0 ] || { echo "mcp-servers.test.sh: $fails failure(s)"; exit 1; }
echo "mcp-servers.test.sh: ok"
