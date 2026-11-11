# tally

A tiny command-line counter. It keeps named counts in one JSON file.

## Usage

```
tally add <name> [n]    add n (default 1) to a count
tally list              print every count, highest first
```

The store is `~/.tally.json`, or the file `TALLY_FILE` names.
