"""#406 item dependencies: `todo block ID --on OTHER`, `todo unblock ID --on OTHER`, a waiting mark in `todo list`.

Spec behaviours 16-21; the strict tests pin open questions 6 (the ` waiting on A,B` mark and its place, only an open
blocker counts, repeats change nothing) and 14 (`blocked ID on OTHER`, `unblocked ID from OTHER`)."""
import re

import pytest
from conftest import line_of

WAITING = re.compile(r"wait", re.IGNORECASE)


def marked(todo, item_id):
    return bool(WAITING.search(line_of(todo.list(), item_id)))


def test_block_marks_the_waiting_item_only(todo):
    todo.add("paint the wall")
    todo.add("buy paint")
    assert not marked(todo, 1)
    assert todo("block", "1", "--on", "2").code == 0
    line = line_of(todo.list(), 1)
    assert WAITING.search(line) and "2" in line.split(":", 1)[1], line
    assert not marked(todo, 2)


def test_finishing_the_blocker_clears_the_mark(todo):
    todo.add("paint the wall")
    todo.add("buy paint")
    assert todo("block", "1", "--on", "2").code == 0
    assert marked(todo, 1)
    assert todo("done", "2").code == 0
    assert not marked(todo, 1)


def test_unblock_removes_the_wait(todo):
    todo.add("paint the wall")
    todo.add("buy paint")
    todo("block", "1", "--on", "2")
    assert todo("unblock", "1", "--on", "2").code == 0
    assert not marked(todo, 1)


def test_direct_cycle_is_refused(todo):
    todo.add("a")
    todo.add("b")
    assert todo("block", "1", "--on", "2").code == 0
    assert todo("block", "2", "--on", "1").code == 2
    assert not marked(todo, 2)
    assert marked(todo, 1)


def test_indirect_cycle_is_refused(todo):
    for title in ("a", "b", "c"):
        todo.add(title)
    assert todo("block", "1", "--on", "2").code == 0
    assert todo("block", "2", "--on", "3").code == 0
    assert todo("block", "3", "--on", "1").code == 2
    assert not marked(todo, 3)


def test_blocking_an_item_on_itself_is_refused(todo):
    todo.add("a")
    todo.add("b")
    assert todo("block", "1", "--on", "1").code == 2
    assert not marked(todo, 1)
    assert todo("block", "2", "--on", "1").code == 0  # the command exists: the refusal above was the self-block


def test_readme_documents_block_and_unblock(readme):
    assert "todo block" in readme
    assert "todo unblock" in readme


@pytest.mark.strict
def test_mark_names_open_blockers_before_the_priority(todo):
    todo.add("paint", "--due", "2026-07-01", "--every", "week", "--priority", "high", "--tag", "home")
    todo.add("buy paint")
    todo.add("borrow ladder")
    before = line_of(todo.list(), 1)
    todo("block", "1", "--on", "3")
    todo("block", "1", "--on", "2")
    assert line_of(todo.list(), 1) == before.replace(" !!", " waiting on 2,3 !!")


@pytest.mark.strict
def test_block_and_unblock_print_what_they_did(todo):
    todo.add("a")
    todo.add("b")
    assert todo("block", "1", "--on", "2").lines == ["blocked 1 on 2"]
    assert todo("unblock", "1", "--on", "2").lines == ["unblocked 1 from 2"]


@pytest.mark.strict
def test_unknown_ids_and_repeats(todo):
    todo.add("a")
    todo.add("b")
    assert todo("block", "1", "--on", "99").code == 2
    assert todo("block", "99", "--on", "1").code == 2
    assert todo("unblock", "1", "--on", "2").code == 0
    assert todo("block", "1", "--on", "2").code == 0
    assert todo("block", "1", "--on", "2").code == 0
    assert line_of(todo.list(), 1) == "[ ] 1: a waiting on 2"


@pytest.mark.strict
def test_a_done_blocker_does_not_count(todo):
    todo.add("a")
    todo.add("b")
    todo("done", "2")
    assert todo("block", "1", "--on", "2").code == 0
    assert line_of(todo.list(), 1) == "[ ] 1: a"
