#!/usr/bin/env bash
# The part of scripts/remote-agent.sh that runs inside the sandbox; remote-agent.sh passes it to `bash -c`.
# Expects the setup unpacked under $HOME (scripts/remote-agent-pack.sh) and the worktree snapshot in ~/work.
# Arguments after $0: the caps, remote.cap_mb (`auto` or left out: from the sandbox's own memory limit) and
# remote.cap_secs (300 when left out), then the model to run the harness on (left out: the harness's default);
# arguments, since the backends pass argv, not the environment.
# The memory cap is the sandbox's share of its machine: its cgroup's memory limit (v2 memory.max, else v1
# memory/memory.limit_in_bytes, under REMOTE_AGENT_CGROUP, default /sys/fs/cgroup) less 1024 MB kept for the agent (half
# the limit when that is under 2048 MB); a number for remote.cap_mb only lowers it. With no limit to read, the number
# is the cap, else 2000. The caps are also exported as CAPPED_MB and CAPPED_SECS, so a test command's own `capped`
# with no flags runs under the same ones.
# Stdin: a header of NAME=value lines up to an empty line, then the brief. The header holds REMOTE_HARNESS (claude,
# codex or opencode, the agent CLI to run; claude when left out), REMOTE_BASE_URL (a model gateway, optional),
# REMOTE_GATEWAY_API (the API it speaks, optional: anthropic, openai-responses or openai-chat) and the one credential
# the harness logs in with (CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN for claude,
# CODEX_API_KEY for codex, ANTHROPIC_API_KEY or OPENAI_API_KEY for opencode, GATEWAY_API_KEY for any with a gateway);
# any other name is refused. For claude the gateway is ANTHROPIC_BASE_URL, and GATEWAY_API_KEY its bearer token
# (ANTHROPIC_AUTH_TOKEN); for codex, a model provider in ~/.codex/config.toml reading the key from the credential's
# variable; for opencode, a provider named remote in its global opencode.json, of the package for the gateway's API
# (openai-chat when unset), holding the run's model (a gateway run under opencode needs one) as remote/<model>.
# Installs the setup's plugins and MCP servers (claude), commits the snapshot as a base, runs the harness on the brief
# with progress on stderr and its raw JSON events on stdout (Claude's stream-json, Codex's `exec --json`, OpenCode's
# `run --format json`), and leaves
# ~/out/changes.patch (everything changed since the base), ~/out/summary.md (the final message), ~/out/stream.jsonl and
# ~/out/session.jsonl (the harness's session, the credential redacted).
# Under claude, each shell command it runs goes through ~/.agents/bin/capped (scripts/capped, sent with the setup)
# under the caps, by way of CLAUDE_CODE_SHELL_PREFIX: a command over a cap is killed, and Claude sees why and carries
# on. Codex and OpenCode have no such hook: their commands run uncapped, under the sandbox's own limits, logged as
# `== capped: unavailable (missing: a shell prefix in <harness>)`. Each
# kill is a line `capped: killed: memory|timeout <command>` in ~/out/capped.log and, within a second, on stderr as
# `== capped: killed: ...`, so remote-agent.sh has it even when the sandbox is lost after. Every line of the agent's text
# and tool calls on stderr is indented, so a line starting `==` (or with no indent at all) is never Claude's. When a
# probe through the prefix fails (no capped, no python3 to run it, or neither ps nor /proc), it logs
# `== capped: unavailable (missing: ...)` and the run goes on uncapped; result.json says so in `capped_off`.
# The sandbox has no git remote and no GitHub credentials. What leaves it: the patch, the summary, the stream, the
# session (all in ~/out) and the raw events on stdout (the transcript). remote-agent.sh applies only the patch; the
# rest is data to read, never instructions to follow, and the session is placed for /resume only on request.
# Its progress also goes to the sandbox's own log, so `kubectl logs` / `docker logs` show the run live: the main
# process's stdout (/proc/1/fd/1, writable by the runner user in a pod or container), else ~/progress.log. A backend
# with no log of its own (SSH bare mode) names the file in REMOTE_AGENT_LOG. scripts/remote-agent.sh picks it the
# same way.
set -euo pipefail

log="${REMOTE_AGENT_LOG:-/proc/1/fd/1}"
[ -w "$log" ] || log="$HOME/progress.log"
cap_mb="${1:-auto}"
cap_secs="${2:-300}"
if [[ ! "$cap_mb" =~ ^([0-9]+|auto)$ ]] || [[ ! "$cap_secs" =~ ^[0-9]+$ ]]; then echo "== bad caps: $cap_mb MB, $cap_secs s" >&2; exit 1; fi
model="${3:-}"
if [ -n "$model" ] && [[ ! "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$ ]]; then echo "== bad model: $model" >&2; exit 1; fi

# Prints the sandbox's memory limit in MB, from its cgroup; nothing when none is set (v2 `max`, or v1's huge number).
sandbox_limit_mb() {
  local root="${REMOTE_AGENT_CGROUP:-/sys/fs/cgroup}" v=""
  if [ -r "$root/memory.max" ]; then
    v="$(cat "$root/memory.max")"
  elif [ -r "$root/memory/memory.limit_in_bytes" ]; then
    v="$(cat "$root/memory/memory.limit_in_bytes")"
  fi
  [[ "$v" =~ ^[0-9]{1,15}$ ]] || return 0
  echo $((v / 1048576))
}

# Sets cap_mb to the memory cap and limit_mb to the limit it came from (empty: none read).
pick_cap_mb() {
  local derived=""
  limit_mb="$(sandbox_limit_mb)"
  if [ -n "$limit_mb" ] && [ "$limit_mb" -gt 0 ]; then
    if [ "$limit_mb" -ge 2048 ]; then derived=$((limit_mb - 1024)); else derived=$((limit_mb / 2)); fi
  fi
  if [ "$cap_mb" = auto ]; then
    cap_mb="${derived:-2000}"
  elif [ -n "$derived" ] && [ "$derived" -lt "$cap_mb" ]; then
    cap_mb="$derived"
  fi
}

# Writes the shell prefix to $1. Claude Code runs it with the whole command line as one argument, and quotes a prefix
# holding " -" as one word, so the caps are written in here rather than passed. A Bash-tool command (it ends with
# `pwd -P >| <file>`) runs under capped, its stderr read for capped's kill line; anything else run through the prefix
# (a hook or an MCP server, which need their stdin and may outlive any cap) runs as it is.
write_prefix() {
  {
    echo '#!/usr/bin/env bash'
    printf 'capped=%q kills=%q mb=%s secs=%s\n' "$HOME/.agents/bin/capped" "$HOME/out/capped.log" "$cap_mb" "$cap_secs"
    cat <<'EOF'
[ $# -eq 1 ] || exec "$@"
case "$1" in *"pwd -P >| "*) ;; *) exec bash -c "$1" ;; esac
# The command as the agent wrote it, for the kill line: what Claude Code's `eval <command>` before the pwd holds,
# unquoted, on one line.
cmd="${1% && pwd -P >| *}"
cmd="${cmd#* && eval }"
q="'\''"
case "$cmd" in
  \'*\') cmd="${cmd:1:${#cmd}-2}"; cmd="${cmd//"$q"/\'}" ;;
  \"*\") cmd="${cmd:1:${#cmd}-2}"; cmd="$(printf '%s' "$cmd" | sed 's/\\\(.\)/\1/g')" ;;
esac
cmd="$(printf '%s' "$cmd" | tr '\n' ' ' | cut -c1-200)"
{
  "$capped" --mb "$mb" --secs "$secs" -- bash -c "$1" 2>&1 >&3 3>&- | while IFS= read -r l || [ -n "$l" ]; do
    printf '%s\n' "$l" >&2
    case "$l" in "capped: killed: "*) printf '%s %s\n' "$l" "$cmd" >>"$kills" ;; esac
  done
  rc=${PIPESTATUS[0]}
} 3>&1
exit "$rc"
EOF
  } >"$1"
  chmod +x "$1"
}

# Copies to stderr, behind `== `, each line ~/out/capped.log gains, every 0.2 s, until file $1 exists; then the last
# ones.
follow_kills() {
  local n=0 total last=""
  while :; do
    [ ! -e "$1" ] || last=1
    total="$(wc -l <"$HOME/out/capped.log" | tr -d ' ')"
    if [ "$total" -gt "$n" ]; then
      sed -n "$((n + 1)),${total}s/^/== /p" "$HOME/out/capped.log" >&2
      n="$total"
    fi
    [ -z "$last" ] || return 0
    # The 0.2 s in steps, so a stop is seen within 0.05 s rather than holding up the run's end.
    for _ in 1 2 3 4; do
      [ ! -e "$1" ] || break
      sleep 0.05
    done
  done
}

# jq's redact: a progress line with the credential's value (scrub_secret in jq's environment, never argv) replaced by
# [redacted], so the agent's text echoing it never reaches the sandbox's log or stderr.
# shellcheck disable=SC2016 # jq's variables
redact_jq='def redact: if ($ENV.scrub_secret // "") == "" then . else split($ENV.scrub_secret) | join("[redacted]") end; '

# Runs Claude on the brief. Progress: Claude's text and each tool call, every line indented (so none passes for this
# script's own `==` lines, nor for a line of the backend's client); the full stream is kept for the summary, and goes
# out raw, as it comes, on the run's stdout (fd 3: see the end), which remote-agent.sh keeps as the live transcript.
# Each text is held until the next assistant or user event: the last one before a successful result is the final
# report, which remote-agent.sh prints on stdout from summary.md, so it is dropped here rather than shown twice on its
# stderr. Before an error result, or with no result at all, it is shown. Sets session (the session file, or empty),
# session_id and failed (non-empty when the result is an error).
run_claude() {
  local f
  claude -p "$brief" ${model:+--model "$model"} --dangerously-skip-permissions --append-system-prompt "$rules" --output-format stream-json --verbose \
    | tee "$HOME/out/stream.jsonl" /dev/fd/3 \
    | scrub_secret="$secret" jq -rn --unbuffered "$redact_jq"'foreach ((inputs | objects), {type: "end"}) as $e ({held: null, out: []};
        if $e.type == "assistant" then
          reduce ($e.message.content[]? | objects) as $c (.out = [];
            if $c.type == "text" then .out += [.held | strings] | .held = ($c.text | if type == "string" then "  " + gsub("\n"; "\n  ") else . end)
            elif $c.type == "tool_use" then
              .out += [.held | strings] | .held = null
              | .out += ["  → \($c.name) \($c.input.command // $c.input.file_path // $c.input.pattern // $c.input.description // "" | tostring | gsub("\n"; " ") | .[0:120])"]
            else . end)
        elif $e.type == "user" then .out = [.held | strings] | .held = null
        elif $e.type == "result" then .out = (if $e.is_error == true then [.held | strings] else [] end) | .held = null
        elif $e.type == "end" then .out = [.held | strings] | .held = null
        else .out = [] end;
        .out[]) | redact' >&2
  jq -r 'select(.type == "result") | .result // ""' "$HOME/out/stream.jsonl" >"$HOME/out/summary.md"
  # The session Claude wrote, named by its init event.
  session_id="$(jq -r 'select(.type == "system" and .subtype == "init") | .session_id // empty' "$HOME/out/stream.jsonl" | head -1 | tr -d '\r')"
  session=""
  if [ -n "$session_id" ]; then
    for f in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/"$session_id.jsonl"; do
      if [ -f "$f" ]; then session="$f"; break; fi
    done
  fi
  failed=""
  if jq -e 'select(.type == "result") | .is_error' "$HOME/out/stream.jsonl" >/dev/null; then failed=1; fi
}

# Runs Codex (`codex exec --json`) on the brief, approvals and its own sandbox off (the container is the sandbox).
# Progress as run_claude's: each completed agent message held until the next item, each command or file change one
# `  → ` line; the last message before turn.completed is the final report, dropped here. Sets session (the thread's
# rollout file under ~/.codex/sessions, or empty), session_id (the thread id) and failed (non-empty unless the last
# turn event is turn.completed).
run_codex() {
  codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check ${model:+-m "$model"} -- "$brief" </dev/null \
    | tee "$HOME/out/stream.jsonl" /dev/fd/3 \
    | scrub_secret="$secret" jq -rn --unbuffered "$redact_jq"'foreach ((inputs | objects), {type: "end"}) as $e ({held: null, out: []};
        if $e.type == "item.completed" and ($e.item | type) == "object" then
          $e.item as $i
          | if $i.type == "agent_message" then
              .out = [.held | strings] | .held = ($i.text | if type == "string" then "  " + gsub("\n"; "\n  ") else null end)
            elif $i.type == "command_execution" then
              .out = [.held | strings] + ["  → shell \($i.command // "" | tostring | gsub("\n"; " ") | .[0:120])"] | .held = null
            elif $i.type == "file_change" then
              .out = [.held | strings] + ["  → edit \([$i.changes[]?.path | strings] | join(" ") | .[0:120])"] | .held = null
            elif $i.type == "mcp_tool_call" then
              .out = [.held | strings] + ["  → \($i.server // "mcp")/\($i.tool // "")"] | .held = null
            else .out = [] end
        elif $e.type == "turn.completed" then .out = [] | .held = null
        elif $e.type == "turn.failed" or $e.type == "error" then
          .out = [.held | strings] + ["  error: \($e.error.message? // $e.message // "" | tostring | gsub("\n"; " ") | .[0:300])"] | .held = null
        elif $e.type == "end" then .out = [.held | strings] | .held = null
        else .out = [] end;
        .out[]) | redact' >&2
  jq -rn '[inputs | objects | select(.type == "item.completed" and .item.type? == "agent_message") | .item.text | strings] | last // ""' \
    "$HOME/out/stream.jsonl" >"$HOME/out/summary.md"
  session_id="$(jq -r 'select(.type == "thread.started") | .thread_id // empty' "$HOME/out/stream.jsonl" | head -1 | tr -d '\r')"
  session=""
  if [[ "$session_id" =~ ^[A-Za-z0-9-]+$ ]] && [ -d "${CODEX_HOME:-$HOME/.codex}/sessions" ]; then
    session="$(find "${CODEX_HOME:-$HOME/.codex}/sessions" -type f -name "rollout-*-$session_id.jsonl" | head -1)"
  fi
  failed=""
  if [ "$(jq -rn '[inputs | objects | select(.type == "turn.completed" or .type == "turn.failed" or .type == "error") | .type] | last // ""' \
    "$HOME/out/stream.jsonl" | tr -d '\r')" != turn.completed ]; then failed=1; fi
}

# Runs OpenCode (`opencode run --format json`) on the brief: --standalone, so its private server takes the credential
# from this environment (the shared background server would not), and --auto, approving every permission not denied
# (the container is the sandbox). Progress as run_claude's: each text part held (its deltas, same part id, joined)
# until the next part, each tool call one `  → ` line, an error event shown with what was held; the text held at the
# end is the final report, dropped here. Logs `== opencode version <v>` first, for result.json. Sets session (the
# session as `opencode session export` gives it, on one line, or empty), session_id (the events' sessionID) and failed
# (non-empty when opencode exits non-zero, an error event came, or no step finished).
run_opencode() {
  local v
  v="$(opencode --version </dev/null 2>/dev/null | head -1 | tr -d '\r')" || v=""
  [[ ! "$v" =~ ^v?[0-9][0-9A-Za-z.+-]*$ ]] || echo "== opencode version ${v#v}" >&2
  rm -f "$HOME/.opencode-rc"
  { opencode run --standalone --auto --format json ${model:+-m "$model"} "$brief" </dev/null || echo "$?" >"$HOME/.opencode-rc"; } \
    | tee "$HOME/out/stream.jsonl" /dev/fd/3 \
    | scrub_secret="$secret" jq -rn --unbuffered "$redact_jq"'foreach ((inputs | objects), {type: "end"}) as $e ({held: null, hid: null, out: []};
        if $e.type == "text" and ($e.part | type) == "object" then
          ($e.part.text | if type == "string" then gsub("\n"; "\n  ") else "" end) as $t
          | if $e.part.id != null and $e.part.id == .hid and .held != null then .out = [] | .held += $t
            else .out = [.held | strings] | .hid = $e.part.id | .held = "  " + $t end
        elif $e.type == "tool_use" and ($e.part | type) == "object" then
          ($e.part.state.input? // {}) as $in
          | .out = [.held | strings]
              + ["  → \($e.part.tool // "tool") \(($in | objects | .command // .filePath // .pattern // .description // .url) // "" | tostring | gsub("\n"; " ") | .[0:120])"]
          | .held = null | .hid = null
        elif $e.type == "error" then
          .out = [.held | strings] + ["  error: \($e.error.message? // $e.error.data.message? // $e.error // "" | tostring | gsub("\n"; " ") | .[0:300])"]
          | .held = null | .hid = null
        elif $e.type == "end" then .out = [] | .held = null
        else .out = [] end;
        .out[]) | redact' >&2
  # The final report: the last text part, its deltas joined.
  jq -rn 'reduce (inputs | objects | select(.type == "text") | .part | objects) as $p ([];
      if $p.id != null and (last | .id?) == $p.id then .[-1].text += ($p.text | strings // "")
      else . + [{id: $p.id, text: ($p.text | strings // "")}] end) | last | .text? // ""' \
    "$HOME/out/stream.jsonl" >"$HOME/out/summary.md"
  session_id="$(jq -r '.sessionID // empty | strings' "$HOME/out/stream.jsonl" 2>/dev/null | head -1 | tr -d '\r')" || session_id=""
  session=""
  if [[ "$session_id" =~ ^[A-Za-z0-9_-]+$ ]] \
    && opencode session export "$session_id" --standalone </dev/null 2>/dev/null | jq -c . >"$HOME/.opencode-session.jsonl" 2>/dev/null \
    && [ -s "$HOME/.opencode-session.jsonl" ]; then
    session="$HOME/.opencode-session.jsonl"
  fi
  failed=""
  if [ -e "$HOME/.opencode-rc" ] || jq -e 'select(.type == "error")' "$HOME/out/stream.jsonl" >/dev/null 2>&1 \
    || ! jq -e 'select(.type == "step_finish")' "$HOME/out/stream.jsonl" >/dev/null 2>&1; then failed=1; fi
}

# The whole run, left unindented; its stdout and stderr are kept apart, stderr copied to the log at the end.
main() {
harness=claude
base_url=""
gateway_api=""
secret=""
cred=""
while IFS= read -r line; do
  [ -n "$line" ] || break
  name="${line%%=*}"
  value="${line#*=}"
  case "$name" in
    REMOTE_HARNESS) harness="$value" ;;
    REMOTE_BASE_URL) base_url="$value" ;;
    REMOTE_GATEWAY_API) gateway_api="$value" ;;
    CLAUDE_CODE_OAUTH_TOKEN | ANTHROPIC_API_KEY | ANTHROPIC_AUTH_TOKEN | CODEX_API_KEY | OPENAI_API_KEY | GATEWAY_API_KEY)
      [ -z "$cred" ] || { echo "== two credentials in the header" >&2; exit 1; }
      cred="$name"
      secret="$value"
      ;;
    *) echo "== bad header line: ${name:0:40}" >&2; exit 1 ;;
  esac
done
brief="$(cat)"
[ -z "$cred" ] || [ "$cred" != GATEWAY_API_KEY ] || [ -n "$base_url" ] || { echo "== GATEWAY_API_KEY without a gateway" >&2; exit 1; }
case "$harness" in claude | codex | opencode) ;; *) echo "== unknown harness: ${harness:0:40}" >&2; exit 1 ;; esac
case "$harness:$cred" in
  claude:GATEWAY_API_KEY) export ANTHROPIC_AUTH_TOKEN="$secret" ;;
  claude:CLAUDE_CODE_OAUTH_TOKEN | claude:ANTHROPIC_API_KEY | claude:ANTHROPIC_AUTH_TOKEN | codex:CODEX_API_KEY | codex:GATEWAY_API_KEY \
    | opencode:ANTHROPIC_API_KEY | opencode:OPENAI_API_KEY | opencode:GATEWAY_API_KEY)
    export "$cred=$secret" ;;
  *:) ;;
  *) echo "== $harness does not take $cred" >&2; exit 1 ;;
esac
[ "$harness" != claude ] || [ -z "$base_url" ] || export ANTHROPIC_BASE_URL="$base_url"
url_re='^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+$'
if [ -n "$base_url" ] && [[ ! "$base_url" =~ $url_re ]]; then echo "== bad base url" >&2; exit 1; fi
# The package of OpenCode's gateway provider: the one that speaks the gateway's API.
case "$harness:$gateway_api" in
  opencode: | opencode:openai-chat) oc_package=@opencode/ai/providers/openai-compatible ;;
  opencode:openai-responses) oc_package=@opencode/ai/providers/openai-compatible/responses ;;
  opencode:anthropic) oc_package=@opencode/ai/providers/anthropic-compatible ;;
  claude: | claude:anthropic | codex: | codex:openai-responses) oc_package="" ;;
  *) echo "== $harness does not speak ${gateway_api:0:40}" >&2; exit 1 ;;
esac
if [ "$harness" = opencode ] && [ -n "$base_url" ] && [ -z "$model" ]; then
  echo "== opencode through a gateway needs a model (remote.model or --model)" >&2
  exit 1
fi
cd "$HOME"
mkdir -p out

# Plugins and MCP servers are reinstalled here rather than copied: their local copies hold absolute paths.
settings=.claude/settings.json
if [ "$harness" = claude ] && [ -f "$settings" ]; then
  jq -r '.extraKnownMarketplaces // {} | .[] | .source.repo // empty' "$settings" | while read -r repo; do
    claude plugin marketplace add "$repo" >/dev/null 2>&1 || echo "== warning: marketplace $repo not added" >&2
  done
  jq -r '.enabledPlugins // {} | to_entries[] | select(.value) | .key' "$settings" | while read -r plugin; do
    claude plugin install "$plugin" >/dev/null 2>&1 || echo "== warning: plugin $plugin not installed" >&2
  done
fi
if [ "$harness" = claude ] && [ -f .claude/remote-mcp.json ]; then
  jq -r 'to_entries[] | "\(.key)\t\(.value | tojson)"' .claude/remote-mcp.json | while IFS=$'\t' read -r name json; do
    claude mcp add-json -s user "$name" "$json" >/dev/null 2>&1 || echo "== warning: MCP server $name not added" >&2
  done
fi

cd work
git init -q
git config user.name remote-agent
git config user.email remote-agent@localhost
git add -A
git commit -q --no-verify -m snapshot
base="$(git rev-parse HEAD)"

rules="You run unattended as a remote subagent, in $HOME/work: a snapshot of the user's working tree with no git \
remote and no GitHub access. Do the task in that folder. Leave your changes in the files (committing is optional); \
they go back to the user as a patch. Do not push or open PRs."
# Codex has no flag for extra instructions: they go in its global AGENTS.md (outside ~/work, so not in the patch), and
# a gateway is a model provider of its config, which reads the key from the credential's variable. The URL was checked
# above, so it holds no quote or backslash.
if [ "$harness" = codex ]; then
  mkdir -p "$HOME/.codex"
  printf '\n%s\n' "$rules" >>"$HOME/.codex/AGENTS.md"
  if [ -n "$base_url" ]; then
    printf 'model_provider = "remote"\n\n[model_providers.remote]\nname = "remote gateway"\nbase_url = "%s"\nenv_key = "%s"\nwire_api = "responses"\n' \
      "$base_url" "${cred:-CODEX_API_KEY}" >"$HOME/.codex/config.toml"
  fi
fi

# OpenCode's rules go in its global AGENTS.md too (outside ~/work); a gateway is a provider named remote in its global
# config, its key read from the credential's variable and the run's model its one model, run as remote/<model>.
if [ "$harness" = opencode ]; then
  oc_config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  mkdir -p "$oc_config"
  printf '\n%s\n' "$rules" >>"$oc_config/AGENTS.md"
  if [ -n "$base_url" ]; then
    # shellcheck disable=SC2016 # jq's variables
    jq -n --arg url "$base_url" --arg env "${cred:-GATEWAY_API_KEY}" --arg pkg "$oc_package" --arg m "$model" \
      '{"$schema": "https://opencode.ai/config.json", model: ("remote/" + $m),
        providers: {remote: {name: "remote gateway", env: [$env], package: $pkg, settings: {baseURL: $url},
          models: {($m): {name: $m}}}}}' >"$oc_config/opencode.json"
    model="remote/$model"
  fi
fi

# The caps, once a probe through the prefix shows capped runs here.
pick_cap_mb
export CAPPED_MB="$cap_mb" CAPPED_SECS="$cap_secs"
: >"$HOME/out/capped.log"
prefix="$HOME/.agents/bin/capped-shell"
mkdir -p "$HOME/.agents/bin"
write_prefix "$prefix"
if [ "$harness" != claude ]; then
  echo "== capped: unavailable (missing: a shell prefix in $harness): the memory and time caps are OFF, shell commands run uncapped" >&2
elif "$prefix" "true && eval true && pwd -P >| /dev/null" </dev/null >/dev/null 2>&1; then
  export CLAUDE_CODE_SHELL_PREFIX="$prefix"
  echo "== capped: shell prefix set (--mb $cap_mb --secs $cap_secs; sandbox limit: ${limit_mb:-none} MB)" >&2
else
  missing=""
  [ -x "$HOME/.agents/bin/capped" ] || missing="capped"
  python3 -c 0 >/dev/null 2>&1 || missing="${missing:+$missing, }python3"
  # capped reads /proc where there is no ps (Linux), so ps is missing only without both.
  ps -A -o pgid= >/dev/null 2>&1 || [ -r /proc/self/stat ] || missing="${missing:+$missing, }ps"
  echo "== capped: unavailable (missing: ${missing:-unknown, see ~/.agents/bin/capped-shell}): the memory and time caps are OFF, shell commands run uncapped" >&2
fi
stop_follow="$HOME/.capped-follow-stop"
rm -f "$stop_follow"
follow_kills "$stop_follow" &
follower=$!
# Stopped on any exit too (a failed harness ends main under set -e), so it never holds stderr open.
trap ': >"$stop_follow"; wait "$follower" || true' EXIT

echo "== $harness${model:+ ($model)}" >&2
case "$harness" in codex) run_codex ;; opencode) run_opencode ;; *) run_claude ;; esac
: >"$stop_follow"
wait "$follower" || true
trap - EXIT

# The session the harness wrote goes back as ~/out/session.jsonl with the credential's value (through the
# environment, never argv) replaced by [redacted]; `remote-agent resume` places a Claude one for /resume. Missing:
# said, not fatal.
if [ -n "$session" ]; then
  scrub_secret="$secret" awk 'BEGIN { tok = ENVIRON["scrub_secret"] }
    { s = $0; out = ""
      while (tok != "" && (i = index(s, tok)) > 0) { out = out substr(s, 1, i - 1) "[redacted]"; s = substr(s, i + length(tok)) }
      print out s }' "$session" >"$HOME/out/session.jsonl"
else
  echo "== no session file${session_id:+ for $session_id}: the run cannot be resumed" >&2
fi
if [ -n "$failed" ]; then
  echo "== $harness reported an error" >&2
  exit 1
fi

git add -A
git diff --cached --binary "$base" >"$HOME/out/changes.patch"
echo "== $(git diff --cached --shortstat "$base" || true)" >&2
}

# Synchronous, unlike `exec 2> >(tee ...)`: every line is in the log before this script exits.
{ main 2>&1 >&3 | tee -a "$log" >&2; } 3>&1
