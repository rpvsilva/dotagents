"""Tests for the repo lock: task merges into different tickets run in parallel, and the lock covers only the git
commands that edit shared metadata, never a retry's sleep."""
import os
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gh, gitops, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

MERGE_TASK = SCRIPTS / "merge_task.py"
REPO = SCRIPTS.parents[2]
N = 5
BARRIER_SECONDS = 30
# The Checks: each merge's suite marks its arrival, then waits until all N merges are inside their suites at once. Merges
# that queue never fill the barrier, so the first one's suite fails after BARRIER_SECONDS; merges in parallel pass at once.
BARRIER = ('touch {d}/$$; i=0; until [ "$(ls {d} | wc -l)" -ge {n} ]; do i=$((i+1)); '
           '[ $i -le {tries} ] || {{ echo "only $(ls {d} | wc -l) of {n} merges reached the Checks"; exit 1; }}; '
           'sleep 0.05; done')

# `gh pr view N` answers like GitHub for PR 10+i: head is task branch `task/t<i>/01` on origin, base `feat/t<i>`, MERGED.
PR_VIEW = r'''#!{python} -IS
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:2] != ["pr", "view"]:
    os.execv(sys.executable, [sys.executable, "-IS", {kit_gh!r}, *args])
i = int(args[2]) - 10
head = subprocess.run(["git", "--git-dir", {origin!r}, "rev-parse", "task/t%d/01" % i],
                      capture_output=True, text=True, encoding="utf-8").stdout.strip()
print(json.dumps({{"state": "MERGED", "headRefOid": head, "baseRefName": "feat/t%d" % i}}))
'''


class ParallelMergeTest(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        self.clone = kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)), encoding="utf-8")
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.barrier = kit.tmp / "barrier"
        self.barrier.mkdir()
        check = BARRIER.format(d=self.barrier.as_posix(), n=N, tries=BARRIER_SECONDS * 20)
        (kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\n```\n{check}\n```\n\n## Gaps\n", encoding="utf-8")
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        for i in range(1, N + 1):
            self.ticket(i)

    def ticket(self, i):
        kit, run = self.kit, self.kit.run_dir
        kit.branch_from_main(f"feat/t{i}")
        (run / "tickets" / f"0{i}-t.md").write_text(
            f"# 0{i}: ticket {i}\nStatus: building\nIssue: #{i}\nBranch: feat/t{i}\nBlocked by: none\n", encoding="utf-8")
        (run / "tasks" / f"0{i}-01-a.md").write_text(
            f"# 0{i}-01: task\nStatus: in-progress\nTicket: 0{i}\nBlocked by: none\nWrites: x\n"
            f"PR: https://github.com/o/r/pull/{10 + i}\n", encoding="utf-8")
        wt = run / "worktrees" / f"0{i}-01"
        kit.git("worktree", "add", "-q", "-b", f"task/t{i}/01", str(wt), f"origin/feat/t{i}", cwd=self.clone)
        (wt / f"task{i}.txt").write_text("task\n", encoding="utf-8")
        kit.git("add", ".", cwd=wt)
        kit.git("commit", "-qm", f"feat: task {i}", cwd=wt)

    def start(self, i):
        return subprocess.Popen([sys.executable, "-I", str(MERGE_TASK), str(self.kit.run_dir), f"0{i}-01"],
                                env=self.kit.env, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def finish(self, p):
        out, err = p.communicate()
        self.assertTrue(out.startswith("OK "), out + err)

    def test_five_merges_into_five_tickets_run_their_checks_at_once(self):
        procs = [self.start(i) for i in range(1, N + 1)]
        for p in procs:
            self.finish(p)
        self.assertEqual(len(list(self.barrier.iterdir())), N, "each merge ran its Checks once")
        for i in range(1, N + 1):
            self.assertEqual(runfolder.get(self.kit.run_dir / "tasks" / f"0{i}-01-a.md", "Status"), "done")


class LockScopeTest(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        self.clone = kit.make_origin()
        self.log = open(kit.tmp / "git.log", "a", encoding="utf-8")
        self.addCleanup(self.log.close)
        self.git = gitops.Git(self.clone, self.log)
        self.threads = []
        self.addCleanup(lambda: [t.join(20) for t in self.threads])  # runs before the log closes
        self.env = dict(os.environ)
        for k, v in kit.env.items():
            os.environ[k] = v
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.env)))

    def started(self, fn):
        """`fn` started on a thread; returns the event it sets when it returns."""
        done = threading.Event()
        t = threading.Thread(target=lambda: (fn(), done.set()), daemon=True)
        self.threads.append(t)
        t.start()
        return done

    def within(self, seconds, fn):
        """True when `fn` returns in `seconds` while another holder has the repo lock."""
        return self.started(fn).wait(seconds)

    def take_lock(self):
        with gitops.lock(self.clone):
            pass

    def test_read_only_commands_and_commands_in_a_worktree_take_no_lock(self):
        wt = self.kit.tmp / "wt"
        self.git.worktree_add(wt, "origin/main", branch="task/x")
        other = gitops.Git(wt, self.log)
        with gitops.lock(self.clone):
            calls = {
                "tip": lambda: self.git.tip(),
                "is_ancestor": lambda: self.git.is_ancestor("origin/main"),
                "worktree git": lambda: other._git("status", "--porcelain"),
                "diff": lambda: other._git("diff", "--name-only", "origin/main...HEAD"),
                "merge": lambda: other._git("merge", "--no-edit", "origin/main"),
                "worktree list": lambda: self.git._git("worktree", "list", "--porcelain"),
            }
            for name, fn in calls.items():
                self.assertTrue(self.within(5, fn), f"{name} waited for the repo lock")

    def test_fetch_and_worktree_and_branch_edits_wait_for_the_lock(self):
        with gitops.lock(self.clone):
            calls = {"fetch": lambda: self.git.fetch(),
                     "worktree add": lambda: self.git.worktree_add(self.kit.tmp / "w2", "origin/main", branch="b2"),
                     "branch -D": lambda: self.git._git("branch", "-D", "nope", check=False)}
            done = {name: self.started(fn) for name, fn in calls.items()}  # all wait at once: one 0.5 s wait, not three
            time.sleep(0.5)
            for name, ev in done.items():
                self.assertFalse(ev.is_set(), f"{name} ran while the repo lock was held")
        for name, ev in done.items():
            self.assertTrue(ev.wait(20), f"{name} never ran once the lock was free")

    def test_push_retry_sleeps_with_the_lock_released(self):
        hook = self.kit.origin / "hooks" / "pre-receive"
        count = self.kit.tmp / "count"
        hook.write_text(f'#!/bin/sh\nn=$(cat {count} 2>/dev/null || echo 0)\necho $((n+1)) > {count}\n'
                        'if [ "$n" -lt 1 ]; then echo "error: HTTP 502 Bad Gateway" >&2; exit 1; fi\n', encoding="utf-8")
        hook.chmod(0o755)
        sleeping, wake, waits = threading.Event(), threading.Event(), []

        def sleep(seconds):  # the retry's wait, held open until the test has tried the lock
            waits.append(seconds)
            sleeping.set()
            wake.wait(20)

        os.environ["BUILD_EPIC_RETRY_SLEEP"] = "1"
        self.kit.git("commit", "-q", "--allow-empty", "-m", "x", cwd=self.clone)
        result = []
        with mock.patch.object(gh, "time", types.SimpleNamespace(sleep=sleep)):
            pusher = threading.Thread(target=lambda: result.append(self.git.push("origin", "HEAD:refs/heads/pushed")))
            pusher.start()
            self.assertTrue(sleeping.wait(20), "the push never waited to retry")
            free = self.within(5, self.take_lock)
            wake.set()
            pusher.join(20)
        self.assertTrue(free, "the lock stayed held across the retry sleep")
        self.assertEqual(waits, [2])
        self.assertEqual(count.read_text(encoding="utf-8").strip(), "2")
        self.assertEqual(result[0].rc, 0)

if __name__ == "__main__":
    unittest.main()
