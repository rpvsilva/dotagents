"""Tests for `steplib/guard.py` (the leave-for-human merge guard) and `guard_rules.py` (its rule printer)."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, decisions, guard  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

GUARD_RULES = SCRIPTS / "guard_rules.py"
RANGE = "origin/main...HEAD"
BLOCK = (
    "```agent-merge-guard\n"
    "process-exit added \\b(sys\\.exit|os\\._exit)\\( in pkg/**\n"
    "tests path tests/**\n"
    "conftest path **/conftest.py\n"
    "```\n"
)
AGENTS = "# Rules\n\nAgents leave these to the human:\n\n" + BLOCK


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.repo = self.kit.make_origin()
        self.kit.init_run()
        self.run_dir = self.kit.run_dir
        env = mock.patch.dict(os.environ, self.kit.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.log = open(self.kit.tmp / "guard.log", "a", encoding="utf-8")
        self.addCleanup(self.log.close)

    def base(self, files):
        """Commits `files` on `main` and pushes it, then starts the PR branch `pr` from there."""
        self.commit(files)
        self.kit.git("push", "-q", "origin", "main", cwd=self.repo)
        self.kit.git("checkout", "-qb", "pr", cwd=self.repo)

    def commit(self, files):
        """Writes each `path: text` (None deletes the path) and commits them all."""
        for name, text in files.items():
            path = self.repo / name
            if text is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        self.kit.git("add", "-A", cwd=self.repo)
        self.kit.git("commit", "-qm", "change", cwd=self.repo)

    def waive(self, clause, waived):
        d = decisions.load(self.run_dir) or decisions.Decisions()
        d.authorizations.append(decisions.Authorization(clause, waived, "Rui: sure"))
        decisions.save(self.run_dir, d)

    def check(self):
        guard.check(self.repo, RANGE, self.run_dir, self.log)

    def refused(self):
        with self.assertRaises(core.Fail) as caught:
            self.check()
        return caught.exception.reason


class CheckTest(GuardCase):
    def test_a_process_exit_added_under_package_code_is_refused_with_file_line_rule(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"pkg/run.py": "import sys\n\nsys.exit(1)\n"})
        self.assertEqual(self.refused(), "guarded pkg/run.py:3 process-exit")

    def test_an_added_rule_ignores_paths_outside_its_glob(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"tools/run.py": "import sys\nsys.exit(1)\n"})
        self.check()

    def test_line_numbers_come_from_the_hunk_headers_of_a_changed_file(self):
        self.base({"AGENTS.md": AGENTS, "pkg/a.py": "a = 1\nb = 2\nc = 3\nd = 4\n"})
        self.commit({"pkg/a.py": "a = 1\nb = 2\nc = 3\nos._exit(2)\nd = 4\nsys.exit(3)\n"})
        self.assertEqual(self.refused(), "guarded pkg/a.py:4 process-exit; pkg/a.py:6 process-exit")

    def test_a_removed_line_is_no_hit(self):
        self.base({"AGENTS.md": AGENTS, "pkg/a.py": "import sys\nsys.exit(1)\n"})
        self.commit({"pkg/a.py": "import sys\n"})
        self.check()

    def test_a_tests_change_is_refused_unless_tests_is_waived_yes(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"tests/test_a.py": "def test_a():\n    pass\n"})
        self.assertEqual(self.refused(), "guarded tests/test_a.py:1 tests")
        for answer in ("no", "ask"):
            self.waive("tests", answer)
            self.assertEqual(self.refused(), "guarded tests/test_a.py:1 tests", answer)
            decisions.path(self.run_dir).unlink()
        self.waive("Tests", "yes")
        self.assertEqual(self.refused(), "guarded tests/test_a.py:1 tests")
        decisions.path(self.run_dir).unlink()
        self.waive("tests", "yes")
        self.check()

    def test_a_conftest_change_needs_conftest_waived_as_well_as_tests(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"tests/conftest.py": "import pytest\n"})
        self.waive("tests", "yes")
        self.assertEqual(self.refused(), "guarded tests/conftest.py:1 conftest")
        self.waive("conftest", "yes")
        self.check()

    def test_a_deleted_file_is_matched_by_its_old_path(self):
        self.base({"AGENTS.md": AGENTS, "tests/test_old.py": "x = 1\n"})
        self.commit({"tests/test_old.py": None})
        self.assertEqual(self.refused(), "guarded tests/test_old.py:0 tests")

    def test_a_rename_is_matched_by_its_old_and_new_paths(self):
        self.base({"AGENTS.md": AGENTS, "tests/test_old.py": "x = 1\n"})
        self.kit.git("mv", "tests/test_old.py", "tests/test_new.py", cwd=self.repo)
        self.kit.git("commit", "-qm", "move", cwd=self.repo)
        self.assertEqual(self.refused(), "guarded tests/test_new.py:1 tests; tests/test_old.py:0 tests")

    def test_paths_git_quotes_read_back_as_written(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({'pkg/a "b"\tü.py': "sys.exit(1)\n", "pkg/c d.py": "sys.exit(1)\n"})
        self.assertEqual(self.refused(), 'guarded pkg/a "b"\tü.py:1 process-exit; pkg/c d.py:1 process-exit')

    def test_a_rule_deleted_on_the_pr_head_still_applies(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"AGENTS.md": "# Rules\n", "pkg/run.py": "sys.exit(1)\n"})
        self.assertEqual(self.refused(), "guarded pkg/run.py:1 process-exit")

    def test_a_rule_added_on_the_pr_head_does_not_apply_yet(self):
        self.base({"AGENTS.md": "# Rules\n"})
        self.commit({"AGENTS.md": AGENTS, "pkg/run.py": "sys.exit(1)\n"})
        self.check()

    def test_a_repo_with_no_block_has_no_rules(self):
        self.base({"AGENTS.md": "# Rules\n\n```sh\ntests path tests/**\n```\n"})
        self.commit({"tests/test_a.py": "x = 1\n", "pkg/run.py": "sys.exit(1)\n"})
        self.check()

    def test_the_block_in_claude_md_counts_when_agents_md_has_none(self):
        self.base({"AGENTS.md": "# Rules\n", "CLAUDE.md": AGENTS})
        self.commit({"tests/test_a.py": "x = 1\n"})
        self.assertEqual(self.refused(), "guarded tests/test_a.py:1 tests")

    def test_the_block_is_read_through_a_symlinked_agents_md(self):
        self.base({"docs/rules.md": AGENTS})
        (self.repo / "AGENTS.md").symlink_to("docs/rules.md")
        self.kit.git("add", "AGENTS.md", cwd=self.repo)
        self.kit.git("commit", "-qm", "link", cwd=self.repo)
        self.kit.git("push", "-q", "origin", "pr:main", cwd=self.repo)
        self.kit.git("fetch", "-q", "origin", cwd=self.repo)
        self.commit({"tests/test_a.py": "x = 1\n"})
        self.assertEqual(self.refused(), "guarded tests/test_a.py:1 tests")

    def test_an_unreadable_block_line_refuses(self):
        self.base({"AGENTS.md": "```agent-merge-guard\ntests tests/**\n```\n"})
        self.commit({"README.md": "changed\n"})
        self.assertEqual(self.refused(), "bad-rule: line 2: tests tests/**")

    def test_a_missing_base_refuses(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"README.md": "changed\n"})
        with self.assertRaises(core.Fail) as caught:
            guard.check(self.repo, "origin/nope...HEAD", self.run_dir, self.log)
        self.assertEqual(caught.exception.reason, "guard-no-base: origin/nope")

    def test_a_range_not_in_base_three_dots_head_form_refuses(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"pkg/run.py": "sys.exit(1)\n"})
        for range_ in ("origin/main..HEAD", "...HEAD", "HEAD"):
            with self.subTest(range_=range_), self.assertRaises(core.Fail) as caught:
                guard.check(self.repo, range_, self.run_dir, self.log)
            self.assertEqual(caught.exception.reason, f"guard-bad-range: {range_}")

    def test_a_symlinked_agents_md_pointing_outside_the_repo_has_no_rules(self):
        self.base({"docs/rules.md": AGENTS})
        # a link climbing out of the tree, even back into it, is no file: the guard reads no rules and fails nothing
        (self.repo / "AGENTS.md").symlink_to(f"../{self.repo.name}/docs/rules.md")
        self.kit.git("add", "AGENTS.md", cwd=self.repo)
        self.kit.git("commit", "-qm", "link", cwd=self.repo)
        self.kit.git("push", "-q", "origin", "pr:main", cwd=self.repo)
        self.kit.git("fetch", "-q", "origin", cwd=self.repo)
        self.commit({"tests/test_a.py": "x = 1\n"})
        self.check()


class ParseTest(unittest.TestCase):
    def rules(self, text):
        return [(r.name, r.kind, r.pattern) for r in guard.parse(text)]

    def test_each_kind_parses_with_its_pattern_as_written(self):
        self.assertEqual(self.rules(AGENTS), [
            ("process-exit", "added", "\\b(sys\\.exit|os\\._exit)\\( in pkg/**"),
            ("tests", "path", "tests/**"),
            ("conftest", "path", "**/conftest.py"),
        ])

    def test_blank_and_comment_lines_are_skipped(self):
        self.assertEqual(self.rules("```agent-merge-guard\n\n# owner files\nowners path CODEOWNERS\n```\n"),
                         [("owners", "path", "CODEOWNERS")])

    def test_a_regex_may_hold_spaces(self):
        self.assertEqual(self.rules("```agent-merge-guard\nskip added @pytest.mark.skip (reason)\n```\n"),
                         [("skip", "added", "@pytest.mark.skip (reason)")])

    def test_unreadable_lines_fail_with_their_line(self):
        for line in ("tests tests/**", "tests glob tests/**", "tests path", "tests path a b", "-x path a",
                     "bad added (", "x added a in", "dup path a\ndup path b"):
            with self.subTest(line=line), self.assertRaises(core.Fail) as caught:
                guard.parse("intro\n```agent-merge-guard\n" + line + "\n```\n")
            self.assertTrue(caught.exception.reason.startswith("bad-rule: line "), caught.exception.reason)

    def test_an_unclosed_block_fails(self):
        with self.assertRaises(core.Fail) as caught:
            guard.parse("```agent-merge-guard\ntests path tests/**\n")
        self.assertEqual(caught.exception.reason, "bad-rule: unclosed agent-merge-guard block")

    def test_globs_match_whole_paths_with_double_star_crossing_folders(self):
        cases = {
            "tests/**": (["tests/a.py", "tests/x/y.py"], ["tests", "a/tests/b.py", "testsx/a.py"]),
            "**/conftest.py": (["conftest.py", "tests/conftest.py", "a/b/conftest.py"], ["tests/xconftest.py"]),
            "pkg/*.py": (["pkg/a.py"], ["pkg/x/a.py", "pkg/a.pyc"]),
            "a/**/b": (["a/b", "a/x/b", "a/x/y/b"], ["a/xb"]),
            "f?.md": (["f1.md"], ["f12.md", "f/.md"]),
        }
        for glob, (hits, misses) in cases.items():
            rule = guard.parse(f"```agent-merge-guard\nr path {glob}\n```\n")[0]
            for path in hits:
                self.assertTrue(rule.matches_path(path), (glob, path))
            for path in misses:
                self.assertFalse(rule.matches_path(path), (glob, path))


class GuardRulesTest(GuardCase):
    def guard_rules(self, ok=True):
        p = self.kit.step(GUARD_RULES, self.run_dir)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        return p.stdout.splitlines()

    def test_prints_one_line_per_rule_from_the_base(self):
        self.base({"AGENTS.md": AGENTS})
        self.commit({"AGENTS.md": "# Rules\n"})  # the PR head's own file is never read
        self.assertEqual(self.guard_rules(), [
            "rule process-exit added \\b(sys\\.exit|os\\._exit)\\( in pkg/**",
            "rule tests path tests/**",
            "rule conftest path **/conftest.py",
        ])

    def test_reads_the_recorded_base_branch(self):
        self.base({"AGENTS.md": "# Rules\n"})
        self.commit({"AGENTS.md": AGENTS})
        self.kit.git("push", "-q", "origin", "pr:feat/integration", cwd=self.repo)
        d = decisions.Decisions(headers={"base": "feat/integration"})
        decisions.save(self.run_dir, d)
        self.assertEqual(len(self.guard_rules()), 3)

    def test_prints_nothing_without_a_block(self):
        self.base({"AGENTS.md": "# Rules\n"})
        self.assertEqual(self.guard_rules(), [])

    def test_an_unreadable_block_line_fails(self):
        self.base({"AGENTS.md": "```agent-merge-guard\ntests tests/**\n```\n"})
        self.assertEqual(self.guard_rules(ok=False), ["FAIL run bad-rule: line 2: tests tests/**"])

    def test_a_base_missing_on_origin_fails_git_fetch(self):
        decisions.save(self.run_dir, decisions.Decisions(headers={"base": "feat/nope"}))
        self.assertEqual(self.guard_rules(ok=False), ["FAIL run git-fetch"])

    def test_a_missing_run_folder_or_argument_fails(self):
        missing = self.kit.tmp / "no-run"
        p = self.kit.step(GUARD_RULES, missing)
        self.assertEqual((p.returncode, p.stdout), (1, f"FAIL run no-run-folder: {missing}\n"))
        p = self.kit.step(GUARD_RULES)
        self.assertEqual((p.returncode, p.stdout), (2, "FAIL run usage: guard_rules.py <run>\n"))


if __name__ == "__main__":
    unittest.main()
