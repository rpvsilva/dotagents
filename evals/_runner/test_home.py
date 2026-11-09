"""Tests for `home.py`: the checkout at a ref, the throwaway home, the fixture repo, the `gh` stub, the tool's env
and what a run keeps."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GOOD = HERE / "fixtures" / "cases" / "good"
sys.path.insert(0, str(HERE))

import cases  # noqa: E402
import home  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

AUTH = EvalSandbox.TOKEN_VARS


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def links(folder):
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def fake_checkout(co, install_sh):
    """A checkout holding only `install_sh` and a `parts.sh` whose closure is the parts it is given."""
    (co / "scripts").mkdir(parents=True)
    (co / "install.sh").write_text(install_sh)
    (co / "scripts" / "parts.sh").write_text('shift; printf "%s\\n" "$@"\n')
    return co


def under(path, folder):
    return Path(path).resolve().is_relative_to(Path(folder).resolve())


class AtHead(EvalSandbox):
    """One checkout of this repo's HEAD shared by the class: extracting and installing cost seconds."""

    @classmethod
    def setUpClass(cls):
        cls.co = home.checkout(REPO, "HEAD")
        cls.addClassCleanup(cls.co.close)

    def prepare(self, case="configure/minimal", **kw):
        run = home.prepare(cases.load(GOOD / case), self.co.path, "claude", **kw)
        self.addCleanup(run.close)
        return run


class Checkout(AtHead):
    def test_checkout_resolves_the_ref_to_its_sha_and_extracts_its_files(self):
        self.assertEqual(self.co.sha, git(REPO, "rev-parse", "HEAD"))
        self.assertTrue((self.co.path / "install.sh").is_file())
        self.assertFalse((self.co.path / ".git").exists())
        self.assertFalse(under(self.co.path, REPO))

    def test_checkout_refuses_an_unknown_ref(self):
        with self.assertRaises(RuntimeError):
            home.checkout(REPO, "no-such-ref-anywhere")

    def test_close_removes_the_checkout(self):
        with home.checkout(REPO, "HEAD") as co:
            self.assertTrue(co.path.is_dir())
        self.assertFalse(co.path.exists())


class Install(AtHead):
    def test_install_of_configure_for_claude_links_only_its_closure_and_no_claude_md(self):
        run = self.prepare()
        h = run.home
        self.assertEqual(links(h / ".agents" / "skills"), ["_shared", "configure"])
        self.assertEqual(links(h / ".claude" / "skills"), ["_shared", "configure"])
        self.assertFalse((h / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((h / ".claude" / "settings.json").exists())
        self.assertFalse((h / ".claude" / "agents").exists())
        for link in [*(h / ".agents" / "skills").iterdir(), *(h / ".claude" / "skills").iterdir()]:
            self.assertTrue(link.is_symlink() and under(link, self.co.path), link)
        # Nothing reached the caller's HOME.
        self.assertEqual(links(self.home / ".agents"), [])

    def test_install_refuses_no_parts_and_any_selection_pulling_in_global(self):
        for parts in ([], ["configure", "global"], ["personal"]):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                home.install(self.co.path, self.tmp / "h", "claude", parts)
        self.assertFalse((self.tmp / "h").exists())

    def test_install_refuses_a_word_that_is_not_one_tool(self):
        for word in ("all", "shared", "vim"):
            with self.subTest(word=word), self.assertRaises(ValueError):
                home.install(self.co.path, self.tmp / "h", word, ["configure"])

    def test_install_runs_the_refs_own_install_sh_with_the_throwaway_env(self):
        co = fake_checkout(self.tmp / "co", 'env >"$HOME/env"; echo "$@" >"$HOME/args"\n')
        os.environ["SOME_LEAK"] = "x"
        self.addCleanup(os.environ.pop, "SOME_LEAK", None)
        h = self.tmp / "h"
        h.mkdir()
        home.install(co, h, "codex", ["configure"])
        env = dict(line.split("=", 1) for line in (h / "env").read_text().splitlines() if "=" in line)
        self.assertEqual((h / "args").read_text(), "codex configure\n")
        self.assertEqual((env["HOME"], env["XDG_CONFIG_HOME"], env["CLAUDE_CONFIG_DIR"]),
                         (str(h), str(h / ".config"), str(h / ".claude")))
        self.assertEqual((env["DOTAGENTS_SKIP_PLUGINS"], env["DOTAGENTS_PLAIN"]), ("1", "1"))
        self.assertNotIn("SOME_LEAK", env)
        self.assertFalse(set(AUTH) & set(env))

    def test_a_failed_install_raises_an_install_error(self):
        co = fake_checkout(self.tmp / "co", "echo broken at this ref >&2; exit 3\n")
        with self.assertRaises(home.InstallError) as err:
            home.install(co, self.tmp / "h", "claude", ["configure"])
        self.assertEqual(err.exception.error, "install")
        self.assertIn("broken at this ref", str(err.exception))

    def test_prepare_leaves_nothing_behind_when_install_fails(self):
        co = fake_checkout(self.tmp / "co", "exit 1\n")
        before = set(Path(home.tempfile.gettempdir()).glob("evals-run-*"))
        with self.assertRaises(home.InstallError):
            home.prepare(cases.load(GOOD / "configure" / "minimal"), co, "claude")
        self.assertEqual(set(Path(home.tempfile.gettempdir()).glob("evals-run-*")), before)


class Work(AtHead):
    def test_fixture_is_a_fresh_repo_with_one_commit_outside_both_checkouts(self):
        run = self.prepare()
        self.assertFalse(under(run.work, REPO) or under(run.work, self.co.path))
        self.assertEqual(git(run.work, "rev-list", "--count", "HEAD"), "1")
        self.assertEqual(git(run.work, "status", "--porcelain"), "")
        fixture = GOOD / "configure" / "minimal" / "fixture"
        self.assertEqual(sorted(git(run.work, "ls-files").splitlines()),
                         sorted(p.relative_to(fixture).as_posix() for p in fixture.rglob("*") if p.is_file()))

    def test_case_settings_are_committed_as_the_repo_settings_file(self):
        run = self.prepare("configure/full")
        self.assertEqual(json.loads((run.work / ".agents" / "settings.json").read_text()),
                         {"build-epic": {"max_parallel": 2}})
        self.assertIn(".agents/settings.json", git(run.work, "ls-files").splitlines())
        self.assertEqual(git(run.work, "status", "--porcelain"), "")

    def test_input_is_a_copy_of_the_cases_and_scratch_is_empty(self):
        run = self.prepare("configure/full")
        src = GOOD / "configure" / "full" / "input"
        self.assertEqual(sorted(p.name for p in run.input.iterdir()), sorted(p.name for p in src.iterdir()))
        self.assertEqual(list(run.scratch.iterdir()), [])
        self.assertEqual(list(self.prepare().input.iterdir()), [])


class Stub(AtHead):
    def test_gh_stub_first_on_path_records_the_call_and_fails(self):
        run = self.prepare()
        out = subprocess.run(["sh", "-c", "gh pr create --title 'a b'"], env=run.env(AUTH), cwd=run.work,
                             capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(run.gh_calls.read_text(), "pr create --title a b\n")

    def test_gh_calls_starts_empty(self):
        self.assertEqual(self.prepare().gh_calls.read_text(), "")


class ToolEnv(EvalSandbox):
    def test_tool_env_holds_only_the_allow_list_and_the_named_auth_vars(self):
        os.environ.update(SOME_LEAK="x", LANG="C.UTF-8")
        self.addCleanup(os.environ.pop, "SOME_LEAK", None)
        h, b = self.tmp / "h", self.tmp / "b"
        env = home.tool_env(h, ["CLAUDE_CODE_OAUTH_TOKEN"], b)
        self.assertLessEqual(set(env), {"PATH", "HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "LANG", "TMPDIR",
                                        "CLAUDE_CODE_OAUTH_TOKEN"})
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], self.tokens["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertEqual((env["HOME"], env["XDG_CONFIG_HOME"], env["CLAUDE_CONFIG_DIR"], env["LANG"]),
                         (str(h), str(h / ".config"), str(h / ".claude"), "C.UTF-8"))
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(b))

    def test_an_unset_auth_var_is_left_out(self):
        del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("ANTHROPIC_API_KEY", home.tool_env(self.tmp, ["ANTHROPIC_API_KEY"]))


class Keep(AtHead):
    def test_keep_copies_real_files_drops_symlinks_and_redacts_every_token(self):
        run = self.prepare()
        tok = self.tokens["CLAUDE_CODE_OAUTH_TOKEN"]
        (run.work / "notes.txt").write_text(f"token {tok} and {self.tokens['CODEX_API_KEY']}\n")
        (run.scratch / "deep").mkdir()
        (run.scratch / "deep" / "out.md").write_text(f"x{tok}x")
        (run.home / ".claude" / "projects").mkdir(parents=True)
        (run.home / ".claude" / "projects" / "s.jsonl").write_text(json.dumps({"env": tok}))
        subprocess.run(["sh", "-c", f"gh auth login --with-token {tok}"], env=run.env(AUTH), capture_output=True)
        dest = self.results / "run-1"
        run.keep(dest, AUTH)

        self.assertEqual((dest / "work" / "notes.txt").read_text(), "token [redacted] and [redacted]\n")
        self.assertEqual((dest / "scratch" / "deep" / "out.md").read_text(), "x[redacted]x")
        self.assertTrue((dest / "home" / ".claude" / "projects" / "s.jsonl").is_file())
        self.assertEqual((dest / "gh-calls").read_text(), "auth login --with-token [redacted]\n")
        self.assertTrue((dest / "work" / ".git" / "HEAD").is_file())
        # Every entry install.sh made is a link into the checkout: none comes back, file or folder.
        self.assertTrue((run.home / ".agents" / "bin" / "settings").is_symlink())
        self.assertFalse([p for p in (dest / "home" / ".agents").rglob("*") if not p.is_dir()])
        self.assertEqual(links(dest / "home" / ".agents" / "skills"), [])
        kept = list(dest.rglob("*"))
        self.assertFalse([p for p in kept if p.is_symlink()])
        for p in kept:
            if p.is_file():
                data = p.read_bytes()
                for value in self.tokens.values():
                    self.assertNotIn(value.encode(), data, p)


class Prompt(EvalSandbox):
    def test_placeholders_are_filled_and_other_braces_stay(self):
        run = home.Run(self.tmp)
        text = "in {work}, write {scratch}/a, read {input}, home {home}, keep {other} and {}"
        self.assertEqual(run.prompt(text), f"in {self.tmp / 'work'}, write {self.tmp / 'scratch'}/a, read "
                                           f"{self.tmp / 'input'}, home {self.tmp / 'home'}, keep {{other}} and {{}}")


if __name__ == "__main__":
    unittest.main()
