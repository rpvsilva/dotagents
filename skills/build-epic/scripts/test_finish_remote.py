"""Tests for `finish_task.py` copying each of the task's remote runs' `result.json` into `<run>/remote/<task>-<run>.json`,
and committing a remote resolver's patch, applied over the merge it left in progress, as the merge commit."""
import json
import subprocess
import sys
import tempfile
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

    def record(self, git_dir, run, name, started, result, worktree=None):
        """A remote-agent record `<git_dir>/remote-agent/<run>/` with its `run.json` and `result.json`, run in
        `worktree` (default: the task's)."""
        folder = git_dir / "remote-agent" / run
        folder.mkdir(parents=True)
        info = {"run": run, "name": name, "worktree": str(worktree or self.wt), "started": started, "state": "done"}
        (folder / "run.json").write_text(json.dumps(info), encoding="utf-8")
        (folder / "result.json").write_bytes(result)
        return result

    def copied(self, run):
        return self.kit.run_dir / "remote" / f"01-02-{run}.json"

    def copies(self):
        folder = self.kit.run_dir / "remote"
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

    def finish(self):
        p = self.kit.step(FINISH, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return self.one_line(p)

    def log(self):
        return (self.kit.run_dir / "logs" / "01-02-finish_task.log").read_text(encoding="utf-8")

    def test_copies_the_result_from_the_common_git_dir_byte_for_byte(self):
        want = self.record(self.common_dir(), "agent-20261106-100000-1", NAME, 1000,
                           b'{"run": "agent-20261106-100000-1",\r\n "total_cost_usd": 1.25}')
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.finish(), "OK 01-02 committed files=1")
        self.assertEqual(self.copied("agent-20261106-100000-1").read_bytes(), want)

    def test_copies_a_record_from_the_worktree_git_dir_before_removing_the_worktree(self):
        want = self.record(self.worktree_git_dir(), "agent-20261106-100000-2", NAME, 1000, b'{"old": true}\n')
        self.assertEqual(self.finish(), "OK 01-02 no-change")
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.copied("agent-20261106-100000-2").read_bytes(), want)

    def test_every_run_of_a_retried_task_is_copied(self):
        first = self.record(self.common_dir(), "agent-20261106-120000-9", NAME, 1000, b'{"try": 1}\n')
        second = self.record(self.common_dir(), "agent-20261106-110000-1", NAME, 2000, b'{"try": 2}\n')
        old = self.record(self.worktree_git_dir(), "agent-20261106-090000-1", NAME, 500, b'{"try": 0}\n')
        self.record(self.common_dir(), "agent-20261106-130000-1", "01-03-other", 3000, b'{"other": 1}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.assertEqual(self.copies(), ["01-02-agent-20261106-090000-1.json", "01-02-agent-20261106-110000-1.json",
                                         "01-02-agent-20261106-120000-9.json"])
        self.assertEqual(self.copied("agent-20261106-120000-9").read_bytes(), first)
        self.assertEqual(self.copied("agent-20261106-110000-1").read_bytes(), second)
        self.assertEqual(self.copied("agent-20261106-090000-1").read_bytes(), old)

    def test_another_runs_task_with_the_same_id_and_name_is_not_copied(self):
        other_wt = self.kit.tmp / "other-run" / "worktrees" / "01-02"
        self.record(self.common_dir(), "agent-20261105-100000-1", NAME, 900, b'{"epic": "a"}\n', worktree=other_wt)
        self.record(self.common_dir(), "agent-20261105-100000-2", "01-02", 900, b'{"epic": "a"}\n', worktree=other_wt)
        want = self.record(self.common_dir(), "agent-20261106-100000-1", NAME, 1000, b'{"epic": "b"}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.assertEqual(self.copies(), ["01-02-agent-20261106-100000-1.json"])
        self.assertEqual(self.copied("agent-20261106-100000-1").read_bytes(), want)

    def test_a_run_named_only_by_the_task_id_is_not_copied(self):
        self.record(self.common_dir(), "agent-20261106-100000-1", "01-02", 1000, b'{"id": 1}\n')
        self.record(self.common_dir(), "agent-20261106-100000-2", "01-02-json-export-v2", 1000, b'{"id": 2}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.assertEqual(self.copies(), [])

    def test_a_rerun_copies_the_same_files_again(self):
        self.record(self.common_dir(), "agent-20261106-100000-1", NAME, 1000, b'{"try": 1}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.finish()
        self.finish()
        self.assertEqual(self.copies(), ["01-02-agent-20261106-100000-1.json"])

    def test_without_a_record_finish_succeeds_and_logs_no_remote_result(self):
        self.record(self.common_dir(), "agent-20261106-130000-1", "01-03-other", 3000, b'{"other": 1}\n')
        self.write(self.wt, "a.txt", "a\n")
        self.assertEqual(self.finish(), "OK 01-02 committed files=1")
        self.assertEqual(self.copies(), [])
        self.assertIn("no remote result found for 01-02", self.log())


class RemoteResolverTest(StepCase):
    """`FAIL TT-NN conflict` leaves the merge in the worktree; a remote resolver edits a snapshot of its files and its
    patch is applied with `git apply` (working tree only, as `scripts/remote-agent.sh` does)."""

    def conflict(self):
        self.advance_ticket("a.txt", "theirs\n")
        self.write(self.wt, "a.txt", "ours\n")
        self.write(self.wt, "b.txt", "b\n")
        self.assertIn("FAIL 01-02 conflict a.txt", self.kit.step(FINISH, self.kit.run_dir, "01-02").stdout)
        self.kit.git("rev-parse", "--verify", "MERGE_HEAD", cwd=self.wt)

    def remote_patch(self, resolved):
        """The patch a sandbox returns: its snapshot is the worktree's files as plain commits (no MERGE_HEAD), and the
        patch is `git diff --cached --binary <snapshot>` after the resolver writes `resolved` into each named file."""
        with tempfile.TemporaryDirectory() as tmp:
            box = Path(tmp)
            self.kit.git("init", "-q", cwd=box)
            for name in self.kit.git("ls-files", "-co", "--exclude-standard", cwd=self.wt).splitlines():
                self.write(box, name, (self.wt / name).read_text(encoding="utf-8"))
            self.kit.git("add", "-A", cwd=box)
            self.kit.git("commit", "-qm", "snapshot", cwd=box)
            for name, text in resolved.items():
                self.write(box, name, text)
            self.kit.git("add", "-A", cwd=box)
            patch = subprocess.run(["git", "-C", str(box), "diff", "--cached", "--binary", "HEAD"], env=self.kit.env,
                                   capture_output=True, check=True).stdout
        path = self.kit.tmp / "changes.patch"
        path.write_bytes(patch)
        return path

    def apply(self, patch):
        self.kit.git("apply", "--whitespace=nowarn", str(patch), cwd=self.wt)

    def test_a_marker_free_patch_applied_mid_merge_is_committed_as_the_merge_commit(self):
        self.conflict()
        task_tip = self.kit.git("rev-parse", "HEAD", cwd=self.wt)
        ticket_tip = self.kit.git("rev-parse", "origin/feat/alpha", cwd=self.wt)
        self.apply(self.remote_patch({"a.txt": "both\n"}))
        self.kit.git("rev-parse", "--verify", "MERGE_HEAD", cwd=self.wt)
        p = self.kit.step(FINISH, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.one_line(p), "OK 01-02 committed files=2")
        self.assertEqual(self.kit.git("rev-parse", "HEAD^1", "HEAD^2", cwd=self.wt).split(), [task_tip, ticket_tip])
        self.assertEqual(self.kit.git("show", "HEAD:a.txt", cwd=self.wt), "both")
        self.assertEqual(self.kit.git("status", "--porcelain", cwd=self.wt), "")

    def test_a_patch_that_leaves_markers_fails_conflict_again(self):
        self.conflict()
        marked = (self.wt / "a.txt").read_text(encoding="utf-8").replace("ours", "ours, edited")
        self.apply(self.remote_patch({"a.txt": marked}))
        self.assert_fail(self.kit.step(FINISH, self.kit.run_dir, "01-02"), "FAIL 01-02 conflict a.txt (log: ")
        self.kit.git("rev-parse", "--verify", "MERGE_HEAD", cwd=self.wt)


if __name__ == "__main__":
    unittest.main()
