---
name: security-audit
description: Security-audit a whole repo with a fresh subagent, verify the findings, then fix the ones the human picks in a PR.
disable-model-invocation: true
---

Find security holes in a repo, prove them, and close the ones the human picks. A fresh reviewer hunts, you **verify** each finding, the human chooses what to fix, and every fix lands through a PR. For one PR, use [review-pr](../review-pr/SKILL.md): it runs the same reviewer.

Input: nothing for the current repo, or `owner/name` for another.

## Steps

1. **Learn the rules.** Read `.agents/security-audit-notes.md`, the repo's `AGENTS.md` (or `CLAUDE.md`), `CONTRIBUTING.md` and the PR template if they exist. Note the **identity** the rules name for GitHub calls (e.g. a `GH_TOKEN` prefix for a bot account) and the local **checks** command; if the rules name a setup that is missing, stop and ask. Done when you know the rule files, the identity and the checks.

2. **Check out the code** in its own git worktree: `git fetch origin && git worktree add --detach <path> origin/<default-branch>`. Clone first, into a temporary folder, when the repo is not local. Done when the worktree's HEAD is the remote default branch.

3. **Start the reviewer**: one subagent with a fresh context, the security reviewer agent if your harness defines one, otherwise a general-purpose subagent told to follow [security-brief.md](security-brief.md). Give it the target (the whole repo), the worktree path and the rule files from step 1. Hand over **only** those, so its read is not anchored on yours. If your harness has no subagents, follow the brief yourself, before reading anything else about the code. Done when it returns its findings.

4. **Verify.** For each finding, open the code it cites and confirm the attack really works: trace the input from source to sink, or run it in the worktree. Drop what does not hold; keep what you could not settle as **plausible**. Done when every finding is **confirmed**, **plausible** or dropped.

5. **Ask what to fix.** Show the kept findings as a numbered list, most severe first, one line each: severity, file:line, claim, the proposed fix, and the plausible ones marked. Ask which to fix: all, some by number, or none. With no kept findings, say what was checked and stop. Done when the human has answered; on "none", go to step 7.

6. **Fix in a PR.** In the worktree, `git switch -c fix/security-<short-name>`. For each chosen finding, write a test that performs the attack and goes **red** when the repo tests that kind of code, then make the smallest fix that turns it **green**; confirm the new tests fail with the fix code reverted. Run the checks from step 1. Then ship the branch with the [ship-pr](../ship-pr/SKILL.md) steps, listing each fixed finding and its test in the PR body. Done when every chosen finding has its fix and test on a pushed branch, and the PR URL exists.

7. **Clean up**: `git worktree remove <path>`.

## Report

What was reviewed, the kept findings (fixed or not), and the PR URL with its state, or why nothing was fixed.
