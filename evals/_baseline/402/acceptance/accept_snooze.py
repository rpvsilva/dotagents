"""#405 snooze: `todo snooze ID --until DATE`, `todo list --snoozed`, hidden from `list` and `remind` until DATE.

Today is 2026-06-10 in every command (conftest.TODAY). Spec behaviours 11-15; the strict tests pin open questions 5
(DATE after today, `--snoozed` shows only snoozed items, hidden from `search` too) and 14 (`snoozed ID until DATE`)."""
from datetime import date

import pytest
from conftest import ids, line_of

UNTIL = "2026-06-12"
DAY_BEFORE, ON_DATE = date(2026, 6, 11), date(2026, 6, 12)


def remind(todo, today=None):
    r = todo("remind") if today is None else todo("remind", today=today)
    assert r.code == 0, r.err
    return r.lines


def test_snoozed_item_is_hidden_from_list(todo):
    todo.add("call the bank")
    todo.add("water plants")
    assert todo("snooze", "1", "--until", UNTIL).code == 0
    assert ids(todo.list()) == [2]


def test_snoozed_item_is_hidden_from_remind(todo):
    todo.add("pay rent", "--due", "2026-06-11")
    assert line_of(remind(todo), 1)
    assert todo("snooze", "1", "--until", UNTIL).code == 0
    assert line_of(remind(todo), 1) is None
    assert line_of(remind(todo, DAY_BEFORE), 1) is None


def test_snoozed_item_shows_again_in_list_on_its_date(todo):
    todo.add("call the bank")
    todo("snooze", "1", "--until", UNTIL)
    assert ids(todo.list(today=DAY_BEFORE)) == []
    assert ids(todo.list(today=ON_DATE)) == [1]


def test_snoozed_item_shows_again_in_remind_on_its_date(todo):
    todo.add("pay rent", "--due", "2026-06-11")
    assert todo("snooze", "1", "--until", UNTIL).code == 0
    assert line_of(remind(todo, DAY_BEFORE), 1) is None
    assert line_of(remind(todo, ON_DATE), 1)


def test_list_snoozed_shows_the_snoozed_item(todo):
    todo.add("call the bank")
    todo("snooze", "1", "--until", UNTIL)
    assert 1 in ids(todo.list("--snoozed"))


def test_snoozing_a_done_item_exits_2(todo):
    todo.add("call the bank")
    todo.add("water plants")
    assert todo("done", "1").code == 0
    assert todo("snooze", "1", "--until", UNTIL).code == 2
    assert ids(todo.list("--done")) == [1]
    assert todo("snooze", "2", "--until", UNTIL).code == 0  # the command exists: the refusal above was the done item


def test_readme_documents_snooze(readme):
    assert "todo snooze" in readme
    assert "--snoozed" in readme


@pytest.mark.strict
def test_snooze_prints_the_date(todo):
    todo.add("call the bank")
    r = todo("snooze", "1", "--until", UNTIL)
    assert r.lines == [f"snoozed 1 until {UNTIL}"]


@pytest.mark.strict
def test_list_snoozed_shows_only_snoozed_items(todo):
    todo.add("call the bank")
    todo.add("water plants")
    todo("snooze", "1", "--until", UNTIL)
    assert ids(todo.list("--snoozed")) == [1]


@pytest.mark.strict
def test_until_must_be_after_today(todo):
    todo.add("call the bank")
    assert todo("snooze", "1", "--until", "2026-06-10").code == 2
    assert todo("snooze", "1", "--until", "2026-06-01").code == 2
    assert todo("snooze", "1", "--until", "2026-6-12").code == 2
    assert todo("snooze", "99", "--until", UNTIL).code == 2
    assert ids(todo.list()) == [1]
    assert todo("snooze", "1", "--until", UNTIL).code == 0


@pytest.mark.strict
def test_snoozed_item_is_hidden_from_search(todo):
    todo.add("call the bank")
    todo.add("bank statement")
    todo("snooze", "1", "--until", UNTIL)
    r = todo("search", "bank")
    assert r.code == 0, r.err
    assert ids(r.lines) == [2]
