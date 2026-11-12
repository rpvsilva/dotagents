"""todo skip ID: move a recurring item to its next occurrence without marking it done."""
from todo.cli.errors import UsageError
from todo.storage import StoreError


def register(parser) -> None:
    parser.add_argument("id", type=int)


def run(args, ctx) -> None:
    try:
        item = ctx.store.skip(args.id, today=ctx.today)
    except StoreError:
        raise  # a bad todo file: main exits 1
    except ValueError as e:  # a one-off or done item, or an out-of-range rule
        raise UsageError(str(e)) from None
    ctx.out(f"skipped {item.id}: {item.title}, next due {item.due.isoformat()}")
