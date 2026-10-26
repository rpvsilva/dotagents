# Cursor adapter

- `agents/*.md`: subagent definitions, linked into `~/.cursor/agents/` by `install.sh` (skipped when `~/.cursor` is missing). Each one only points at a brief in `~/.agents/skills/`. Cursor also reads `~/.claude/agents/`, but files in `~/.cursor/agents/` win, so the Cursor copy can use Cursor-only frontmatter (`model: inherit`, `readonly`).
- Not wired yet: `global/AGENTS.md` and skills.
