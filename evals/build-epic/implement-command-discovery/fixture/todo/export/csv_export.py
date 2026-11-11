"""CSV export: one header row, then one row per item."""
import csv
import io

from todo.storage import Item

COLUMNS = ["id", "title", "done", "due", "tags", "priority", "every"]
# A cell starting with one of these runs as a formula in Excel or Sheets.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def cell(text: str) -> str:
    """Prefix a would-be formula with `'` so spreadsheets show it as text.

    Each `;`-part is guarded too: spreadsheets whose list separator is `;` split the cell there.
    """
    return ";".join("'" + part if part.startswith(FORMULA_STARTS) else part for part in text.split(";"))


def to_csv(items: list[Item]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for i in items:
        writer.writerow([
            i.id,
            cell(i.title),
            "yes" if i.done else "no",
            i.due.isoformat() if i.due else "",
            cell(" ".join(i.tags)),
            i.priority or "",
            i.every or "",
        ])
    return buf.getvalue()
