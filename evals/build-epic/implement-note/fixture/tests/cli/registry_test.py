import sys

import pytest

from todo.cli.commands import COMMANDS, discover


def test_commands_are_discovered_and_sorted_by_name():
    names = ["add", "done", "export", "list", "priority", "remind", "search", "skip", "stop", "sync", "tag"]
    assert list(COMMANDS) == names


def test_cmd_suffix_is_stripped_from_module_name():
    assert COMMANDS["list"].__name__ == "todo.cli.commands.list_cmd"
    assert COMMANDS["sync"].__name__ == "todo.cli.commands.sync_cmd"
    assert COMMANDS["add"].__name__ == "todo.cli.commands.add"


def test_module_without_register_and_run_fails_at_discovery(tmp_path, monkeypatch):
    pkg = tmp_path / "fakecmds"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("def register(parser): pass\ndef run(args, ctx): pass\n")
    (pkg / "broken_cmd.py").write_text("def register(parser): pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    for m in [m for m in sys.modules if m.startswith("fakecmds")]:
        monkeypatch.delitem(sys.modules, m)
    import fakecmds

    with pytest.raises(ImportError, match="fakecmds.broken_cmd"):
        discover(fakecmds.__name__, fakecmds.__path__)
