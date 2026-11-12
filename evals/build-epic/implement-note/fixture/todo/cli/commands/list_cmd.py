"""todo list [--overdue] [--tag T]... [--priority P] [--open|--done] [--search TEXT] [--sort priority|due]"""
from todo.cli.format import format_list_item
from todo.cli.select import add_filter_arguments, select_items
from todo.cli.tree import tree
from todo.storage import is_overdue


def register(parser) -> None:
    parser.add_argument("--overdue", action="store_true")
    add_filter_arguments(parser)


def run(args, ctx) -> None:
    items = ctx.store.load()
    shown = [i for i in select_items(items, args) if not args.overdue or is_overdue(i, ctx.today)]
    for item, depth in tree(shown):
        ctx.out(format_list_item(item, items, depth))
