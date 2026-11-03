import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import compare

HERE = Path(__file__).parent
PRICING = str(HERE / "fixtures" / "pricing.json")


def tokens(input=0, output=0, cache_read=0, cache_write=0):
    return {"input": input, "output": output, "cacheRead": cache_read,
            "cacheWrite5m": cache_write, "cacheWrite1h": 0}


def span(skill, wall=60, turns=5, tool_calls=10, model="model-a", **counts):
    return {"name": (skill or "no skill").split(":")[-1], "skill": skill,
            "start": "", "end": "", "wallClockSeconds": wall, "turns": turns,
            "toolCalls": tool_calls, "tools": {},
            "tokensByModel": {model: tokens(**counts)}, "subagents": []}


def run(session, date, *spans):
    return {"schemaVersion": 3, "sessionId": session, "date": date,
            "start": f"{date}T10:00:00.000Z", "spans": list(spans)}


class Compare(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def save(self, *runs):
        for r in runs:
            (self.dir / f"{r['sessionId']}.json").write_text(json.dumps(r), encoding="utf-8")

    def compare(self, *args, dirs=None):
        """Run compare.py's main in this process (a fresh interpreter per case costs ~60 ms)."""
        argv = ["compare.py", *(a for d in (dirs or [self.dir]) for a in ("--dir", str(d))),
                "--pricing", PRICING, *args]
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # main reconfigures stdout
        err = io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            try:
                compare.main()
            except SystemExit as e:
                code = e.code
            out.flush()
        return subprocess.CompletedProcess(argv, code, out.buffer.getvalue().decode("utf-8"), err.getvalue())

    def ok(self, *args):
        proc = self.compare(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def rows(self, out, skill):
        """The cells of each body row of the table under the line naming skill."""
        lines = out.splitlines()
        start = lines.index(skill)
        found = []
        for line in lines[start + 1:]:
            if not line.startswith(("│", "┌", "├", "└")):
                break
            if line.startswith("│"):
                found.append([c.strip() for c in line.strip("│").split("│")])
        return found[1:]

    def test_rows_are_oldest_to_newest_one_per_run_with_figures(self):
        self.save(run("b", "2026-10-02", span("deploy", wall=90, turns=7, tool_calls=12,
                                               input=2000, output=300, cache_read=40000, cache_write=500)),
                  run("a", "2026-10-01", span("deploy", input=1000)))
        rows = self.rows(self.ok(), "deploy")
        self.assertEqual(rows[0][:5], ["2026-10-01", "$0.00", "60 s", "5", "10"])
        self.assertEqual(rows[1], ["2026-10-02", "$0.01", "90 s", "7", "12", "2.0k", "300", "40k", "500"])

    def test_a_run_that_ran_the_skill_twice_gives_one_summed_row(self):
        self.save(run("a", "2026-10-01", span("deploy", wall=60, turns=5, tool_calls=10, input=1000),
                      span(None, wall=30), span("deploy", wall=40, turns=2, tool_calls=3, input=500)))
        rows = self.rows(self.ok(), "deploy")
        self.assertEqual(rows, [["2026-10-01", "$0.00", "100 s", "7", "13", "1.5k", "0", "0", "0"]])
        self.assertEqual(self.rows(self.ok(), "no skill")[0][2], "30 s")

    def test_skill_limits_output_and_the_short_name_works(self):
        self.save(run("a", "2026-10-01", span("plug:deploy"), span("review")))
        out = self.ok("deploy")
        self.assertIn("plug:deploy", out.splitlines())
        self.assertNotIn("review", out)

    def test_since_and_until_are_inclusive_on_the_run_date(self):
        self.save(*(run(d, d, span("deploy")) for d in
                    ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04")))
        rows = self.rows(self.ok("--since", "2026-10-02", "--until", "2026-10-03"), "deploy")
        self.assertEqual([r[0] for r in rows], ["2026-10-02", "2026-10-03", "change"])

    def test_change_row_is_latest_minus_earliest_per_figure(self):
        self.save(run("a", "2026-10-01", span("deploy", wall=60, turns=10, tool_calls=4,
                                               input=1000, output=1_000_000, cache_read=0, cache_write=2000)),
                  run("b", "2026-10-02", span("deploy", wall=200, turns=10, tool_calls=4)),
                  run("c", "2026-10-03", span("deploy", wall=90, turns=5, tool_calls=6,
                                               input=1500, output=500_000, cache_read=3000, cache_write=1000)))
        change = self.rows(self.ok(), "deploy")[-1]
        self.assertEqual(change, ["change", "-$5.00 (-50%)", "+30 s (+50%)", "-5 (-50%)", "+2 (+50%)",
                                  "+500 (+50%)", "-500k (-50%)", "+3.0k", "-1.0k (-50%)"])

    def test_no_change_row_with_one_run(self):
        self.save(run("a", "2026-10-01", span("deploy")))
        self.assertEqual([r[0] for r in self.rows(self.ok(), "deploy")], ["2026-10-01"])

    def test_an_unpriced_model_shows_unknown_cost_and_change(self):
        self.save(run("a", "2026-10-01", span("deploy", model="model-x", input=1000)),
                  run("b", "2026-10-02", span("deploy", input=1000)))
        rows = self.rows(self.ok(), "deploy")
        self.assertEqual([r[1] for r in rows], ["unknown", "$0.00", "unknown"])
        self.save(run("c", "2026-10-03", span("deploy", model="model-x", input=1000)))
        rows = self.rows(self.ok("--since", "2026-10-02"), "deploy")
        self.assertEqual([r[1] for r in rows], ["$0.00", "unknown", "unknown"])

    def test_no_matching_run_says_so_on_stderr_and_exits_2(self):
        self.save(run("a", "2026-10-01", span("deploy")))
        for args in (["review"], ["--since", "2026-11-01"]):
            proc = self.compare(*args)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertIn("No saved run", proc.stderr)

    def test_reports_skipped_files(self):
        self.save(run("a", "2026-10-01", span("deploy")))
        (self.dir / "bad.json").write_text("{", encoding="utf-8")
        self.assertIn("Skipped 1 saved file", self.ok())

    def test_the_script_runs_as_a_command(self):
        self.save(run("a", "2026-10-01", span("deploy")))
        proc = subprocess.run(
            [sys.executable, str(HERE / "compare.py"), "--dir", str(self.dir), "--pricing", PRICING],
            capture_output=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.rows(proc.stdout, "deploy")[0][0], "2026-10-01")


if __name__ == "__main__":
    unittest.main()
