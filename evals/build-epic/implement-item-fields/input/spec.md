# Spec: Todo interplay rerun (gh-35 subset), epic #402

## Goal

Add five features to the `todo` CLI, built from `main` into the run's base (`rerun2-local`), never into `main`: subtasks (`todo add --parent`, children indented in `todo list` with a `done/total` count on the parent), notes (`todo note`, `todo show`), snooze (`todo snooze --until`, `todo list --snoozed`, hidden from `list` and `remind`), item dependencies (`todo block --on`, `todo unblock --on`, a waiting mark in `todo list`) and `todo next` (the single best item). The five share the `Item` model in `todo/storage/model.py`, the JSON form in `todo/storage/json_store.py`, the command registry in `todo/cli/commands/__init__.py`, `todo list` output in `todo/cli/format.py` and the README's `Usage` section, so planning must order their interplay. Each issue keeps its README usage current. Specs are the same as gh-35's #46, #47, #52, #57 and #58.

## Behaviours

Subtasks (#403):
1. `todo add TITLE --parent ID` creates an item whose parent is item ID. (#403)
2. `todo list` lists each child indented under its parent. (#403)
3. A parent's `todo list` line shows `done/total`: its done children over all its children. (#403)
4. `todo add --parent ID` with an unknown ID exits 2 and adds nothing. (#403)
5. README `Usage` documents `--parent`. (#403)

Notes (#404):
6. `todo note ID TEXT` adds a note to item ID, stamped with the time it was added. (#404)
7. Notes keep the order they were added in. (#404)
8. `todo show ID` prints all of the item's fields and all its notes, in order, each with its time. (#404)
9. `todo note` and `todo show` with an unknown ID exit 2. (#404)
10. README `Usage` documents `todo note` and `todo show`. (#404)

Snooze (#405):
11. `todo snooze ID --until DATE` hides an open item from `todo list` and `todo remind` until DATE. (#405)
12. On DATE (when today reaches DATE) the item shows again in `todo list` and `todo remind`. (#405)
13. `todo list --snoozed` shows the snoozed items. (#405)
14. Snoozing a done item exits 2. (#405)
15. README `Usage` documents `todo snooze` and `--snoozed`. (#405)

Item dependencies (#406):
16. `todo block ID --on OTHER` makes item ID wait on item OTHER. (#406)
17. `todo list` marks an item that waits on an open item. (#406)
18. Finishing the blocker (`todo done OTHER`) clears the mark. (#406)
19. `todo unblock ID --on OTHER` removes the wait. (#406)
20. A block that would make a cycle (OTHER already waits on ID, directly or through other items, or ID is OTHER) is refused with exit 2, and nothing changes. (#406)
21. README `Usage` documents `todo block` and `todo unblock`. (#406)

Next (#407):
22. `todo next` prints the single best item: among open items that are not waiting and not snoozed, overdue first, then due soonest, then highest priority. (#407)
23. Ties break by lowest id. (#407)
24. With no candidate, `todo next` says so and exits 0. (#407)
25. README documents the ranking. (#407)

## Out of scope

- Any other gh-35 feature (trash, archive, doing state, timed due dates, title fragments as IDs, natural-language dates, board, plan, stats, calendar). (#402)
- Any network service. (#402)
- Changing how `todo sync` merges: new fields travel inside the whole item under the existing last-write-wins rule (`todo/sync/merge.py`), with no new merge logic. (#402)
- Merging into `main`. (#402)
- Editing or deleting notes; unsnoozing before the date. (gh-35 #47, #52 plan decisions)

## Open questions

Assumed answers carry the approved decisions of the earlier reruns (issue comments on #403-#407) and of gh-35 (#46, #47, #52, #57, #58), minus features `main` does not have. Pass B confirms them against the code.

1. Unknown ID: exit 2 for the new commands only, or everywhere? [Exit 2 for the new commands and `--parent`/`--on`; existing commands (`done`, `skip`, `stop`, `tag`, `priority`) keep exit 1. Approved for the earlier rerun.]
2. Subtask depth and counting? [Any depth, two spaces of indent per level; `done/total` counts direct children only. A child whose parent is not listed (filtered out, or missing) lists at top level. A parent cycle in a hostile todo file must never hang `todo list`.]
3. Where does `done/total` sit on the parent's line? [Right after the title part, before the due date and the other suffixes; e.g. `[ ] 1: trip 1/3 (due ...)`. Pass B fixes the exact spot against `todo/cli/format.py`.]
4. Notes format and limits? [Stored with a UTC timestamp, shown in local time; at most 100 notes per item, each 1-1000 characters; non-printable characters show as `?`. Notes travel with the item on sync as part of the whole item.]
5. `--until` rules? [Strict `YYYY-MM-DD` like `--due`; must be after today, else exit 2. Completing a snoozed item clears the snooze. Snoozed items hide from `list`, `search`, `export`, `remind` and `next`; `todo list --snoozed` shows only snoozed items. Unknown ID exits 2.]
6. Who blocks, and the mark's form? [Only an open item in the todo file blocks; a done or missing blocker does not. Mark: ` waiting on 3,5` (open blockers by id), after the rule suffix and before the priority marker. Blocking on itself exits 2; blocking twice changes nothing; unblocking a pair that is not blocked changes nothing.]
7. `todo next` output and "due soonest"? [The item printed as its `todo list` line; `nothing to do` with exit 0 when no candidate. Undated items rank after dated ones; overdue means due before today.]
8. Do subtasks, notes, snooze or blocks carry onto the next occurrence of a recurring item (`todo done`, `todo skip`)? [On the next occurrence `todo done` creates: parent and blocks keep, notes drop (they belong to the done occurrence), snooze reset. `todo skip` moves the same item: parent and blocks keep, snooze reset, notes keep (see 10). Settled in map.md's `Field matrix`.]
9. Do `todo export` CSV/Markdown gain columns for the new fields? [No; export output stays as is, apart from hiding snoozed items like `todo list`.]
10. `todo skip` on a recurring item with notes (map.md `Field matrix`: notes x skip is `?`): keep or drop them? [Keep: skip moves the same item (same id) in place, and dropping would delete the user's text with no undo.]
11. Is `--snoozed` a `todo list` flag only, or one of the shared filters (`todo/cli/select.py`) that `todo export` and `todo search` also take? [Shared filter: `todo export` keeps exporting exactly what `todo list` shows with the same flags, as the README promises.]
12. Which fields does `todo show` print? [The visible ones, one `name: value` line each, `-` when empty: id, title, done (`yes`/`no`), due, priority, tags, every, parent, snoozed until, blocked on, updated (local time); then `notes:` and one line per note, `  YYYY-MM-DD HH:MM  text` in local time, or `notes: none`. The hidden sync fields (`series_id`, `occurrence`, `anchor_day`) are not shown.]
13. Command registration: the new commands are found from their own files in `todo/cli/commands/` (prefactor 01-01), so no ticket edits a shared command table. [Yes; `todo -h` then lists commands in name order.]
14. Output lines of the new commands? [`noted ID`, `snoozed ID until DATE`, `blocked ID on OTHER`, `unblocked ID from OTHER`; `todo add --parent` keeps `added ID: TITLE`. A no-op block or unblock prints the same line and saves nothing.]
