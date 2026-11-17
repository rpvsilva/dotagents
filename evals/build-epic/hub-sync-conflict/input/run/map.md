# Map: epic #1 (report and stats)

## Area: commands

- `tally/commands.py`: `COMMANDS`, one sorted list of command names. Each ticket adds its command's name to it.
- `tally/<command>.py`: one module per command, `run(items) -> list[str]`.
- Tests: `tests/<name>_test.py`.

## Checks

```
python3 -I -m pytest -q -c pyproject.toml
```

Setup: none (the run has no network: the `python3` on PATH holds pytest).

## Lanes

- Lane 1: 01, 02. Ticket 02's feature task (02-01) writes only its own files plus its `COMMANDS` entry; its hub task (02-02) needs ticket 01's `stats.summary`, so it waits for ticket 01 to merge and syncs `main` first.

Hub files: `tally/commands.py`
Hub order: 01, 02
