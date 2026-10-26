# Renovate PR subagent brief

The extra brief for [fix-pr](../fix-pr/SKILL.md) on a unit of Renovate PRs (one PR, or a few coupled ones). Follow fix-pr; these steps replace its step 4 (**fix the root cause**). The orchestrator also gave you the package(s) and the triage class.

1. Regenerate the lock with a **targeted** update of just the PR's package(s), using the command for the repo's lock file in [Package managers](SKILL.md#package-managers). Updating everything drags in unrelated upgrades; widen only by naming the extra packages resolution needs.
2. If nothing resolves: run the package manager's "why" command and report the blocking packages as **blocked**. A genuine ecosystem block stays blocked; report it rather than hacking around it.
3. Grep the app for the package's API and port it per the package's changelog / upgrade guide.

Commit only the lock file plus code fixes. Add the resolved versions to your report.
