"""todo sync: pull from $TODO_REMOTE, then push to it."""
from todo.sync import remote_store, sync
from todo.sync.remote import RemoteNotConfigured


def register(parser) -> None:
    pass


def run(args, ctx) -> None:
    try:
        remote = remote_store()
    except RemoteNotConfigured as e:
        raise SystemExit(f"todo: error: {e}") from None
    result = sync(ctx.store, remote)
    ctx.out(f"pulled {len(result.pulled)}, pushed {len(result.pushed)}")
