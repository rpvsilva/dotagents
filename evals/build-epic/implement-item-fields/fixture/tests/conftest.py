import pytest

from todo.cli import main


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run the CLI against tmp_path/todo.json and return its output lines."""
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "todo.json"))

    def run(*argv, today=None):
        lines = []
        main(list(argv), out=lines.append, today=today)
        return lines

    return run
