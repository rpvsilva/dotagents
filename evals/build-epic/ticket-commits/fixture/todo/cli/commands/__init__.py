"""One module per command; each has register(parser) and run(args, ctx)."""
from todo.cli.commands import add, done, export, list_cmd, priority, remind, search, skip, stop, sync_cmd, tag

COMMANDS = {
    "add": add,
    "list": list_cmd,
    "done": done,
    "skip": skip,
    "priority": priority,
    "remind": remind,
    "search": search,
    "stop": stop,
    "export": export,
    "sync": sync_cmd,
    "tag": tag,
}
