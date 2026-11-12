import json
from datetime import date

import pytest

from todo.cli.format import format_list_item, format_remind_item
from todo.storage import Item


@pytest.fixture
def write(tmp_path):
    def put(*rows):
        (tmp_path / "todo.json").write_text(json.dumps(list(rows)))

    return put


def test_children_print_indented_under_parent_with_count(cli, write):
    write(
        {"id": 1, "title": "trip", "due": "2026-12-01"},
        {"id": 2, "title": "book", "parent": 1, "done": True},
        {"id": 3, "title": "pack", "parent": 1},
        {"id": 4, "title": "other"},
        {"id": 5, "title": "bags", "parent": 3},
        {"id": 6, "title": "pass", "parent": 1},
    )
    assert cli("list") == [
        "[ ] 1: trip 1/3 (due 2026-12-01)",
        "  [x] 2: book",
        "  [ ] 3: pack 0/1",
        "    [ ] 5: bags",
        "  [ ] 6: pass",
        "[ ] 4: other",
    ]


def test_child_of_filtered_out_parent_prints_at_top_level(cli, write):
    write(
        {"id": 1, "title": "trip", "tags": ["a"]},
        {"id": 2, "title": "book", "parent": 1, "tags": ["b"]},
    )
    assert cli("list", "--tag", "b") == ["[ ] 2: book #b"]


def test_child_of_missing_parent_prints_at_top_level(cli, write):
    write({"id": 2, "title": "book", "parent": 9})
    assert cli("list") == ["[ ] 2: book"]


def test_open_filter_hides_done_child_but_count_keeps_it(cli, write):
    write(
        {"id": 1, "title": "trip"},
        {"id": 2, "title": "book", "parent": 1, "done": True},
        {"id": 3, "title": "pack", "parent": 1},
    )
    assert cli("list", "--open") == ["[ ] 1: trip 1/2", "  [ ] 3: pack"]


def test_sort_orders_roots_and_each_parents_children(cli, write):
    write(
        {"id": 1, "title": "a", "due": "2026-12-05"},
        {"id": 2, "title": "b", "due": "2026-12-01"},
        {"id": 3, "title": "a1", "parent": 1, "due": "2026-12-09"},
        {"id": 4, "title": "a2", "parent": 1, "due": "2026-12-02"},
    )
    assert cli("list", "--sort", "due") == [
        "[ ] 2: b (due 2026-12-01)",
        "[ ] 1: a 0/2 (due 2026-12-05)",
        "  [ ] 4: a2 (due 2026-12-02)",
        "  [ ] 3: a1 (due 2026-12-09)",
    ]


def test_search_indents_children_too(cli, write):
    write(
        {"id": 1, "title": "trip"},
        {"id": 2, "title": "trip book", "parent": 1},
    )
    assert cli("search", "trip") == ["[ ] 1: trip 0/1", "  [ ] 2: trip book"]


def test_parent_cycle_lists_both_once_at_top_level(cli, write):
    write(
        {"id": 1, "title": "a", "parent": 2},
        {"id": 2, "title": "b", "parent": 1},
    )
    assert cli("list") == ["[ ] 1: a 0/1", "[ ] 2: b 0/1"]


def test_cycle_hanging_off_a_root_never_drops_items(cli, write):
    write(
        {"id": 1, "title": "root"},
        {"id": 2, "title": "a", "parent": 3},
        {"id": 3, "title": "b", "parent": 2},
    )
    lines = cli("list")
    assert lines == ["[ ] 1: root", "[ ] 2: a 0/1", "[ ] 3: b 0/1"]


def test_remind_ignores_parents(cli, write):
    write(
        {"id": 1, "title": "trip", "due": "2026-11-10"},
        {"id": 2, "title": "book", "parent": 1, "due": "2026-11-10"},
    )
    lines = cli("remind", today=date(2026, 11, 10))
    assert "[ ] 1: trip (due 2026-11-10)" in lines
    assert "[ ] 2: book (due 2026-11-10)" in lines


def test_export_ignores_parents(cli, write):
    write({"id": 1, "title": "trip"}, {"id": 2, "title": "book", "parent": 1})
    out = "\n".join(cli("export", "--format", "csv"))
    assert "1/1" not in out and "  [" not in out


def test_format_list_item_without_children_is_unchanged():
    item = Item(1, "milk")
    assert format_list_item(item) == format_remind_item(item)


def test_format_list_item_counts_children_in_whole_list():
    items = [Item(1, "trip"), Item(2, "a", parent=1, done=True), Item(3, "b", parent=1)]
    assert format_list_item(items[0], items) == "[ ] 1: trip 1/2"
    assert format_list_item(items[1], items, 2) == "    [x] 2: a"
