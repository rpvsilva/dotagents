import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from todo.cli.format import format_item, format_list_item
from todo.storage import Item, JsonStore

TODAY = date(2026, 11, 10)


def test_format_item():
    assert format_item(Item(1, "milk")) == "[ ] 1: milk"
    assert format_item(Item(2, "eggs", done=True, due=date(2026, 11, 1))) == (
        "[x] 2: eggs (due 2026-11-01)"
    )


def test_add_and_list(cli):
    assert cli("add", "milk") == ["added 1: milk"]
    assert cli("list") == ["[ ] 1: milk"]


def test_done(cli):
    cli("add", "milk")
    cli("done", "1")
    assert cli("list") == ["[x] 1: milk"]


def test_add_due_then_list(cli):
    assert cli("add", "milk", "--due", "2026-11-01") == ["added 1: milk"]
    cli("add", "eggs")
    assert cli("list") == ["[ ] 1: milk (due 2026-11-01)", "[ ] 2: eggs"]


def test_done_keeps_due(cli, tmp_path):
    cli("add", "milk", "--due", "2026-11-01")
    cli("done", "1")
    assert JsonStore(tmp_path / "todo.json").load()[0].due == date(2026, 11, 1)
    assert cli("list") == ["[x] 1: milk (due 2026-11-01)"]


@pytest.mark.parametrize("bad", ["2026-13-01", "20261101", "tomorrow", "2026-1-5", "2026-01-5"])
def test_add_rejects_invalid_due(cli, tmp_path, capsys, bad):
    with pytest.raises(SystemExit) as exc:
        cli("add", "milk", "--due", bad)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert bad in err and "YYYY-MM-DD" in err
    assert not (tmp_path / "todo.json").exists()


def test_add_invalid_due_leaves_existing_file_unchanged(cli, tmp_path):
    cli("add", "eggs", "--due", "2026-11-01")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit):
        cli("add", "milk", "--due", "2026-02-30")
    assert (tmp_path / "todo.json").read_text() == before


def test_load_invalid_stored_due_names_item(cli, tmp_path, capsys):
    (tmp_path / "todo.json").write_text(
        '[{"id": 1, "title": "milk", "done": false, "due": "soon"}]'
    )
    with pytest.raises(SystemExit) as exc:
        cli("list")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "item 1" in err and "'soon'" in err and "YYYY-MM-DD" in err


def test_list_overdue(cli):
    cli("add", "late", "--due", "2026-11-09")
    cli("add", "today", "--due", "2026-11-10")
    cli("add", "late-done", "--due", "2026-11-01")
    cli("done", "3")
    cli("add", "no-due")
    assert cli("list", "--overdue", today=TODAY) == ["[ ] 1: late (due 2026-11-09)"]
    assert len(cli("list", today=TODAY)) == 4


def test_remind_mixed(cli):
    cli("add", "late", "--due", "2026-11-09")
    cli("add", "today", "--due", "2026-11-10")
    cli("add", "day3", "--due", "2026-11-13")
    cli("add", "day4", "--due", "2026-11-14")
    cli("add", "done-soon", "--due", "2026-11-11")
    cli("done", "5")
    cli("add", "no-due")
    assert cli("remind", today=TODAY) == [
        "overdue:",
        "[ ] 1: late (due 2026-11-09)",
        "due soon:",
        "[ ] 2: today (due 2026-11-10)",
        "[ ] 3: day3 (due 2026-11-13)",
    ]


def test_remind_empty_group_heading_kept(cli):
    cli("add", "today", "--due", "2026-11-10")
    assert cli("remind", today=TODAY) == [
        "overdue:",
        "due soon:",
        "[ ] 1: today (due 2026-11-10)",
    ]


def test_remind_nothing_due(cli):
    assert cli("remind", today=TODAY) == ["nothing due"]
    cli("add", "far", "--due", "2026-12-31")
    cli("add", "no-due")
    assert cli("remind", today=TODAY) == ["nothing due"]


@pytest.mark.parametrize("module", ["todo", "todo.cli"])
def test_runs_as_module(tmp_path, module):
    repo = Path(__file__).resolve().parents[2]
    env = {"TODO_FILE": str(tmp_path / "todo.json"), "PYTHONPATH": str(repo)}
    result = subprocess.run(
        [sys.executable, "-m", module, "add", "milk"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "added 1: milk\n"


def test_done_missing_id(cli, tmp_path, capsys):
    cli("add", "milk")
    with pytest.raises(SystemExit) as exc:
        cli("done", "99")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err == "todo: error: no item 99\n"
    assert "Traceback" not in err


def test_usage_error_exits_2(cli, capsys, monkeypatch):
    from todo.cli.commands import COMMANDS
    from todo.cli.errors import UsageError

    def bad_run(args, ctx):
        raise UsageError("too many things")

    monkeypatch.setattr(COMMANDS["list"], "run", bad_run)
    with pytest.raises(SystemExit) as exc:
        cli("list")
    assert exc.value.code == 2
    assert capsys.readouterr().err == "todo: error: too many things\n"


def stored_tags(tmp_path):
    return [i.tags for i in JsonStore(tmp_path / "todo.json").load()]


@pytest.mark.parametrize(
    "flags",
    [["--tag", "groceries", "--tag", "home"], ["--tag", "groceries,home"]],
)
def test_add_with_tags(cli, tmp_path, flags):
    assert cli("add", "buy milk", *flags) == ["added 1: buy milk"]
    assert stored_tags(tmp_path) == [["groceries", "home"]]


def test_add_without_tags_stores_none(cli, tmp_path):
    cli("add", "milk")
    assert stored_tags(tmp_path) == [[]]


def test_add_tags_are_normalised(cli, tmp_path):
    cli("add", "a", "--tag", " Work ")
    cli("add", "b", "--tag", "a", "--tag", "a")
    cli("add", "c", "--tag", "Home,home", "--tag", "HOME")
    assert stored_tags(tmp_path) == [["work"], ["a"], ["home"]]


@pytest.mark.parametrize("bad", ["a b", "", "a,", " "])
def test_add_rejects_bad_tag(cli, tmp_path, capsys, bad):
    cli("add", "eggs")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("add", "milk", "--tag", bad)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert repr(bad) in err and "tag" in err
    assert (tmp_path / "todo.json").read_text() == before


def test_add_rejects_more_than_max_tags(cli, tmp_path, capsys):
    tags = ",".join(f"t{n}" for n in range(11))
    with pytest.raises(SystemExit) as exc:
        cli("add", "milk", "--tag", tags)
    assert exc.value.code == 2
    assert "10" in capsys.readouterr().err
    assert not (tmp_path / "todo.json").exists()


def test_add_max_tags_after_dedupe_is_allowed(cli, tmp_path):
    tags = ",".join(f"t{n}" for n in range(10))
    cli("add", "milk", "--tag", tags, "--tag", "T0")
    assert len(stored_tags(tmp_path)[0]) == 10


def test_load_invalid_stored_tag_names_item(cli, tmp_path, capsys):
    (tmp_path / "todo.json").write_text('[{"id": 2, "title": "milk", "tags": ["a b"]}]')
    with pytest.raises(SystemExit) as exc:
        cli("list")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "item 2" in err and "'a b'" in err


def stored_priorities(tmp_path):
    return [i.priority for i in JsonStore(tmp_path / "todo.json").load()]


@pytest.mark.parametrize("level", ["high", "medium", "low"])
def test_add_with_priority(cli, tmp_path, level):
    assert cli("add", "pay rent", "--priority", level) == ["added 1: pay rent"]
    assert stored_priorities(tmp_path) == [level]


def test_add_without_priority_stores_none(cli, tmp_path):
    cli("add", "milk")
    assert stored_priorities(tmp_path) == [None]


def test_priority_sets_and_clears(cli, tmp_path):
    cli("add", "pay rent")
    before = JsonStore(tmp_path / "todo.json").load()[0].updated_at
    assert cli("priority", "1", "high") == ["priority 1: high"]
    item = JsonStore(tmp_path / "todo.json").load()[0]
    assert item.priority == "high" and item.updated_at >= before
    assert cli("priority", "1", "none") == ["priority 1: none"]
    assert stored_priorities(tmp_path) == [None]


def test_priority_moves_updated_at(cli, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from todo.storage import Store

    stamps = iter([datetime(2026, 11, 1, tzinfo=timezone.utc),
                   datetime(2026, 11, 2, tzinfo=timezone.utc)])
    monkeypatch.setattr(Store, "clock", staticmethod(lambda: next(stamps)))
    cli("add", "pay rent")
    cli("priority", "1", "low")
    assert JsonStore(tmp_path / "todo.json").load()[0].updated_at == datetime(
        2026, 11, 2, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "argv", [["add", "pay rent", "--priority", "urgent"], ["priority", "1", "urgent"],
             ["add", "pay rent", "--priority", "none"]],
)
def test_bad_priority_exits_2(cli, tmp_path, capsys, argv):
    cli("add", "eggs")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli(*argv)
    assert exc.value.code == 2
    assert "'urgent'" in capsys.readouterr().err or argv[-1] == "none"
    assert (tmp_path / "todo.json").read_text() == before


def test_priority_missing_id(cli, capsys):
    cli("add", "milk")
    with pytest.raises(SystemExit) as exc:
        cli("priority", "99", "high")
    assert exc.value.code == 1
    assert capsys.readouterr().err == "todo: error: no item 99\n"


def test_load_invalid_stored_priority_names_item(cli, tmp_path, capsys):
    (tmp_path / "todo.json").write_text('[{"id": 3, "title": "rent", "priority": "urgent"}]')
    with pytest.raises(SystemExit) as exc:
        cli("list")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "item 3" in err and "'urgent'" in err


@pytest.mark.parametrize(
    "argv, line",
    [
        (
            ["buy milk", "--due", "2026-11-01", "--priority", "high", "--tag", "groceries,home"],
            "[ ] 1: buy milk (due 2026-11-01) !! #groceries #home",
        ),
        (["milk", "--priority", "high", "--tag", "a"], "[ ] 1: milk !! #a"),
        (["milk", "--priority", "medium"], "[ ] 1: milk !"),
        (["milk", "--priority", "low", "--tag", "a"], "[ ] 1: milk #a"),
        (["milk", "--tag", "a", "--tag", "b"], "[ ] 1: milk #a #b"),
        (["milk", "--due", "2026-11-01"], "[ ] 1: milk (due 2026-11-01)"),
    ],
)
def test_list_shows_priority_and_tags(cli, argv, line):
    cli("add", *argv)
    assert cli("list") == [line]


def test_list_shows_marker_after_priority_command(cli):
    cli("add", "milk")
    cli("priority", "1", "medium")
    assert cli("list") == ["[ ] 1: milk !"]


def test_remind_ignores_priority_and_tags(cli):
    cli("add", "late", "--due", "2026-11-09")
    cli("add", "soon", "--due", "2026-11-11", "--priority", "high", "--tag", "work,home")
    cli("add", "today", "--due", "2026-11-10", "--priority", "low")
    assert cli("remind", today=TODAY) == [
        "overdue:",
        "[ ] 1: late (due 2026-11-09)",
        "due soon:",
        "[ ] 2: soon (due 2026-11-11)",
        "[ ] 3: today (due 2026-11-10)",
    ]


def test_format_list_item_matches_format_item_without_extras():
    item = Item(2, "eggs", done=True, due=date(2026, 11, 1))
    assert format_list_item(item) == format_item(item)


def test_tag_adds_and_removes(cli, tmp_path):
    cli("add", "milk", "--tag", "home")
    assert cli("tag", "1", "+work", "-home") == ["tagged 1: #work"]
    assert stored_tags(tmp_path) == [["work"]]


def test_tag_bare_word_adds_and_normalises(cli, tmp_path):
    cli("add", "milk")
    assert cli("tag", "1", "Work", "+HOME") == ["tagged 1: #work #home"]
    assert stored_tags(tmp_path) == [["work", "home"]]


def test_tag_remove_only_and_absent_is_noop(cli, tmp_path):
    cli("add", "milk", "--tag", "home")
    assert cli("tag", "1", "-home") == ["tagged 1:"]
    assert stored_tags(tmp_path) == [[]]
    assert cli("tag", "1", "-home") == ["tagged 1:"]


def test_tag_dash_h_is_a_removal(cli, tmp_path):
    cli("add", "milk", "--tag", "h,x")
    assert cli("tag", "1", "-h") == ["tagged 1: #x"]


def test_tag_stamps_updated_at(cli, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from todo.storage import Store

    t1 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    stamps = iter([t1, t2])
    monkeypatch.setattr(Store, "clock", staticmethod(lambda: next(stamps)))
    cli("add", "milk")
    cli("tag", "1", "+work")
    assert JsonStore(tmp_path / "todo.json").load()[0].updated_at == t2


@pytest.mark.parametrize("bad", ["+a b", "+", "-", " "])
def test_tag_rejects_bad_tag(cli, tmp_path, capsys, bad):
    cli("add", "milk")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("tag", "1", bad)
    assert exc.value.code == 2
    assert "tag" in capsys.readouterr().err
    assert (tmp_path / "todo.json").read_text() == before


def test_tag_over_max_exits_2_and_saves_nothing(cli, tmp_path, capsys):
    cli("add", "milk", "--tag", ",".join(f"t{n}" for n in range(10)))
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("tag", "1", "+extra")
    assert exc.value.code == 2
    assert "10" in capsys.readouterr().err
    assert (tmp_path / "todo.json").read_text() == before


def test_tag_without_changes_exits_2(cli, capsys):
    cli("add", "milk")
    with pytest.raises(SystemExit) as exc:
        cli("tag", "1")
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("todo: error:")


def test_tag_missing_id(cli, capsys):
    cli("add", "milk")
    with pytest.raises(SystemExit) as exc:
        cli("tag", "99", "+work")
    assert exc.value.code == 1
    assert capsys.readouterr().err == "todo: error: no item 99\n"


def test_tag_on_bad_stored_file_exits_1(cli, tmp_path, capsys):
    (tmp_path / "todo.json").write_text('[{"id": 2, "title": "milk", "tags": ["a b"]}]')
    with pytest.raises(SystemExit) as exc:
        cli("tag", "2", "+work")
    assert exc.value.code == 1
    assert "item 2" in capsys.readouterr().err


# Filter, search and sort on `todo list`, and `todo search` (ticket 06)


def ids(lines):
    return [int(line.split(":")[0].split()[-1]) for line in lines]


@pytest.fixture
def board(cli):
    """Five items with a mix of tags, priority, due date and done state."""
    cli("add", "Buy Milk", "--tag", "groceries,home", "--priority", "medium", "--due", "2026-11-12")
    cli("add", "write report", "--tag", "work", "--priority", "high", "--due", "2026-11-05")
    cli("add", "call mum", "--tag", "home", "--due", "2026-11-12")
    cli("add", "file taxes", "--tag", "work,home", "--priority", "high")
    cli("add", "milkshake", "--priority", "low", "--due", "2026-11-01")
    cli("done", "5")
    return cli


def test_list_without_new_flags_is_by_id(board):
    assert ids(board("list", today=TODAY)) == [1, 2, 3, 4, 5]


def test_list_tag(board):
    assert ids(board("list", "--tag", "work")) == [2, 4]


def test_list_tag_is_normalised(board):
    assert ids(board("list", "--tag", " WORK ")) == [2, 4]


def test_list_two_tags_need_both(board):
    assert ids(board("list", "--tag", "work", "--tag", "home")) == [4]


def test_list_priority_is_exact(board):
    assert ids(board("list", "--priority", "high")) == [2, 4]
    assert ids(board("list", "--priority", "medium")) == [1]
    assert ids(board("list", "--priority", "low")) == [5]


def test_list_open(board):
    assert ids(board("list", "--open")) == [1, 2, 3, 4]


def test_list_done(board):
    assert ids(board("list", "--done")) == [5]


def test_list_open_and_done_exits_2(board, capsys):
    with pytest.raises(SystemExit) as exc:
        board("list", "--open", "--done")
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_list_search_title_mixed_case(board):
    assert ids(board("list", "--search", "MiLk")) == [1, 5]


def test_list_search_tag_mixed_case(board):
    assert ids(board("list", "--search", "GROCER")) == [1]
    assert ids(board("list", "--search", "Hom")) == [1, 3, 4]


def test_list_search_does_not_match_hash(board):
    assert board("list", "--search", "#home") == []


def test_list_filters_combine(board):
    assert ids(board("list", "--tag", "home", "--priority", "high")) == [4]
    assert ids(board("list", "--search", "milk", "--open")) == [1]


def test_list_filters_combine_with_overdue(board):
    # overdue on TODAY: 2 (2026-11-05); 5 is done so not overdue
    assert ids(board("list", "--overdue", "--tag", "work", today=TODAY)) == [2]
    assert board("list", "--overdue", "--tag", "home", today=TODAY) == []


def test_list_sort_priority_ties_by_id_unset_last(board):
    assert ids(board("list", "--sort", "priority")) == [2, 4, 1, 5, 3]


def test_list_sort_due_ties_by_id_none_last(board):
    assert ids(board("list", "--sort", "due")) == [5, 2, 1, 3, 4]


def test_list_sort_with_filter(board):
    assert ids(board("list", "--tag", "home", "--sort", "due")) == [1, 3, 4]


def test_list_sort_rejects_unknown(board):
    with pytest.raises(SystemExit) as exc:
        board("list", "--sort", "title")
    assert exc.value.code == 2


def test_list_filtered_lines_keep_format(board):
    assert board("list", "--tag", "groceries") == [
        "[ ] 1: Buy Milk (due 2026-11-12) ! #groceries #home"
    ]


@pytest.mark.parametrize("text", ["milk", "HOME", "nothing-matches"])
def test_search_equals_list_search(board, text):
    assert board("search", text) == board("list", "--search", text)


def test_search_on_empty_store(cli):
    assert cli("search", "milk") == []


# Review fixes on PR #15 (ticket 09)


def test_tag_comma_list_adds_and_removes_each(cli, tmp_path):
    cli("add", "milk")
    assert cli("tag", "1", "+a,b") == ["tagged 1: #a #b"]
    assert cli("tag", "1", "c,D") == ["tagged 1: #a #b #c #d"]
    assert cli("tag", "1", "-a,b") == ["tagged 1: #c #d"]
    assert stored_tags(tmp_path) == [["c", "d"]]


def test_tag_add_and_remove_same_tag_exits_2(cli, tmp_path, capsys):
    cli("add", "milk", "--tag", "x")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("tag", "1", "+x", "-X")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("todo: error:") and "x" in err and "both" in err
    assert (tmp_path / "todo.json").read_text() == before


def test_unrelated_key_error_is_not_reported_as_missing_item(cli, capsys, monkeypatch):
    from todo.cli.commands import list_cmd

    def broken(args, ctx):
        raise KeyError("oops")

    monkeypatch.setattr(list_cmd, "run", broken)
    with pytest.raises(KeyError):
        cli("list")
    assert "no item" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "every, rule",
    [
        (["month"], "month"),
        (["2", "weeks"], "2 weeks"),
        (["2 weeks"], "2 weeks"),
        (["day"], "day"),
        (["year"], "year"),
        (["3", "Months"], "3 months"),
    ],
)
def test_add_every_stores_rule(cli, tmp_path, every, rule):
    assert cli("add", "rent", "--every", *every, today=TODAY) == ["added 1: rent"]
    [item] = JsonStore(tmp_path / "todo.json").load()
    assert item.every == rule


def test_add_every_two_words_then_tag(cli, tmp_path):
    cli("add", "x", "--every", "2", "weeks", "--tag", "a", today=TODAY)
    [item] = JsonStore(tmp_path / "todo.json").load()
    assert (item.title, item.every, item.tags) == ("x", "2 weeks", ["a"])


def test_add_every_without_due_is_due_today(cli, tmp_path):
    cli("add", "rent", "--every", "month", today=date(2026, 3, 7))
    [item] = JsonStore(tmp_path / "todo.json").load()
    assert item.due == date(2026, 3, 7)


def test_add_every_keeps_due(cli, tmp_path):
    cli("add", "rent", "--every", "month", "--due", "2026-11-01", today=TODAY)
    [item] = JsonStore(tmp_path / "todo.json").load()
    assert item.due == date(2026, 11, 1)


@pytest.mark.parametrize("every", [["fortnight"], ["0", "days"], ["0 days"], ["2", "3", "weeks"]])
def test_add_bad_every_exits_2_and_saves_nothing(cli, tmp_path, capsys, every):
    with pytest.raises(SystemExit) as exc:
        cli("add", "rent", "--every", *every, today=TODAY)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid rule" in err and "Traceback" not in err
    assert not (tmp_path / "todo.json").exists()


def test_list_shows_rule_after_due_before_tags(cli):
    cli("add", "a", "--due", "2026-10-01", today=TODAY)
    cli("add", "b", today=TODAY)
    cli("add", "c", today=TODAY)
    cli("add", "pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home", today=TODAY)
    assert cli("list")[3] == "[ ] 4: pay rent (due 2026-11-01) ↻ every month #home"


def test_list_shows_rule_before_priority(cli):
    cli("add", "water", "--every", "2", "weeks", "--priority", "high", "--tag", "home", today=TODAY)
    assert cli("list") == ["[ ] 1: water (due 2026-11-10) ↻ every 2 weeks !! #home"]


def test_done_daily_item_late_on_its_day_is_next_due_tomorrow(cli):
    cli("add", "water", "--every", "day", today=TODAY)
    assert cli("done", "1", today=TODAY) == ["done 1", "next 2: water (due 2026-11-11)"]
    assert cli("list", today=TODAY) == [
        "[x] 1: water (due 2026-11-10) ↻ every day",
        "[ ] 2: water (due 2026-11-11) ↻ every day",
    ]


def test_done_overdue_monthly_skips_missed_months(cli):
    cli("add", "rent", "--every", "month", "--due", "2026-08-15", today=TODAY)
    cli("done", "1", today=TODAY)
    assert cli("list", "--open", today=TODAY) == ["[ ] 2: rent (due 2026-11-15) ↻ every month"]


def test_done_twice_on_recurring_item_creates_one_occurrence(cli):
    cli("add", "water", "--every", "day", today=TODAY)
    cli("done", "1", today=TODAY)
    cli("done", "1", today=TODAY)
    assert len(cli("list", today=TODAY)) == 2


def test_remind_shows_rule_suffix_without_priority_or_tags(cli):
    cli("add", "rent", "--due", "2026-11-09", "--every", "month", "--priority", "high", "--tag", "home", today=TODAY)
    cli("add", "water", "--due", "2026-11-11", "--every", "2", "weeks", today=TODAY)
    cli("add", "milk", "--due", "2026-11-10")
    assert cli("remind", today=TODAY) == [
        "overdue:",
        "[ ] 1: rent (due 2026-11-09) ↻ every month",
        "due soon:",
        "[ ] 2: water (due 2026-11-11) ↻ every 2 weeks",
        "[ ] 3: milk (due 2026-11-10)",
    ]


def test_skip_moves_recurring_item_to_next_occurrence(cli):
    cli("add", "rent", "--every", "month", "--due", "2026-11-01", today=TODAY)
    assert cli("skip", "1", today=TODAY) == ["skipped 1: rent, next due 2026-12-01"]
    assert cli("list", today=TODAY) == ["[ ] 1: rent (due 2026-12-01) ↻ every month"]


def test_skip_monthly_from_jan_31_twice_lands_on_mar_31(cli):
    cli("add", "rent", "--every", "month", "--due", "2027-01-31", today=TODAY)
    cli("skip", "1", today=date(2027, 1, 31))
    assert cli("skip", "1", today=date(2027, 2, 28)) == ["skipped 1: rent, next due 2027-03-31"]


def test_skip_one_off_exits_2_and_saves_nothing(cli, tmp_path, capsys):
    cli("add", "milk", "--due", "2026-11-10")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("skip", "1", today=TODAY)
    assert exc.value.code == 2
    assert capsys.readouterr().err == "todo: error: item 1 is not recurring\n"
    assert (tmp_path / "todo.json").read_text() == before


def test_skip_done_item_exits_2(cli, capsys):
    cli("add", "water", "--every", "day", today=TODAY)
    cli("done", "1", today=TODAY)
    with pytest.raises(SystemExit) as exc:
        cli("skip", "1", today=TODAY)
    assert exc.value.code == 2
    assert capsys.readouterr().err == "todo: error: item 1 is already done, nothing to skip\n"


def test_skip_missing_id_exits_1(cli, capsys):
    cli("add", "milk")
    with pytest.raises(SystemExit) as exc:
        cli("skip", "99", today=TODAY)
    assert exc.value.code == 1
    assert capsys.readouterr().err == "todo: error: no item 99\n"


def test_stop_then_list_shows_no_rule_and_done_creates_nothing(cli):
    cli("add", "rent", "--due", "2026-11-01", "--every", "month", "--tag", "home", today=TODAY)
    assert cli("stop", "1", today=TODAY) == ["stopped 1"]
    assert cli("list", today=TODAY) == ["[ ] 1: rent (due 2026-11-01) #home"]
    assert cli("done", "1", today=TODAY) == ["done 1"]
    assert cli("list", today=TODAY) == ["[x] 1: rent (due 2026-11-01) #home"]


def test_stop_done_recurring_item_keeps_done_and_due(cli):
    cli("add", "water", "--every", "day", today=TODAY)
    cli("done", "1", today=TODAY)
    cli("stop", "1", today=TODAY)
    assert cli("list", today=TODAY) == [
        "[x] 1: water (due 2026-11-10)",
        "[ ] 2: water (due 2026-11-11) ↻ every day",
    ]


def test_stop_one_off_exits_2_and_saves_nothing(cli, tmp_path, capsys):
    cli("add", "milk", "--due", "2026-11-01")
    before = (tmp_path / "todo.json").read_text()
    with pytest.raises(SystemExit) as exc:
        cli("stop", "1", today=TODAY)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "item 1 is not recurring" in err and "Traceback" not in err
    assert (tmp_path / "todo.json").read_text() == before


def test_stop_missing_id_exits_1(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("stop", "9", today=TODAY)
    assert exc.value.code == 1
    assert "no item 9" in capsys.readouterr().err


@pytest.mark.parametrize("every", [["9999", "years"], ["999999999", "days"]])
def test_add_every_past_last_date_exits_2_and_saves_nothing(cli, tmp_path, capsys, every):
    with pytest.raises(SystemExit) as exc:
        cli("add", "rent", "--every", *every, today=TODAY)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "9999-12-31" in err and "Traceback" not in err
    assert not (tmp_path / "todo.json").exists()


@pytest.mark.parametrize("command", ["done", "skip"])
def test_done_or_skip_on_stored_rule_past_last_date_exits_2(cli, tmp_path, capsys, command):
    (tmp_path / "todo.json").write_text(
        '[{"id": 1, "title": "rent", "due": "2026-11-10", "every": "9999 years"}]'
    )
    with pytest.raises(SystemExit) as exc:
        cli(command, "1", today=TODAY)
    assert exc.value.code == 2
    assert "9999-12-31" in capsys.readouterr().err


def test_titles_from_the_file_cannot_drive_the_terminal_or_markdown(cli, tmp_path):
    (tmp_path / "todo.json").write_text(json.dumps([{"id": 1, "title": "x\x1b[2J\n# Done", "done": False}]))
    assert cli("list") == ["[ ] 1: x?[2J?# Done"]
    assert cli("export", "--format", "md")[0].splitlines()[-1] == "- [ ] x?\\[2J?# Done"


def test_export_out_replaces_a_planted_symlink_not_its_target(cli, tmp_path):
    target = tmp_path / "victim.rc"
    target.write_text("keep me\n")
    out = tmp_path / "todos.csv"
    out.symlink_to(target)
    cli("add", "milk")
    cli("export", "--format", "csv", "--out", str(out))
    assert target.read_text() == "keep me\n"
    assert not out.is_symlink() and "milk" in out.read_text()


def run_module(*argv, env, cwd, flags=()):
    repo = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, *flags, "-m", "todo", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={"PYTHONPATH": str(repo), **env}, cwd=cwd, check=False,
    )


def test_default_store_ignores_todo_json_in_current_directory(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "cloned-repo"
    repo.mkdir()
    planted = repo / "todo.json"
    planted.write_text('[{"id": 1, "title": "attacker item", "done": false}]')
    before = planted.read_text()
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    added = run_module("add", "call bank", env=env, cwd=repo)
    assert added.returncode == 0, added.stderr
    listed = run_module("list", env=env, cwd=repo)
    assert listed.stdout == "[ ] 1: call bank\n"
    assert planted.read_text() == before
    assert "call bank" in (home / ".todo.json").read_text()


def test_export_out_writes_utf8_under_an_ascii_locale(tmp_path):
    store = tmp_path / "todo.json"
    JsonStore(store).save([Item(1, "café ↻ 💸")])
    out = tmp_path / "todo.md"
    env = {"TODO_FILE": str(store), "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0"}
    result = run_module("export", "--format", "md", "--out", str(out), env=env, cwd=tmp_path, flags=("-X", "utf8=0"))
    assert result.returncode == 0, result.stderr
    assert out.read_bytes().decode("utf-8") == "# Todo\n\n- [ ] café ↻ 💸\n"


@pytest.mark.parametrize("argv", [["list"], ["search", "rent"], ["remind"], ["export", "--format", "md"]])
def test_output_survives_a_title_the_stdout_codec_cannot_encode(tmp_path, argv):
    store = tmp_path / "todo.json"
    JsonStore(store).save([Item(1, "pay rent 💸", due=date.today())])
    env = {"TODO_FILE": str(store), "PYTHONIOENCODING": "cp1252"}
    result = run_module(*argv, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "pay rent ?" in result.stdout
