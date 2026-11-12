"""Markdown export: a task list, open items first, then done items, each group in input order."""
from todo.storage import PRIORITY_MARKERS, Item


def escape(text: str) -> str:
    """Escape characters that would change Markdown rendering in a list item."""
    for ch in "\\`*_[]<>|":
        text = text.replace(ch, "\\" + ch)
    return text


def line(item: Item) -> str:
    text = f"- [{'x' if item.done else ' '}] {escape(item.title)}"
    if item.due:
        text += f" (due {item.due.isoformat()})"
    if item.every:
        text += f" ↻ every {item.every}"
    if item.priority in PRIORITY_MARKERS:
        text += f" {PRIORITY_MARKERS[item.priority]}"
    text += "".join(f" #{escape(tag)}" for tag in item.tags)
    return text


def to_markdown(items: list[Item]) -> str:
    open_items = [i for i in items if not i.done]
    done_items = [i for i in items if i.done]
    out = ["# Todo", ""]
    if not items:
        out.append("_No items._")
    out += [line(i) for i in open_items]
    if done_items:
        if open_items:
            out.append("")
        out += ["## Done", ""] + [line(i) for i in done_items]
    return "\n".join(out) + "\n"
