"""#21 Recurring items in remind and export: the `↻ every RULE` suffix in `todo remind` and Markdown, an `every` CSV
column.

Today is 2026-11-10. Core tests follow the issue's criteria; the strict ones pin what the first build chose (remind
lines without the priority marker or tags, the Markdown line ending like the `todo list` line)."""
import csv
import io

import pytest
from conftest import line_of


def remind(todo):
    return todo.ok("remind")


def test_remind_shows_the_rule_suffix(todo):
    todo.add("rent", "--due", "2026-11-09", "--every", "month")
    todo.add("water", "--due", "2026-11-11", "--every", "2", "weeks")
    lines = remind(todo)
    assert "↻ every month" in line_of(lines, 1)
    assert "↻ every 2 weeks" in line_of(lines, 2)


def test_remind_shows_no_suffix_on_one_off_items(todo):
    todo.add("rent", "--due", "2026-11-09", "--every", "month")
    todo.add("milk", "--due", "2026-11-10")
    assert "↻" not in line_of(remind(todo), 2)


def test_markdown_export_shows_the_rule_suffix(todo):
    todo.add("pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    todo.add("buy milk")
    md = "\n".join(todo.ok("export", "--format", "md"))
    [rent] = [line for line in md.splitlines() if "pay rent" in line]
    [milk] = [line for line in md.splitlines() if "buy milk" in line]
    assert "↻ every month" in rent
    assert "↻" not in milk


def csv_rows(todo):
    return list(csv.reader(io.StringIO("\n".join(todo.ok("export", "--format", "csv")))))


def test_csv_export_adds_the_every_column_last(todo):
    todo.add("pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    todo.add("water plants", "--due", "2026-11-10", "--every", "2", "weeks")
    todo.add("buy milk")
    rows = csv_rows(todo)
    assert rows[0] == ["id", "title", "done", "due", "tags", "priority", "every"]
    assert [(r[1], r[-1]) for r in rows[1:]] == [("pay rent", "month"), ("water plants", "2 weeks"), ("buy milk", "")]


def test_csv_export_with_only_one_off_items_still_has_the_every_column(todo):
    todo.add("buy milk", "--due", "2026-11-01", "--tag", "home", "--priority", "high")
    rows = csv_rows(todo)
    assert rows == [["id", "title", "done", "due", "tags", "priority", "every"],
                    ["1", "buy milk", "no", "2026-11-01", "home", "high", ""]]


@pytest.mark.strict
def test_remind_lines_show_the_rule_without_priority_or_tags(todo):
    todo.add("rent", "--due", "2026-11-09", "--every", "month", "--priority", "high", "--tag", "home")
    todo.add("water", "--due", "2026-11-11", "--every", "2", "weeks")
    todo.add("milk", "--due", "2026-11-10")
    assert remind(todo) == [
        "overdue:",
        "[ ] 1: rent (due 2026-11-09) ↻ every month",
        "due soon:",
        "[ ] 2: water (due 2026-11-11) ↻ every 2 weeks",
        "[ ] 3: milk (due 2026-11-10)",
    ]


@pytest.mark.strict
def test_markdown_line_ends_like_the_list_line(todo):
    todo.add("pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home", "--priority", "high")
    md = todo.ok("export", "--format", "md")[0].splitlines()
    assert "- [ ] pay rent (due 2026-11-01) ↻ every month !! #home" in md
    assert md[2] == "- " + todo.list()[0].replace(" 1:", "", 1)
