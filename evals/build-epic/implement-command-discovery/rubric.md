Score the epic-implementer's run on task 01-01: Find commands from their own modules (prefactor). The setup lines at the top of the prompt and its closing paragraph stand in for build-epic's worktree and remote, which an offline eval lacks; the rest is the prompt `task_prompt.py` writes.

Its `Writes`: todo/cli/commands/__init__.py, tests/cli/registry_test.py. Its acceptance criteria, as the task file states them:

> - `sorted(COMMANDS)` is exactly `add, done, export, list, priority, remind, search, skip, stop, sync, tag`, each mapped to the same module as before.
> - Every existing test passes unchanged (`tests/cli/cli_test.py:141` still patches `COMMANDS["list"]`).
> - A module without `register` and `run` fails at import with a clear error naming it, not later at dispatch.
> - No other file changes; `todo/cli/app.py` reads `COMMANDS` as before.

Judge the tests the implementer added or changed under `tests/` in the diff, and its report in the scratch folder (`reports/01-01.md`). The checks already test the behaviour itself with hidden acceptance tests; here, score whether the implementer's own tests prove the task.

- criteria-proved: Every acceptance criterion above has at least one test in the diff that would go red if the change it covers were reverted. 5 when all are covered, 4 with one minor criterion uncovered, 3 with one main criterion uncovered, 1 when most are uncovered.
- tests-can-fail: No test is hollow: each assertion pins the behaviour (an exact output line, an exit code, the file's bytes or fields), not something that stays true with the change reverted (e.g. `assert x in output` where the old output already holds it, or a test that only checks no exception). 5 with none hollow, 3 with one, 1 with several.
- public-surface: Tests drive the public surface the task names (the `todo` CLI through the `cli` fixture or `main`, and the functions the task's criteria name), and assert what a user or caller observes. No test calls a private helper (a `_name`), mocks or patches the repo's own code (setting the environment a test needs is fine: `TODO_FILE`, `Store.clock` as map.md's `Test isolation` says, or a throwaway package on `sys.path`), or asserts implementation details (call counts, private attributes, how a module is laid out). 5 with none, 3 with one, 1 with several.
- isolated: Every test sets the environment it reads, as map.md's `Test isolation` says (`TODO_FILE` through the `cli` fixture or `monkeypatch`, `Store.clock` through `monkeypatch.setattr` or `clock=`, `today=` passed explicitly), so it passes whatever the caller's shell holds; none writes outside its `tmp_path`. 5 when all are, 1 when a test reads the real home or shell state.
- mutation-shown: The report names, for its new tests, the break it made (a deleted line, a flipped condition) and saw each go red, as the brief's step 2 asks, and the breaks named match the code in the diff. 5 when every new test's break is named and plausible, 3 when only some are, 1 when none are.
