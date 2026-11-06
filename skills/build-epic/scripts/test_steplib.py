"""Tests for the step-script core (`steplib`), `init_run.py` and `testkit.py`."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, ci, core, gh, gitops, runfolder  # noqa: E402
from testkit import SCRIPTS, Kit, git, skip_without_zsh, zsh  # noqa: E402

INIT_RUN = SCRIPTS / "init_run.py"

PROBE = f'''import sys
sys.path.insert(0, {str(SCRIPTS)!r})
from steplib import core

def step(ctx):
    mode = ctx.args[0] if ctx.args else "ok"
    if mode == "ok":
        print("noise from the step")
        r = ctx.run(["sh", "-c", "echo out-text; echo err-text >&2; exit 3"])
        return "done", "rc=%d" % r.rc
    if mode == "boom":
        raise RuntimeError("kaboom")
    if mode == "fail":
        raise core.Fail("conflict")

core.main("probe", step)
'''


class KitCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.probe = self.kit.tmp / "probe.py"
        self.probe.write_text(PROBE)

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        return p.stdout.rstrip("\n")

    def log_of(self, line):
        self.assertRegex(line, r"\(log: .+\)$")
        path = Path(line.rsplit("(log: ", 1)[1][:-1])
        self.assertTrue(path.is_file(), path)
        return path


class MainTest(KitCase):
    def test_ok_prints_one_line_and_sends_subprocess_output_to_the_log(self):
        p = self.kit.step(self.probe, self.kit.run_dir, "01-02")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.one_line(p), "OK 01-02 done rc=3")
        log = (self.kit.run_dir / "logs" / "01-02-probe.log").read_text()
        for text in ("out-text", "err-text", "noise from the step", "[rc 3]"):
            self.assertIn(text, log)
        self.assertNotIn("text", p.stderr)

    def test_uncaught_exception_is_one_fail_error_line_and_exit_1(self):
        p = self.kit.step(self.probe, self.kit.run_dir, "01", "boom")
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith("FAIL 01 error (log: "), line)
        self.assertEqual(self.log_of(line), self.kit.run_dir / "logs" / "01-probe.log")
        self.assertIn("RuntimeError: kaboom", self.log_of(line).read_text())

    def test_fail_prints_its_reason_and_the_log_and_exits_1(self):
        p = self.kit.step(self.probe, self.kit.run_dir, "01", "fail")
        self.assertEqual(p.returncode, 1)
        line = self.one_line(p)
        self.assertTrue(line.startswith("FAIL 01 conflict (log: "), line)
        self.log_of(line)

    def test_missing_run_folder_fails_in_one_line(self):
        p = self.kit.step(self.probe, self.kit.tmp / "nope", "01")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.one_line(p).startswith("FAIL 01 no-run-folder"))


class RunTest(unittest.TestCase):
    def test_exit_code_survives_a_command_writing_to_stderr(self):
        # Bug 3: exit codes lost when stderr is redirected.
        log = io.StringIO()
        res = core.run(["sh", "-c", "echo oops >&2; exit 3"], log)
        self.assertEqual(res.rc, 3)
        self.assertEqual(res.err, "oops\n")
        self.assertEqual(res.out, "")
        self.assertIn("oops", log.getvalue())

    def test_a_shell_string_is_refused(self):
        with self.assertRaises(TypeError):
            core.run("echo hi; exit 3", io.StringIO())

    def test_a_missing_command_is_rc_127_not_an_exception(self):
        self.assertEqual(core.run(["no-such-command-steplib"], io.StringIO()).rc, 127)


class RunFolderPathTest(KitCase):
    """Bug 8: a script that worked only from inside the repo."""

    def setUp(self):
        super().setUp()
        self.kit.make_origin()

    def test_init_run_records_repo_default_and_remote(self):
        p = self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.one_line(p),
                         f"OK run initialised repo={self.kit.clone} default=main remote={self.kit.origin} identity=login")
        self.assertEqual(runfolder.run_info(self.kit.run_dir),
                         {"Repo": str(self.kit.clone), "Default": "main", "Remote": str(self.kit.origin)})
        self.assertEqual(runfolder.get(self.kit.run_dir / "run.md", "Identity"), "login")

    def test_init_run_records_the_epic_given_with_epic(self):
        p = self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone, "--epic", "#181")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(runfolder.get(self.kit.run_dir / "run.md", "Epic"), "#181")

    def test_from_root_from_a_linked_run_and_from_a_relative_run_give_the_same_result(self):
        linked = self.kit.link(self.kit.run_dir, "run-link")
        runs = [
            self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone, cwd="/"),
            self.kit.step(INIT_RUN, linked, self.kit.clone, cwd=self.kit.home),
            self.kit.step(INIT_RUN, "run", "clone", cwd=self.kit.tmp),
            self.kit.step(INIT_RUN, "../run-link", "../clone", cwd=self.kit.home),
        ]
        lines = {self.one_line(p) for p in runs}
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines.pop().startswith(f"OK run initialised repo={self.kit.clone} "))
        self.assertEqual(sorted(os.listdir(self.kit.tmp / "home")), [".gitconfig"])
        self.assertTrue((self.kit.run_dir / "logs" / "run-init_run.log").is_file())

    def test_fail_line_names_the_resolved_log_path_when_run_through_a_link(self):
        linked = self.kit.link(self.kit.run_dir, "run-link")
        line = self.one_line(self.kit.step(INIT_RUN, linked, self.kit.tmp / "home"))
        self.assertTrue(line.startswith("FAIL run not-a-repo"), line)
        self.assertEqual(self.log_of(line), self.kit.run_dir / "logs" / "run-init_run.log")

    def test_a_linked_script_run_with_python_I_finds_steplib(self):
        script = self.kit.link(INIT_RUN, "init-link.py")
        p = self.kit.step(script, self.kit.run_dir, self.kit.clone, cwd="/")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.one_line(p).startswith("OK run initialised"))

    def test_default_branch_is_read_from_the_remote_when_origin_head_is_unset(self):
        self.kit.git("remote", "set-head", "origin", "-d", cwd=self.kit.clone)
        p = self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone)
        self.assertIn(" default=main ", self.one_line(p))

    def test_a_repo_without_origin_or_any_default_branch_fails_writing_no_run_md(self):
        self.kit.git("remote", "set-head", "origin", "-d", cwd=self.kit.clone)
        self.kit.git("--git-dir", str(self.kit.origin), "symbolic-ref", "HEAD", "refs/heads/gone")
        line = self.one_line(self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone))
        self.assertTrue(line.startswith("FAIL run no-default-branch (log: "), line)
        self.kit.git("remote", "remove", "origin", cwd=self.kit.clone)
        line = self.one_line(self.kit.step(INIT_RUN, self.kit.run_dir, self.kit.clone))
        self.assertTrue(line.startswith("FAIL run no-origin (log: "), line)
        self.assertFalse((self.kit.run_dir / "run.md").exists())


class OneIdPerCallTest(KitCase):
    """Bug 7: zsh does not word-split, so a loop over `$ids` ran once with every id at once."""

    def logs(self):
        d = self.kit.run_dir / "logs"
        return sorted(os.listdir(d)) if d.exists() else []

    def test_an_unsplit_id_list_is_refused_and_nothing_acts(self):
        p = self.kit.shell("bash", f'ids="01 02"; python3 -I "{self.probe}" "{self.kit.run_dir}" "$ids"')
        self.assertEqual(p.returncode, 2)
        self.assertTrue(self.one_line(p).startswith("FAIL 01 bad-id"), p.stdout)
        self.assertEqual(self.logs(), [])

    @skip_without_zsh
    def test_zsh_loop_over_ids_acts_on_each_id_alone(self):
        cmd = (f'ids="01 02 03"; for id in ${{=ids}}; do python3 -I "{self.probe}" "{self.kit.run_dir}" $id; done;'
               f' for id in $ids; do python3 -I "{self.probe}" "{self.kit.run_dir}" $id; done')
        p = self.kit.shell(zsh, cmd)
        self.assertEqual(p.stdout.splitlines()[:3], ["OK 01 done rc=3", "OK 02 done rc=3", "OK 03 done rc=3"])
        self.assertTrue(p.stdout.splitlines()[3].startswith("FAIL 01 bad-id"), p.stdout)
        self.assertEqual(len(p.stdout.splitlines()), 4)
        self.assertEqual(self.logs(), ["01-probe.log", "02-probe.log", "03-probe.log"])


class GhRetryTest(KitCase):
    def setUp(self):
        super().setUp()
        self.log = io.StringIO()
        patcher = mock.patch.dict(os.environ, BUILD_EPIC_RETRY_SLEEP="0")
        patcher.start()
        self.addCleanup(patcher.stop)

    def gh(self, *args):
        return gh.gh(list(args), self.log, env=self.kit.env)

    def test_http_5xx_is_retried_until_it_succeeds(self):
        for n in (1, 2):
            self.kit.answer("pr-view", err="HTTP 502: Bad Gateway (https://api.github.com/graphql)\n", rc=1, n=n)
        self.kit.answer("pr-view", out='{"number": 7}\n')
        res = self.gh("pr", "view", "7")
        self.assertEqual((res.rc, res.out), (0, '{"number": 7}\n'))
        self.assertEqual(self.kit.gh_calls(), [["pr", "view", "7"]] * 3)

    def test_connection_reset_and_graphql_something_went_wrong_are_retried(self):
        self.kit.answer("api-graphql", err="read tcp: connection reset by peer\n", rc=1, n=1)
        self.kit.answer("api-graphql", err="GraphQL: Something went wrong while executing your query.\n", rc=1, n=2)
        self.assertEqual(self.gh("api", "graphql").rc, 0)
        self.assertEqual(len(self.kit.gh_calls()), 3)

    def test_a_lasting_5xx_stops_after_five_tries_with_its_exit_code(self):
        self.kit.answer("pr-list", err="HTTP 503: Service Unavailable\n", rc=1)
        self.assertEqual(self.gh("pr", "list").rc, 1)
        self.assertEqual(len(self.kit.gh_calls()), 5)

    def test_waits_are_2_4_8_16_seconds(self):
        self.kit.answer("pr-list", err="HTTP 500\n", rc=1)
        with mock.patch.dict(os.environ, BUILD_EPIC_RETRY_SLEEP="1"), mock.patch.object(gh.time, "sleep") as sleep:
            self.gh("pr", "list")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8, 16])

    def test_permanent_failures_fail_at_once(self):
        cases = {
            "4xx": "HTTP 422: Validation Failed (https://api.github.com/repos/o/r/pulls)\n",
            "conflict": "X Pull request o/r#7 is not mergeable: the merge commit cannot be cleanly created.\n",
            "merge conflict": "GraphQL: Merge conflict (mergePullRequest) HTTP 502\n",
            "head mismatch": "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)\n",
        }
        for name, err in cases.items():
            with self.subTest(name):
                if self.kit.gh_log.exists():
                    self.kit.gh_log.unlink()
                for f in self.kit.answers.iterdir():
                    f.unlink()
                self.kit.answer("pr-merge", err=err, rc=1)
                self.assertEqual(self.gh("pr", "merge", "7", "--match-head-commit", "abc").rc, 1)
                self.assertEqual(len(self.kit.gh_calls()), 1)

    def test_git_push_is_retried_on_5xx_and_not_on_403(self):
        self.kit.make_origin()
        fake = self.kit.tmp / "fakebin"
        fake.mkdir()
        real_git = subprocess.run(["sh", "-c", "command -v git"], text=True, stdout=subprocess.PIPE).stdout.strip()
        (fake / "git").write_text(
            "#!/bin/sh\n"
            f'n=$(cat "{fake}/n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{fake}/n"\n'
            f'if [ "$n" -le "$(cat "{fake}/fails")" ]; then\n'
            f'  echo "fatal: unable to access \'x\': The requested URL returned error: $(cat "{fake}/code")" >&2\n'
            "  exit 128\nfi\n"
            f'exec "{real_git}" "$@"\n')
        (fake / "git").chmod(0o755)
        env = dict(self.kit.env, PATH=f"{fake}{os.pathsep}{self.kit.env['PATH']}")
        for code, fails, want_rc, want_calls in (("502", "2", 0, 3), ("403", "1", 128, 1)):
            with self.subTest(code):
                (fake / "n").write_text("0")
                (fake / "fails").write_text(fails)
                (fake / "code").write_text(code)
                res = gh.push(["origin", "HEAD:refs/heads/x"], self.log, cwd=self.kit.clone, env=env)
                self.assertEqual(res.rc, want_rc, res.err)
                self.assertEqual((fake / "n").read_text().strip(), str(want_calls))


class RunFolderFilesTest(KitCase):
    def setUp(self):
        super().setUp()
        run = self.kit.run_dir
        (run / "tickets").mkdir()
        (run / "tasks").mkdir()
        (run / "tickets" / "01-core.md").write_text("# 01: Core\nStatus: ready\nBranch: feat/core\n\nBody.\n")
        (run / "tickets" / "02-more.md").write_text("# 02: More\nStatus: ready\n")
        (run / "tasks" / "01-01-lib.md").write_text(
            "# 01-01: Lib\nStatus: ready\nTicket: 01\nBlocked by: 03, ticket 02\n\n## What\nStatus: not a header\n")
        (run / "tasks" / "01-10-other.md").write_text("# 01-10\nStatus: ready\n")
        (run / "tasks" / "01-tests.md").write_text("# 01-tests\nStatus: ready\n")

    def test_finds_tickets_and_tasks_by_number(self):
        run = self.kit.run_dir
        self.assertEqual(runfolder.ticket_file(run, "1").name, "01-core.md")
        self.assertEqual(runfolder.ticket_file(run, "02").name, "02-more.md")
        self.assertEqual(runfolder.task_file(run, "01-01").name, "01-01-lib.md")
        self.assertEqual(runfolder.task_file(run, "1-1").name, "01-01-lib.md")
        self.assertEqual(runfolder.task_file(run, "01-10").name, "01-10-other.md")
        self.assertEqual(runfolder.task_file(run, "01-tests").name, "01-tests.md")
        for bad in ("01-02", "03-01", "x"):
            with self.subTest(bad), self.assertRaises(core.Fail):
                runfolder.task_file(run, bad)
        with self.assertRaises(core.Fail):
            runfolder.ticket_file(run, "9")

    def test_reads_headers_and_blockers(self):
        task = runfolder.task_file(self.kit.run_dir, "01-01")
        self.assertEqual(runfolder.get(task, "Status"), "ready")
        self.assertEqual(runfolder.task_blockers(task), ({"3"}, {"2"}))

    def test_set_replaces_the_header_line_in_a_new_file(self):
        task = runfolder.task_file(self.kit.run_dir, "01-01")
        before = task.read_text()
        inode = task.stat().st_ino
        runfolder.set_header(task, "Status", "in-progress")
        self.assertEqual(task.read_text(), before.replace("Status: ready", "Status: in-progress", 1))
        self.assertNotEqual(task.stat().st_ino, inode)  # renamed into place, never written in place
        self.assertEqual([f.name for f in task.parent.iterdir() if f.name.startswith(".") and "tmp" in f.name], [])

    def test_set_adds_a_missing_header_after_the_others(self):
        ticket = runfolder.ticket_file(self.kit.run_dir, "01")
        runfolder.set_header(ticket, "PR", "12")
        self.assertEqual(ticket.read_text(), "# 01: Core\nStatus: ready\nBranch: feat/core\nPR: 12\n\nBody.\n")

    def test_concurrent_writers_keep_every_change(self):
        ticket = runfolder.ticket_file(self.kit.run_dir, "02")
        code = ("import sys; sys.path.insert(0, %r); from steplib import runfolder\n"
                "for i in range(15): runfolder.set_header(%r, 'K' + sys.argv[1] + str(i), i)" % (str(SCRIPTS), str(ticket)))
        procs = [subprocess.Popen([sys.executable, "-c", code, w]) for w in "ab"]
        self.assertEqual([p.wait() for p in procs], [0, 0])
        text = ticket.read_text()
        for w in "ab":
            for i in range(15):
                self.assertIn(f"K{w}{i}: {i}\n", text)

    def test_a_newline_in_a_value_is_refused(self):
        with self.assertRaises(ValueError):
            runfolder.set_header(runfolder.ticket_file(self.kit.run_dir, "01"), "Status", "a\nPR: 1")

    def assert_fail(self, reason, fn, *args):
        with self.assertRaises(core.Fail) as e:
            fn(*args)
        self.assertEqual(e.exception.reason, reason)

    def test_a_file_not_found_names_why(self):
        run = self.kit.run_dir
        self.assert_fail("bad-task-id: x", runfolder.task_file, run, "x")
        self.assert_fail("bad-task-id: 01", runfolder.task_file, run, "01")
        self.assert_fail("no-task", runfolder.task_file, run, "01-02")
        self.assert_fail("no-ticket", runfolder.ticket_file, run, "9")
        (run / "tasks" / "01-1-dup.md").write_text("# 01-01\n")
        (run / "tickets" / "2-dup.md").write_text("# 02\n")
        self.assert_fail("ambiguous-task: 01-01-lib.md 01-1-dup.md", runfolder.task_file, run, "1-01")
        self.assert_fail("ambiguous-ticket: 02-more.md 2-dup.md", runfolder.ticket_file, run, "2")

    def test_run_info_names_a_missing_run_md_or_its_missing_lines(self):
        run = self.kit.run_dir
        self.assert_fail("no-run-md: run init_run.py first", runfolder.run_info, run)
        (run / "run.md").write_text("Repo: /r\nDefault: main\n")
        self.assert_fail("run-md-missing: Remote", runfolder.run_info, run)


class ChecksCommandsTest(unittest.TestCase):
    def setUp(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="steplib-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.map = Path(tmp) / "map.md"

    def commands(self, text):
        self.map.write_text(text)
        return checks.commands(self.map)

    def test_every_command_in_the_checks_code_blocks_in_order(self):
        text = ("# Map\n\n## Checks\n\nLocal:\n\n```sh\n# a comment\nmake lint\n\n  make test  \n```\n\n"
                "CI only:\n\n```\nmake e2e\n```\n\n## Gaps\n\n```\nnot-a-check\n```\n")
        self.assertEqual(self.commands(text), ["make lint", "make test", "make e2e"])

    def test_a_missing_map_or_one_with_no_check_command_fails(self):
        with self.assertRaises(core.Fail) as e:
            checks.commands(self.map)
        self.assertEqual(e.exception.reason, f"no-map: {self.map}")
        for text in ("# Map\n\n## Gaps\n\n```\nmake test\n```\n", "## Checks\n\nmake test\n",
                     "## Checks\n\n```\n# only a comment\n\n```\n"):
            with self.subTest(text), self.assertRaises(core.Fail) as e:
                self.commands(text)
            self.assertTrue(e.exception.reason.startswith("no-checks: "), e.exception.reason)


class TicketChecksTest(KitCase):
    """`ci.checks`: the ticket PR's checks, read against the stub `gh` in this process."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, self.kit.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = io.StringIO()

    def checks(self):
        return ci.checks("9", self.log, self.kit.tmp)

    def test_checks_still_pending_after_the_last_poll_time_out(self):
        self.kit.answer("pr-checks", out='[{"name": "ci", "bucket": "pending", "link": ""}]', rc=8)
        with mock.patch.object(ci, "POLLS", 3), self.assertRaises(core.Fail) as e:
            self.checks()
        self.assertEqual(e.exception.reason, "ticket-ci-timeout")
        self.assertEqual(self.kit.gh_calls(), [["pr", "checks", "9", "--json", "name,bucket,link"]] * 3)
        self.assertIn("checks pending, poll 3/3", self.log.getvalue())

    def test_no_checks_reported_is_no_checks_and_unreadable_output_fails(self):
        self.kit.answer("pr-checks", err="no checks reported on the 'feat/t' branch\n", rc=1, n=1)
        self.assertEqual(self.checks(), [])
        self.kit.answer("pr-checks", out="<html>rate limited</html>", rc=1, n=2)
        with self.assertRaises(core.Fail) as e:
            self.checks()
        self.assertEqual(e.exception.reason, "ticket-ci")

class GitOpsTest(KitCase):
    def setUp(self):
        super().setUp()
        self.kit.make_origin()
        self.log = io.StringIO()
        self.g = gitops.Git(self.kit.clone, self.log)

    def test_fetch_worktree_branch_tip_tree_and_ancestry(self):
        g, kit = self.g, self.kit
        base = g.tip("HEAD")
        self.assertEqual(base, git("rev-parse", "HEAD", cwd=kit.clone))
        self.assertEqual(g.tree("HEAD"), git("rev-parse", "HEAD^{tree}", cwd=kit.clone))
        g.fetch()
        self.assertEqual(g.tip("origin/main"), base)
        wt = kit.tmp / "wt"
        g.worktree_add(wt, "origin/main", branch="task/x")
        (wt / "f").write_text("x\n")
        kit.git("add", "f", cwd=wt)
        kit.git("commit", "-qm", "x", cwd=wt)
        self.assertTrue(g.is_ancestor(base, "task/x"))
        self.assertFalse(g.is_ancestor("task/x", base))
        g.worktree_remove(wt)
        g.worktree_remove(wt)  # already gone: fine
        self.assertFalse(wt.exists())
        g.branch_delete("task/x")
        g.branch_delete("task/x")  # already gone: fine
        self.assertNotIn("task/x", git("branch", cwd=kit.clone))

    def test_a_failed_git_command_is_a_fail_with_its_output_logged(self):
        with self.assertRaises(core.Fail) as e:
            self.g.tip("no-such-ref")
        self.assertEqual(e.exception.reason, "git-rev-parse")
        self.assertIn("no-such-ref", self.log.getvalue())

    def test_git_waits_while_another_step_holds_the_repo_lock(self):
        wt, released = self.kit.tmp / "wt", self.kit.tmp / "released"
        self.g.worktree_add(wt, "HEAD", detach=True)
        code = ("import io, os, sys; sys.path.insert(0, %r); from steplib import gitops\n"
                "print('ready', flush=True)\n"
                "tip = gitops.Git(%r, io.StringIO()).tip()\n"
                "print(tip, os.path.exists(%r))" % (str(SCRIPTS), str(wt), str(released)))
        with gitops.lock(self.kit.clone):  # the main checkout's lock also covers its worktrees
            p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 text=True)
            self.assertEqual(p.stdout.readline().strip(), "ready")
            time.sleep(0.2)  # time for the child to reach the lock and wait on it
            self.assertIsNone(p.poll(), "git ran while the lock was held")
            released.touch()
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(out.split(), [self.g.tip(), "True"], "git ran while the lock was held")

if __name__ == "__main__":
    unittest.main()
