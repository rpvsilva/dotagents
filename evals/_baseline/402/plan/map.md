# Map: epic #402 (subtasks, notes, snooze, item dependencies, next)

No `notes/` (areas.md: no exploration needed); the planner read `todo/` and `tests/` directly on `main` (46b4cb8).

## Area: command registry (prefactor, #403 ticket)

- `todo/cli/commands/__init__.py`: today a hand-written `COMMANDS` dict and import line (`todo/cli/commands/__init__.py:2-16`); every new command (note, show, snooze, block, unblock, next) would append to it, chaining all tickets. Change: build `COMMANDS` by discovering the package's modules (`pkgutil.iter_modules` over `__path__`, imported with `importlib`), command name = module name with a trailing `_cmd` stripped (`list_cmd` -> `list`, `sync_cmd` -> `sync`), sorted by name. Each feature then adds only its own module.
- `todo/cli/app.py:24-26` (`build_parser`) and `todo/cli/app.py:46` read `COMMANDS` and stay as they are.
- Tests covering it: every `cli(...)` test in `tests/cli/test_cli.py` (dispatch), `tests/cli/test_cli.py:141` (`test_usage_error_exits_2` patches `COMMANDS["list"]`).
- Tests to add: `tests/cli/test_registry.py`: the discovered names equal the current eleven commands; `list_cmd`/`sync_cmd` map to `list`/`sync`.
- `tests/cli/test_registry.py` is a hub file (`Lanes`): no feature task edits it, not even the tasks that add a command (02-01, 02-02, 03-01, 04-01, 05-01), so a hub task's sync of the base never conflicts there. The file keeps the sorted + subset form from 01-01 on (`assert list(COMMANDS) == sorted(COMMANDS)`, `assert set(names) <= set(COMMANDS)`), never an exact `assert list(COMMANDS) == names`, so a new command passes before its name is listed. Each hub task (03-03, 04-03, 05-02), after its sync, sets the `names` line to every command on its branch, sorted. No code is copied between tickets.

## Area: item model and JSON form (prefactor, #403 ticket)

- `todo/storage/model.py:188-208` (`Item`): add `parent: int | None = None`, `notes: list[Note]` (default empty), `snoozed_until: date | None = None`, `blocked_on: list[int]` (default empty); add a frozen `Note(at: datetime, text: str)`, `MAX_NOTES = 100`, `MAX_NOTE_CHARS = 1000`, `normalize_note(text)` (via `normalize_title`, `todo/storage/model.py:56`, then length 1..1000), and the shared predicates `is_snoozed(item, today)` (open, `snoozed_until` set and `today < snoozed_until`) and `waiting_on(item, items)` (for an open item, the ids in `blocked_on` of items in `items` that are open, in ascending order). Snooze (#405), dependencies (#406) and next (#407) all read these two, so they are built once here.
- `todo/storage/json_store.py:22-91` (`item_from_row`): parse and check each new field from a possibly hostile file (`todo/storage/json_store.py:25-26` docstring): `parent` an int 1..`MAX_ID` (not a bool) and not the item's own id; `notes` a list of at most 100 objects with exactly `at` (ISO 8601, naive taken as UTC, like `updated_at` at `todo/storage/json_store.py:49-59`) and `text` (1..1000 characters, non-printable as `?`); `snoozed_until` a strict date (`parse_date`); `blocked_on` a list of ints 1..`MAX_ID`, not its own id, duplicates dropped keeping order. Errors are `StoreError` naming the item, like the existing ones.
- `todo/storage/json_store.py:125-133` (`item_to_row`): write `notes` as `[{"at": iso, "text": ...}]`, `snoozed_until` as `YYYY-MM-DD`; omit `parent`, `notes`, `snoozed_until`, `blocked_on` when empty, as it omits the recurrence keys today, so a file with none of the new features keeps its exact old shape (`tests/storage/test_storage.py:379` checks the omitted recurrence keys).
- `todo/storage/base.py`: add `Store.update(item_id, change)`: load, find the item or raise `ItemNotFound`, call `change(item, items)`; when it returns true stamp `updated_at = self.clock()` and save, else save nothing; an exception from `change` saves nothing. Notes, snooze and block logic go through it, each in its own module (`todo/storage/notes.py`, `snooze.py`, `blocks.py`), so no ticket after the first edits `base.py`. Also apply the `Field matrix` in `complete` (`todo/storage/base.py:88-125`: the done item's `snoozed_until` cleared; the next occurrence copies `parent` and `blocked_on`, leaves `notes` empty and `snoozed_until` None) and `skip` (`todo/storage/base.py:127-151`: `snoozed_until` cleared, the rest kept).
- `todo/storage/__init__.py`: export `Note`, `is_snoozed`, `waiting_on`, `MAX_NOTES`, `MAX_NOTE_CHARS`, `normalize_note`.
- `todo/sync/merge.py`: no change. New fields travel inside the whole item (`todo/sync/merge.py:1-6`, `replace(...)` at `todo/sync/merge.py:76` keeps them; equality at `todo/sync/merge.py:103-106` compares them).
- Tests covering it: `tests/storage/test_storage.py` (row shape, bad stored values, complete/skip), `tests/sync/test_sync.py` (whole-item merge).
- Tests to add: `tests/storage/test_new_fields.py`: round trip of each field; old-shape rows unchanged; each hostile value raises `StoreError` naming the item; `complete`/`skip` follow the Field matrix; a sync where the later side added a note, a snooze, a parent or a block keeps it.

## Area: subtasks (#403)

- `todo/storage/base.py:47-86` (`add`): new `parent: int | None` argument; an unknown parent raises `ItemNotFound` before anything is saved.
- `todo/cli/commands/add.py`: `--parent ID` (`type=int`); catch `ItemNotFound` and raise `UsageError("no item ID")` (exit 2, `todo/cli/app.py:48-49`), so nothing is added.
- `todo/cli/tree.py` (new): order the shown items as a tree, returning `(item, depth)` pairs: a root is a shown item whose parent is unset or not shown; children follow their parent in the shown order; every shown item appears exactly once, and an item left unreached (a parent cycle in a hostile file) prints at depth 0, so `todo list` never hangs or drops an item.
- `todo/cli/format.py:20-26` (`format_list_item`): becomes `format_list_item(item, items=(), depth=0)`: two spaces per depth level before `[`; when the item has children in `items` (the whole loaded list, not the filtered one), ` done/total` of its direct children right after the title, before ` (due ...)`. `format_item` and `format_remind_item` stay as they are, so `todo remind` lines do not change.
- `todo/cli/commands/list_cmd.py:12-15` (`run`): load once, filter (including `--overdue`), pass the shown items through `tree.py`, print each with `format_list_item(item, items, depth)`. `todo search` runs through it (`todo/cli/commands/search.py:16-17`).
- Tests covering it: `tests/cli/test_cli.py:22-36`, `:301`, `:432-525` (list lines and filters), `:325` (`format_list_item` equals `format_item` without extras: still true with no children).
- Tests to add: `tests/cli/test_subtask_add.py`, `tests/cli/test_subtask_list.py`.

## Area: notes (#404)

- `todo/storage/notes.py` (new): `add_note(store, item_id, text)`: `normalize_note`, at most `MAX_NOTES`, appends `Note(at=store.clock(), text)` through `Store.update`.
- `todo/cli/commands/note.py` (new): `todo note ID TEXT`; unknown ID and bad text exit 2 (`UsageError`); prints `noted ID`.
- `todo/cli/commands/show.py` (new): `todo show ID`; the lines of spec open question 12; unknown ID exits 2. Times via `datetime.astimezone()` (local), `YYYY-MM-DD HH:MM`.
- Tests covering it: none (new commands).
- Tests to add: `tests/cli/test_note.py`, `tests/cli/test_show.py`.

## Area: snooze (#405)

- `todo/storage/snooze.py` (new): `snooze(store, item_id, until, today)`: `ValueError` on a done item or `until <= today`; sets `snoozed_until` through `Store.update`.
- `todo/cli/commands/snooze.py` (new): `todo snooze ID --until DATE` (required, `type=due_date` from `todo/cli/commands/add.py:8-15`); unknown ID, done item, past date exit 2; prints `snoozed ID until DATE`.
- `todo/cli/select.py:9-46`: `--snoozed` joins `add_filter_arguments`; `matches(item, args, today)` and `select_items(items, args, today)` hide `is_snoozed` items unless `--snoozed`, which shows only them.
- `todo/cli/commands/list_cmd.py:12-15`, `todo/cli/commands/export.py:29`: pass `ctx.today` to `select_items`.
- `todo/cli/commands/remind.py:11-13`: drop `is_snoozed` items from both groups.
- `todo/cli/commands/search.py:9-14` copies `todo list`'s defaults, so it hides snoozed items with no edit; checked in the hub task.
- Tests covering it: `tests/cli/test_cli.py:75-117` (remind, overdue), `:432-525` (filters), `tests/export/test_export.py` (export filters).
- Tests to add: `tests/cli/test_snooze_cmd.py`, `tests/cli/test_snooze_hide.py`.

## Area: item dependencies (#406)

- `todo/storage/blocks.py` (new): `block(store, item_id, on)`: `ItemNotFound` for either id; `ValueError` when `on == item_id` or `item_id` is reachable from `on` through `blocked_on` (a walk with a seen set, so a cycle already in a hostile file cannot hang it); a pair already there changes nothing (no save, no stamp). `unblock(store, item_id, on)`: removes `on`; an absent pair changes nothing; `ItemNotFound` for an unknown `item_id`.
- `todo/cli/commands/block.py`, `todo/cli/commands/unblock.py` (new): `--on OTHER` required (`type=int`); unknown ids and cycles exit 2; print `blocked ID on OTHER` / `unblocked ID from OTHER`.
- `todo/cli/format.py` (`format_list_item`, as 01-04 leaves it): ` waiting on 3,5` from `waiting_on(item, items)` after the ` ↻ every` suffix and before the priority marker. `todo done OTHER` clears it with no extra code, since a done blocker is not waiting.
- Tests covering it: `tests/cli/test_cli.py:301`, `:605-616` (suffix order).
- Tests to add: `tests/cli/test_block.py`, `tests/cli/test_waiting_mark.py`.

## Area: next (#407)

- `todo/cli/commands/next.py` (new): candidates are open items with `not is_snoozed(i, today)` and `not waiting_on(i, items)`; key `(not is_overdue, due is None, due or date.max, priority rank, id)` (priority rank as `todo/cli/select.py:10`); prints `format_list_item(best, items)` (depth 0), or `nothing to do`; exit 0 either way.
- Tests to add: `tests/cli/test_next.py`.

## Area: README (hub)

- `README.md` `## Usage` (`README.md:7-45`) and the prose under it: one line per new command and flag, the list-line suffix order, the `todo next` ranking. `## Known limits`: older versions stop on a file holding a parent, notes, a snooze or blocks (unknown fields, `todo/storage/json_store.py:31-33`).

## Checks

```
~/.agents/bin/capped -- python -I -m pytest -q -c pyproject.toml
```

From `.github/workflows/ci.yml` (the last `run:` step) and `AGENTS.md`; it always runs the full suite. With the run's environment activated, `python` is the venv's.

Setup:

```
python3 -m venv "$BUILD_EPIC_ENV"
"$BUILD_EPIC_ENV/bin/pip" install --require-hashes -r requirements-ci.txt
```

## Invariants

Done when each rule an earlier feature set and a new task could break is one line here.

- Exit codes: `UsageError` exits 2 with `todo: error: <msg>` (`todo/cli/app.py:48-49`); `ItemNotFound` from existing commands exits 1 with `todo: error: no item N` (`todo/cli/app.py:50-51`, tested at `tests/cli/test_cli.py:131`). New commands and `--parent`/`--on` catch `ItemNotFound` themselves and raise `UsageError(f"no item {id}")`, so they exit 2 with the same text; `main` stays unchanged.
- A bad todo file is `StoreError`, exit 1 (`todo/cli/app.py:52-53`); commands re-raise it before catching `ValueError` (`todo/cli/commands/add.py:51-52`), since `StoreError` is a `ValueError` (`todo/storage/model.py:35`).
- A refused command saves nothing: validation runs before `save` (`todo/storage/base.py:62-85`, tested at `tests/cli/test_cli.py:56`).
- Every change stamps `updated_at = self.clock()` (`todo/storage/base.py:149,170,182,208`); sync's last-write-wins needs it (`todo/sync/merge.py:1-6`). A no-op (block twice, unblock absent) saves nothing and stamps nothing.
- Stored rows from a remote are hostile: every field is type- and range-checked in `item_from_row` and fails as `StoreError` naming the item (`todo/storage/json_store.py:22-33`); unknown keys fail (`todo/storage/json_store.py:31-33`).
- Empty new fields are omitted from the row, as `todo/storage/json_store.py:129-132` omits the recurrence keys, so an untouched file keeps its shape for older versions.
- Text that reaches the terminal goes through `normalize_title` (non-printable as `?`, `todo/storage/model.py:56-60`); messages quoting stored values use `show` (`todo/storage/model.py:26-31`).
- `todo list` line order: `[ ] ID: title`, then ` done/total` (new), ` (due D)`, ` ↻ every R`, ` waiting on A,B` (new), the priority marker, `#tags` (`todo/cli/format.py:5-26`, `README.md:25-35`, tested at `tests/cli/test_cli.py:605-616`).
- `todo remind` lines stay `format_remind_item` (no priority, tags, count or mark; `todo/cli/format.py:12-17`, tested at `tests/cli/test_cli.py:312`).
- `todo export` exports exactly what `todo list` shows with the same filters, and its CSV columns and Markdown lines do not change (`README.md:45`, `todo/export/csv_export.py:7`).
- `todo search TEXT` equals `todo list --search TEXT` (`todo/cli/commands/search.py:1-14`, tested at `tests/cli/test_cli.py:518`).
- Sync merges whole items and adds no per-field logic (`todo/sync/merge.py:1-6`; spec Out of scope).
- Writes go through `JsonStore.save`'s temp file and `os.replace` (`todo/storage/json_store.py:184-196`); no command writes the todo file another way.
- Package code never exits or replaces the process and never imports `pytest`/`_pytest`/`pluggy` (`AGENTS.md`); new commands report through `UsageError`, not `SystemExit`.
- `todo` user-facing docs stay current with each command and flag (`AGENTS.md`: keep `README.md` current).
- `tests/cli/test_registry.py` keeps the sorted + subset form (`assert list(COMMANDS) == sorted(COMMANDS)`, `assert set(names) <= set(COMMANDS)`), never an exact `list(COMMANDS) == names`, so a new command passes before its name is listed; it is a hub file, and only hub tasks edit its `names` line, after a sync.

## Field matrix

Columns are the commands that create, copy or move items. `add` creates (keep = from the command's input, reset = a fresh value, drop = left empty); `done` copies the item into its next occurrence (recurring items only; the done item itself only gets `done`, `updated_at` and a cleared `snoozed_until`); `skip` moves the item in place to its next occurrence; `sync` copies the winning item whole (`every` and `anchor_day` can still be cleared by stop-wins, `todo/sync/merge.py:72-80`).

| field | add | done | skip | sync |
| --- | --- | --- | --- | --- |
| id | reset | reset | keep | keep |
| title | keep | keep | keep | keep |
| done | reset | reset | keep | keep |
| due | keep | reset | reset | keep |
| updated_at | reset | reset | reset | keep |
| tags | keep | keep | keep | keep |
| priority | keep | keep | keep | keep |
| every | keep | keep | keep | keep |
| anchor_day | drop | keep | keep | keep |
| series_id | reset | keep | keep | keep |
| occurrence | reset | reset | reset | keep |
| parent | keep | keep | keep | keep |
| notes | drop | drop | ? | keep |
| snoozed_until | drop | reset | reset | keep |
| blocked_on | drop | keep | keep | keep |

The `?` (notes x skip) is spec open question 10 [keep].

## Test isolation

Done when every `Env:` name in a task has a line here.

- `TODO_FILE`: set to `tmp_path/todo.json` per test by the `cli` fixture in `tests/conftest.py` (pytest's `monkeypatch`, undone after each test).
- `TODO_REMOTE`: set per test with `monkeypatch.setenv` in `tests/sync/test_sync.py`; sync tests of new fields use `JsonStore` pairs on `tmp_path` instead.
- `Store.clock`: a class attribute; tests replace it with `monkeypatch.setattr(Store, "clock", ...)` (undone after the test, as `tests/cli/test_cli.py:248`) or pass `clock=` to one `JsonStore` (`tests/storage/test_storage.py:113`); never assigned on the class directly.
- `today`: no global; the `cli` fixture in `tests/conftest.py` passes `today=` to `main`, which puts it on `Context.today` (`todo/cli/app.py:41-45`); snooze, remind and next tests always pass it.
- `TZ` (local time zone): read only, by `datetime.astimezone()` in `todo show`; tests build the expected text with the same call and never set `TZ` or call `time.tzset`, so nothing needs resetting.

## Gaps

- No tests for `todo -h` output; the registry prefactor changes command order in help (spec open question 13 [name order]).
- Children of a recurring parent stay under the done occurrence; the next occurrence starts with no children. Items blocked on a recurring item stop waiting once it is done, and do not move to its next occurrence (blocks name ids). Assumed fine, consistent with spec open question 8.
- An item's `parent` or `blocked_on` may name an id that no longer exists (a remote that dropped it, or an id collision on sync, `README.md` Known limits); a missing parent lists at top level and a missing blocker does not count (spec open questions 2, 6).
- Sync id collisions (`todo/sync/merge.py:7-8`) can make a child point at an unrelated item; known limit, unchanged.
- Spec open questions 1-14 each carry an assumed answer in `spec.md`.
- Every ticket adds files under `tests/`, so by `AGENTS.md` each PR goes to Rui to approve and merge; the run's merges into `@BASE@` follow `decisions.md`.

## Lanes

- Lane 1: 01 -> 02, 03, 04, 05. Tickets 02-05 need ticket 01's prefactors (command discovery, the item fields, `Store.update`, `is_snoozed`, `waiting_on`, `format_list_item(item, items, depth)`), so they start once 01 is merged, then build in parallel: their feature tasks write disjoint files (02: `todo/storage/notes.py`, `todo/cli/commands/note.py`, `show.py`; 03: `todo/storage/snooze.py`, `todo/cli/commands/snooze.py`, `todo/cli/select.py`, `list_cmd.py`, `export.py`, `remind.py`; 04: `todo/storage/blocks.py`, `block.py`, `unblock.py`, `todo/cli/format.py`; 05: `todo/cli/commands/next.py`). Only their hub tasks wait, each for the ticket before it.

`tests/cli/test_registry.py` is a hub file beside `README.md`: no feature task edits it, and each hub task (03-03, 04-03, 05-02) lists it in `Writes` and edits it after its sync. Hub tasks run one at a time in the hub order, so its overlaps are never in flight together.

Hub files: `README.md`, `tests/cli/test_registry.py`
Hub order: 01, 02, 03, 04, 05
