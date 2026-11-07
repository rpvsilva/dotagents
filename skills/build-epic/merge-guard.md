# Merge guard rules

A repo's **leave-for-human rules**: changes an agent may not merge unless the run's human waived them. Every agent merge checks its diff against them before it pushes anything, and refuses with `guarded <file:line rule>; ...`, merging nothing. This file is the one place the format is defined; `scripts/steplib/guard.py` reads it and `scripts/guard_rules.py <run>` lists a repo's rules.

## Where they live

A fenced block tagged `agent-merge-guard` in the repo's `AGENTS.md`, or in its `CLAUDE.md` when `AGENTS.md` has none (a symlinked file is followed). A repo with no block has no rules: every diff passes.

The rules are read from the merge's base on `origin` (`origin/<base>`), never from the PR head, so a PR that deletes or loosens a rule is still judged by it; a rule added in a PR applies once that PR has merged.

## Format

One rule per line; blank lines and lines starting with `#` are skipped.

- `<name> path <glob>`: any change to a matching path. A deleted file matches by its old path; a rename is a delete plus an add, so both paths are checked.
- `<name> added <regex> [in <glob>]`: any added line the Python regex finds a match in, only in matching paths when `in <glob>` is given. A regex may hold spaces; one ending in the word `in` needs an `in <glob>` after it.

`<name>` starts with a letter or digit and holds only letters, digits, `.`, `_` and `-`; each name is used once. Globs match the whole path from the repo root: `*` and `?` stay inside one folder, `**` crosses folders, and `**/` also matches no folder at all (`**/conftest.py` matches `conftest.py` and `tests/conftest.py`). Any other line fails the guard with `bad-rule: line <n>: <text>`, so a typo refuses every merge rather than dropping a rule.

A hit is named `<file>:<line> <rule>`: an added line by its number in the new file, a path hit by the first changed line of the new file (`0` for a deletion or a change with no lines, such as a binary file).

## Waivers

A rule's name is its clause in the run's `decisions.md` Authorizations: at approval, `guard_rules.py <run>` prints `rule <name> <kind> <pattern>` per rule, and each answer is recorded with `decide.py <run> clause "<name>" yes|no|ask "<the human's words>"`. A hit passes only when its rule's row says `yes`. `no`, `ask` (no script can ask), no row and no `decisions.md` all refuse. A file that several rules match needs every one of them waived.

## Example

````markdown
```agent-merge-guard
# a process exit in package code is the human's call
process-exit added \b(sys\.exit|os\._exit)\( in pkg/**
tests path tests/**
conftest path **/conftest.py
```
````

With `tests` waived `yes` and nothing else: a change to `tests/test_cli.py` merges; a change to `tests/conftest.py` is refused with `guarded tests/conftest.py:1 conftest` until `conftest` is waived too; `sys.exit(1)` added at line 12 of `pkg/cli.py` is refused with `guarded pkg/cli.py:12 process-exit`.
