Written by `python3 -I evals/_results/page.py --run <run folder> ... --evals-results <dir> --out <file>`; edit the inputs, not this file.

# Results

Aggregates only. Costs are repriced at the table dated in each row. Human round trips are the dated quotes recorded after approval; `unknown` means the input was missing.

## Build-epic runs

| run | date | model(s) | Claude Code | cost per task | cost basis | pricing as of | human round trips | PRs merged by the agent |
|---|---|---|---|---|---|---|---|---|
| run 1 | 2026-11-18 | claude-opus-5-5, claude-sonnet-5-5 | 2.1.312 | unknown | unknown | unknown | 0 | 5 |
| run 2 | 2026-11-18 | claude-opus-5-5, claude-sonnet-5-5 | 2.1.312 | unknown | unknown | unknown | 1 | 5 |
| run 3 | 2026-11-19 | unknown | unknown | unknown | unknown | unknown | 0 | 5 |

## Eval pass rates

| case | date | model(s) | Claude Code | pass rate | runs judged |
|---|---|---|---|---|---|
| build-epic/hub-sync-conflict | 2026-11-17 | claude-opus-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/implement-block-unblock | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/implement-command-discovery | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/implement-item-fields | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/implement-note | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/pass-a-remove | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| build-epic/pass-a-rename | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| build-epic/pass-a-traps | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 0% (0/3) | 3 |
| build-epic/pass-b-top | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| build-epic/ticket-commits | 2026-11-17 | claude-opus-5-5 | 2.1.326 | 0% (0/3) | 3 |
| configure/build-epic-only | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| configure/machine-three-agents | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| configure/repo-two-agents | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| configure/unknown-setting | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| review-pr/task-seeded | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
| review-pr/ticket-seeded | 2026-11-17 | claude-sonnet-5-5 | 2.1.326 | 100% (3/3) | 3 |
