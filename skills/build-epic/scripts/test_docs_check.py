"""Tests for `docs_check.py` and the docs check `ship_ticket.py open` runs, on a throwaway README and a stub command."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import runfolder  # noqa: E402
from testkit import SCRIPTS, Kit  # noqa: E402

DOCS_CHECK = SCRIPTS / "docs_check.py"
SHIP = SCRIPTS / "ship_ticket.py"
REPO = SCRIPTS.parents[2]

REGISTERED = ["status", "run", "--force", "--json"]
README = """# Tool

Some words: status of things.

## Usage

- `status [--json]`: prints the status.
- `run --force <task>`: runs one task.

## Other

- `gone`: a command named outside the usage section does not count.
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.kit = kit = Kit()
        self.addCleanup(kit.close)
        kit.env.pop("XDG_CONFIG_HOME", None)
        self.clone = kit.make_origin()
        (kit.home / ".agents" / "bin").mkdir(parents=True)
        (kit.home / ".agents" / "bin" / "settings").symlink_to(REPO / "scripts" / "settings.sh")
        kit.init_run()

    def register(self, names, rc=0):
        """Sets `docs_commands` to a stub printing `names`, one per line, and exiting `rc`."""
        stub = self.kit.tmp / "list-commands"
        stub.write_text("#!/bin/sh\n" + "".join(f"echo '{n}'\n" for n in names) + f"exit {rc}\n", encoding="utf-8")
        stub.chmod(0o755)
        self.set_setting("docs_commands", str(stub))

    def set_setting(self, key, value):
        p = self.kit.tool(self.kit.home / ".agents/bin/settings", "set", "build-epic", key, value, "--machine")
        self.assertEqual(p.returncode, 0, p.stderr)

    def readme(self, text, at=None):
        (at or self.clone).joinpath("README.md").write_text(text, encoding="utf-8")

    def one_line(self, p):
        self.assertEqual(p.stdout.count("\n"), 1, p.stdout + p.stderr)
        line = p.stdout.rstrip("\n")
        self.assertEqual(p.returncode, 0 if line.startswith("OK ") else 1, line)
        return line


class DocsCheckTest(Base):
    def check(self, *args):
        return self.one_line(self.kit.step(DOCS_CHECK, self.kit.run_dir, *args))

    def test_unset_docs_commands_skips_the_check(self):
        self.readme("# Tool\n")
        self.assertEqual(self.check(), "OK run docs-check skipped")

    def test_a_readme_naming_every_command_and_flag_passes(self):
        self.register(REGISTERED)
        self.readme(README)
        self.assertEqual(self.check(self.clone), "OK run docs-check commands=4")

    def test_the_worktree_defaults_to_the_runs_repo(self):
        self.register(REGISTERED)
        self.readme(README)
        self.assertEqual(self.check(), "OK run docs-check commands=4")

    def test_a_flag_missing_from_the_usage_section_fails_missing(self):
        self.register(REGISTERED + ["--verbose", "init"])
        self.readme(README)
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-drift missing=--verbose,init \(log: ")

    def test_a_removed_command_still_named_fails_unknown(self):
        self.register(["status", "--force", "--json"])
        self.readme(README)
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-drift unknown=run \(log: ")

    def test_both_drifts_are_named_together(self):
        self.register(["status", "run", "--json", "--quiet"])
        self.readme(README)
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-drift missing=--quiet unknown=--force \(log: ")

    def test_a_name_only_outside_the_section_counts_as_missing(self):
        self.register(REGISTERED + ["gone"])
        self.readme(README)
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-drift missing=gone \(log: ")

    def test_the_section_follows_docs_readme_section(self):
        self.register(["gone"])
        self.readme(README)
        self.set_setting("docs_readme_section", "Other")
        self.assertEqual(self.check(self.clone), "OK run docs-check commands=1")

    def test_a_readme_without_the_section_fails(self):
        self.register(REGISTERED)
        self.readme("# Tool\n\n## Use\n\n`status`\n")
        self.assertRegex(self.check(self.clone), r"^FAIL run no-readme-section: Usage \(log: ")

    def test_a_failing_or_silent_command_fails(self):
        self.readme(README)
        self.register(REGISTERED, rc=3)
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-command \(log: ")
        self.register([])
        self.assertRegex(self.check(self.clone), r"^FAIL run docs-command \(log: ")


TICKET = "# 01: Ship JSON things\nStatus: building\nIssue: #7\nBranch: feat/t\nBlocked by: none\n"
TASK = "# 01-01: task\nStatus: done\nTicket: 01\nBlocked by: none\nWrites: x\nPR: https://github.com/o/r/pull/5\n"


class ShipOpenDocsTest(Base):
    def setUp(self):
        super().setUp()
        kit = self.kit
        self.readme(README)
        kit.git("commit", "-qam", "usage", cwd=self.clone)
        kit.git("push", "-q", "origin", "main", "main:feat/t", cwd=self.clone)
        kit.git("fetch", "-q", "origin", cwd=self.clone)
        for d in ("tickets", "tasks", "worktrees"):
            (kit.run_dir / d).mkdir()
        self.ticket = kit.run_dir / "tickets" / "01-t.md"
        self.ticket.write_text(TICKET, encoding="utf-8")
        (kit.run_dir / "tasks" / "01-01-a.md").write_text(TASK, encoding="utf-8")
        runfolder.set_header(self.ticket, "PR", "https://github.com/o/r/pull/20")

    def ship(self):
        return self.one_line(self.kit.step(SHIP, self.kit.run_dir, "01", "open"))

    def sent(self):
        return [c for c in self.kit.gh_calls() if c[:2] in (["pr", "edit"], ["pr", "ready"], ["pr", "create"])]

    def test_open_refuses_on_drift_and_sends_nothing(self):
        self.register(REGISTERED + ["--verbose"])
        self.assertRegex(self.ship(), r"^FAIL 01 docs-drift missing=--verbose \(log: ")
        self.assertEqual(self.sent(), [])

    def test_open_checks_the_ticket_head(self):
        self.register(REGISTERED)
        self.readme("# Tool\n")  # the main checkout's README is not the ticket's
        self.assertEqual(self.ship(), "OK 01 open pr=20")

    def test_open_passes_with_docs_commands_unset(self):
        self.assertEqual(self.ship(), "OK 01 open pr=20")
        self.assertEqual([c[:3] for c in self.sent()], [["pr", "edit", "20"], ["pr", "ready", "20"]])


if __name__ == "__main__":
    unittest.main()
