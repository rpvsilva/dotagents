#!/usr/bin/env bash
# Starts Claude Code as the build-epic orchestrator with only what a run uses, then runs /build-epic.
# Usage: build-epic <epic or issue> [--parallel N] [--explore] [--into <branch>] ...
# Every argument goes to /build-epic as it is. install.sh links this file to ~/.agents/bin/build-epic when build-epic
# is installed for Claude. docs/build-epic-launcher.md has the measurements behind the choices below.
#   - No MCP servers: none from any config and no claude.ai connectors. The orchestrator never calls one.
#   - Only the built-in tools the orchestrator and its subagents call. A subagent gets its tools from the parent's set
#     minus its own disallowedTools, so the set covers both: Monitor and ToolSearch are the implementers'.
#   - Auto-compact at 40% of the context window (CLAUDE_AUTOCOMPACT_PCT_OVERRIDE, unless already set): the model cannot
#     compact itself, so this is what keeps the orchestrator's context small across tickets.
# User settings stay: permissions, hooks (guard-merge, telemetry-save) and the skills folder are read as usual.
set -euo pipefail

tools="Bash,Read,Write,Edit,Agent,SendMessage,TaskStop,AskUserQuestion,ToolSearch,Monitor"

export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE="${CLAUDE_AUTOCOMPACT_PCT_OVERRIDE:-40}"

command -v claude >/dev/null || { echo "build-epic: claude not on PATH" >&2; exit 127; }

# The prompt goes first: --mcp-config and --tools take every word after them up to the next option.
exec claude "/build-epic${*:+ $*}" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools "$tools"
