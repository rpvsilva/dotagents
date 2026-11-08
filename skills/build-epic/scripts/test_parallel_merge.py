"""Tests for the repo lock: task merges into different tickets run in parallel, and the lock covers only the git
commands that edit shared metadata, never a retry's sleep."""
import datetime
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, gitops, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

MERGE_TASK = SCRIPTS / "merge_task.py"
REPO = SCRIPTS.parents[2]
SUITE_SECONDS = 4
N = 5

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
        (kit.run_dir / "map.md").write_text(
            f"# Map\n\n## Checks\n\n```\nsleep {SUITE_SECONDS}\n```\n\n## Gaps\n", encoding="utf-8")
        kit.answer("pr-list", out="[]")
        kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        for i in range(1, N + 2):  # ticket N+1 is the lone merge the others are timed against
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

    def stamp(self, i):
        log = (self.kit.run_dir / "logs" / f"0{i}-01-merge_task.log").read_text(encoding="utf-8")
        m = re.search(r"^== merge_task \S+  at (\S+)$", log, re.MULTILINE)
        return datetime.datetime.fromisoformat(m.group(1))

    def test_five_merges_into_five_tickets_take_about_one_merges_time(self):
        t0 = time.monotonic()
        self.finish(self.start(N + 1))
        one = time.monotonic() - t0

        t0 = time.monotonic()
        procs = [self.start(i) for i in range(1, N + 1)]
        for p in procs:
            self.finish(p)
        wall = time.monotonic() - t0

        self.assertLessEqual(wall, 1.5 * one, f"5 merges took {wall:.1f}s, one took {one:.1f}s")
        starts = [self.stamp(i) for i in range(1, N + 1)]
        spread = (max(starts) - min(starts)).total_seconds()
        self.assertLess(spread, SUITE_SECONDS - 1, f"the merges started {spread}s apart: they queued")
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

    def within(self, seconds, fn):
        """True when `fn` returns in `seconds` while another holder has the repo lock."""
        done = threading.Event()
        t = threading.Thread(target=lambda: (fn(), done.set()), daemon=True)
        self.threads.append(t)
        t.start()
        return done.wait(seconds)

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
            for name, fn in {"fetch": lambda: self.git.fetch(),
                             "worktree add": lambda: self.git.worktree_add(self.kit.tmp / "w2", "origin/main", branch="b2"),
                             "branch -D": lambda: self.git._git("branch", "-D", "nope", check=False)}.items():
                self.assertFalse(self.within(0.5, fn), f"{name} ran while the repo lock was held")

    def test_push_retry_sleeps_with_the_lock_released(self):
        hook = self.kit.origin / "hooks" / "pre-receive"
        count = self.kit.tmp / "count"
        hook.write_text(f'#!/bin/sh\nn=$(cat {count} 2>/dev/null || echo 0)\necho $((n+1)) > {count}\n'
                        'if [ "$n" -lt 2 ]; then echo "error: HTTP 502 Bad Gateway" >&2; exit 1; fi\n', encoding="utf-8")
        hook.chmod(0o755)
        os.environ["BUILD_EPIC_RETRY_SLEEP"] = "1"  # waits of 2 s and 4 s
        self.kit.git("commit", "-q", "--allow-empty", "-m", "x", cwd=self.clone)
        result = []
        pusher = threading.Thread(target=lambda: result.append(self.git.push("origin", "HEAD:refs/heads/pushed")))
        pusher.start()
        deadline = time.monotonic() + 10
        while not (self.kit.tmp / "count").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)  # the first try has failed and the push sleeps
        t0 = time.monotonic()
        with gitops.lock(self.clone):
            waited = time.monotonic() - t0
        pusher.join()
        self.assertLess(waited, 1.0, "the lock stayed held across the retry sleep")
        self.assertEqual(result[0].rc, 0)


if __name__ == "__main__":
    unittest.main()
