"""Tests for `status.py --line` (one status line per ticket) and `status.py --write` (the run's `state.md`)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testkit import SCRIPTS, Kit  # noqa: E402

STATUS = SCRIPTS / "status.py"
LONG = "A very long ticket title that keeps going " * 6


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
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
        write(run / "reports" / "04-03.md", "status: blocked\nchanged: none\nwhy: the API is missing\nfollow-up:\n")
        # 09-01 ended plan-wrong: finish_task.py stored the report and left the task in-progress.
        write(run / "reports" / "09-01.md", "status: plan-wrong\nchanged: abc\nwhy: the area is wrong\nfollow-up:\n")

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
        return p, (self.run_dir / "state.md").read_text()

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

    def test_line_of_an_unknown_ticket_is_one_fail_line(self):
        p = self.status("--line", "99")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stdout, "FAIL 99 no-ticket\n")

    # --write

    def test_write_prints_ok_with_the_path_and_writes_at_most_40_lines(self):
        p, text = self.state()
        self.assertEqual(p.stdout, f"OK run state {self.run_dir.resolve() / 'state.md'}\n")
        self.assertLessEqual(len(text.splitlines()), 40, text)

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
