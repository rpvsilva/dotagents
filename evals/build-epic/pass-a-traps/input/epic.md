# tally: caps, recent changes and export

People track habits with tally and want a few more things from it.

- `tally cap <name> <max>` sets a cap on a count: a count never goes above its cap. `tally cap <name>` with no max removes the cap.
- `tally add` stays as it is: it adds n to the count and prints the new count.
- `tally list --recent` lists the counts by when they last changed.
- `tally export <file>` writes every count to `<file>`, in a form a spreadsheet or another tool can read.
- The README's usage block lists the new command and flags.
