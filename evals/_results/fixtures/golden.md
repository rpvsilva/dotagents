Written by `python3 -I evals/_results/page.py --run <run folder> ... --evals-results <dir> --out <file>`; edit the inputs, not this file.

# Results

Aggregates only. Costs are repriced at the table dated in each row. Human round trips are the dated quotes recorded after approval; `unknown` means the input was missing.

## Build-epic runs

| run | date | model(s) | Claude Code | cost per task | cost basis | pricing as of | human round trips | PRs merged by the agent |
|---|---|---|---|---|---|---|---|---|
| run 1 | 2026-11-04 | claude-sonnet-5-5, model-a | 2.1.327 | $0.88 | measured | 2026-01-01 | 2 | 3 |

## Eval pass rates

| case | date | model(s) | Claude Code | pass rate | runs judged |
|---|---|---|---|---|---|
| skill-x/case-one | 2026-11-03 | model-a | 2.1.327 | 67% (2/3) | 3 |
| skill-x/case-two | 2026-11-03 | model-a | 2.1.327 | unknown | 0 |
