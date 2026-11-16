import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

PLANTED_CONFIGS = {
    "pytest.toml": '[pytest]\naddopts = ["--collect-only"]\n',
    ".pytest.toml": '[pytest]\naddopts = ["--collect-only"]\n',
    ".pytest.ini": "[pytest]\naddopts = --collect-only\n",
}


def ci_pytest_command():
    workflow = (REPO / ".github/workflows/ci.yml").read_text()
    (line,) = re.findall(r"^\s*- run: (python .*-m pytest.*)$", workflow, re.M)
    return shlex.split(line)


def local_pytest_command():
    agents = (REPO / "AGENTS.md").read_text()
    (line,) = re.findall(r"`(\.venv/bin/python [^`]*-m pytest[^`]*)`", agents)
    return shlex.split(line)


@pytest.mark.parametrize("command", [ci_pytest_command, local_pytest_command], ids=["ci", "local"])
@pytest.mark.parametrize("name", PLANTED_CONFIGS)
def test_planted_pytest_config_cannot_hide_a_failing_test(tmp_path, command, name):
    (tmp_path / "pyproject.toml").write_text((REPO / "pyproject.toml").read_text())
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_broken.py").write_text("def test_broken():\n    assert False\n")
    (tmp_path / name).write_text(PLANTED_CONFIGS[name])
    argv = [sys.executable, *command()[1:]]
    result = subprocess.run(argv, capture_output=True, text=True, cwd=tmp_path, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in result.stdout


def test_no_compiled_python_files_are_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.pyc", "*.pyd", "*.so"],
        capture_output=True, text=True, cwd=REPO, check=True,
    ).stdout.split()
    assert tracked == []
