# Baseline results (epic #499, step A)

The figures later steps of #499 are judged by, recorded for #500 on 2026-11-12 with build-epic at dotagents `main` as installed then (before step B). All runs were local (remote subagents off), on Claude Code 2.1.322, as the `rpvsilva-agent` App, with the authorizations answered the same way: merging PRs that add tests yes, running tests locally on unreviewed code yes, other protected paths no, code that ends the process or changes how pytest runs no. Each run's raw files (`telemetry*.json`, `metrics.json`, `quality.json`) are in `~/.agents/runs/build-epic-sandbox/<run>/baseline/` on Rui's machine.

Time figures were recomputed for #507 from the same sessions, with no new run (`telemetry*-v6.json` and `metrics-v6.json` beside the originals): active minutes now leave out every wait on the human, not only gaps over 15 minutes, and per-ticket minutes keep only active time. The wall-clock figures stay beside them.

## #402: three replays of one approved plan

Each replay is [402](402/README.md)'s fixed plan into a fresh `baseline-402-rN` cut from `46b4cb8`, so planning is not measured.

| | r1 | r2 | r3 | median |
| --- | --- | --- | --- | --- |
| Hidden acceptance tests | 53/53 | 53/53 | 53/53 | 53/53 |
| Cost (USD) | 15.65 | 15.50 | 24.87 | 15.65 |
| Orchestrator / subagents (USD) | 8.76 / 6.89 | 8.84 / 6.66 | 15.98 / 8.89 | 8.84 / 6.89 |
| Active minutes | 62.7 | 50.7 | 74.5 | 62.7 |
| Minutes waiting on the human | 564.1 | 0.5 | 22.4 | 22.4 |
| Agents at once (average) | 1.61 | 1.71 | 1.50 | 1.61 |
| Orchestrator turns | 159 | 150 | 261 | 159 |
| Stops for the human | 3 | 1 | 4 | 3 |
| Tasks (fix tasks) | 17 (0) | 17 (1) | 18 (1) | 17 (1) |
| PRs merged | 22 | 22 | 23 | 22 |
| Red merges, hand fixes after the run | 0, 0 | 0, 0 | 0, 0 | 0, 0 |
| Per-ticket active minutes, median / max | 17.2 / 28.5 | 15.2 / 25.6 | 18.0 / 42.1 | 17.2 / 28.5 |
| Per-ticket wall-clock minutes, median / max | 17.2 / 645.1 | 15.2 / 25.6 | 18.0 / 64.0 | 17.2 / 64.0 |

- Every replay passed every hidden test; what varies is the hub tasks. r1's hub task 05-02 hit a merge conflict in `tests/cli/test_registry.py` and waited on the human overnight (the session ran 11 h, ticket 05 took 10.7 h to merge by the wall clock and 28.5 active minutes; the wait was 562 minutes). r3's 04-03 hit one too, asked the human three times and re-planned ticket 04: about 9 USD and 100 turns more. r2 had none.
- Hub tasks waited 2–17 minutes for earlier tickets, and 55 in r3: the critical path.
- Lead time (epic issue to last ticket merge) is about 71 h in every replay, since #402 was opened on 2026-11-09; it is not a figure of the run.

## #16: one whole run, planning included

[16](16/README.md)'s base `fcef2c7` into `baseline-16-r1`, planned from the issues by the run itself (`gh-16-baseline-r1`). It spanned two sessions: the first planned and got approval, then stopped because `capped` was not on its shell's PATH; the second built.

| | plan session | build session | run |
| --- | --- | --- | --- |
| Hidden acceptance tests | | | 67/73 (core 52/52, strict 15/21), feature-weighted 0.884 |
| Cost (USD) | 3.46 | 17.66 | 21.12 |
| Orchestrator / subagents (USD) | 1.31 / 2.15 | 10.24 / 7.42 | 11.55 / 9.57 |
| Active minutes | 19.6 | 71.7 | 91.3 |
| Minutes waiting on the human | 6.7 | 60.7 | 67.4 |
| Agents at once (average) | 1.00 | 1.16 | |
| Orchestrator turns | 28 | 178 | 206 |
| Stops for the human | 3 | 3 | 6 |
| Per-ticket active minutes, median / max | | | 12.3 / 40.6 |
| Per-ticket wall-clock minutes, median / max | | | 12.3 / 40.6 |

- 5 tickets, 18 tasks (3 fix tasks), 23 PRs merged, no red merge, no hand fix after the run. Ticket reviews: 0 blocker, 4 should-fix, 8 nit; 19 follow-ups (7 should-fix, 12 nit).
- The six strict tests it failed pin choices the issues leave open (a rule past 9999-12-31 refused, skip of a done item exiting 2, skip keeping the id with its `skipped` line, a stopped done occurrence, the `stopped` line), so they are not defects; compare #16 runs on core first.
- Of its six stops, three came from the PATH problem, and two from a mid-run re-plan taking `AGENTS.md` out of tasks 03-03 and 05-02 (a path left to the human), which waited 48 minutes for approval.
- The run reported that `setup.py` printed `OK skips=0` though the base suite exited 127 and never ran (`capped` not found).

## Spend

78.34 USD measured of the 140–200 budget: 56.02 for the #402 replays, 21.12 for #16 and about 1.20 for the eval cases' first run. Not measured: the session that stopped before replay 1 began (a few turns) and any later eval runs.
