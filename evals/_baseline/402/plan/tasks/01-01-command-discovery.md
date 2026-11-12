# 01-01: Find commands from their own modules (prefactor)
Status: ready
Ticket: 01
Blocked by: none
Writes: todo/cli/commands/__init__.py, tests/cli/test_registry.py
Tier: low

## What to build
`COMMANDS` stops being a hand-written table. `todo/cli/commands/__init__.py` discovers every module in its package (`pkgutil.iter_modules(__path__)`, imported with `importlib.import_module`), names each command after its module with a trailing `_cmd` stripped (`list_cmd` -> `list`, `sync_cmd` -> `sync`), and keeps them sorted by name. A new command is then one new file, so tickets 02-05 never edit a shared table. Users see no change except `todo -h` listing commands in name order.

## Acceptance criteria
- [ ] `sorted(COMMANDS)` is exactly `add, done, export, list, priority, remind, search, skip, stop, sync, tag`, each mapped to the same module as before.
- [ ] Every existing test passes unchanged (`tests/cli/test_cli.py:141` still patches `COMMANDS["list"]`).
- [ ] A module without `register` and `run` fails at import with a clear error naming it, not later at dispatch.
- [ ] No other file changes; `todo/cli/app.py` reads `COMMANDS` as before.

## Check
`tests/cli/test_registry.py` (new), then the full Checks command in map.md.

## Pointers
map.md `Area: command registry`; spec open question 13.
