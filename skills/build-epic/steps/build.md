# Build: `start`, `dispatch`, `finish`, `review`, `merge`, `human`

`next.py <run> --go`, `--wait` and `--returned <TT-NN>` run `start`, `dispatch`, `finish` and `merge` themselves and print each script's line; the sections below say what those lines mean and what to do on a `FAIL`. Run a script yourself only when `next.py` without `--go` names its action.

## `start <TT>`

`start_ticket.py <run> <TT>` makes the ticket's branch (its `Branch` line) from the run's base, here and on origin, sets it `building` and marks its issues started: the `in progress` label, assigned to the human (`OK TT building branch=<b> base=<sha7>`). With `started=manual` on its line (the `in_progress` Setting names a project field, or another tracker), mark the issues started yourself as that Setting says. `FAIL TT push` leaves it `ready`.

## `dispatch <TT-NN> [hub]`

All dispatch lines at once:

1. `start_task.py <run> <TT-NN>` adds `worktrees/TT-NN` on a new task branch from the ticket branch and sets the task `in-progress` (`OK TT-NN in-progress wt=<path> tip=<sha7>`). For a `hub` task it also merges the run's base in (`synced=<sha7>`); when that merge conflicts it adds `conflict=<files>` and leaves the markers for the implementer: add `--sync <files>` below, and run that task here, never remotely.
2. `task_prompt.py <run> <TT-NN> [--sync <files>]` writes the prompt and prints `OK TT-NN local prompt=<file> agent=epic-implementer`. Start that agent with the file's text, description `implement: TT-NN <title>`.
3. **Remotely** (remote is on and the task has no `--sync`): `task_prompt.py <run> <TT-NN> --remote` prints `OK TT-NN remote prompt=<file> args=<arguments>`; `next.py` starts `~/.agents/bin/remote-agent` with them itself, detached, its stdout to `<run>/remote/TT-NN.out`, its stderr to `<run>/remote/TT-NN.log` and its exit code to `<run>/remote/TT-NN.exit`, and prints `remote TT-NN started log=<file>`; `next.py --wait` (or any later `--go`, `--returned` or `--reviewed`) collects it once it ends, its output moved to `<run>/reports/TT-NN.json`. Run it yourself, in the background with its stdout to `<run>/reports/TT-NN.json`, only for a `remote TT-NN args=<arguments>` line or a run this file tells you to start. Keep the name and worktree it gives (`TT-NN-<slug>`, `<run>/worktrees/TT-NN`): `finish_task.py` finds the run's cost by them.

## `finish <TT-NN>`

When the implementer returns: pipe its final message to `report_check.py <run> <TT-NN>` (a remote one: `report_check.py <run> <TT-NN> --from-report` once `remote-agent` exits 0 or 3, which `next.py` does itself for a run it started; any other exit, `FAIL TT-NN remote-exit <code>: <stderr>` from `next.py`, is acted on as [Remote subagents](../../remote-subagents/SKILL.md) step 4 says: `blocked` with stderr's last lines as the why, unless the code says otherwise). A `brief-violation` gets one new agent with `task_prompt.py <run> <TT-NN> --reissue` (`--remote` when it ran remotely); a second one is `blocked`.

Then, for an agent that ran here, `capped stop <run>/worktrees/TT-NN`; then `finish_task.py <run> <TT-NN> --title "<the task's title in the commit convention>"`. It commits what the agent left uncommitted, merges the ticket branch in, and adds paths changed outside `Writes` to that line:

- `OK TT-NN committed files=<n>`: done here; `next.py` says `review` or `merge`.
- `OK TT-NN no-change`: the task is `done`.
- `FAIL TT-NN conflict <files>`: send it back to a **resolver**: its implementer (continue it if your harness can, otherwise a new one on the same worktree, description `resolve: TT-NN`) with `task_prompt.py <run> <TT-NN> --resolve "<files>"`. With remote on, dispatch the resolver remotely as [A resolver](../../remote-subagents/SKILL.md#a-resolver) says (never commit its patch yourself), unless a conflicted file has no `<<<<<<<` line or is missing, or the run exits 3: then resolve it here. On its return, `report_check.py` as above, then `finish_task.py <run> <TT-NN> --report <run>/reports/TT-NN.json --title "<as before>"`.
- `FAIL TT-NN blocked <why>` or `plan-wrong <why>`: its work is committed; `next.py` prints the `human` line.

## `review <TT-NN>`

A `Tier: high` task is reviewed before it merges; every other task is covered by its ticket's deep review. It has no PR, so the review runs here, from its worktree: follow [review-pr](../../review-pr/SKILL.md) steps 3 and 4 with the prompt `python3 -I ~/.agents/skills/review-pr/scripts/review_prompt.py task --worktree <run>/worktrees/TT-NN --head <the worktree's HEAD> --base <ticket-branch> --run <run> --task <task file> --output <run>/reviews/TT-NN-<sha7>.json` (no `--pr`), the default reviewer only, description `review: TT-NN`. Post nothing now: the ship step posts it on the ticket PR. Sort its confirmed and plausible findings as [ship.md](ship.md#triage) does: each **Fix** goes back to the implementer as a `red` does (below), numbered in `<run>/reviews/TT-NN-<sha7>-findings.md`; then `finish_task.py`, and `next.py` asks for a review of the new head. When a second round still finds a Fix, stop and tell the human with the review's rendered view.

## `merge <TT-NN>`

`merge_task.py <run> <TT-NN>` catches the task up with the ticket branch, runs the merge guard and every command of `map.md`'s Checks (timed; the task's proof until the deep review), then merges it into the local ticket branch as one merge commit, sets it `done` and removes its worktree: `OK TT-NN done suites=<n> <secs>s commit=<sha7>`. Once the ticket has a PR (a fix round), it also pushes the ticket branch. One merge per ticket at a time; merges into different tickets may run at once. Otherwise the task stays `in-progress`:

- `unreviewed`: a `Tier: high` task with no review of its head; `next.py` says `review`.
- `conflict <files>`: run `finish_task.py` to restart the catch-up in the worktree, resolve as under `finish`, then merge again.
- `red <suite>`: send it back to its implementer (continue it, or a new one with `task_prompt.py <run> <TT-NN> --failure <log>`; remotely, always that prompt with `--remote`). On its return, `finish` as above, then merge again.
- `ticket-moved`: another merge landed first; merge again. `push`: merge again once; tell the human when it fails twice.
- `guarded <file:line rule>; ...`: the change hits a leave-for-human rule the human did not waive; nothing merged. Tell the human with the line as printed; only their answer, recorded with `decide.py <run> clause "<rule name>" yes "<their words>"`, waives it. `bad-rule`, `guard-no-base`, `git-<cmd>`: the guard could not run; tell the human with the log.

Each `follow-up` row of the task's output (`followups`) that a later task must pick up becomes an acceptance criterion of the not-yet-started task of the same ticket it concerns, or else of that ticket's review-fixes task ([ship.md](ship.md#triage)).

## `human <TT-NN> blocked|plan-wrong: <why>`

Show the human `view.py <run> task <TT-NN>` and the question in one sentence.

- **blocked**: first, a remote task whose `remote-agent result <TT-NN-slug>` says `killed` gets one retry on the same worktree with `task_prompt.py <run> <TT-NN> --remote --cap "<kind> killed <cmd> (--mb <mb> --secs <secs>)"` (from the result's last `capped_kills` entry and its `caps`); a run whose prompt already carried a `cap` gets none. Otherwise set the task `blocked`, run `tracker_cmd.py <run> blocked <TT-NN> "<why>"`, and keep building the rest.
- **plan-wrong**: run `tracker_cmd.py <run> blocked <TT-NN> "<why>"`, dispatch nothing new, let running implementers finish, then [`plan revise`](plan.md#plan-revise) with the task's path as the reason.

## Base change

When the human asks to move the run onto another base: `catch_up.py <run> <new-base>` changes nothing and prints `OK run catch-up <n> unstarted <m> started unstarted=<TT,..|none> started=<TT,..|none>`. Offer them that catch-up with both lists, record their answer with `decide.py <run> quote`, and once they accept run `catch_up.py <run> <new-base> --apply` (`OK run caught-up base=<new-base> ...`). `FAIL run conflict <TT>: <files>`: hand the files to the human, then run it again. `pr-edit <TT>`: run it again. `no-base`: the branch is not on origin.
