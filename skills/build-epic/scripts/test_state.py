"""Tests for `status.py --line` (one status line per ticket) and `status.py --write` (the run's `state.md`)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

STATUS = SCRIPTS / "status.py"
LONG = "A very long ticket title that keeps going " * 6


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ticket(nn, title, status, pr=None, blocked="none", extra=""):
    return (f"# {nn}: {title}\nStatus: {status}\nIssue: #{100 + int(nn)}\nBranch: feat/t{nn}\nBlocked by: {blocked}\n"
            + (f"PR: https://github.com/o/r/pull/{pr}\n" if pr else "") + extra)


def task(tid, status, blocked="none"):
    return f"# {tid}: Task {tid}\nStatus: {status}\nTicket: {tid.split('-')[0]}\nBlocked by: {blocked}\nWrites: x\n"


# A 10-ticket run: (ticket args, its tasks as (NN, status, blocked by)).
RUN = {
    "01": (("Alpha", "merged", 11), [("01", "done", "none"), ("02", "done", "01")]),
    "02": (("Beta", "merged", 12, "01"), [("01", "done", "none")]),
    "03": ((LONG, "in-review", 40, "02"), [("01", "done", "none"), ("02", "done", "01")]),
    "04": (("Delta", "building", 41, "02"), [("01", "done", "none"), ("02", "in-progress", "01"),
                                             ("03", "blocked", "none"), ("04", "ready", "02"),
                                             ("05", "ready", "01, ticket 01")]),
    "05": (("Epsilon", "building", 42), [("01", "done", "none"), ("02", "done", "none")]),
    "06": (("Zeta", "ready", None, "03"), [("01", "ready", "none")]),
    "07": (("Eta", "ready"), [("01", "ready", "none"), ("02", "ready", "01")]),
    "08": (("Theta", "claimed", None, "none", "Claimed by: someone\n"), [("01", "ready", "none")]),
    "09": (("Iota", "building"), [("01", "in-progress", "none"), ("02", "ready", "01")]),
    "10": (("Kappa", "ready", None, "09"), [("01", "ready", "none")]),
}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.run_dir = run = self.kit.run_dir
        for nn, (args, tasks) in RUN.items():
            write(run / "tickets" / f"{nn}-t.md", ticket(nn, *args))
            for n, status, blocked in tasks:
                write(run / "tasks" / f"{nn}-{n}-x.md", task(f"{nn}-{n}", status, blocked))
        write(run / "reports" / "04-03.json", json.dumps({"status": "blocked", "why": "the API is missing"}))
        # 09-01 ended plan-wrong: finish_task.py stored the report and left the task in-progress.
        write(run / "reports" / "09-01.json", json.dumps({"status": "plan-wrong", "why": "the area is wrong"}))

    def status(self, *args, run=None):
        p = self.kit.step(STATUS, run or self.run_dir, *args)
        self.assertEqual(p.stderr, "")
        return p

    def line(self, tt):
        p = self.status("--line", tt)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(len(p.stdout.splitlines()), 1, p.stdout)
        return p.stdout.rstrip("\n")

    def state(self):
        p = self.status("--write")
        self.assertEqual(p.returncode, 0, p.stdout)
        return p, (self.run_dir / "state.md").read_text(encoding="utf-8")

    # --line

    def test_line_is_one_line_of_at_most_160_characters_even_with_a_long_title(self):
        out = self.line("03")
        self.assertLessEqual(len(out), 160)
        self.assertTrue(out.startswith("03 in-review #40 A very long ticket title"), out)

    def test_line_of_a_ticket_in_review_waits_on_the_human(self):
        self.assertRegex(self.line("03"), r"next: human approval of #40$")

    def test_line_of_a_ticket_with_a_blocked_task_waits_on_the_human_for_that_task(self):
        out = self.line("04")
        self.assertTrue(out.startswith("04 building #41 Delta"), out)
        self.assertIn("next: human: task 04-03 blocked", out)

    def test_line_of_a_ticket_with_every_task_done_is_next_shipped(self):
        self.assertRegex(self.line("05"), r"^05 building #42 Epsilon +next: ship$")

    def test_line_of_a_ready_ticket_names_the_unmerged_tickets_it_waits_on(self):
        self.assertRegex(self.line("6"), r"^06 ready - Zeta +next: ticket 03 \(in-review\)$")
        self.assertRegex(self.line("07"), r"^07 ready - Eta +next: start$")

    def test_line_of_a_merged_and_a_claimed_ticket(self):
        self.assertRegex(self.line("01"), r"^01 merged #11 Alpha +next: none$")
        self.assertRegex(self.line("08"), r"next: someone \(claimed\)$")

    def test_line_of_a_building_ticket_names_its_running_tasks_and_dispatchable_ones(self):
        write(self.run_dir / "tickets" / "11-t.md", ticket("11", "Lambda", "building", 43))
        for n, status, blocked in (("01", "in-progress", "none"), ("02", "ready", "none"), ("03", "ready", "01")):
            write(self.run_dir / "tasks" / f"11-{n}-x.md", task(f"11-{n}", status, blocked))
        self.assertRegex(self.line("11"), r"^11 building #43 Lambda +next: tasks 11-01; dispatch 11-02$")
        write(self.run_dir / "tickets" / "11-t.md", ticket("11", "Lambda", "building", 43))
        write(self.run_dir / "tasks" / "11-02-x.md", task("11-02", "in-progress"))
        self.assertRegex(self.line("11"), r"next: tasks 11-01, 11-02$")
        write(self.run_dir / "tasks" / "11-01-x.md", task("11-01", "done"))
        write(self.run_dir / "tasks" / "11-02-x.md", task("11-02", "done"))
        write(self.run_dir / "tasks" / "11-03-x.md", task("11-03", "ready", "ticket 03"))
        self.assertRegex(self.line("11"), r"next: tasks$")

    def test_line_without_a_pr_or_a_claimant_and_of_a_blocked_ticket(self):
        write(self.run_dir / "tickets" / "11-t.md", ticket("11", "Lambda", "in-review"))
        self.assertRegex(self.line("11"), r"^11 in-review - Lambda +next: human approval$")
        write(self.run_dir / "tickets" / "11-t.md", ticket("11", "Lambda", "claimed"))
        self.assertRegex(self.line("11"), r"next: another developer \(claimed\)$")
        write(self.run_dir / "tickets" / "11-t.md", ticket("11", "Lambda", "blocked"))
        self.assertRegex(self.line("11"), r"next: human: ticket blocked$")

    def test_a_done_task_with_a_blocked_report_waits_on_no_human(self):
        write(self.run_dir / "reports" / "05-01.json", json.dumps({"status": "blocked", "why": "old"}))
        self.assertRegex(self.line("05"), r"next: ship$")
        _, text = self.state()
        self.assertNotIn("05-01", text)

    def test_bad_arguments_print_the_usage_and_exit_2(self):
        for args in (("--line",), ("--write", "x"), ("--bogus",)):
            with self.subTest(args=args):
                p = self.status(*args)
                self.assertEqual(p.returncode, 2)
                self.assertEqual(p.stdout, "FAIL run usage: status.py <run> [--report | --line <TT> | --write]\n")

    def test_line_of_an_unknown_ticket_is_one_fail_line(self):
        p = self.status("--line", "99")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout, "FAIL 99 no-ticket\n")

    # --write

    def test_write_prints_ok_with_the_path_and_writes_at_most_40_lines(self):
        p, text = self.state()
        self.assertEqual(p.stdout, f"OK run state {self.run_dir.resolve() / 'state.md'}\n")
        self.assertLessEqual(len(text.splitlines()), 40, text)
        self.assertEqual(sorted(f.name for f in self.run_dir.glob(".state.md.*")), [])

    def test_a_run_waiting_on_nothing_says_nothing_under_each_heading(self):
        for f in (*self.run_dir.glob("tickets/*.md"), *self.run_dir.glob("tasks/*.md")):
            f.unlink()
        write(self.run_dir / "tickets" / "01-t.md", ticket("01", "Alpha", "merged", 11))
        _, text = self.state()
        self.assertIn("In flight: none\n\n## Needs a human\n- nothing\n\n## Next\n- nothing\n", text)

    def test_state_lists_every_ticket_with_its_status_line(self):
        _, text = self.state()
        for tt in RUN:
            self.assertIn(self.line(tt), text.splitlines())

    def test_state_names_every_blocked_task_with_its_why(self):
        _, text = self.state()
        self.assertIn("- task 04-03 blocked: the API is missing", text.splitlines())
        self.assertIn("- task 09-01 plan-wrong: the area is wrong", text.splitlines())

    def test_a_blocked_task_with_no_report_says_so(self):
        write(self.run_dir / "tasks" / "07-02-x.md", task("07-02", "blocked"))
        _, text = self.state()
        self.assertIn("- task 07-02 blocked: no report", text.splitlines())

    def test_a_stale_report_on_a_re_planned_task_is_not_stuck(self):
        # 09-01's plan-wrong report predates the re-plan that set the task back to ready.
        report = self.run_dir / "reports" / "09-01.json"
        path = write(self.run_dir / "tasks" / "09-01-x.md", task("09-01", "ready"))
        old = report.stat().st_mtime - 60
        os.utime(report, (old, old))
        self.assertNotIn("human", self.line("09"))
        _, text = self.state()
        self.assertNotIn("09-01", text.split("## Needs a human")[1].split("##")[0])
        # Dispatched again and reporting plan-wrong again: stuck once more.
        write(path, task("09-01", "in-progress"))
        os.utime(path, (old - 60, old - 60))
        self.assertIn("next: human: task 09-01 plan-wrong", self.line("09"))

    def test_state_stays_within_40_lines_with_25_stuck_tasks(self):
        for n in range(10, 35):
            write(self.run_dir / "tasks" / f"07-{n}-x.md", task(f"07-{n}", "blocked"))
        _, text = self.state()
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 40, text)
        human = text.split("## Needs a human\n")[1].split("\n\n")[0].splitlines()
        self.assertRegex(human[-1], r"^- \+\d+ more$")
        shown = len(human) - 1
        self.assertEqual(int(human[-1][3:].split()[0]), 25 + 2 + 1 - shown, human)
        self.assertIn("- ship: 05", lines)

    def test_state_names_every_ticket_waiting_on_a_human(self):
        write(self.run_dir / "tickets" / "10-t.md", ticket("10", "Kappa", "blocked", blocked="09"))
        _, text = self.state()
        self.assertIn("- ticket 03 in-review #40: human approval", text.splitlines())
        self.assertIn("- ticket 10 blocked", text.splitlines())

    def test_state_lists_tasks_in_flight_and_next_actions(self):
        _, text = self.state()
        lines = text.splitlines()
        self.assertIn("In flight: 04-02, 09-01", lines)
        self.assertIn("- start: 07", lines)
        self.assertIn("- dispatch: 04-05", lines)
        self.assertIn("- ship: 05", lines)
        self.assertIn("- wait: 06 on 03, 10 on 09", lines)

    def test_write_reads_the_run_folder_only_and_works_through_a_linked_run_path(self):
        link = self.kit.link(self.run_dir, "run-link")
        p = self.status("--write", run=link)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertTrue((self.run_dir / "state.md").exists())
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_missing_run_folder_is_one_fail_line(self):
        p = self.kit.step(STATUS, self.kit.tmp / "nope", "--write")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r"^FAIL run no-run-folder: .*nope\n$")


if __name__ == "__main__":
    unittest.main()
