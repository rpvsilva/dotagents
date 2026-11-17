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

4. **Start the test-tuner** as in [Subagents](../_shared/conventions.md#subagents), with [tuner-brief.md](tuner-brief.md) as its brief. Its role is `tuner` ([Protocol](../_shared/conventions.md#protocol)). Write its prompt with `python3 -I ~/.agents/skills/_shared/protocol/prompt.py tuner worktree=<path> scope=<scope> notes=<notes file> baseline=<baseline file> output=<path>/../tune-tests-run/reports/00-tests.json --out <path>/../tune-tests-run/prompts/00-tests.md` (`scope`: `every suite`, or `the files changed since <ref>`), and hand it that file. Check its final message with `python3 -I ~/.agents/skills/_shared/protocol/check.py tuner output <path>/../tune-tests-run/reports/00-tests.json --message -` (the message on stdin) and act on the line as [Protocol](../_shared/conventions.md#protocol) says; on a brief violation that you cannot continue the same subagent for, start a new one on the same worktree with `reissue=<that output file>` added to the prompt. Done when `check.py` prints `OK tuner <status>`. On `blocked`, stop and tell the human its `why`.

   Which subagents may run remotely, and when, is [Remote subagents](../remote-subagents/SKILL.md)' rule. When the test-tuner runs remotely, name it `--name tune-tests`, attach the baseline file, plus the notes file when it sits outside the worktree, and write the prompt with `'worktree=~/work'`, those inputs as `'baseline=~/attached/<name>'` (quoted, so the shell keeps the `~`), `remote:=true` and no `output`. The snapshot has one commit and no history, so give a `since <ref>` scope as the list of files `git diff --name-only <ref>` prints here. When it returns, copy its `report_path` to `<path>/../tune-tests-run/reports/00-tests.json`, check it with `check.py tuner output <that file>` (no `--message`: the file is the message), commit its changes with the repo's convention, then act on its output.

5. **Verify** with build-epic's script: `python3 -I ~/.agents/skills/build-epic/scripts/verify_tuner.py <run> <TT>-tests [--report <file>]`. It checks that the tuner's output matches its schema, that only test files changed, that no assertion was removed without a `moved` entry naming it, that the suites are green, and that each `mutations` entry of the output turns them red (applied, run, and restored on any exit). Its `<run>` is the throwaway run folder beside the worktree, `<path>/../tune-tests-run/`, holding:
   - `map.md` with a `## Checks` section: one fenced code block, one suite command per line (the commands from step 3);
   - `tickets/00-tune.md` with a `Branch: <default>` line, so the script diffs the worktree against where it left the default branch;
   - `worktrees/00-tests`, a link to the worktree;
   - `reports/00-tests.json`, the tuner's output (step 4).

   Then run it with `<path>/../tune-tests-run 00-tests`. Act on its one line:
   - `OK <TT>-tests verified mutations=<n>`: the claims hold. Run each suite again, by itself, as in step 3, for the after times: the total is at most the baseline's.
   - `FAIL ...` (its log is named on the line): send what it names back to the tuner (continue the same subagent if your harness can, otherwise start a new one on the same worktree; a new one or a remote one gets the prompt of step 4 plus `failure=<a file holding the line>`, attached as `~/attached/<name>` when remote), then run it again. When the same failure comes back, or the script cannot check the PR (`no-report`, `bad-report`, `bad-mutation`, a suite that times out), verify that part by hand: read the diff of every test file for each assertion's replacement, break the behaviour of the new test you trust least and watch it go **red**, then restore the code.

   Done when the script prints `OK` (or every part it could not check holds by hand) and the total time is at most the baseline's.

6. **Ship** with [ship-pr](../ship-pr/SKILL.md) from the worktree. The PR description holds a table of each suite's time before (step 3) and after (step 5), and the tuner's causes and coverage additions as `python3 -I ~/.agents/skills/_shared/protocol/render.py tuner <path>/../tune-tests-run/reports/00-tests.json` prints them. Done when the PR exists.

7. **Clean up**: once the PR is merged or closed, `git worktree remove <path>` and delete the baseline file and `tune-tests-run/`.

## Report

The PR URL, total time before and after, and one line per change: the suite, the cause it fixed or the behaviour it now covers.
