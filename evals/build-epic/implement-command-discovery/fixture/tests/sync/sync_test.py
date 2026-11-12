import shutil
from datetime import date, datetime, timezone

import pytest

from todo.storage import Item, JsonStore, StoreError
from todo.sync import merge, sync
from todo.sync.remote import remote_store


def at(day):
    return datetime(2026, 11, day, 12, tzinfo=timezone.utc)


def test_merge_union_of_new_items():
    result = merge([Item(1, "a", updated_at=at(1))], [Item(2, "b", updated_at=at(1))])
    assert [i.title for i in result.items] == ["a", "b"]
    assert result.pulled == [2] and result.pushed == [1]


def test_merge_later_write_wins_each_way():
    local = [Item(1, "a", done=True, updated_at=at(3)), Item(2, "b", updated_at=at(1))]
    remote = [Item(1, "a", updated_at=at(2)), Item(2, "b2", updated_at=at(4))]
    result = merge(local, remote)
    assert [(i.title, i.done) for i in result.items] == [("a", True), ("b2", False)]
    assert result.pulled == [2] and result.pushed == [1]


def test_merge_tie_keeps_local():
    result = merge([Item(1, "mine", updated_at=at(1))], [Item(1, "theirs", updated_at=at(1))])
    assert result.items[0].title == "mine"
    assert result.pushed == [1] and result.pulled == []


def test_merge_unstamped_loses():
    result = merge([Item(1, "old")], [Item(1, "new", updated_at=at(1))])
    assert result.items[0].title == "new"


def test_merge_identical_is_noop():
    items = [Item(1, "a", updated_at=at(1))]
    result = merge(items, list(items))
    assert result.pulled == [] and result.pushed == []


def test_sync_writes_both_sides(tmp_path):
    local = JsonStore(tmp_path / "local.json", clock=lambda: at(1))
    remote = JsonStore(tmp_path / "remote.json", clock=lambda: at(2))
    local.add("milk")
    remote.add("milk")
    remote.complete(1)
    local_only = Item(2, "eggs", updated_at=at(3))
    local.save(local.load() + [local_only])

    sync(local, remote)

    assert local.load() == remote.load()
    assert [(i.title, i.done) for i in local.load()] == [("milk", True), ("eggs", False)]


def test_sync_creates_missing_remote(tmp_path):
    local = JsonStore(tmp_path / "local.json")
    local.add("milk")
    result = sync(local, JsonStore(tmp_path / "remote.json"))
    assert result.pushed == [1]
    assert [i.title for i in JsonStore(tmp_path / "remote.json").load()] == ["milk"]


def test_sync_bad_remote_touches_nothing(tmp_path):
    local = JsonStore(tmp_path / "local.json")
    local.add("milk")
    before = (tmp_path / "local.json").read_text()
    (tmp_path / "remote.json").write_text('[{"id": 1, "title": "x", "due": "soon"}]')
    with pytest.raises(StoreError):
        sync(local, JsonStore(tmp_path / "remote.json"))
    assert (tmp_path / "local.json").read_text() == before


def test_cli_sync(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_REMOTE", str(tmp_path / "remote.json"))
    cli("add", "milk")
    assert cli("sync") == ["pulled 0, pushed 1"]
    assert cli("sync") == ["pulled 0, pushed 0"]
    later = datetime(2100, 1, 1, tzinfo=timezone.utc)
    JsonStore(tmp_path / "remote.json", clock=lambda: later).complete(1)
    assert cli("sync") == ["pulled 1, pushed 0"]
    assert cli("list") == ["[x] 1: milk"]


def test_cli_sync_without_remote(cli, monkeypatch):
    monkeypatch.delenv("TODO_REMOTE", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli("sync")
    assert "TODO_REMOTE" in str(exc.value.code)


# Tags and priority travel with the item (behaviours 24-25).


def tagged(title, day, tags=(), priority=None):
    return Item(1, title, updated_at=at(day), tags=list(tags), priority=priority)


@pytest.mark.parametrize("newer_side", ["local", "remote"])
def test_merge_newer_side_wins_whole_item_with_tags_and_priority(newer_side):
    old = tagged("a", 1, ["home", "work"], "low")
    new = tagged("a2", 2, ["work"], "high")
    local, remote = (new, old) if newer_side == "local" else (old, new)

    result = merge([local], [remote])

    assert result.items == [new]
    if newer_side == "local":
        assert result.pushed == [1] and result.pulled == []
    else:
        assert result.pulled == [1] and result.pushed == []


def test_merge_tie_keeps_local_tags_and_priority():
    mine = tagged("a", 1, ["mine"], "high")
    theirs = tagged("a", 1, ["theirs"], "low")
    result = merge([mine], [theirs])
    assert result.items == [mine]
    assert result.pushed == [1] and result.pulled == []


def test_merge_identical_tagged_items_is_noop():
    result = merge([tagged("a", 1, ["x", "y"], "medium")], [tagged("a", 1, ["x", "y"], "medium")])
    assert result.pulled == [] and result.pushed == []


def test_sync_json_stores_match_with_tags_and_priority(tmp_path):
    local = JsonStore(tmp_path / "local.json", clock=lambda: at(1))
    remote = JsonStore(tmp_path / "remote.json", clock=lambda: at(2))
    local.add("milk", tags=["groceries"], priority="low")
    remote.add("milk", tags=["groceries", "home"], priority="high")  # newer
    local.save(local.load() + [Item(2, "rent", updated_at=at(3), tags=["bills"], priority="medium")])

    sync(local, remote)

    assert local.load() == remote.load()
    assert [(i.title, i.tags, i.priority) for i in remote.load()] == [
        ("milk", ["groceries", "home"], "high"),
        ("rent", ["bills"], "medium"),
    ]


def test_sync_old_format_remote(tmp_path):
    (tmp_path / "remote.json").write_text(
        '[{"id": 1, "title": "old", "done": false, "due": null,'
        ' "updated_at": "2026-11-05T12:00:00+00:00"}]'
    )
    local = JsonStore(tmp_path / "local.json")
    local.save([Item(2, "new", updated_at=at(1), tags=["work"], priority="high")])
    remote = JsonStore(tmp_path / "remote.json")

    result = sync(local, remote)

    assert result.pulled == [1] and result.pushed == [2]
    assert local.load() == remote.load()
    assert [(i.title, i.tags, i.priority) for i in remote.load()] == [
        ("old", [], None),
        ("new", ["work"], "high"),
    ]


def test_cli_tag_then_sync_pushes_tags(cli, tmp_path, monkeypatch):
    remote_file = tmp_path / "remote.json"
    monkeypatch.setenv("TODO_REMOTE", str(remote_file))
    cli("add", "milk")
    cli("sync")
    cli("tag", "1", "+work")
    assert cli("sync") == ["pulled 0, pushed 1"]
    assert JsonStore(remote_file).load()[0].tags == ["work"]
    assert '"work"' in remote_file.read_text()


# Recurring items: one next occurrence after sync (behaviours 16-17, spec decision 1).
# Both sides start from the same file so item ids line up (SUP-4118 is out of scope).

WEEK_START = date(2026, 11, 10)


def occurrence(item_id, occ, day, done=False, series="s1"):
    return Item(item_id, "water", done=done, due=WEEK_START, updated_at=at(day),
                every="week", series_id=series, occurrence=occ)


def shared_pair(tmp_path, local_clock, remote_clock):
    """Two JsonStores holding the same recurring item, as after a sync."""
    JsonStore(tmp_path / "local.json", clock=lambda: at(1)).add(
        "water", due=WEEK_START, every="week"
    )
    shutil.copy(tmp_path / "local.json", tmp_path / "remote.json")
    return (
        JsonStore(tmp_path / "local.json", clock=local_clock),
        JsonStore(tmp_path / "remote.json", clock=remote_clock),
    )


def open_items(store):
    return [i for i in store.load() if not i.done]


def test_merge_drops_duplicate_occurrence_keeping_latest_change():
    local = [occurrence(1, 1, 1, done=True), occurrence(3, 2, 2)]
    remote = [occurrence(1, 1, 1, done=True), occurrence(2, 2, 3)]
    result = merge(local, remote)
    assert [(i.id, i.occurrence, i.done) for i in result.items] == [(1, 1, True), (2, 2, False)]
    assert result.pulled == [2, 3]  # 2 arrives locally, 3 is removed locally
    assert result.pushed == []


def test_merge_keeps_occurrences_of_other_series_and_one_offs():
    local = [occurrence(1, 2, 1, series="a"), Item(3, "milk", updated_at=at(1))]
    remote = [occurrence(2, 2, 2, series="b"), Item(4, "eggs", updated_at=at(1))]
    assert [i.id for i in merge(local, remote).items] == [1, 2, 3, 4]


def test_merge_removed_duplicate_counts_as_a_change_on_its_side():
    local = [occurrence(2, 2, 3)]
    remote = [occurrence(2, 2, 3), occurrence(5, 2, 2)]
    result = merge(local, remote)
    assert [i.id for i in result.items] == [2]
    assert result.pushed == [5] and result.pulled == []


def test_sync_both_sides_complete_offline_keeps_one_next_occurrence(tmp_path):
    local, remote = shared_pair(tmp_path, lambda: at(2), lambda: at(3))
    local.complete(1, today=WEEK_START)
    remote.complete(1, today=WEEK_START)

    sync(local, remote)
    sync(remote, local)

    assert local.load() == remote.load()
    done, = [i for i in local.load() if i.done]
    nxt, = open_items(local)
    assert done.id == 1
    assert (nxt.occurrence, nxt.updated_at) == (2, at(3))
    assert nxt.series_id == done.series_id


@pytest.mark.parametrize("skipper_is_local", [True, False])
def test_sync_skip_later_than_complete_keeps_the_skipped_item(tmp_path, skipper_is_local):
    local, remote = shared_pair(tmp_path, lambda: at(2), lambda: at(2))
    skipper, completer = (local, remote) if skipper_is_local else (remote, local)
    skipper.clock = lambda: at(3)  # the skip is the later change
    completer.complete(1, today=WEEK_START)
    skipper.skip(1, today=WEEK_START)

    sync(local, remote)

    assert skipper.load() == completer.load()
    [item] = skipper.load()
    assert (item.id, item.done, item.occurrence) == (1, False, 2)
    assert item.due == date(2026, 11, 17) and item.updated_at == at(3)


@pytest.mark.parametrize("completer_is_local", [True, False])
def test_sync_complete_later_than_skip_keeps_done_item_and_next(tmp_path, completer_is_local):
    local, remote = shared_pair(tmp_path, lambda: at(2), lambda: at(2))
    completer, skipper = (local, remote) if completer_is_local else (remote, local)
    completer.clock = lambda: at(3)  # the completion is the later change
    skipper.skip(1, today=WEEK_START)
    completer.complete(1, today=WEEK_START)

    sync(local, remote)

    assert skipper.load() == completer.load()
    done, nxt = completer.load()
    assert (done.id, done.done, done.occurrence) == (1, True, 1)
    assert (nxt.id, nxt.done, nxt.occurrence) == (2, False, 2)


# Stop wins across sync (review of PR #23, human decision): a machine that
# completed an occurrence offline does not restart a series another machine stopped.


@pytest.mark.parametrize("stopper_is_local", [True, False])
@pytest.mark.parametrize("stop_is_later", [True, False])
def test_sync_stop_wins_over_offline_completion(tmp_path, stopper_is_local, stop_is_later):
    local, remote = shared_pair(tmp_path, lambda: at(2), lambda: at(2))
    stopper, completer = (local, remote) if stopper_is_local else (remote, local)
    (stopper if stop_is_later else completer).clock = lambda: at(3)
    completer.complete(1, today=WEEK_START)  # creates occurrence 2 as item 2
    stopper.stop(1)

    sync(local, remote)
    sync(remote, local)

    assert local.load() == remote.load()
    assert [i.id for i in local.load()] == [1, 2]  # nothing deleted
    assert [i.every for i in local.load()] == [None, None]
    nxt = local.load()[1]
    assert (nxt.title, nxt.done, nxt.due) == ("water", False, date(2026, 11, 17))
    assert local.complete(2, today=WEEK_START) is None
    assert [i.id for i in local.load()] == [1, 2]


def test_sync_stop_of_a_done_occurrence_keeps_the_series_going(tmp_path):
    local, remote = shared_pair(tmp_path, lambda: at(2), lambda: at(3))
    local.complete(1, today=WEEK_START)
    local.stop(1)  # removes the rule from the done item only (spec decision 5)

    sync(local, remote)

    assert local.load() == remote.load()
    first, nxt = remote.load()
    assert (first.done, first.every) == (True, None)
    assert (nxt.done, nxt.every) == (False, "week")


def test_sync_stop_ends_only_its_own_series(tmp_path):
    shared = JsonStore(tmp_path / "local.json", clock=lambda: at(1))
    shared.add("water", due=WEEK_START, every="week")
    shared.add("rent", due=WEEK_START, every="month")
    shutil.copy(tmp_path / "local.json", tmp_path / "remote.json")
    local = JsonStore(tmp_path / "local.json", clock=lambda: at(2))
    remote = JsonStore(tmp_path / "remote.json", clock=lambda: at(3))
    local.stop(2)
    remote.complete(1, today=WEEK_START)

    sync(local, remote)

    assert local.load() == remote.load()
    assert [(i.title, i.done, i.every) for i in local.load()] == [
        ("water", True, "week"), ("rent", False, None), ("water", False, "week"),
    ]


def test_remote_store_does_not_follow_symlinks(tmp_path):
    store = remote_store({"TODO_REMOTE": str(tmp_path / "remote.json")})
    assert store.follow_symlinks is False
