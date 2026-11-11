# 03-01: todo snooze --until
Status: ready
Ticket: 03
Blocked by: none
Writes: todo/storage/snooze.py, todo/cli/commands/snooze.py, tests/cli/test_snooze_cmd.py
Tier: low
Env: TODO_FILE, today

## What to build
`todo snooze ID --until DATE` snoozes an open item until DATE (strict `YYYY-MM-DD`, after today). Prints `snoozed ID until DATE`. A done item, a DATE today or earlier, or an unknown ID exits 2 and saves nothing. Snoozing a snoozed item moves its date.

## Acceptance criteria
- [ ] New `todo/storage/snooze.py`: `snooze(store, item_id, until, today)` through `Store.update`; `ValueError` on a done item or `until <= today`.
- [ ] New `todo/cli/commands/snooze.py`: `--until` required, `type=due_date` (`todo/cli/commands/add.py:8-15`); found by command discovery.
- [ ] Done item: exit 2; `--until` equal to or before `today`: exit 2; malformed date: exit 2 (argparse); unknown ID: exit 2 with `todo: error: no item ID`; todo file unchanged each time.
- [ ] `updated_at` moves on a snooze.
- [ ] `todo done` on a snoozed item clears `snoozed_until` (from 01-02; asserted here through the CLI).

## Check
`tests/cli/test_snooze_cmd.py` (new; always pass `today=`), then the full Checks command.

## Pointers
Spec behaviours 11, 14; open questions 1, 5, 14; map.md `Area: snooze`.
