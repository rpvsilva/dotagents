# 🪝 Hooks

[← Back to README](../README.md)

A **hook** is a script the tool runs around an agent's action, such as before a shell command or when a session ends. It can block the action, approve it without asking, or just do something on the side.

Each hook's logic is one script in [`scripts/hooks/`](../scripts/hooks/). Each tool wires it up with a small JSON snippet in `adapters/<tool>/hooks/`, calling it as `~/.agents/hooks/<name>.sh`. Shared code is in `scripts/hooks/lib.sh`. Each script's header comment gives its exact rules.

These are guardrails against mistakes, not a security boundary: you can still run anything yourself.

## `guard-merge`

**Blocks** these commands:
- `gh pr merge`
- merging through `gh api`
- approving a PR (`gh pr review`, or `gh api` with `APPROVE`)
- pushing to `main` or `master`

**Where:** only in repos with a `.review-gate.yml` at their root. In those repos, `scripts/merge-pr.sh` is the only way for an agent to merge.

**Why:** GitHub enforces the `main` ruleset that `scripts/protect-main.sh` applies: a PR, green CI, and a code owner's review on high-tier paths. `scripts/merge-pr.sh` adds the `agent-pr-gate` judgement, pinned to the head commit, and the tier rules. This hook stops an agent from merging directly, approving as the maintainer or pushing to `main`: an agent approving with the maintainer's login would fake their approval.

**Tools:** Claude Code, Codex, Cursor.

## `guard-secrets`

**Blocks:**
- reading or naming a secret file, through a file tool or in a shell command
- `git commit` when it adds a secret file, or a line that looks like a token or private key

Secret files are `.env*`, `*.pem`, `*.key`, SSH private keys, `.netrc`, `.aws/credentials`, and anything under `secrets/`. Examples such as `.env.example` pass. When it blocks a commit, the reason names the file, never the secret.

**Where:** every repo. The patterns are in `scripts/hooks/secret-patterns.txt`.

**Why:** keeps secrets out of the agent's context and out of git history.

**Tools:** Claude Code (shell, Read, Edit, Write, Grep), Codex (shell), Cursor (shell, file reads).

## `allow-task-cleanup`

**Approves without asking:** commands that only delete finished branches or remove their worktrees. A finished branch is one of:
- a task branch (`task/<ticket>/<NN>`, `fix/security-<name>/<NN>`)
- a branch whose tip is the head of a merged PR on GitHub
- a branch that no longer exists

Anything else gets no opinion, and the tool asks as usual.

**Why:** lets `build-epic` and `security-audit` clean up and keep running unattended, without risking unmerged work.

**Tools:** Claude Code only. Cursor's guard hooks already approve what they pass, and Codex hooks can't approve.

## `telemetry-save`

**Does:** when a session ends, saves the whole run (time, turns, tool calls, tokens, cost, each step) as `~/.agents/telemetry/<session id>.json`. The `telemetry` skill reports on these files.

It runs in the background, prints nothing and never fails the session. Turn it off with the `telemetry` Setting `auto_save` ([settings.md](settings.md)).

**Tools:** Claude Code only, since the other tools have no log extractor.

## Adding a hook

See "Add a hook" in [AGENTS.md](../AGENTS.md). In short: write the script, add a snippet per tool, list it in the README, and run `./install.sh`.
