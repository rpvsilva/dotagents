#!/usr/bin/env bash
# Installs the MCP servers in a servers file into one tool's user config, filling each ${VAR} from the
# environment and ~/.config/dotagents/mcp.env (KEY=value lines, sourced; never committed).
# A server with an unset variable is skipped. Servers the file does not name are left alone.
# Claude goes through `claude mcp`; Cursor's ~/.cursor/mcp.json is edited per server; Codex's
# ~/.codex/config.toml gets one marked block, rewritten each run. Running it twice changes nothing.
# Usage: scripts/mcp-servers.sh <claude|codex|cursor> <servers.json> <backup-dir>
set -euo pipefail

usage="usage: mcp-servers.sh <claude|codex|cursor> <servers.json> <backup-dir>"
tool="${1:?$usage}"
list="${2:?$usage}"
backup="${3:?$usage}"

secrets="${DOTAGENTS_MCP_ENV:-$HOME/.config/dotagents/mcp.env}"
if [ -f "$secrets" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$secrets"
  set +a
fi

# One line per server: {name, missing: [unset vars], server: <config with every ${VAR} filled>}.
servers="$(jq -c '
  def vars: [.. | strings | scan("\\$\\{([A-Za-z_][A-Za-z0-9_]*)\\}")[0]] | unique;
  .mcpServers | to_entries[] | {
    name: .key,
    missing: (.value | vars | map(select(($ENV[.] // "") == ""))),
    server: (.value | walk(if type == "string" then gsub("\\$\\{(?<k>[A-Za-z_][A-Za-z0-9_]*)\\}"; $ENV[.k] // "") else . end))
  }' "$list")"

ready="$(jq -c 'select(.missing == [])' <<<"$servers")"
while read -r skip; do
  echo "  mcp $skip; add it to $secrets"
done < <(jq -r 'select(.missing != []) | "\(.name): skipped, \(.missing | join(", ")) not set"' <<<"$servers")

# Copies $1 into the backup folder once per run, like merge-json.sh.
save() {
  [ -e "$1" ] || return 0
  local saved
  saved="$backup/$(echo "${1#"$HOME"/}" | tr '/' '_')"
  [ -e "$saved" ] && return 0
  mkdir -p "$backup"
  cp "$1" "$saved"
  echo "  backed up $1"
}

# Writes $2 to file $1 when it differs; new files are private, since they hold the filled secrets.
write() {
  local file="$1" content="$2"
  if [ -e "$file" ] && [ "$(cat "$file")" = "$content" ]; then
    echo "  $file: MCP servers already installed"
    return
  fi
  save "$file"
  mkdir -p "$(dirname "$file")"
  [ -e "$file" ] || (umask 077 && : >"$file")
  printf '%s\n' "$content" >"$file"
  echo "  $file: MCP servers installed"
}

install_claude() {
  command -v claude >/dev/null || { echo "  mcp: claude not on PATH, skipped"; return; }
  local failed=0 name server current
  while read -r line; do
    [ -n "$line" ] || continue
    name="$(jq -r .name <<<"$line")"
    server="$(jq -c .server <<<"$line")"
    current="$(jq -c --arg n "$name" '.mcpServers[$n] // null' "$HOME/.claude.json" 2>/dev/null || echo null)"
    if [ "$(jq -S . <<<"$current")" = "$(jq -S . <<<"$server")" ]; then
      echo "  mcp $name: already installed"
      continue
    fi
    [ "$current" = null ] || claude mcp remove "$name" --scope user >/dev/null || true
    if claude mcp add-json "$name" "$server" --scope user >/dev/null; then
      echo "  mcp $name: installed"
    else
      echo "  mcp $name: install failed"
      failed=1
    fi
  done <<<"$ready"
  return "$failed"
}

# Cursor tells stdio from HTTP by the command or url key, so the type key is dropped.
install_cursor() {
  local file="$HOME/.cursor/mcp.json" current="{}"
  [ -s "$file" ] && current="$(cat "$file")"
  jq -e 'type == "object"' >/dev/null 2>&1 <<<"$current" || { echo "  $file: not a JSON object, left as is" >&2; return 1; }
  write "$file" "$(jq --argjson s "$(jq -s . <<<"$ready")" \
    'reduce $s[] as $x (.; .mcpServers[$x.name] = ($x.server | del(.type)))' <<<"$current")"
}

begin="# >>> dotagents mcp: written by install.sh, edit mcp/servers.json instead >>>"
end="# <<< dotagents mcp <<<"

# Codex reads [mcp_servers.<name>] tables; JSON strings are valid TOML strings, so tojson quotes them.
install_codex() {
  local file="$HOME/.codex/config.toml" block rest
  block="$(jq -r '
    def kv: to_entries | map("\(.key | tojson) = \(.value | tojson)") | join(", ") | "{ \(.) }";
    "\n[mcp_servers.\(.name | tojson)]",
    (.server | if .url then "url = \(.url | tojson)" else "command = \(.command | tojson)" end),
    (.server.args // empty | "args = \(tojson)"),
    (.server.env // empty | "env = \(kv)"),
    (.server.headers // empty | "http_headers = \(kv)")' <<<"$ready")"
  rest=""
  [ -e "$file" ] && rest="$(awk -v b="$begin" -v e="$end" '$0 == b {skip=1} !skip {print} $0 == e {skip=0}' "$file")"
  # The block goes last: its tables would swallow any top-level key written after it.
  block="$begin$block"$'\n'"$end"
  [ -z "$rest" ] || block="$rest"$'\n\n'"$block"
  write "$file" "$block"
}

case "$tool" in
  claude) install_claude ;;
  cursor) install_cursor ;;
  codex) install_codex ;;
  *) echo "$usage" >&2; exit 1 ;;
esac
