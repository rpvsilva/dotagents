"""Tests for `post_review.py` and `ship_ticket.py` against a stub `gh`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, decisions, prbody, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

POST_REVIEW = SCRIPTS / "post_review.py"
SHIP = SCRIPTS / "ship_ticket.py"
REPO = SCRIPTS.parents[2]

# `gh pr view` answers like GitHub: the PR's head is the ticket branch's tip on origin, its state `answers/pr-state`
# (default MERGED), its base `answers/pr-base` (default main). Every other call goes to the testkit stub.
PR_VIEW = r'''#!{python} -IS
import os, sys
d = os.environ["GH_STUB_DIR"]
args = sys.argv[1:]
if args[:2] != ["pr", "view"]:
    if os.name == "nt":  # Windows starts no `#!` script, and its os.execv returns before the child ends
        import subprocess
        sys.exit(subprocess.run([sys.executable, "-IS", {kit_gh!r}, *args]).returncode)
    os.execv(sys.executable, [sys.executable, "-IS", {kit_gh!r}, *args])
import json, subprocess
with open(os.path.join(d, "gh.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
with open(os.path.join(d, "gh-tokens.log"), "a", encoding="utf-8") as f:
    f.write(os.environ.get("GH_TOKEN", "") + "\n")

def answer(name, default):
    p = os.path.join(d, "answers", name)
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else default

head = subprocess.run(["git", "--git-dir", {origin!r}, "rev-parse", "feat/t"], capture_output=True, text=True,
                      encoding="utf-8")
print(json.dumps({{"state": answer("pr-state", "MERGED"), "headRefOid": head.stdout.strip(),
                  "baseRefName": answer("pr-base", "main")}}))
'''

TICKET = "# 01: Ship JSON things\nStatus: building\nIssue: #7, #8\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-{nn}: task {nn}\nStatus: done\nTicket: 01\nBlocked by: none\nWrites: x\nCommit: {pr:07d}{nn}{zeros}\n"
GATE = ".review-gate.yml"
REVIEWS = "api-repos__owner___repo__pulls_{n}_reviews"


class Base(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        clone = kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)), encoding="utf-8")
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.branch_from_main("feat/t")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.ticket.write_text(TICKET, encoding="utf-8")
        (kit.run_dir / "tasks" / "01-01-a.md").write_text(TASK.format(nn="01", pr=5, zeros="0" * 31), encoding="utf-8")
        (kit.run_dir / "tasks" / "01-02-b.md").write_text(TASK.format(nn="02", pr=6, zeros="0" * 31), encoding="utf-8")
        # The Checks `ship_ticket.py merge` runs on the ticket head before a merge into a non-default base.
        (kit.run_dir / "map.md").write_text("# Map\n\n## Checks\n\n```\ntrue\n```\n", encoding="utf-8")

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        if line.startswith("FAIL "):
            self.assertRegex(line, r" \(log: .+\)$")
            self.assertTrue(Path(line.rsplit("(log: ", 1)[1][:-1]).is_file(), line)
        return line

    def calls(self, *prefix):
        return [c for c in self.kit.gh_calls() if c[:len(prefix)] == list(prefix)]

    def head(self):
        return self.kit.git("--git-dir", str(self.kit.origin), "rev-parse", "feat/t")

    def commit_to_base(self, path, text):
        """Commits `path` on `main` and moves `feat/t` along, as a file the repo had before the ticket started."""
        clone = self.kit.clone
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_text(text, encoding="utf-8")
        self.kit.git("add", path, cwd=clone)
        self.kit.git("commit", "-qm", f"add {path}", cwd=clone)
        self.kit.git("push", "-q", "origin", "main", "main:feat/t", cwd=clone)
        self.kit.git("fetch", "-q", "origin", cwd=clone)

    def set_setting(self, key, value):
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "set", "build-epic", key, value, "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)


class PostReviewTest(Base):
    def post(self, *args, cwd="/"):
        return self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, *args, cwd=cwd))

    def payload(self):
        call = self.calls("api")[-1]
        return json.loads(Path(call[call.index("--input") + 1]).read_text(encoding="utf-8"))

    def test_the_payload_is_a_comment_on_the_prs_head_and_never_an_approval(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n", encoding="utf-8")
        self.post("https://github.com/o/r/pull/12", report)
        payload = self.payload()
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], self.head())
        call = self.calls("api")[-1]
        self.assertEqual(call[:4], ["api", "repos/{owner}/{repo}/pulls/12/reviews", "--method", "POST"])
        self.assertNotIn("APPROVE", json.dumps(self.kit.gh_calls()))

    def test_an_empty_report_is_refused_and_nothing_posts(self):
        report = self.kit.tmp / "r.md"
        report.write_text(" \n\n", encoding="utf-8")
        line = self.post("#12", report)
        self.assertRegex(line, r"^FAIL PR12 empty-review \(log: ")
        self.assertEqual(self.calls("api"), [])

    def test_a_missing_report_is_refused(self):
        line = self.post("12", self.kit.tmp / "nope.md")
        self.assertRegex(line, r"^FAIL PR12 empty-review \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_value_naming_no_pr_is_refused(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n", encoding="utf-8")
        self.assertRegex(self.post("latest", report), r"^FAIL latest bad-pr: latest \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_pr_view_without_a_head_fails_and_nothing_posts(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n", encoding="utf-8")
        (self.kit.bin / "gh-kit").replace(self.kit.bin / "gh")  # the plain stub, so `pr view` answers what is set
        for out, rc in (("", 1), ("not json", 0), ("{}", 0)):
            with self.subTest(out=out, rc=rc):
                self.kit.answer("pr-view", out=out, rc=rc)
                self.assertRegex(self.post("12", report), r"^FAIL PR12 pr-view \(log: ")
        self.assertEqual(self.calls("api"), [])

    def test_a_report_that_is_not_utf8_is_refused_and_nothing_posts(self):
        report = self.kit.tmp / "r.md"
        report.write_bytes(b"caf\xe9\n")
        self.assertRegex(self.post("12", report), r"^FAIL PR12 bad-report \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_failed_post_fails(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n", encoding="utf-8")
        self.kit.answer(REVIEWS.format(n=12), err="HTTP 422: Unprocessable", rc=1)
        self.assertRegex(self.post("12", report), r"^FAIL PR12 post \(log: ")


class ShipOpenTest(Base):
    def ship(self, action):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", action))

    def test_opens_the_ticket_pr_with_its_body_and_records_it(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.ship("open"), "OK 01 open pr=30")
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/30")
        create = self.calls("pr", "create")[0]
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--title") + 1], "feat: Ship JSON things")
        self.assertNotIn("--draft", create)
        body = Path(create[create.index("--body-file") + 1]).read_text(encoding="utf-8")
        self.assertIn("01-01 task 01: merged in `", body)
        self.assertIn("Closes #7\nCloses #8", body)
        self.assertTrue((self.kit.run_dir / "worktrees" / "01").is_dir())
        self.assertEqual(self.calls("pr", "ready"), [])

    def test_open_pushes_the_local_ticket_branch_and_leaves_its_worktree_detached(self):
        clone = self.kit.clone
        self.kit.git("branch", "--no-track", "feat/t", "origin/feat/t", cwd=clone)
        wt = self.kit.tmp / "merge-wt"
        self.kit.git("worktree", "add", "-q", "--detach", str(wt), "feat/t", cwd=clone)
        (wt / "x.txt").write_text("x\n", encoding="utf-8")
        self.kit.git("add", "x.txt", cwd=wt)
        self.kit.git("commit", "-qm", "task", cwd=wt)
        self.kit.git("update-ref", "refs/heads/feat/t", self.kit.git("rev-parse", "HEAD", cwd=wt), cwd=clone)
        tip = self.kit.git("rev-parse", "feat/t", cwd=clone)
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.ship("open"), "OK 01 open pr=30")
        self.assertEqual(self.head(), tip)
        ticket_wt = self.kit.run_dir / "worktrees" / "01"
        self.assertEqual(self.kit.git("rev-parse", "HEAD", cwd=ticket_wt), tip)
        self.assertEqual(self.kit.git("branch", "--show-current", cwd=ticket_wt), "")

    def test_a_rerun_edits_the_recorded_ticket_pr_with_the_body(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        edit = self.calls("pr", "edit")[0]
        self.assertEqual(edit[:3], ["pr", "edit", "20"])
        self.assertIn("Closes #7", Path(edit[edit.index("--body-file") + 1]).read_text(encoding="utf-8"))
        self.assertEqual(self.calls("pr", "ready"), [])
        self.assertEqual(self.calls("pr", "create"), [])
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/20")

    def test_edits_the_open_pr_from_the_ticket_branch_and_records_it(self):
        self.kit.answer("pr-list", out=json.dumps([{"number": 21, "url": "https://github.com/o/r/pull/21"}]))
        self.assertEqual(self.ship("open"), "OK 01 open pr=21")
        self.assertEqual(self.calls("pr", "list")[0][:6], ["pr", "list", "--head", "feat/t", "--base", "main"])
        self.assertEqual(self.calls("pr", "edit")[0][:3], ["pr", "edit", "21"])
        self.assertEqual(self.calls("pr", "create"), [])
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/21")

    def test_opens_against_the_base_decisions_md_names(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: `integ`\n", encoding="utf-8")
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.ship("open"), "OK 01 open pr=30")
        self.assertEqual(self.calls("pr", "list")[0][4:6], ["--base", "integ"])
        create = self.calls("pr", "create")[0]
        self.assertEqual(create[create.index("--base") + 1], "integ")

    def test_an_unreadable_pr_list_or_a_failed_edit_fails(self):
        self.kit.answer("pr-list", out="not json")
        self.assertRegex(self.ship("open"), r"^FAIL 01 pr-list \(log: ")
        self.kit.answer("pr-list", out=json.dumps([{"number": 21}]))
        self.kit.answer("pr-edit", err="HTTP 422", rc=1)
        self.assertRegex(self.ship("open"), r"^FAIL 01 pr-edit \(log: ")
        self.assertIsNone(runfolder.get(self.ticket, "PR"))

    def test_an_unknown_action_is_a_usage_failure(self):
        self.assertRegex(self.ship("land"), r"^FAIL 01 usage: ship_ticket.py ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_failed_create_fails(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", err="HTTP 422", rc=1)
        self.assertRegex(self.ship("open"), r"^FAIL 01 pr-create \(log: ")
        self.assertIsNone(runfolder.get(self.ticket, "PR"))


class ShipMergeTest(Base):
    def setUp(self):
        super().setUp()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.kit.answer("pr-list", out="[]")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        self.wt = self.kit.run_dir / "worktrees" / "01"

    def ship(self, action):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", action))

    def rule(self, script):
        """Sets the merge rule to a script printing and exiting as `script` says."""
        path = self.kit.tmp / "merge-rule.sh"
        path.write_text(f"#!/usr/bin/env bash\n{script}\n", encoding="utf-8")
        path.chmod(0o755)
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "set", "build-epic", "merge_command",
                            f"{path} {{pr}}", "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_merges_closes_the_issues_and_removes_the_worktree(self):
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.calls("pr", "merge"),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", self.head()]])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "merged")
        self.assertEqual([c[:3] for c in self.calls("issue", "close")], [["issue", "close", "7"], ["issue", "close", "8"]])
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.kit.git("branch", "--list", "feat/t", cwd=self.kit.clone), "")

    def test_a_pr_into_the_default_branch_while_decisions_md_names_another_base_closes_nothing(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: integ\n", encoding="utf-8")
        self.assertRegex(self.ship("merge"), r"^FAIL 01 wrong-base main \(log: ")  # test_base_guard.py covers the guard
        self.assertEqual(self.calls("issue", "close"), [])

    def test_without_a_merge_rule_a_gated_repo_refuses_to_merge_into_the_default_branch(self):
        self.commit_to_base(".review-gate.yml", "default_tier: medium\n")
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-merge-rule \(log: ")
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_without_a_merge_rule_a_gated_repo_still_merges_into_another_base(self):
        self.commit_to_base(".review-gate.yml", "default_tier: medium\n")
        self.kit.git("branch", "-f", "--no-track", "feat/t", "origin/feat/t", cwd=self.kit.clone)  # as if merged here
        local = self.kit.git("rev-parse", "refs/heads/feat/t", cwd=self.kit.clone)
        (self.kit.run_dir / "logs" / "PR20-review.json").write_text(json.dumps({"commit_id": local}), encoding="utf-8")
        self.kit.branch_from_main("integ")
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: integ\n", encoding="utf-8")
        (self.kit.answers / "pr-base").write_text("integ", encoding="utf-8")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(len(self.calls("pr", "merge")), 1)

    def test_a_gh_merge_naming_a_required_review_fails_and_is_never_in_review(self):
        (self.kit.answers / "pr-state").write_text("OPEN", encoding="utf-8")
        self.kit.answer("pr-merge", err="X Pull request #20 is not mergeable: At least 1 approving review is required.\n",
                        rc=1)
        self.assertRegex(self.ship("merge"), r"^FAIL 01 merge \(log: ")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_rule_waiting_on_a_human_approval_leaves_it_in_review(self):
        (self.kit.answers / "pr-state").write_text("OPEN", encoding="utf-8")
        self.rule('echo "PR #$1: agent-pr-gate says blocked (exit 1), not merging"\n'
                  'echo "  - High-tier path needs an approval from a code owner."\nexit 1')
        self.assertEqual(self.ship("merge"), "OK 01 in-review pr=20")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "in-review")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertFalse(self.wt.exists())

    def test_any_other_refusal_fails(self):
        (self.kit.answers / "pr-state").write_text("OPEN", encoding="utf-8")
        self.rule('echo "PR #$1: agent-pr-gate says blocked (exit 1), not merging"\necho "  - CI failed."\nexit 1')
        self.assertRegex(self.ship("merge"), r"^FAIL 01 merge \(log: ")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertTrue(self.wt.is_dir())

    def test_without_a_pr_it_fails(self):
        (self.kit.run_dir / "tickets" / "01-t.md").write_text(TICKET, encoding="utf-8")
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-pr \(log: ")


GUARD = "# Rules\n\n```agent-merge-guard\ntests path tests/**\nconftest path **/conftest.py\n```\n"


class ShipGuardTest(Base):
    """The base's merge guard (`steplib/guard.py`) checks the ticket's diff before anything is pushed or merged: a
    ticket adding `tests/conftest.py` with only `tests` waived."""

    def setUp(self):
        super().setUp()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.commit_to_base("AGENTS.md", GUARD)
        self.commit_to_base(GATE, "default_tier: medium\n")
        self.kit.branch_from_main("integ")
        clone = self.kit.clone
        (clone / "tests").mkdir()
        (clone / "tests" / "conftest.py").write_text("import os\n", encoding="utf-8")
        self.kit.git("add", "tests/conftest.py", cwd=clone)
        self.kit.git("commit", "-qm", "conftest", cwd=clone)
        self.kit.git("push", "-q", "origin", "HEAD:feat/t", cwd=clone)
        self.kit.git("reset", "-q", "--hard", "origin/main", cwd=clone)
        self.waive("tests")

    def waive(self, clause, base=None):
        d = decisions.load(self.kit.run_dir) or decisions.Decisions()
        d.authorizations.append(decisions.Authorization(clause, "yes", "Rui: sure"))
        if base:
            d.headers["base"] = base
        decisions.save(self.kit.run_dir, d)

    def ship(self, *args):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge", *args))

    def test_a_guarded_ticket_into_the_default_branch_fails_guarded_before_no_merge_rule(self):
        self.assertRegex(self.ship(), r"^FAIL 01 guarded tests/conftest.py:1 conftest \(log: ")
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")
        self.waive("conftest")
        self.assertRegex(self.ship(), r"^FAIL 01 no-merge-rule \(log: ")
        self.set_setting("merge_command", "true {pr}")
        self.assertEqual(self.ship(), "OK 01 merged pr=20")

    def test_a_guarded_ticket_into_another_base_runs_no_checks_and_pushes_nothing(self):
        self.waive("tests", base="integ")
        (self.kit.answers / "pr-base").write_text("integ", encoding="utf-8")
        ran = self.kit.tmp / "checks-ran"
        (self.kit.run_dir / "map.md").write_text(f"# Map\n\n## Checks\n\n```\ntouch {ran}\n```\n", encoding="utf-8")
        before = self.head()
        self.assertRegex(self.ship("--reviewed", before), r"^FAIL 01 guarded tests/conftest.py:1 conftest \(log: ")
        self.assertEqual(self.head(), before)
        self.assertFalse(ran.exists())
        self.assertEqual(self.calls("pr", "merge"), [])

    def test_an_unreadable_rule_on_the_default_branch_fails_bad_rule_and_merges_nothing(self):
        clone = self.kit.clone
        (clone / "AGENTS.md").write_text("# Rules\n\n```agent-merge-guard\nconftest path\n```\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "break a rule", cwd=clone)
        self.kit.git("push", "-q", "origin", "main", cwd=clone)
        self.assertRegex(self.ship(), r"^FAIL 01 bad-rule: line 4: conftest path \(log: ")
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_ticket_into_an_integration_branch_that_lost_the_block_is_judged_by_the_default_branch(self):
        # An earlier ticket merged into `integ` with its block deletion waived; this ticket forks from there.
        clone = self.kit.clone
        self.kit.git("checkout", "-q", "-B", "integ", "origin/integ", cwd=clone)
        (clone / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        self.kit.git("commit", "-qam", "drop the rules", cwd=clone)
        (clone / "tests").mkdir()
        (clone / "tests" / "conftest.py").write_text("import os\n", encoding="utf-8")
        self.kit.git("add", "tests/conftest.py", cwd=clone)
        self.kit.git("commit", "-qm", "conftest", cwd=clone)
        self.kit.git("push", "-q", "origin", "HEAD~1:integ", "+HEAD:feat/t", cwd=clone)
        self.kit.git("checkout", "-q", "main", cwd=clone)
        self.waive("tests", base="integ")
        (self.kit.answers / "pr-base").write_text("integ", encoding="utf-8")
        before = self.head()
        self.assertRegex(self.ship("--reviewed", before), r"^FAIL 01 guarded tests/conftest.py:1 conftest \(log: ")
        self.assertEqual(self.calls("pr", "merge"), [])


class IdentityTest(Base):
    """The run's identity, decided by `init_run.py` from the `identity_command` Setting, mints a token per gh call."""

    def token_command(self):
        """A command printing `tok<n>` on its n-th run, counting its runs in `tokens.count`."""
        path = self.kit.tmp / "token.sh"
        count = self.kit.tmp / "tokens.count"
        path.write_text(f'#!/usr/bin/env bash\nn=$(( $(cat "{count}" 2>/dev/null || echo 0) + 1 ))\n'
                        f'echo "$n" >"{count}"\necho "tok$n"\n', encoding="utf-8")
        path.chmod(0o755)
        return path

    def init(self):
        p = self.kit.step(SCRIPTS / "init_run.py", self.kit.run_dir, self.kit.clone)
        self.assertEqual(p.returncode, 0, p.stdout)
        return p.stdout

    def test_every_gh_call_gets_a_fresh_token_from_the_identity_command(self):
        self.set_setting("identity_command", str(self.token_command()))
        self.init()
        self.assertEqual(runfolder.get(self.kit.run_dir / "run.md", "Identity"), str(self.kit.tmp / "token.sh"))
        self.kit.answer("pr-list", err="HTTP 502: Bad Gateway", rc=1, n=1)
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open")), "OK 01 open pr=30")
        tokens = self.kit.gh_tokens()
        self.assertEqual(len(tokens), 3)
        self.assertEqual(len(set(tokens)), 3, tokens)
        self.assertTrue(all(t.startswith("tok") for t in tokens), tokens)
        log = (self.kit.run_dir / "logs" / "01-ship_ticket.log").read_text(encoding="utf-8")
        self.assertNotIn("tok1", log)

    def test_the_merge_rule_runs_as_the_identity(self):
        self.set_setting("identity_command", str(self.token_command()))
        self.init()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        seen = self.kit.tmp / "rule-token"
        rule = self.kit.tmp / "merge-rule.sh"
        rule.write_text(f'#!/usr/bin/env bash\necho "$GH_TOKEN" >"{seen}"\n', encoding="utf-8")
        rule.chmod(0o755)
        self.set_setting("merge_command", f"{rule} {{pr}}")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge")), "OK 01 merged pr=20")
        self.assertRegex(seen.read_text(encoding="utf-8").strip(), r"^tok\d+$")

    def test_a_failing_identity_command_fails_the_step_and_calls_no_gh(self):
        bad = self.kit.tmp / "bad.sh"
        bad.write_text("#!/usr/bin/env bash\necho 'no key' >&2\nexit 1\n", encoding="utf-8")
        bad.chmod(0o755)
        self.set_setting("identity_command", str(bad))
        self.init()
        self.assertRegex(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open")),
                         r"^FAIL 01 identity-token \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_with_no_identity_named_gh_runs_as_the_current_login(self):
        self.init()
        self.assertEqual(runfolder.get(self.kit.run_dir / "run.md", "Identity"), "login")
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", out="https://github.com/o/r/pull/30\n")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open")), "OK 01 open pr=30")
        self.assertEqual(set(self.kit.gh_tokens()), {""})


class ChecksReportTest(unittest.TestCase):
    def test_saved_suite_results_render_in_the_ticket_pr_body(self):
        with Kit() as kit:
            (kit.run_dir / "tickets").mkdir()
            (kit.run_dir / "tasks").mkdir()
            ticket = kit.run_dir / "tickets" / "01-t.md"
            ticket.write_text(TICKET, encoding="utf-8")
            task = kit.run_dir / "tasks" / "01-01-a.md"
            task.write_text(TASK.format(nn="01", pr=5, zeros="0" * 31), encoding="utf-8")
            with open(kit.tmp / "log", "w", encoding="utf-8") as log:
                suites = checks.run_all(["true", "exit 3"], kit.tmp, log)
            checks.save(kit.run_dir, "01-01", suites)
            body = prbody.section(prbody.ticket_body(kit.run_dir, ticket), "Testing")
            self.assertRegex(body, r"- `true`: passed in [\d.]+s\n  - `exit 3`: failed in [\d.]+s")


if __name__ == "__main__":
    unittest.main()
