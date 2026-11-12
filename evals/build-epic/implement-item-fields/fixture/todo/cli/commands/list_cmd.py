"""todo list [--overdue] [--tag T]... [--priority P] [--open|--done] [--search TEXT] [--sort priority|due]"""
from todo.cli.format import format_list_item
from todo.cli.select import add_filter_arguments, select_items
from todo.storage import is_overdue


def register(parser) -> None:
    parser.add_argument("--overdue", action="store_true")
    add_filter_arguments(parser)


def run(args, ctx) -> None:
    for item in select_items(ctx.store.load(), args):
        if not args.overdue or is_overdue(item, ctx.today):
            ctx.out(format_list_item(item))
