"""#407 `todo next`: the single best item among open items that are neither waiting nor snoozed.

Today is 2026-06-10 (conftest.TODAY). Spec behaviours 22-25; the strict tests pin open question 7 (the item's
`todo list` line, `nothing to do`, undated after dated)."""
import pytest
from conftest import ids, line_of


def best(todo):
    """The one item id `todo next` prints."""
    r = todo("next")
    assert r.code == 0, r.err
    assert len(ids(r.lines)) == 1, r.lines
    return ids(r.lines)[0]


def test_overdue_comes_first(todo):
    todo.add("soon and urgent", "--due", "2026-06-11", "--priority", "high")
    todo.add("late", "--due", "2026-06-01", "--priority", "low")
    assert best(todo) == 2


def test_then_the_soonest_due_date(todo):
    todo.add("later", "--due", "2026-06-20", "--priority", "high")
    todo.add("sooner", "--due", "2026-06-12", "--priority", "low")
    assert best(todo) == 2


def test_then_the_highest_priority(todo):
    todo.add("low", "--due", "2026-06-12", "--priority", "low")
    todo.add("high", "--due", "2026-06-12", "--priority", "high")
    todo.add("medium", "--due", "2026-06-12", "--priority", "medium")
    assert best(todo) == 2


def test_ties_break_by_the_lowest_id(todo):
    todo.add("first", "--due", "2026-06-12")
    todo.add("second", "--due", "2026-06-12")
    assert best(todo) == 1


def test_waiting_items_are_skipped(todo):
    todo.add("late but blocked", "--due", "2026-06-01")
    todo.add("blocker", "--due", "2026-06-30")
    assert todo("block", "1", "--on", "2").code == 0
    assert best(todo) == 2


def test_snoozed_and_done_items_are_skipped(todo):
    todo.add("late but snoozed", "--due", "2026-06-01")
    todo.add("late but done", "--due", "2026-06-02")
    todo.add("left", "--due", "2026-06-30")
    assert todo("snooze", "1", "--until", "2026-06-20").code == 0
    assert todo("done", "2").code == 0
    assert best(todo) == 3


def test_no_candidate_says_so_and_exits_0(todo):
    r = todo("next")
    assert r.code == 0 and r.lines, (r.code, r.lines, r.err)
    assert ids(r.lines) == []
    todo.add("done already")
    todo("done", "1")
    r = todo("next")
    assert r.code == 0 and r.lines and ids(r.lines) == [], (r.code, r.lines)


def test_readme_documents_the_ranking(readme):
    assert "todo next" in readme
    assert "overdue" in readme.lower()
    assert "priority" in readme.lower()


@pytest.mark.strict
def test_prints_the_item_as_its_list_line(todo):
    todo.add("trip", "--due", "2026-06-12", "--priority", "high", "--tag", "travel")
    todo.add("pack", "--parent", "1")
    todo.add("other", "--due", "2026-06-30")
    r = todo("next")
    assert r.lines == [line_of(todo.list(), 1)]


@pytest.mark.strict
def test_nothing_to_do(todo):
    assert todo("next").lines == ["nothing to do"]


@pytest.mark.strict
def test_undated_items_rank_after_dated_ones(todo):
    todo.add("undated", "--priority", "high")
    todo.add("dated", "--due", "2026-07-30", "--priority", "low")
    assert best(todo) == 2
