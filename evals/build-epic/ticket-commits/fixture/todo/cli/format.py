"""How items look on the terminal."""
from todo.storage import PRIORITY_MARKERS, Item


def format_item(item: Item) -> str:
    line = f"[{'x' if item.done else ' '}] {item.id}: {item.title}"
    if item.due:
        line += f" (due {item.due.isoformat()})"
    return line


def format_remind_item(item: Item) -> str:
    """format_item plus the recurrence rule, as `todo remind` shows them (no priority or tags)."""
    line = format_item(item)
    if item.every:
        line += f" ↻ every {item.every}"
    return line


def format_list_item(item: Item) -> str:
    """format_remind_item plus the priority marker and #tags, as `todo list` shows them."""
    parts = [format_remind_item(item)]
    if item.priority in PRIORITY_MARKERS:
        parts.append(PRIORITY_MARKERS[item.priority])
    parts.extend(f"#{tag}" for tag in item.tags)
    return " ".join(parts)
