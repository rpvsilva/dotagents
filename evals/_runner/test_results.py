"""Tests for `results.py` (the saved run: `result.json`, its store and the reuse lookup) and for `testlib.EvalSandbox`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import results  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

FULL = {
    "schema": results.SCHEMA,
    "tool": "claude",
    "tool_version": "2.1.319",
    "model": "claude-sonnet-5-5",
    "ref": "main",
    "ref_sha": "a" * 40,
    "case": "configure/minimal",
    "case_hash": "b" * 64,
    "run": 2,
    "checks": {"settings-written": True, "no-github": False},
    "checks_hash": "c" * 64,
    "passed": False,
    "tokens": {"claude-sonnet-5-5": {"input": 10, "output": 20, "cacheRead": 30, "cacheWrite5m": 40,
                                     "cacheWrite1h": 0}},
    "cost_usd": 0.012345,
    "cost_basis": "pricing",
    "pricing_as_of": "2026-11-01",
    "turns": 7,
    "duration_s": 12.5,
    "capped": "turns",
    "subagents": "fallback",
    "error": None,
    "rubric": {"scores": {"clear": {"score": 4, "reason": "one line"}}, "judge_model": "claude-haiku-5-5",
               "rubric_hash": "d" * 64},
    "transcript": "transcript.jsonl",
}


class ResultFile(EvalSandbox):
    def test_write_then_read_round_trips_every_field(self):
        d = self.tmp / "run-1"
        results.write(d, FULL)
        self.assertEqual(results.read(d), FULL)
        self.assertEqual(sorted(FULL), sorted(results.FIELDS))

    def test_unknown_values_are_saved_as_null_never_zero(self):
        d = self.tmp / "run-1"
        results.write(d, {"tool": "cursor", "model": "gpt-5", "run": 1})
        saved = json.loads((d / "result.json").read_text())
        for field in ("tokens", "cost_usd", "turns", "duration_s", "capped", "rubric", "tool_version"):
            self.assertIsNone(saved[field], field)
        self.assertEqual(saved["schema"], results.SCHEMA)
        self.assertEqual(set(saved), set(results.FIELDS))

    def test_read_fills_missing_fields_with_null(self):
        d = self.tmp / "run-1"
        d.mkdir()
        (d / "result.json").write_text(json.dumps({"tool": "claude", "turns": 3}))
        got = results.read(d)
        self.assertEqual(got["turns"], 3)
        self.assertIsNone(got["cost_usd"])
        self.assertIsNone(got["tokens"])

    def test_write_refuses_an_unknown_field(self):
        with self.assertRaises(ValueError):
            results.write(self.tmp / "run-1", {"tool": "claude", "cost": 1})
        self.assertFalse((self.tmp / "run-1" / "result.json").exists())


class CaseHash(EvalSandbox):
    def case(self):
        d = self.tmp / "case"
        (d / "fixture" / "src").mkdir(parents=True)
        (d / "case.json").write_text('{"model": "claude-sonnet-5-5"}')
        (d / "prompt.md").write_text("do it\n")
        (d / "fixture" / "src" / "a.txt").write_text("a\n")
        return d

    def test_is_a_sha256_stable_for_the_same_files(self):
        d = self.case()
        h = results.case_hash(d)
        self.assertRegex(h, r"^[0-9a-f]{64}$")
        self.assertEqual(results.case_hash(d), h)

    def test_changes_when_a_file_changes(self):
        d = self.case()
        h = results.case_hash(d)
        (d / "fixture" / "src" / "a.txt").write_text("b\n")
        self.assertNotEqual(results.case_hash(d), h)

    def test_changes_when_a_file_is_added_or_renamed(self):
        d = self.case()
        h = results.case_hash(d)
        (d / "rubric.md").write_text("")
        added = results.case_hash(d)
        self.assertNotEqual(added, h)
        (d / "rubric.md").rename(d / "rubric2.md")
        self.assertNotEqual(results.case_hash(d), added)

    def test_does_not_depend_on_where_the_case_sits(self):
        a = self.case()
        b = self.tmp / "elsewhere" / "case"
        b.parent.mkdir()
        a.rename(b)
        self.assertEqual(results.case_hash(b), results.case_hash(self.case()))

    def test_tells_moved_content_apart(self):
        # Path and content boundaries are unambiguous: "ab" + "" differs from "a" + "b".
        d1, d2 = self.tmp / "one", self.tmp / "two"
        d1.mkdir()
        d2.mkdir()
        (d1 / "x").write_text("ab")
        (d1 / "y").write_text("")
        (d2 / "x").write_text("a")
        (d2 / "y").write_text("b")
        self.assertNotEqual(results.case_hash(d1), results.case_hash(d2))
        # One file whose content reads like the next path and its content.
        d3, d4 = self.tmp / "three", self.tmp / "four"
        d3.mkdir()
        d4.mkdir()
        (d3 / "x").write_bytes(b"ay\0b")
        (d4 / "x").write_bytes(b"a")
        (d4 / "y").write_bytes(b"b")
        self.assertNotEqual(results.case_hash(d3), results.case_hash(d4))


class StoreRoot(EvalSandbox):
    def test_the_results_flag_wins(self):
        self.assertEqual(results.store_root(str(self.tmp / "flag")), self.tmp / "flag")

    def test_else_evals_results(self):
        self.assertEqual(results.store_root(None), Path(os.environ["EVALS_RESULTS"]))

    def test_else_the_home_store(self):
        del os.environ["EVALS_RESULTS"]
        self.assertEqual(results.store_root(None), self.home / ".agents" / "evals" / "results")

    def test_an_empty_evals_results_counts_as_unset(self):
        os.environ["EVALS_RESULTS"] = ""
        self.assertEqual(results.store_root(None), self.home / ".agents" / "evals" / "results")

    def test_run_dir_follows_the_layout(self):
        root = results.store_root(None)
        self.assertEqual(results.run_dir(root, "claude", "f" * 40, "claude-sonnet-5-5", "configure/minimal", "e" * 64, 3),
                         root / "claude" / ("f" * 40) / "claude-sonnet-5-5" / "configure" / "minimal" / ("e" * 64)
                         / "run-3")

    def test_run_dir_refuses_a_key_that_escapes_the_store(self):
        root = results.store_root(None)
        for bad in ("", "..", "../x", "/abs", "a/../../b"):
            with self.assertRaises(ValueError, msg=bad):
                results.run_dir(root, "claude", "f" * 40, "m-1", bad, "e" * 64, 1)
        with self.assertRaises(ValueError):
            results.run_dir(root, "claude", "f" * 40, "m-1", "c/x", "e" * 64, 0)


class SavedRuns(EvalSandbox):
    KEY = ("claude", "f" * 40, "claude-sonnet-5-5", "configure/minimal", "e" * 64)

    def save(self, root, n, key=KEY):
        d = results.run_dir(root, *key, n)
        results.write(d, {"tool": key[0], "run": n})
        return d

    def test_lists_finished_runs_in_run_order(self):
        root = results.store_root(None)
        runs = [self.save(root, n) for n in (10, 2, 1)]
        self.assertEqual(results.saved_runs(root, *self.KEY), [runs[2], runs[1], runs[0]])

    def test_skips_a_run_with_no_result(self):
        root = results.store_root(None)
        done = self.save(root, 1)
        results.run_dir(root, *self.KEY, 2).mkdir(parents=True)
        (results.run_dir(root, *self.KEY, 3).parent / "notes").mkdir()
        self.assertEqual(results.saved_runs(root, *self.KEY), [done])

    def test_is_empty_when_nothing_is_saved(self):
        self.assertEqual(results.saved_runs(results.store_root(None), *self.KEY), [])

    def test_matches_every_part_of_the_key(self):
        root = results.store_root(None)
        self.save(root, 1)
        for i in range(len(self.KEY)):
            other = list(self.KEY)
            other[i] = other[i][:-1] + "0" if i != 3 else "configure/full"
            self.assertEqual(results.saved_runs(root, *other), [], other)


SANDBOX_VARS = ("HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "EVALS_RESULTS", "PATH") + EvalSandbox.TOKEN_VARS


class Sandbox(unittest.TestCase):
    """EvalSandbox driven through a throwaway subclass, as each evals test uses it."""

    def run_probe(self, probe):
        class Probe(EvalSandbox):
            def test_probe(self):
                probe(self)

        out = unittest.TestResult()
        Probe("test_probe").run(out)
        self.assertEqual(out.errors + out.failures, [])

    def test_points_home_config_and_results_into_temp_folders(self):
        seen = {}

        def probe(t):
            seen.update({v: os.environ.get(v) for v in SANDBOX_VARS})
            seen["home"] = Path.home()
            self.assertEqual(Path(os.environ["XDG_CONFIG_HOME"]), t.home / ".config")
            self.assertEqual(Path(os.environ["CLAUDE_CONFIG_DIR"]), t.home / ".claude")
            self.assertTrue(t.home.is_dir())
            self.assertTrue(Path(os.environ["EVALS_RESULTS"]).is_dir())
            self.assertFalse(Path(os.environ["EVALS_RESULTS"]).is_relative_to(t.home))
            self.assertEqual(results.store_root(None), Path(os.environ["EVALS_RESULTS"]))

        self.run_probe(probe)
        self.assertEqual(seen["home"], Path(seen["HOME"]))
        self.assertTrue(seen["HOME"].startswith(tempfile.gettempdir()))
        self.assertNotEqual(seen["HOME"], os.environ.get("HOME"))

    def test_sets_fake_token_values(self):
        def probe(t):
            for var in EvalSandbox.TOKEN_VARS:
                self.assertEqual(os.environ[var], t.tokens[var])
                self.assertIn("fake", t.tokens[var])
            self.assertEqual(len(set(t.tokens.values())), len(EvalSandbox.TOKEN_VARS))

        self.run_probe(probe)

    def test_fake_binary_comes_first_on_path(self):
        def probe(t):
            path = t.fake_bin("claude", 'echo "fake claude $*"\nexit 3\n')
            self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(t.bin))
            self.assertEqual(path, t.bin / "claude")
            out = subprocess.run(["claude", "-p", "hi"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual((out.returncode, out.stdout), (3, "fake claude -p hi\n"))

        self.run_probe(probe)

    def test_restores_every_variable_in_teardown(self):
        os.environ["XDG_CONFIG_HOME"] = "/kept/xdg"
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME", None)
        before = {v: os.environ.get(v) for v in SANDBOX_VARS}
        made = []
        self.run_probe(lambda t: made.append(t.tmp))
        self.assertEqual({v: os.environ.get(v) for v in SANDBOX_VARS}, before)
        self.assertFalse(made[0].exists())

    def test_restores_when_the_test_fails(self):
        before = {v: os.environ.get(v) for v in SANDBOX_VARS}

        class Failing(EvalSandbox):
            def test_fail(self):
                self.fail("on purpose")

        out = unittest.TestResult()
        Failing("test_fail").run(out)
        self.assertEqual(len(out.failures), 1)
        self.assertEqual({v: os.environ.get(v) for v in SANDBOX_VARS}, before)


if __name__ == "__main__":
    unittest.main()
