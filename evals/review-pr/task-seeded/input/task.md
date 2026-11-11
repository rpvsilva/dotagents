# T-03: `tally reset <name>`

Add a command that sets one count back to 0.

## Acceptance criteria

- `tally reset <name>` sets the count of `<name>` to 0 and prints `<name> 0`; every other count is unchanged.
- Resetting a name with no count fails with `tally: no count: <name>` on stderr and exit 1, and changes nothing.
- `tally reset` with no name, or more than one, fails with `tally: usage: tally reset <name>`.
- The README lists the command.
