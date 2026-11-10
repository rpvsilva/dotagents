# tally: remove a count

Today a count can only grow: once `tea` is in the store, it stays there for good. Add a way to take a name out.

- `tally remove <name>` deletes the count and prints `removed <name>`.
- Removing a name the store does not hold is an error, like the other commands' errors.
- `tally list` no longer shows a removed name; adding it again starts it from 1.
