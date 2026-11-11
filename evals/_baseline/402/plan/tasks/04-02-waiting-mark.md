# 04-02: The waiting mark in todo list
Status: ready
Ticket: 04
Blocked by: none
Writes: todo/cli/format.py, tests/cli/test_waiting_mark.py
Tier: low
Env: TODO_FILE

## What to build
`todo list` marks an open item that waits on open items: ` waiting on 3,5` after the ` ↻ every` suffix and before the priority marker. Finishing a blocker (`todo done OTHER`) drops it from the mark; the mark goes when none is open. A missing or done blocker does not count.

## Acceptance criteria
- [ ] `format_list_item(item, items, depth)` adds ` waiting on ` plus `waiting_on(item, items)` joined by `,`, when non-empty, in the place above; nothing else in the line moves (`tests/cli/test_cli.py:605-616` pass).
- [ ] `[ ] 2: pack (due 2026-12-01) ↻ every week waiting on 1 !! #trip` shape for an item with every suffix.
- [ ] After `todo done 1` the mark is gone; with two blockers it shows the open one only.
- [ ] A done item shows no mark.
- [ ] `todo remind` and `todo export` lines do not change.

## Check
`tests/cli/test_waiting_mark.py` (new; blocks written with `Store.update` or a JSON row, not through `todo block`), then the full Checks command.

## Pointers
Spec behaviours 17, 18; open question 6; map.md `Area: item dependencies`, `Invariants` (list line order).
