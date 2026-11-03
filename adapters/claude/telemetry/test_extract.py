import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"

OPUS_TURN = {"input": 10, "output": 100, "cacheRead": 1000, "cacheWrite5m": 0, "cacheWrite1h": 200}
HAIKU_TURN = {"input": 5, "output": 50, "cacheRead": 500, "cacheWrite5m": 100, "cacheWrite1h": 0}


def times(turn, n):
    return {kind: value * n for kind, value in turn.items()}


def extract(*args, script=HERE / "extract.py"):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, encoding="utf-8",
    )


_RUNS = {}


def run(*args, script=HERE / "extract.py"):
    """extract.py's (or script's) report for these args; each (script, args) runs once, as its input never changes."""
    key = (str(script), args)
    if key not in _RUNS:
        proc = extract(*args, script=script)
        assert proc.returncode == 0, proc.stderr
        _RUNS[key] = json.loads(proc.stdout)
    return copy.deepcopy(_RUNS[key])


def by_name(run_):
    return {span["name"]: span for span in run_["spans"]}


class Spans(unittest.TestCase):
    """spans.jsonl: chat, /grill-with-docs (loads grilling), /model, /renovate-prs twice, /telemetry."""

    @classmethod
    def setUpClass(cls):
        cls.out = run("--session", str(FIXTURES / "spans.jsonl"))
        cls.spans = by_name(cls.out)

    def test_each_typed_skill_starts_a_span_in_order(self):
        # /model is a built-in command and loads no skill, so it starts no span.
        self.assertEqual([s["name"] for s in self.out["spans"]],
                         ["no skill", "grill-with-docs", "renovate-prs", "renovate-prs-2"])

    def test_activity_before_any_skill_is_its_own_span(self):
        span = self.spans["no skill"]
        self.assertIsNone(span["skill"])
        self.assertEqual((span["turns"], span["toolCalls"], span["wallClockSeconds"]), (1, 0, 2))

    def test_skills_loaded_inside_a_span_count_toward_it(self):
        # q2 (Skill: grilling), q3 and q4 (Bash); 09:01:00 to the /model line at 09:03:00.
        span = self.spans["grill-with-docs"]
        self.assertEqual(span["skill"], "grill-with-docs")
        self.assertEqual(span["turns"], 3)
        self.assertEqual(span["tools"], {"Skill": 1, "Bash": 1})
        self.assertEqual(span["wallClockSeconds"], 120)
        self.assertEqual(span["tokensByModel"], {"claude-opus-5-5": times(OPUS_TURN, 3)})

    def test_a_subagent_belongs_to_the_span_that_started_it(self):
        # q5, q6 plus the subagent's t1, t2; the background subagent ends at 09:06:00.
        span = self.spans["renovate-prs"]
        self.assertEqual(span["turns"], 4)
        self.assertEqual(span["toolCalls"], 2)
        self.assertEqual(span["wallClockSeconds"], 120)
        self.assertEqual(span["subagents"], [{
            "type": "general-purpose", "description": "Fix PR", "turns": 2, "toolCalls": 1,
            "tools": {"Read": 1}, "tokensByModel": {"claude-haiku-5-5": times(HAIKU_TURN, 2)},
        }])
        self.assertEqual(self.spans["renovate-prs-2"]["subagents"], [])

    def test_the_same_skill_typed_again_starts_a_numbered_span(self):
        span = self.spans["renovate-prs-2"]
        self.assertEqual(span["skill"], "renovate-prs")
        self.assertEqual((span["start"], span["turns"], span["wallClockSeconds"]),
                         ("2026-10-23T09:05:00.000Z", 1, 4))

    def test_the_run_lasts_from_the_first_span_to_whatever_ends_last(self):
        self.assertEqual(self.out["start"], "2026-10-23T09:00:00.000Z")
        self.assertEqual(self.out["end"], "2026-10-23T09:06:00.000Z")
        self.assertEqual(self.out["wallClockSeconds"], 360)


class SkillNamed(unittest.TestCase):
    def test_narrows_the_report_to_that_skills_last_span(self):
        run_ = run("--session", str(FIXTURES / "spans.jsonl"), "--skill", "renovate-prs")
        self.assertEqual([s["start"] for s in run_["spans"]], ["2026-10-23T09:05:00.000Z"])
        self.assertEqual(run_["spans"][0]["name"], "renovate-prs")
        self.assertEqual(run_["wallClockSeconds"], 4)

    def test_finds_the_skill_before_an_earlier_telemetry_call(self):
        # run-twice.jsonl: /renovate-prs, /telemetry, /renovate-prs, /telemetry.
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")
        span = run_["spans"][0]
        self.assertEqual(span["start"], "2026-10-23T10:03:00.000Z")
        # The reply after the final /telemetry is left out; r4-r7 plus the subagent's two.
        self.assertEqual(span["turns"], 6)
        self.assertEqual(span["tools"], {"mcp__datadog-mcp__search_logs": 1, "Agent": 1, "Bash": 1,
                                         "mcp__datadog-mcp__get_trace": 1, "Read": 1})

    def test_merges_log_lines_of_one_api_call(self):
        # r4 and r6 are each split over two lines; usage is counted once per call.
        span = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")["spans"][0]
        self.assertEqual(span["tokensByModel"]["claude-opus-5-5"], times(OPUS_TURN, 4))

    def test_wall_clock_waits_for_the_background_subagent(self):
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"), "--skill", "renovate-prs")
        self.assertEqual(run_["end"], "2026-10-23T10:06:30.000Z")
        self.assertEqual(run_["wallClockSeconds"], 210)

    def test_stops_and_lists_the_skills_that_did_run(self):
        proc = extract("--session", str(FIXTURES / "spans.jsonl"), "--skill", "deploy")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("Skills that ran: grill-with-docs, renovate-prs", proc.stderr)


class NoSkillNamed(unittest.TestCase):
    def test_starts_at_the_first_typed_message_after_the_previous_telemetry_call(self):
        # The previous /telemetry's own reply (r3) belongs to that report, not to this run.
        run_ = run("--session", str(FIXTURES / "run-twice.jsonl"))
        self.assertEqual(run_["start"], "2026-10-23T10:03:00.000Z")
        self.assertEqual([(s["name"], s["turns"]) for s in run_["spans"]], [("renovate-prs", 6)])

    def test_a_skill_the_model_started_outside_any_span_starts_one(self):
        run_ = run("--session", str(FIXTURES / "model-invoked.jsonl"))
        # "update deps" alone made no turns, so the empty "no skill" span is left out.
        self.assertEqual([s["name"] for s in run_["spans"]], ["renovate-prs"])
        span = run_["spans"][0]
        self.assertEqual((span["start"], span["wallClockSeconds"], span["turns"], span["toolCalls"]),
                         ("2026-10-23T11:00:03.000Z", 27, 3, 2))
        self.assertEqual(run_["start"], "2026-10-23T11:00:00.000Z")
        self.assertEqual(run_["wallClockSeconds"], 30)

    def test_says_so_when_telemetry_runs_twice_in_a_row(self):
        lines = (FIXTURES / "run-twice.jsonl").read_text().splitlines()
        report_call = next(line for line in lines if "/telemetry</command-name>" in line)
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_text("\n".join([*lines, report_call, report_call]) + "\n")
            proc = extract("--session", str(session))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Nothing to measure", proc.stderr)


class DefaultSession(unittest.TestCase):
    """With no --session, measure the session Claude Code says is running, not the newest log."""

    def test_uses_the_session_id_from_the_environment(self):
        with tempfile.TemporaryDirectory() as home:
            project_dir = Path(home) / "work" / "app"
            project_dir.mkdir(parents=True)
            logs = Path(home) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(project_dir.resolve()))
            logs.mkdir(parents=True)
            shutil.copy(FIXTURES / "model-invoked.jsonl", logs / "this-session.jsonl")
            shutil.copy(FIXTURES / "run-twice.jsonl", logs / "other-session.jsonl")
            os.utime(logs / "other-session.jsonl")  # the other session wrote last
            env = {**os.environ, "HOME": home, "USERPROFILE": home, "CLAUDE_CODE_SESSION_ID": "this-session"}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--cwd", str(project_dir)],
                                  capture_output=True, encoding="utf-8", env=env)
            self.assertEqual(json.loads(proc.stdout)["sessionId"], "this-session", proc.stderr)

    def test_finds_the_session_by_id_when_run_from_another_folder(self):
        # The session started in work/, then the shell moved into work/app/ (e.g. an audited repo).
        with tempfile.TemporaryDirectory() as home:
            started_in = Path(home) / "work"
            run_from = started_in / "app"
            run_from.mkdir(parents=True)
            logs = Path(home) / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", str(started_in.resolve()))
            logs.mkdir(parents=True)
            shutil.copy(FIXTURES / "model-invoked.jsonl", logs / "this-session.jsonl")
            env = {**os.environ, "HOME": home, "USERPROFILE": home, "CLAUDE_CODE_SESSION_ID": "this-session"}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--cwd", str(run_from)],
                                  capture_output=True, encoding="utf-8", env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["sessionId"], "this-session")


SENTINEL = "SENTINEL-NEVER-STORED"


def step_labels(calls, folder, script=HERE / "extract.py"):
    """The labels extract.py (or script) gives each (tool, value) call, whose input is {key: value}, in one session written to folder."""
    keys = {"Bash": "command", "Read": "file_path"}
    lines = [{"type": "user", "timestamp": "2026-10-24T09:00:00.000Z", "message": {"role": "user", "content": "go"}}]
    for i, (tool, value) in enumerate(calls):
        # One call per second, so the steps come back in the order given.
        lines.append({"type": "assistant", "timestamp": f"2026-10-24T09:{1 + i // 60:02d}:{i % 60:02d}.000Z",
                      "requestId": f"q{i}", "message": {
                          "model": "claude-opus-5-5", "role": "assistant", "usage": {},
                          "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": {keys[tool]: value}}]}})
    session = Path(folder) / "masked.jsonl"
    session.write_text("".join(json.dumps(line) + "\n" for line in lines))
    steps = run("--session", str(session), script=script)["steps"]
    assert len(steps) == len(calls), steps
    return [step["label"] for step in steps]


class Steps(unittest.TestCase):
    """steps.jsonl: tool calls before any skill, /renovate-prs, and an Agent call whose subagent log has its own."""

    @classmethod
    def setUpClass(cls):
        cls.out = run("--session", str(FIXTURES / "steps.jsonl"))
        cls.steps = cls.out["steps"]
        cls.by_label = {(s["tool"], s["label"]): s for s in cls.steps}

    def test_the_run_is_schema_version_3(self):
        self.assertEqual(self.out["schemaVersion"], 3)

    def test_one_step_per_tool_call_in_start_order_subagents_included(self):
        self.assertEqual([(s["tool"], s["start"]) for s in self.steps], [
            ("Bash", "2026-10-24T10:00:01.000Z"), ("Bash", "2026-10-24T10:00:02.000Z"),
            ("Read", "2026-10-24T10:00:02.500Z"), ("Write", "2026-10-24T10:00:04.000Z"),
            ("Edit", "2026-10-24T10:00:04.000Z"), ("NotebookEdit", "2026-10-24T10:00:04.000Z"),
            ("Grep", "2026-10-24T10:00:05.000Z"), ("Glob", "2026-10-24T10:00:05.000Z"),
            ("WebFetch", "2026-10-24T10:00:05.000Z"), ("WebSearch", "2026-10-24T10:00:05.000Z"),
            ("AskUserQuestion", "2026-10-24T10:00:07.000Z"), ("mcp__srv__search", "2026-10-24T10:00:07.000Z"),
            ("TodoWrite", "2026-10-24T10:00:07.000Z"), ("Bash", "2026-10-24T10:00:30.000Z"),
            ("Skill", "2026-10-24T10:01:01.000Z"), ("Bash", "2026-10-24T10:01:02.000Z"),
            ("Agent", "2026-10-24T10:01:03.000Z"), ("Read", "2026-10-24T10:01:04.000Z"),
            ("Bash", "2026-10-24T10:01:05.000Z"), ("Bash", "2026-10-24T10:01:11.000Z"),
        ])

    def test_a_step_lasts_from_its_call_to_its_result_in_milliseconds(self):
        self.assertEqual(self.by_label[("Bash", "git status")]["seconds"], 0.412)
        self.assertEqual(self.by_label[("Read", "/repo/README.md")]["seconds"], 0.75)
        self.assertEqual(self.by_label[("Agent", "general-purpose: Fix PR")]["seconds"], 7.0)

    def test_a_call_with_no_result_has_null_seconds(self):
        self.assertIsNone(self.by_label[("Bash", "make deploy")]["seconds"])
        self.assertFalse(self.by_label[("Bash", "make deploy")]["failed"])

    def test_a_result_flagged_as_an_error_marks_the_step_failed(self):
        self.assertEqual([s["label"] for s in self.steps if s["failed"]], ["false", "pytest -q"])

    def test_a_result_landing_in_the_next_span_is_timed_and_kept_in_the_span_of_its_call(self):
        step = self.by_label[("Bash", "sleep 30")]
        self.assertEqual((step["seconds"], step["span"], step["skill"]), (30.0, "no skill", None))

    def test_each_step_names_its_span_and_skill(self):
        step = self.by_label[("Skill", "grilling")]
        self.assertEqual((step["span"], step["skill"], step["subagent"]), ("renovate-prs", "renovate-prs", None))
        self.assertEqual([s["name"] for s in self.out["spans"]], ["no skill", "renovate-prs"])

    def test_subagent_steps_are_tagged_with_its_agent_type(self):
        tagged = [(s["tool"], s["label"], s["span"]) for s in self.steps if s["subagent"] == "general-purpose"]
        self.assertEqual(tagged, [("Read", "/repo/x.py", "renovate-prs"), ("Bash", "pytest -q", "renovate-prs")])

    def test_subagent_steps_name_the_subagent_instance_and_others_null(self):
        self.assertEqual({s["subagentId"] for s in self.steps if s["subagent"]}, {"agent-f"})
        self.assertEqual({s["subagentId"] for s in self.steps if not s["subagent"]}, {None})

    def test_only_a_background_bash_is_flagged_background(self):
        self.assertEqual([s["label"] for s in self.steps if "background" in s], ["npm test"])
        self.assertIs(self.by_label[("Bash", "npm test")]["background"], True)

    def test_each_step_holds_only_the_known_fields(self):
        keys = {"tool", "label", "start", "seconds", "failed", "span", "skill", "subagent", "subagentId"}
        for step in self.steps:
            self.assertEqual(set(step) - {"background"}, keys)

    def test_labels_come_from_the_input_key_of_each_tool(self):
        self.assertEqual([(s["tool"], s["label"]) for s in self.steps if s["subagent"] is None and s["tool"] != "Bash"], [
            ("Read", "/repo/README.md"), ("Write", "/repo/new.txt"), ("Edit", "/repo/old.txt"),
            ("NotebookEdit", "/repo/nb.ipynb"), ("Grep", "TODO in src"), ("Glob", "**/*.py"),
            ("WebFetch", "https://example.com/docs"), ("WebSearch", "python re"),
            ("AskUserQuestion", ""), ("mcp__srv__search", ""), ("TodoWrite", ""),
            ("Skill", "grilling"), ("Agent", "general-purpose: Fix PR"),
        ])

    def test_a_label_keeps_only_the_first_line(self):
        self.assertIn(("Bash", "git status"), self.by_label)

    def test_no_prompt_reply_output_or_other_input_is_stored(self):
        # The fixture puts the sentinel in content, old_string, new_string, prompt, questions,
        # tool results, typed prompts and subagent messages.
        fixture = (FIXTURES / "steps.jsonl").read_text()
        for key in ("content", "old_string", "new_string", "prompt", "questions"):
            self.assertIn(f'"{key}": ', fixture)
        self.assertIn(SENTINEL, fixture)
        self.assertNotIn(SENTINEL, json.dumps(self.out))

    def labels_from_a_copy(self, patterns=None):
        """Every label from a copy of the extractor whose tree holds this patterns text, or no patterns file."""
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "a" / "b" / "c" / "extract.py"
            script.parent.mkdir(parents=True)
            shutil.copy(HERE / "extract.py", script)
            if patterns is not None:
                rules = Path(folder) / "scripts" / "hooks" / "secret-patterns.txt"
                rules.parent.mkdir(parents=True)
                rules.write_text(patterns)
            proc = subprocess.run([sys.executable, str(script), "--session", str(FIXTURES / "steps.jsonl")],
                                  capture_output=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        steps = json.loads(proc.stdout)["steps"]
        self.assertEqual(len(steps), len(self.steps))
        return {s["label"] for s in steps}

    def test_with_no_patterns_file_every_label_is_empty(self):
        self.assertEqual(self.labels_from_a_copy(), {""})

    def test_with_a_rule_that_does_not_compile_every_label_is_empty(self):
        # A good rule beside the broken one does not unlock the labels: masking fails closed.
        self.assertEqual(self.labels_from_a_copy("token ghp_[A-Za-z0-9]{36}\nlabel bearer (\n"), {""})

    def test_a_patterns_file_with_rules_gives_labels(self):
        # Guards the two tests above: the copied tree itself is not what empties the labels.
        self.assertIn("git status", self.labels_from_a_copy("token ghp_[A-Za-z0-9]{36}\n"))


class CrlfPatterns(unittest.TestCase):
    """A patterns file with CRLF endings (a Windows checkout) masks labels the same as with LF."""

    def test_token_and_label_rules_still_mask(self):
        calls = [("Bash", "curl -d " + "ghp_" + "a" * 36 + " x"), ("Bash", "mysql --password=hunter2"),
                 ("Bash", "echo hello")]
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "a" / "b" / "c" / "extract.py"
            script.parent.mkdir(parents=True)
            shutil.copy(HERE / "extract.py", script)
            rules = Path(folder) / "scripts" / "hooks" / "secret-patterns.txt"
            rules.parent.mkdir(parents=True)
            lf = (HERE.parents[2] / "scripts" / "hooks" / "secret-patterns.txt").read_bytes().replace(b"\r\n", b"\n")
            rules.write_bytes(lf.replace(b"\n", b"\r\n"))
            labels = step_labels(calls, folder, script)
        self.assertEqual(labels, ["curl -d *** x", "mysql --password=***", "echo hello"])


class StepMasking(unittest.TestCase):
    """Labels are masked with the token and label rules of scripts/hooks/secret-patterns.txt, then cut."""

    # Built at runtime so no token-like text is committed.
    TOKENS = {
        "github": "ghp_" + "a" * 36,
        "github fine-grained": "github_pat_" + "B" * 22,
        "aws": "AKIA" + "C" * 16,
        "slack": "xoxb-" + "1" * 10,
        "anthropic": "sk-ant-" + "d" * 20,
        "google": "AIza" + "e" * 35,
        "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    }

    PASSWORDS = {
        "mysql --password=hunter2 db": "mysql --password=*** db",
        "export API_KEY=abc123": "export API_KEY=***",
        "DB_PASSWORD: s3cret make": "DB_PASSWORD: *** make",
        "GITHUB_TOKEN=abc123 gh pr list": "GITHUB_TOKEN=*** gh pr list",
        'curl -H "Authorization: ' + 'Bearer abc.def" x': 'curl -H "Authorization: ***" x',
        "echo bearer abc.def": "echo ***",
    }
    # Each credential form a label rule masks; the label keeps the program, flags, host and path.
    # Built at runtime where token- or credential-shaped, so no such text is committed
    # (gitleaks flags a curl -u user:pass or Authorization header written out whole).
    FORMS = {
        "curl user:password": ("curl -u " + "admin" + ":" + "hunter2 https://x", "curl -u *** https://x"),
        "curl --user": ("curl --user " + "admin" + ":" + "hunter2 https://x", "curl --user *** https://x"),
        "mysql -p": ("mysql -u root -ps3cr3t db", "mysql -u root -p*** db"),
        "--password=": ("mysql --password=s3cr3t db", "mysql --password=*** db"),
        "--password value": ("mysql --password s3cr3t db", "mysql --password *** db"),
        "NAME=value with PASS": ("DB_PASS=hunter2 ./run", "DB_PASS=*** ./run"),
        "NAME=value with KEY": ("export STRIPE_KEY=sk_live_x", "export STRIPE_KEY=***"),
        "NAME=value with SECRET": ("API_SECRET=x make", "API_SECRET=*** make"),
        "NAME=value with PWD": ("db_pwd=x make", "db_pwd=*** make"),
        "NAME=value with AUTH": ("MY_AUTH=x make", "MY_AUTH=*** make"),
        "NAME=value with TOKEN": ("npm_token=x make", "npm_token=*** make"),
        "URL userinfo": ("git clone https://rui:pw123@github.com/a/b", "git clone https://***@github.com/a/b"),
        "URL query": ("curl https://x/api?key=abc&b=1", "curl https://x/api?***"),
        "Authorization Basic": ('curl -H "Authorization: Basic ' + "YWRtaW46" + 'cGFzcw==" x', 'curl -H "Authorization: ***" x'),
        "Authorization other scheme": ("curl -H 'Authorization: token abc' x", "curl -H 'Authorization: ***' x"),
        "Authorization no scheme": ('curl -H "Authorization:abc" x', 'curl -H "Authorization:***" x'),
        "OpenAI key": ("echo " + "sk-" + "proj-" + "a1" * 12, "echo ***"),
        "Stripe key": ("echo " + "sk_" + "live_" + "b2" * 12, "echo ***"),
    }
    # Look like the forms above but hold no secret, so they are kept as they are.
    KEPT = ["git push -u origin main", "mkdir -p src/a", "cp -pr a b", "ssh -p22 host",
            "curl https://example.com/docs", "git log --format=%H", "echo desk-top"]
    NEAR_CUT = "echo " + "x" * 65 + " " + TOKENS["github"] + " done"

    @classmethod
    def setUpClass(cls):
        # Every case is one call in a single session, so the extractor runs once for the class.
        calls = [("Bash", f"curl -d {token} x") for token in cls.TOKENS.values()]
        calls += [("Read", "/tmp/" + cls.TOKENS["github"] + ".txt"), ("Bash", "echo " + "x" * 200), ("Bash", cls.NEAR_CUT)]
        calls += [("Bash", command) for command in cls.PASSWORDS]
        calls += [("Bash", command) for command, _ in cls.FORMS.values()]
        calls += [("Bash", command) for command in cls.KEPT]
        calls += [("Bash", "\n\n  \ngit push\nmore")]
        with tempfile.TemporaryDirectory() as folder:
            cls.labels = dict(zip(calls, step_labels(calls, folder)))

    def label(self, tool, value):
        return self.labels[(tool, value)]

    def test_every_token_kind_becomes_stars(self):
        for kind, token in self.TOKENS.items():
            with self.subTest(kind):
                self.assertEqual(self.label("Bash", f"curl -d {token} x"), "curl -d *** x")

    def test_a_token_in_a_file_path_is_masked(self):
        self.assertEqual(self.label("Read", "/tmp/" + self.TOKENS["github"] + ".txt"), "/tmp/***.txt")

    def test_passwords_keys_and_bearer_tokens_become_stars(self):
        for command, label in self.PASSWORDS.items():
            with self.subTest(command):
                self.assertEqual(self.label("Bash", command), label)

    def test_each_credential_form_is_masked_keeping_the_rest(self):
        for form, (command, label) in self.FORMS.items():
            with self.subTest(form):
                self.assertEqual(self.label("Bash", command), label)

    def test_commands_with_no_secret_are_kept_whole(self):
        for command in self.KEPT:
            with self.subTest(command):
                self.assertEqual(self.label("Bash", command), command)

    def test_a_label_starts_at_the_first_non_blank_line(self):
        self.assertEqual(self.label("Bash", "\n\n  \ngit push\nmore"), "git push")

    def test_a_long_label_is_cut_to_80_characters(self):
        label = self.label("Bash", "echo " + "x" * 200)
        self.assertEqual(len(label), 80)
        self.assertEqual(label, "echo " + "x" * 74 + "…")

    def test_a_token_near_the_cut_is_masked_whole_before_cutting(self):
        token = self.TOKENS["github"]
        label = self.label("Bash", self.NEAR_CUT)
        self.assertEqual(label, "echo " + "x" * 65 + " *** done")
        self.assertNotIn(token[:8], label)


class Whole(unittest.TestCase):
    """--whole measures the whole session, as the session-end save does; earlier reports are ordinary spans."""

    def test_keeps_earlier_telemetry_reports_and_everything_after_them(self):
        # run-twice.jsonl: /clear and chat, /renovate-prs, /telemetry, /renovate-prs, /telemetry.
        run_ = run("--whole", "--session", str(FIXTURES / "run-twice.jsonl"))
        self.assertEqual([s["name"] for s in run_["spans"]],
                         ["no skill", "renovate-prs", "telemetry", "renovate-prs-2", "telemetry-2"])
        self.assertEqual(run_["start"], "2026-10-23T09:59:00.000Z")
        # The final /telemetry's own reply (r8) is measured too.
        self.assertEqual(by_name(run_)["telemetry-2"]["turns"], 1)
        self.assertEqual(run_["end"], "2026-10-23T10:06:30.000Z")

    def test_marks_the_run_as_whole(self):
        self.assertIs(run("--whole", "--session", str(FIXTURES / "run-twice.jsonl"))["whole"], True)
        self.assertIs(run("--session", str(FIXTURES / "run-twice.jsonl"))["whole"], False)

    def test_works_from_any_folder_with_a_transcript_path(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.run([sys.executable, str((HERE / "extract.py").resolve()), "--whole",
                                   "--session", str((FIXTURES / "spans.jsonl").resolve())],
                                  capture_output=True, encoding="utf-8", cwd=folder)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["sessionId"], "spans")


class RepoAndDate(unittest.TestCase):
    def test_repo_is_the_git_top_level_of_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            top = Path(folder).resolve()
            subprocess.run(["git", "init", "-q", str(top)], check=True)
            (top / "sub").mkdir()
            run_ = run("--session", str(FIXTURES / "spans.jsonl"), "--cwd", str(top / "sub"))
        self.assertEqual(run_["repo"], str(top))

    @unittest.skipIf(os.name == "nt", "a shell-script git stub cannot run on Windows; the test above covers it there")
    def test_repo_is_in_the_os_native_form_whatever_git_prints(self):
        # Git for Windows prints C:/Users/...; a run's repo must be the same string the OS's own paths give.
        with tempfile.TemporaryDirectory() as folder:
            stub = Path(folder) / "git"
            stub.write_text('#!/bin/sh\necho "//work//dotagents/"\n')
            stub.chmod(0o755)
            env = {**os.environ, "PATH": folder + os.pathsep + os.environ.get("PATH", "")}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--session",
                                   str(FIXTURES / "spans.jsonl"), "--cwd", str(HERE)],
                                  capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["repo"], str(Path("//work/dotagents")))

    def test_repo_is_null_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(folder).resolve().parent)}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--session",
                                   str(FIXTURES / "spans.jsonl"), "--cwd", folder],
                                  capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(json.loads(proc.stdout)["repo"])

    def test_repo_is_null_without_git(self):
        with tempfile.TemporaryDirectory() as empty:
            env = {**os.environ, "PATH": empty}
            proc = subprocess.run([sys.executable, str(HERE / "extract.py"), "--session",
                                   str(FIXTURES / "spans.jsonl"), "--cwd", str(HERE)],
                                  capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(json.loads(proc.stdout)["repo"])

    def test_date_is_the_day_the_run_started(self):
        for args in ([], ["--whole"]):
            run_ = run(*args, "--session", str(FIXTURES / "spans.jsonl"))
            self.assertEqual(run_["date"], run_["start"][:10])
            self.assertEqual(run_["date"], "2026-10-23")


class DamagedLog(unittest.TestCase):
    def test_a_half_written_last_line_is_skipped(self):
        text = (FIXTURES / "spans.jsonl").read_text()
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_text(text + '{"type": "assistant", "timestamp": "2026-10-23T09:07:00.000Z", "mess')
            proc = extract("--whole", "--session", str(session))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        run_ = json.loads(proc.stdout)
        self.assertEqual([s["name"] for s in run_["spans"]],
                         ["no skill", "grill-with-docs", "renovate-prs", "renovate-prs-2", "telemetry"])
        self.assertEqual(run_["spans"][-1]["end"], "2026-10-23T09:05:35.000Z")

    def damaged(self, tail):
        """extract.py --whole on a copy of the spans fixture, its subagents beside it, with tail (bytes) appended."""
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_bytes((FIXTURES / "spans.jsonl").read_bytes() + tail)
            shutil.copytree(FIXTURES / "spans", Path(folder) / "s")
            return extract("--whole", "--session", str(session))

    def assertSameRun(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["spans"], run("--whole", "--session", str(FIXTURES / "spans.jsonl"))["spans"])

    def test_a_last_line_cut_inside_a_utf8_character_is_skipped(self):
        self.assertSameRun(self.damaged(b'{"type":"user","message":{"content":"caf\xc3'))

    def test_a_line_that_is_json_but_not_an_object_is_skipped(self):
        for line in (b"123", b'"x"', b"[]", b"null"):
            with self.subTest(line=line):
                self.assertSameRun(self.damaged(line + b"\n"))

    def test_a_bad_line_anywhere_in_the_log_is_skipped(self):
        lines = (FIXTURES / "spans.jsonl").read_bytes().splitlines(keepends=True)
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            session.write_bytes(b"".join(lines[:3]) + b'{"half\n' + b"\xff\xfe\n" + b"[1]\n" + b"".join(lines[3:]))
            shutil.copytree(FIXTURES / "spans", Path(folder) / "s")
            self.assertSameRun(extract("--whole", "--session", str(session)))

    def test_a_half_written_subagent_meta_drops_only_that_subagent(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            shutil.copy(FIXTURES / "spans.jsonl", session)
            shutil.copytree(FIXTURES / "spans", Path(folder) / "s")
            subagents = Path(folder) / "s" / "subagents"
            (subagents / "agent-x.meta.json").write_text('{"type":')
            (subagents / "agent-x.jsonl").write_text("")
            (subagents / "agent-y.meta.json").write_text("[]")
            proc = extract("--whole", "--session", str(session))
        self.assertSameRun(proc)
        self.assertTrue(any(s["subagents"] for s in json.loads(proc.stdout)["spans"]))

    def test_a_subagent_meta_without_its_log_does_not_stop_the_save(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Path(folder) / "s.jsonl"
            shutil.copy(FIXTURES / "spans.jsonl", session)
            shutil.copytree(FIXTURES / "spans", Path(folder) / "s")
            (Path(folder) / "s" / "subagents" / "agent-z.meta.json").write_text('{"agentType": "Explore", "toolUseId": "zz"}')
            proc = extract("--whole", "--session", str(session))
        self.assertSameRun(proc)


if __name__ == "__main__":
    unittest.main()
