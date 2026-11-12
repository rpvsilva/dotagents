# 03-02: Snoozed items hidden from list, search, export and remind; --snoozed
Status: ready
Ticket: 03
Blocked by: none
Writes: todo/cli/select.py, todo/cli/commands/list_cmd.py, todo/cli/commands/export.py, todo/cli/commands/remind.py, tests/cli/test_snooze_hide.py
Tier: low
Env: TODO_FILE, today

## What to build
A snoozed item (`is_snoozed(item, today)`) is hidden from `todo list`, `todo search`, `todo export` and `todo remind` until today reaches its date, when it shows again. `--snoozed`, a shared filter, shows only snoozed items.

## Acceptance criteria
- [ ] `add_filter_arguments` adds `--snoozed`; `matches(item, args, today)` and `select_items(items, args, today)` hide snoozed items, or with `--snoozed` keep only them; `list_cmd.run` and `export.run` pass `ctx.today`.
- [ ] On DATE itself (`today == snoozed_until`) the item shows again in `list` and `remind`.
- [ ] `todo remind` drops snoozed items from both groups; `nothing due` when only snoozed items were due.
- [ ] `todo search TEXT` hides snoozed items (it copies `todo list` defaults, `todo/cli/commands/search.py:9-14`).
- [ ] `todo export` with and without `--snoozed` selects exactly what `todo list` does.
- [ ] `--snoozed` combines with the other filters and the subtask tree (a snoozed parent hidden: its children list at top level).

## Check
`tests/cli/test_snooze_hide.py` (new; set `snoozed_until` with `Store.update` or a JSON row; always pass `today=`), then the full Checks command.

## Pointers
Spec behaviours 11, 12, 13; open questions 5, 11; map.md `Area: snooze`, `Invariants` (export parity, search equals list --search).
