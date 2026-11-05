---
name: tune-tests
description: Make a repo's tests faster and their coverage wider in one PR, by a test-tuner subagent: profile, fix stalls, close gaps, with before/after times you verify.
disable-model-invocation: true
---

Take a repo's tests to faster and better covered, as one PR. A fresh **test-tuner** subagent does the digging and the edits; you measure the **baseline** before it starts and **verify** its claims after, so the PR carries numbers you ran, not numbers it reported.

Inputs: optional `--diff <ref>` to tune only the suites covering the files changed since `<ref>` (default: every suite).

## Steps

1. **Learn the rules** ([how](../_shared/conventions.md#learn-the-rules); notes file `.agents/tune-tests-notes.md`: the test command, suites to leave alone, known slow spots). Find the command CI runs for each suite. Done when you can run every suite in scope by itself, and know the identity.

2. **Make a worktree** off the default branch, on a new branch named by the repo's convention: `git fetch origin && git worktree add <path> -b <branch> origin/<default>`. Done when `git status` there shows the new branch at the default branch's tip.

3. **Measure the baseline.** In the worktree, run each suite in scope by itself, one at a time, and write `<path>/../tune-tests-baseline.md` (outside the worktree): one row per suite with its wall time and result. If a suite is **red** on the default branch, stop and report it: tuning starts from green. Done when every suite in scope has a time and passes.

4. **Start the test-tuner** as in [Subagents](../_shared/conventions.md#subagents), with [tuner-brief.md](tuner-brief.md) as its brief. Hand it the worktree path, the baseline file path, the scope (every suite, or the files changed since `<ref>`) and the notes file path. Done when it has returned its report.

   The test-tuner is **remote-capable** ([Remote subagents](../_shared/conventions.md#remote-subagents)): name it `--name tune-tests` and attach the baseline file, plus the notes file when it sits outside the worktree. The snapshot has one commit and no history, so give a `since <ref>` scope as the list of files `git diff --name-only <ref>` prints here. When it returns, commit its changes with the repo's convention, then act on its report.

5. **Verify.** In the worktree:
   - Run each suite again, by itself, as in step 3. Every suite is green, and the total is at most the baseline's.
   - Read the diff of every test file. Each assertion the tuner removed or merged has the replacement its report names; a change that drops one without a replacement goes back to the tuner, or gets reverted.
   - Pick the new test you trust least: break the behaviour it covers, watch it go **red**, restore the code.

   Send what does not hold back to the tuner (continue the same subagent if your harness can, otherwise start a new one on the same worktree; a remote tuner gets a new run on the same worktree, with what did not hold in its message), then verify again. Done when all three hold.

6. **Ship** with [ship-pr](../ship-pr/SKILL.md) from the worktree. The PR description holds a table of each suite's time before (step 3) and after (step 5), and the tuner's causes and coverage additions. Done when the PR exists.

7. **Clean up**: once the PR is merged or closed, `git worktree remove <path>` and delete the baseline file.

## Report

The PR URL, total time before and after, and one line per change: the suite, the cause it fixed or the behaviour it now covers.
