import json
import stat
from datetime import date, datetime, timedelta, timezone

import pytest

from todo.storage import (
    MAX_TAGS,
    PRIORITIES,
    Item,
    ItemNotFound,
    NotRecurring,
    JsonStore,
    Store,
    StoreError,
    is_due_soon,
    is_overdue,
    normalize_tag,
    format_rule,
    next_due,
    normalize_tags,
    parse_priority,
    parse_rule,
)

TODAY = date(2026, 11, 10)


def test_store_is_an_interface():
    with pytest.raises(TypeError):
        Store()
    assert isinstance(JsonStore("x.json"), Store)


def test_store_roundtrip(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a")
    assert [i.title for i in store.load()] == ["a"]


def test_store_roundtrip_with_due(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a", due=date(2026, 11, 1))
    store.add("b")
    assert '"due": "2026-11-01"' in (tmp_path / "t.json").read_text()
    assert [i.due for i in store.load()] == [date(2026, 11, 1), None]


def test_save_keeps_file_mode(tmp_path):
    # A shared remote stays readable by the group after one user saves it.
    path = tmp_path / "t.json"
    path.write_text("[]")
    path.chmod(0o664)
    JsonStore(path).add("a")
    assert path.stat().st_mode & 0o777 == 0o664


def test_save_drops_special_mode_bits(tmp_path):
    # Whoever owns a shared remote cannot make our saved copy setuid or executable.
    path = tmp_path / "t.json"
    path.write_text("[]")
    path.chmod(0o6777)
    JsonStore(path).add("a")
    assert stat.S_IMODE(path.stat().st_mode) == 0o666


def test_no_follow_store_refuses_symlink(tmp_path):
    # A symlink planted at a shared remote must not make us read the file it points to.
    target = tmp_path / "private.json"
    target.write_text('[{"id": 1, "title": "secret", "done": false}]')
    link = tmp_path / "remote.json"
    link.symlink_to(target)
    with pytest.raises(StoreError, match="symlink"):
        JsonStore(link, follow_symlinks=False).load()
    assert [i.title for i in JsonStore(link).load()] == ["secret"]


def test_old_format_file_loads_without_due(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "done": false}]')
    [item] = JsonStore(path).load()
    assert item.title == "milk" and item.due is None


def test_invalid_stored_due_raises_store_error(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 7, "title": "milk", "done": false, "due": "2026-1-5"}]')
    with pytest.raises(StoreError, match="item 7.*'2026-1-5'.*YYYY-MM-DD"):
        JsonStore(path).load()


def test_complete_unknown_id(tmp_path):
    with pytest.raises(KeyError):
        JsonStore(tmp_path / "t.json").complete(1)


def test_is_overdue():
    assert is_overdue(Item(1, "a", due=date(2026, 11, 9)), TODAY)
    assert not is_overdue(Item(1, "a", due=TODAY), TODAY)
    assert not is_overdue(Item(1, "a", done=True, due=date(2026, 11, 9)), TODAY)
    assert not is_overdue(Item(1, "a"), TODAY)


def test_is_due_soon():
    assert is_due_soon(Item(1, "a", due=TODAY), TODAY)
    assert is_due_soon(Item(1, "a", due=date(2026, 11, 13)), TODAY)
    assert not is_due_soon(Item(1, "a", due=date(2026, 11, 14)), TODAY)
    assert not is_due_soon(Item(1, "a", due=date(2026, 11, 9)), TODAY)
    assert not is_due_soon(Item(1, "a", done=True, due=TODAY), TODAY)
    assert not is_due_soon(Item(1, "a"), TODAY)


def fixed_clock(*stamps):
    it = iter(stamps)
    return lambda: next(it)


def test_add_and_complete_stamp_updated_at(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2))
    store.add("a")
    assert store.load()[0].updated_at == t1
    store.complete(1)
    assert store.load()[0].updated_at == t2
    assert '"updated_at": "2026-11-02T09:00:00+00:00"' in (tmp_path / "t.json").read_text()


def test_naive_updated_at_loads_as_utc(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "a", "updated_at": "2026-11-01T09:00:00"}]')
    assert JsonStore(path).load()[0].updated_at == datetime(2026, 11, 1, 9, tzinfo=timezone.utc)


def test_invalid_updated_at_raises_store_error(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 3, "title": "a", "updated_at": "yesterday"}]')
    with pytest.raises(StoreError, match="item 3.*'yesterday'"):
        JsonStore(path).load()


def test_normalize_tags():
    assert normalize_tag(" Work ") == "work"
    assert normalize_tags(["b", "A", "a", " b "]) == ["b", "a"]
    for bad in ["", "   ", "a b", "a\tb"]:
        with pytest.raises(ValueError):
            normalize_tag(bad)
    with pytest.raises(ValueError):
        normalize_tags([str(n) for n in range(MAX_TAGS + 1)])
    assert len(normalize_tags([str(n) for n in range(MAX_TAGS)] + ["0"])) == MAX_TAGS


def test_store_roundtrip_with_tags(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a", tags=["groceries", "home"])
    store.add("b")
    assert [i.tags for i in JsonStore(tmp_path / "t.json").load()] == [["groceries", "home"], []]


def test_add_normalises_tags(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    assert store.add("a", tags=[" Work ", "work", "home"]).tags == ["work", "home"]


def test_old_format_file_loads_without_tags(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "done": false}]')
    assert JsonStore(path).load()[0].tags == []


def test_null_tags_load_as_empty(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "tags": null}]')
    assert JsonStore(path).load()[0].tags == []


def test_stored_tags_are_normalised(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "tags": ["Work", "work"]}]')
    assert JsonStore(path).load()[0].tags == ["work"]


@pytest.mark.parametrize(
    "tags",
    ['"work"', '{"a": 1}', "[1]", '[null]', '["a b"]', '[""]', '["  "]',
     "[" + ", ".join(f'"t{n}"' for n in range(11)) + "]"],
)
def test_invalid_stored_tags_raise_store_error(tmp_path, tags):
    path = tmp_path / "t.json"
    path.write_text(f'[{{"id": 4, "title": "milk", "tags": {tags}}}]')
    with pytest.raises(StoreError, match="item 4"):
        JsonStore(path).load()


def test_parse_priority():
    assert PRIORITIES == ("high", "medium", "low")
    for word in PRIORITIES:
        assert parse_priority(word) == word
    assert parse_priority(None) is None
    for bad in ["urgent", "", "HIGH", 1]:
        with pytest.raises(ValueError):
            parse_priority(bad)


def test_old_format_file_loads_without_priority(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "done": false}]')
    assert JsonStore(path).load()[0].priority is None


def test_store_roundtrip_with_priority(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a", priority="high")
    store.add("b")
    assert [i.priority for i in JsonStore(tmp_path / "t.json").load()] == ["high", None]


@pytest.mark.parametrize("priority", ['"urgent"', '""', "1", '["high"]'])
def test_invalid_stored_priority_raises_store_error(tmp_path, priority):
    path = tmp_path / "t.json"
    path.write_text(f'[{{"id": 5, "title": "rent", "priority": {priority}}}]')
    with pytest.raises(StoreError, match="item 5"):
        JsonStore(path).load()


def test_set_priority_stamps_updated_at(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2, t3))
    store.add("a")
    store.set_priority(1, "medium")
    item = store.load()[0]
    assert (item.priority, item.updated_at) == ("medium", t2)
    store.set_priority(1, None)
    item = store.load()[0]
    assert (item.priority, item.updated_at) == (None, t3)


def test_set_priority_rejects_bad_value_and_unknown_id(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a")
    with pytest.raises(ValueError):
        store.set_priority(1, "urgent")
    with pytest.raises(KeyError):
        store.set_priority(99, "high")


def test_update_tags_adds_removes_and_stamps_updated_at(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2))
    store.add("a", tags=["home", "x"])
    item = store.update_tags(1, add=[" Work "], remove=["home", "absent"])
    assert item.tags == ["x", "work"]
    loaded = store.load()[0]
    assert loaded.tags == ["x", "work"]
    assert loaded.updated_at == t2


def test_update_tags_unknown_id(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    with pytest.raises(KeyError):
        store.update_tags(99, add=["a"], remove=[])


def test_update_tags_over_max_saves_nothing(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a", tags=[f"t{n}" for n in range(MAX_TAGS)])
    before = (tmp_path / "t.json").read_text()
    with pytest.raises(ValueError):
        store.update_tags(1, add=["one-more"], remove=[])
    assert (tmp_path / "t.json").read_text() == before


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.complete(99),
        lambda s: s.update_tags(99, add=["a"], remove=[]),
        lambda s: s.set_priority(99, "high"),
    ],
)
def test_missing_id_raises_item_not_found(tmp_path, call):
    store = JsonStore(tmp_path / "t.json")
    store.add("a")
    with pytest.raises(ItemNotFound) as exc:
        call(store)
    assert isinstance(exc.value, KeyError)
    assert exc.value.args == (99,)


def test_update_tags_add_and_remove_same_tag_saves_nothing(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a", tags=["x"])
    before = (tmp_path / "t.json").read_text()
    with pytest.raises(ValueError, match="both"):
        store.update_tags(1, add=["X"], remove=[" x "])
    assert (tmp_path / "t.json").read_text() == before


@pytest.mark.parametrize(
    "text, rule",
    [
        ("day", (1, "day")),
        ("days", (1, "day")),
        ("week", (1, "week")),
        ("month", (1, "month")),
        ("months", (1, "month")),
        ("year", (1, "year")),
        ("2 weeks", (2, "week")),
        ("3 month", (3, "month")),
        ("1 day", (1, "day")),
        ("  2   Weeks ", (2, "week")),
    ],
)
def test_parse_rule_accepts_units_with_optional_count(text, rule):
    assert parse_rule(text) == rule


@pytest.mark.parametrize(
    "text", ["fortnight", "0 days", "-1 week", "2", "", "2 weeks later", "1.5 days", None]
)
def test_parse_rule_rejects_unknown_unit_or_non_positive_count(text):
    with pytest.raises(ValueError):
        parse_rule(text)


@pytest.mark.parametrize(
    "rule, text",
    [((1, "month"), "month"), ((2, "week"), "2 weeks"), ((1, "day"), "day"), ((5, "year"), "5 years")],
)
def test_format_rule(rule, text):
    assert format_rule(*rule) == text
    assert parse_rule(text) == rule


def test_item_is_not_recurring_by_default():
    assert Item(1, "a").every is None


def test_store_roundtrip_with_rule(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=date(2026, 11, 1), every="months", today=TODAY)
    store.add("water", due=date(2026, 11, 1), every="2 week", today=TODAY)
    assert [i.every for i in JsonStore(tmp_path / "t.json").load()] == ["month", "2 weeks"]


def test_add_recurring_without_due_is_due_today(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    item = store.add("rent", every="month", today=TODAY)
    assert item.due == TODAY
    assert store.load()[0].due == TODAY


def test_add_recurring_keeps_given_due(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    assert store.add("rent", due=date(2026, 12, 1), every="month", today=TODAY).due == date(2026, 12, 1)


def test_add_one_off_without_due_stays_undated(tmp_path):
    assert JsonStore(tmp_path / "t.json").add("milk", today=TODAY).due is None


def test_add_bad_rule_saves_nothing(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    with pytest.raises(ValueError):
        store.add("rent", every="fortnight", today=TODAY)
    assert store.load() == []


def test_old_format_file_loads_without_rule(tmp_path):
    path = tmp_path / "t.json"
    path.write_text('[{"id": 1, "title": "milk", "done": false, "due": "2026-11-01"}]')
    [item] = JsonStore(path).load()
    assert item.every is None


def test_one_off_item_saves_no_recurrence_keys(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk", due=date(2026, 11, 1), tags=["home"], priority="high")
    store.add("rent", every="month", today=TODAY)
    [one_off, recurring] = json.loads((tmp_path / "t.json").read_text())
    assert "every" not in one_off
    assert "series_id" not in one_off and "occurrence" not in one_off
    assert recurring["every"] == "month"


@pytest.mark.parametrize("every", ['"fortnight"', '"0 days"', "3", "[]"])
def test_invalid_stored_rule_raises_store_error(tmp_path, every):
    path = tmp_path / "t.json"
    path.write_text(f'[{{"id": 4, "title": "rent", "done": false, "every": {every}}}]')
    with pytest.raises(StoreError, match="item 4"):
        JsonStore(path).load()


D = date


@pytest.mark.parametrize(
    "due, every, today, expected",
    [
        (D(2026, 11, 10), "day", D(2026, 11, 10), D(2026, 11, 11)),  # done late on its day
        (D(2026, 11, 9), "day", D(2026, 11, 10), D(2026, 11, 10)),  # overdue: lands on today
        (D(2026, 11, 2), "week", D(2026, 11, 2), D(2026, 11, 9)),
        (D(2026, 11, 2), "2 weeks", D(2026, 11, 2), D(2026, 11, 16)),
        (D(2026, 8, 15), "month", D(2026, 11, 10), D(2026, 11, 15)),  # overdue, skips Sep/Oct
        (D(2026, 3, 1), "year", D(2026, 3, 1), D(2027, 3, 1)),
    ],
)
def test_next_due_steps_interval_until_today_or_later(due, every, today, expected):
    assert next_due(due, every, today) == expected


def test_next_due_monthly_clamps_and_keeps_anchor_day():
    chain = [D(2026, 1, 31)]
    for _ in range(3):
        chain.append(next_due(chain[-1], "month", chain[-1], anchor_day=31))
    assert chain == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30)]


def test_next_due_yearly_feb_29_returns_in_leap_years():
    chain = [D(2028, 2, 29)]
    for _ in range(4):
        chain.append(next_due(chain[-1], "year", chain[-1], anchor_day=29))
    assert chain == [D(2028, 2, 29), D(2029, 2, 28), D(2030, 2, 28), D(2031, 2, 28), D(2032, 2, 29)]


def test_next_due_anchor_defaults_to_due_day():
    assert next_due(D(2026, 1, 31), "month", D(2026, 1, 31)) == D(2026, 2, 28)


def test_complete_recurring_creates_next_occurrence(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2))
    store.add("rent", due=D(2026, 1, 31), tags=["home"], priority="high", every="month")
    store.complete(1, today=D(2026, 1, 31))
    first, nxt = JsonStore(tmp_path / "t.json").load()
    assert first.done and first.updated_at == t2
    assert (nxt.id, nxt.title, nxt.tags, nxt.priority, nxt.every) == (2, "rent", ["home"], "high", "month")
    assert not nxt.done and nxt.due == D(2026, 2, 28) and nxt.updated_at == t2
    assert nxt.anchor_day == 31


def test_complete_next_occurrence_keeps_series_anchor_day(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=D(2026, 1, 31), every="month")
    store.complete(1, today=D(2026, 1, 31))
    store.complete(2, today=D(2026, 2, 28))
    assert [i.due for i in store.load()] == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31)]


def test_complete_overdue_monthly_creates_no_missed_occurrences(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=D(2026, 8, 15), every="month")
    store.complete(1, today=D(2026, 11, 10))
    assert [(i.id, i.done, i.due) for i in store.load()] == [
        (1, True, D(2026, 8, 15)),
        (2, False, D(2026, 11, 15)),
    ]


def test_complete_done_recurring_item_twice_creates_one_occurrence(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("water", every="day", today=TODAY)
    store.complete(1, today=TODAY)
    store.complete(1, today=TODAY)
    assert [i.id for i in store.load()] == [1, 2]


def test_complete_one_off_creates_nothing_and_needs_no_today(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk", due=TODAY)
    store.complete(1)
    assert [(i.id, i.done) for i in store.load()] == [(1, True)]


def test_one_off_and_first_occurrence_save_no_anchor_key(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk")
    store.add("rent", every="month", today=TODAY)
    assert "anchor_day" not in (tmp_path / "t.json").read_text()


@pytest.mark.parametrize("anchor", ['"31"', "0", "32", "true"])
def test_invalid_stored_anchor_day_raises_store_error(tmp_path, anchor):
    path = tmp_path / "t.json"
    path.write_text(
        f'[{{"id": 5, "title": "rent", "due": "2026-01-31", "every": "month", "anchor_day": {anchor}}}]'
    )
    with pytest.raises(StoreError, match="item 5"):
        JsonStore(path).load()


def test_skip_moves_due_keeps_id_and_stays_open(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2))
    store.add("water", due=D(2026, 11, 10), every="week", tags=["home"])
    item = store.skip(1, today=TODAY)
    assert (item.id, item.done, item.due, item.updated_at) == (1, False, D(2026, 11, 17), t2)
    [saved] = JsonStore(tmp_path / "t.json").load()
    assert (saved.id, saved.done, saved.due, saved.updated_at) == (1, False, D(2026, 11, 17), t2)
    assert (saved.title, saved.tags, saved.every) == ("water", ["home"], "week")


def test_skip_overdue_steps_to_today_or_later(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=D(2026, 8, 15), every="month")
    store.skip(1, today=D(2026, 11, 10))
    assert [(i.id, i.due) for i in store.load()] == [(1, D(2026, 11, 15))]


def test_skip_twice_from_jan_31_keeps_anchor_day(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=D(2026, 1, 31), every="month")
    assert store.skip(1, today=D(2026, 1, 31)).due == D(2026, 2, 28)
    assert store.skip(1, today=D(2026, 2, 28)).due == D(2026, 3, 31)


@pytest.mark.parametrize("done", [False, True], ids=["one-off", "done"])
def test_skip_one_off_or_done_item_raises_and_saves_nothing(tmp_path, done):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk", due=TODAY, every=None if not done else "day")
    if done:
        store.complete(1, today=TODAY)
    before = (tmp_path / "t.json").read_text()
    error = (ValueError, "already done") if done else (NotRecurring, "not recurring")
    with pytest.raises(error[0], match=error[1]):
        store.skip(1, today=TODAY)
    assert (tmp_path / "t.json").read_text() == before


def test_skip_missing_id_raises_item_not_found(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("a")
    with pytest.raises(ItemNotFound) as exc:
        store.skip(99, today=TODAY)
    assert exc.value.args == (99,)


def test_stop_clears_rule_and_keeps_done_and_due(tmp_path):
    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    store = JsonStore(tmp_path / "t.json", clock=fixed_clock(t1, t2))
    store.add("rent", due=D(2026, 1, 31), every="month")
    store.stop(1)
    [item] = JsonStore(tmp_path / "t.json").load()
    assert (item.every, item.done, item.due, item.updated_at) == (None, False, D(2026, 1, 31), t2)
    assert "every" not in (tmp_path / "t.json").read_text()


def test_stop_then_complete_creates_nothing(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("water", every="day", today=TODAY)
    store.stop(1)
    assert store.complete(1, today=TODAY) is None
    assert [(i.id, i.done) for i in store.load()] == [(1, True)]


def test_stop_done_recurring_item_removes_only_the_rule(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("water", every="day", today=TODAY)
    store.complete(1, today=TODAY)
    store.stop(1)
    first, nxt = store.load()
    assert (first.every, first.done, first.due) == (None, True, TODAY)
    assert (nxt.id, nxt.every, nxt.done) == (2, "day", False)


def test_stop_one_off_raises_not_recurring_and_saves_nothing(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk", due=TODAY)
    before = (tmp_path / "t.json").read_text()
    with pytest.raises(NotRecurring):
        store.stop(1)
    assert (tmp_path / "t.json").read_text() == before


def test_stop_missing_id_raises_item_not_found(tmp_path):
    with pytest.raises(ItemNotFound):
        JsonStore(tmp_path / "t.json").stop(9)


def test_stop_leaves_no_rule_keys_and_series_keys_only_on_the_open_item(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", due=D(2026, 1, 31), every="month")
    store.complete(1, today=D(2026, 1, 31))
    store.stop(1)
    store.stop(2)
    text = (tmp_path / "t.json").read_text()
    assert "every" not in text and "anchor_day" not in text
    done, open_ = json.loads(text)
    assert "series_id" not in done and "occurrence" not in done
    assert open_["occurrence"] == 2 and open_["series_id"]


# Series id and occurrence number, for sync (behaviour 16, spec decision 1).


def test_item_series_fields_default_to_none():
    item = Item(1, "a")
    assert (item.series_id, item.occurrence) == (None, None)


def test_add_every_starts_a_new_series_at_occurrence_1(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    first = store.add("rent", every="month", today=TODAY)
    second = store.add("water", every="day", today=TODAY)
    one_off = store.add("milk")
    assert isinstance(first.series_id, str) and first.series_id
    assert first.series_id != second.series_id
    assert (first.occurrence, second.occurrence) == (1, 1)
    assert (one_off.series_id, one_off.occurrence) == (None, None)
    assert JsonStore(tmp_path / "t.json").load() == [first, second, one_off]


def test_complete_copies_series_id_and_raises_occurrence(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    first = store.add("rent", due=D(2026, 1, 31), every="month")
    store.complete(1, today=D(2026, 1, 31))
    store.complete(2, today=D(2026, 2, 28))
    items = store.load()
    assert [i.series_id for i in items] == [first.series_id] * 3
    assert [i.occurrence for i in items] == [1, 2, 3]


def test_skip_raises_occurrence_on_the_same_item(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    first = store.add("water", due=D(2026, 11, 10), every="week")
    store.skip(1, today=D(2026, 11, 10))
    store.skip(1, today=D(2026, 11, 10))
    [item] = store.load()
    assert (item.id, item.series_id, item.occurrence) == (1, first.series_id, 3)


def test_stop_done_item_clears_series_id_and_occurrence(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    store.add("rent", every="month", today=TODAY)
    store.complete(1, today=TODAY)
    item = store.stop(1)
    assert (item.series_id, item.occurrence) == (None, None)


@pytest.mark.parametrize(
    "field, value",
    [("series_id", '""'), ("series_id", "7"), ("occurrence", "0"), ("occurrence", '"2"'), ("occurrence", "true")],
)
def test_invalid_stored_series_field_raises_store_error(tmp_path, field, value):
    path = tmp_path / "t.json"
    path.write_text(
        f'[{{"id": 6, "title": "rent", "due": "2026-01-31", "every": "month", "{field}": {value}}}]'
    )
    with pytest.raises(StoreError, match="item 6"):
        JsonStore(path).load()


# Rules that would push dates past 9999-12-31 (review of PR #23).


@pytest.mark.parametrize(
    "every, due",
    [("9999 years", None), ("999999999 days", None), ("month", D(9999, 12, 15))],
)
def test_add_rule_past_last_date_raises_and_saves_nothing(tmp_path, every, due):
    store = JsonStore(tmp_path / "t.json")
    store.add("milk")
    before = (tmp_path / "t.json").read_text()
    with pytest.raises(ValueError, match="9999-12-31"):
        store.add("rent", due=due, every=every, today=TODAY)
    assert (tmp_path / "t.json").read_text() == before


@pytest.mark.parametrize("action", ["complete", "skip"])
@pytest.mark.parametrize("every", ["9999 years", "999999999 days"])
def test_stored_rule_past_last_date_raises_value_error_and_saves_nothing(tmp_path, action, every):
    path = tmp_path / "t.json"
    path.write_text(f'[{{"id": 1, "title": "rent", "due": "2026-11-10", "every": "{every}"}}]')
    before = path.read_text()
    with pytest.raises(ValueError, match="9999-12-31"):
        getattr(JsonStore(path), action)(1, today=TODAY)
    assert path.read_text() == before


def test_stop_open_item_keeps_series_id_and_occurrence_for_sync(tmp_path):
    store = JsonStore(tmp_path / "t.json")
    first = store.add("rent", every="month", today=TODAY)
    store.stop(1)
    [item] = JsonStore(tmp_path / "t.json").load()
    assert (item.every, item.series_id, item.occurrence) == (None, first.series_id, 1)
    assert store.complete(1, today=TODAY) is None


# Security hardening: the todo file may come from a shared remote, so treat its rows as untrusted.


def test_load_replaces_control_characters_in_title(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps([{"id": 1, "title": "a\x1b]52;c;eA==\x07b\nc", "done": False}]))
    assert JsonStore(tmp_path / "t.json").load()[0].title == "a?]52;c;eA==?b?c"


def test_add_replaces_control_characters_in_title(tmp_path):
    assert JsonStore(tmp_path / "t.json").add("a\x1b[2Jb").title == "a?[2Jb"


@pytest.mark.parametrize(
    "row",
    [
        ["not", "a", "dict"],
        {"id": 1, "title": "a", "done": False, "evil": 1},
        {"id": "x", "title": "a", "done": False},
        {"id": True, "title": "a", "done": False},
        {"id": 1, "title": 7, "done": False},
        {"id": 1, "title": "a", "done": "yes"},
        {"title": "a", "done": False},
    ],
)
def test_load_rejects_malformed_row(tmp_path, row):
    (tmp_path / "t.json").write_text(json.dumps([row]))
    with pytest.raises(StoreError):
        JsonStore(tmp_path / "t.json").load()


def test_load_caps_far_future_updated_at(tmp_path):
    (tmp_path / "t.json").write_text(
        json.dumps([{"id": 1, "title": "a", "done": False, "updated_at": "9999-01-01T00:00:00+00:00"}])
    )
    stamp = JsonStore(tmp_path / "t.json").load()[0].updated_at
    assert stamp <= datetime.now(timezone.utc) + timedelta(days=30)


def test_save_keeps_old_file_when_write_fails(tmp_path, monkeypatch):
    store = JsonStore(tmp_path / "t.json")
    store.add("old")
    monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.add("new")
    assert [i.title for i in store.load()] == ["old"]
    assert [p.name for p in tmp_path.iterdir()] == ["t.json"]


@pytest.mark.parametrize("tag", ["a\x1b[2jb", "a\x07", "a‮b"])
def test_load_rejects_control_characters_in_tag(tmp_path, tag):
    (tmp_path / "t.json").write_text(json.dumps([{"id": 1, "title": "a", "done": False, "tags": [tag]}]))
    with pytest.raises(StoreError):
        JsonStore(tmp_path / "t.json").load()


def test_unknown_fields_error_escapes_control_characters(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps([{"id": "\x1b[31m", "title": "a", "evil\x1b[2J": 1}]))
    with pytest.raises(StoreError) as exc:
        JsonStore(tmp_path / "t.json").load()
    assert "\x1b" not in str(exc.value)


@pytest.mark.parametrize("text", ['[{"id": 1', "5", '{"id": 1}', "[" * 100_000])
def test_load_rejects_malformed_file(tmp_path, text):
    (tmp_path / "t.json").write_text(text)
    with pytest.raises(StoreError):
        JsonStore(tmp_path / "t.json").load()


def test_load_refuses_a_fifo_instead_of_hanging(tmp_path):
    import os

    os.mkfifo(tmp_path / "t.json")
    with pytest.raises(StoreError, match="not a regular file"):
        JsonStore(tmp_path / "t.json").load()


def test_load_reports_a_deeply_nested_row_as_a_clean_error(tmp_path):
    depth = 100_000
    (tmp_path / "t.json").write_text("[" + "[" * depth + "]" * depth + "]")
    with pytest.raises(StoreError) as exc:
        JsonStore(tmp_path / "t.json").load()
    assert len(str(exc.value)) < 500


@pytest.mark.parametrize(
    "row",
    [
        {"id": "x" * 1_000_000, "title": "a"},
        {"id": 1, "title": "a", "done": "y" * 1_000_000},
        {"id": 1, "title": "a", "due": "d" * 1_000_000},
        {"id": 1, "title": "a", "priority": "p" * 1_000_000},
        {"id": 1, "title": "a", "every": "e" * 1_000_000},
        {"id": 1, "title": "a", "tags": ["t t" * 1_000_000]},
        {"id": 1, "title": "a", "series_id": ["s"] * 1_000_000},
        {"id": 1, "title": "a", "k" * 1_000_000: 1},
        {"id": 1, "title": "a", **{f"k{n}": 1 for n in range(100_000)}},
    ],
)
def test_load_error_shows_a_bounded_value(tmp_path, row):
    (tmp_path / "t.json").write_text(json.dumps([row]))
    with pytest.raises(StoreError) as exc:
        JsonStore(tmp_path / "t.json").load()
    assert len(str(exc.value)) < 500


@pytest.mark.parametrize("item_id", [int("9" * 4300), 2**53 + 1, 0, -1], ids=["4300-digits", "2**53+1", "0", "-1"])
def test_load_rejects_an_id_out_of_range(tmp_path, item_id):
    (tmp_path / "t.json").write_text(json.dumps([{"id": item_id, "title": "a"}]))
    with pytest.raises(StoreError, match="invalid id"):
        JsonStore(tmp_path / "t.json").load()


def test_add_after_the_largest_id_raises_and_saves_nothing(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": 2**53, "title": "a"}]))
    before = path.read_text()
    with pytest.raises(ValueError, match="no free id"):
        JsonStore(path).add("b")
    assert path.read_text() == before


def test_complete_recurring_after_the_largest_id_raises_and_saves_nothing(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": 2**53, "title": "a", "due": "2026-11-10", "every": "day"}]))
    before = path.read_text()
    with pytest.raises(ValueError, match="no free id"):
        JsonStore(path).complete(2**53, today=TODAY)
    assert path.read_text() == before


def test_load_refuses_a_fifo_swapped_in_after_the_file_check(tmp_path, monkeypatch):
    import os
    import pathlib
    import threading

    path = tmp_path / "t.json"
    os.mkfifo(path)
    # The FIFO replaces the regular file between any check by name and the open.
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)
    errors = []

    def load():
        try:
            JsonStore(path).load()
        except Exception as e:
            errors.append(e)

    reader = threading.Thread(target=load, daemon=True)
    reader.start()
    reader.join(timeout=2)
    hung = reader.is_alive()
    if hung:  # let the blocked reader go
        os.close(os.open(path, os.O_WRONLY))
        reader.join(timeout=2)
    assert not hung
    assert len(errors) == 1 and isinstance(errors[0], StoreError)
    assert "not a regular file" in str(errors[0])


def test_remote_load_refuses_a_hard_link_to_another_file(tmp_path):
    import os

    private = tmp_path / "private.json"
    private.write_text(json.dumps([{"id": 1, "title": "secret"}]))
    remote = tmp_path / "remote.json"
    os.link(private, remote)
    with pytest.raises(StoreError, match="hard link"):
        JsonStore(remote, follow_symlinks=False).load()


def test_load_refuses_a_file_over_the_size_limit(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"id": 1, "title": "x" * 16 * 2**20}]))  # just over the 16 MiB README limit
    with pytest.raises(StoreError, match="too large"):
        JsonStore(path).load()


def test_save_sets_mode_on_its_own_temp_file_not_a_symlink_swapped_in(tmp_path, monkeypatch):
    import os
    import tempfile

    victim = tmp_path / "victim.json"
    victim.write_text("private")
    victim.chmod(0o600)
    remote = tmp_path / "remote.json"
    remote.write_text("[]")
    remote.chmod(0o666)
    real_mkstemp = tempfile.mkstemp

    def mkstemp_then_swap(*args, **kwargs):
        # A writer of the shared folder replaces the temp file's name with a symlink to the victim.
        fd, tmp = real_mkstemp(*args, **kwargs)
        os.unlink(tmp)
        os.symlink(victim, tmp)
        return fd, tmp

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_then_swap)
    JsonStore(remote, follow_symlinks=False).save([])
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600


def test_save_does_not_copy_mode_from_a_file_someone_else_owns(tmp_path, monkeypatch):
    import os

    path = tmp_path / "todo.json"
    path.write_text("[]")
    path.chmod(0o666)
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)  # the file belongs to another user
    JsonStore(path).add("call bank")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_remote_save_keeps_mode_of_a_remote_another_member_saved(tmp_path, monkeypatch):
    import os

    remote = tmp_path / "remote.json"
    remote.write_text("[]")
    remote.chmod(0o660)
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)  # last saved by another member
    JsonStore(remote, follow_symlinks=False).save([])
    assert stat.S_IMODE(remote.stat().st_mode) == 0o660


def test_save_keeps_the_group_of_a_group_shared_file(tmp_path, monkeypatch):
    # Linux gives a new file the creator's primary group (no setgid folder); macOS gives it the
    # folder's group. Simulate Linux by moving the temp file to another of our groups.
    import os
    import tempfile

    groups = sorted(set(os.getgroups()))
    if len(groups) < 2:
        pytest.skip("needs a user in two groups")
    project, primary = groups[0], groups[1]
    remote = tmp_path / "remote.json"
    remote.write_text("[]")
    os.chown(remote, -1, project)
    remote.chmod(0o660)
    real_mkstemp = tempfile.mkstemp

    def mkstemp_in_primary_group(*args, **kwargs):
        fd, tmp = real_mkstemp(*args, **kwargs)
        os.chown(tmp, -1, primary)
        return fd, tmp

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_in_primary_group)
    JsonStore(remote, follow_symlinks=False).save([])
    assert remote.stat().st_gid == project
    assert stat.S_IMODE(remote.stat().st_mode) == 0o660


def test_save_fails_closed_when_it_cannot_keep_the_group(tmp_path, monkeypatch):
    import os

    remote = tmp_path / "remote.json"
    remote.write_text("[]")
    remote.chmod(0o660)

    def fchown_refused(*args):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "fchown", fchown_refused)
    with pytest.raises(StoreError, match="group"):
        JsonStore(remote, follow_symlinks=False).save([Item(id=1, title="a")])
    assert remote.read_text() == "[]"
    assert [p.name for p in tmp_path.iterdir()] == ["remote.json"]


def _remote_through_a_symlinked_folder(tmp_path):
    """`shared/team` is a symlink to another folder holding a todo file; return (remote, that file)."""
    import os

    other = tmp_path / "other-project"
    other.mkdir()
    target = other / "todo.json"
    target.write_text(json.dumps([{"id": 1, "title": "not the team's"}]))
    (tmp_path / "shared").mkdir()
    os.symlink(other, tmp_path / "shared" / "team")
    return tmp_path / "shared" / "team" / "todo.json", target


@pytest.mark.parametrize("action", ["load", "save"])
def test_remote_refuses_a_folder_symlink_someone_else_planted(tmp_path, monkeypatch, action):
    import os

    remote, target = _remote_through_a_symlinked_folder(tmp_path)
    before = target.read_text()
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)  # the symlink belongs to another user
    store = JsonStore(remote, follow_symlinks=False)
    with pytest.raises(StoreError, match="symlink"):
        store.load() if action == "load" else store.save([])
    assert target.read_text() == before


def test_remote_follows_a_folder_symlink_the_user_owns(tmp_path):
    remote, _ = _remote_through_a_symlinked_folder(tmp_path)
    assert [i.title for i in JsonStore(remote, follow_symlinks=False).load()] == ["not the team's"]
