"""Export: render items as CSV or Markdown text."""
from todo.export.csv_export import to_csv
from todo.export.markdown import to_markdown

FORMATS = {"csv": to_csv, "md": to_markdown}


def export(items, fmt: str) -> str:
    """Render `items` in format `fmt` (a key of FORMATS)."""
    try:
        render = FORMATS[fmt]
    except KeyError:
        raise ValueError(f"unknown export format {fmt!r}") from None
    return render(items)


__all__ = ["FORMATS", "export", "to_csv", "to_markdown"]
