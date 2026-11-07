"""Tests for `compare_runs.py`: the two-run comparison, checked against the gh-35 comparison it was ported from."""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "compare_runs.py"
GH35 = HERE / "fixtures" / "gh-35"
RUN_METRICS = HERE / "fixtures" / "run-folder" / "expected.json"
# gh-35's role names, as spec Q14 maps them; the others kept their name.
OLD_ROLES = {"task-review": "review", "tuner-review": "review", "conflict-resolver": "resolve",
             "security-review": "security"}
GH35_ROLES = ("task-review", "ticket-review", "re-review", "tuner-review", "security-review", "implement",
              "conflict-resolver", "plan", "explore")
ROLES = ("implement", "review", "re-review", "ticket-review", "security", "fix", "resolve", "tune", "plan", "explore",
         "other")


def gh35_args(**override):
    files = {"a": GH35 / "remote-telemetry.json", "a-metrics": GH35 / "remote-metrics.json",
             "a-branch": GH35 / "remote-branch.json", "a-name": "remote",
             "b": GH35 / "local-telemetry.json", "b-metrics": GH35 / "local-metrics.json",
             "b-branch": GH35 / "local-branch.json", "b-name": "local", "pricing": GH35 / "pricing.json"}
    files.update(override)
    return [arg for key, value in files.items() if value is not None for arg in (f"--{key}", str(value))]


def compare(args):
    res = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise AssertionError(f"exit {res.returncode}: {res.stderr}")
    return json.loads(res.stdout)


def first_difference(actual, expected, path="$"):
    """The path of the first value that differs, or None."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        for key in sorted(set(actual) | set(expected)):
            if key not in actual or key not in expected:
                return f"{path}.{key} ({'missing' if key not in actual else 'unexpected'})"
            found = first_difference(actual[key], expected[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return f"{path} (length {len(actual)} != {len(expected)})"
        for i, (a, e) in enumerate(zip(actual, expected)):
            found = first_difference(a, e, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if actual == expected and type(actual) is type(expected) else f"{path}: {actual!r} != {expected!r}"


def regroup(subagents):
    """count, costUSD and toolCalls per Q14 role, from gh-35's per-subagent list."""
    groups = defaultdict(list)
    for agent in subagents:
        groups[OLD_ROLES.get(agent["role"], agent["role"])].append(agent)
    return {role: {"count": len(items), "costUSD": round(sum(a["costUSD"] for a in items), 2),
                   "toolCalls": sum(a["toolCalls"] for a in items)} for role, items in groups.items()}


class Gh35(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual = compare(gh35_args())
        cls.expected = json.loads((GH35 / "expected.json").read_text(encoding="utf-8"))

    def test_numeric_sections_equal_the_gh35_comparison_under_the_q14_roles(self):
        actual, expected = json.loads(json.dumps(self.actual)), json.loads(json.dumps(self.expected))
        for name in ("remote", "local"):
            run, want = actual["runs"][name], expected["runs"][name]
            for agent in want["subagentList"] + want["subagents"]["costliest"]:
                agent["role"] = OLD_ROLES.get(agent["role"], agent["role"])
            groups = regroup(want["subagentList"])
            got = {role: {k: v[k] for k in ("count", "costUSD", "toolCalls")}
                   for role, v in run["subagents"].pop("byRole").items()}
            self.assertEqual(got, groups, name)
            want["subagents"].pop("byRole")
            # Added beside the ported figures: the measured cost of remote runs, none in gh-35's metrics.
            self.assertIsNone(run["cost"].pop("remoteMeasuredUSD"))
        subs, want_subs = actual["comparison"]["subagents"], expected["comparison"]["subagents"]
        for role in ROLES:
            for field, metric in (("count", subs.pop(f"{role}Runs")), ("costUSD", subs.pop(f"{role}USD"))):
                for name in ("remote", "local"):
                    group = regroup(expected["runs"][name]["subagentList"]).get(role, {})
                    self.assertEqual(metric[name], group.get(field, 0), f"{role} {field} {name}")
        for role in GH35_ROLES:
            want_subs.pop(f"{role}Runs")
            want_subs.pop(f"{role}USD")
        self.assertIsNone(first_difference(actual, expected))

    def test_the_plans_role_figures_hold(self):
        remote = self.actual["runs"]["remote"]["subagents"]["byRole"]
        local = self.actual["runs"]["local"]["subagents"]["byRole"]
        self.assertEqual(round(sum(remote[r]["costUSD"] for r in ("review", "ticket-review", "re-review")), 1), 47.6)
        self.assertEqual(round(local["re-review"]["costUSD"], 1), 15.2)
        self.assertEqual(round(local["implement"]["costUSD"], 1), 28.9)

    def test_the_remote_run_has_an_estimate_for_its_unmeasured_remote_runs(self):
        remote, local = self.actual["runs"]["remote"]["cost"], self.actual["runs"]["local"]["cost"]
        self.assertEqual(remote["unmeasuredRemoteRuns"]["runs"], 106)
        self.assertGreater(remote["totalEstimateUSD"]["mid"], remote["recomputedSessionUSD"])
        self.assertIsNone(local["unmeasuredRemoteRuns"])

    def test_the_output_has_no_prose(self):
        self.assertEqual(sorted(self.actual), ["comparison", "pricing", "runs", "schema"])
        self.assertEqual(self.actual["schema"], "build-epic-run-comparison/v1")
        self.assertNotIn("description", self.actual["runs"]["remote"])
        self.assertNotIn("method", self.actual["runs"]["remote"]["cost"]["unmeasuredRemoteRuns"])
        self.assertNotIn("note", self.actual["comparison"]["cost"]["measuredUSD"])


class Fixture(unittest.TestCase):
    def test_the_committed_fixture_holds_no_home_path(self):
        files = sorted(GH35.iterdir())
        self.assertTrue(files)
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, path.name)
            self.assertNotIn("-Users-", text, path.name)


class Runs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_default_names_are_a_and_b(self):
        out = compare(gh35_args(**{"a-name": None, "b-name": None}))
        self.assertEqual(sorted(out["runs"]), ["a", "b"])
        turns = out["comparison"]["orchestrator"]["turns"]
        self.assertEqual(turns["bMinusA"], turns["b"] - turns["a"])
        self.assertIn("bVsAPct", turns)

    def test_a_saved_role_wins_over_the_description(self):
        run = json.loads((GH35 / "local-telemetry.json").read_text(encoding="utf-8"))
        span = next(s for s in run["spans"] if s["skill"] == "build-epic")
        for agent in span["subagents"]:
            if agent["type"] == "epic-implementer":
                agent["role"] = "fix"
        path = self.tmp / "local.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        roles = compare(gh35_args(b=path))["runs"]["local"]["subagents"]["byRole"]
        self.assertNotIn("implement", roles)
        self.assertEqual(round(roles["fix"]["costUSD"], 1), 28.9)

    def test_measured_remote_cost_counts_in_the_total_and_only_runs_without_cost_are_estimated(self):
        # run_metrics.py's output: 3 remote runs, $3.75 measured, 1 without cost.
        # Priced from the other run's implementers, which only the local run has.
        out = compare(gh35_args(**{"a-metrics": RUN_METRICS}))
        cost = out["runs"]["remote"]["cost"]
        self.assertEqual(cost["remoteMeasuredUSD"], 3.75)
        self.assertEqual(cost["unmeasuredRemoteRuns"]["runs"], 1)
        basis = cost["unmeasuredRemoteRuns"]["basisPerRunUSD"]["median"]
        self.assertAlmostEqual(cost["totalEstimateUSD"]["mid"], cost["recomputedSessionUSD"] + 3.75 + basis, places=1)
        self.assertEqual(out["comparison"]["subagents"]["remoteAgentRuns"]["remote"], 3)

    def test_runs_without_cost_and_no_implementers_to_price_them_have_no_estimate(self):
        cost = compare(gh35_args(**{"b-metrics": RUN_METRICS}))["runs"]["local"]["cost"]
        self.assertEqual(cost["unmeasuredRemoteRuns"]["runs"], 1)
        self.assertIsNone(cost["unmeasuredRemoteRuns"]["estimateUSD"])
        self.assertIsNone(cost["totalEstimateUSD"])

    def test_a_run_without_branch_measurements_has_no_final_branch_figures(self):
        out = compare(gh35_args(**{"b-branch": None}))
        self.assertIsNone(out["runs"]["local"]["finalBranch"])
        self.assertIsNone(out["comparison"]["finalBranch"]["testsPassed"]["local"])

    def test_a_model_missing_from_the_pricing_compares_with_null_cost(self):
        pricing = json.loads((GH35 / "pricing.json").read_text(encoding="utf-8"))
        del pricing["models"]["claude-sonnet-5-5"]
        path = self.tmp / "pricing.json"
        path.write_text(json.dumps(pricing), encoding="utf-8")
        out = compare(gh35_args(pricing=path))
        cost = out["runs"]["local"]["cost"]
        self.assertIsNone(cost["byModelUSD"]["claude-sonnet-5-5"])
        self.assertGreater(cost["byModelUSD"]["claude-opus-5-5"], 0)
        self.assertIsNone(cost["recomputedSessionUSD"])
        self.assertIsNone(cost["totalEstimateUSD"])
        self.assertIsNone(cost["byTokenKindUSD"]["output"])
        subs = out["runs"]["local"]["subagentList"]
        self.assertTrue(all(s["costUSD"] is None for s in subs if "claude-sonnet-5-5" in s["models"]))
        self.assertTrue(all(s["costUSD"] > 0 for s in subs if s["models"] == ["claude-opus-5-5"]))
        self.assertIsNone(out["comparison"]["cost"]["measuredUSD"]["local"])
        self.assertNotIn("better", out["comparison"]["cost"]["measuredUSD"])
        self.assertIsNone(out["pricing"]["models"]["claude-sonnet-5-5"])

    def test_roles_without_a_saved_role_follow_the_report_rule(self):
        run = json.loads((GH35 / "local-telemetry.json").read_text(encoding="utf-8"))
        span = next(s for s in run["spans"] if s["skill"] == "build-epic")
        extra = [("test-tuner", "Tune 01 tests"), ("pr-fixer", "Fix PR 12 checks"),
                 ("general-purpose", "Security review of PR 12")]
        template = next(a for a in span["subagents"] if a["type"] == "epic-implementer")
        for t, d in extra:
            span["subagents"].append({**template, "type": t, "description": d})
        path = self.tmp / "local.json"
        path.write_text(json.dumps(run), encoding="utf-8")
        roles = compare(gh35_args(b=path))["runs"]["local"]["subagents"]["byRole"]
        self.assertEqual((roles["tune"]["count"], roles["fix"]["count"], roles["security"]["count"] - 4), (1, 1, 1))

    def test_the_same_name_twice_is_refused(self):
        res = subprocess.run([sys.executable, "-I", str(SCRIPT), *gh35_args(**{"b-name": "remote"})],
                             capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
        self.assertEqual(res.returncode, 2)
        self.assertIn("differ", res.stderr)


if __name__ == "__main__":
    unittest.main()
