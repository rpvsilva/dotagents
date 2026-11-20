# 🔧 How it works

[← Back to README](../README.md)

`install.sh` puts this repo into each tool's config folders in one of two ways:

- **Link**: a symlink, used for files the tool only reads. Editing the repo changes the tool at once.
- **Merge**: used for config files the tool or other programs also write to, such as `~/.claude/settings.json`. `install.sh` merges a JSON snippet into the file (with [`scripts/merge-json.sh`](../scripts/merge-json.sh)) and keeps what's already there. Re-run `install.sh` after changing a snippet.

Before replacing a real file, `install.sh` moves it to `~/.dotagents-backup/<timestamp>/`. Running it twice changes nothing but re-creates the links.

```sh
./install.sh                    # every tool, every group, the maintainer's `personal` example included
./install.sh shared             # only ~/.agents/
./install.sh claude             # shared + Claude Code (likewise codex, cursor)
./install.sh pr-loop remote     # only these groups (or parts), on every tool
./install.sh claude safety-hooks
```

Words come in any order: one of `all`, `shared`, `claude`, `codex` or `cursor` picks the tools, and every other word is a **group** (`groups.json`) or a **part** (`scripts/parts.sh list`). An unknown word, or a second tool word, prints the usage and exits 1. A selection installs its parts, core, and the **closure**: every part they require, transitively (`scripts/parts.sh closure <words>`). Optional needs stay out. Installs add up: a later `install.sh epics` keeps what an earlier `install.sh remote` linked. A bare `./install.sh` installs every group, including the maintainer's `personal` example (global instructions, guardrails and plugins). The README quick start installs only the public groups, by naming them.

## Shared: `~/.agents/`

Every tool target installs this first. It's the one tool-neutral place the others point at. A selection links only its own parts here; core (`_shared`, `configure`, the hooks library, `settings`, `capped`) always comes.

| Path | Points at | Used for |
|---|---|---|
| `~/.agents/skills/<skill>` | `skills/<skill>/` | Skills. Codex reads them here directly |
| `~/.agents/hooks/<file>` | `scripts/hooks/<file>` | The one path every tool's hook config calls: a real folder of one link per file, `lib.sh` and `secret-patterns.txt` always and each installed hook's script, so its links show which hooks are installed. An older install's one link to the repo folder is replaced by per-file links to every hook, since the tools' configs may call any of them; a link to anywhere else is backed up. Every hook a tool's config calls is linked to this checkout's script, whatever installed it, and one this checkout lacks is named |
| `~/.agents/bin/settings` | `scripts/settings.sh` | Reading and writing Settings ([settings.md](settings.md)) |
| `~/.agents/bin/remote-agent` | `scripts/remote-agent.sh` | Remote subagents ([remote/README.md](../remote/README.md)) |
| `~/.agents/bin/build-epic` | `adapters/claude/build-epic/launch.sh` | Starting a build-epic run in Claude Code with only the tools it calls ([build-epic-launcher.md](build-epic-launcher.md)); linked when `build-epic` is installed for Claude |

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
./uninstall.sh --dry-run          # print what would change (`would ...` lines), change nothing
./uninstall.sh                    # everything; or one tool: uninstall.sh claude|codex|cursor
./uninstall.sh pr-loop            # only a group or part, keeping what other installed parts require
./uninstall.sh --purge            # also delete ~/.config/dotagents (secrets, Settings), ~/.agents/telemetry and ~/.agents/runs
```

It takes words in any order as `install.sh` does, but its tool words are `all`, `claude`, `codex` and `cursor`: `shared` is refused, since the shared `~/.agents/` links go only with `all`. A second tool word or an unknown word prints the usage and exits 1. It touches only what `install.sh` put there:
- **Links**: removes every link pointing into this repo (or, for Claude skills, into `~/.agents/skills/`), stale ones included. Your own links and files stay. A file `install.sh` moved aside is copied back from `~/.dotagents-backup/`.
- **Merged files**: removes the snippets' entries and any hook calling `~/.agents/hooks/`, and keeps the rest. Each file it edits is copied to `~/.dotagents-backup/<timestamp>-uninstall/` first.
- **MCP servers and Claude plugins** listed in this repo are removed; their marketplaces stay.
- A single tool keeps the shared `~/.agents/` links for the others. `~/.dotagents-backup/` is never deleted.

With a selection, it removes the selected parts (a skill takes its subagents) minus the closure of every installed part outside the selection, and never core. It reads what is installed from the links in place: skill, subagent, mod and hook links (under an older install's one link to the hooks folder, every hook reachable through it), `~/.agents/bin/remote-agent`, and the `global` links; there is no record file. So after `install.sh pr-loop epics`, `uninstall.sh pr-loop` removes `address-review` and `renovate-prs` but keeps `review-pr`, `ship-pr`, `fix-pr` and their subagents, which `epics` requires; it names each part it keeps. It removes each part's own links one by one, Claude's skill links before the shared ones, so no link is left dangling. From merged files it removes only the removed hooks' snippets and the items calling their scripts; other hooks, settings and your own entries stay. `--purge` applies to a full uninstall only.

Run it from the checkout the links point to: after moving the repo, links to the old path aren't recognised.

## Why skills and hooks are split this way

- **A skill never names a tool.** It says "start one subagent per unit", not a specific tool call, and gives a fallback when a capability may be missing. That keeps one copy working everywhere.
- **Hook logic is a plain script.** Each adapter only wires it up, so any tool with hooks reuses the same script.
- **Knowledge about one repo stays in that repo**, in `.agents/<skill>-notes.md` (Lessons) and `.agents/settings.json` (Settings). Nothing about a specific project lives here.
