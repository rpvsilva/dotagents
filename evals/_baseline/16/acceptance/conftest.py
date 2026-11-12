"""Runs the sandbox `todo` CLI of the checkout under test from the outside, as the checkout's own tests do.

ACCEPT_CHECKOUT names the checkout (default: the current folder); its root goes first on `sys.path`, so `todo` is
imported from it. Each test gets its own `HOME` under `tmp_path`, and each machine (`todo`, or the two of `machines`)
its own todo file (`TODO_FILE`, set around each command); `machines` share one `TODO_REMOTE`. Each command passes
`today=` to `todo.cli.main(argv, out=, today=)`, the entry point every version of the CLI has, so no date depends on
the clock. ACCEPT_RESULTS, when set, names a JSON file written at the end: one row per test (`file`, `name`,
`outcome` passed|failed|error|skipped, `tier` core|strict), which `evals/_baseline/402/quality.py` reads.
"""
import contextlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

CHECKOUT = Path(os.environ.get("ACCEPT_CHECKOUT") or os.getcwd()).resolve()
TODAY = date(2026, 11, 10)
_ROWS = {}
LINE = re.compile(r"^\s*\[([ x])\] (\d+): ")


def pytest_configure(config):
    sys.path.insert(0, str(CHECKOUT))


@dataclass
class Result:
    code: int
    lines: list = field(default_factory=list)
    err: str = ""

    @property
    def text(self):
        return "\n".join(self.lines)


class Todo:
    """`todo(*argv, today=)` runs one command on this machine's todo file and returns its Result."""

    def __init__(self, path):
        from todo.cli import main  # imported here, so a broken checkout fails each test, not the collection
        self._main = main
        self.path = path

    def __call__(self, *argv, today=TODAY):
        out, err, stdout = [], io.StringIO(), io.StringIO()
        code = 0
        before = os.environ.get("TODO_FILE")
        os.environ["TODO_FILE"] = str(self.path)
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(stdout):
                try:
                    rc = self._main([str(a) for a in argv], out=out.append, today=today)
                    code = rc if isinstance(rc, int) else 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        finally:
            os.environ["TODO_FILE"] = before or ""
        return Result(code, out + stdout.getvalue().splitlines(), err.getvalue())

    def ok(self, *argv, today=TODAY):
        r = self(*argv, today=today)
        assert r.code == 0, (argv, r.err)
        return r.lines

    def add(self, title, *flags, today=TODAY):
        """Adds an item and returns its id, read from the `added ID: TITLE` line."""
        lines = self.ok("add", title, *flags, today=today)
        m = re.search(r"added (\d+):", "\n".join(lines))
        assert m, lines
        return int(m.group(1))

    def list(self, *flags, today=TODAY):
        return self.ok("list", *flags, today=today)

    def items(self, today=TODAY):
        """`todo list --open` and `--done` together, as (id, done, line) rows."""
        rows = []
        for flag in ("--open", "--done"):
            for line in self.list(flag, today=today):
                m = LINE.match(line)
                if m:
                    rows.append((int(m.group(2)), m.group(1) == "x", line))
        return sorted(rows)

    def open_lines(self, today=TODAY):
        return [line for _, done, line in self.items(today) if not done]

    def done_lines(self, today=TODAY):
        return [line for _, done, line in self.items(today) if done]


def line_of(lines, item_id):
    """The line of item `item_id` (a `todo list` or `todo remind` line), or None."""
    pattern = re.compile(rf"^\s*\[[ x]\] {item_id}:")
    return next((line for line in lines if pattern.match(line)), None)


def due_of(line):
    """The `(due YYYY-MM-DD)` date of a line, or None."""
    m = re.search(r"\(due (\d{4}-\d{2}-\d{2})\)", line or "")
    return m.group(1) if m else None


@pytest.fixture
def todo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todo.json"))
    return Todo(tmp_path / "todo.json")


@pytest.fixture
def machines(tmp_path, monkeypatch):
    """Two machines, each with its own todo file, syncing the same remote file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "a.json"))
    monkeypatch.setenv("TODO_REMOTE", str(tmp_path / "remote.json"))
    return Todo(tmp_path / "a.json"), Todo(tmp_path / "b.json")


@pytest.fixture
def readme():
    path = CHECKOUT / "README.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def pytest_runtest_logreport(report):
    """One row per test: the call's outcome, or `error` when its setup or teardown failed."""
    if report.when == "call":
        outcome = report.outcome
    elif report.failed:
        outcome = "error"
    elif report.when == "setup" and report.skipped:
        outcome = "skipped"
    else:
        return
    file, _, name = report.nodeid.partition("::")
    row = _ROWS.setdefault(report.nodeid, {"file": Path(file).name, "name": name, "outcome": outcome,
                                           "tier": "strict" if "strict" in report.keywords else "core"})
    if row["outcome"] == "passed":
        row["outcome"] = outcome


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get("ACCEPT_RESULTS")
    if target:
        Path(target).write_text(json.dumps(list(_ROWS.values()), indent=1) + "\n", encoding="utf-8")
