"""Task 01-01's acceptance criteria, from outside: commands are found from their own modules, in name order."""
import re

from acceptlib import CliTest

NAMES = ["add", "done", "export", "list", "priority", "remind", "search", "skip", "stop", "sync", "tag"]


class CommandDiscovery(CliTest):
    def help_choices(self, repo=None):
        got = self.ok("-h", repo=repo)
        found = re.search(r"\{([a-z_,]+)\}", "\n".join(got))
        self.assertIsNotNone(found, got)
        return found.group(1).split(",")

    def test_help_lists_the_eleven_commands_in_name_order(self):
        self.assertEqual(self.help_choices(), NAMES)

    def test_existing_commands_still_run(self):
        self.assertEqual(self.ok("add", "milk", "--due", "2026-11-01", "--tag", "shop"), ["added 1: milk"])
        self.assertEqual(self.ok("add", "eggs", "--every", "week", "--due", "2026-11-02"), ["added 2: eggs"])
        self.ok("priority", "1", "high")
        self.ok("tag", "1", "+food")
        self.assertEqual(len(self.ok("list")), 2)
        self.assertEqual(self.ok("search", "milk"), self.ok("list", "--search", "milk"))
        self.ok("skip", "2")
        self.ok("stop", "2")
        self.ok("done", "1")
        self.assertEqual(self.ok("export", "--format", "csv")[0], "id,title,done,due,tags,priority,every")
        self.ok("remind")

    def test_a_new_module_is_a_command_with_no_other_edit(self):
        repo = self.copy_of_work()
        (repo / "todo/cli/commands/hello_cmd.py").write_text(
            "def register(parser):\n    parser.add_argument('who')\n\n\n"
            "def run(args, ctx):\n    ctx.out(f'hello {args.who}')\n")
        self.assertEqual(self.ok("hello", "you", repo=repo), ["hello you"])
        self.assertEqual(self.help_choices(repo), sorted(NAMES + ["hello"]))

    def test_a_module_without_register_and_run_fails_at_import_naming_it(self):
        repo = self.copy_of_work()
        (repo / "todo/cli/commands/broken.py").write_text("def register(parser):\n    pass\n")
        got = self.todo("list", repo=repo)
        self.assertNotEqual(got.returncode, 0, got.stdout)
        self.assertIn("broken", got.stderr)
