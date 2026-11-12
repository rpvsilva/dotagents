"""todo stop ID"""
from todo.cli.errors import UsageError
from todo.storage import NotRecurring


def register(parser) -> None:
    parser.add_argument("id", type=int)


def run(args, ctx) -> None:
    try:
        ctx.store.stop(args.id)
    except NotRecurring as e:
        raise UsageError(str(e)) from e
    ctx.out(f"stopped {args.id}")
