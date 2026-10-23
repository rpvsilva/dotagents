# Claude Code adapter

- `agents/*.md`: subagent definitions, linked into `~/.claude/agents/` by `install.sh`.
- `hooks/*.json`: hook config snippets. Each one calls a script in `../../scripts/hooks/`. Merge them into the `hooks` key of `~/.claude/settings.json` (not linked, because that file also holds machine-local settings).
- `telemetry/extract.py`: reads Claude Code session logs (`~/.claude/projects/`) into the neutral run JSON the `telemetry` skill reports on. Not linked; the skill reaches it through the repo. Tests: `python3 -m unittest test_extract` in that folder.
