# tally

A tiny CLI used to test the `build-epic` skill. Checks: `python3 -I -m pytest -q -c pyproject.toml`. Commits: Conventional Commits.

- `tally/commands.py`: `COMMANDS`, the command names `python -m tally` accepts, sorted.
- `tally/<command>.py`: one module per command, each with `run(items) -> list[str]` (the lines it prints).
- Tests: `tests/<name>_test.py`.
