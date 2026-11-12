"""todo done ID"""
from todo.cli.errors import UsageError
from todo.storage import StoreError


def register(parser) -> None:
    parser.add_argument("id", type=int)


def run(args, ctx) -> None:
    try:
        nxt = ctx.store.complete(args.id, today=ctx.today)
    except StoreError:
        raise  # a bad todo file: main exits 1
    except ValueError as e:  # a stored rule whose next date is out of range
        raise UsageError(str(e)) from None
    ctx.out(f"done {args.id}")
    if nxt is not None:
        ctx.out(f"next {nxt.id}: {nxt.title} (due {nxt.due.isoformat()})")
