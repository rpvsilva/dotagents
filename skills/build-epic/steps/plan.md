# Plan: `plan A`, `explore`, `plan B`, `plan revise`

Write each planner's or explorer's prompt with `python3 -I ../_shared/protocol/prompt.py <role> key=value ... --out <run>/prompts/<id>.md` and start the agent with that file's text. On its return, pipe its final message to `report_check.py <run> <id>`: `OK <id> done` and the next `next.py` moves on; `OK <id> blocked` makes `next.py` print a `human` line: show the human `render.py`'s view of the output (`python3 -I ../_shared/protocol/render.py <role> <run>/reports/<id>.json`) and stop until they answer.

## `plan A`

An `epic-planner` (description `plan: A`), role `planner`: `pass=A`, `input=` the epic (its link or ID; plain text goes in a file, given by path), `run=<run>`, `notes=` the notes file's path, `explore=always` when `--explore` was given, `output=<run>/reports/plan-A.json`. It writes `spec.md` and `areas.md`.

## `explore <area>`

One `epic-explorer` per line, all at once (description `explore: <area>`), role `explorer`: `areas=<run>/areas.md`, `area=<area>`, `spec=<run>/spec.md`, `note=<run>/notes/<area>.md`, `output=<run>/reports/explore-<area>.json`.

## `plan B`

A fresh `epic-planner` (description `plan: B`): `pass=B`, `run=`, `notes=`, `output=<run>/reports/plan-B.json`. It writes `map.md`, `tickets/` and `tasks/`.

## `plan revise`

Write the reason to a file first: `plan_check.py`'s output (`python3 plan_check.py <run> > <run>/logs/plan-check.log`), the human's words verbatim on a rejection (`<run>/logs/rejection-<n>.md`), or, after a task's `plan-wrong`, the task's path. Then an `epic-planner` (description `plan: revise-<n>`, `<n>` counting the run's revise passes from 1): `pass=revise`, `run=`, `notes=`, `reason=` that file, `output=<run>/reports/plan-revise-<n>.json`.

After an approval ([approve.md](approve.md)), a revise is approved the same way: show `view.py <run> plan`, wait for the human, then run `tracker_cmd.py <run> planned` and `tracker_cmd.py <run> unblocked <TT>` for the ticket that was blocked. Until then dispatch nothing new.
