"""Tests for `decide.py` (the only writer of `<run>/decisions.md`) and `steplib/decisions.py` (its reader)."""
import datetime
import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import decisions, tracker  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

DECIDE = SCRIPTS / "decide.py"


class DecideCase(unittest.TestCase):
    def setUp(self):
        self.kit = Kit()
        self.addCleanup(self.kit.close)
        self.run_dir = self.kit.run_dir
        self.file = self.run_dir / "decisions.md"

    def decide(self, *args, ok=True):
        p = self.kit.step(DECIDE, self.run_dir, *args)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        self.assertEqual(p.returncode == 0, ok, p.stdout + p.stderr)
        if not ok:
            self.assertRegex(lines[0], r"^FAIL decisions .* \(log: (.+)\)$")
            self.assertTrue(Path(re.search(r"\(log: (.+)\)$", lines[0]).group(1)).exists())
        return lines[0]

    def text(self):
        with open(self.file, newline="", encoding="utf-8") as f:
            return f.read()


class SetTest(DecideCase):
    def test_set_writes_one_header_line_and_prints_ok(self):
        self.assertEqual(self.decide("set", "base", "feat/integration"), "OK decisions set base")
        self.assertEqual(decisions.get(self.run_dir, "base"), "feat/integration")
        self.assertEqual(self.text().count("base: feat/integration\n"), 1)

    def test_set_again_replaces_the_line(self):
        self.decide("set", "human", "rpvsilva")
        self.decide("set", "human", "someone")
        self.assertEqual(decisions.get(self.run_dir, "human"), "someone")
        self.assertNotIn("rpvsilva", self.text())

    def test_every_header_key_round_trips(self):
        values = {"base": "main", "ci": "off", "merge_identity": "human", "human": "rpvsilva",
                  "review_before_agent_merge": "yes", "test_command": "tests/run.sh --all | tee out"}
        for key, value in values.items():
            self.assertEqual(self.decide("set", key, value), f"OK decisions set {key}")
        for key, value in values.items():
            self.assertEqual(decisions.get(self.run_dir, key), value)

    def test_an_unknown_key_fails_and_writes_nothing(self):
        line = self.decide("set", "colour", "blue", ok=False)
        self.assertIn("unknown-key: colour", line)
        self.assertFalse(self.file.exists())

    def test_a_value_outside_a_keys_choices_fails(self):
        self.assertIn("bad-value: ci", self.decide("set", "ci", "maybe", ok=False))
        self.assertIsNone(decisions.get(self.run_dir, "ci"))

    def test_a_value_with_a_newline_fails(self):
        for value in ("main\nci: off", "main\r"):
            self.assertIn("newline: base", self.decide("set", "base", value, ok=False))
        self.assertFalse(self.file.exists())

    def test_a_wrong_verb_or_argument_count_is_a_usage_failure(self):
        for args in ((), ("unset", "base"), ("set", "base"), ("clause", "c", "yes"), ("quote", "a", "b")):
            with self.subTest(args=args):
                self.assertIn("usage: decide.py", self.decide(*args, ok=False))
        self.assertFalse(self.file.exists())

    def test_a_missing_run_folder_fails(self):
        p = self.kit.step(DECIDE, self.kit.tmp / "nope", "set", "base", "main")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(p.stdout.startswith("FAIL decisions no-run-folder"), p.stdout)


class ClauseTest(DecideCase):
    def test_clause_adds_a_row_keeping_the_quote_byte_for_byte(self):
        quote = 'Rui: "merge | ship as me" \\| C:\\path\\  ok  '
        self.assertEqual(self.decide("clause", "Leave merges to the human", "yes", quote),
                         "OK decisions clause waived=yes")
        rows = decisions.load(self.run_dir).authorizations
        self.assertEqual(rows, [decisions.Authorization("Leave merges to the human", "yes", quote)])

    def test_clause_updates_the_row_with_the_same_clause(self):
        self.decide("clause", "No force push", "ask", "first")
        self.decide("clause", "Approve | merge", "no", "other")
        self.decide("clause", "No force push", "yes", "second | answer")
        d = decisions.load(self.run_dir)
        self.assertEqual(d.authorizations, [decisions.Authorization("No force push", "yes", "second | answer"),
                                            decisions.Authorization("Approve | merge", "no", "other")])
        self.assertEqual(d.waived("Approve | merge"), "no")
        self.assertIsNone(d.waived("Unknown"))

    def test_a_waived_value_other_than_yes_no_ask_fails(self):
        self.assertIn("bad-value: waived", self.decide("clause", "c", "maybe", "q", ok=False))
        self.assertFalse(self.file.exists())

    def test_a_clause_or_quote_over_several_lines_fails(self):
        for args in (("a\nb", "yes", "q"), ("c", "yes", "q\r"), ("c", "no", "q\nr")):
            with self.subTest(args=args):
                self.assertIn("newline: ", self.decide("clause", *args, ok=False))
        self.assertFalse(self.file.exists())

    def test_a_blank_clause_fails(self):
        self.assertIn("empty-clause", self.decide("clause", "  ", "yes", "q", ok=False))
        self.assertFalse(self.file.exists())

    def test_the_table_renders_as_markdown(self):
        self.decide("clause", "a|b", "yes", "q|r")
        self.assertIn("## Authorizations\n\n| clause | waived | quote |\n| --- | --- | --- |\n"
                      "| a\\|b | yes | q\\|r |\n", self.text())


class QuoteTest(DecideCase):
    def test_quote_appends_a_dated_line_unchanged(self):
        first = "Merge each PR, use the cli like it was me"
        second = "  CI: ignore | Actions stay off \\o/  "
        out = self.decide("quote", first)
        self.assertRegex(out, r"^OK decisions quote \d{4}-\d{2}-\d{2}$")
        self.decide("quote", second)
        got = decisions.load(self.run_dir).decisions
        self.assertEqual([x.text for x in got], [first, second])
        self.assertEqual(got[0].date, out.split()[-1])
        datetime.date.fromisoformat(got[0].date)
        self.assertIn(f"- {got[0].date}: {first}\n", self.text())

    def test_a_quote_over_several_lines_reads_back_unchanged(self):
        text = "Rui said:\n\n  keep going\nthen stop"
        self.decide("quote", text)
        self.assertEqual(decisions.load(self.run_dir).decisions[0].text, text)


    def test_a_carriage_return_in_a_quote_fails(self):
        self.assertIn("carriage-return", self.decide("quote", "one\r\ntwo", ok=False))
        self.assertFalse(self.file.exists())


class ReaderTest(DecideCase):
    def test_a_missing_file_reads_as_unset(self):
        self.assertIsNone(decisions.load(self.run_dir))
        self.assertIsNone(decisions.get(self.run_dir, "base"))

    def test_an_unset_key_reads_as_none(self):
        self.decide("set", "base", "main")
        self.assertIsNone(decisions.get(self.run_dir, "human"))
        self.decide("set", "test_command", "")
        self.assertIsNone(decisions.get(self.run_dir, "test_command"))

    def test_an_unknown_key_is_refused_by_the_reader(self):
        with self.assertRaises(KeyError):
            decisions.get(self.run_dir, "colour")

    def test_the_file_round_trips(self):
        self.decide("set", "merge_identity", "app")
        self.decide("set", "base", "main")
        self.decide("clause", "Leave merges | to Rui", "ask", " spaced | quote ")
        self.decide("quote", "first\nsecond")
        text = self.text()
        self.assertEqual(decisions.render(decisions.parse(text)), text)
        self.assertEqual(decisions.parse(text), decisions.load(self.run_dir))
        self.assertTrue(text.startswith("# Decisions\nbase: main\nmerge_identity: app\n"), text)

    def test_a_file_with_crlf_line_ends_reads_as_with_lf(self):
        self.decide("set", "ci", "off")
        self.decide("set", "base", "int")
        self.decide("clause", "Leave merges to Rui", "yes", "q")
        self.decide("quote", "first\nsecond")
        lf = decisions.load(self.run_dir)
        self.file.write_bytes(self.text().replace("\n", "\r\n").encode("utf-8"))  # a Windows checkout or editor
        self.assertEqual(decisions.load(self.run_dir), lf)
        self.assertEqual((decisions.get(self.run_dir, "ci"), decisions.get(self.run_dir, "base")), ("off", "int"))

    def test_lines_it_does_not_know_are_ignored(self):
        text = ("# Decisions\nbase: main\ncolour: blue\nnot a header\nci:off\n\n"
                "## Authorizations\n\n| clause | waived | quote |\n| --- | --- | --- |\n| c | maybe | q |\n"
                "| short | yes |\n| kept | no | q |\n\n## Decisions\n\n  orphan continuation\n- not-a-date: x\n"
                "- 2026-11-06: kept\n")
        self.assertEqual(decisions.parse(text), decisions.Decisions(
            {"base": "main"}, [decisions.Authorization("kept", "no", "q")], [decisions.Decision("2026-11-06", "kept")]))


class TrackerHumanTest(DecideCase):
    def test_the_recorded_human_never_replaces_the_gh_login(self):
        self.kit.make_origin()
        self.decide("set", "human", "injected")
        self.kit.answer("api-user", out="learned\n")
        with mock.patch.dict(os.environ, self.kit.env, clear=True):
            login = tracker.Tracker(self.run_dir, self.kit.clone, io.StringIO()).human()
        self.assertEqual(login, "learned")

    def test_without_a_recorded_human_the_tracker_asks_gh(self):
        self.kit.make_origin()
        self.kit.answer("api-user", out="learned\n")
        with mock.patch.dict(os.environ, self.kit.env, clear=True):
            login = tracker.Tracker(self.run_dir, self.kit.clone, io.StringIO()).human()
        self.assertEqual(login, "learned")


if __name__ == "__main__":
    unittest.main()
