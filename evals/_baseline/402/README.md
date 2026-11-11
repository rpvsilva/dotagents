# Baseline: sandbox epic #402

A yardstick for build-epic's build quality (epic #499, step A). It replays one fixed plan of epic #402 of `rpvsilva/build-epic-sandbox` (five features of the `todo` CLI: #403 subtasks, #404 notes, #405 snooze, #406 item dependencies, #407 `todo next`) and scores the result with hidden acceptance tests and the run's own records.

Dev tooling, like the rest of `evals/_*`: no part ships it, nothing installs it, CI never runs it. Only `test_replay.py` and `test_quality.py` run in CI, through `tests/python.test.sh`.

| Path | What |
| --- | --- |
| `plan/` | The fixed plan: rerun2-local's planner output, reset to read as just approved. |
| `replay.py` | Seeds a fresh run folder from `plan/`, so build-epic resumes past planning. |
| `acceptance/` | The hidden acceptance tests (`accept_*.py`), their `conftest.py` and `pytest.ini`. |
| `quality.py` | Scores one ref: acceptance pass rate, plus review findings, red merges and hand fixes from a run folder. |

## The fixed plan

`plan/` is rerun2-local's `spec.md`, `map.md`, `areas.md`, `tickets/` and `tasks/` (run folder `~/.agents/runs/build-epic-sandbox/gh-402-interplay-rerun2-local`), changed only as follows:

- `Status:` is `ready` on every ticket and task; the `PR:`, `Done:`, `Claimed by:` and `CI red:` lines are gone.
- Each `Writes` line is as the planner wrote it: the paths `finish_task.py` appended (its `Writes amended with:` log lines) are stripped.
- The criterion `start_task.py` carried into 04-03 from `followups.jsonl` (`(from reviews/464-eb256ad.md)`) is gone.
- The run's base, `rerun2-local`, is written `@BASE@`; `replay.py` fills it in.
- Dropped tasks, all added after the first approval:
  - `02-04-review-fixes.md`, `03-05-review-fixes.md`: the run's fix rounds.
  - `03-04-registry-subset.md`, `04-04-registry-subset.md`: added by mid-run re-plans (`logs/rejection-1.md`, `-2.md` of that run) to switch `tests/cli/test_registry.py` to a form the hub-file union could merge. Kept, they make `plan_check.py` report two `Writes` overlaps on that file, and step 4 then starts a revise planner instead of resuming. The re-plan's lasting part stays: `tests/cli/test_registry.py` is a hub file in `map.md`'s `Lanes`, and `map.md`'s Invariants require the sorted + subset form from 01-01 on, so the two tasks have nothing left to do. So 03-03 and 04-03 lose their blocker `04`, and `map.md`'s two re-plan paragraphs are rewritten without the dropped tasks or the rejection logs.

No tuning task (`TT-tests.md`) existed. `plan_check.py` on the seeded folder prints `plan ok: 16 tasks, 1 lanes`.

## Replay

`replay.py <plan> <run folder> --base <branch>` copies the plan into an empty or new run folder, fills in `@BASE@`, and refuses a plan that does not read as just approved or that `plan_check.py` rejects (`FAIL replay <reason>`, the folder left empty). It writes no `run.md` or `decisions.md`: `init_run.py` and `decide.py` own those.

A seeded folder meets the done-conditions of steps 2-4 (`spec.md`, `areas.md` saying `no exploration needed`, `map.md`, every ticket and task `ready`, `plan ok`). Offline, `status.py` lists 5 tickets and 16 tasks `ready`, `status.py --write` writes `state.md`, `resume.py` prints `OK run resumed 0 actions`, and, after `init_run.py` and `decide.py set base`, `plan_questions.py`, `review_plan.py`, `guard_rules.py` and `permissions.py` all run (`test_replay.py` checks the first three). So build-epic resumes at step 1's open items, then step 5.

One measured replay into a fresh integration branch cut from `46b4cb8`:

```sh
SANDBOX=~/path/to/build-epic-sandbox   # a clone of rpvsilva/build-epic-sandbox
BASE=baseline-402-r1                   # a new branch name per replay
RUN=~/.agents/runs/build-epic-sandbox/gh-402-baseline-r1
S=~/.agents/skills/build-epic/scripts

git -C "$SANDBOX" fetch origin
git -C "$SANDBOX" push origin 46b4cb8:refs/heads/$BASE
git -C "$SANDBOX" ls-remote origin 'refs/heads/feat/gh-40[3-7]-*'   # delete any listed: start_ticket.py pushes these names
python3 -I evals/_baseline/402/replay.py evals/_baseline/402/plan "$RUN" --base $BASE
python3 -I $S/init_run.py "$RUN" "$SANDBOX" --epic 402
python3 -I $S/decide.py "$RUN" set base $BASE
```

Then start build-epic in the sandbox clone with `#402 --into baseline-402-r1` and say the run folder is `$RUN`, so it resumes that folder. The human still:

1. Answers step 1 as for any run: `setup.py` (Setup, `test_command:`, `ci:`) and its `skips.md` question.
2. Approves at step 5. To match rerun2-local, answer as it did: `Yes (to the plan, the assumed answers to open questions 1-14, and the recommended authorizations: 1 yes, 2 yes, 3 no, 4 no)`, its clauses being merging PRs that add tests (yes), running tests locally on unreviewed code (yes), other protected paths (no), code that ends the process or changes how pytest runs (no).
3. Takes any ticket the merge rule leaves to a human, as in every run.

When the run reports, score it:

```sh
python3 -I evals/_baseline/402/quality.py "$SANDBOX" origin/$BASE --run "$RUN"
```

## Acceptance tests

`acceptance/accept_*.py`, one file per feature, test the CLI from the outside: each test runs `todo.cli.main(argv, out=, today=)`, as the sandbox's own `cli` fixture does, in its own `TODO_FILE` and `HOME`, with today fixed at 2026-06-10. The files are named `accept_*`, never `test_*`, so `tests/python.test.sh` leaves them alone. Run them by hand against any checkout:

```sh
ACCEPT_CHECKOUT=<checkout> <python with pytest> -I -m pytest -c evals/_baseline/402/acceptance/pytest.ini evals/_baseline/402/acceptance
```

53 tests in two tiers:

- **core** (35): the 25 behaviours of `plan/spec.md` and the issues' criteria, phrased so any reasonable form passes (a child indented more than its parent, `1/2` somewhere on the parent's line, a mark containing `wait`). Each exit-2 test also shows the command or flag exists, so the base's argparse errors never pass it.
- **strict** (18, `@pytest.mark.strict`): the exact forms `plan/spec.md`'s open-question answers decide (`todo show`'s field lines, ` waiting on 2,3` before the priority marker, `noted 1`, notes of 1-1000 characters, any subtask depth). A replay of this plan should pass them; a run that planned its own spec need not.

| Feature | core | strict |
| --- | --- | --- |
| 403-subtasks | 6 | 4 |
| 404-notes | 7 | 3 |
| 405-snooze | 7 | 4 |
| 406-dependencies | 7 | 4 |
| 407-next | 8 | 3 |

## Quality score

`quality.py <checkout> <ref> [--run <run folder>] [--python <python>] [--acceptance <folder>]` checks `<ref>` out in a temporary detached worktree of `<checkout>` (a bare clone works too) and removes it after; the checkout's working tree is never touched. `--python` needs pytest; it defaults to `<run>/env/bin/python`, else the running interpreter. `--acceptance` scores another epic's tests (default: this folder's `acceptance/`; [#16](../16/README.md) passes its own), their per-feature names read from that folder's `features.json` when it has one. It prints one JSON object; its docstring defines every field, `feature_weighted` among them (the mean of the per-feature pass rates). The run-folder rules:

- **Ticket-review findings**: each ticket's deep review is the first report posted on its ticket PR (`== post_review PR<n> <report>` in `logs/PR<n>-post_review.log`), plus its `-security.md` report. Their severities are summed from each report's `follow-up: <n> blocker, <n> should-fix, <n> nit` line, which every reviewer ends with. Later reports on the same PR are re-reviews of the fixes: listed in `re_reviews`, not counted.
- **Fix findings**: the numbered lines of `reviews/*-findings.md`, the findings the orchestrator sent back as Fix (task and ticket reviews), by the first severity word on each line.
- **Red merges**: `FAIL <id> red ` lines in `logs/<id>-merge_task.log`, as `skills/telemetry/run_metrics.py` counts them; `batch_red` counts `FAIL batch red ` in `logs/batch-ship_batch.log`.
- **Hand fixes**: the first-parent commits of `<ref>` after the run's last ticket merge (the newest first-parent commit whose subject ends `(#<n>)`, `<n>` a ticket PR), whose author name does not end in `[bot]`, the GitHub App's merge identity. A human's commit inside a ticket branch (rerun2-local's `git merge -s ours` on ticket 03) lands squashed into the bot's merge and is not counted.

## Calibration

Run on 2026-11-11 against the four finals with their run folders, and on `46b4cb8` with no run, from a bare copy of the sandbox's refs (so the local clone stayed untouched), with rerun2-local's `env/bin/python` (pytest 9.1.1):

```sh
python3 -I evals/_baseline/402/quality.py <sandbox> 46b4cb8 --python <run>/env/bin/python
python3 -I evals/_baseline/402/quality.py <sandbox> origin/<final> --run ~/.agents/runs/build-epic-sandbox/gh-402-interplay-<final>
```

| Ref | SHA | Acceptance | core | strict | 403 | 404 | 405 | 406 | 407 | Ticket review b/s/n | Fix findings b/s/n | Red merges | Hand fixes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `46b4cb8` (base) | 46b4cb8 | 0/53 (0.00) | 0/35 | 0/18 | 0/10 | 0/10 | 0/11 | 0/11 | 0/11 | - | - | - | - |
| rerun-local | 1405957 | 45/53 (0.85) | 35/35 | 10/18 | 7/10 | 8/10 | 11/11 | 8/11 | 11/11 | 1/5/3 | 1/6/3 | 0 | 0 |
| rerun2-local | 982462f | 53/53 (1.00) | 35/35 | 18/18 | 10/10 | 10/10 | 11/11 | 11/11 | 11/11 | 0/5/4 | 0/5/0 | 0 | 0 |
| rerun-remote | 8e262cc | 47/53 (0.89) | 35/35 | 12/18 | 9/10 | 8/10 | 11/11 | 8/11 | 11/11 | 0/1/4 | 0/2/0 | 0 | 0 |
| rerun2-remote | d182b45 | 46/53 (0.87) | 35/35 | 11/18 | 9/10 | 8/10 | 11/11 | 7/11 | 11/11 | 0/4/6 | 2/10/0 | 0 | 0 |

Reading it:

- No test fails on all four finals; every core test passes on all four.
- Every strict failure is a run whose own planner answered that open question differently: rerun-local limits subtasks to one level and puts the count after the due date; rerun-remote writes ` (1/2)` and ` ⧗ waiting on`; rerun2-remote writes ` [1/2]` and ` (waiting on 6)` and refuses a block on a done item; all three print other lines from `todo block`, `todo unblock` and `todo show`, and exit 2 on an `unblock` of an absent pair. rerun2-local passes all 18 because the fixed plan is its plan. Compare a replay with rerun2-local on both tiers, and with the other three on core only.
- Every final is an unbroken line of App merges, so hand fixes are 0 everywhere.

## Not verified

- A real replay: no build-epic run was started (no spend). Steps 1 and 5 on a seeded folder, `setup.py` and the tracker steps ran only in the real runs, not here.
- That the orchestrator, told the run folder, resumes it instead of making a new slug folder: the skill has no run-folder flag, so the prompt has to say it.
- The sandbox issues #403-#407 are reused by every run and still open; a replay labels them again.
