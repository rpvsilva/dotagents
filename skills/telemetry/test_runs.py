import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runs import run_date, saved_runs, skill_matches


def run(session, start, **extra):
    return {"schemaVersion": 3, "sessionId": session, "start": start, "spans": [], **extra}


def write(folder, name, content):
    folder.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (folder / name).write_text(text, encoding="utf-8")


class SavedRuns(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def ids(self, runs):
        return [(r["sessionId"], r.get("label")) for r in runs]

    def test_reads_the_machine_folder_and_the_repos_folder(self):
        home, repo = self.tmp / "home", self.tmp / "repo"
        write(home / ".agents" / "telemetry", "a.json", run("a", "2026-10-01T10:00:00.000Z"))
        write(repo / ".agents" / "telemetry", "b.json", run("b", "2026-10-02T10:00:00.000Z"))
        (repo / "sub").mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        cwd = os.getcwd()
        os.chdir(repo / "sub")
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            runs, skipped = saved_runs()
        self.assertEqual(self.ids(runs), [("a", None), ("b", None)])
        self.assertEqual(skipped, 0)

    def test_given_folders_replace_the_default_ones(self):
        home = self.tmp / "home"
        write(home / ".agents" / "telemetry", "a.json", run("a", "2026-10-01T10:00:00.000Z"))
        write(self.tmp / "d1", "b.json", run("b", "2026-10-02T10:00:00.000Z"))
        write(self.tmp / "d2", "c.json", run("c", "2026-10-03T10:00:00.000Z"))
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            runs, _ = saved_runs([self.tmp / "d1", self.tmp / "d2", self.tmp / "missing"])
        self.assertEqual(self.ids(runs), [("b", None), ("c", None)])

    def test_skips_and_counts_bad_json_and_runs_without_spans(self):
        folder = self.tmp / "d"
        write(folder, "good.json", run("a", "2026-10-01T10:00:00.000Z"))
        write(folder, "bad.json", '{"sessionId": "b", "spans": [')
        write(folder, "nospans.json", {"sessionId": "c", "start": "2026-10-01T10:00:00.000Z"})
        write(folder, "list.json", [1, 2])
        write(folder, "notes.txt", "not a run")
        runs, skipped = saved_runs([folder])
        self.assertEqual(self.ids(runs), [("a", None)])
        self.assertEqual(skipped, 3)

    def test_the_automatic_save_of_a_session_wins_over_a_manual_one(self):
        write(self.tmp / "home", "s.json", run("s", "2026-10-01T10:00:00.000Z", whole=True, label="auto"))
        write(self.tmp / "repo", "fix.json", run("s", "2026-10-01T09:00:00.000Z", label="manual"))
        runs, _ = saved_runs([self.tmp / "repo", self.tmp / "home"])
        self.assertEqual(self.ids(runs), [("s", "auto")])

    def test_of_two_manual_saves_of_a_session_the_earliest_wins(self):
        folder = self.tmp / "d"
        write(folder, "late.json", run("s", "2026-10-01T11:00:00.000Z", label="late"))
        write(folder, "early.json", run("s", "2026-10-01T10:00:00.000Z", label="early"))
        write(folder, "zlate.json", run("s", "2026-10-01T12:00:00.000Z", label="later"))
        runs, _ = saved_runs([folder])
        self.assertEqual(self.ids(runs), [("s", "early")])

    def test_runs_are_sorted_by_start(self):
        folder = self.tmp / "d"
        write(folder, "a.json", run("a", "2026-10-03T10:00:00.000Z"))
        write(folder, "b.json", run("b", "2026-10-01T10:00:00.000Z"))
        write(folder, "c.json", run("c", "2026-10-02T10:00:00.000Z"))
        runs, _ = saved_runs([folder])
        self.assertEqual([r["sessionId"] for r in runs], ["b", "c", "a"])


class RunDate(unittest.TestCase):
    def test_uses_the_runs_date_over_its_start(self):
        self.assertEqual(run_date(run("a", "2026-10-01T23:30:00.000Z", date="2026-10-02")), "2026-10-02")

    def test_a_version_2_run_without_date_is_dated_from_its_start(self):
        old = {"schemaVersion": 2, "sessionId": "a", "start": "2026-09-30T08:00:00.000Z", "spans": []}
        self.assertEqual(run_date(old), "2026-09-30")


class SkillMatches(unittest.TestCase):
    def test_a_plugin_skill_matches_its_short_name(self):
        self.assertTrue(skill_matches("plugin:renovate-prs", "renovate-prs"))

    def test_a_skill_matches_its_own_name(self):
        self.assertTrue(skill_matches("renovate-prs", "renovate-prs"))
        self.assertTrue(skill_matches("plugin:renovate-prs", "plugin:renovate-prs"))

    def test_a_different_skill_does_not_match(self):
        self.assertFalse(skill_matches("plugin:renovate-prs", "prs"))
        self.assertFalse(skill_matches("renovate-prs", "plugin:renovate-prs"))

    def test_no_skill_matches_only_activity_outside_any_skill(self):
        self.assertTrue(skill_matches(None, "no skill"))
        self.assertFalse(skill_matches(None, "renovate-prs"))
        self.assertFalse(skill_matches("renovate-prs", "no skill"))


if __name__ == "__main__":
    unittest.main()
