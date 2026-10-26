# Claude Code adapter

- `agents/*.md`: subagent definitions, linked into `~/.claude/agents/` by `install.sh`. Each one only points at a brief in `~/.agents/skills/`.
- `hooks/*.json`: hook config snippets. Each one calls a script in `~/.agents/hooks/` (linked to `scripts/hooks/` by `install.sh`). `guard-merge.json` blocks `gh pr merge`, PR approvals and pushes to main in repos with a `.review-gate.yml`. Merge them into the `hooks` key of `~/.claude/settings.json` (not linked, because that file also holds machine-local settings).
- `settings/agent-identity.json`: denies `gh pr review` and `gh auth token`, so Claude can't approve PRs or borrow Rui's GitHub login (see Pull requests in `AGENTS.md`). A guardrail against mistakes, not a security boundary. Merge its `permissions.deny` into `~/.claude/settings.json` by hand, like the hooks.
- `telemetry/extract.py`: reads Claude Code session logs (`~/.claude/projects/`) into the neutral run JSON the `telemetry` skill reports on. Not linked; the skill reaches it through the repo. Tests: `python3 -m unittest test_extract` in that folder.
