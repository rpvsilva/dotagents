"""Tests for the `build-epic` pilot cases: each case's checks pass on a hand-built good run folder and fail on an
empty one, and each check fails when the part of the plan it covers is broken."""
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CASES = HERE / "build-epic"
sys.path.insert(0, str(HERE / "_runner"))

import cases  # noqa: E402
import checks  # noqa: E402
import home  # noqa: E402
from testlib import EvalSandbox  # noqa: E402

PASS_A = ("pass-a-remove", "pass-a-rename")
PASS_B = ("pass-b-top",)

SPEC = """# Spec: tally remove

## Goal

`tally remove <name>` takes a count out of the store.

## Behaviours

1. (epic) `tally remove <name>` deletes the count and prints `removed <name>`.
2. (epic) Removing a name the store does not hold prints `tally: no count named <name>` and exits 1.

## Out of scope

- Removing several names at once.

## Open questions

1. Does `remove` ask before deleting? [Assumed: no; the other commands never ask.]
"""

AREAS = "# Areas\n\nno exploration needed: the source the epic touches is under 200 lines.\n"

MAP = """# Map: tally top

## Area: cli

- `tally/cli.py`: add `top`. Tests: `tests/cli_test.py`.

## Checks

```
capped --mb 2000 --secs 300 -- python3 -m unittest discover -s tests -p '*_test.py'
```

Setup: none

## Invariants

- Errors go to stderr as `tally: <message>` and exit 1 (`AGENTS.md:8`).

## Field matrix

None: the epic adds no command that creates, copies or moves counts.

## Test isolation

- `TALLY_FILE`: set to a temp file by `TallyTest.setUp` in `tests/cli_test.py`.

## Gaps

- None.

## Lanes

- Lane 1: 01.

Hub files: none
"""

TICKET = """# 01: tally top
Status: ready
Issue: none
Branch: feat/tally-top
Blocked by: none
"""

TASK = """# 01-01: tally top prints the highest counts
Status: ready
Ticket: 01
Blocked by: none
Writes: tally/cli.py, tests/cli_test.py, README.md
Env: TALLY_FILE

## What to build
`tally top <n>` prints the `n` highest counts.

## Acceptance criteria
- [ ] `tally top 2` prints the first two lines of `tally list`.

## Check
`tests/cli_test.py`.

## Pointers
Spec behaviours 1-4.
"""


def report(status):
    return f"# Report\n\nWrote the plan.\n\n```\nstatus: {status}\nchanged: spec.md\nfollow-up:\n```\n"


def write_good(name, scratch):
    """The run folder a good planner run of case `name` leaves."""
    (scratch / "reports").mkdir(parents=True, exist_ok=True)
    if name in PASS_A:
        (scratch / "spec.md").write_text(SPEC)
        (scratch / "areas.md").write_text(AREAS)
        (scratch / "reports" / "plan-A.md").write_text(report("done"))
        return
    (scratch / "notes").mkdir(exist_ok=True)
    (scratch / "tickets").mkdir(exist_ok=True)
    (scratch / "tasks").mkdir(exist_ok=True)
    (scratch / "spec.md").write_text(SPEC)
    (scratch / "areas.md").write_text(AREAS)
    (scratch / "map.md").write_text(MAP)
    (scratch / "tickets" / "01-tally-top.md").write_text(TICKET)
    (scratch / "tasks" / "01-01-top.md").write_text(TASK)
    (scratch / "reports" / "plan-B.md").write_text(report("done"))


class PilotBuildEpicTest(EvalSandbox):
    def run_case(self, name, good=True, breaks=None, checkout=REPO):
        """`{check: passed}` for case `name` on a fresh run folder: the fixture repo as `work/`, and `scratch/`
        filled by `write_good` when `good`, then changed by `breaks(paths)` when given."""
        case = cases.load(CASES / name)
        root = self.tmp / name
        paths = {"work": root / "work", "scratch": root / "scratch", "input": root / "input",
                 "home": root / "home", "transcript": root / "transcript.jsonl", "gh_calls": root / "gh-calls",
                 "checkout": checkout}
        home.make_work(case["fixture"], paths["work"])
        shutil.copytree(case["input"], paths["input"])
        for key in ("scratch", "home"):
            paths[key].mkdir()
        paths["transcript"].write_text("")
        paths["gh_calls"].write_text("")
        if good:
            write_good(name, paths["scratch"])
        if breaks:
            breaks(paths)
        return checks.run(case["checks"], paths)

    def assert_fails_only(self, name, check, breaks):
        result = self.run_case(name, breaks=breaks)
        self.assertEqual([c for c, ok in result.items() if not ok], [check], name)

    def test_cases_are_well_formed(self):
        self.assertEqual(cases.check(HERE), [])
        self.assertEqual(sorted(p.name for p in CASES.iterdir()), sorted(PASS_A + PASS_B))

    def test_pass_a_cases_check_spec_areas_report_and_repo(self):
        for name in PASS_A:
            self.assertEqual(set(cases.load(CASES / name)["checks"]),
                             {"spec-sections", "areas-written", "report-done", "work-unchanged"})

    def test_pass_b_case_checks_plan_map_report_and_repo(self):
        self.assertEqual(set(cases.load(CASES / "pass-b-top")["checks"]),
                         {"plan-ok", "map-sections", "report-done", "work-unchanged"})

    def test_every_case_passes_on_a_good_run_folder(self):
        for name in PASS_A + PASS_B:
            result = self.run_case(name)
            self.assertTrue(all(result.values()), f"{name}: {result}")

    def test_every_case_fails_on_an_empty_run_folder(self):
        for name in PASS_A + PASS_B:
            result = self.run_case(name, good=False)
            failed = {c for c, ok in result.items() if not ok}
            self.assertEqual(failed, set(result) - {"work-unchanged", checks.NO_GITHUB}, name)

    def test_work_unchanged_fails_when_the_planner_edits_or_adds_a_repo_file(self):
        def edit(paths):
            with (paths["work"] / "README.md").open("a") as f:
                f.write("planned\n")

        def add(paths):
            (paths["work"] / "plan.md").write_text("a plan in the repo\n")

        for name in ("pass-a-remove", "pass-b-top"):
            self.assert_fails_only(name, "work-unchanged", edit)
            shutil.rmtree(self.tmp / name)
            self.assert_fails_only(name, "work-unchanged", add)
            shutil.rmtree(self.tmp / name)

    def test_work_unchanged_fails_on_pass_a_rename_when_the_planner_edits_or_adds_a_repo_file(self):
        def edit(paths):
            with (paths["work"] / "README.md").open("a") as f:
                f.write("planned\n")

        self.assert_fails_only("pass-a-rename", "work-unchanged", edit)
        shutil.rmtree(self.tmp / "pass-a-rename")
        self.assert_fails_only("pass-a-rename", "work-unchanged",
                               lambda paths: (paths["work"] / "plan.md").write_text("a plan in the repo\n"))

    def test_spec_sections_fails_on_a_missing_heading_or_no_numbered_behaviour(self):
        for heading in ("## Goal", "## Behaviours", "## Out of scope", "## Open questions"):
            def drop(paths, heading=heading):
                spec = paths["scratch"] / "spec.md"
                spec.write_text(spec.read_text().replace(heading + "\n", ""))
            self.assert_fails_only("pass-a-remove", "spec-sections", drop)
            shutil.rmtree(self.tmp / "pass-a-remove")

        def unnumbered(paths):
            spec = paths["scratch"] / "spec.md"
            spec.write_text(spec.read_text().replace("1. (epic)", "- (epic)").replace("2. (epic)", "- (epic)"))

        self.assert_fails_only("pass-a-remove", "spec-sections", unnumbered)

    def test_spec_sections_fails_on_pass_a_rename_with_a_missing_heading_or_no_numbered_behaviour(self):
        def unnumbered(paths):
            spec = paths["scratch"] / "spec.md"
            spec.write_text(spec.read_text().replace("1. (epic)", "- (epic)").replace("2. (epic)", "- (epic)"))

        for heading in ("## Goal", "## Behaviours", "## Out of scope", "## Open questions"):
            def drop(paths, heading=heading):
                spec = paths["scratch"] / "spec.md"
                spec.write_text(spec.read_text().replace(heading + "\n", ""))
            self.assert_fails_only("pass-a-rename", "spec-sections", drop)
            shutil.rmtree(self.tmp / "pass-a-rename")
        self.assert_fails_only("pass-a-rename", "spec-sections", unnumbered)

    def test_areas_written_fails_on_an_empty_areas_file(self):
        self.assert_fails_only("pass-a-rename", "areas-written",
                               lambda paths: (paths["scratch"] / "areas.md").write_text(""))

    def test_areas_written_fails_on_pass_a_remove_with_an_empty_areas_file(self):
        self.assert_fails_only("pass-a-remove", "areas-written",
                               lambda paths: (paths["scratch"] / "areas.md").write_text(""))

    def test_report_done_fails_unless_the_report_ends_with_status_done(self):
        for name, file in (("pass-a-remove", "plan-A.md"), ("pass-b-top", "plan-B.md")):
            self.assert_fails_only(name, "report-done",
                                   lambda paths, f=file: (paths["scratch"] / "reports" / f).write_text(
                                       report("blocked")))
            shutil.rmtree(self.tmp / name)
            self.assert_fails_only(name, "report-done",
                                   lambda paths, f=file: (paths["scratch"] / "reports" / f).write_text(
                                       report("done") + "\n".join(["more text"] * 10) + "\n"))
            shutil.rmtree(self.tmp / name)

    def test_report_done_fails_on_pass_a_rename_unless_the_report_ends_with_status_done(self):
        reports = lambda paths: paths["scratch"] / "reports" / "plan-A.md"  # noqa: E731
        self.assert_fails_only("pass-a-rename", "report-done",
                               lambda paths: reports(paths).write_text(report("blocked")))
        shutil.rmtree(self.tmp / "pass-a-rename")
        self.assert_fails_only("pass-a-rename", "report-done",
                               lambda paths: reports(paths).write_text(
                                   report("done") + "\n".join(["more text"] * 10) + "\n"))

    def test_plan_ok_fails_on_a_plan_check_problem_or_no_ticket_or_task(self):
        def no_writes(paths):
            task = paths["scratch"] / "tasks" / "01-01-top.md"
            task.write_text(task.read_text().replace("Writes: tally/cli.py, tests/cli_test.py, README.md\n", ""))

        def no_tasks(paths):
            os.remove(paths["scratch"] / "tasks" / "01-01-top.md")

        def no_tickets(paths):
            os.remove(paths["scratch"] / "tickets" / "01-tally-top.md")

        for breaks in (no_writes, no_tasks, no_tickets):
            self.assert_fails_only("pass-b-top", "plan-ok", breaks)
            shutil.rmtree(self.tmp / "pass-b-top")

    def test_plan_ok_fails_when_plan_check_does_not_print_plan_ok(self):
        checkout = self.tmp / "checkout"
        script = checkout / "skills" / "build-epic" / "plan_check.py"
        script.parent.mkdir(parents=True)
        script.write_text("print('plan has problems')\n")
        result = self.run_case("pass-b-top", checkout=checkout)
        self.assertEqual([c for c, ok in result.items() if not ok], ["plan-ok"])

    def test_map_sections_fails_on_a_missing_section_setup_or_hub_files_line(self):
        cuts = ["## " + s + "\n" for s in ("Checks", "Invariants", "Field matrix", "Test isolation", "Gaps",
                                           "Lanes")]
        for cut in cuts + ["Setup: none\n", "Hub files: none\n"]:
            def drop(paths, cut=cut):
                path = paths["scratch"] / "map.md"
                path.write_text(path.read_text().replace(cut, ""))
            result = self.run_case("pass-b-top", breaks=drop)
            self.assertFalse(result["map-sections"], cut)
            shutil.rmtree(self.tmp / "pass-b-top")

    def test_fixtures_are_tiny_repos_with_rules_ci_and_a_test_not_named_test_py(self):
        for name in PASS_A + PASS_B:
            fixture = CASES / name / "fixture"
            files = [p for p in fixture.rglob("*") if p.is_file()]
            self.assertLess(sum(len(p.read_text().splitlines()) for p in files), 2000, name)
            self.assertTrue((fixture / "AGENTS.md").is_file(), name)
            self.assertTrue(list((fixture / ".github" / "workflows").glob("*.yml")), name)
            self.assertTrue(list(fixture.glob("tests/*_test.py")), name)


if __name__ == "__main__":
    unittest.main()
