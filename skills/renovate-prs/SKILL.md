---
name: renovate-prs
description: Fix, verify and merge the open Renovate PRs in the current repo, one isolated subagent per PR.
disable-model-invocation: true
---

Process every open Renovate PR: fix what is fixable, verify each locally and in CI, merge serially, report the rest as **blocked**.

## Steps

1. **Learn the repo.** [Learn the rules](../_shared/conventions.md#learn-the-rules) with notes file `.agents/renovate-notes.md`, and use the identity for every `gh` call. Then find:
   - **Toolchain**: package manager(s) and lock file(s), from the files at the root (see [Package managers](#package-managers)). Note local vs CI runtime versions (the CI workflow pins them).
   - **Checks**: the lint, test and build commands, from the PR CI workflow and the manifest's scripts. Run what CI runs.
   - **Merge rule**: how the repo lets an agent merge (a merge script, required checks, approval tiers). With none named, `gh pr merge` with the method from `gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed`, squash when allowed.

   Done when you can name the exact install, lint, test and build commands a subagent runs, and the merge command.

2. **Triage.** `gh pr list --state open --author app/renovate` plus `gh pr checks <n>` per PR. Sort each into:
   - **green**: CI passes. Still needs a local look for breaking changes CI can't see (code paths with no tests, deploy-only tooling).
   - **artifact failure**: `renovate/artifacts` fails and install complains the lock file is out of date. Renovate failed to write the lock; the fix is regenerating it, unless a real constraint conflict sits behind it.
   - **lockless pin**: the PR bumps a pin with no lock file (a Docker digest, an Action SHA, a one-line version file). There is no artifact to regenerate: judge it on CI alone, and merge it when green.
   - **red**: install works but lint/test/build fails: a code breakage to port.

   **Couple** PRs that can only move together (a framework and its plugin, a test runner and its adapter) into one unit. Done when every PR is in exactly one unit.

3. **Fan out.** Start one subagent per unit, in parallel, each in its own git worktree (if your harness has no subagents, work the units one by one in separate `git worktree add` checkouts). Use the PR fixer agent if your harness defines one; otherwise a general-purpose subagent told to follow [fix-pr](../fix-pr/SKILL.md). Give each its PR number(s), branch, package(s), triage class, the commands from step 1 and any notes-file rules, and tell it to use [subagent-brief.md](subagent-brief.md) as its extra brief; you don't need to read either file yourself. Done when every unit has reported back as **fixed** (pushed SHA) or **blocked** (named blocking packages).

4. **Merge serially.** PRs touching the same lock file conflict after each merge. For each fixed PR, one at a time:
   1. If it conflicts with main: in its worktree, `git rebase origin/main`, resolve the lock by taking main's copy (`git checkout origin/main -- <lockfile>`), re-run the same targeted update, then lint + test, `git rebase --continue`, `git push --force-with-lease=<branch>:<old-sha>`.
   2. `gh pr checks <n> --watch` until every check passes.
   3. Merge with the merge command from step 1, then delete the PR branch.

   A green PR on a path that needs a code owner's approval is not merged: leave it waiting and report it as waiting on that approval.

   Done when every fixed PR shows `MERGED`, or is green and waiting on a named person (an approval the merge rule requires).

5. **Clean up.** Remove the worktrees and their local branches, `git pull --ff-only` on main, re-list open PRs (Renovate often opens new ones mid-run).

6. **Report.** Three lists: **merged** (one line each on what it took; a PR still waiting says on whom), **blocked** (the blocking packages and what would unblock them), **check by hand** (changed code no test covers, deploy-only risks). Ask before closing blocked PRs or adding Renovate ignore rules.

## Package managers

| Lock file | Targeted update | Why blocked |
|---|---|---|
| `composer.lock` | `composer update <pkg> --no-scripts` | `composer why-not <pkg> <ver>` |
| `package-lock.json` | `npm install <pkg>@<ver>` | `npm explain <pkg>` |
| `yarn.lock` (v1) | `yarn upgrade <pkg>@<ver>` | `yarn why <pkg>` |
| `pnpm-lock.yaml` | `pnpm update <pkg>@<ver>` | `pnpm why <pkg>` |
| `poetry.lock` | `poetry update <pkg>` | `poetry show --tree` |
| `uv.lock` | `uv lock --upgrade-package <pkg>` | `uv tree --invert --package <pkg>` |
| `go.sum` | `go get <mod>@<ver> && go mod tidy` | `go mod why -m <mod>` |
| `Cargo.lock` | `cargo update -p <pkg>` | `cargo tree -i <pkg>` |
| `Gemfile.lock` | `bundle update <gem> --conservative` | `bundle exec gem dependency <gem> --reverse-dependencies` |
