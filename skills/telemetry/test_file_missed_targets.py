"""Tests for `file_missed_targets.py`: one issue per missed target, through a stub `gh`, as telemetry's identity."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "file_missed_targets.py"
SETTINGS = HERE.parent.parent / "scripts" / "settings.sh"

# Logs each call (argv and GH_TOKEN). `issue list` prints `issues.json` in the folder (the issues the repo holds);
# `issue create` adds one there and prints its URL, unless `no-url` is in the folder; `fail` in the folder makes
# the `issue create` after that many successes fail.
STUB = """import json, os, pathlib, sys
folder = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
log = folder / "calls.jsonl"
creates = sum(1 for line in log.read_text().splitlines() if json.loads(line)["args"][:2] == ["issue", "create"]) if log.exists() else 0
with log.open("a") as f:
    f.write(json.dumps({"args": args, "token": os.environ.get("GH_TOKEN")}) + "\\n")
issues = folder / "issues.json"
held = json.loads(issues.read_text()) if issues.exists() else []
if args[:2] == ["issue", "list"]:
    print(json.dumps(held))
    sys.exit(0)
fail = folder / "fail"
if fail.exists() and creates >= int(fail.read_text()):
    sys.exit("gh: HTTP 502")
if args[:2] != ["issue", "create"]:
    sys.exit(1)
number = 101 + creates
held.append({"number": number, "body": args[args.index("--body") + 1]})
issues.write_text(json.dumps(held))
if not (folder / "no-url").exists():
    print(f"https://github.com/o/r/issues/{number}")
"""

def target(status, value, threshold=0, op="eq", **extra):
    return {"run": "b", "metric": "m", "op": op, "threshold": threshold, "unit": "u", "source": "#181: s",
            "value": value, "status": status, **extra}


COMPARISON = {
    "schema": "build-epic-run-comparison/v1",
    "comparison": {
        "time": {},
        "targets": {
            "total-cost": target("missed", 120.5, 90, "lt", unit="USD", basis="estimate-mid", range=[100, 140]),
            "orchestrator-cost": target("met", 10, 15, "lt"),
            "conflict-resolvers": target("missed", 2, unit="agents"),
            "human-round-trips": target("unknown", None),
        },
    },
    "runs": {},
}


class FileMissedTargets(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "gh_stub.py").write_text(STUB, encoding="utf-8")
        self.gh = shlex.join([sys.executable, "-I", str(self.tmp / "gh_stub.py"), str(self.tmp)])
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
        self.env = {**env, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.tmp / "config")}
        self.file = self.tmp / "comparison.json"
        self.file.write_text(json.dumps(COMPARISON), encoding="utf-8")

    def install_settings(self):
        """Links the real settings tool where install.sh does, so the Declaration in settings.json is what it reads."""
        bin_ = self.home / ".agents" / "bin"
        bin_.mkdir(parents=True)
        (bin_ / "settings").symlink_to(SETTINGS)
        return bin_ / "settings"

    def run_script(self, *extra, check=True):
        res = subprocess.run([sys.executable, "-I", str(SCRIPT), str(self.file), "--epic", "192", "--gh", self.gh, *extra],
                             capture_output=True, text=True, encoding="utf-8", env=self.env, cwd=str(self.tmp),
                             stdin=subprocess.DEVNULL)
        if check:
            self.assertEqual(res.returncode, 0, res.stderr)
        return res

    def calls(self):
        log = self.tmp / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def creates(self):
        return [c for c in self.calls() if c["args"][:2] == ["issue", "create"]]

    def targets(self):
        return json.loads(self.file.read_text(encoding="utf-8"))["comparison"]["targets"]

    def flag(self, call, name):
        args = call["args"]
        return args[args.index(name) + 1]

    def test_two_missed_targets_file_two_issues_and_met_and_unknown_file_none(self):
        self.run_script()
        titles = [self.flag(c, "--title") for c in self.creates()]
        self.assertEqual(len(titles), 2, titles)
        self.assertTrue(any("total-cost" in t for t in titles), titles)
        self.assertTrue(any("conflict-resolvers" in t for t in titles), titles)

    def test_a_rerun_files_none(self):
        self.run_script()
        self.run_script()
        self.assertEqual(len(self.creates()), 2)

    def test_the_output_names_the_issues_filed_and_those_filed_before(self):
        first = json.loads(self.run_script().stdout)
        self.assertEqual(first, {"filed": {"total-cost": 101, "conflict-resolvers": 102}, "already": {}})
        rerun = json.loads(self.run_script().stdout)
        self.assertEqual(rerun, {"filed": {}, "already": {"total-cost": 101, "conflict-resolvers": 102}})

    def test_an_empty_gh_is_refused(self):
        res = subprocess.run([sys.executable, "-I", str(SCRIPT), str(self.file), "--epic", "192", "--gh", " "],
                             capture_output=True, text=True, encoding="utf-8", env=self.env, cwd=str(self.tmp),
                             stdin=subprocess.DEVNULL)
        self.assertEqual(res.returncode, 2)
        self.assertIn("--gh is empty", res.stderr)
        self.assertEqual(self.calls(), [])

    def test_the_comparison_names_each_filed_issue_number(self):
        self.run_script()
        got = self.targets()
        self.assertEqual({k: v.get("issue") for k, v in got.items()},
                         {"total-cost": 101, "orchestrator-cost": None, "conflict-resolvers": 102,
                          "human-round-trips": None})
        self.assertEqual(got["total-cost"]["value"], 120.5, "the rest of the target stays")

    def test_the_body_holds_threshold_value_marker_and_the_epic(self):
        self.run_script("--repo", "o/r")
        first = self.creates()[0]
        self.assertEqual(self.flag(first, "--repo"), "o/r")
        body = self.flag(first, "--body")
        for part in ("< 90 USD", "120.5 USD", "<!-- telemetry-missed-target: total-cost -->", "Part of #192",
                     "estimate-mid", "100", "140"):
            self.assertIn(part, body)

    def test_without_identity_command_issues_are_filed_as_the_current_login(self):
        self.install_settings()
        self.run_script()
        self.assertEqual([c["token"] for c in self.calls()], [None, None, None])

    def test_issues_are_filed_as_the_identity_command_setting(self):
        tool = self.install_settings()
        token = self.tmp / "token.sh"
        token.write_text("#!/bin/sh\necho tok-123\n", encoding="utf-8")
        token.chmod(0o755)
        subprocess.run([str(tool), "set", "telemetry", "identity_command", str(token), "--machine"], env=self.env,
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
        self.run_script()
        self.assertEqual([c["token"] for c in self.calls()], ["tok-123"] * 3)

    def test_a_failing_identity_command_files_nothing(self):
        tool = self.install_settings()
        subprocess.run([str(tool), "set", "telemetry", "identity_command", "false", "--machine"], env=self.env,
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
        res = self.run_script(check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_a_failing_gh_keeps_the_issues_already_filed(self):
        (self.tmp / "fail").write_text("1", encoding="utf-8")
        res = self.run_script(check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("HTTP 502", res.stderr)
        self.assertEqual([v.get("issue") for v in self.targets().values() if v["status"] == "missed"], [101, None])

    def test_an_issue_already_holding_the_marker_is_recorded_not_filed_again(self):
        # A fresh comparison (compare_runs.py rerun and saved over the file) has no `issue`; the repo holds one.
        (self.tmp / "issues.json").write_text(json.dumps([
            {"number": 7, "body": "old\n<!-- telemetry-missed-target: total-cost -->\nPart of #192\n"},
            {"number": 8, "body": "<!-- telemetry-missed-target: total-cost-extra -->"}]), encoding="utf-8")
        out = json.loads(self.run_script("--repo", "o/r").stdout)
        self.assertEqual(out, {"filed": {"conflict-resolvers": 101}, "already": {"total-cost": 7}})
        self.assertEqual([self.flag(c, "--title") for c in self.creates() if "total-cost" in self.flag(c, "--title")], [])
        self.assertEqual(self.targets()["total-cost"]["issue"], 7)
        search = [c for c in self.calls() if c["args"][:2] == ["issue", "list"]]
        self.assertEqual(len(search), 1)
        self.assertIn("telemetry-missed-target", self.flag(search[0], "--search"))
        self.assertEqual(self.flag(search[0], "--repo"), "o/r")

    def test_a_create_that_prints_no_url_is_found_by_its_marker_and_never_filed_twice(self):
        (self.tmp / "no-url").write_text("", encoding="utf-8")
        self.run_script()
        self.assertEqual({k: v.get("issue") for k, v in self.targets().items() if v["status"] == "missed"},
                         {"total-cost": 101, "conflict-resolvers": 102})
        self.file.write_text(json.dumps(COMPARISON), encoding="utf-8")  # a fresh comparison saved over the file
        self.run_script()
        self.assertEqual(len(self.creates()), 2)

    def test_a_failing_search_files_nothing(self):
        (self.tmp / "issues.json").write_text("{not json", encoding="utf-8")
        res = self.run_script(check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.creates(), [])

    def test_a_comparison_without_targets_is_refused(self):
        self.file.write_text(json.dumps({"comparison": {}}), encoding="utf-8")
        res = self.run_script(check=False)
        self.assertEqual(res.returncode, 2)
        self.assertIn("--targets", res.stderr)


if __name__ == "__main__":
    unittest.main()
