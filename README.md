# dotagents

My personal AI setup: skills, instructions, MCP servers and hooks, written once and linked into every AI coding tool.

## Layout

```
AGENTS.md           how to work on this repo: goal, principles, contributing
global/AGENTS.md    my global instructions, linked into every tool
skills/             tool-neutral skills (SKILL.md format)
mcp/servers.json    MCP servers, one list for every tool
scripts/hooks/      hook logic as plain scripts, wired up per tool
adapters/<tool>/    tool-only pieces: per-tool agent wrappers, hook config
install.sh          symlinks everything into place
tests/              checks CI runs; run them locally with tests/run.sh
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
| `fix-pr` | Make one PR's failing checks pass and push; also the brief for the `pr-fixer` subagent |
| `renovate-prs` | Fix, verify and merge open Renovate PRs, one isolated subagent per PR |
| `review-pr` | First-pass PR review by a fresh `pr-reviewer` subagent; verified findings posted as a comment-only review |
| `ship-pr` | Take the current changes to a merged PR, following the repo's own branch, commit, check and merge rules |
| `telemetry` | Show what a finished run cost, one row per skill (time, turns, tool calls, tokens, cost); save it as JSON |
