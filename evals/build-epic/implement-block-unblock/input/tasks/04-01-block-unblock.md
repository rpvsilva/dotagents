# 04-01: todo block --on and todo unblock --on
Status: in-progress
Ticket: 04
Blocked by: none
Writes: todo/storage/blocks.py, todo/cli/commands/block.py, todo/cli/commands/unblock.py, tests/cli/block_test.py, tests/cli/registry_test.py
Tier: low
Env: TODO_FILE

## What to build
`todo block ID --on OTHER` makes ID wait on OTHER; `todo unblock ID --on OTHER` removes that. A block that would make a cycle (ID is OTHER, or OTHER already waits on ID directly or through other items) exits 2 and changes nothing. Unknown ids exit 2. Blocking twice or unblocking an absent pair changes nothing and saves nothing.

## Acceptance criteria
- [ ] New `todo/storage/blocks.py`: `block(store, item_id, on)` and `unblock(store, item_id, on)` through `Store.update`; the cycle walk over `blocked_on` keeps a seen set, so a cycle already in a hostile file cannot hang it.
- [ ] New `todo/cli/commands/block.py` and `unblock.py`, `--on` required (`type=int`), found by command discovery; print `blocked ID on OTHER` / `unblocked ID from OTHER`.
- [ ] `block 1 --on 1`, `block 1 --on 2` after `block 2 --on 1`, and `block 1 --on 3` after `block 3 --on 2`, `block 2 --on 1`: exit 2, file unchanged.
- [ ] Unknown ID or OTHER on `block`, unknown ID on `unblock`: exit 2 with `todo: error: no item N`.
- [ ] Repeated `block`/absent `unblock`: same output line, file bytes and `updated_at` unchanged.

## Check
`tests/cli/block_test.py` (new), then the full Checks command.

## Pointers
Spec behaviours 16, 19, 20; open questions 1, 6, 14; map.md `Area: item dependencies`.
