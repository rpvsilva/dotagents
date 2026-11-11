# TT-02: Manage counts

Let a user fix their counts: names follow one rule, and a count can be removed or renamed.

## Acceptance criteria

1. A name is one or more lowercase letters, digits or `-`. Any command that creates a name refuses any other with `tally: bad name: <name>` and exit 1, and changes nothing.
2. `tally remove <name>` deletes the count and prints nothing. Removing a name with no count fails with `tally: no count: <name>` and exit 1.
3. `tally rename <old> <new>` moves the count of `<old>` to `<new>` and prints `<new> <n>`.
   - `<old>` with no count fails with `tally: no count: <old>`, exit 1.
   - `<new>` that already has a count fails with `tally: count exists: <new>`, exit 1, and changes nothing: rename never merges or overwrites counts.
4. The README lists both commands and the name rule.
5. Every behaviour above has a test.

## Tasks

- T-01: name rule in `add` (criterion 1).
- T-02: `remove` (criterion 2).
- T-03: `rename` (criterion 3).
