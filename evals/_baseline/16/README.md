# Baseline: sandbox epic #16

A second yardstick for build-epic's build quality (epic #499, step A), for cross-checks with [#402](../402/README.md). Epic #16 of `rpvsilva/build-epic-sandbox`, "Recurring todos", has six issues: #17 create a recurring todo (`--every`), #18 completing one schedules the next, #19 `todo skip`, #20 `todo stop`, #21 the rule in `remind` and export, #22 one next occurrence after sync. Each run rebuilds it on a base where the feature was removed, and is scored with hidden acceptance tests and the run's own records.

Dev tooling, like the rest of `evals/_*`: no part ships it, nothing installs it, CI never runs it. Only `test_layout.py` runs in CI, through `tests/python.test.sh`.

| Path | What |
| --- | --- |
| `acceptance/` | The hidden acceptance tests (`accept_*.py`), their `conftest.py`, `pytest.ini` and `features.json` (file to issue). |
| `test_layout.py` | Checks `acceptance/` has the shape `quality.py --acceptance` reads. |

Scoring reuses [`../402/quality.py`](../402/quality.py) with `--acceptance evals/_baseline/16/acceptance`.

## The base

#16 was built once and merged into sandbox `main` as `3922e44` (on `6dabb50`). Later commits (`a029e25`..`46b4cb8`: docs, security hardening, CI) touched the recurrence code too. The base is `fcef2c7` (`chore: remove recurring todos to rebuild epic #16`), one commit on `46b4cb8`, so a run sees the same repo rules, CI and hardening as #402's runs. It is `git revert 3922e44`, with the 7 conflicting files resolved by keeping every later change that guards recurrence-free code:

- Removed: `--every`, `todo skip`, `todo stop`, `Item.every`/`anchor_day`/`series_id`/`occurrence`, rule parsing and `next_due`, `complete(today=)` and the next occurrence, `NotRecurring`, the sync dedupe of occurrences and "stop wins", the `↻ every` suffix in list, remind and Markdown, the CSV `every` column, the recurrence parts of `README.md` and `AGENTS.md`, every recurrence test, and the `.agents/security-audit-notes.md` accepted risk about syncing series (its heading stays, with no entries).
- Kept from the later commits: `normalize_title`, `show`, `MAX_ID` and `_next_id`, the row checks of `item_from_row` (unknown fields refused, so a stored `every` fails to load on the base), the capped `updated_at`, `check_folders`, `_read` (FIFO, size, hard-link and symlink checks), the atomic `save` keeping mode and group, `export --out` replacing a symlink, the CSV formula guard, `~/.todo.json` as default store, the stdout `errors="replace"`, the remote's `follow_symlinks=False`, and every test of those, minus the recurring cases (`test_complete_recurring_after_the_largest_id...`, the `every` and `series_id` rows of `test_load_error_shows_a_bounded_value`).

`git diff fcef2c7 46b4cb8` touches only recurrence: 22 files, 1246 insertions, 49 deletions. The sandbox suite on the base: 251 passed.

## Replay

No fixed plan: unlike #402, each run plans #16 itself from the issues, so a measured run is the whole of build-epic, planning included. One measured run into a fresh integration branch cut from the base:

```sh
SANDBOX=~/path/to/build-epic-sandbox   # a clone of rpvsilva/build-epic-sandbox
BASE=baseline-16-r1                    # a new branch name per run
RUN=~/.agents/runs/build-epic-sandbox/gh-16-baseline-r1
S=~/.agents/skills/build-epic/scripts

git -C "$SANDBOX" fetch origin
git -C "$SANDBOX" push origin fcef2c7c87f05b497eb43e65f254020e15567ce6:refs/heads/$BASE
git -C "$SANDBOX" ls-remote origin 'refs/heads/feat/gh-1[6-9]-*' 'refs/heads/feat/gh-2[0-2]-*'   # delete any listed: start_ticket.py pushes these names
gh issue reopen 16 17 18 19 20 21 22 -R rpvsilva/build-epic-sandbox   # closed by the first build; see Not verified
mkdir -p "$RUN"
python3 -I $S/init_run.py "$RUN" "$SANDBOX" --epic 16
python3 -I $S/decide.py "$RUN" set base $BASE
```

Then start build-epic in the sandbox clone with `#16 --into baseline-16-r1` and say the run folder is `$RUN`, so it resumes that folder instead of making a new one. The human answers step 1 (`setup.py`, its `skips.md` question) and step 5 (the plan, its open questions and the authorizations) as for any run; to compare runs, answer the authorizations the same way each time (#402's rerun2-local said: merging PRs that add tests yes, running tests locally on unreviewed code yes, other protected paths no, code that ends the process or changes how pytest runs no). The human takes any ticket the merge rule leaves to them.

When the run reports, score it:

```sh
python3 -I evals/_baseline/402/quality.py "$SANDBOX" origin/$BASE --run "$RUN" --acceptance evals/_baseline/16/acceptance
```

## Acceptance tests

`acceptance/accept_*.py`, one file per issue, test the CLI from the outside: each command runs `todo.cli.main(argv, out=, today=)` with `TODO_FILE` set to the test's own file, `HOME` under `tmp_path`, and today 2026-11-10 unless a test passes its own. The sync tests run two machines (two todo files) and `todo sync` against one `TODO_REMOTE`. No test reads the todo file's JSON or imports anything but `todo.cli.main`, so any storage design passes. Named `accept_*`, never `test_*`, so `tests/python.test.sh` leaves them alone. Run them by hand against any checkout:

```sh
ACCEPT_CHECKOUT=<checkout> <python with pytest> -I -m pytest -c evals/_baseline/16/acceptance/pytest.ini evals/_baseline/16/acceptance
```

They come from the tests `3922e44` added and the later commits kept, rewritten to go through the CLI. 73 tests in two tiers:

- **core** (52): the issues' acceptance criteria, including the forms the issues spell out (`↻ every month` after the due date, the issue's `[ ] 4: pay rent (due 2026-11-01) ↻ every month #home`, the CSV header `id,title,done,due,tags,priority,every`, exit 2 on a bad rule or on skip/stop of a one-off). Each exit-2 test also shows the command or flag exists, so the base's argparse errors never pass it.
- **strict** (21, `@pytest.mark.strict`): choices the issues leave open, pinned as the first build made them: the rule before the priority marker, rules written back in canonical form (`--every 3 Months` shows `every 3 months`), a quoted rule, the `next 2: ...`, `skipped 1: ..., next due ...` and `stopped 1` lines, skip keeping the id, exit 2 on skipping a done item and exit 1 on an absent id, remind lines without priority or tags, the Markdown line ending like the list line, a rule past 9999-12-31 refused, and a stopped series staying stopped across sync. A run that planned its own answers need not pass them; compare runs on core first.

| Issue (`features.json`) | core | strict |
| --- | --- | --- |
| 17-create | 17 | 9 |
| 18-next-occurrence | 14 | 2 |
| 19-skip | 4 | 3 |
| 20-stop | 3 | 3 |
| 21-remind-export | 5 | 2 |
| 22-sync | 9 | 2 |

The issues weigh very differently in test count (26 tests for #17, 6 for #20), so `quality.py` also prints `feature_weighted`: the mean of the six per-issue pass rates, each issue counting the same.

## Calibration

Run on 2026-11-11 from the scratch clone with pytest 9 (`~/.agents/venvs/evals/bin/python`):

```sh
python3 -I evals/_baseline/402/quality.py <sandbox> <ref> --python <python> --acceptance evals/_baseline/16/acceptance
```

| Ref | What | Acceptance | core | strict | 17 | 18 | 19 | 20 | 21 | 22 | feature_weighted |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fcef2c7` | the base | 0/73 | 0/52 | 0/21 | 0/26 | 0/16 | 0/7 | 0/6 | 0/7 | 0/11 | 0.0 |
| `46b4cb8` | sandbox `main`, the build hardened | 73/73 | 52/52 | 21/21 | 26/26 | 16/16 | 7/7 | 6/6 | 7/7 | 11/11 | 1.0 |
| `3922e44` | the first build as merged | 73/73 | 52/52 | 21/21 | 26/26 | 16/16 | 7/7 | 6/6 | 7/7 | 11/11 | 1.0 |

`46b4cb8` passed three runs in a row (the sync tests order changes by the real clock, with a 20 ms sleep between them).

What the sync tests catch: with `46b4cb8`'s occurrence dedupe switched off, only the two "a later skip wins" tests fail. When both machines complete the same occurrence, both next occurrences get the same id (both sides start from one synced file, and the id collision of items made on both sides, SUP-4118, is out of the epic's scope), so the id merge alone already leaves one.

## Not verified

- A real run: no build-epic run was started (no spend), so nothing here shows build-epic resuming a run folder made by `init_run.py` without a plan, or how a run handles #16's issues, which the first build closed (reopening them is the safe guess; a run on closed issues is untested).
- That the base commit `fcef2c7` is on the sandbox remote: it was made in a local clone; push it (any branch) before the first run.
