"""Tests for `next.py`: the orchestrator's next actions, read from a run folder as the steps leave it."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEXT = HERE / "next.py"
DECIDE = HERE / "decide.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ticket(tt, status, blocked="none", pr=None, extra=""):
    return (f"# Ticket {tt}\nStatus: {status}\nIssue: #{tt}\nBranch: feat/t{tt}\nBlocked by: {blocked}\n"
            + (f"PR: #{pr}\n" if pr else "") + extra)


def task(ident, status, writes, blocked="none", extra=""):
    return (f"# {ident}: task {ident}\nStatus: {status}\nTicket: {ident[:2]}\nBlocked by: {blocked}\n"
            f"Writes: {writes}\n{extra}\n## Acceptance criteria\n- works\n")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class NextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
        self.run_dir = run = self.tmp / "run"
        write(run / "run.md", f"# Run\nRepo: {self.repo}\nDefault: main\nRemote: none\nIdentity: login\n")

    def decide(self, *args):
        subprocess.run([sys.executable, "-I", str(DECIDE), str(self.run_dir), *args], check=True, capture_output=True)

    def next(self, *args):
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.run_dir), *args], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return [line.split("  (")[0] for line in p.stdout.splitlines()]

    def planned(self, slots=2):
        run = self.run_dir
        self.decide("set", "merge_identity", "app")
        self.decide("set", "review_before_agent_merge", "no")
        write(run / "spec.md", "# Spec\n")
        write(run / "areas.md", "# Areas\n\nno exploration needed\n")
        write(run / "map.md", "# Map\n\n## Lanes\n- 01\n")
        write(run / "tickets" / "01-a.md", ticket("01", "ready"))
        write(run / "tasks" / "01-01-x.md", task("01-01", "ready", "a.py"))
        self.decide("set", "test_command", "pytest")
        self.decide("set", "parallel", str(slots))
        self.decide("set", "approved", "yes")

    def test_setup_actions_come_one_at_a_time_in_step_order(self):
        run = self.run_dir
        (run / "run.md").unlink()
        self.assertEqual(self.next(), ["init run"])
        write(run / "run.md", f"# Run\nRepo: {self.repo}\n")
        self.assertEqual(self.next(), ["rules run"])
        self.decide("set", "merge_identity", "app")
        self.decide("set", "review_before_agent_merge", "no")
        self.assertEqual(self.next(), ["plan A"])
        write(run / "spec.md", "# Spec\n")
        write(run / "areas.md", "# Areas\n\n## store\nQuestion: x\n\n## cli\nQuestion: y\n")
        self.assertEqual(self.next(), ["explore store"])
        self.decide("set", "parallel", "3")
        self.assertEqual(self.next(), ["explore store", "explore cli"])
        write(run / "notes" / "store.md", "x")
        write(run / "notes" / "cli.md", "x")
        self.assertEqual(self.next(), ["plan B"])
        write(run / "map.md", "# Map\n")
        write(run / "tickets" / "01-a.md", ticket("01", "ready"))
        write(run / "tasks" / "01-01-x.md", task("01-01", "ready", "a.py"))
        write(run / "tasks" / "01-02-y.md", task("01-02", "ready", "a.py"))
        self.assertEqual(self.next(), ["plan revise problems=1"])
        write(run / "tasks" / "01-02-y.md", task("01-02", "ready", "a.py", blocked="01"))
        self.assertEqual(self.next(), ["setup run"])
        self.decide("set", "test_command", "pytest")
        self.assertEqual(self.next(), ["approve run"])
        self.decide("set", "approved", "yes")
        self.assertEqual(self.next(), ["start 01"])

    def test_a_blocked_planner_goes_to_the_human(self):
        self.decide("set", "merge_identity", "app")
        self.decide("set", "review_before_agent_merge", "no")
        write(self.run_dir / "reports" / "plan-A.json", json.dumps({"status": "blocked", "why": "no tracker"}))
        self.assertEqual(self.next(), ["human plan-A blocked: no tracker"])

    def test_dispatch_keeps_writes_disjoint_and_within_the_slots(self):
        self.planned(slots=2)
        run = self.run_dir
        write(run / "tickets" / "01-a.md", ticket("01", "building"))
        write(run / "tasks" / "01-02-y.md", task("01-02", "ready", "a.py, b.py"))
        write(run / "tasks" / "01-03-z.md", task("01-03", "ready", "c.py"))
        write(run / "tasks" / "01-04-w.md", task("01-04", "ready", "d.py"))
        self.assertEqual(self.next(), ["dispatch 01-01", "dispatch 01-03"])
        write(run / "tasks" / "01-01-x.md", task("01-01", "in-progress", "a.py"))
        self.assertEqual(self.next(), ["dispatch 01-03", "wait 01-01 implementer"])

    def test_a_task_moves_from_finish_to_review_to_merge(self):
        self.planned()
        run = self.run_dir
        write(run / "tickets" / "01-a.md", ticket("01", "building"))
        path = write(run / "tasks" / "01-01-x.md", task("01-01", "in-progress", "a.py", extra="Tier: high"))
        time.sleep(0.01)
        write(run / "reports" / "01-01.json", json.dumps({"status": "done"}))
        self.assertEqual(self.next(), ["finish 01-01"])
        write(path, task("01-01", "in-progress", "a.py", extra="Tier: high\nFinished: 2026-11-17T10:00:00Z"))
        wt = run / "worktrees" / "01-01"
        git(self.repo, "worktree", "add", "-q", "--detach", str(wt))
        self.assertEqual(self.next(), ["review 01-01"])
        write(run / "reviews" / f"01-01-{git(wt, 'rev-parse', '--short=7', 'HEAD')}.json", "{}")
        self.assertEqual(self.next(), ["merge 01-01"])

    def test_a_stale_output_is_not_a_finish(self):
        self.planned()
        run = self.run_dir
        write(run / "tickets" / "01-a.md", ticket("01", "building"))
        out = write(run / "reports" / "01-01.json", json.dumps({"status": "done"}))
        old = time.time() - 60
        os.utime(out, (old, old))
        write(run / "tasks" / "01-01-x.md", task("01-01", "in-progress", "a.py"))
        self.assertEqual(self.next(), ["wait 01-01 implementer"])

    def test_a_blocked_task_goes_to_the_human_first(self):
        self.planned()
        run = self.run_dir
        write(run / "tickets" / "01-a.md", ticket("01", "building"))
        write(run / "tasks" / "01-01-x.md", task("01-01", "in-progress", "a.py"))
        time.sleep(0.01)
        write(run / "reports" / "01-01.json", json.dumps({"status": "plan-wrong", "why": "no ids"}))
        self.assertEqual(self.next()[0], "human 01-01 plan-wrong: no ids")

    def test_a_ticket_ships_through_open_review_triage_and_merge(self):
        self.planned()
        run = self.run_dir
        git(self.repo, "branch", "feat/t01")
        tip = git(self.repo, "rev-parse", "feat/t01")
        write(run / "tasks" / "01-01-x.md", task("01-01", "done", "a.py"))
        write(run / "tickets" / "01-a.md", ticket("01", "building"))
        self.assertEqual(self.next(), ["ship 01 open"])
        write(run / "tickets" / "01-a.md", ticket("01", "building", pr=7))
        self.assertEqual(self.next(), ["ship 01 review pr=7"])
        write(run / "logs" / "PR7-review.json", json.dumps({"commit_id": tip}))
        self.assertEqual(self.next(), [f"ship 01 triage pr=7 head={tip[:7]}"])
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(run), "--triaged", "01", "none"],
                           capture_output=True, text=True)
        self.assertEqual(p.stdout, f"OK 01 triaged {tip[:7]} none\n")
        self.assertEqual(self.next(), ["ship 01 merge pr=7"])

    def test_a_fix_round_needs_a_re_review_and_a_third_goes_to_the_human(self):
        self.planned()
        run = self.run_dir
        git(self.repo, "branch", "feat/t01")
        first = git(self.repo, "rev-parse", "feat/t01")
        write(run / "tasks" / "01-01-x.md", task("01-01", "done", "a.py"))
        write(run / "tickets" / "01-a.md", ticket("01", "building", pr=7))
        write(run / "logs" / "PR7-review.json", json.dumps({"commit_id": first}))
        self.next("--triaged", "01", "fix")
        write(run / "tasks" / "01-02-review-fixes.md", task("01-02", "ready", "a.py"))
        self.assertEqual(self.next(), ["dispatch 01-02"])
        write(run / "tasks" / "01-02-review-fixes.md", task("01-02", "done", "a.py"))
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "fix")
        git(self.repo, "branch", "-f", "feat/t01", "HEAD")
        second = git(self.repo, "rev-parse", "feat/t01")
        self.assertEqual(self.next(), ["ship 01 re-review pr=7"])
        write(run / "logs" / "PR7-review.json", json.dumps({"commit_id": second}))
        self.next("--triaged", "01", "fix")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "fix2")
        git(self.repo, "branch", "-f", "feat/t01", "HEAD")
        write(run / "logs" / "PR7-review.json", json.dumps({"commit_id": git(self.repo, "rev-parse", "HEAD")}))
        self.assertEqual(self.next(), ["human 01 review-round-3 pr=7"])

    def test_the_end_cleans_and_reports(self):
        self.planned()
        write(self.run_dir / "tickets" / "01-a.md", ticket("01", "merged"))
        write(self.run_dir / "tasks" / "01-01-x.md", task("01-01", "done", "a.py"))
        self.assertEqual(self.next(), ["clean run", "report run"])

    def test_a_ticket_waiting_on_another_waits(self):
        self.planned()
        write(self.run_dir / "tickets" / "02-b.md", ticket("02", "ready", blocked="01"))
        write(self.run_dir / "tasks" / "02-01-y.md", task("02-01", "ready", "b.py"))
        self.assertEqual(self.next(), ["start 01", "wait 02 on ticket 01"])

    def test_no_run_folder(self):
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.tmp / "none")], capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout.split(":")[0]), (1, "FAIL run no-run-folder"))


if __name__ == "__main__":
    unittest.main()
