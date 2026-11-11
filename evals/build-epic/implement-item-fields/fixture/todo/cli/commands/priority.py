"""todo priority ID high|medium|low|none"""
from todo.storage import PRIORITIES

NONE = "none"


def register(parser) -> None:
    parser.add_argument("id", type=int)
    parser.add_argument("level", choices=[*PRIORITIES, NONE])


def run(args, ctx) -> None:
    ctx.store.set_priority(args.id, None if args.level == NONE else args.level)
    ctx.out(f"priority {args.id}: {args.level}")
