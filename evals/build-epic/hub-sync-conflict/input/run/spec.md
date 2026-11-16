# Spec: report and stats, epic #1

## Goal

Add two commands to `tally`: `stats` (how many items are done) and `report` (the open items), and end `report` with the stats summary line.

## Behaviours

1. `python -m tally stats` prints `<done> of <total> done`. (ticket 01)
2. `python -m tally report` prints `report: <n> open`, then one `- <title>` line per open item. (ticket 02)
3. `report`'s last line is the stats summary, exactly as `stats` prints it. (ticket 02)
4. `COMMANDS` lists every command, sorted. (both)

## Open questions

None.
