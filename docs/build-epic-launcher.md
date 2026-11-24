# 🚀 build-epic launcher (Claude Code)

[← Back to the docs](README.md)

> **TL;DR** A long `build-epic` run pays for every tool definition on every turn. `~/.agents/bin/build-epic` starts Claude Code with only the tools the run uses and no MCP servers, which cuts the first request from about 27k tokens to about 9k.

The build-epic orchestrator is the main Claude Code session. Before its first turn it carries a fixed base: the system prompt, every tool's definition (built-ins, MCP servers, claude.ai connectors), the skill listing and the CLAUDE.md files. It pays for that base on every turn of a long run. A skill cannot drop tools from a session already running, so `~/.agents/bin/build-epic` starts the session without what the orchestrator never calls, then runs `/build-epic`.

## ▶️ Start a run

```sh
~/.agents/bin/build-epic '#402' --into baseline-402 --parallel 3
```

Every argument goes to `/build-epic` unchanged, so it takes what the skill takes. To resume a run, run the same command again: the skill resumes from the run folder. `claude --resume` would bring every tool back.

It runs, with `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=40` and `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` unless you set your own:

```sh
claude "/build-epic <arguments>" --strict-mcp-config --mcp-config '{"mcpServers":{}}' \
  --tools "Bash,Read,Write,Edit,Agent,SendMessage,TaskStop,AskUserQuestion,ToolSearch,Monitor"
```

- **No MCP servers**: none from any config and no claude.ai connectors.
- **Only the tools a run calls.** A subagent's tools are the parent's set minus its own `disallowedTools`, so the set covers the subagents too: `Monitor` and `ToolSearch` are the implementers'. A tool left out is gone for every subagent of the run.
- **Auto-compact at about 80k tokens**: 40% of a 200k compact window. The skill says to compact after each shipped ticket, but the model cannot run `/compact`. Opus 5.5 has a 1M-token window, for which Claude Code's compact window is 600k, so by default it compacts near 570k, and the percentage alone moves that only to 240k. The first two step D replays of #402 never compacted: the orchestrator reached 205k after three tickets, and 162k after all five. Claude Code compacts when a turn starts, a subagent's notification included, so the threshold is crossed at most one turn's worth.
- **User settings stay**: permissions, hooks (`guard-merge`, `telemetry-save`) and the skills folder load as usual. The skill listing is dropped with the `Skill` tool; `/build-epic` still expands.

The launcher is Claude-only (`adapters/claude/build-epic/launch.sh`). `install.sh` links it when `build-epic` is installed for Claude.

## 📈 What a run actually calls

Tool calls in the 16 saved orchestrator sessions of build-epic runs in the sandbox (Nov 2026), main session only:

| Tool | Calls |
|---|---|
| Bash | 1814 |
| Agent | 490 |
| SendMessage | 74 |
| ToolSearch (to load `SendMessage` and `TaskStop`) | 9 |
| Write | 7 |
| AskUserQuestion | 6 |
| Read | 5 |
| Edit | 5 |
| TaskStop | 3 |

Their subagents called `Bash`, `Read`, `Write`, `Edit`, `Monitor`, `TaskStop` and `ToolSearch`. No session called an MCP tool or `Skill`.

## 📏 Measurements

Tokens of the first request (input, cache write and cache read) of `claude -p "reply ok"` from an empty folder, on 17 Nov 2026 with Claude Code 2.1.326, on this machine's config (2 MCP servers, 21 claude.ai connectors, 80 skills):

| Variant | Opus 5.5 | Haiku 4.5 |
|---|---|---|
| Plain | 27,013 | 25,895 |
| No MCP servers | 22,694 | 23,518 |
| Only the tools above | 13,126 | 15,770 |
| Launcher (both) | **8,845** | 13,421 |
| Launcher, plus `--disable-slash-commands` | | 13,419 |
| Launcher, plus `--setting-sources user` | | 13,419 |

- The tool list is the bigger cut: it drops about 20 unused built-ins and the `Skill` tool's listing of every skill.
- `--disable-slash-commands` saves nothing more once `Skill` is gone, and would disable `/build-epic`.
- No flag drops the CLAUDE.md files except `--bare`, which also drops the login. The global one is one line; the repo's own is the rules the run follows.
- An interactive session carries more than `-p`. The step C orchestrators (`gh-402-step-c-r1..r3`) started at about 62,500 tokens on their first request, the `/build-epic` skill text (about 12,000) included.
