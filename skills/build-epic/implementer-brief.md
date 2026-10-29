# Epic implementer brief

You build one task for `build-epic` (or `security-audit`, where the fix branch plays the ticket branch), inside your own worktree, on your own task branch. Whoever started you gave you: the task path, the run folder, the worktree path and the ticket branch name. Your work ends as commits on your task branch: the orchestrator pushes them, opens and merges the task's PR, and owns the task's status.

1. **Get ready.** Work in the worktree, and run `map.md`'s `Setup` there first. Confirm your branch contains the ticket branch (`git merge-base --is-ancestor <ticket-branch> HEAD`); if not, stop and report `blocked`. Read the task, the `spec.md` behaviours and `map.md` sections its `Pointers` name, `map.md`'s `Checks`, and the repo's rules (`AGENTS.md` or `CLAUDE.md`). When the orchestrator sent a failure back, read it too. Done when you can name the check that proves the task.
2. **Build test-first**, one acceptance criterion at a time: write a test that goes **red** for the right reason, make it **green** with the smallest change, then tidy. If a `tdd` skill is available, follow it. When the task's check is marked GAP, make the change and then run that check. Every test is **real**:
   - It drives the public surface (the CLI, the API, a module's exported functions) and asserts what a user or caller observes; private helpers and mocks of the repo's own code stay out of it.
   - It is named after the behaviour it checks, and every acceptance criterion has at least one.
   - It can fail: once green, break the behaviour it covers (delete the line, flip the condition), watch that test go **red**, then restore the code. A test that stays green gets rewritten or deleted.

   Done when every acceptance criterion holds, its check is green, and each new test has gone red against broken code.
3. **Diagnose every red you cannot explain at a glance.** Decide which it is:
   - **Code**: the plan holds and the code is wrong. Fix it and run again, up to 3 attempts on the same failure, then report `blocked` with the failure.
   - **Plan**: the task cannot be built as written (a missing blocker, the wrong area, a criterion contradicting the spec or the code). Commit what you have and report `plan-wrong` with the reason; the planner and the human own the plan.

   Done when the red is fixed or reported.
4. **Run every check** in `map.md`'s `Checks`, then commit on your task branch with the repo's commit convention. Done when all pass.
5. **Catch up with the ticket.** `git fetch origin && git merge <ticket-branch>`, resolve any conflicts, and run the checks again. Done when your branch contains the ticket branch's tip and every check passes.

## When you run remotely

Your worktree is `~/work`: a snapshot of the task worktree, already holding the ticket branch's tip, with one commit and no other branches. The task, `spec.md`, `map.md` and `notes/` are under `~/attached`. Skip the branch check in step 1 and all of step 5, and do not commit: leave your changes in the files. The orchestrator commits them on your task branch and catches up with the ticket. In the report, `changed:` lists the files.

## Report

Your final message is this block and nothing else; details belong in the files you wrote or the commits you made.

```
status: done | blocked | plan-wrong
changed: <the last commit SHA and the files changed>
why: <one line, when status is not done>
follow-up: <one line each, only for work a later task must pick up (e.g. docs for flags it adds)>
```
