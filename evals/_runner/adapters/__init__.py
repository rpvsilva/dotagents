"""Tool adapters: the only code that knows a tool (spec behaviour 8). Each adapter is its own file here, loaded by
name with `load()`; there is no registry, so adding a tool is adding a file.

An adapter module holds:
- `INSTALL_WORD`: the tool word `install.sh` takes (`claude`, `codex`, `cursor`).
- `AUTH_VARS`: the env vars the tool authenticates with, passed to its process only and scrubbed from kept files.
- `USES_CASE_MODEL`: true when the case's `model` is this tool's; else the runner needs `--model`.
- `prepare_home(home)`: whatever the throwaway home needs before `install.sh` runs.
- `command(prompt, model, caps, work) -> argv`: the headless run; `caps` is the case's `{turns, secs, cost_usd}`.
- `parse(run_dir, home, work) -> {tokens, turns, duration_s, capped, tool_version, spawned_agents, error}`: what
  the run's transcript (`<run_dir>/transcript.jsonl`, the tool's stdout) and logs say; unknown values are `None`.
- `answer(run_dir) -> str | None`: the run's final answer, from the same transcript; `None` when it has none.
"""
import importlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRANSCRIPT = "transcript.jsonl"


def names():
    """The adapters found beside this file."""
    return sorted(p.stem for p in HERE.glob("*.py") if not p.stem.startswith(("_", "test_")))


def load(name):
    """The adapter module for tool `name`; a ValueError naming the adapters found when there is none."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name not in names():
        raise ValueError(f"no adapter for tool '{name}' (adapters found: {', '.join(names()) or 'none'})")
    return importlib.import_module(f"{__name__}.{name}")
