# Finish: `clean run`, `report run`

## `clean run`

`clean.py <run>` once no task is `in-progress` (it refuses while one is): it removes every worktree under `worktrees/`, the run's local task branches, and the `in progress` and `blocked` labels from the issues the run closed (`OK run clean <n> removed`). Keep the run folder.

## `report run [stuck]`

`stuck`: what is left is `blocked`, or waits on a `blocked`, `claimed` or `in-review` ticket; each ticket branch keeps every merged task, and a later run resumes from there.

Run `status.py <run> --write`, then give the human `view.py <run> report` as printed: the table, every task waiting with its `why`, every claimed ticket, and every follow-up of the run's agents (owner-only files' patches included). Under it, add only:

- each ticket merged into an integration branch, with the line `Issues #<a>, ... are labelled merged-to-<base> and close when <base> reaches <default branch>.`, then the one PR left to the human: `<base>` into `<default branch>`, the repo's gate judging it and its approval-tier paths needing their approval;
- each review whose posting was denied, with its output's path;
- whether `tracker_cmd.py <run> close-epic` closed the epic, or which issues keep it open;
- with `ci: off`, that the local Checks runs were the only proof;
- the run folder's path.
