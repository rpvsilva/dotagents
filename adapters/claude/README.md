# Claude Code adapter

- `agents/*.md`: subagent definitions, linked into `~/.claude/agents/` by `install.sh`.
- `hooks/*.json`: hook config snippets. Each one calls a script in `../../scripts/hooks/`. Merge them into the `hooks` key of `~/.claude/settings.json` (not linked, because that file also holds machine-local settings).
- `settings/agent-identity.json`: denies `gh pr review` and `gh auth token`, so Claude can't approve PRs or borrow Rui's GitHub login (see Pull requests in `AGENTS.md`). A guardrail against mistakes, not a security boundary. Merge its `permissions.deny` into `~/.claude/settings.json` by hand, like the hooks.
- `telemetry/extract.py`: reads Claude Code session logs (`~/.claude/projects/`) into the neutral run JSON the `telemetry` skill reports on. Not linked; the skill reaches it through the repo. Tests: `python3 -m unittest test_extract` in that folder.
