"""Tests for the adapter loader (`adapters/__init__.py`) and the Claude Code adapter (`adapters/claude.py`), on the
hand-written stream-json transcripts and session logs in `fixtures/claude/`."""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import adapters  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

FIXTURES = HERE / "fixtures" / "claude"
CAPS = {"turns": 12, "secs": 300.0, "cost_usd": 1.5}
SESSION_TOKENS = {"claude-sonnet-5-5": {"input": 15, "output": 150, "cacheRead": 1500, "cacheWrite5m": 200,
                                        "cacheWrite1h": 300}}
RESULT_TOKENS = {"claude-sonnet-5-5": {"input": 7, "output": 70, "cacheRead": 700, "cacheWrite5m": 7000,
                                       "cacheWrite1h": 0}}


class LoadTest(EvalSandbox):
    def test_load_returns_the_claude_adapter(self):
        claude = adapters.load("claude")
        self.assertEqual(claude.INSTALL_WORD, "claude")
        self.assertTrue(claude.USES_CASE_MODEL)
        self.assertEqual(set(claude.AUTH_VARS), {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"})
        for name in ("prepare_home", "command", "parse", "answer"):
            self.assertTrue(callable(getattr(claude, name)), name)

    def test_unknown_tool_names_the_adapters_found(self):
        for name in ("nope", "../claude", "test_claude", "__init__"):
            with self.assertRaises(ValueError) as caught:
                adapters.load(name)
            self.assertIn(f"'{name}'", str(caught.exception))
            self.assertIn("adapters found: ", str(caught.exception))
            self.assertIn("claude", str(caught.exception).split("adapters found: ")[1])


class CommandTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.claude = adapters.load("claude")

    def test_command_is_headless_claude_with_every_cap(self):
        argv = self.claude.command("set max_parallel to 2", "claude-sonnet-5-5", CAPS, self.tmp)
        self.assertEqual(argv, ["claude", "-p", "set max_parallel to 2", "--model", "claude-sonnet-5-5",
                                "--max-turns", "12", "--max-budget-usd", "1.5", "--output-format", "stream-json",
                                "--verbose", "--dangerously-skip-permissions"])
        self.assertNotIn("--bare", argv)

    def test_command_refuses_a_prompt_read_as_an_option(self):
        for prompt in ("-p", "--help me", "  -x"):
            with self.assertRaises(ValueError):
                self.claude.command(prompt, "claude-sonnet-5-5", CAPS, self.tmp)

    def test_command_never_holds_a_token(self):
        argv = self.claude.command("go", "claude-sonnet-5-5", CAPS, self.tmp)
        for token in self.tokens.values():
            self.assertNotIn(token, " ".join(argv))

    def test_prepare_home_makes_the_config_folder(self):
        home = self.tmp / "eval-home"
        home.mkdir()
        self.claude.prepare_home(home)
        self.assertTrue((home / ".claude").is_dir())


class ParseTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.claude = adapters.load("claude")
        self.run_dir = self.tmp / "run"
        self.eval_home = self.tmp / "eval-home"
        self.work = self.tmp / "work"
        for d in (self.run_dir, self.eval_home / ".claude", self.work):
            d.mkdir(parents=True)

    def parse(self, transcript, session=None, session_id=None):
        shutil.copy(FIXTURES / f"{transcript}.jsonl", self.run_dir / adapters.TRANSCRIPT)
        if session:
            project = self.eval_home / ".claude" / "projects" / "-tmp-work"
            project.mkdir(parents=True)
            shutil.copy(FIXTURES / f"{session}.session.jsonl", project / f"{session_id}.jsonl")
        return self.claude.parse(self.run_dir, self.eval_home, self.work)

    def write(self, stream):
        (self.run_dir / adapters.TRANSCRIPT).write_text("".join(json.dumps(e) + "\n" for e in stream))

    def test_pass_reads_tokens_from_the_session_log(self):
        got = self.parse("pass", "pass", "s-pass")
        self.assertEqual(got, {"tokens": SESSION_TOKENS, "turns": 2, "duration_s": 12.5, "capped": None,
                               "tool_version": "2.1.319", "spawned_agents": 0, "error": None,
                               "zero_cost": False})

    def test_a_revoked_token_run_is_an_auth_error_with_a_reported_zero_cost(self):
        got = self.parse("auth-failed")
        self.assertEqual(got["error"], "auth")
        self.assertIs(got["zero_cost"], True)

    def test_an_authentication_failed_retry_without_a_status_is_an_auth_error(self):
        self.write([{"type": "system", "subtype": "api_retry", "error": "authentication_failed"},
                    {"type": "result", "is_error": True, "total_cost_usd": 0.1}])
        self.assertEqual(self.claude.parse(self.run_dir, self.eval_home, self.work)["error"], "auth")

    def test_a_401_retry_without_an_error_name_is_an_auth_error(self):
        self.write([{"type": "system", "subtype": "api_retry", "error_status": 401},
                    {"type": "result", "is_error": True, "total_cost_usd": 0.1}])
        self.assertEqual(self.claude.parse(self.run_dir, self.eval_home, self.work)["error"], "auth")

    def test_assistant_text_about_authentication_is_not_an_auth_error(self):
        self.write([{"type": "assistant", "message": {"content": [
                        {"type": "text", "text": "Failed to authenticate: 401 authentication_failed"}]}},
                    {"type": "system", "subtype": "api_retry", "error_status": 500, "error": "server_error"},
                    {"type": "result", "is_error": True, "total_cost_usd": 0.1}])
        self.assertEqual(self.claude.parse(self.run_dir, self.eval_home, self.work)["error"], "tool")

    def test_zero_cost_needs_a_zero_cost_and_every_usage_count_zero(self):
        zero = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        cases = [(0, zero, True), (0.0, zero, True), (0.1, zero, False), (0, {**zero, "output_tokens": 3}, False),
                 (None, zero, False), (0, None, False)]
        for cost, usage, want in cases:
            result = {"type": "result", "is_error": False, "total_cost_usd": cost, "usage": usage}
            self.write([{k: v for k, v in result.items() if v is not None}])
            got = self.claude.parse(self.run_dir, self.eval_home, self.work)["zero_cost"]
            self.assertIs(got, want, (cost, usage))

    def test_zero_cost_is_none_without_a_result_event(self):
        self.write([{"type": "system", "subtype": "init", "session_id": "x"}])
        self.assertIsNone(self.claude.parse(self.run_dir, self.eval_home, self.work)["zero_cost"])

    def test_renew_says_how_to_renew_the_token(self):
        self.assertIn("claude setup-token", self.claude.RENEW)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", self.claude.RENEW)
        self.assertIn("ANTHROPIC_API_KEY", self.claude.RENEW)

    def test_the_auth_fixture_holds_no_token_like_value(self):
        text = (FIXTURES / "auth-failed.jsonl").read_text()
        self.assertNotRegex(text, r"sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{8,}|Bearer ")

    def test_without_a_session_log_tokens_come_from_the_result_event(self):
        self.assertEqual(self.parse("pass")["tokens"], RESULT_TOKENS)

    def test_a_session_log_with_nothing_to_measure_gives_null_tokens(self):
        got = self.parse("pass", "empty", "s-pass")
        self.assertIsNone(got["tokens"])
        self.assertIsNone(got["error"])

    def test_a_session_log_with_no_model_gives_null_tokens(self):
        got = self.parse("pass", "no-model", "s-pass")
        self.assertIsNone(got["tokens"])
        self.assertIsNone(got["error"])

    def test_max_turns_is_capped_turns(self):
        got = self.parse("max-turns")
        self.assertEqual(got["capped"], "turns")
        self.assertIsNone(got["error"])

    def test_over_budget_is_capped_cost(self):
        got = self.parse("over-budget")
        self.assertEqual(got["capped"], "cost")
        self.assertIsNone(got["error"])

    def test_killed_mid_stream_is_a_tool_error_with_unknowns_null(self):
        got = self.parse("killed")
        self.assertEqual(got, {"tokens": None, "turns": None, "duration_s": None, "capped": None,
                               "tool_version": "2.1.319", "spawned_agents": 0, "error": "tool",
                               "zero_cost": None})

    def test_killed_mid_stream_still_reads_tokens_from_the_session_log(self):
        self.assertEqual(self.parse("killed", "pass", "s-killed")["tokens"], SESSION_TOKENS)

    def test_no_transcript_is_a_tool_error(self):
        got = self.claude.parse(self.run_dir, self.eval_home, self.work)
        self.assertEqual(got["error"], "tool")
        self.assertIsNone(got["tool_version"])
        self.assertIsNone(got["tokens"])

    def test_answer_is_the_result_event_text(self):
        shutil.copy(FIXTURES / "pass.jsonl", self.run_dir / adapters.TRANSCRIPT)
        self.assertEqual(self.claude.answer(self.run_dir), "done")

    def test_no_result_event_means_no_answer(self):
        self.assertIsNone(self.claude.answer(self.run_dir))
        shutil.copy(FIXTURES / "killed.jsonl", self.run_dir / adapters.TRANSCRIPT)
        self.assertIsNone(self.claude.answer(self.run_dir))

    def test_spawned_agents_counts_task_and_agent_tool_uses(self):
        got = self.parse("subagent")
        self.assertEqual(got["spawned_agents"], 3)
        self.assertEqual(got["turns"], 3)
