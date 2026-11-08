"""Tests for hub-task waits: `merge_task.py`'s `Done:` header, `start_task.py`'s `<run>/waits.jsonl` row and
`status.py --report`'s `## Hub waits` section."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_merge_task  # noqa: E402
import test_start  # noqa: E402
from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS  # noqa: E402

STATUS = SCRIPTS / "status.py"
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def utc(text):
    return datetime.strptime(text, STAMP).replace(tzinfo=timezone.utc)


def stamp(when):
    return when.strftime(STAMP)


class DoneHeaderTest(unittest.TestCase):
    """`merge_task.py` against the same origin and stub `gh` as `test_merge_task.py`."""
    setUp = test_merge_task.MergeTaskTest.setUp
    checks = test_merge_task.MergeTaskTest.checks
    commit = test_merge_task.MergeTaskTest.commit
    merge = test_merge_task.MergeTaskTest.merge

    def test_a_merged_task_gets_a_utc_done_header(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        line = self.merge()
        after = datetime.now(timezone.utc)
        self.assertTrue(line.startswith("OK 01-01 done "), line)
        done = runfolder.get(self.task, "Done")
        self.assertRegex(done or "", r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(before <= utc(done) <= after, done)

    def test_a_failed_merge_writes_no_done_header(self):
        self.checks("false")
        self.assertTrue(self.merge().startswith("FAIL 01-01 red false"))
        self.assertIsNone(runfolder.get(self.task, "Done"))


class HubWaitTest(test_start.StartCase):
    def setUp(self):
        super().setUp()
        self.write_ticket("building", "none")
        self.kit.git("push", "-q", "origin", f"origin/main:refs/heads/{test_start.BRANCH}", cwd=self.kit.clone)
        self.kit.git("fetch", "-q", "origin", cwd=self.kit.clone)
        self.waits = self.kit.run_dir / "waits.jsonl"

    def task(self, name, status, blocked, done=None):
        path = self.kit.run_dir / "tasks" / name
        tid = "-".join(name.split("-")[:2])
        path.write_text(f"# {tid}: {name}\nStatus: {status}\nTicket: 01\nBlocked by: {blocked}\n"
                        + (f"Done: {done}\n" if done else ""), encoding="utf-8")
        return path

    def start(self, ident):
        p = self.kit.step(test_start.START_TASK, self.kit.run_dir, ident)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def rows(self):
        return [json.loads(line) for line in self.waits.read_text(encoding="utf-8").splitlines()]

    def report(self):
        p = self.kit.step(STATUS, self.kit.run_dir, "--report")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_a_hub_task_started_30_minutes_after_its_last_blocker_reports_30_minutes_on_its_tickets(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.task("01-01-a.md", "done", "none", done=stamp(now - timedelta(minutes=90)))
        self.task("01-02-b.md", "done", "none", done=stamp(now - timedelta(minutes=30)))
        self.task("01-03-hub.md", "ready", "01, 02, ticket 02, ticket 03")
        self.start("01-03")
        [row] = self.rows()
        self.assertEqual(row["task"], "01-03")
        self.assertEqual(row["waited_on"], ["02", "03"])
        self.assertEqual(row["ready_at"], stamp(now - timedelta(minutes=30)))
        self.assertLess(abs(utc(row["started_at"]) - now), timedelta(minutes=1))
        self.assertEqual(row["minutes"], 30)
        report = self.report()
        self.assertIn("\n## Hub waits\n\n- 01-03: 30 min on tickets 02, 03\n", report)

    def test_a_hub_task_whose_blockers_have_no_done_header_records_no_ready_at_and_reports_unknown(self):
        self.task("01-01-a.md", "done", "none")
        self.task("01-02-hub.md", "ready", "01, ticket 02")
        self.start("01-02")
        [row] = self.rows()
        self.assertEqual((row["task"], row["waited_on"]), ("01-02", ["02"]))
        self.assertIsNone(row["ready_at"])
        self.assertIsNone(row["minutes"])
        self.assertIn("\n## Hub waits\n\n- 01-02: unknown on ticket 02\n", self.report())

    def test_a_task_that_is_not_a_hub_task_records_no_wait_and_the_report_has_no_section(self):
        self.task("01-01-a.md", "done", "none", done=stamp(datetime.now(timezone.utc)))
        self.task("01-02-b.md", "ready", "01")
        self.start("01-02")
        self.assertFalse(self.waits.exists())
        self.assertNotIn("Hub waits", self.report())


if __name__ == "__main__":
    unittest.main()
