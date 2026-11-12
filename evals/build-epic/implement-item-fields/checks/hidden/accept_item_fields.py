"""Task 01-02's acceptance criteria, from outside: the new item fields through `todo.storage`'s exports, the
JSON file a user (or a remote) holds, and the `todo` CLI reading it."""
import json
from datetime import date, datetime, timedelta, timezone

from acceptlib import CliTest

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 10)


def storage():
    import todo.storage as s
    return s


class ItemFields(CliTest):
    def store(self, path=None, clock=None):
        return storage().JsonStore(path or self.file, clock=clock)

    def test_storage_exports_the_new_names(self):
        s = storage()
        self.assertEqual((s.MAX_NOTES, s.MAX_NOTE_CHARS), (100, 1000))
        for name in ("Note", "normalize_note", "is_snoozed", "waiting_on"):
            self.assertTrue(callable(getattr(s, name)), name)

    def test_normalize_note_marks_non_printables_and_bounds_length(self):
        s = storage()
        self.assertEqual(s.normalize_note("a\x07b"), "a?b")
        self.assertEqual(s.normalize_note("x" * 1000), "x" * 1000)
        for bad in ("", "x" * 1001):
            with self.assertRaises(ValueError):
                s.normalize_note(bad)

    def test_note_is_frozen(self):
        s = storage()
        note = s.Note(at=T0, text="hi")
        with self.assertRaises(Exception):
            note.text = "other"

    def test_each_new_field_round_trips_through_the_file(self):
        s = storage()
        items = [s.Item(1, "trip"),
                 s.Item(2, "pack", parent=1, notes=[s.Note(at=T0, text="socks"), s.Note(at=T1, text="hat")],
                        snoozed_until=date(2026, 12, 1), blocked_on=[1])]
        self.store().save(items)
        loaded = {i.id: i for i in self.store().load()}
        got = loaded[2]
        self.assertEqual((got.parent, got.snoozed_until, got.blocked_on), (1, date(2026, 12, 1), [1]))
        self.assertEqual([(n.at, n.text) for n in got.notes], [(T0, "socks"), (T1, "hat")])
        row = self.row(2)
        self.assertEqual(row["snoozed_until"], "2026-12-01")
        self.assertEqual([n["text"] for n in row["notes"]], ["socks", "hat"])
        for n, want in zip(row["notes"], (T0, T1)):
            self.assertEqual(set(n), {"at", "text"})
            at = datetime.fromisoformat(n["at"])
            self.assertEqual((at, at.utcoffset()), (want, timedelta(0)))

    def test_a_row_using_no_new_field_keeps_its_old_shape(self):
        s = storage()
        self.store().save([s.Item(1, "milk")])
        self.assertFalse({"parent", "notes", "snoozed_until", "blocked_on"} & set(self.row(1)))

    def test_the_cli_reads_a_file_holding_the_new_fields(self):
        self.write_rows([
            {"id": 1, "title": "trip"},
            {"id": 2, "title": "pack", "parent": 1, "notes": [{"at": "2026-11-01T09:00:00+00:00", "text": "socks"}],
             "snoozed_until": "2026-12-01", "blocked_on": [1]},
        ])
        lines = self.ok("list")
        self.assertEqual(len(lines), 2, lines)

    def test_duplicate_blocks_load_once_and_a_naive_note_time_is_utc(self):
        self.write_rows([
            {"id": 1, "title": "a"}, {"id": 2, "title": "b"},
            {"id": 3, "title": "c", "blocked_on": [2, 1, 2], "notes": [{"at": "2026-11-01T09:00:00", "text": "n"}]},
        ])
        got = {i.id: i for i in self.store().load()}[3]
        self.assertEqual(got.blocked_on, [2, 1])
        self.assertEqual(got.notes[0].at, T0)

    def test_each_hostile_value_fails_naming_the_item(self):
        at = "2026-11-01T09:00:00+00:00"
        bad = {
            "parent": ["2", True, 0, 2**53 + 1, 7],
            "notes": ["x", {"at": at, "text": "a"}, [{"at": at, "text": "a"}] * 101, [5],
                      [{"at": at, "text": "a", "extra": 1}], [{"text": "a"}], [{"at": "soon", "text": "a"}],
                      [{"at": at, "text": ""}], [{"at": at, "text": "x" * 1001}], [{"at": at, "text": 5}]],
            "snoozed_until": ["2026-13-01", "tomorrow", 20261201],
            "blocked_on": [3, ["1"], [True], [0], [7], [2**53 + 1]],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=json.dumps(value)[:60]):
                    self.write_rows([{"id": 3, "title": "ok"}, {"id": 7, "title": "bad", key: value}])
                    err = self.refused("list", code=1)
                    self.assertIn("item 7", err)
                    with self.assertRaises(storage().StoreError):
                        self.store().load()

    def test_update_stamps_and_saves_only_when_the_change_says_so(self):
        s = storage()
        store = self.store(clock=lambda: T1)
        store.save([s.Item(1, "milk", updated_at=T0)])
        before = self.file.read_bytes()
        with self.assertRaises(s.ItemNotFound):
            store.update(9, lambda item, items: True)
        self.assertEqual(store.update(1, lambda item, items: False).id, 1)
        self.assertEqual(self.file.read_bytes(), before)

        def boom(item, items):
            item.title = "changed"
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            store.update(1, boom)
        self.assertEqual(self.file.read_bytes(), before)

        def rename(item, items):
            self.assertEqual([i.id for i in items], [1])
            item.title = "oat milk"
            return True
        store.update(1, rename)
        (got,) = self.store().load()
        self.assertEqual((got.title, got.updated_at), ("oat milk", T1))

    def test_is_snoozed_only_for_an_open_item_before_its_date(self):
        s = storage()
        until = date(2026, 11, 12)
        self.assertTrue(s.is_snoozed(s.Item(1, "a", snoozed_until=until), TODAY))
        self.assertFalse(s.is_snoozed(s.Item(1, "a", snoozed_until=until), until))
        self.assertFalse(s.is_snoozed(s.Item(1, "a", snoozed_until=until, done=True), TODAY))
        self.assertFalse(s.is_snoozed(s.Item(1, "a"), TODAY))

    def test_waiting_on_names_open_existing_blockers_in_order(self):
        s = storage()
        items = [s.Item(3, "c"), s.Item(4, "d", done=True), s.Item(5, "e")]
        item = s.Item(1, "a", blocked_on=[5, 9, 4, 3])
        self.assertEqual(s.waiting_on(item, items + [item]), [3, 5])
        self.assertEqual(s.waiting_on(s.Item(1, "a", done=True, blocked_on=[3]), items), [])

    def recurring(self):
        s = storage()
        store = self.store()
        store.add("parent")
        store.add("blocker")
        store.add("water plants", due=TODAY, every="week")
        items = store.load()
        item = items[2]
        item.parent, item.blocked_on = 1, [2]
        item.notes = [s.Note(at=T0, text="south window")]
        item.snoozed_until = TODAY + timedelta(days=2)
        store.save(items)
        return store

    def test_done_follows_the_field_matrix(self):
        nxt = self.recurring().complete(3, today=TODAY)
        done = {i.id: i for i in self.store().load()}[3]
        self.assertEqual((done.done, done.snoozed_until), (True, None))
        self.assertEqual((nxt.parent, nxt.blocked_on, nxt.notes, nxt.snoozed_until), (1, [2], [], None))
        self.assertEqual({i.id: i for i in self.store().load()}[nxt.id].blocked_on, [2])

    def test_skip_follows_the_field_matrix(self):
        self.recurring().skip(3, today=TODAY)
        got = {i.id: i for i in self.store().load()}[3]
        self.assertEqual((got.parent, got.blocked_on, got.snoozed_until), (1, [2], None))
        self.assertEqual([n.text for n in got.notes], ["south window"])

    def test_sync_keeps_the_later_sides_new_fields(self):
        s = storage()
        from todo.sync.sync import sync
        remote_file = self.tmp / "remote.json"
        local, remote = self.store(clock=lambda: T1), self.store(remote_file, clock=lambda: T1)
        local.save([s.Item(1, "a", updated_at=T0), s.Item(2, "b", updated_at=T0)])
        remote.save([s.Item(1, "a", updated_at=T0),
                     s.Item(2, "b", updated_at=T1, parent=1, notes=[s.Note(at=T1, text="later")],
                            snoozed_until=date(2026, 12, 1), blocked_on=[1])])
        sync(local, remote)
        for store in (self.store(), self.store(remote_file)):
            got = {i.id: i for i in store.load()}[2]
            self.assertEqual((got.parent, got.snoozed_until, got.blocked_on, [n.text for n in got.notes]),
                             (1, date(2026, 12, 1), [1], ["later"]))
