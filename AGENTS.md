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

### Add a subagent

1. Write its instructions as a portable brief next to the skill that starts it: `skills/<skill>/<name>-brief.md` or similar. The skill tells the subagent to follow that file and says what to use when no matching agent exists.
2. For each tool with agent definitions, add a thin wrapper in `adapters/<tool>/agents/`: frontmatter for that tool (model, tools, isolation) and a body that only points at `~/.agents/skills/<skill>/<file>`. Never copy the brief into a wrapper.
3. Run `./install.sh` and confirm each wrapper is linked into the tool's agents folder.

### Wire up a tool

1. Put tool-only files in `adapters/<tool>/` with a short `README.md` saying where they get linked.
2. Add an `install_<tool>` function and a `case` entry in `install.sh`, linking through the `link` helper so existing files get backed up to `~/.dotagents-backup/`.
3. Run `bash -n install.sh`, then `./install.sh <tool>` twice: the second run must change nothing but re-create links.

### Add a hook or MCP server

- **Hook**: the script in `scripts/hooks/<name>.sh`, then the wiring per tool in `adapters/<tool>/hooks/`.
- **MCP server**: add it to `mcp/servers.json` with credentials as `${ENV_VAR}` references only.

### Checks

Run `tests/run.sh` before pushing. It runs what CI (`.github/workflows/ci.yml`) runs: shellcheck, actionlint, every `tests/*.test.sh`: `install.test.sh` (install.sh in a throwaway `$HOME`, run twice), `python.test.sh` (each folder's `test_*.py`), `merge-pr.test.sh` (`scripts/merge-pr.sh` against a stub `gh`), `gh-app-token.test.sh` (`scripts/gh-app-token.sh` against a stub GitHub API) and `repo.test.sh` (the rules above: skill frontmatter, README rows, relative links, `${ENV_VAR}`-only MCP credentials, executable scripts, high tier matching CODEOWNERS). When you add a rule to this file, add its check to `tests/repo.test.sh`.

### Pull requests

`agent-pr-gate` decides what a PR needs before it may merge; its rules are in `.review-gate.yml`.

- Fill in `Testing`, `Risk` and `How to revert` from `.github/pull_request_template.md`. Under `Testing`, write what you ran and its result; `TODO` or `N/A` counts as empty.
- Open, update and merge PRs as the `rpvsilva-agent` GitHub App, not as Rui, so Rui can approve them: prefix each `gh` call and `scripts/merge-pr.sh` with `GH_TOKEN="$(scripts/gh-app-token.sh)"`. Setup: the App ID in `~/.config/dotagents/github-app.env` (`DOTAGENTS_APP_ID=...`) and its private key at `~/.config/dotagents/github-app.pem`. If either is missing, stop and ask Rui; never fall back to his account.
- Never approve a PR. Approval is Rui's, and an agent approving with his login would fake it.
- A PR touching a **high-tier** path (`.review-gate.yml`, mirrored in `CODEOWNERS`) needs Rui's approval. To make a path high-tier, add it to both files.
- Merge only with `scripts/merge-pr.sh <number>`. It merges only when `ci-ok` and `agent-pr-gate` are green on the PR's current head commit. Never merge with `gh pr merge` or the web UI, and never push to `main`: the repo is private on GitHub Free, so GitHub enforces nothing and this script is the only gate.

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`), scoped by area when useful: `feat(skills): add renovate-prs`.
