# 🔧 How it works

[← Back to the docs](README.md)

> **TL;DR** `install.sh` links this repo into each tool's config folders. Edit the repo, and every tool sees it at once. `uninstall.sh` undoes exactly what `install.sh` did, and nothing else.

## 🔗 Two ways in: link or merge

`install.sh` puts the repo into each tool in one of two ways:

- **Link** (a symlink) for files the tool only reads. The repo *is* the config, so editing a skill changes the tool right away.
- **Merge** for config files that the tool or other programs also write to, like `~/.claude/settings.json`. `install.sh` merges a JSON snippet into the file with [`scripts/merge-json.sh`](../scripts/merge-json.sh) and keeps everything already there. Change a snippet? Re-run `install.sh`.

Nothing gets clobbered. Before replacing a real file, `install.sh` moves it to `~/.dotagents-backup/<timestamp>/`. Running it twice changes nothing but re-creating the links.

## 🧩 Pick what to install

```sh
./install.sh                    # every tool, every group, the maintainer's `personal` example included
./install.sh shared             # only ~/.agents/
./install.sh claude             # shared + Claude Code (likewise codex, cursor)
./install.sh pr-loop remote     # only these groups (or parts), on every tool
./install.sh claude safety-hooks
```

How the words work:
- Order doesn't matter.
- **Tool words** (`all`, `shared`, `claude`, `codex`, `cursor`) pick the tools. One at most.
- **Every other word** is a **group** (from `groups.json`) or a **part** (see `scripts/parts.sh list`).
- An unknown word, or a second tool word, prints the usage and exits 1.

A selection brings along what it needs:
- **Core** always comes (`_shared`, `configure`, the hooks library, `settings`, `capped`).
- **Required needs** come too, all the way down. `scripts/parts.sh closure <words>` shows the full list.
- **Optional needs** stay out unless you name them.

Installs add up. A later `install.sh epics` keeps what an earlier `install.sh remote` linked.

> 💡 A bare `./install.sh` also installs `personal`: the maintainer's own instructions, guardrails and plugins, kept as an example. The quick start in the main README names only the public groups, so it leaves `personal` out.

## 🏠 Home base: `~/.agents/`

Every tool target installs this first. It's the one tool-neutral place the others point at. A selection links only its own parts here, plus core.

| Path | Points at | Used for |
|---|---|---|
| `~/.agents/skills/<skill>` | `skills/<skill>/` | Skills. Codex reads them right here |
| `~/.agents/hooks/<file>` | `scripts/hooks/<file>` | The one path every tool's hook config calls (more below) |
| `~/.agents/bin/settings` | `scripts/settings.sh` | Reading and writing Settings ([settings.md](settings.md)) |
| `~/.agents/bin/remote-agent` | `scripts/remote-agent.sh` | Remote subagents ([remote.md](remote.md)) |
| `~/.agents/bin/build-epic` | `adapters/claude/build-epic/launch.sh` | Starting a lean build-epic run in Claude Code ([build-epic-launcher.md](build-epic-launcher.md)). Linked when `build-epic` is installed for Claude |

About `~/.agents/hooks/`: it's a real folder holding one link per file. `lib.sh` and `secret-patterns.txt` are always there, plus each installed hook's script, so a glance at the folder shows which hooks you have.
- An older install's single link to the whole repo folder is replaced by per-file links to every hook, since the tools' configs may call any of them.
- A link pointing anywhere else is backed up first.
- Every hook a tool's config calls gets linked to this checkout's script, whatever installed it. One this checkout lacks is named.

## 🧰 Per tool

**Adapters** hold the tool-only pieces: subagent wrappers, hook wiring and rules. They live in `adapters/<tool>/`, and each adapter's README says exactly where its files go.

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

Things that might surprise you:
- Codex and Cursor are skipped when `~/.codex` or `~/.cursor` doesn't exist.
- Codex ignores new or changed hooks until you trust them with `/hooks` in Codex.
- Claude plugins and MCP servers are skipped when `claude` isn't on `PATH`, or when `DOTAGENTS_SKIP_PLUGINS` is set.
- MCP credentials come from `~/.config/dotagents/mcp.env` (`KEY=value` lines). A server whose variable is unset is skipped, and `install.sh` names it.

<a id="uninstall"></a>

## 🧹 Uninstall

```sh
./uninstall.sh --dry-run          # print what would change (`would ...` lines), change nothing
./uninstall.sh                    # everything; or one tool: uninstall.sh claude|codex|cursor
./uninstall.sh pr-loop            # only a group or part, keeping what other installed parts require
./uninstall.sh --purge            # also delete ~/.config/dotagents (secrets, Settings), ~/.agents/telemetry and ~/.agents/runs
```

> 💡 Start with `--dry-run`. It's free and it shows you everything first.

Words work as they do for `install.sh`, except the tool words are `all`, `claude`, `codex` and `cursor`. `shared` is refused, since the shared `~/.agents/` links only go with `all`. `--purge` applies to a full uninstall only.

It touches only what `install.sh` put there:
- **Links:** removes every link into this repo (or, for Claude skills, into `~/.agents/skills/`), stale ones included. Your own links and files stay. A file `install.sh` moved aside is copied back from `~/.dotagents-backup/`.
- **Merged files:** removes the snippets' entries and any hook calling `~/.agents/hooks/`, and keeps the rest. Each file is copied to `~/.dotagents-backup/<timestamp>-uninstall/` before it's edited.
- **MCP servers and Claude plugins** listed in this repo are removed. Their marketplaces stay.
- **One tool alone** keeps the shared `~/.agents/` links for the others.
- `~/.dotagents-backup/` is never deleted.

### Removing one group

Uninstalling a selection is smart about shared needs. It removes the selected parts (a skill takes its subagents with it), minus anything another installed part still requires, and never core.

Example: after `install.sh pr-loop epics`, running `uninstall.sh pr-loop` removes `address-review` and `renovate-prs`. It keeps `review-pr`, `ship-pr`, `fix-pr` and their subagents, because `epics` needs them, and it names each part it keeps.

How it knows what's installed: there's no record file. It reads the links in place (skill, subagent, mod and hook links, `~/.agents/bin/remote-agent` and the `global` links). Under an older install's single hooks-folder link, every hook reachable through it counts. It removes each part's links one by one, Claude's skill links before the shared ones, so no link is left dangling. From merged files it removes only the removed hooks' snippets and the items calling their scripts. Other hooks, settings and your own entries stay.

> ⚠️ Run it from the checkout the links point to. After moving the repo, links to the old path aren't recognised.

## 🤔 Why it's split this way

- **A skill never names a tool.** It says "start one subagent per unit", not a specific tool call, and gives a fallback when a capability may be missing. One copy works everywhere.
- **Hook logic is a plain script.** Each adapter only wires it up, so every tool with hooks reuses the same script.
- **What's known about a repo stays in that repo**, in `.agents/<skill>-notes.md` (Lessons) and `.agents/settings.json` (Settings). Nothing about a specific project lives here.
