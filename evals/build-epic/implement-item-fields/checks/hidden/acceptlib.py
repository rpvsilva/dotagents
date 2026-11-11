"""Helpers for the hidden acceptance tests: run the work repo's `todo` CLI as a user would, on a temp todo file.

Not a check itself (`checks/hidden/` holds no executable): `checks/hidden-tests.sh` runs the `accept_*.py` files
beside it with `python3 -m unittest`, in `$EVAL_WORK`, so `import todo` reads the implementer's code.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORK = Path(os.environ["EVAL_WORK"]).resolve()


class CliTest(unittest.TestCase):
    """Each test gets its own temp folder and todo file; `TODO_*` from the caller's shell never leaks in."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.file = self.tmp / "todo.json"

    def todo(self, *args, repo=None, timeout=60):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TODO_", "PYTHON"))}
        env.update(TODO_FILE=str(self.file), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-m", "todo", *map(str, args)], cwd=repo or WORK, env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)

    def ok(self, *args, repo=None):
        """The command's stdout lines; fails the test unless it exits 0."""
        got = self.todo(*args, repo=repo)
        self.assertEqual(got.returncode, 0, f"todo {' '.join(map(str, args))}: {got.stderr}")
        return got.stdout.splitlines()

    def refused(self, *args, code=2):
        """Runs a command that must exit `code` and leave the todo file's bytes as they were; returns stderr."""
        before = self.file.read_bytes() if self.file.exists() else None
        got = self.todo(*args)
        self.assertEqual(got.returncode, code, f"todo {' '.join(map(str, args))}: {got.stdout}{got.stderr}")
        after = self.file.read_bytes() if self.file.exists() else None
        self.assertEqual(after, before, f"todo {' '.join(map(str, args))} changed the todo file")
        return got.stderr

    def rows(self):
        return json.loads(self.file.read_text())

    def write_rows(self, rows):
        self.file.write_text(json.dumps(rows, indent=2))

    def row(self, item_id):
        return next(r for r in self.rows() if r["id"] == item_id)

    def copy_of_work(self):
        """A copy of the work repo's code in the temp folder, for a test that must add a file to the package."""
        dest = self.tmp / "repo"
        shutil.copytree(WORK, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv"))
        return dest
