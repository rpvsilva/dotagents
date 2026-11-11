# tally: rename a count

People mistype names (`cofee`) and then have two counts for one thing. Let them fix it.

- `tally rename <old> <new>` moves the count of `<old>` to `<new>` and prints `<new> <n>`.
- When `<new>` already has a count, the two are added together, so nothing is lost.
- Renaming a name the store does not hold is an error.
- The README's usage block lists the new command.
