# Renovate PR subagent brief

You fix one unit of Renovate PRs (one PR, or a few coupled ones) in your own git worktree. The orchestrator gave you: PR number(s), branch, package(s), triage class, and the repo's install, lint, test and build commands. Rules from the repo's notes file override these.

1. `git fetch origin && git checkout -B <branch> origin/<branch>`.
2. Regenerate the lock with a **targeted** update of just the PR's package(s), using the command for the repo's lock file in [Package managers](SKILL.md#package-managers). Updating everything drags in unrelated upgrades; widen only by naming the extra packages resolution needs.
3. If nothing resolves: run the package manager's "why" command and report the blocking packages as **blocked**. Never hack around a genuine ecosystem block.
4. Grep the app for the package's API and port it per the package's changelog / upgrade guide.
5. Run install, lint, test and build. A failure that also happens on `origin/main` is pre-existing; note it.
6. Commit only the lock file plus code fixes, with the repo's commit convention, and push to the PR branch. **Leave merging to the orchestrator.**

Done when you report back: resolved versions, changed files, check results, and either the pushed SHA (**fixed**) or the blocking packages (**blocked**).
