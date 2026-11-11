"""Task 04-01's acceptance criteria, from outside: `todo block ID --on OTHER` and `todo unblock ID --on OTHER`."""
from acceptlib import CliTest


class BlockUnblock(CliTest):
    def setUp(self):
        super().setUp()
        for title in ("milk", "eggs", "bread"):
            self.ok("add", title)

    def test_block_makes_an_item_wait_and_unblock_removes_it(self):
        before = self.row(2)["updated_at"]
        self.assertEqual(self.ok("block", "2", "--on", "1"), ["blocked 2 on 1"])
        self.assertEqual(self.row(2).get("blocked_on"), [1])
        self.assertGreater(self.row(2)["updated_at"], before)
        self.assertEqual(self.ok("block", "2", "--on", "3"), ["blocked 2 on 3"])
        self.assertEqual(sorted(self.row(2)["blocked_on"]), [1, 3])
        self.assertEqual(self.ok("unblock", "2", "--on", "1"), ["unblocked 2 from 1"])
        self.assertEqual(self.row(2).get("blocked_on"), [3])
        self.ok("unblock", "2", "--on", "3")
        self.assertFalse(self.row(2).get("blocked_on"))

    def test_blocking_twice_prints_the_same_line_and_saves_nothing(self):
        self.ok("block", "2", "--on", "1")
        before = self.file.read_bytes()
        self.assertEqual(self.ok("block", "2", "--on", "1"), ["blocked 2 on 1"])
        self.assertEqual(self.file.read_bytes(), before)

    def test_unblocking_an_absent_pair_prints_the_line_and_saves_nothing(self):
        before = self.file.read_bytes()
        self.assertEqual(self.ok("unblock", "2", "--on", "1"), ["unblocked 2 from 1"])
        self.assertEqual(self.file.read_bytes(), before)

    def test_blocking_an_item_on_itself_exits_2(self):
        self.refused("block", "1", "--on", "1")

    def test_a_direct_cycle_exits_2(self):
        self.ok("block", "2", "--on", "1")
        self.refused("block", "1", "--on", "2")

    def test_a_cycle_through_another_item_exits_2(self):
        self.ok("block", "3", "--on", "2")
        self.ok("block", "2", "--on", "1")
        self.refused("block", "1", "--on", "3")

    def test_a_cycle_already_in_the_file_does_not_hang_block(self):
        rows = self.rows()
        rows[0]["blocked_on"], rows[1]["blocked_on"] = [2], [1]
        self.write_rows(rows)
        got = self.todo("block", "3", "--on", "1", timeout=20)
        self.assertEqual((got.returncode, got.stdout.splitlines()), (0, ["blocked 3 on 1"]), got.stderr)
        self.refused("block", "1", "--on", "3")

    def test_unknown_ids_exit_2_naming_the_id(self):
        for args, missing in ((("block", "9", "--on", "1"), 9), (("block", "1", "--on", "8"), 8),
                              (("unblock", "7", "--on", "1"), 7)):
            with self.subTest(args=args):
                self.assertIn(f"todo: error: no item {missing}", self.refused(*args))

    def test_on_is_required_and_a_whole_number(self):
        self.refused("block", "1")
        self.refused("unblock", "1")
        self.refused("block", "1", "--on", "x")
