# 🔧 How it works

[← Back to README](../README.md)

`install.sh` puts this repo into each tool's config folders in one of two ways:

- **Link**: a symlink, used for files the tool only reads. Editing the repo changes the tool at once.
- **Merge**: used for config files the tool or other programs also write to, such as `~/.claude/settings.json`. `install.sh` merges a JSON snippet into the file (with [`scripts/merge-json.sh`](../scripts/merge-json.sh)) and keeps what's already there. Re-run `install.sh` after changing a snippet.

Before replacing a real file, `install.sh` moves it to `~/.dotagents-backup/<timestamp>/`. Running it twice changes nothing but re-creates the links.

```sh
./install.sh            # everything
./install.sh shared     # only ~/.agents/
./install.sh claude     # shared + Claude Code (likewise codex, cursor)
```

## Shared: `~/.agents/`

Every tool target installs this first. It's the one tool-neutral place the others point at.

| Path | Points at | Used for |
|---|---|---|
| `~/.agents/skills/<skill>` | `skills/<skill>/` | Skills. Codex reads them here directly |
| `~/.agents/hooks/` | `scripts/hooks/` | The one path every tool's hook config calls |
| `~/.agents/bin/settings` | `scripts/settings.sh` | Reading and writing Settings ([settings.md](settings.md)) |
| `~/.agents/bin/remote-agent` | `scripts/remote-agent.sh` | Remote subagents ([remote/README.md](../remote/README.md)) |

## Per tool

**Adapters** are the tool-only pieces: subagent wrappers, hook wiring and rules. They live in `adapters/<tool>/`, and each adapter's README says exactly where its files go.

| What | Claude Code | Codex | Cursor |
|---|---|---|---|
| Global instructions (`global/AGENTS.md`) | link: `~/.claude/CLAUDE.md` | link: `~/.codex/AGENTS.md` | not wired yet |
| Skills | link: `~/.claude/skills/<skill>` | reads `~/.agents/skills` | not wired yet |
| Subagents | link: `~/.claude/agents/*.md` | link: `~/.codex/agents/*.toml` | link: `~/.cursor/agents/*.md` |
| Hooks | merge: `~/.claude/settings.json` | merge: `~/.codex/hooks.json` | merge: `~/.cursor/hooks.json` |
| Guardrails | merge: `~/.claude/settings.json` | link: `~/.codex/rules/` | none (see adapter) |
| MCP servers | `claude mcp add-json` | marked block in `~/.codex/config.toml` | merge: `~/.cursor/mcp.json` |
| Plugins and mods | `claude plugin install`; mods linked into `~/.claude/skills/` | none | none |

Adapter details: [Claude Code](../adapters/claude/README.md), [Codex](../adapters/codex/README.md), [Cursor](../adapters/cursor/README.md).

Notes:
- Codex and Cursor are skipped when `~/.codex` or `~/.cursor` doesn't exist.
- Codex skips new or changed hooks until you trust them with `/hooks` in Codex.
- Claude plugins and MCP servers are skipped when `claude` isn't on `PATH`, or when `DOTAGENTS_SKIP_PLUGINS` is set.
- MCP credentials come from `~/.config/dotagents/mcp.env` (`KEY=value` lines). A server whose variable is unset is skipped, and `install.sh` names it.

## Uninstall

```sh
./uninstall.sh --dry-run   # print what would change, change nothing
./uninstall.sh             # everything; or one tool: uninstall.sh claude|codex|cursor
./uninstall.sh --purge     # also delete ~/.config/dotagents (secrets, Settings), ~/.agents/telemetry and ~/.agents/runs
```

It touches only what `install.sh` put there:
- **Links**: removes every link pointing into this repo (or, for Claude skills, into `~/.agents/skills/`), stale ones included. Your own links and files stay. A file `install.sh` moved aside is copied back from `~/.dotagents-backup/`.
- **Merged files**: removes the snippets' entries and any hook calling `~/.agents/hooks/`, and keeps the rest. Each file it edits is copied to `~/.dotagents-backup/<timestamp>-uninstall/` first.
- **MCP servers and Claude plugins** listed in this repo are removed; their marketplaces stay.
- A single tool keeps the shared `~/.agents/` links for the others. `~/.dotagents-backup/` is never deleted.

Run it from the checkout the links point to: after moving the repo, links to the old path aren't recognised.

## Why skills and hooks are split this way

- **A skill never names a tool.** It says "start one subagent per unit", not a specific tool call, and gives a fallback when a capability may be missing. That keeps one copy working everywhere.
- **Hook logic is a plain script.** Each adapter only wires it up, so any tool with hooks reuses the same script.
- **Knowledge about one repo stays in that repo**, in `.agents/<skill>-notes.md` (Lessons) and `.agents/settings.json` (Settings). Nothing about a specific project lives here.
