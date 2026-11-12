"""Errors a command's `run` raises; `main` turns them into exit codes."""


class UsageError(Exception):
    """Bad input that argparse could not catch; `main` exits 2."""
