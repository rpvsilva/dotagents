"""Tests for `finish_task.py` copying a remote run's `result.json` into `<run>/remote/<task>.json`."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_finish_open import FINISH, StepCase  # noqa: E402

NAME = "01-02-json-export"


class FinishRemoteTest(StepCase):
    def common_dir(self):
        return Path(self.kit.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=self.clone))

    def worktree_git_dir(self):
        return Path(self.kit.git("rev-parse", "--absolute-git-dir", cwd=self.wt))

    def record(self, git_dir, run, name, started, result):
        """A remote-agent record `<git_dir>/remote-agent/<run>/` with its `run.json` and `result.json`."""
        folder = git_dir / "remote-agent" / run
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"run": run, "name": name, "started": started, "state": "done"}))
        (folder / "result.json").write_bytes(result)
        return result

    def copied(self):
        return self.kit.run_dir / "remote" / "01-02.json"

    def finish(self):
        p = self.kit.step(FINISH, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return self.one_line(p)

    def log(self):
        return (self.kit.run_dir / "logs" / "01-02-finish_task.log").read_text()

    def test_copies_the_result_from_the_common_git_dir_byte_for_byte(self):
        want = self.record(self.common_dir(), "agent-20261106-100000-1", NAME, 1000,
                           b'{"run": "agent-20261106-100000-1",\r\n "total_cost_usd": 1.25}')
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.finish(), "OK 01-02 committed files=1")
        self.assertEqual(self.copied().read_bytes(), want)

    def test_copies_a_record_from_the_worktree_git_dir_before_removing_the_worktree(self):
        want = self.record(self.worktree_git_dir(), "agent-20261106-100000-2", NAME, 1000, b'{"old": true}\n')
        self.assertEqual(self.finish(), "OK 01-02 no-change")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.copied().read_bytes(), want)

    def test_the_newest_run_of_a_retried_task_wins(self):
        self.record(self.common_dir(), "agent-20261106-120000-9", NAME, 1000, b'{"try": 1}\n')
        want = self.record(self.common_dir(), "agent-20261106-110000-1", NAME, 2000, b'{"try": 2}\n')
        self.record(self.worktree_git_dir(), "agent-20261106-090000-1", NAME, 500, b'{"try": 0}\n')
        self.record(self.common_dir(), "agent-20261106-130000-1", "01-03-other", 3000, b'{"other": 1}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.assertEqual(self.copied().read_bytes(), want)

    def test_unreadable_and_resultless_records_are_passed_over(self):
        common = self.common_dir()
        want = self.record(common, "agent-20261106-100000-1", "01-02", 1000, b'{"by": "id"}\n')
        self.record(common, "agent-20261106-120000-1", NAME, 9000, b'{"no": "result"}\n')
        (common / "remote-agent" / "agent-20261106-120000-1" / "result.json").unlink()
        for run, text in (("agent-20261106-130000-1", "{not json"), ("agent-20261106-140000-1", '["01-02"]')):
            (common / "remote-agent" / run).mkdir()
            (common / "remote-agent" / run / "run.json").write_text(text)
            (common / "remote-agent" / run / "result.json").write_text('{"wrong": true}\n')
        (common / "remote-agent" / "agent-20261106-150000-1").mkdir()
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.finish(), "OK 01-02 committed files=1")
        self.assertEqual(self.copied().read_bytes(), want)

    def test_a_run_named_with_the_task_id_and_a_suffix_matches_and_an_undated_run_counts_as_oldest(self):
        common = self.common_dir()
        self.record(common, "agent-20261106-100000-1", "01-02", "late", b'{"started": "not a number"}\n')
        want = self.record(common, "agent-20261106-090000-1", "01-02-retry", 2000, b'{"try": 2}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.assertEqual(self.copied().read_bytes(), want)

    def test_without_a_record_finish_succeeds_and_logs_no_remote_result(self):
        self.record(self.common_dir(), "agent-20261106-130000-1", "01-03-other", 3000, b'{"other": 1}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.finish(), "OK 01-02 committed files=1")
        self.assertFalse(self.copied().exists())
        self.assertIn("no remote result found for 01-02", self.log())


if __name__ == "__main__":
    unittest.main()
