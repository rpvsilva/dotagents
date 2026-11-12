# 01-03: todo add --parent
Status: ready
Ticket: 01
Blocked by: 02
Writes: todo/storage/base.py, todo/cli/commands/add.py, tests/cli/test_subtask_add.py
Tier: low
Env: TODO_FILE

## What to build
`todo add TITLE --parent ID` adds an item whose parent is item ID. An unknown ID exits 2 with `todo: error: no item ID` and adds nothing. Output stays `added N: TITLE`.

## Acceptance criteria
- [ ] `todo add trip`, then `todo add tickets --parent 1` stores item 2 with `parent == 1`.
- [ ] `--parent` combines with `--due`, `--tag`, `--priority`, `--every` in any order after TITLE.
- [ ] `todo add x --parent 99` (unknown) exits 2, prints `todo: error: no item 99`, and the todo file is unchanged (or still absent).
- [ ] A done item may be a parent (assumed; nothing forbids it).
- [ ] `Store.add(..., parent=None)` raises `ItemNotFound` on an unknown parent before saving.

## Check
`tests/cli/test_subtask_add.py` (new), then the full Checks command.

## Pointers
Spec behaviours 1, 4; open question 1; map.md `Area: subtasks`, `Invariants` (exit codes, a refused command saves nothing).
