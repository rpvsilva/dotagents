"""Ticket 02 on top of ticket 01: both commands registered, and `report` ending with `stats`' summary."""
import os
import subprocess
import sys

from conftest import CHECKOUT

ITEMS = [{"title": "a", "done": True}, {"title": "b", "done": False}, {"title": "c", "done": False}]


def test_commands_hold_both_tickets_sorted():
    from tally.commands import COMMANDS
    assert COMMANDS == ["add", "list", "report", "stats"]


def test_report_keeps_its_lines_and_ends_with_the_stats_summary():
    from tally import report
    assert report.run(ITEMS) == ["report: 2 open", "- b", "- c", "1 of 3 done"]


def test_report_of_no_items_still_ends_with_the_summary():
    from tally import report, stats
    assert report.run([])[-1] == stats.summary([])


def test_stats_from_ticket_01_still_works():
    from tally import stats
    assert stats.run(ITEMS) == ["1 of 3 done"]


def test_the_cli_runs_report(tmp_path):
    env = dict(os.environ, TALLY_FILE=str(tmp_path / "none.json"), PYTHONPATH=str(CHECKOUT))
    p = subprocess.run([sys.executable, "-m", "tally", "report"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ["report: 0 open", "0 of 0 done"]
