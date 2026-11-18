"""Tests for `next.py --go` and `--returned`: the actions needing no decision done in one call, as a run does them."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import REVIEWS  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

NEXT = SCRIPTS / "next.py"
REPO = SCRIPTS.parents[2]


def task(ident, writes):
    return (f"# {ident}: add {writes}\nStatus: ready\nTicket: {ident[:2]}\nBlocked by: none\nWrites: {writes}\n\n"
            "## Acceptance criteria\n- works\n")


def output(writes):
    return {"status": "done", "why": "", "commit": "", "files": [writes],
            "tests": [{"name": "t", "criterion": "works", "broke": "removed it"}],
            "suites": [{"command": "true", "baseline_s": 0.1, "final_s": 0.1, "passed": True}], "followups": []}


class GoTest(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.make_origin()
        kit.init_run()
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        run = self.run = kit.run_dir
        for d in ("tickets", "tasks", "reports"):
            (run / d).mkdir(exist_ok=True)
        (run / "map.md").write_text("# Map\n\n## Checks\n\n```\ntrue\n```\n\n## Lanes\n- 01\n", encoding="utf-8")
        (run / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (run / "areas.md").write_text("# Areas\n\nno exploration needed\n", encoding="utf-8")
        (run / "tickets" / "01-a.md").write_text(
            "# 01: ticket a\nStatus: ready\nIssue: #1\nBranch: feat/a\nBlocked by: none\n", encoding="utf-8")
        (run / "tasks" / "01-01-x.md").write_text(task("01-01", "x.txt"), encoding="utf-8")
        (run / "tasks" / "01-02-y.md").write_text(task("01-02", "y.txt"), encoding="utf-8")
        (run / "decisions.md").write_text(
            "# Decisions\nbase: main\nci: off\nmerge_identity: app\nreview_before_agent_merge: no\n"
            "test_command: true\ncaps: 1500 300\nparallel: 2\napproved: yes\n", encoding="utf-8")
        kit.answer("api-user", out="rui\n")

    def next(self, *args, message=None):
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.run), *args], input=message or "",
                           capture_output=True, text=True, env=self.kit.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return [line.split("  (")[0] for line in p.stdout.splitlines()]

    def implement(self, ident, name):
        wt = self.run / "worktrees" / ident
        (wt / name).write_text("done\n", encoding="utf-8")
        self.kit.git("add", ".", cwd=wt)
        self.kit.git("commit", "-qm", f"feat: {name}", cwd=wt)
        out = self.run / "reports" / f"{ident}.json"
        out.write_text(json.dumps(output(name)), encoding="utf-8")
        return json.dumps({"status": "done", "output": str(out)})

    def test_go_starts_the_ticket_and_prepares_both_tasks_in_one_call(self):
        lines = self.next("--go")
        self.assertTrue(lines[0].startswith("OK 01 building"), lines)
        agents = [line for line in lines if line.startswith("agent ")]
        self.assertEqual([line.split(" ")[1] for line in agents], ["01-01", "01-02"], lines)
        self.assertIn("description=implement: 01-01 add x.txt", agents[0])
        self.assertTrue(Path(agents[0].split(" ")[2].removeprefix("prompt=")).is_file(), agents[0])
        self.assertFalse([line for line in lines if line.startswith(("dispatch ", "start ", "wait 01-0"))], lines)

    def test_returned_checks_finishes_and_merges_in_one_call(self):
        self.next("--go")
        message = self.implement("01-01", "x.txt")
        lines = self.next("--returned", "01-01", message=message)
        self.assertEqual(lines[0], "OK 01-01 done")
        self.assertTrue(lines[1].startswith("OK 01-01 "), lines)
        self.assertTrue(any(line.startswith("OK 01-01 done suites=") for line in lines), lines)
        self.assertIn("Status: done", (self.run / "tasks" / "01-01-x.md").read_text(encoding="utf-8"))
        self.assertIn("wait 01-02 implementer", lines)

    def test_once_every_task_merged_go_opens_the_ticket_pr(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        lines = self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        self.assertIn("OK 01 open pr=99", lines)
        self.assertTrue(any(line.startswith("ship 01 review pr=99") for line in lines), lines)

    def test_reviewed_posts_the_review_and_shows_its_findings_for_the_triage(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.kit.answer(REVIEWS.format(n=99), out=json.dumps({"html_url": "https://github.com/o/r/pull/99#r1"}))
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        review = self.run / "reviews" / "99-abc1234.json"
        review.parent.mkdir(exist_ok=True)
        review.write_text(json.dumps({
            "status": "done", "mode": "ticket", "checked": "every hunk", "process": [], "followups": [],
            "findings": [{"severity": "should-fix", "axis": "bugs", "path": "x.txt", "line": 1,
                          "claim": "x is wrong", "scenario": "read x"}]}), encoding="utf-8")
        head = self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/a")
        self.kit.answer("pr-view", out=json.dumps({"headRefOid": head}))
        lines = self.next("--reviewed", "01", str(review))
        self.assertEqual(lines[0], "OK PR99 review https://github.com/o/r/pull/99#r1")
        self.assertTrue(any("x is wrong" in line for line in lines), lines)

    def test_each_action_line_carries_its_instruction(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.run), "--returned", "01-02"],
                           input=self.implement("01-02", "y.txt"), capture_output=True, text=True, env=self.kit.env)
        review = [line for line in p.stdout.splitlines() if line.startswith("ship 01 review")]
        self.assertEqual(len(review), 1, p.stdout)
        self.assertIn("(ship.md: a `pr-reviewer` on `review_prompt.py ticket --pr <n>`", review[0])

    def test_a_first_brief_violation_writes_the_reissue_prompt(self):
        self.next("--go")
        self.implement("01-01", "x.txt")
        lines = self.next("--returned", "01-01", message="not json")
        self.assertTrue(lines[0].startswith("FAIL 01-01 brief-violation"), lines)
        self.assertTrue(lines[1].startswith("agent 01-01 "), lines)


if __name__ == "__main__":
    unittest.main()
