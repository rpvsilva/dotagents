---
name: fix-pr
description: Take one PR's failing checks from red to green and push; never merges. Also the pr-fixer agent's brief.
disable-model-invocation: true
---

Take one PR (or a few coupled ones sharing a branch) from **red** to **green**: reproduce the failure, fix its root cause, push. Merging belongs to whoever started you.

Inputs: the PR number(s). When an orchestrator started you, its message may also carry the branch, the repo's commands, and an **extra brief**: steps for its kind of PR (e.g. Renovate's lock regeneration). Rules in the extra brief and in `.agents/fix-pr-notes.md` override the defaults below.

## Steps

1. **Learn the repo.** Read `.agents/fix-pr-notes.md` and the repo's `AGENTS.md` (or `CLAUDE.md`) if they exist. Find the install, lint, test and build commands from the PR CI workflow and the manifest's scripts, unless you were given them. Done when you can name each command CI runs.

2. **Check out the branch** in its own git worktree, unless you are already in one: `git fetch origin && git worktree add <path> -B <branch> origin/<branch>`. Done when `git status` shows the PR branch at its remote head.

3. **Find the failure.** `gh pr checks <n>`, then the failing jobs' logs (`gh run view <run-id> --log-failed`). Run the failing command locally until it goes **red** the same way. If it also fails on `origin/main`, it is pre-existing: note it and leave it alone. Done when you can reproduce every failure, or have marked it pre-existing or CI-only (a missing secret, a flaky runner).

4. **Fix the root cause.** Follow the extra brief here if you have one. Otherwise make the smallest change that fixes the cause, not the symptom; every test stays as strict as it was. Done when the command from step 3 passes locally.

5. **Run every check** CI runs (step 1). Done when all pass, or the only failures are pre-existing ones.

6. **Push.** Commit only the fix, using the repo's commit convention, and push to the PR branch. After a rebase, push with `--force-with-lease=<branch>:<old-sha>`. Done when `gh pr checks <n> --watch` ends **green**, or a failure remains that you cannot reproduce locally.

## Report

**fixed**: the pushed SHA, files changed, what the cause was, and check results. Or **blocked**: what fails, why you could not fix it, and what would unblock it. Add any pre-existing failures under both.
