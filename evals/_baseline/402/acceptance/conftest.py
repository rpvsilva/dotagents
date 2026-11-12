"""Runs the sandbox `todo` CLI of the checkout under test from the outside, as the checkout's own tests do.

ACCEPT_CHECKOUT names the checkout (default: the current folder); its root goes first on `sys.path`, so `todo` is
imported from it. Each test gets its own todo file (`TODO_FILE`) and `HOME` under `tmp_path`, and passes `today=` to
`todo.cli.main(argv, out=, today=)`, the entry point every version of the CLI has, so no date depends on the clock.
ACCEPT_RESULTS, when set, names a JSON file written at the end: one row per test (`file`, `name`, `outcome`
passed|failed|error|skipped, `tier` core|strict), which `../quality.py` reads.
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
TODAY = date(2026, 6, 10)
_ROWS = {}


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
    """`todo(*argv, today=)` runs one command and returns its Result; `add` and `list` are shortcuts."""

    def __init__(self):
        from todo.cli import main  # imported here, so a broken checkout fails each test, not the collection
        self._main = main

    def __call__(self, *argv, today=TODAY):
        out, err, stdout = [], io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(stdout):
            try:
                rc = self._main([str(a) for a in argv], out=out.append, today=today)
                code = rc if isinstance(rc, int) else 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        return Result(code, out + stdout.getvalue().splitlines(), err.getvalue())

    def add(self, title, *flags):
        """Adds an item and returns its id, read from the `added ID: TITLE` line."""
        r = self("add", title, *flags)
        assert r.code == 0, r.err
        m = re.search(r"added (\d+):", r.text)
        assert m, r.lines
        return int(m.group(1))

    def list(self, *flags, today=TODAY):
        r = self("list", *flags, today=today)
        assert r.code == 0, r.err
        return r.lines


def line_of(lines, item_id):
    """The `todo list` line of item `item_id`, indent kept, or None."""
    pattern = re.compile(rf"^\s*\[[ x]\] {item_id}:")
    return next((line for line in lines if pattern.match(line)), None)


def ids(lines):
    """The item ids of `todo list` lines, in order."""
    return [int(m.group(1)) for m in (re.match(r"^\s*\[[ x]\] (\d+):", line) for line in lines) if m]


def indent(line):
    return len(line) - len(line.lstrip(" "))


@pytest.fixture
def todo(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todo.json"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return Todo()


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
