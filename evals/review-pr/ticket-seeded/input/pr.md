# PR #20: Manage counts: valid names, `remove` and `rename`

Base branch: `main`. Author: the run's identity. Changed files: `README.md`, `tally/cli.py`, `tests/cli_test.py`.

The whole ticket in `ticket.md`, built as three task PRs merged into this branch:

- T-01: names must be lowercase letters, digits and `-`.
- T-02: `tally remove <name>`.
- T-03: `tally rename <old> <new>`.

Documents both commands and the name rule in the README, with tests for each.
