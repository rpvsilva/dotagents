# 02-02: Report ends with the stats summary (hub)
Status: ready
Ticket: 02
Blocked by: 01, ticket 01
Writes: tally/report.py, tests/report_test.py
Tier: low

## What to build
`report`'s output ends with one more line: `tally.stats.summary(items)`, the line `stats` prints. Call it; never copy its logic.

## Acceptance criteria
- [ ] `report.run(items)[-1] == stats.summary(items)` for any items, empty included.
- [ ] The lines before it are unchanged.
- [ ] `COMMANDS` lists `add, list, report, stats`, sorted.

## Check
`tests/report_test.py`, then the full Checks command in map.md.

## Pointers
map.md `Area: commands`; spec behaviour 3.
