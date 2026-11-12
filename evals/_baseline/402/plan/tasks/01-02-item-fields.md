# 01-02: New item fields, their JSON form, Store.update and the shared predicates (prefactor)
Status: ready
Ticket: 01
Blocked by: none
Writes: todo/storage/model.py, todo/storage/json_store.py, todo/storage/base.py, todo/storage/__init__.py, tests/storage/test_new_fields.py
Tier: high
Env: Store.clock

## What to build
The `Item` model gains the four fields every ticket of the epic uses, built once here: `parent`, `notes` (a list of `Note(at, text)`), `snoozed_until` and `blocked_on`. The JSON store reads and checks them from a possibly hostile file and writes them only when set, so a file that uses none of them keeps its exact old shape. `Store.update(item_id, change)` gives later tickets one load-find-change-stamp-save path, so they never edit `base.py`. `is_snoozed(item, today)` and `waiting_on(item, items)` are the rules snooze, dependencies and `todo next` share. `complete` and `skip` follow map.md's `Field matrix` for the new fields.

## Acceptance criteria
- [ ] `Item(...)` has `parent: int | None = None`, `notes: list[Note]` (empty default), `snoozed_until: date | None = None`, `blocked_on: list[int]` (empty default); `Note` is a frozen dataclass `(at: datetime, text: str)`; `MAX_NOTES = 100`, `MAX_NOTE_CHARS = 1000`, `normalize_note(text)` (non-printable as `?`, then 1..1000 characters, else `ValueError`).
- [ ] Each field round-trips through `JsonStore.save`/`load`; `notes` rows are `{"at": <ISO 8601 UTC>, "text": ...}`, `snoozed_until` is `YYYY-MM-DD`.
- [ ] A row with none of the new fields set saves without the keys `parent`, `notes`, `snoozed_until`, `blocked_on`.
- [ ] Each hostile stored value raises `StoreError` naming the item: a non-int, bool, out-of-range or self `parent`; `notes` not a list, over 100, an entry not an object, with unknown keys, a bad `at`, an empty, over-long or non-text `text`; a bad `snoozed_until`; `blocked_on` not a list, holding a non-int, bool, out-of-range or own id. Duplicate `blocked_on` ids load once, first order kept. A naive `at` is taken as UTC.
- [ ] `Store.update(item_id, change)`: raises `ItemNotFound` on a missing id; `change(item, items)` returning false saves nothing and stamps nothing; returning true stamps `updated_at = self.clock()` and saves; an exception in `change` saves nothing.
- [ ] `is_snoozed(item, today)` is true only for an open item with `today < snoozed_until`. `waiting_on(item, items)` is empty for a done item, else the ascending ids in `blocked_on` of items in `items` that exist and are open.
- [ ] `complete` clears the done item's `snoozed_until`; its next occurrence keeps `parent` and `blocked_on`, has no notes and no snooze. `skip` clears `snoozed_until` and keeps `parent`, `blocked_on` and `notes`.
- [ ] A sync where the later side set each new field keeps it on both sides (whole item, no merge change).
- [ ] `todo/storage/__init__.py` exports `Note`, `MAX_NOTES`, `MAX_NOTE_CHARS`, `normalize_note`, `is_snoozed`, `waiting_on`.

## Check
`tests/storage/test_new_fields.py` (new), then the full Checks command in map.md (`tests/storage/test_storage.py:379` and `:594` keep passing: the row shape of untouched items).

## Pointers
Spec open questions 4, 5, 6, 8, 10; map.md `Area: item model and JSON form`, `Field matrix`, `Invariants` (hostile rows, omitted empty fields, stamping).
