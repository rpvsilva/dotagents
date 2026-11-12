"""#20 Stop a series: `todo stop ID` removes the rule; the item stays open as a one-off.

Today is 2026-11-10. Core tests follow the issue's criteria; the strict ones pin what the first build chose (the
`stopped ID` line, an absent id with exit 1, stopping a done occurrence leaves the series going in its next one)."""
import pytest
from conftest import line_of


def test_stop_removes_the_rule_and_keeps_the_item_open(todo):
    todo.add("rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    todo.ok("stop", "1")
    assert todo.list() == ["[ ] 1: rent (due 2026-11-01) #home"]


def test_done_after_stop_creates_no_next_occurrence(todo):
    todo.add("rent", "--due", "2026-11-01", "--every", "month")
    todo.ok("stop", "1")
    todo.ok("done", "1")
    assert todo.open_lines() == []
    assert len(todo.items()) == 1
    assert "↻" not in line_of(todo.list(), 1)


def test_stop_a_one_off_item_exits_2_with_a_clear_error_and_changes_nothing(todo):
    todo.add("milk", "--due", "2026-11-01")
    todo.add("rent", "--due", "2026-11-01", "--every", "month")
    before = todo.list()
    r = todo("stop", "1")
    assert r.code == 2
    assert r.err.strip() and "Traceback" not in r.err
    assert todo.list() == before
    todo.ok("stop", "2")  # the command exists: the refusal above was the one-off item


@pytest.mark.strict
def test_stop_prints_stopped(todo):
    todo.add("rent", "--every", "month")
    assert todo.ok("stop", "1") == ["stopped 1"]


@pytest.mark.strict
def test_stop_an_absent_id_exits_1(todo):
    r = todo("stop", "9")
    assert r.code == 1 and "no item 9" in r.err


@pytest.mark.strict
def test_stop_a_done_occurrence_keeps_the_series_going(todo):
    todo.add("water", "--every", "day")
    todo.ok("done", "1")
    todo.ok("stop", "1")
    assert todo.list() == ["[x] 1: water (due 2026-11-10)", "[ ] 2: water (due 2026-11-11) ↻ every day"]
