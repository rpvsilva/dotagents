"""Tests for `next.py --go` and `--returned`: the actions needing no decision done in one call, as a run does them."""
import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ship import REVIEWS  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402
from steplib import runfolder  # noqa: E402

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
        review = [line for line in lines if line.startswith("agent 01 ")]
        self.assertEqual(len(review), 1, lines)
        self.assertIn("agent=pr-reviewer description=ticket-review: PR 99", review[0])

    def test_no_ship_leaves_the_ticket_pr_to_a_ship_line(self):
        # The ticket-commits and hub-sync-conflict evals stop before shipping, with no GitHub.
        self.next("--go", "--no-ship")
        self.next("--returned", "01-01", "--no-ship", message=self.implement("01-01", "x.txt"))
        lines = self.next("--returned", "01-02", "--no-ship", message=self.implement("01-02", "y.txt"))
        self.assertFalse([line for line in lines if line.startswith("OK 01 open")], lines)
        self.assertIn("ship 01 open", lines)
        self.assertNotIn("PR:", (self.run / "tickets" / "01-a.md").read_text(encoding="utf-8"))

    def ready_to_merge(self, clause_waived):
        # Run #16 e1 on turns-remote: `--go` merged a ticket PR touching AGENTS.md although the human said no to it;
        # the sandbox has no agent-merge-guard block, so the guard passed it.
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.kit.answer(REVIEWS.format(n=99), out=json.dumps({"html_url": "https://github.com/o/r/pull/99#r1"}))
        path = self.run / "decisions.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## Authorizations\n\n| clause | waived | quote |\n"
                        f"| --- | --- | --- |\n| AGENTS.md changes | {clause_waived} | no for AGENTS.md |\n",
                        encoding="utf-8")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        review = self.run / "reviews" / "99-abc1234.json"
        review.parent.mkdir(exist_ok=True)
        review.write_text(json.dumps({"status": "done", "mode": "ticket", "checked": "every hunk", "process": [],
                                      "followups": [], "findings": []}), encoding="utf-8")
        head = self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/a")
        self.kit.answer("pr-view", out=json.dumps({"headRefOid": head}))
        self.next("--reviewed", "01", str(review))
        self.next("--triaged", "01", "none")

    def test_an_unwaived_clause_no_guard_block_enforces_holds_the_ticket_merge(self):
        self.ready_to_merge("no")
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.run), "--go"], capture_output=True, text=True,
                           env=self.kit.env)
        merge = [line for line in p.stdout.splitlines() if line.startswith("ship 01 merge")]
        self.assertEqual(len(merge), 1, p.stdout)
        self.assertIn('no `agent-merge-guard` block enforces the clauses this run did not waive ("AGENTS.md changes")',
                      merge[0])
        self.assertFalse([line for line in p.stdout.splitlines() if line.startswith("OK 01 ")], p.stdout)

    def test_unguarded_is_empty_with_a_guard_block_or_every_clause_waived(self):
        import importlib
        nxt = importlib.import_module("next")
        from steplib import decisions
        self.ready_to_merge("no")
        self.assertEqual(nxt.unguarded(self.run, decisions.load(self.run)), ["AGENTS.md changes"])
        clone = self.kit.clone
        (clone / "AGENTS.md").write_text("# Rules\n\n```agent-merge-guard\nagents path AGENTS.md\n```\n",
                                         encoding="utf-8")
        self.kit.git("add", "AGENTS.md", cwd=clone)
        self.kit.git("commit", "-qm", "docs: guard block", cwd=clone)
        self.kit.git("push", "-q", "origin", "HEAD:main", cwd=clone)
        self.kit.git("fetch", "-q", "origin", cwd=clone)
        self.assertEqual(nxt.unguarded(self.run, decisions.load(self.run)), [])
        d = decisions.load(self.run)
        d.authorizations = [d.authorizations[0]._replace(waived="yes")]
        self.assertEqual(nxt.unguarded(self.run, d), [])

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
        import next as nxt
        self.assertIn("(ship.md: `next.py <run> --go` prepares its reviewers",
                      nxt.how("ship 01 review pr=99  (ship.md)"))
        self.assertIn("(build.md: `next.py <run> --go` writes its reviewer's prompt", nxt.how("review 01-01  (build.md)"))

    def test_go_prepares_a_ticket_review_here_and_shows_it_for_the_post(self):
        # #543: the review steps (tier, prompt, post) run inside next.py; the orchestrator only starts the reviewer.
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        lines = self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        prompt = Path(next(line for line in lines if line.startswith("agent 01 ")).split(" ")[2])
        text = prompt.read_text(encoding="utf-8")
        head7 = prompt.stem.split("-")[2]
        output = self.run / "reviews" / f"99-{head7}.json"
        self.assertIn(str(output), text)
        self.assertIn('"mode": "ticket"', text)
        self.assertNotIn("agent 01 ", " ".join(self.next("--go")))  # prepared once
        output.write_text(json.dumps({"status": "done", "mode": "ticket", "findings": [], "checked": "every hunk",
                                      "process": [], "followups": []}), encoding="utf-8")
        lines = self.next("--go")
        self.assertIn(f"ship 01 post pr=99 output={output}", lines)
        self.assertIn("OK reviewer done", lines)
        self.assertNotIn("OK reviewer done", self.next("--go"))  # shown once
        head = self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/a")
        self.kit.answer("pr-view", out=json.dumps({"headRefOid": head}))
        self.kit.answer(REVIEWS.format(n=99), out=json.dumps({"html_url": "https://github.com/o/r/pull/99#r1"}))
        lines = self.next("--reviewed", "01", str(output))
        self.assertEqual(lines[0], "OK PR99 review https://github.com/o/r/pull/99#r1")
        self.assertFalse((self.run / "worktrees" / f"review-99-{head7}").exists())

    def test_a_high_tier_task_review_is_prepared_once_and_posted_after_the_deep_review(self):
        runfolder.set_header(self.run / "tasks" / "01-01-x.md", "Tier", "high")
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        lines = self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        review = [line for line in lines if line.startswith("agent 01-01 ")]
        self.assertEqual(len(review), 1, lines)
        self.assertIn("agent=pr-reviewer description=review: 01-01", review[0])
        self.assertIn("review 01-01", lines)
        self.assertNotIn("agent 01-01 ", " ".join(self.next("--go")))
        prompt = Path(review[0].split(" ")[2])
        task_output = self.run / "reviews" / f"01-01-{prompt.stem.split('-')[3]}.json"
        self.assertIn(str(task_output), prompt.read_text(encoding="utf-8"))
        clean = json.dumps({"status": "done", "mode": "task", "findings": [], "checked": "every hunk", "process": [],
                            "followups": []})
        task_output.write_text(clean, encoding="utf-8")
        lines = self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        prompt = Path(next(line for line in lines if line.startswith("agent 01 ")).split(" ")[2])
        output = self.run / "reviews" / f"99-{prompt.stem.split('-')[2]}.json"
        output.write_text(clean.replace('"task"', '"ticket"'), encoding="utf-8")
        head = self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/a")
        self.kit.answer("pr-view", out=json.dumps({"headRefOid": head}))
        self.kit.answer(REVIEWS.format(n=99), out=json.dumps({"html_url": "https://github.com/o/r/pull/99#r1"}))
        lines = self.next("--reviewed", "01", str(output))
        self.assertEqual([line for line in lines if line.startswith("OK PR99 review")], ["OK PR99 review https://github.com/o/r/pull/99#r1"] * 2, lines)
        self.assertTrue(task_output.with_name(task_output.stem + ".posted.md").is_file())

    def remote_on(self):
        path = self.run / "decisions.md"
        path.write_text(path.read_text(encoding="utf-8").replace("approved: yes", "remote: on\napproved: yes"),
                        encoding="utf-8")

    def test_with_remote_on_go_prepares_remote_runs_with_no_flag(self):
        # Replay e1 of #402 ran every agent here: the orchestrator never passed `--go --remote`.
        self.remote_on()
        lines = self.next("--go")
        remote = [line for line in lines if line.startswith("remote ")]
        self.assertEqual([line.split(" ")[1] for line in remote], ["01-01", "01-02"], lines)
        self.assertIn("--name 01-01-x", remote[0])
        self.assertFalse([line for line in lines if line.startswith("agent ")], lines)

    def test_local_overrides_remote_on(self):
        self.remote_on()
        lines = self.next("--go", "--local")
        self.assertEqual([line.split(" ")[1] for line in lines if line.startswith("agent ")], ["01-01", "01-02"])

    def test_returned_reads_a_remote_tasks_saved_output_with_no_flag(self):
        self.remote_on()
        self.next("--go")
        self.implement("01-01", "x.txt")
        lines = self.next("--returned", "01-01", message="ignored: a remote agent's output is its saved report")
        self.assertEqual(lines[0], "OK 01-01 done")
        self.assertIn("Status: done", (self.run / "tasks" / "01-01-x.md").read_text(encoding="utf-8"))

    def test_with_remote_on_the_ticket_review_names_the_remote_way(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        self.remote_on()
        p = subprocess.run([sys.executable, "-I", str(NEXT), str(self.run), "--returned", "01-02"],
                           input=self.implement("01-02", "y.txt"), capture_output=True, text=True, env=self.kit.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        import next as nxt
        self.assertIn("(ship.md: remote is on: `next.py <run> --go` starts its remote reviewers itself",
                      nxt.how("ship 01 review pr=99  (ship.md)", remote=True))

    def stub_remote(self, exit_code=0, sleep=0):
        """A `remote-agent` that edits `<name's last word>.txt` in the worktree, leaves it uncommitted as a remote run
        does, and prints the output object on stdout."""
        stub = self.kit.home / ".agents" / "bin" / "remote-agent"
        out = json.dumps(output("NAME.txt")).replace("NAME", "$word")
        stub.write_text("#!/bin/sh\n"
                        f"sleep {sleep}\n"
                        'while [ "$1" != "${1#--}" ]; do [ "$1" = --name ] && name=$2; shift 2; done\n'
                        'word=${name##*-}; echo "[$name] transcript: /t/$name.jsonl" >&2\n'
                        f'[ {exit_code} = 0 ] && echo done > "$2/$word.txt"\n'
                        f"echo '{out}'\n"
                        f'[ {exit_code} = 0 ] || echo "[$name] usage limit: resets 5pm" >&2\n'
                        f"exit {exit_code}\n", encoding="utf-8")
        stub.chmod(0o755)

    def test_go_starts_remote_runs_and_wait_finishes_them(self):
        # e1b and e2 of #402: each remote task cost the orchestrator a background launch and a `--returned` call.
        self.remote_on()
        self.stub_remote()
        lines = self.next("--go", "--no-ship")
        started = [line for line in lines if line.startswith("remote ")]
        self.assertEqual([line.split(" ")[1:3] for line in started], [["01-01", "started"], ["01-02", "started"]])
        lines = self.next("--wait", "--no-ship")
        for ident in ("01-01-x", "01-02-y"):
            self.assertIn("Status: done", (self.run / "tasks" / f"{ident}.md").read_text(encoding="utf-8"), lines)
        self.assertTrue(any(line.startswith("ship 01 open") for line in lines), lines)
        self.assertEqual(list((self.run / "remote").glob("*.pid")), [])

    def test_wait_collects_into_a_run_folder_with_no_reports_folder_yet(self):
        # Replay e3 of #402: a replayed run folder had no `reports/`, `--wait` crashed moving 01-01's output there,
        # after the pid was gone, and the output was lost.
        self.remote_on()
        self.stub_remote()
        (self.run / "reports").rmdir()
        self.next("--go", "--no-ship")
        self.next("--wait", "--no-ship")
        for ident in ("01-01-x", "01-02-y"):
            self.assertIn("Status: done", (self.run / "tasks" / f"{ident}.md").read_text(encoding="utf-8"))

    def test_the_post_instruction_keeps_base_to_a_re_review(self):
        # Replay e3: on a first review the orchestrator passed the head as `--base`, so the posted header read
        # `git diff <head>...<head>`.
        import next as nxt
        line = nxt.how("ship 01 post pr=9 output=/r/9-abc1234.json  (ship.md)")
        self.assertIn("only when the line has `base=`", line)

    def test_a_failed_remote_run_is_left_to_the_orchestrator_with_its_stderr(self):
        self.remote_on()
        self.stub_remote(exit_code=5)
        lines, fails = [], []
        for args in (("--go",), ("--wait",), ("--wait",)):  # each call collects the runs that ended by then
            if len(fails) < 2:
                lines = self.next(*args, "--no-ship")
                fails += [line for line in lines if line.startswith("FAIL ")]
        self.assertEqual(sorted(line.split(" ")[1:4] for line in fails),
                         [["01-01", "remote-exit", "5:"], ["01-02", "remote-exit", "5:"]], lines)
        self.assertIn("usage limit: resets 5pm", fails[0])
        self.assertIn("Status: in-progress", (self.run / "tasks" / "01-01-x.md").read_text(encoding="utf-8"))
        self.assertEqual(list((self.run / "remote").glob("*.pid")), [])

    def test_wait_returns_the_wait_lines_when_its_time_runs_out(self):
        self.remote_on()
        self.stub_remote(sleep=30)
        self.next("--go", "--no-ship")
        self.kit.env["BUILD_EPIC_WAIT_SECS"] = "1"
        lines = self.next("--wait", "--no-ship")
        self.assertEqual(lines, ["wait 01-01 remote", "wait 01-02 remote"])
        self.assertEqual(len(list((self.run / "remote").glob("*.pid"))), 2)
        for pid in (self.run / "remote").glob("*.pid"):
            os.killpg(int(pid.read_text(encoding="utf-8")), signal.SIGTERM)

    def stub_remote_reviewer(self, exit_code=3):
        """A `remote-agent` for reviewers: a run saves a clean review as its report and its `result.json`, exits
        `exit_code`; `remote-agent result <name>` prints that result."""
        state = self.kit.tmp / "remote-state"
        state.mkdir(exist_ok=True)
        report = json.dumps({"status": "done", "mode": "ticket", "findings": [], "checked": "every hunk",
                             "process": [], "followups": []})
        stub = self.kit.home / ".agents" / "bin" / "remote-agent"
        stub.write_text("#!/bin/sh\n"
                        f'S={state}\n'
                        'if [ "$1" = result ]; then cat "$S/$2.json"; exit 0; fi\n'
                        'echo "$*" >"$S/argv"\n'
                        'while [ "$1" != "${1#--}" ]; do [ "$1" = --name ] && name=$2; shift 2; done\n'
                        f"echo '{report}' >\"$S/$name.md\"\n"
                        f'echo "{{\\"exit_code\\": {exit_code}, \\"report_path\\": \\"$S/$name.md\\"}}" >"$S/$name.json"\n'
                        f"exit {exit_code}\n", encoding="utf-8")
        stub.chmod(0o755)
        return state

    def ship_with_remote_review(self, exit_code=3):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/99\n")
        self.next("--go")
        self.next("--returned", "01-01", message=self.implement("01-01", "x.txt"))
        self.remote_on()
        state = self.stub_remote_reviewer(exit_code)
        head = self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/a")
        self.kit.answer("pr-view", out=json.dumps({
            "author": {"login": "app/agent"}, "title": "t", "body": "", "headRefOid": head, "baseRefName": "main",
            "files": []}))
        lines = self.next("--returned", "01-02", message=self.implement("01-02", "y.txt"))
        head = self.kit.git("rev-parse", "feat/a", cwd=self.kit.clone)
        return lines, state, head[:7]

    def test_go_starts_the_remote_ticket_review_and_wait_collects_it_for_the_triage(self):
        # e1b and e2 of #402: each remote review cost the orchestrator a prompt, a launch and a result call.
        lines, state, head7 = self.ship_with_remote_review()
        self.assertTrue(any(line.startswith("OK 01 open pr=99") for line in lines), lines)
        self.assertIn(f"remote 01 review started name=review-99-{head7}", " ".join(lines))
        self.assertEqual(runfolder.get(self.run / "tickets" / "01-a.md", "Opened by"), "app/agent")
        lines = self.next("--wait")
        argv = (state / "argv").read_text(encoding="utf-8")
        self.assertIn("--agent pr-reviewer", argv)
        self.assertIn(f"/worktrees/review-99-{head7}", argv)
        output = self.run / "reviews" / f"99-{head7}.json"
        self.assertIn("OK reviewer done", lines)
        self.assertIn(f"ship 01 post pr=99 output={output}", lines)
        self.assertTrue(output.is_file())
        self.assertTrue((self.run / "remote" / f"review-99-{head7}.json").is_file())
        self.assertFalse((self.run / "worktrees" / f"review-99-{head7}").exists())
        prompt = (self.run / "prompts" / f"review-99-{head7}.md").read_text(encoding="utf-8")
        self.assertIn('"remote": true', prompt)

    def test_a_failed_remote_review_falls_back_to_a_review_here(self):
        _, _, head7 = self.ship_with_remote_review(exit_code=1)
        lines = self.next("--wait")
        self.assertTrue(any(line.startswith("FAIL 01 remote-exit 1") for line in lines), lines)
        self.assertTrue((self.run / "remote" / f"review-99-{head7}.local").is_file())
        self.assertTrue(any(line.startswith("agent 01 ") and "-local.md agent=pr-reviewer" in line for line in lines),
                        lines)  # the next `go` prepared it here
        lines = self.next("--go")
        self.assertIn("wait 01 review here", lines)
        self.assertFalse([line for line in lines if line.startswith(("agent 01 ", "remote 01 "))], lines)

    def test_a_first_brief_violation_writes_the_reissue_prompt(self):
        self.next("--go")
        self.implement("01-01", "x.txt")
        lines = self.next("--returned", "01-01", message="not json")
        self.assertTrue(lines[0].startswith("FAIL 01-01 brief-violation"), lines)
        self.assertTrue(lines[1].startswith("agent 01-01 "), lines)


if __name__ == "__main__":
    unittest.main()
