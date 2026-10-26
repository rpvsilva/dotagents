# Cursor adapter

- `agents/*.md`: subagent definitions, linked into `~/.cursor/agents/` by `install.sh` (skipped when `~/.cursor` is missing). Each one only points at a brief in `~/.agents/skills/`. Cursor also reads `~/.claude/agents/`, but files in `~/.cursor/agents/` win, so the Cursor copy can use Cursor-only frontmatter (`model: inherit`, `readonly`).
- `hooks/*.json`: hook config snippets. Each one calls a script in `~/.agents/hooks/` and prints `{"permission":"allow"}` when it passes, since Cursor blocks on missing JSON. Merge them into `~/.cursor/hooks.json` by hand (not linked, because other tools write to it).
- No twin of Claude's `agent-identity.json`: Cursor's deny list matches only a command's first word, so it cannot deny `gh pr review` without denying all of `gh`.
- Not wired yet: `global/AGENTS.md` and skills.
