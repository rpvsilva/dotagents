"""#18 Completing a recurring todo schedules the next one: `todo done ID` adds the next occurrence.

Today is 2026-11-10 unless a test passes its own. Core tests follow the issue's criteria; the strict ones pin what
the first build chose (the `next ID: TITLE (due DATE)` line, a rule past 9999-12-31 refused on `done`)."""
from datetime import date

import pytest
from conftest import TODAY, due_of, line_of


def test_next_occurrence_copies_title_tags_priority_and_rule(todo):
    todo.add("pay rent", "--due", "2026-11-10", "--every", "month", "--tag", "home", "--priority", "high")
    todo.ok("done", "1")
    [nxt] = todo.open_lines()
    assert not nxt.startswith("[ ] 1:")
    for part in ("pay rent", "↻ every month", "!!", "#home"):
        assert part in nxt, nxt
    assert todo.done_lines()[0].startswith("[x] 1: pay rent")


def test_next_occurrence_gets_a_new_id_and_is_not_done(todo):
    todo.add("pay rent", "--due", "2026-11-10", "--every", "month")
    todo.ok("done", "1")
    ids = [i for i, _, _ in todo.items()]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert [i for i, done, _ in todo.items() if not done] == [max(ids)]


@pytest.mark.parametrize("rule, due, today, expected", [
    (["day"], "2026-11-10", date(2026, 11, 10), "2026-11-11"),
    (["week"], "2026-11-02", date(2026, 11, 2), "2026-11-09"),
    (["2", "weeks"], "2026-11-02", date(2026, 11, 2), "2026-11-16"),
    (["month"], "2026-11-01", date(2026, 11, 1), "2026-12-01"),
    (["year"], "2026-03-01", date(2026, 3, 1), "2027-03-01"),
])
def test_next_due_is_the_previous_due_plus_the_interval(todo, rule, due, today, expected):
    todo.add("x", "--due", due, "--every", *rule, today=today)
    todo.ok("done", "1", today=today)
    assert [due_of(line) for line in todo.open_lines(today)] == [expected]


def test_overdue_item_steps_to_today_or_later_without_missed_occurrences(todo):
    todo.add("rent", "--due", "2026-08-15", "--every", "month")
    todo.ok("done", "1", today=TODAY)
    assert len(todo.items()) == 2
    assert [due_of(line) for line in todo.open_lines()] == ["2026-11-15"]


def test_overdue_daily_item_is_next_due_today(todo):
    todo.add("water", "--due", "2026-11-09", "--every", "day")
    todo.ok("done", "1", today=TODAY)
    assert [due_of(line) for line in todo.open_lines()] == ["2026-11-10"]


def test_daily_item_completed_late_on_its_day_is_next_due_tomorrow(todo):
    todo.add("water", "--every", "day", today=TODAY)
    todo.ok("done", "1", today=TODAY)
    assert [due_of(line) for line in todo.open_lines()] == ["2026-11-11"]


def complete_chain(todo, first, rule, steps):
    """Adds a series due `first`, then completes each open occurrence on its own due date; returns the due dates."""
    todo.add("x", "--due", first, "--every", rule)
    dues = [first]
    for _ in range(steps):
        [line] = todo.open_lines()
        item = int(line.split(":")[0].split()[-1])
        day = date.fromisoformat(due_of(line))
        todo.ok("done", str(item), today=day)
        [nxt] = todo.open_lines(day)
        dues.append(due_of(nxt))
    return dues


def test_month_ends_clamp_and_keep_the_series_day(todo):
    assert complete_chain(todo, "2027-01-31", "month", 3) == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"]


def test_yearly_feb_29_falls_on_feb_28_and_returns_in_leap_years(todo):
    assert complete_chain(todo, "2028-02-29", "year", 4) == [
        "2028-02-29", "2029-02-28", "2030-02-28", "2031-02-28", "2032-02-29"]


def test_completing_a_done_occurrence_again_adds_nothing(todo):
    todo.add("water", "--every", "day")
    todo.ok("done", "1")
    todo("done", "1")
    assert len(todo.items()) == 2


def test_one_off_item_creates_nothing_on_done(todo):
    todo.add("water", "--every", "day")
    todo.add("milk", "--due", "2026-11-10")
    todo.ok("done", "2")
    assert len(todo.items()) == 2
    assert line_of(todo.list(), 2).startswith("[x] 2: milk")


@pytest.mark.strict
def test_done_prints_the_next_occurrence(todo):
    todo.add("water", "--every", "day")
    assert todo.ok("done", "1") == ["done 1", "next 2: water (due 2026-11-11)"]
    assert todo.list() == ["[x] 1: water (due 2026-11-10) ↻ every day", "[ ] 2: water (due 2026-11-11) ↻ every day"]


@pytest.mark.strict
def test_done_on_a_stored_rule_past_the_last_date_exits_2(todo):
    todo.add("rent", "--due", "9999-12-30", "--every", "day")
    todo.ok("done", "1", today=date(9999, 12, 30))  # its next occurrence is due 9999-12-31, the last date
    r = todo("done", "2", today=date(9999, 12, 31))
    assert r.code == 2 and "9999-12-31" in r.err and "Traceback" not in r.err
