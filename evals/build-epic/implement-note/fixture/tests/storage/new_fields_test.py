import json
from datetime import date, datetime, timezone

import pytest

from todo.storage import (
    MAX_NOTE_CHARS,
    MAX_NOTES,
    Item,
    ItemNotFound,
    JsonStore,
    Note,
    StoreError,
    is_snoozed,
    normalize_note,
    waiting_on,
)
from todo.sync import sync

TODAY = date(2026, 11, 10)
STAMP = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
NEW_KEYS = ("parent", "notes", "snoozed_until", "blocked_on")


def store_at(tmp_path, name="t.json", clock=lambda: STAMP):
    return JsonStore(tmp_path / name, clock=clock)


def raw_rows(store):
    return json.loads(store.path.read_text())


def write_row(tmp_path, **fields):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"id": 5, "title": "x", **fields}]))
    return JsonStore(path)


def test_defaults_are_empty():
    item = Item(1, "a")
    assert (item.parent, item.notes, item.snoozed_until, item.blocked_on) == (None, [], None, [])
    assert Item(1, "a").notes is not Item(2, "b").notes


def test_note_is_frozen():
    with pytest.raises(Exception):
        Note(STAMP, "x").text = "y"


def test_normalize_note_replaces_unprintable_and_checks_length():
    assert normalize_note("a\nb\x00") == "a?b?"
    assert normalize_note("x" * MAX_NOTE_CHARS) == "x" * MAX_NOTE_CHARS
    for bad in ("", "x" * (MAX_NOTE_CHARS + 1), 5):
        with pytest.raises(ValueError):
            normalize_note(bad)


def test_every_new_field_round_trips(tmp_path):
    store = store_at(tmp_path)
    item = Item(
        3, "a", updated_at=STAMP, parent=1, notes=[Note(STAMP, "hello")],
        snoozed_until=date(2026, 12, 1), blocked_on=[2, 1],
    )
    store.save([item])
    assert store.load() == [item]
    row = raw_rows(store)[0]
    assert row["parent"] == 1
    assert row["notes"] == [{"at": STAMP.isoformat(), "text": "hello"}]
    assert row["snoozed_until"] == "2026-12-01"
    assert row["blocked_on"] == [2, 1]


def test_untouched_row_keeps_old_shape(tmp_path):
    store = store_at(tmp_path)
    store.add("a")
    row = raw_rows(store)[0]
    assert not any(key in row for key in NEW_KEYS)


def test_naive_note_time_is_utc(tmp_path):
    store = write_row(tmp_path, notes=[{"at": "2026-11-01T12:00:00", "text": "n"}])
    assert store.load()[0].notes == [Note(STAMP, "n")]


def test_duplicate_blocked_on_loads_once_in_first_order(tmp_path):
    store = write_row(tmp_path, blocked_on=[3, 1, 3, 2, 1])
    assert store.load()[0].blocked_on == [3, 1, 2]


@pytest.mark.parametrize(
    "fields",
    [
        {"parent": "1"}, {"parent": True}, {"parent": 0}, {"parent": 2**53 + 1}, {"parent": 5},
        {"notes": "x"}, {"notes": [{"at": STAMP.isoformat(), "text": "n"}] * (MAX_NOTES + 1)},
        {"notes": ["x"]}, {"notes": [{"at": STAMP.isoformat(), "text": "n", "extra": 1}]},
        {"notes": [{"text": "n"}]}, {"notes": [{"at": "nope", "text": "n"}]},
        {"notes": [{"at": 5, "text": "n"}]},
        {"notes": [{"at": "0001-01-01T00:00:00+05:00", "text": "n"}]},
        {"notes": [{"at": "9999-12-31T23:59:59-05:00", "text": "n"}]}, {"notes": [{"at": STAMP.isoformat(), "text": ""}]},
        {"notes": [{"at": STAMP.isoformat(), "text": "x" * (MAX_NOTE_CHARS + 1)}]},
        {"notes": [{"at": STAMP.isoformat(), "text": 7}]},
        {"snoozed_until": "2026-1-1"}, {"snoozed_until": 5}, {"snoozed_until": "2026-13-01"},
        {"blocked_on": 3}, {"blocked_on": ["1"]}, {"blocked_on": [True]}, {"blocked_on": [0]},
        {"blocked_on": [2**53 + 1]}, {"blocked_on": [5]},
    ],
)
def test_hostile_value_raises_store_error_naming_the_item(tmp_path, fields):
    with pytest.raises(StoreError, match="item 5"):
        write_row(tmp_path, **fields).load()


def test_update_missing_id_raises(tmp_path):
    store = store_at(tmp_path)
    store.add("a")
    with pytest.raises(ItemNotFound):
        store.update(9, lambda item, items: True)


def test_update_false_saves_and_stamps_nothing(tmp_path):
    store = store_at(tmp_path)
    store.add("a")
    before = store.path.read_text()
    store.clock = lambda: LATER

    def change(item, items):
        item.title = "changed"
        return False

    store.update(1, change)
    assert store.path.read_text() == before


def test_update_true_stamps_and_saves(tmp_path):
    store = store_at(tmp_path)
    store.add("a")
    store.clock = lambda: LATER
    seen = []

    def change(item, items):
        seen.append((item.id, [i.id for i in items]))
        item.title = "changed"
        return True

    result = store.update(1, change)
    assert seen == [(1, [1])]
    assert result.title == "changed"
    [loaded] = store.load()
    assert (loaded.title, loaded.updated_at) == ("changed", LATER)


def test_update_exception_saves_nothing(tmp_path):
    store = store_at(tmp_path)
    store.add("a")
    before = store.path.read_text()

    def change(item, items):
        item.title = "changed"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store.update(1, change)
    assert store.path.read_text() == before


def test_is_snoozed_only_open_items_before_the_date():
    item = Item(1, "a", snoozed_until=date(2026, 11, 11))
    assert is_snoozed(item, TODAY)
    assert not is_snoozed(item, date(2026, 11, 11))
    assert not is_snoozed(item, date(2026, 11, 12))
    assert not is_snoozed(Item(1, "a", done=True, snoozed_until=date(2026, 11, 11)), TODAY)
    assert not is_snoozed(Item(1, "a"), TODAY)


def test_waiting_on_lists_open_existing_blockers_ascending():
    items = [
        Item(1, "a", done=True), Item(2, "b"), Item(3, "c"),
        Item(4, "d", blocked_on=[9, 3, 1, 2]),
    ]
    assert waiting_on(items[3], items) == [2, 3]
    assert waiting_on(Item(4, "d", done=True, blocked_on=[2]), items) == []
    assert waiting_on(items[1], items) == []


def test_complete_clears_snooze_and_next_occurrence_keeps_parent_and_blocks(tmp_path):
    store = store_at(tmp_path)
    store.add("p")
    store.add("r", every="week", due=date(2026, 11, 10))
    items = store.load()
    r = items[1]
    r.parent, r.blocked_on, r.snoozed_until = 1, [1], date(2026, 12, 1)
    r.notes = [Note(STAMP, "n")]
    store.save(items)

    nxt = store.complete(2, today=TODAY)

    done = store.load()[1]
    assert done.done and done.snoozed_until is None and done.notes == [Note(STAMP, "n")]
    assert (nxt.parent, nxt.blocked_on, nxt.notes, nxt.snoozed_until) == (1, [1], [], None)
    assert nxt.blocked_on is not done.blocked_on
    assert store.load()[2] == nxt


def test_skip_clears_snooze_and_keeps_parent_blocks_and_notes(tmp_path):
    store = store_at(tmp_path)
    store.add("p")
    store.add("r", every="week", due=date(2026, 11, 10))
    items = store.load()
    r = items[1]
    r.parent, r.blocked_on, r.snoozed_until = 1, [1], date(2026, 12, 1)
    r.notes = [Note(STAMP, "n")]
    store.save(items)

    skipped = store.skip(2, today=TODAY)

    assert skipped.snoozed_until is None
    assert (skipped.parent, skipped.blocked_on, skipped.notes) == (1, [1], [Note(STAMP, "n")])
    assert store.load()[1] == skipped


@pytest.mark.parametrize(
    "field,value",
    [
        ("parent", 1),
        ("notes", [Note(LATER, "later")]),
        ("snoozed_until", date(2026, 12, 1)),
        ("blocked_on", [1]),
    ],
)
def test_sync_keeps_a_field_the_later_side_set(tmp_path, field, value):
    local = store_at(tmp_path, "local.json")
    remote = store_at(tmp_path, "remote.json")
    local.save([Item(1, "p", updated_at=STAMP), Item(2, "a", updated_at=STAMP)])
    remote.save(local.load())
    items = remote.load()
    setattr(items[1], field, value)
    items[1].updated_at = LATER
    remote.save(items)

    sync(local, remote)

    for side in (local, remote):
        assert getattr(side.load()[1], field) == value
