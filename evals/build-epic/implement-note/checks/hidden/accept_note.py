"""Task 02-01's acceptance criteria, from outside: `todo note ID TEXT` through the CLI, and `add_note` where the
task names the clock it stamps with."""
from datetime import datetime, timedelta, timezone

from acceptlib import CliTest

UTC = timezone.utc
AT = "2026-11-01T09:00:00+00:00"


class Note(CliTest):
    def setUp(self):
        super().setUp()
        self.ok("add", "milk")
        self.ok("add", "eggs")

    def test_note_prints_noted_and_stores_a_utc_stamped_note(self):
        start = datetime.now(UTC) - timedelta(seconds=5)
        self.assertEqual(self.ok("note", "2", "free range"), ["noted 2"])
        (note,) = self.row(2)["notes"]
        self.assertEqual(note["text"], "free range")
        at = datetime.fromisoformat(note["at"])
        self.assertEqual(at.utcoffset(), timedelta(0))
        self.assertTrue(start <= at <= datetime.now(UTC) + timedelta(seconds=5), at)
        self.assertNotIn("notes", self.row(1))

    def test_notes_keep_their_order_and_the_item_stamp_moves(self):
        before = datetime.fromisoformat(self.row(1)["updated_at"])
        self.ok("note", "1", "first")
        self.ok("note", "1", "second")
        notes = self.row(1)["notes"]
        self.assertEqual([n["text"] for n in notes], ["first", "second"])
        first, second = (datetime.fromisoformat(n["at"]) for n in notes)
        self.assertLessEqual(first, second)
        self.assertGreater(datetime.fromisoformat(self.row(1)["updated_at"]), before)

    def test_add_note_stamps_with_the_store_clock(self):
        from todo.storage import JsonStore
        from todo.storage.notes import add_note
        given = []

        def clock():
            given.append(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=len(given)))
            return given[-1]
        store = JsonStore(self.file, clock=clock)
        add_note(store, 1, "first")
        add_note(store, 1, "second")
        (item, _) = store.load()
        self.assertEqual([n.text for n in item.notes], ["first", "second"])
        first, second = (n.at for n in item.notes)
        self.assertIn(first, given)
        self.assertIn(second, given)
        self.assertLess(first, second)

    def test_non_printable_characters_are_stored_as_question_marks(self):
        self.ok("note", "1", "a\x07b\x1bc")
        self.assertEqual(self.row(1)["notes"][0]["text"], "a?b?c")

    def test_a_note_of_1000_characters_is_kept(self):
        self.ok("note", "1", "x" * 1000)
        self.assertEqual(len(self.row(1)["notes"][0]["text"]), 1000)

    def test_unknown_id_exits_2_and_saves_nothing(self):
        err = self.refused("note", "9", "hello")
        self.assertIn("todo: error: no item 9", err)

    def test_empty_or_over_long_text_exits_2_and_saves_nothing(self):
        for text in ("", "x" * 1001):
            with self.subTest(length=len(text)):
                self.refused("note", "1", text)

    def test_the_101st_note_exits_2_and_saves_nothing(self):
        rows = self.rows()
        rows[0]["notes"] = [{"at": AT, "text": f"n{i}"} for i in range(100)]
        self.write_rows(rows)
        self.refused("note", "1", "one too many")

    def test_a_bad_todo_file_still_exits_1(self):
        self.write_rows([{"id": 1, "title": "milk", "notes": "not a list"}])
        self.refused("note", "1", "hello", code=1)
        self.file.write_text("{not json")
        self.refused("note", "1", "hello", code=1)
