# dotagents

My personal AI setup: skills, instructions, MCP servers and hooks, written once and linked into every AI coding tool.

## Layout

```
AGENTS.md           global instructions (Claude reads it as ~/.claude/CLAUDE.md)
skills/             tool-neutral skills (SKILL.md format)
mcp/servers.json    MCP servers, one list for every tool
scripts/hooks/      hook logic as plain scripts, wired up per tool
adapters/<tool>/    tool-only pieces: Claude agents and hook config, Codex, Cursor
install.sh          symlinks everything into place
```

## Install

```sh
git clone git@github.com:rpvsilva/dotagents.git ~/Desktop/Projects/dotagents
~/Desktop/Projects/dotagents/install.sh          # or: install.sh claude
```

Everything is symlinked, so editing the repo updates every tool at once. Re-run `install.sh` after adding a skill. Anything it replaces is moved to `~/.dotagents-backup/`.

## Rules

- **Skills say what to do, not which tool does it.** "Start one subagent per PR in its own worktree", not a specific tool call. Tool-only frontmatter (e.g. Claude's `disable-model-invocation`) is fine: other tools ignore it.
- **Tool-only behaviour lives in `adapters/<tool>/`**, never inside a skill.
- **Hook logic lives in `scripts/hooks/`**; adapters only wire it up, so any tool with hooks reuses the same script.
- **Repo-specific knowledge lives in that repo**, e.g. `.agents/renovate-notes.md`, which a skill reads when present.
- **No secrets.** API keys and tokens go in env vars or untracked local files (see `.gitignore`).

## Skills

| Skill | What it does |
|---|---|
| `renovate-prs` | Fix, verify and merge open Renovate PRs, one isolated subagent per PR |
