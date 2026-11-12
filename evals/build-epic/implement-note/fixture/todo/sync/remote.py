"""Where the remote lives: for now, another JSON file named by TODO_REMOTE."""
import os
from pathlib import Path

from todo.storage import JsonStore, Store


class RemoteNotConfigured(Exception):
    """TODO_REMOTE is not set."""


def remote_store(env=os.environ) -> Store:
    path = env.get("TODO_REMOTE")
    if not path:
        raise RemoteNotConfigured("TODO_REMOTE is not set; point it at the remote todo file")
    return JsonStore(Path(path), follow_symlinks=False)
