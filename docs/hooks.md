# 🪝 Hooks

[← Back to the docs](README.md)

> **TL;DR** Hooks are small scripts the tool runs around your agent's actions. Two of them say "no", one says "yes, don't bother asking", and one quietly takes notes.

A **hook** runs at a moment like "before a shell command" or "when the session ends". It can **block** the action, **approve** it without asking you, or just **do something** on the side.

| Hook | Its job | Tools |
|---|---|---|
| 🛑 [`guard-merge`](#-guard-merge) | Stops agents from merging, approving or pushing to `main` | Claude Code, Codex, Cursor |
| 🔒 [`guard-secrets`](#-guard-secrets) | Keeps secrets out of the agent's eyes and out of git | Claude Code, Codex, Cursor |
| ✅ [`allow-task-cleanup`](#-allow-task-cleanup) | Approves deleting finished branches and worktrees | Claude Code |
| 📊 [`telemetry-save`](#-telemetry-save) | Saves each session's stats when it ends | Claude Code |

> ⚠️ These are guardrails against mistakes, not a security boundary. You can still run anything yourself.

How they're built: each hook's logic is one script in [`scripts/hooks/`](../scripts/hooks/), with shared code in `scripts/hooks/lib.sh`. Each tool wires it up with a small JSON snippet in `adapters/<tool>/hooks/`, calling it as `~/.agents/hooks/<name>.sh`. Each script's header comment gives its exact rules.

## 🛑 `guard-merge`

**Blocks:**
- `gh pr merge`
- merging through `gh api`
- approving a PR (`gh pr review`, or `gh api` with `APPROVE`)
- pushing to `main` or `master`

**Where:** only in repos with a `.review-gate.yml` at their root. There, `scripts/merge-pr.sh` is the only way for an agent to merge.

**Why:** GitHub enforces the `main` ruleset that `scripts/protect-main.sh` applies: a PR, green CI, and a code owner's review on high-tier paths. `scripts/merge-pr.sh` adds the `agent-pr-gate` judgement, pinned to the head commit, and the tier rules. This hook closes the side doors. An agent approving with the maintainer's login would fake the maintainer's approval.

## 🔒 `guard-secrets`

**Blocks:**
- reading or naming a secret file, through a file tool or in a shell command
- `git commit` when it adds a secret file, or a line that looks like a token or private key

Secret files are `.env*`, `*.pem`, `*.key`, SSH private keys, `.netrc`, `.aws/credentials`, and anything under `secrets/`. Examples such as `.env.example` pass. When it blocks a commit, the message names the file, never the secret.

**Where:** every repo. The patterns live in `scripts/hooks/secret-patterns.txt`.

**Why:** what the agent never reads, it can't leak, and what's never committed doesn't haunt your git history.

**Coverage:** Claude Code (shell, Read, Edit, Write, Grep), Codex (shell), Cursor (shell, file reads).

## ✅ `allow-task-cleanup`

**Approves without asking:** commands that only delete finished branches or remove their worktrees. A branch is finished when it's:
- a task branch (`task/<ticket>/<NN>`, `fix/security-<name>/<NN>`),
- the head of a merged PR on GitHub, or
- already gone.

For anything else it has no opinion, and the tool asks you as usual.

**Why:** `build-epic` and `security-audit` can tidy up and keep running unattended, without any risk to unmerged work.

**Claude Code only:** Cursor's guard hooks already approve what they pass, and Codex hooks can't approve.

## 📊 `telemetry-save`

**Does:** when a session ends, it saves the whole run (time, turns, tool calls, tokens, cost, each step) as `~/.agents/telemetry/<session id>.json`. The `telemetry` skill turns these files into reports.

It runs in the background, prints nothing and never fails the session. Turn it off with the `telemetry` skill's `auto_save` Setting ([settings.md](settings.md)).

**Claude Code only:** the other tools have no log extractor.

## ➕ Adding a hook

See "Add a hook" in [AGENTS.md](../AGENTS.md). In short: write the script, add a snippet per tool, list it in the README, and run `./install.sh`.
