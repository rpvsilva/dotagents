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

1. Create `skills/<name>/SKILL.md` (frontmatter `name` matching the folder, plus `description`). Supporting files sit beside it and are reached by relative links. Steps several skills share (learning the repo's rules, starting subagents) live once in `skills/_shared/conventions.md`; link to its section instead of restating it. Folders starting with `_` hold shared files, not skills.
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

### Add a hook, Claude plugin or MCP server

- **Hook**: the script in `scripts/hooks/<name>.sh`, then the wiring per tool in `adapters/<tool>/hooks/`, calling it as `$HOME/.agents/hooks/<name>.sh` (`install.sh` links that folder). `install.sh` merges each snippet into the tool's config with `scripts/merge-json.sh`, keeping what is already there.
- **Claude plugin**: add a line to `adapters/claude/plugins.txt`; `install.sh claude` installs it.
- **MCP server**: add it to `mcp/servers.json` with credentials as `${ENV_VAR}` references only, and the values in `~/.config/dotagents/mcp.env` (`KEY=value` lines, never committed). `install.sh` fills them in and installs the server for Claude Code (`claude mcp`), Codex (`~/.codex/config.toml`) and Cursor (`~/.cursor/mcp.json`); a server with an unset variable is skipped and named.

### Checks

Run `tests/run.sh` before pushing. It runs what CI (`.github/workflows/ci.yml`) runs: shellcheck at the version pinned in `scripts/shellcheck-version` (the local one when it matches, else the `koalaman/shellcheck` image through docker; another version fails, and with neither it is skipped with a warning; to bump it, edit that file and the remote runner image), actionlint, every `tests/*.test.sh`: `install.test.sh` (install.sh in a throwaway `$HOME`, run twice), `python.test.sh` (each folder's `test_*.py`), `merge-pr.test.sh` (`scripts/merge-pr.sh` against a stub `gh` and `npx`), `gh-app-token.test.sh` (`scripts/gh-app-token.sh` against a stub GitHub API), `gh-runner.test.sh` (`scripts/gh-runner.sh` against a stub GitHub API and a stub `docker`), `guard-merge.test.sh` (`scripts/hooks/guard-merge.sh` in throwaway repos), `guard-secrets.test.sh` (`scripts/hooks/guard-secrets.sh` on paths, commands and throwaway repos), `merge-json.test.sh` (`scripts/merge-json.sh` on throwaway configs), `claude-plugins.test.sh` (`scripts/claude-plugins.sh` against a stub `claude`), `mcp-servers.test.sh` (`scripts/mcp-servers.sh` against a stub `claude` and throwaway configs), `remote-agent.test.sh` (`scripts/remote-agent-pack.sh` on a throwaway `$HOME`, `scripts/remote-agent.sh` against a stub `kubectl`), `remote-backends.test.sh` (`scripts/remote-agent.sh` on the `docker` and `ssh` backends against a stub `docker` and `ssh`), `remote-agent-setup.test.sh` (`remote-agent setup` with answers on stdin in a throwaway config folder), `remote-agent-check.test.sh` (`remote-agent check` against the same stubs and a stub `kubectl`) and `repo.test.sh` (the rules above: skill frontmatter, README rows, relative links, hook snippets calling a script that exists, `${ENV_VAR}`-only MCP credentials, executable scripts, high tier matching CODEOWNERS, `CLAUDE.md` committed as a symlink to `AGENTS.md`). When you add a rule to this file, add its check to `tests/repo.test.sh`.

### Pull requests

`agent-pr-gate` decides what a PR needs before it may merge; its rules are in `.review-gate.yml`.

- Fill in `Testing`, `Risk` and `How to revert` from `.github/pull_request_template.md`. Under `Testing`, write what you ran and its result; `TODO` or `N/A` counts as empty.
- Open, update and merge PRs as the `rpvsilva-agent` GitHub App, not as Rui, so Rui can approve them: prefix each `gh` call and `scripts/merge-pr.sh` with `GH_TOKEN="$(scripts/gh-app-token.sh)"`. Setup: the App ID in `~/.config/dotagents/github-app.env` (`DOTAGENTS_APP_ID=...`) and its private key at `~/.config/dotagents/github-app.pem`. If either is missing, stop and ask Rui; never fall back to his account.
- Never approve a PR. Approval is Rui's, and an agent approving with his login would fake it.
- A PR touching a **high-tier** path (`.review-gate.yml`, mirrored in `CODEOWNERS`) needs Rui's approval. To make a path high-tier, add it to both files.
- Merge only with `scripts/merge-pr.sh <number>`. It runs `agent-pr-gate check` (the gate's read-only CLI, pinned to the commit the gate workflow uses; needs `node`), which waits for CI and judges the PR by `.review-gate.yml`, and merges only when it passes, pinned to the head commit it judged. A PR into another branch (a `build-epic` task PR into its ticket branch) merges without the gate: run `tests/run.sh` on it first. CI and the gate run on PRs into `main` only, to save runners: Linux always (`ci.yml`), Windows when shell code changes (`portability.yml`); macOS is `tests/run.sh` on your machine. Never merge with `gh pr merge` or the web UI, and never push to `main`: the repo is private on GitHub Free, so GitHub enforces nothing and this script is the only gate. The `guard-merge` hook (`scripts/hooks/guard-merge.sh`, wired for Claude Code, Codex and Cursor) blocks those commands.

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`), scoped by area when useful: `feat(skills): add renovate-prs`.
