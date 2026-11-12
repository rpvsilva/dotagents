"""#17 Create a recurring todo: `todo add TITLE --every RULE`, the rule shown in `todo list`.

Today is 2026-11-10 (conftest.TODAY). Core tests follow the issue's criteria and its example line; the strict ones
pin what the first build chose (the rule before the priority marker, plural and capitalised rules written back in
canonical form, a quoted rule, three words refused, a rule past 9999-12-31 refused)."""
import pytest
from conftest import TODAY, due_of, line_of


@pytest.mark.parametrize("rule, shown", [
    (["month"], "every month"),
    (["2", "weeks"], "every 2 weeks"),
    (["day"], "every day"),
    (["year"], "every year"),
])
def test_add_every_shows_the_rule_in_list(todo, rule, shown):
    todo.add("rent", "--due", "2026-11-01", "--every", *rule)
    assert f"↻ {shown}" in line_of(todo.list(), 1)


@pytest.mark.parametrize("rule", [["days"], ["weeks"], ["1", "month"], ["2", "week"], ["3", "years"]])
def test_add_every_takes_singular_or_plural(todo, rule):
    todo.add("rent", "--due", "2026-11-01", "--every", *rule)
    assert "↻ every" in line_of(todo.list(), 1)


def test_list_shows_the_rule_as_the_issue_does(todo):
    todo.add("a", "--due", "2026-10-01")
    todo.add("b")
    todo.add("c")
    todo.add("pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    assert line_of(todo.list(), 4) == "[ ] 4: pay rent (due 2026-11-01) ↻ every month #home"


def test_recurring_item_without_due_is_due_today(todo):
    todo.add("water plants", "--every", "day", today=TODAY)
    assert due_of(line_of(todo.list(), 1)) == TODAY.isoformat()


def test_one_off_item_without_due_stays_undated(todo):
    todo.add("water plants", "--every", "day")
    todo.add("buy milk")
    assert line_of(todo.list(), 2) == "[ ] 2: buy milk"


@pytest.mark.parametrize("rule", [["fortnight"], ["0", "days"], ["-1", "weeks"], ["weekly"]])
def test_bad_rule_exits_2_with_a_clear_error_and_adds_nothing(todo, rule):
    r = todo("add", "rent", "--every", *rule)
    assert r.code == 2
    assert r.err.strip() and "Traceback" not in r.err
    assert line_of(todo.list(), 1) is None
    todo.add("rent", "--every", "month")  # the flag exists: the refusal above was the rule


def test_a_file_without_rules_still_loads_and_its_items_do_not_repeat(todo):
    todo.path.write_text(
        '[{"id": 1, "title": "milk", "done": false, "due": "2026-11-12", "updated_at": null,'
        ' "tags": ["home"], "priority": "high"},'
        ' {"id": 2, "title": "eggs", "done": false, "due": null, "updated_at": null, "tags": [], "priority": null}]',
        encoding="utf-8")
    assert [line_of(todo.list(), i) is not None for i in (1, 2)] == [True, True]
    assert "↻" not in "\n".join(todo.list())
    todo.ok("done", "1")
    assert todo.open_lines() == ["[ ] 2: eggs"]
    todo.add("rent", "--every", "month")  # and the file takes recurring items from now on
    assert "↻ every month" in line_of(todo.list(), 3)


@pytest.mark.strict
def test_list_shows_the_rule_before_the_priority_marker(todo):
    todo.add("water", "--every", "2", "weeks", "--priority", "high", "--tag", "home")
    assert todo.list() == ["[ ] 1: water (due 2026-11-10) ↻ every 2 weeks !! #home"]


@pytest.mark.strict
@pytest.mark.parametrize("rule, shown", [(["months"], "every month"), (["3", "Months"], "every 3 months"),
                                         (["1", "day"], "every day"), (["2", "week"], "every 2 weeks")])
def test_rule_is_shown_in_canonical_form(todo, rule, shown):
    todo.add("rent", "--due", "2026-11-01", "--every", *rule)
    assert line_of(todo.list(), 1) == f"[ ] 1: rent (due 2026-11-01) ↻ {shown}"


@pytest.mark.strict
def test_quoted_rule_works_like_two_words(todo):
    todo.add("water", "--every", "2 weeks", "--tag", "home")
    assert line_of(todo.list(), 1) == "[ ] 1: water (due 2026-11-10) ↻ every 2 weeks #home"


@pytest.mark.strict
def test_three_word_rule_exits_2(todo):
    assert todo("add", "rent", "--every", "2", "3", "weeks").code == 2
    todo.add("rent", "--every", "2", "weeks")  # the flag exists: the refusal above was the rule


@pytest.mark.strict
@pytest.mark.parametrize("rule", [["9999", "years"], ["999999999", "days"]])
def test_rule_past_the_last_date_exits_2_and_adds_nothing(todo, rule):
    r = todo("add", "rent", "--every", *rule)
    assert r.code == 2 and "9999-12-31" in r.err and "Traceback" not in r.err
    assert line_of(todo.list(), 1) is None
