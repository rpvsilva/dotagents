"""Tests for the Cursor adapter (`adapters/cursor.py`), on the hand-written stream-json transcripts in
`fixtures/cursor/`, and through `run.py` with a fake `cursor-agent` on `PATH`."""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent
EVALS = RUNNER.parent
sys.path.insert(0, str(RUNNER))
sys.path.insert(0, str(EVALS))

import adapters  # noqa: E402
import results  # noqa: E402
import run  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

FIXTURES = HERE / "fixtures" / "cursor"
MINIMAL = RUNNER / "fixtures" / "cases" / "good" / "configure" / "minimal"
CAPS = {"turns": 12, "secs": 300.0, "cost_usd": 1.5}
MODEL = "gpt-5.2"


class AdapterTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.cursor = adapters.load("cursor")

    def test_installs_for_cursor_and_needs_a_model_given(self):
        self.assertEqual(self.cursor.INSTALL_WORD, "cursor")
        self.assertFalse(self.cursor.USES_CASE_MODEL)
        self.assertEqual(tuple(self.cursor.AUTH_VARS), ("CURSOR_API_KEY",))

    def test_prepare_home_makes_the_config_folder(self):
        home = self.tmp / "eval-home"
        home.mkdir()
        self.cursor.prepare_home(home)
        self.assertTrue((home / ".cursor").is_dir())

    def test_command_is_headless_cursor_agent_in_the_work_folder(self):
        work = self.tmp / "work"
        argv = self.cursor.command("set max_parallel to 2", MODEL, CAPS, work)
        self.assertEqual(argv, ["cursor-agent", "-p", "set max_parallel to 2", "--output-format", "stream-json",
                                "--model", MODEL, "--force", "--trust", "--workspace", str(work)])

    def test_command_never_holds_a_key(self):
        argv = self.cursor.command("go", MODEL, CAPS, self.tmp)
        self.assertNotIn("--api-key", argv)
        for token in self.tokens.values():
            self.assertNotIn(token, " ".join(argv))

    def test_command_refuses_a_prompt_read_as_an_option(self):
        for prompt in ("-p", "--help me", "  -x"):
            with self.assertRaises(ValueError):
                self.cursor.command(prompt, MODEL, CAPS, self.tmp)


class ParseTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.cursor = adapters.load("cursor")
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()

    def parse(self, transcript=None):
        if transcript:
            shutil.copy(FIXTURES / f"{transcript}.jsonl", self.run_dir / adapters.TRANSCRIPT)
        return self.cursor.parse(self.run_dir, self.tmp / "eval-home", self.tmp / "work")

    def test_pass_has_turns_and_time_and_null_tokens(self):
        self.assertEqual(self.parse("pass"), {"tokens": None, "turns": 3, "duration_s": 9.25, "capped": None,
                                              "tool_version": None, "spawned_agents": 0, "error": None})

    def test_renew_names_the_new_key(self):
        self.assertIn("CURSOR_API_KEY", self.cursor.RENEW)

    def test_a_stream_with_no_result_is_a_tool_error(self):
        self.assertEqual(self.parse("no-result"), {"tokens": None, "turns": 1, "duration_s": None, "capped": None,
                                                   "tool_version": None, "spawned_agents": 0, "error": "tool"})

    def test_a_result_marked_as_an_error_is_a_tool_error(self):
        got = self.parse("error")
        self.assertEqual(got["error"], "tool")
        self.assertEqual(got["duration_s"], 1.5)

    def test_no_transcript_is_a_tool_error(self):
        got = self.parse()
        self.assertEqual(got["error"], "tool")
        self.assertIsNone(got["turns"])
        self.assertIsNone(got["tokens"])

    def test_spawned_agents_counts_started_task_tool_calls(self):
        got = self.parse("subagent")
        self.assertEqual(got["spawned_agents"], 2)
        self.assertEqual(got["turns"], 2)
        self.assertIsNone(got["error"])

    def answer(self, transcript=None):
        if transcript:
            shutil.copy(FIXTURES / f"{transcript}.jsonl", self.run_dir / adapters.TRANSCRIPT)
        return self.cursor.answer(self.run_dir)

    def test_answer_is_the_last_assistant_message(self):
        self.assertEqual(self.answer("pass"), "Done: max_parallel is 2 in this repo.")

    def test_answer_of_a_failed_or_cut_run_is_the_last_message_sent(self):
        self.assertEqual(self.answer("error"), "I cannot reach the model.")
        self.assertEqual(self.answer("no-result"), "Reading the configure skill.")

    def test_answer_joins_a_messages_text_parts_and_skips_messages_with_none(self):
        lines = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Set "},
                                                               {"type": "tool_use", "name": "Edit"},
                                                               {"type": "text", "text": "it."}]}},
                 {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
                 {"type": "result", "subtype": "success", "is_error": False, "result": "Set it."}]
        (self.run_dir / adapters.TRANSCRIPT).write_text("".join(json.dumps(e) + "\n" for e in lines))
        self.assertEqual(self.answer(), "Set it.")

    def test_answer_is_none_without_an_assistant_message_or_a_transcript(self):
        self.assertIsNone(self.answer())
        lines = [{"type": "user", "message": {"content": [{"type": "text", "text": "set max_parallel to 2"}]}},
                 {"type": "result", "subtype": "success", "is_error": False, "result": "x"}]
        (self.run_dir / adapters.TRANSCRIPT).write_text("".join(json.dumps(e) + "\n" for e in lines))
        self.assertIsNone(self.answer())


class RunTest(EvalSandbox):
    """`run.py --tool cursor` on an unchanged case, with a fake `cursor-agent` that writes the setting the case's check
    looks for and prints a fixture stream."""

    def setUp(self):
        super().setUp()
        self.evals = self.tmp / "evals"
        self.seen = self.tmp / "seen"
        self.out = self.tmp / "out.txt"
        self.case = self.evals / "configure" / "quick"
        shutil.copytree(MINIMAL, self.case)

    def cursor_agent(self, stream, extra=""):
        self.fake_bin("cursor-agent", f'printf "%s\\n" "$@" >"{self.seen}.argv"\n'
                                      f'{{ pwd; echo "$CURSOR_API_KEY"; echo "$CLAUDE_CODE_OAUTH_TOKEN"; '
                                      f'echo "$ANTHROPIC_API_KEY"; echo "$CODEX_API_KEY"; '
                                      f'test -d "$HOME/.cursor" && echo cursor-dir; }} >"{self.seen}"\n'
                                      "mkdir -p .agents && echo '{}' >.agents/settings.json\n"
                                      f'{extra}\ncat "{FIXTURES / stream}"\n')

    def run_cases(self, model=MODEL):
        with open(self.out, "a") as out:
            got = run.run_cases("cursor", "HEAD", [self.case], runs=1, model=model, out=out)
        entry = got["runs"][0]
        return entry["dir"], results.read(entry["dir"])

    def test_a_passing_run_saves_unknown_cost_never_zero(self):
        self.cursor_agent("pass.jsonl")
        d, res = self.run_cases()
        self.assertEqual(d.relative_to(self.results).parts[0], "cursor")
        self.assertEqual((res["tool"], res["model"]), ("cursor", MODEL))
        self.assertTrue(res["passed"])
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": True})
        self.assertIsNone(res["tokens"])
        self.assertIsNone(res["cost_usd"])
        self.assertEqual(res["cost_basis"], "unknown")
        self.assertEqual((res["turns"], res["duration_s"], res["capped"], res["error"]), (3, 9.25, None, None))
        self.assertEqual((d / adapters.TRANSCRIPT).read_text(), (FIXTURES / "pass.jsonl").read_text())
        self.assertIn("unknown", self.out.read_text())

    def test_the_tool_runs_in_the_work_folder_with_the_cursor_home_made(self):
        self.cursor_agent("pass.jsonl")
        self.run_cases()
        work, *_, cursor_dir = self.seen.read_text().splitlines()
        argv = Path(f"{self.seen}.argv").read_text().splitlines()
        self.assertEqual(Path(work).name, "work")
        self.assertEqual(argv[argv.index("--workspace") + 1], work)
        self.assertEqual(argv[argv.index("--model") + 1], MODEL)
        self.assertEqual(cursor_dir, "cursor-dir")

    def test_a_stream_with_no_result_saves_a_tool_error(self):
        self.cursor_agent("no-result.jsonl")
        _, res = self.run_cases()
        self.assertFalse(res["passed"])
        self.assertEqual(res["error"], "tool")
        self.assertIsNone(res["cost_usd"])
        self.assertEqual(res["cost_basis"], "unknown")

    def test_the_key_reaches_only_the_tool_and_no_kept_file(self):
        self.cursor_agent("pass.jsonl", 'echo "$CURSOR_API_KEY" >leak.txt; echo "key $CURSOR_API_KEY"\n'
                                        'echo "$CURSOR_API_KEY" >"$HOME/.cursor/leak.txt"; echo "$CURSOR_API_KEY" >&2')
        d, _ = self.run_cases()
        key, claude_token, anthropic_key, codex_key = self.seen.read_text().splitlines()[1:5]
        self.assertEqual(key, self.tokens["CURSOR_API_KEY"])
        self.assertEqual((claude_token, anthropic_key, codex_key), ("", "", ""))
        self.assertTrue((d / "work" / "leak.txt").is_file())
        kept = [p for p in d.rglob("*") if p.is_file()] + [self.out]
        for path in kept:
            for token in self.tokens.values():
                self.assertNotIn(token.encode(), path.read_bytes(), str(path))

    def test_a_run_without_a_model_is_refused(self):
        self.cursor_agent("pass.jsonl")
        with self.assertRaises(run.RunError) as caught:
            with open(os.devnull, "w") as out:
                run.run_cases("cursor", "HEAD", [self.case], runs=1, out=out)
        self.assertIn("--model", str(caught.exception))
        self.assertFalse(self.seen.exists())
        self.assertEqual(json.loads((self.case / "case.json").read_text())["model"], "claude-sonnet-5-5")
