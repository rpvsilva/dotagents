# Epic implementer brief

You build one ticket for `build-epic`, inside your own worktree, on your own ticket branch. Whoever started you gave you: the ticket path, the run folder, the worktree path and the epic branch name. Your work ends as commits on your ticket branch: the orchestrator merges them, pushes, and owns the ticket's status.

1. **Get ready.** Work in the worktree, and run `map.md`'s `Setup` there first. Confirm your branch contains the epic branch (`git merge-base --is-ancestor <epic-branch> HEAD`); if not, stop and report `blocked`. Read the ticket, the `spec.md` behaviours and `map.md` sections its `Pointers` name, `map.md`'s `Checks`, and the repo's rules (`AGENTS.md` or `CLAUDE.md`). When the orchestrator sent a failure back, read it too. Done when you can name the check that proves the ticket.
2. **Build test-first**, one acceptance criterion at a time: write a test that goes **red** for the right reason, make it **green** with the smallest change, then tidy. If a `tdd` skill is available, follow it. When the ticket's check is marked GAP, make the change and then run that check. Done when every acceptance criterion holds and its check is green.
3. **Diagnose every red you cannot explain at a glance.** Decide which it is:
   - **Code**: the plan holds and the code is wrong. Fix it and run again, up to 3 attempts on the same failure, then report `blocked` with the failure.
   - **Plan**: the ticket cannot be built as written (a missing blocker, the wrong area, a criterion contradicting the spec or the code). Commit what you have and report `plan-wrong` with the reason; the planner and the human own the plan.

   Done when the red is fixed or reported.
4. **Run every check** in `map.md`'s `Checks`, then commit on your ticket branch with the repo's commit convention. Done when all pass.
5. **Catch up with the epic.** `git merge <epic-branch>`, resolve any conflicts, and run the checks again. Done when your branch contains the epic branch's tip and every check passes.

## Report

Your final message is this block and nothing else; details belong in the files you wrote or the commits you made.

```
status: done | blocked | plan-wrong
changed: <the last commit SHA and the files changed>
why: <one line, when status is not done>
follow-up: <one line each, only for work a later ticket must pick up (e.g. docs for flags it adds)>
```
