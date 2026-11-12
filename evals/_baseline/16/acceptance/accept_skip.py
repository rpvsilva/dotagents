"""#19 Skip one occurrence: `todo skip ID` moves a recurring item to its next occurrence without marking it done.

Today is 2026-11-10 unless a test passes its own. Core tests follow the issue's criteria and #18's date rules; the
strict ones pin what the first build chose (same id, the `skipped ID: TITLE, next due DATE` line, a done item
refused with exit 2, an absent id with exit 1)."""
from datetime import date

import pytest
from conftest import due_of, line_of


def test_skip_moves_the_item_to_its_next_occurrence_without_marking_it_done(todo):
    todo.add("rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    todo.ok("skip", "1")
    assert todo.done_lines() == []
    [line] = todo.open_lines()
    assert due_of(line) == "2026-12-01"
    assert "rent" in line and "↻ every month" in line and "#home" in line


def test_skip_follows_the_series_day_across_month_ends(todo):
    todo.add("rent", "--due", "2027-01-31", "--every", "month")
    todo.ok("skip", "1", today=date(2027, 1, 31))
    [line] = todo.open_lines(date(2027, 2, 28))
    assert due_of(line) == "2027-02-28"
    item = line.split(":")[0].split()[-1]
    todo.ok("skip", item, today=date(2027, 2, 28))
    assert [due_of(line) for line in todo.open_lines()] == ["2027-03-31"]


def test_skip_then_done_schedules_the_occurrence_after_the_skipped_one(todo):
    todo.add("water", "--due", "2026-11-10", "--every", "week")
    todo.ok("skip", "1")
    [line] = todo.open_lines()
    todo.ok("done", line.split(":")[0].split()[-1], today=date(2026, 11, 17))
    assert [due_of(line) for line in todo.open_lines()] == ["2026-11-24"]


def test_skip_a_one_off_item_exits_2_with_a_clear_error_and_changes_nothing(todo):
    todo.add("milk", "--due", "2026-11-10")
    todo.add("rent", "--due", "2026-11-01", "--every", "month")
    before = todo.list()
    r = todo("skip", "1")
    assert r.code == 2
    assert r.err.strip() and "Traceback" not in r.err
    assert todo.list() == before
    todo.ok("skip", "2")  # the command exists: the refusal above was the one-off item


@pytest.mark.strict
def test_skip_keeps_the_id_and_prints_the_next_due_date(todo):
    todo.add("rent", "--due", "2026-11-01", "--every", "month")
    assert todo.ok("skip", "1") == ["skipped 1: rent, next due 2026-12-01"]
    assert todo.list() == ["[ ] 1: rent (due 2026-12-01) ↻ every month"]


@pytest.mark.strict
def test_skip_a_done_item_exits_2(todo):
    todo.add("water", "--every", "day")
    todo.ok("done", "1")
    r = todo("skip", "1")
    assert r.code == 2 and "already done" in r.err


@pytest.mark.strict
def test_skip_an_absent_id_exits_1(todo):
    todo.add("water", "--every", "day")
    r = todo("skip", "99")
    assert r.code == 1 and "no item 99" in r.err
