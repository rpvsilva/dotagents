"""Where a core command (`settings`, `capped`) or `remote-agent` lives: `$HOME/.agents/bin` first, then PATH."""
import os
import shutil


def path(name):
    """`$HOME/.agents/bin/<name>` when it exists, else `name` on PATH, else the home path (so errors name it)."""
    home = os.path.join(os.environ.get("HOME") or os.path.expanduser("~"), ".agents", "bin", name)
    return home if os.path.exists(home) else shutil.which(name) or home
