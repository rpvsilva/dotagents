# dotagents

Rui's personal AI setup, written once and linked into every AI coding tool (Claude Code, Codex, Cursor, ...). The goal: one **portable** source of truth for instructions, skills, MCP servers and hooks, so switching tools or machines costs nothing.

This file governs work **on this repo**. `global/AGENTS.md` is a different thing: the personal instructions that `install.sh` links into every tool's global config.

## Principles

- **Portable first.** A skill says *what* to do, never which tool does it: "start one subagent per unit, each in its own worktree", not a specific tool call. Give a fallback when a capability may be missing ("if your harness has no subagents, work the units one by one"). Tool-only frontmatter (e.g. Claude's `disable-model-invocation`) is fine: other tools ignore it.
- **Adapters hold the rest.** Behaviour only one tool supports (subagent definitions, hook wiring, plugin manifests) lives in `adapters/<tool>/`, never inside a skill.
- **Hook logic is a script.** It lives in `scripts/hooks/`; each adapter only wires it up, so any tool with hooks reuses the same script.
- **Repo knowledge stays in its repo.** A skill reads per-repo notes from `.agents/<skill>-notes.md` in the target repo; nothing about a specific project lives here.
- **No secrets.** Keys and tokens come from env vars or untracked local files (`.gitignore` covers `*.local.*`, `.env*`, `secrets/`).

Write every skill and instruction file following the `writing-for-agents` skill when it is available.

## Contributing

### Add or change a skill

1. Create `skills/<name>/SKILL.md` (frontmatter `name` matching the folder, plus `description`). Supporting files sit beside it and are reached by relative links.
2. Re-read it against the **portable first** principle: every tool-specific word replaced or given a fallback.
3. Run `./install.sh` and confirm `~/.agents/skills/<name>` and `~/.claude/skills/<name>` are links into this repo.
4. Add a row to the Skills table in `README.md`.

Done when the skill loads in Claude Code by name and the README row exists.

### Wire up a tool

1. Put tool-only files in `adapters/<tool>/` with a short `README.md` saying where they get linked.
2. Add an `install_<tool>` function and a `case` entry in `install.sh`, linking through the `link` helper so existing files get backed up to `~/.dotagents-backup/`.
3. Run `bash -n install.sh`, then `./install.sh <tool>` twice: the second run must change nothing but re-create links.

### Add a hook or MCP server

- **Hook**: the script in `scripts/hooks/<name>.sh`, then the wiring per tool in `adapters/<tool>/hooks/`.
- **MCP server**: add it to `mcp/servers.json` with credentials as `${ENV_VAR}` references only.

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`), scoped by area when useful: `feat(skills): add renovate-prs`.
