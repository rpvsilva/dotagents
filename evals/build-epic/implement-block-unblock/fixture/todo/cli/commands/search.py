"""todo search TEXT: the same as `todo list --search TEXT`."""
import argparse

from todo.cli.commands import list_cmd


def register(parser) -> None:
    parser.add_argument("search", metavar="text")
    # Every other `todo list` option keeps its default, so run matches `list --search`.
    scratch = argparse.ArgumentParser()
    list_cmd.register(scratch)
    defaults = vars(scratch.parse_args([]))
    del defaults["search"]
    parser.set_defaults(**defaults)


def run(args, ctx) -> None:
    list_cmd.run(args, ctx)
