# dotagents

My personal AI setup: skills, instructions, MCP servers and hooks, written once and linked into every AI coding tool.

## Layout

```
AGENTS.md           how to work on this repo: goal, principles, contributing
global/AGENTS.md    my global instructions, linked into every tool
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

## Contributing

Principles and how to add a skill, tool, hook or MCP server: see [AGENTS.md](AGENTS.md).

## Skills

| Skill | What it does |
|---|---|
| `renovate-prs` | Fix, verify and merge open Renovate PRs, one isolated subagent per PR |
