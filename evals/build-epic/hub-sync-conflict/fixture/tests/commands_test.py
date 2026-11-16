from tally import list as list_cmd
from tally.commands import COMMANDS


def test_commands_are_sorted():
    assert COMMANDS == sorted(COMMANDS)


def test_list_marks_done_items():
    assert list_cmd.run([{"title": "a", "done": True}, {"title": "b", "done": False}]) == ["[x] a", "[ ] b"]
