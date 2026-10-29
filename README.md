# dotagents

My personal AI setup: skills, instructions, MCP servers and hooks, written once and linked into every AI coding tool.

## Layout

```
AGENTS.md           how to work on this repo: goal, principles, contributing
global/AGENTS.md    my global instructions, linked into every tool
skills/             tool-neutral skills (SKILL.md format); skills/_shared/ holds steps they share
mcp/servers.json    MCP servers, one list for every tool; secrets in ~/.config/dotagents/mcp.env
scripts/hooks/      hook logic as plain scripts, wired up per tool
adapters/<tool>/    tool-only pieces: per-tool agent wrappers, hook config
remote/             running subagents on a cluster for compute only; see remote/README.md
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
| `add-plugin` | Add a Claude Code plugin: show what it adds and costs, then list it in `plugins.txt`, install it and ship the change |
| `address-review` | Work through a PR's open review threads: verify each claim, fix what holds up test-first, reply to the rest; pushback and design calls go to Rui first |
| `build-epic` | Take an epic or issue from the tracker to one reviewed PR: planner, explorer and implementer subagents, one human approval of the plan |
| `fix-pr` | Make one PR's failing checks pass and push; also the brief for the `pr-fixer` subagent |
| `remember` | After a skill run, save what it learned about the repo into `.agents/<skill>-notes.md`, after Rui sees the diff; repo-wide or skill-wide lessons become proposals |
| `renovate-prs` | Fix, verify and merge open Renovate PRs, one isolated subagent per PR |
| `review-pr` | First-pass PR review by fresh `pr-reviewer` and `security-reviewer` subagents; verified findings posted as a comment-only review |
| `security-audit` | Security-audit a repo of any size with subagents only: a `security-mapper` splits it into areas and flows, one `security-reviewer` per piece, gap sweeps until nothing new; fix the picked findings in a PR |
| `ship-pr` | Take the current changes to a merged PR, following the repo's own branch, commit, check and merge rules |
| `telemetry` | Show what a finished run cost, one row per skill (time, turns, tool calls, tokens, cost); save it as JSON |
| `to-epic` | Turn meeting notes, docs or any source into an epic and its issues on the tracker, after one human approval of the draft |
