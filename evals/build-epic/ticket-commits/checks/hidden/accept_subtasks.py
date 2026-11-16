"""#403 subtasks: `todo add --parent`, children under their parent in `todo list`, `done/total` on the parent.

Spec behaviours 1-5; the strict tests pin open questions 2 (any depth, two spaces a level, direct children counted,
a child of a hidden parent at top level) and 3 (the count right after the title)."""
import re

import pytest
from conftest import ids, indent, line_of

COUNT = re.compile(r"(?<![\d-])(\d+)/(\d+)(?![\d-])")


def test_add_parent_lists_the_child_indented_under_its_parent(todo):
    trip = todo.add("trip")
    r = todo("add", "pack", "--parent", trip)
    assert r.code == 0, r.err
    assert r.lines == ["added 2: pack"]
    lines = todo.list()
    assert ids(lines) == [1, 2]
    assert indent(line_of(lines, 2)) > indent(line_of(lines, 1))


def test_children_follow_their_parent_not_the_id_order(todo):
    todo.add("a")
    todo.add("b")
    todo.add("c", "--parent", "1")
    lines = todo.list()
    assert ids(lines) == [1, 3, 2]
    assert indent(line_of(lines, 3)) > indent(line_of(lines, 1)) == indent(line_of(lines, 2))


def test_parent_shows_done_children_over_all_children(todo):
    todo.add("trip")
    todo.add("pack", "--parent", "1")
    todo.add("book", "--parent", "1")
    assert todo("done", "3").code == 0
    assert COUNT.findall(line_of(todo.list(), 1)) == [("1", "2")]
    assert todo("done", "2").code == 0
    assert COUNT.findall(line_of(todo.list(), 1)) == [("2", "2")]


def test_item_without_children_shows_no_count(todo):
    todo.add("trip", "--due", "2026-07-01")
    todo.add("pack", "--parent", "1")
    lines = todo.list()
    assert line_of(lines, 2).strip() == "[ ] 2: pack"
    todo.add("alone")
    assert line_of(todo.list(), 3) == "[ ] 3: alone"


def test_unknown_parent_exits_2_and_adds_nothing(todo):
    todo.add("trip")
    r = todo("add", "pack", "--parent", "99")
    assert r.code == 2
    assert ids(todo.list()) == [1]
    assert todo("add", "pack", "--parent", "1").code == 0  # the flag exists: the refusal above was the unknown id


def test_readme_documents_parent(readme):
    assert "--parent" in readme


@pytest.mark.strict
def test_any_depth_two_spaces_per_level(todo):
    todo.add("a")
    todo.add("b", "--parent", "1")
    todo.add("c", "--parent", "2")
    lines = todo.list()
    assert ids(lines) == [1, 2, 3]
    assert [indent(line) for line in lines] == [0, 2, 4]


@pytest.mark.strict
def test_count_sits_right_after_the_title(todo):
    todo.add("trip", "--due", "2026-07-01")
    todo.add("pack", "--parent", "1")
    assert line_of(todo.list(), 1) == "[ ] 1: trip 0/1 (due 2026-07-01)"


@pytest.mark.strict
def test_count_covers_direct_children_only(todo):
    todo.add("a")
    todo.add("b", "--parent", "1")
    todo.add("c", "--parent", "2")
    assert todo("done", "3").code == 0
    lines = todo.list()
    assert COUNT.findall(line_of(lines, 1)) == [("0", "1")]
    assert COUNT.findall(line_of(lines, 2)) == [("1", "1")]


@pytest.mark.strict
def test_child_of_a_hidden_parent_lists_at_top_level(todo):
    todo.add("trip")
    todo.add("pack", "--parent", "1")
    assert todo("done", "2").code == 0
    lines = todo.list("--done")
    assert ids(lines) == [2]
    assert indent(lines[0]) == 0
