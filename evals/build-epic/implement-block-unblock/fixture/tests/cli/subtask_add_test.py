from datetime import date

import pytest

from todo.storage import ItemNotFound, JsonStore


def load(tmp_path):
    return JsonStore(tmp_path / "todo.json").load()


def test_add_with_parent_stores_parent(cli, tmp_path):
    cli("add", "trip")
    assert cli("add", "tickets", "--parent", "1") == ["added 2: tickets"]
    items = load(tmp_path)
    assert items[0].parent is None
    assert items[1].parent == 1


def test_parent_combines_with_other_options_in_any_order(cli, tmp_path):
    cli("add", "trip")
    cli(
        "add", "tickets",
        "--tag", "a,b", "--parent", "1", "--priority", "high",
        "--due", "2026-12-01", "--every", "2", "weeks",
    )
    cli("add", "hotel", "--every", "1", "week", "--parent", "1", "--due", "2026-12-02")
    tickets, hotel = load(tmp_path)[1:]
    assert tickets.parent == 1
    assert tickets.tags == ["a", "b"]
    assert tickets.priority == "high"
    assert tickets.due == date(2026, 12, 1)
    assert tickets.every == "2 weeks"
    assert hotel.parent == 1 and hotel.every == "week"


def test_unknown_parent_exits_2_and_file_absent(cli, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("add", "x", "--parent", "99")
    assert exc.value.code == 2
    assert capsys.readouterr().err == "todo: error: no item 99\n"
    assert not (tmp_path / "todo.json").exists()


def test_unknown_parent_leaves_existing_file_unchanged(cli, tmp_path, capsys):
    cli("add", "trip")
    before = (tmp_path / "todo.json").read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli("add", "x", "--parent", "99")
    assert exc.value.code == 2
    assert capsys.readouterr().err == "todo: error: no item 99\n"
    assert (tmp_path / "todo.json").read_bytes() == before


def test_done_item_may_be_parent(cli, tmp_path):
    cli("add", "trip")
    cli("done", "1")
    assert cli("add", "tickets", "--parent", "1") == ["added 2: tickets"]
    assert load(tmp_path)[1].parent == 1


def test_store_add_unknown_parent_raises_before_saving(tmp_path):
    store = JsonStore(tmp_path / "todo.json")
    with pytest.raises(ItemNotFound):
        store.add("x", parent=5)
    assert not (tmp_path / "todo.json").exists()
    store.add("trip")
    assert store.add("sub", parent=1).parent == 1


def test_done_on_recurring_child_creates_next_occurrence_with_same_parent(cli, tmp_path):
    cli("add", "trip")
    cli("add", "pay", "--every", "month", "--due", "2026-11-01", "--parent", "1")
    cli("done", "2")
    items = load(tmp_path)
    assert items[1].done and items[1].parent == 1
    assert len(items) == 3
    assert items[2].every == "month" and items[2].parent == 1 and not items[2].done


def test_skip_on_recurring_child_keeps_parent(cli, tmp_path):
    cli("add", "trip")
    cli("add", "pay", "--every", "month", "--due", "2026-11-01", "--parent", "1")
    cli("skip", "2")
    items = load(tmp_path)
    assert len(items) == 2 and items[1].parent == 1 and not items[1].done
