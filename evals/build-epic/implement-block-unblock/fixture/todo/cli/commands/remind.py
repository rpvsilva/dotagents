"""todo remind: overdue items, then items due in the next 3 days."""
from todo.cli.format import format_remind_item
from todo.storage import is_due_soon, is_overdue


def register(parser) -> None:
    pass


def run(args, ctx) -> None:
    items = ctx.store.load()
    overdue = [i for i in items if is_overdue(i, ctx.today)]
    soon = [i for i in items if is_due_soon(i, ctx.today)]
    if not overdue and not soon:
        ctx.out("nothing due")
        return
    ctx.out("overdue:")
    for i in overdue:
        ctx.out(format_remind_item(i))
    ctx.out("due soon:")
    for i in soon:
        ctx.out(format_remind_item(i))
