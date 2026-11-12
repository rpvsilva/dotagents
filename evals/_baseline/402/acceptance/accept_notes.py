"""#404 notes: `todo note ID TEXT` and `todo show ID`.

Spec behaviours 6-10; the strict tests pin open questions 4 (1-1000 characters), 12 (`todo show`'s lines) and 14
(`noted ID`)."""
import re

import pytest

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME = re.compile(r"\d{2}:\d{2}")


def note_lines(lines, *texts):
    """The output lines holding each text, in output order."""
    return [line for line in lines if any(t in line for t in texts)]


def test_show_prints_notes_in_the_order_added(todo):
    todo.add("trip")
    for text in ("first note", "second note", "third note"):
        assert todo("note", "1", text).code == 0
    r = todo("show", "1")
    assert r.code == 0, r.err
    first, second, third = (r.text.find(t) for t in ("first note", "second note", "third note"))
    assert 0 <= first < second < third, r.lines


def test_each_note_shows_the_time_it_was_added(todo):
    todo.add("trip")
    todo("note", "1", "pack the tent")
    (line,) = note_lines(todo("show", "1").lines, "pack the tent")
    assert DATE.search(line) and TIME.search(line), line


def test_show_prints_the_item_fields(todo):
    todo.add("trip", "--due", "2026-07-01", "--priority", "high", "--tag", "travel", "--every", "week")
    r = todo("show", "1")
    assert r.code == 0, r.err
    for value in ("trip", "2026-07-01", "high", "travel", "week"):
        assert value in r.text, value


def test_notes_belong_to_their_item(todo):
    todo.add("trip")
    todo.add("work")
    todo("note", "1", "pack the tent")
    assert "pack the tent" in todo("show", "1").text
    assert "pack the tent" not in todo("show", "2").text


def test_note_on_unknown_id_exits_2(todo):
    todo.add("trip")
    assert todo("note", "1", "kept").code == 0
    assert todo("note", "99", "lost").code == 2
    assert "lost" not in todo("show", "1").text


def test_show_unknown_id_exits_2(todo):
    todo.add("trip")
    assert todo("show", "1").code == 0
    assert todo("show", "99").code == 2


def test_readme_documents_note_and_show(readme):
    assert "todo note" in readme
    assert "todo show" in readme


@pytest.mark.strict
def test_note_prints_noted_id(todo):
    todo.add("trip")
    r = todo("note", "1", "pack the tent")
    assert (r.code, r.lines) == (0, ["noted 1"])


@pytest.mark.strict
def test_show_lines_name_each_field_and_each_note(todo):
    todo.add("trip", "--due", "2026-07-01", "--priority", "high")
    r = todo("show", "1")
    for line in ("id: 1", "title: trip", "done: no", "due: 2026-07-01", "priority: high", "tags: -",
                 "parent: -", "snoozed until: -", "blocked on: -", "notes: none"):
        assert line in r.lines, line
    todo("note", "1", "pack the tent")
    lines = todo("show", "1").lines
    assert "notes:" in lines
    assert any(re.fullmatch(r"  \d{4}-\d{2}-\d{2} \d{2}:\d{2}  pack the tent", line) for line in lines), lines


@pytest.mark.strict
def test_empty_or_too_long_note_exits_2_and_saves_nothing(todo):
    todo.add("trip")
    assert todo("note", "1", "").code == 2
    assert todo("note", "1", "x" * 1001).code == 2
    assert todo("note", "1", "x" * 1000).code == 0
    assert "notes: none" not in todo("show", "1").lines
    assert sum("x" * 1000 in line for line in todo("show", "1").lines) == 1
