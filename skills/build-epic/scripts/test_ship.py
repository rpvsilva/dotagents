"""Tests for `post_review.py` and `ship_ticket.py` against a stub `gh`."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, prbody, runfolder  # noqa: E402
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
    os.execv({kit_gh!r}, [{kit_gh!r}, *args])
import json, subprocess
with open(os.path.join(d, "gh.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
with open(os.path.join(d, "gh-tokens.log"), "a") as f:
    f.write(os.environ.get("GH_TOKEN", "") + "\n")

def answer(name, default):
    p = os.path.join(d, "answers", name)
    return open(p).read().strip() if os.path.exists(p) else default

head = subprocess.run(["git", "--git-dir", {origin!r}, "rev-parse", "feat/t"], capture_output=True, text=True)
print(json.dumps({{"state": answer("pr-state", "MERGED"), "headRefOid": head.stdout.strip(),
                  "baseRefName": answer("pr-base", "main")}}))
'''

TEMPLATE = """## Summary

## Testing

<!-- What you ran and its result. TODO, N/A or an unchecked box counts as empty. -->

## Risk

## How to revert
"""
FILLED = """## Summary

Cuts the JSON step in two.

## Testing

tests/run.sh: all ok

## Risk

Low: scripts only.

## How to revert

Revert the squash commit.
"""
TICKET = "# 01: Ship JSON things\nStatus: building\nIssue: #7, #8\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-{nn}: task {nn}\nStatus: done\nTicket: 01\nBlocked by: none\nWrites: x\nPR: https://github.com/o/r/pull/{pr}\n"
REVIEWS = "api-repos__owner___repo__pulls_{n}_reviews"


class Base(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        clone = kit.make_origin()
        (kit.bin / "gh").rename(kit.bin / "gh-kit")
        (kit.bin / "gh").write_text(PR_VIEW.format(python=sys.executable, kit_gh=str(kit.bin / "gh-kit"),
                                                   origin=str(kit.origin)))
        (kit.bin / "gh").chmod(0o755)
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.branch_from_main("feat/t")
        kit.init_run()
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.ticket.write_text(TICKET)
        (kit.run_dir / "tasks" / "01-01-a.md").write_text(TASK.format(nn="01", pr=5))
        (kit.run_dir / "tasks" / "01-02-b.md").write_text(TASK.format(nn="02", pr=6))

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
        (clone / path).write_text(text)
        self.kit.git("add", path, cwd=clone)
        self.kit.git("commit", "-qm", f"add {path}", cwd=clone)
        self.kit.git("push", "-q", "origin", "main", "main:feat/t", cwd=clone)
        self.kit.git("fetch", "-q", "origin", cwd=clone)

    def set_setting(self, key, value):
        p = subprocess.run([str(self.kit.home / ".agents/bin/settings"), "set", "build-epic", key, value, "--machine"],
                           env=self.kit.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)


class PostReviewTest(Base):
    def post(self, *args, cwd="/"):
        return self.one_line(self.kit.step(POST_REVIEW, self.kit.run_dir, *args, cwd=cwd))

    def payload(self):
        call = self.calls("api")[-1]
        return json.loads(Path(call[call.index("--input") + 1]).read_text())

    def test_a_relative_report_path_posts_the_files_text(self):
        work = self.kit.tmp / "work"
        work.mkdir()
        (work / "report.md").write_text("Finding: the JSON parser drops keys.\n")
        self.kit.answer(REVIEWS.format(n=12), out=json.dumps({"html_url": "https://github.com/o/r/pull/12#r1"}))
        line = self.post("12", "report.md", cwd=work)
        self.assertEqual(line, "OK PR12 review https://github.com/o/r/pull/12#r1")
        self.assertEqual(self.payload()["body"], "Finding: the JSON parser drops keys.\n")

    def test_the_payload_is_a_comment_on_the_prs_head_and_never_an_approval(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n")
        comments = self.kit.tmp / "c.json"
        comments.write_text(json.dumps([{"path": "a.py", "line": 3, "body": "nit"}]))
        self.post("https://github.com/o/r/pull/12", report, "--comments", comments)
        payload = self.payload()
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], self.head())
        self.assertEqual(payload["comments"], [{"path": "a.py", "line": 3, "body": "nit"}])
        call = self.calls("api")[-1]
        self.assertEqual(call[:4], ["api", "repos/{owner}/{repo}/pulls/12/reviews", "--method", "POST"])
        self.assertNotIn("APPROVE", json.dumps(self.kit.gh_calls()))

    def test_an_empty_report_is_refused_and_nothing_posts(self):
        report = self.kit.tmp / "r.md"
        report.write_text(" \n\n")
        line = self.post("#12", report)
        self.assertRegex(line, r"^FAIL PR12 empty-review \(log: ")
        self.assertEqual(self.calls("api"), [])

    def test_a_missing_report_is_refused(self):
        line = self.post("12", self.kit.tmp / "nope.md")
        self.assertRegex(line, r"^FAIL PR12 empty-review \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_value_naming_no_pr_is_refused(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n")
        self.assertRegex(self.post("latest", report), r"^FAIL latest bad-pr: latest \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_comments_that_are_not_a_json_array_of_objects_are_refused(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n")
        for comments in ("not json", '{"path": "a.py"}', '["nit"]'):
            with self.subTest(comments):
                self.assertRegex(self.post("12", report, "--comments", comments), r"^FAIL PR12 bad-comments \(log: ")
        self.assertEqual(self.kit.gh_calls(), [])

    def test_a_failed_post_fails(self):
        report = self.kit.tmp / "r.md"
        report.write_text("ok\n")
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
        body = Path(create[create.index("--body-file") + 1]).read_text()
        self.assertIn("task 01: https://github.com/o/r/pull/5", body)
        self.assertIn("Closes #7\nCloses #8", body)
        self.assertTrue((self.kit.run_dir / "worktrees" / "01").is_dir())

    def test_marks_the_draft_ticket_pr_ready_with_the_body(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        edit = self.calls("pr", "edit")[0]
        self.assertEqual(edit[:3], ["pr", "edit", "20"])
        self.assertIn("Closes #7", Path(edit[edit.index("--body-file") + 1]).read_text())
        self.assertEqual(self.calls("pr", "ready"), [["pr", "ready", "20"]])
        self.assertEqual(self.calls("pr", "create"), [])
        self.assertEqual(runfolder.get(self.ticket, "PR"), "https://github.com/o/r/pull/20")

    def test_a_pr_already_ready_is_fine_and_any_other_ready_failure_fails(self):
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        self.kit.answer("pr-ready", err="! Pull request o/r#20 is already \"ready for review\"\n", rc=1, n=1)
        self.assertEqual(self.ship("open"), "OK 01 open pr=20")
        self.kit.answer("pr-ready", err="GraphQL: Resource not accessible by integration\n", rc=1, n=2)
        self.assertRegex(self.ship("open"), r"^FAIL 01 pr-ready \(log: ")

    def test_a_failed_create_fails(self):
        self.kit.answer("pr-list", out="[]")
        self.kit.answer("pr-create", err="HTTP 422", rc=1)
        self.assertRegex(self.ship("open"), r"^FAIL 01 pr-create \(log: ")
        self.assertIsNone(runfolder.get(self.ticket, "PR"))

    def body_file(self, text):
        path = self.kit.tmp / "body.md"
        path.write_text(text)
        return path

    def test_the_orchestrators_body_fills_the_template_and_the_script_adds_tasks_and_closes(self):
        self.commit_to_base(".github/pull_request_template.md", TEMPLATE)
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        body = self.body_file(FILLED)
        line = self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open", "--body-file", body))
        self.assertEqual(line, "OK 01 open pr=20")
        edit = self.calls("pr", "edit")[0]
        sent = Path(edit[edit.index("--body-file") + 1]).read_text()
        self.assertTrue(sent.startswith(FILLED), sent)
        self.assertIn("task 01: https://github.com/o/r/pull/5", sent)
        self.assertIn("Closes #7\nCloses #8", sent)

    def test_a_relative_body_file_is_read_from_the_callers_folder(self):
        work = self.kit.tmp / "work"
        work.mkdir()
        (work / "body.md").write_text(FILLED)
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        line = self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open", "--body-file", "body.md", cwd=work))
        self.assertEqual(line, "OK 01 open pr=20")
        edit = self.calls("pr", "edit")[0]
        self.assertIn("Cuts the JSON step", Path(edit[edit.index("--body-file") + 1]).read_text())

    def test_a_template_section_left_empty_todo_or_na_is_refused_and_nothing_is_sent(self):
        self.commit_to_base(".github/pull_request_template.md", TEMPLATE)
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        cases = {
            "no body": None,
            "empty Risk": FILLED.replace("Low: scripts only.", ""),
            "TODO Testing": FILLED.replace("tests/run.sh: all ok", "TODO"),
            "N/A revert": FILLED.replace("Revert the squash commit.", "N/A"),
            "only a comment": FILLED.replace("Low: scripts only.", "<!-- say the risk -->"),
            "an unchecked box": FILLED.replace("tests/run.sh: all ok", "- [ ] ran the tests"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                args = ["open"] if text is None else ["open", "--body-file", self.body_file(text)]
                line = self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", *args))
                self.assertRegex(line, r"^FAIL 01 empty-template-section ")
        self.assertEqual(self.calls("pr", "edit"), [])
        self.assertEqual(self.calls("pr", "ready"), [])


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
        path.write_text(f"#!/usr/bin/env bash\n{script}\n")
        path.chmod(0o755)
        p = subprocess.run([str(self.kit.home / ".agents/bin/settings"), "set", "build-epic", "merge_command",
                            f"{path} {{pr}}", "--machine"], env=self.kit.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_merges_closes_the_issues_and_removes_the_worktree(self):
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.calls("pr", "merge"),
                         [["pr", "merge", "20", "--squash", "--delete-branch", "--match-head-commit", self.head()]])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "merged")
        self.assertEqual([c[:3] for c in self.calls("issue", "close")], [["issue", "close", "7"], ["issue", "close", "8"]])
        self.assertFalse(self.wt.exists())
        self.assertEqual(self.kit.git("branch", "--list", "feat/t", cwd=self.kit.clone), "")

    def test_a_pr_into_another_base_leaves_the_issues_open_whatever_decisions_md_says(self):
        (self.kit.answers / "pr-base").write_text("integ")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_a_pr_into_the_default_branch_closes_the_issues_whatever_decisions_md_says(self):
        (self.kit.run_dir / "decisions.md").write_text("# Decisions\nbase: integ\n")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual([c[:3] for c in self.calls("issue", "close")], [["issue", "close", "7"], ["issue", "close", "8"]])

    def test_without_a_merge_rule_a_gated_repo_refuses_to_merge_into_the_default_branch(self):
        self.commit_to_base(".review-gate.yml", "default_tier: medium\n")
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-merge-rule \(log: ")
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_without_a_merge_rule_a_gated_repo_still_merges_into_another_base(self):
        self.commit_to_base(".review-gate.yml", "default_tier: medium\n")
        (self.kit.answers / "pr-base").write_text("integ")
        self.assertEqual(self.ship("merge"), "OK 01 merged pr=20")
        self.assertEqual(len(self.calls("pr", "merge")), 1)

    def test_a_gh_merge_naming_a_required_review_fails_and_is_never_in_review(self):
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.kit.answer("pr-merge", err="X Pull request #20 is not mergeable: At least 1 approving review is required.\n",
                        rc=1)
        self.assertRegex(self.ship("merge"), r"^FAIL 01 merge \(log: ")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")

    def test_a_rule_waiting_on_a_human_approval_leaves_it_in_review(self):
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.rule('echo "PR #$1: agent-pr-gate says blocked (exit 1), not merging"\n'
                  'echo "  - High-tier path needs an approval from a code owner."\nexit 1')
        self.assertEqual(self.ship("merge"), "OK 01 in-review pr=20")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "in-review")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertFalse(self.wt.exists())

    def test_any_other_refusal_fails(self):
        (self.kit.answers / "pr-state").write_text("OPEN")
        self.rule('echo "PR #$1: agent-pr-gate says blocked (exit 1), not merging"\necho "  - CI failed."\nexit 1')
        self.assertRegex(self.ship("merge"), r"^FAIL 01 merge \(log: ")
        self.assertEqual(runfolder.get(self.ticket, "Status"), "building")
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertTrue(self.wt.is_dir())

    def test_without_a_pr_it_fails(self):
        (self.kit.run_dir / "tickets" / "01-t.md").write_text(TICKET)
        self.assertRegex(self.ship("merge"), r"^FAIL 01 no-pr \(log: ")


class IdentityTest(Base):
    """The run's identity, decided by `init_run.py` from the `identity_command` Setting, mints a token per gh call."""

    def token_command(self):
        """A command printing `tok<n>` on its n-th run, counting its runs in `tokens.count`."""
        path = self.kit.tmp / "token.sh"
        count = self.kit.tmp / "tokens.count"
        path.write_text(f'#!/usr/bin/env bash\nn=$(( $(cat "{count}" 2>/dev/null || echo 0) + 1 ))\n'
                        f'echo "$n" >"{count}"\necho "tok$n"\n')
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
        log = (self.kit.run_dir / "logs" / "01-ship_ticket.log").read_text()
        self.assertNotIn("tok1", log)

    def test_the_merge_rule_runs_as_the_identity(self):
        self.set_setting("identity_command", str(self.token_command()))
        self.init()
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")
        seen = self.kit.tmp / "rule-token"
        rule = self.kit.tmp / "merge-rule.sh"
        rule.write_text(f'#!/usr/bin/env bash\necho "$GH_TOKEN" >"{seen}"\n')
        rule.chmod(0o755)
        self.set_setting("merge_command", f"{rule} {{pr}}")
        self.assertEqual(self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "merge")), "OK 01 merged pr=20")
        self.assertRegex(seen.read_text().strip(), r"^tok\d+$")

    def test_a_failing_identity_command_fails_the_step_and_calls_no_gh(self):
        bad = self.kit.tmp / "bad.sh"
        bad.write_text("#!/usr/bin/env bash\necho 'no key' >&2\nexit 1\n")
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
    def test_saved_suite_results_render_in_the_task_pr_body(self):
        with Kit() as kit:
            (kit.run_dir / "tickets").mkdir()
            (kit.run_dir / "tasks").mkdir()
            ticket = kit.run_dir / "tickets" / "01-t.md"
            ticket.write_text(TICKET)
            task = kit.run_dir / "tasks" / "01-01-a.md"
            task.write_text(TASK.format(nn="01", pr=5))
            with open(kit.tmp / "log", "w") as log:
                suites = checks.run_all(["true", "exit 3"], kit.tmp, log)
            checks.save(kit.run_dir, "01-01", suites)
            body = prbody.task_body(kit.run_dir, "01-01", task, ticket)
            self.assertRegex(body, r"- `true`: passed in [\d.]+s\n- `exit 3`: failed in [\d.]+s")


if __name__ == "__main__":
    unittest.main()
