"""Tests for `compare_runs.py`: the two-run comparison, checked against the gh-35 comparison it was ported from."""
import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "compare_runs.py"
GH35 = HERE / "fixtures" / "gh-35"
RUN_METRICS = HERE / "fixtures" / "run-folder" / "expected.json"
TARGETS = HERE / "targets.json"
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


COMMITTED = {}  # compare_runs.py's output per args naming only committed files: those runs repeat across cases


def compare(args):
    """compare_runs.py's output for `args`, a fresh copy each call."""
    key = tuple(args)
    committed = all(a.startswith("--") or not Path(a).is_absolute() or HERE in Path(a).parents for a in args)
    if not committed or key not in COMMITTED:
        res = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True,
                             encoding="utf-8", stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            raise AssertionError(f"exit {res.returncode}: {res.stderr}")
        if not committed:
            return json.loads(res.stdout)
        COMMITTED[key] = res.stdout
    return json.loads(COMMITTED[key])


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
            # Added beside them too: the Claude Code versions, which gh-35's telemetry and metrics predate.
            self.assertEqual(run.pop("claudeVersions"), {"local": None, "remote": None}, name)
        self.assertEqual(actual["comparison"].pop("claudeVersions"),
                         {name: {"local": None, "remote": None} for name in ("remote", "local")})
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

    def test_each_run_names_its_local_and_remote_claude_code_versions(self):
        run = json.loads((GH35 / "local-telemetry.json").read_text(encoding="utf-8"))
        run["claudeVersions"] = ["2.1.319"]
        tel = self.tmp / "tel.json"
        tel.write_text(json.dumps(run), encoding="utf-8")
        out = compare(gh35_args(**{"b": tel, "b-metrics": RUN_METRICS}))
        want = {"local": ["2.1.319"], "remote": {"9.0.1": 1, "9.0.2": 1, "unknown": 1}}
        self.assertEqual(out["runs"]["local"]["claudeVersions"], want)
        self.assertEqual(out["comparison"]["claudeVersions"],
                         {"remote": {"local": None, "remote": None}, "local": want})

    def test_saved_active_time_agents_lead_time_and_interruptions_are_compared(self):
        run = json.loads((GH35 / "local-telemetry.json").read_text(encoding="utf-8"))
        span = next(s for s in run["spans"] if s["skill"] == "build-epic")
        span.update(activeSeconds=5400, agentsAtOnce={"peak": 3, "average": 1.5}, humanMessages=4)
        span["tools"]["AskUserQuestion"] = 2
        tel = self.tmp / "tel.json"
        tel.write_text(json.dumps(run), encoding="utf-8")
        met = json.loads(RUN_METRICS.read_text(encoding="utf-8"))
        last = (datetime.fromisoformat(span["start"].replace("Z", "+00:00")) + timedelta(minutes=200)).isoformat()
        met["lead_time"] = {"epic": 5, "epic_opened": "2026-11-01T00:00:00+00:00", "last_ticket_merged": last,
                            "hours": 30.5, "unmerged_tickets": []}
        metrics = self.tmp / "met.json"
        metrics.write_text(json.dumps(met), encoding="utf-8")
        out = compare(gh35_args(**{"b": tel, "b-metrics": metrics}))
        local = out["runs"]["local"]
        self.assertEqual((local["time"]["activeMinutes"], local["time"]["agentsAtOnce"]["average"]), (90.0, 1.5))
        self.assertEqual((local["time"]["leadTimeHours"], local["time"]["runStartToLastTicketMergeMinutes"]),
                         (30.5, 200.0))
        # Four typed answers and two questions asked.
        self.assertEqual(local["outcomes"]["interruptions"], 6)
        self.assertEqual(out["comparison"]["outcomes"]["interruptions"]["local"], 6)
        self.assertEqual(out["comparison"]["time"]["leadTimeHours"]["local"], 30.5)
        # Saved before schemaVersion 5: unknown, never 0.
        self.assertIsNone(out["runs"]["remote"]["outcomes"]["interruptions"])
        self.assertIsNone(out["runs"]["remote"]["time"]["leadTimeHours"])

    def test_per_ticket_time_keeps_only_active_time_and_waits_on_the_human_stand_alone(self):
        run = json.loads((GH35 / "local-telemetry.json").read_text(encoding="utf-8"))
        span = next(s for s in run["spans"] if s["skill"] == "build-epic")
        # Work 10:00-10:30, an overnight wait for the human, work again 08:00-08:20 the next day.
        span.update(activeSeconds=50 * 60, humanWaitSeconds=(21 * 60 + 30) * 60,
                    activeIntervals=[["2026-11-01T10:00:00.000Z", "2026-11-01T10:30:00.000Z"],
                                     ["2026-11-02T08:00:00.000Z", "2026-11-02T08:20:00.000Z"]],
                    humanWaits=[{"answered": "2026-11-02T08:00:00.000Z", "seconds": (21 * 60 + 30) * 60}])
        tel = self.tmp / "tel-active.json"
        tel.write_text(json.dumps(run), encoding="utf-8")
        met = json.loads(RUN_METRICS.read_text(encoding="utf-8"))
        met["per_ticket"] = {
            # Opened 10:10, merged 08:10 the next day: 22 h of wall clock, 20 + 10 minutes of work.
            "01": {"first_task_pr_opened": "2026-11-01T10:10:00+00:00", "ticket_merged": "2026-11-02T08:10:00+00:00",
                   "wall_minutes_first_task_pr_to_ticket_merge": 1320.0},
            "02": {"first_task_pr_opened": "2026-11-01T10:00:00+00:00", "ticket_merged": "2026-11-01T10:20:00+00:00",
                   "wall_minutes_first_task_pr_to_ticket_merge": 20.0},
            "03": {"first_task_pr_opened": None, "ticket_merged": None,
                   "wall_minutes_first_task_pr_to_ticket_merge": None}}
        metrics = self.tmp / "met-active.json"
        metrics.write_text(json.dumps(met), encoding="utf-8")
        out = compare(gh35_args(**{"b": tel, "b-metrics": metrics}))
        local = out["runs"]["local"]
        self.assertEqual([t["active_minutes"] for t in local["perTicket"].values()], [30.0, 20.0, None])
        self.assertEqual((local["time"]["perTicketActiveMinutes"]["max"], local["time"]["perTicketWallMinutes"]["max"]),
                         (30.0, 1320.0))
        self.assertEqual(local["time"]["humanWaitMinutes"], 1290.0)
        self.assertEqual(local["time"]["humanWaits"], [{"answered": "2026-11-02T08:00:00.000Z", "minutes": 1290.0}])
        self.assertEqual(out["comparison"]["time"]["perTicketActiveMaxMinutes"]["local"], 30.0)
        self.assertEqual(out["comparison"]["time"]["humanWaitMinutes"]["local"], 1290.0)
        # Saved before schemaVersion 6: unknown, never 0.
        self.assertIsNone(out["runs"]["remote"]["time"]["perTicketActiveMinutes"])
        self.assertIsNone(out["runs"]["remote"]["time"]["humanWaitMinutes"])

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


class Targets(unittest.TestCase):
    """`--targets`: the judged run (`--b`) against each #181 target."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write(self, name, data):
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def metrics(self, **change):
        met = json.loads(RUN_METRICS.read_text(encoding="utf-8"))
        met.update(change)
        return self.write("metrics.json", met)

    def test_without_targets_the_comparison_has_no_targets(self):
        self.assertNotIn("targets", compare(gh35_args())["comparison"])

    def test_every_target_on_gh35_has_a_status_threshold_op_and_value(self):
        targets = compare(gh35_args() + ["--targets", str(TARGETS)])["comparison"]["targets"]
        want = json.loads(TARGETS.read_text(encoding="utf-8"))["targets"]
        self.assertEqual(sorted(targets), sorted(t["id"] for t in want))
        for t in want:
            got = targets[t["id"]]
            self.assertIn(got["status"], ("met", "missed", "unknown"), t["id"])
            self.assertEqual((got["threshold"], got["op"], got["run"]), (t["threshold"], t["op"], "local"), t["id"])
            self.assertIn("value", got)
            self.assertEqual(got["status"] == "unknown", got["value"] is None, t["id"])

    def test_gh35_metrics_lack_the_new_inputs_so_those_targets_are_unknown(self):
        targets = compare(gh35_args() + ["--targets", str(TARGETS)])["comparison"]["targets"]
        for key in ("hand-written-scripts", "human-round-trips", "suites-per-merge"):
            self.assertEqual((targets[key]["value"], targets[key]["status"]), (None, "unknown"), key)
        # The local run started no remote run: none killed, none without cost.
        self.assertEqual(targets["remote-memory-kills"]["status"], "met")
        self.assertEqual(targets["remote-cost-measured"]["status"], "met")

    def test_null_round_trips_and_scripts_are_unknown_never_missed(self):
        args = gh35_args(**{"b-metrics": self.metrics(human_round_trips=None, hand_written_scripts=None)})
        targets = compare(args + ["--targets", str(TARGETS)])["comparison"]["targets"]
        self.assertEqual(targets["human-round-trips"]["status"], "unknown")
        self.assertEqual(targets["hand-written-scripts"]["status"], "unknown")

    def test_round_trips_and_scripts_are_judged_from_the_new_fields(self):
        missed = compare(gh35_args(**{"b-metrics": RUN_METRICS}) + ["--targets", str(TARGETS)])
        met = compare(gh35_args(**{"b-metrics": self.metrics(human_round_trips=0, hand_written_scripts=0)})
                      + ["--targets", str(TARGETS)])
        for key in ("human-round-trips", "hand-written-scripts"):
            self.assertEqual(missed["comparison"]["targets"][key]["value"], 2)
            self.assertEqual(missed["comparison"]["targets"][key]["status"], "missed", key)
            self.assertEqual(met["comparison"]["targets"][key]["status"], "met", key)

    def test_remote_kills_cost_measured_and_suites_per_merge_come_from_run_metrics(self):
        args = gh35_args(**{"b-metrics": RUN_METRICS}) + ["--targets", str(TARGETS)]
        targets = compare(args)["comparison"]["targets"]
        # 1 run killed by memory then by timeout, 1 run without cost, 3 merged task PRs with 3 saved suite files.
        self.assertEqual((targets["remote-memory-kills"]["value"], targets["remote-memory-kills"]["status"]),
                         (1, "missed"))
        self.assertEqual((targets["remote-cost-measured"]["value"], targets["remote-cost-measured"]["status"]),
                         (False, "missed"))
        self.assertEqual((targets["suites-per-merge"]["value"], targets["suites-per-merge"]["status"]), (1.0, "met"))

    def test_ratios_are_per_ticket_of_the_judged_run(self):
        out = compare(gh35_args() + ["--targets", str(TARGETS)])
        run, targets = out["runs"]["local"], out["comparison"]["targets"]
        tickets = run["outcomes"]["tickets"]["count"]
        opus = sum(1 for s in run["subagentList"] if s["role"] in ("review", "ticket-review", "re-review", "security")
                   and any("opus" in m for m in s["models"]))
        self.assertGreater(opus, 0)
        self.assertEqual(targets["opus-reviewer-runs-per-ticket"]["value"], round(opus / tickets, 2))
        self.assertEqual(targets["orchestrator-turns-per-ticket"]["value"],
                         round(run["orchestrator"]["turns"] / tickets, 2))
        self.assertEqual(targets["conflict-resolvers"]["value"],
                         (run["subagents"]["byRole"].get("resolve") or {}).get("count", 0))
        done = 78  # gh-35's local run: tasks.by_status.done
        self.assertEqual(targets["orchestrator-cost-per-task"]["value"], round(run["cost"]["orchestratorUSD"] / done, 2))

    def test_a_threshold_is_judged_by_its_op(self):
        out = compare(gh35_args() + ["--targets", str(TARGETS)])
        value = out["comparison"]["targets"]["orchestrator-turns-per-ticket"]["value"]
        cases = [("lt", value, "missed"), ("le", value, "met"), ("eq", value, "met"), ("lt", value + 1, "met"),
                 ("eq", value + 1, "missed")]
        path = self.write("targets.json", {"targets": [
            {"id": f"t{i}", "metric": "orchestratorTurnsPerTicket", "op": op, "threshold": th, "unit": "turns",
             "source": "test"} for i, (op, th, _) in enumerate(cases)]})
        targets = compare(gh35_args() + ["--targets", str(path)])["comparison"]["targets"]
        self.assertEqual([targets[f"t{i}"]["status"] for i in range(len(cases))], [want for *_, want in cases])

    def test_an_unknown_metric_or_op_is_refused(self):
        for bad in ({"metric": "noSuchMetric", "op": "lt"}, {"metric": "orchestratorUSD", "op": "gt"}):
            path = self.write("targets.json", {"targets": [{"id": "x", "threshold": 1, "unit": "", "source": "",
                                                            **bad}]})
            res = subprocess.run([sys.executable, "-I", str(SCRIPT), *gh35_args(), "--targets", str(path)],
                                 capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
            self.assertEqual(res.returncode, 2, bad)
            self.assertIn(next(iter(bad.values())), res.stderr)

    def test_the_cost_target_judges_measured_cost_when_every_remote_run_has_one(self):
        out = compare(gh35_args() + ["--targets", str(TARGETS)])
        cost = out["comparison"]["targets"]["total-cost-per-task"]
        self.assertEqual(cost["basis"], "measured")
        self.assertEqual(cost["value"], round(out["runs"]["local"]["cost"]["recomputedSessionUSD"] / 78, 2))
        self.assertNotIn("range", cost)

    def test_the_cost_target_names_the_estimate_it_judged_when_remote_runs_lack_cost(self):
        # Judge the remote run: 106 remote runs without cost, priced from the local run's implementers.
        args = gh35_args(**{"a": GH35 / "local-telemetry.json", "a-metrics": GH35 / "local-metrics.json",
                            "a-branch": GH35 / "local-branch.json", "a-name": "local",
                            "b": GH35 / "remote-telemetry.json", "b-metrics": GH35 / "remote-metrics.json",
                            "b-branch": GH35 / "remote-branch.json", "b-name": "remote"})
        out = compare(args + ["--targets", str(TARGETS)])
        cost, estimate = (out["comparison"]["targets"]["total-cost-per-task"],
                          out["runs"]["remote"]["cost"]["totalEstimateUSD"])
        done = 99  # gh-35's remote run: tasks.by_status.done
        self.assertEqual(cost["basis"], "estimate-mid")
        self.assertEqual(cost["value"], round(estimate["mid"] / done, 2))
        self.assertEqual(cost["range"], [round(estimate["low"] / done, 2), round(estimate["high"] / done, 2)])
        self.assertEqual(cost["run"], "remote")
        # gh-35's metrics name no kills, and none of its remote runs has a cost.
        kills, measured = (out["comparison"]["targets"][k] for k in ("remote-memory-kills", "remote-cost-measured"))
        self.assertEqual((kills["value"], kills["status"]), (None, "unknown"))
        self.assertEqual((measured["value"], measured["status"]), (False, "missed"))

    def test_the_cost_target_is_unknown_when_no_estimate_can_be_made(self):
        args = gh35_args(**{"b-metrics": RUN_METRICS}) + ["--targets", str(TARGETS)]
        targets = compare(args)["comparison"]["targets"]
        cost = targets["total-cost-per-task"]
        self.assertEqual(cost["basis"], "estimate-mid")
        self.assertEqual((cost["value"], cost["status"]), (None, "unknown"))

    def test_remote_reviewers_count_as_reviewer_runs(self):
        base = compare(gh35_args() + ["--targets", str(TARGETS)])
        metrics = json.loads((GH35 / "local-metrics.json").read_text(encoding="utf-8"))
        metrics["remote"] = {"runs": 4, "runs_without_cost": 0, "cost_usd": 4.0, "by_role": {
            "review": {"count": 2, "models": {"claude-opus-5-5": 1, "claude-sonnet-5-5": 1}},
            "security": {"count": 1, "models": {"claude-opus-5-5": 1}},
            "task": {"count": 1, "models": {"claude-opus-5-5": 1}}}}
        got = compare(gh35_args(**{"b-metrics": self.write("metrics.json", metrics)}) + ["--targets", str(TARGETS)])
        tickets = base["runs"]["local"]["outcomes"]["tickets"]["count"]
        before, after = (X["comparison"]["targets"] for X in (base, got))
        runs = ("reviewer-runs-per-ticket", "opus-reviewer-runs-per-ticket")
        for key, added in zip(runs, (3, 2)):
            want = round(before[key]["value"] * tickets + added, 0) / tickets
            self.assertAlmostEqual(after[key]["value"], want, delta=0.01, msg=key)
        self.assertAlmostEqual(got["runs"]["local"]["outcomes"]["reviewerRunsPerTicket"],
                               after["reviewer-runs-per-ticket"]["value"], delta=0.001)

    def test_per_task_costs_are_unknown_without_done_tasks(self):
        metrics = json.loads((GH35 / "local-metrics.json").read_text(encoding="utf-8"))
        metrics["tasks"]["by_status"] = {"ready": 3}
        path = self.write("metrics.json", metrics)
        targets = compare(gh35_args(**{"b-metrics": path}) + ["--targets", str(TARGETS)])["comparison"]["targets"]
        for key in ("total-cost-per-task", "orchestrator-cost-per-task"):
            self.assertEqual((targets[key]["value"], targets[key]["status"]), (None, "unknown"), key)


if __name__ == "__main__":
    unittest.main()
