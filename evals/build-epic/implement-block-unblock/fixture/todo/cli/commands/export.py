"""todo export --format csv|md [--out FILE] [--tag T]... [--priority P] [--open|--done] [--search TEXT] [--sort priority|due]"""
import os
import tempfile
from pathlib import Path

from todo.cli.select import add_filter_arguments, select_items
from todo.export import FORMATS, export


def register(parser) -> None:
    parser.add_argument("--format", choices=sorted(FORMATS), required=True)
    parser.add_argument("--out", type=Path, help="write to FILE instead of stdout")
    add_filter_arguments(parser)


def write_file(path: Path, text: str) -> None:
    """Write a temp file beside `path`, then swap it in, so a symlink planted at `path` is replaced, never followed."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def run(args, ctx) -> None:
    items = select_items(ctx.store.load(), args)
    text = export(items, args.format)
    if args.out:
        write_file(args.out, text)
        ctx.out(f"exported {len(items)} items to {args.out}")
    else:
        ctx.out(text.rstrip("\n"))
