# Spec: tally top

## Goal

`tally top <n>` prints only the `n` highest counts, so a long store stays readable. It orders counts as `tally list` does and fails like the other commands on a bad argument.

## Behaviours

1. (epic) `tally top <n>` prints the first `n` lines `tally list` would print, as `<name> <n>`, highest first, ties by name.
2. (epic) When the store holds fewer than `n` counts, `tally top <n>` prints them all.
3. (epic) `tally top` with no `n`, a non-number or a number under 1 prints `tally: usage: tally top <n>` to stderr and exits 1.
4. (epic) The README's usage block lists `tally top <n>`.

## Out of scope

- Changing `tally list` or the store format.
- A bottom-n or a filter by name.

## Open questions

1. Does `tally top 0` print nothing or fail? [Assumed: it fails with the usage error, as behaviour 3 says, since a top of nothing is a mistake.]
