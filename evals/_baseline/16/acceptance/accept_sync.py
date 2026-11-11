"""#22 One next occurrence after syncing offline completions, through `todo sync` on two machines sharing a remote.

Today is 2026-11-10. Both machines start from the same synced file, so item ids line up (the id collision of items
created on both sides, SUP-4118, is out of the epic's scope). The CLI stamps each change with the real clock, so a
short sleep orders the two competing changes. Core tests follow the issue's criteria; the strict ones pin the
first build's review decision that a stopped series stays stopped across sync."""
import re
import time

import pytest
from conftest import due_of, line_of


def shared_series(machines, *flags):
    """Machine A adds a weekly series due today (plus `flags`), then both machines sync, so both hold it as item 1."""
    a, b = machines
    a.add("water", "--due", "2026-11-10", "--every", "week", *flags)
    a.ok("sync")
    b.ok("sync")
    assert [i for i, _, _ in b.items()] == [1]
    return a, b


def sync_all(a, b):
    for m in (a, b, a):
        m.ok("sync")
    assert a.items() == b.items()


def later():
    time.sleep(0.02)


def test_both_machines_complete_the_same_occurrence_and_keep_one_next(machines):
    a, b = shared_series(machines)
    a.ok("done", "1")
    later()
    b.ok("done", "1")
    sync_all(a, b)
    for m in (a, b):
        assert [due_of(line) for line in m.open_lines()] == ["2026-11-17"]
        assert len(m.done_lines()) == 1


def test_one_machine_completes_and_the_other_syncs_first_still_one_next(machines):
    a, b = shared_series(machines)
    a.ok("done", "1")
    a.ok("sync")
    later()
    b.ok("done", "1")  # b completes its stale copy before pulling a's completion
    sync_all(a, b)
    for m in (a, b):
        assert [due_of(line) for line in m.open_lines()] == ["2026-11-17"]


@pytest.mark.parametrize("skipper", [0, 1], ids=["a-skips", "b-skips"])
def test_a_later_skip_wins_over_a_completion(machines, skipper):
    a, b = shared_series(machines)
    skips, completes = (a, b) if skipper == 0 else (b, a)
    completes.ok("done", "1")
    later()
    skips.ok("skip", "1")
    sync_all(a, b)
    for m in (a, b):
        assert [due_of(line) for line in m.open_lines()] == ["2026-11-17"]
        assert m.done_lines() == []


@pytest.mark.parametrize("completer", [0, 1], ids=["a-completes", "b-completes"])
def test_a_later_completion_wins_over_a_skip(machines, completer):
    a, b = shared_series(machines)
    completes, skips = (a, b) if completer == 0 else (b, a)
    skips.ok("skip", "1")
    later()
    completes.ok("done", "1")
    sync_all(a, b)
    for m in (a, b):
        assert [due_of(line) for line in m.open_lines()] == ["2026-11-17"]
        assert [due_of(line) for line in m.done_lines()] == ["2026-11-10"]


def test_one_off_items_sync_as_before(machines):
    a, b = machines
    a.add("buy milk")
    a.add("water", "--due", "2026-11-10", "--every", "week")
    a.ok("sync")
    b.ok("sync")
    b.ok("done", "1")
    later()
    a.ok("priority", "1", "high")  # the later change wins as a whole: open again, with its priority
    b.add("call mum")  # id 3, new on b only
    sync_all(a, b)
    lines = a.list()
    milk = next(line for line in lines if "buy milk" in line)
    assert milk.startswith("[ ] 1: buy milk") and "!!" in milk
    assert any(line.startswith("[ ] 3: call mum") for line in lines)


def paragraphs(readme):
    return [p for p in re.split(r"\n\s*\n", readme)]


def test_readme_states_the_sync_rule(readme):
    assert any(re.search(r"\bsync", p, re.I) and re.search(r"occurrence", p, re.I) for p in paragraphs(readme))


def test_readme_known_limits_say_older_versions_crash_on_recurring_items(readme):
    assert any(re.search(r"older version", line, re.I) and re.search(r"recurring|--every", line, re.I)
               for line in readme.splitlines())


@pytest.mark.strict
@pytest.mark.parametrize("stop_is_later", [True, False], ids=["stop-later", "stop-earlier"])
def test_a_stopped_series_stays_stopped_after_an_offline_completion(machines, stop_is_later):
    a, b = shared_series(machines)
    first, second = (b, a) if stop_is_later else (a, b)
    first.ok("done" if stop_is_later else "stop", "1")
    later()
    second.ok("stop" if stop_is_later else "done", "1")
    sync_all(a, b)
    for m in (a, b):
        assert len(m.items()) == 2  # nothing deleted
        assert all("↻" not in line for line in m.list())
        assert due_of(line_of(m.open_lines(), 2)) == "2026-11-17"  # the next occurrence, now a one-off
