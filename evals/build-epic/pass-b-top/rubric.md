Score the planner's pass B: `map.md`, `tickets/` and `tasks/` in its run folder, judged against the canned `spec.md` it was given and the repo's `AGENTS.md`.

- covers-spec: Every behaviour of `spec.md` belongs to a task, and no task builds something the spec does not ask for.
- tracer-bullets: Each task is a narrow slice through every layer it needs (command, store, test, README) and verifiable on its own; a one-ticket epic this small is not split into more tasks than it needs.
- writes-real: Each task's `Writes` names the repo's real files (`tally/cli.py`, `tests/cli_test.py`, `README.md`), and tasks sharing a file are ordered by `Blocked by`.
- checks-real: `map.md`'s `Checks` holds the command the CI workflow runs, in its capped form, and each task's `Check` names a test that would really prove its criteria.
- sections-meaningful: `Invariants`, `Field matrix` and `Test isolation` say what this repo needs (the `tally: <message>` error form, the store written only through `tally/store.py`, `TALLY_FILE` in a temp file), or `None` with a reason; nothing is filler.
- assumed-answers: Every open question, the spec's and any new one, has a sensible assumed answer in brackets.
