"""Tests for the Codex adapter (`adapters/codex.py`): the adapter alone on the hand-written `codex exec --json`
transcripts and session logs in `fixtures/codex/`, and whole runs through `run.py`'s `run_cases` with a fake `codex`
on `PATH` that refuses to run unless the key is in its env and not in its argv."""
import json
import shutil
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
EVALS = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(EVALS))

import adapters  # noqa: E402
import results  # noqa: E402
import run  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

FIXTURES = HERE / "fixtures" / "codex"
MINIMAL = HERE.parent / "fixtures" / "cases" / "good" / "configure" / "minimal"
CAPS = {"turns": 12, "secs": 300.0, "cost_usd": 1.5}
MODEL = "gpt-5.1-codex"
PASS_TOKENS = {MODEL: {"input": 700, "output": 120, "cacheRead": 3500, "cacheWrite5m": 0, "cacheWrite1h": 0}}
ROLLOUT = "sessions/2026/11/09/rollout-2026-11-09T10-00-00-{thread}.jsonl"


def thread_id(transcript):
    return json.loads((FIXTURES / transcript).read_text().splitlines()[0])["thread_id"]


class AdapterTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.codex = adapters.load("codex")

    def test_install_word_auth_and_model(self):
        self.assertEqual(self.codex.INSTALL_WORD, "codex")
        self.assertEqual(tuple(self.codex.AUTH_VARS), ("CODEX_API_KEY",))
        self.assertFalse(self.codex.USES_CASE_MODEL)
        self.assertIn("codex", adapters.names())

    def test_prepare_home_makes_the_codex_folder(self):
        home = self.tmp / "eval-home"
        home.mkdir()
        self.codex.prepare_home(home)
        self.assertTrue((home / ".codex").is_dir())

    def test_command_is_codex_exec_json_in_a_workspace_write_sandbox(self):
        argv = self.codex.command("set max_parallel to 2", MODEL, CAPS, self.tmp)
        self.assertEqual(argv, ["codex", "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check",
                                "-m", MODEL, "set max_parallel to 2"])

    def test_command_refuses_a_prompt_read_as_an_option(self):
        for prompt in ("-p", "--help me", "  -x"):
            with self.assertRaises(ValueError):
                self.codex.command(prompt, MODEL, CAPS, self.tmp)

    def test_command_never_holds_a_token(self):
        argv = " ".join(self.codex.command("go", MODEL, CAPS, self.tmp))
        for token in self.tokens.values():
            self.assertNotIn(token, argv)


class ParseTest(EvalSandbox):
    def setUp(self):
        super().setUp()
        self.codex = adapters.load("codex")
        self.run_dir = self.tmp / "run"
        self.eval_home = self.tmp / "eval-home"
        self.work = self.tmp / "work"
        for d in (self.run_dir, self.eval_home / ".codex", self.work):
            d.mkdir(parents=True)

    def parse(self, transcript, rollout=None, thread=None):
        shutil.copy(FIXTURES / f"{transcript}.jsonl", self.run_dir / adapters.TRANSCRIPT)
        if rollout:
            path = self.eval_home / ".codex" / ROLLOUT.format(thread=thread or thread_id(f"{transcript}.jsonl"))
            path.parent.mkdir(parents=True)
            shutil.copy(FIXTURES / f"{rollout}.rollout.jsonl", path)
        return self.codex.parse(self.run_dir, self.eval_home, self.work)

    def test_pass_takes_the_last_turns_running_total_counts_turns_and_reads_model_and_version_from_the_session_log(self):
        self.assertEqual(self.parse("pass", "pass"), {
            "tokens": PASS_TOKENS, "turns": 2, "duration_s": None, "capped": None, "tool_version": "0.58.0",
            "spawned_agents": 0, "error": None})

    def test_a_session_log_of_another_thread_is_not_read(self):
        got = self.parse("pass", "pass", thread="0199a213-other")
        self.assertIsNone(got["tool_version"])
        self.assertEqual(list(got["tokens"]), ["unknown"])

    def test_without_a_session_log_the_model_and_version_are_unknown(self):
        got = self.parse("pass")
        self.assertEqual(got["tokens"], {"unknown": PASS_TOKENS[MODEL]})
        self.assertIsNone(got["tool_version"])
        self.assertIsNone(got["error"])

    def test_an_error_event_and_failed_turn_is_a_tool_error_keeping_the_tokens_spent(self):
        got = self.parse("error", "pass")
        self.assertEqual(got["error"], "tool")
        self.assertEqual(got["turns"], 1)
        self.assertEqual(got["tokens"], {MODEL: {"input": 400, "output": 30, "cacheRead": 100, "cacheWrite5m": 0,
                                                 "cacheWrite1h": 0}})
        self.assertIsNone(got["capped"])

    def test_killed_mid_stream_is_a_tool_error_with_unknowns_null(self):
        self.assertEqual(self.parse("killed", "pass"), {
            "tokens": None, "turns": None, "duration_s": None, "capped": None, "tool_version": "0.58.0",
            "spawned_agents": 0, "error": "tool"})

    def test_no_transcript_is_a_tool_error(self):
        got = self.codex.parse(self.run_dir, self.eval_home, self.work)
        self.assertEqual((got["error"], got["tokens"], got["turns"], got["tool_version"]), ("tool", None, None, None))

    def test_spawned_agents_counts_each_agent_started_once(self):
        self.assertEqual(self.parse("subagent")["spawned_agents"], 2)

    def test_agent_messages_and_commands_are_not_subagents(self):
        self.assertEqual(self.parse("fallback")["spawned_agents"], 0)
        self.assertEqual(self.parse("pass")["spawned_agents"], 0)


class RunTest(EvalSandbox):
    """Whole runs through `run.run_cases("codex", ...)` against this repo at HEAD."""

    def setUp(self):
        super().setUp()
        self.evals = self.tmp / "evals"
        self.calls = self.tmp / "codex-calls"
        self.out = self.tmp / "out.txt"

    def case(self, name="configure/quick", **fields):
        dest = self.evals / name
        shutil.copytree(MINIMAL, dest)
        data = json.loads((dest / "case.json").read_text())
        data.update(fields)
        (dest / "case.json").write_text(json.dumps(data))
        return dest

    def fake_codex(self, stream, extra=""):
        """A `codex` that exits 3 unless `CODEX_API_KEY` is in its env, not in its argv, and no other tool's key
        reached it; then writes the session log with the `-m` model, does the configure work and prints `stream`."""
        rollout = '"$HOME/.codex/' + ROLLOUT.format(thread=thread_id(stream)) + '"'
        self.fake_bin("codex", f"""
echo call >>"{self.calls}"
[ -n "$CODEX_API_KEY" ] || {{ echo "no CODEX_API_KEY" >&2; exit 3; }}
case "$*" in *"$CODEX_API_KEY"*) echo "key in argv" >&2; exit 3;; esac
[ -z "$ANTHROPIC_API_KEY$CLAUDE_CODE_OAUTH_TOKEN$CURSOR_API_KEY" ] || {{ echo "another tool's key" >&2; exit 3; }}
model=; prev=
for a in "$@"; do [ "$prev" = -m ] && model=$a; prev=$a; done
mkdir -p "$(dirname {rollout})"
printf '%s\\n' '{{"type": "session_meta", "payload": {{"cli_version": "0.58.0"}}}}' \\
  "{{\\"type\\": \\"turn_context\\", \\"payload\\": {{\\"model\\": \\"$model\\"}}}}" >{rollout}
mkdir -p .agents && echo '{{}}' >.agents/settings.json
{extra}
cat "{FIXTURES / stream}"
""")

    def run_cases(self, cases, **kw):
        kw.setdefault("runs", 1)
        kw.setdefault("model", MODEL)
        with open(self.out, "a") as out:
            return run.run_cases("codex", "HEAD", cases, out=out, **kw)

    def saved(self, got):
        entry = got["runs"][0]
        return entry["dir"], results.read(entry["dir"])

    def call_count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def test_a_case_runs_on_codex_unchanged_with_its_tokens_and_unknown_cost(self):
        self.fake_codex("pass.jsonl")
        d, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(self.call_count(), 1)
        self.assertEqual(d.relative_to(self.results).parts[0], "codex")
        self.assertEqual((res["tool"], res["model"], res["tool_version"]), ("codex", MODEL, "0.58.0"))
        self.assertEqual(res["checks"], {"settings-written": True, "no-github": True})
        self.assertTrue(res["passed"])
        self.assertEqual((res["tokens"], res["turns"], res["error"], res["capped"]), (PASS_TOKENS, 2, None, None))
        self.assertEqual((res["cost_usd"], res["cost_basis"]), (None, "unknown"))
        self.assertEqual(res["subagents"], "none")
        self.assertIn(" unknown ", self.out.read_text())

    def test_cost_is_computed_when_pricing_holds_the_model(self):
        table = json.loads(run.PRICING.read_text())
        table["models"][MODEL] = {"input": 1.25, "output": 10.0, "cacheRead": 0.125, "cacheWrite5m": 0,
                                  "cacheWrite1h": 0}
        priced = self.tmp / "pricing.json"
        priced.write_text(json.dumps(table))
        self.fake_codex("pass.jsonl")
        with mock.patch.object(run, "PRICING", priced):
            _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["cost_basis"], "pricing")
        self.assertEqual(res["cost_usd"], round((700 * 1.25 + 120 * 10.0 + 3500 * 0.125) / 1_000_000, 6))

    def test_a_skill_with_agents_and_none_started_ends_fallback(self):
        self.fake_codex("fallback.jsonl")
        _, res = self.saved(self.run_cases([self.case("build-epic/plan", install=["configure"])]))
        self.assertEqual(res["subagents"], "fallback")

    def test_a_skill_with_agents_that_starts_one_ends_used(self):
        self.fake_codex("subagent.jsonl")
        _, res = self.saved(self.run_cases([self.case("build-epic/plan", install=["configure"])]))
        self.assertEqual(res["subagents"], "used")

    def test_an_error_event_fails_the_run_as_a_tool_error(self):
        self.fake_codex("error.jsonl")
        _, res = self.saved(self.run_cases([self.case()]))
        self.assertEqual(res["error"], "tool")
        self.assertFalse(res["passed"])
        self.assertIn("error tool", self.out.read_text())

    def test_the_home_has_the_codex_folder_before_install_runs(self):
        repo = self.tmp / "dotagents"
        (repo / "scripts").mkdir(parents=True)
        shutil.copy(EVALS.parent / "scripts" / "capped", repo / "scripts" / "capped")
        (repo / "scripts" / "parts.sh").write_text('shift; printf "%s\\n" "$@"\n')
        (repo / "install.sh").write_text('[ "$1" = codex ] || exit 1\n'
                                         '[ -d "$HOME/.codex" ] || { echo "codex: ~/.codex not found" >&2; exit 1; }\n')
        for args in (("init", "-q"), ("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "-c",
                                                     "commit.gpgsign=false", "commit", "-qm", "x")):
            run.subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                               stdin=run.subprocess.DEVNULL)
        self.fake_codex("pass.jsonl")
        _, res = self.saved(self.run_cases([self.case()], repo=repo))
        self.assertIsNone(res["error"])
        self.assertEqual(self.call_count(), 1)

    def test_the_key_reaches_only_codex_and_no_kept_file(self):
        self.fake_codex("pass.jsonl", 'echo "$CODEX_API_KEY" >leak.txt; echo "$CODEX_API_KEY" >"$HOME/leak.txt"\n'
                                      'echo "key $CODEX_API_KEY" >&2; echo "{\\"type\\": \\"x\\", \\"k\\": '
                                      '\\"$CODEX_API_KEY\\"}"\n')
        d, res = self.saved(self.run_cases([self.case()]))
        self.assertIsNone(res["error"])
        self.assertIn("[redacted]", (d / "work" / "leak.txt").read_text())
        kept = [p for p in d.rglob("*") if p.is_file()]
        self.assertIn(d / "transcript.jsonl", kept)
        for path in kept:
            for token in self.tokens.values():
                self.assertNotIn(token.encode(), path.read_bytes(), path)

    def test_codex_needs_a_full_model_id_before_any_run(self):
        self.fake_codex("pass.jsonl")
        with self.assertRaises(run.RunError) as caught:
            self.run_cases([self.case()], model=None)
        self.assertIn("--model <full model ID>", str(caught.exception))
        self.assertEqual(self.call_count(), 0)
        self.assertEqual(list(self.results.iterdir()), [])
