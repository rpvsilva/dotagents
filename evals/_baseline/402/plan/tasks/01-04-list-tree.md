# 01-04: Children indented under their parent, with done/total
Status: ready
Ticket: 01
Blocked by: 02
Writes: todo/cli/tree.py, todo/cli/format.py, todo/cli/commands/list_cmd.py, tests/cli/test_subtask_list.py
Tier: low
Env: TODO_FILE

## What to build
`todo list` (and `todo search`) prints each child right under its parent, indented two spaces per level, at any depth. A parent's line shows ` done/total` for its direct children right after the title: `[ ] 1: trip 1/3 (due 2026-12-01)`. A child whose parent is not shown (filtered out or missing) prints at top level. A parent cycle in a hostile file never hangs `todo list` and never drops an item.

## Acceptance criteria
- [ ] New `todo/cli/tree.py`: `tree(shown) -> list[(item, depth)]`: roots are shown items whose parent is unset or not shown, children follow their parent in the shown order, every shown item appears exactly once, unreached items (cycles) print at depth 0.
- [ ] `format_list_item(item, items=(), depth=0)`: `"  " * depth` before `[`; ` done/total` right after the title when `items` holds children of the item (counted over `items`, the whole loaded list, not the filtered one); nothing when it has none. `format_list_item(item)` with no children still equals today's output (`tests/cli/test_cli.py:325`).
- [ ] `list_cmd.run` loads once, applies the filters and `--overdue`, orders through `tree`, prints `format_list_item(item, items, depth)`.
- [ ] `--sort` orders roots and each parent's children; `--open` hiding a done child still counts it in `done/total`.
- [ ] A file where 1's parent is 2 and 2's parent is 1 lists both lines once, at depth 0, and returns.
- [ ] `todo remind` and `todo export` output do not change.

## Check
`tests/cli/test_subtask_list.py` (new; write parents into the todo file directly or with `Store.add(parent=...)` once 01-03 lands, not through the CLI flag), then the full Checks command.

## Pointers
Spec behaviours 2, 3; open questions 2, 3; map.md `Area: subtasks`, `Invariants` (list line order, remind lines, export parity).
