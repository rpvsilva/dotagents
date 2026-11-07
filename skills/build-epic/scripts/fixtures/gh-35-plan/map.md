# Map: todo gains due times, done priority and subtasks (gh-35)

## Invariants

- Every command that writes the store goes through `todo/store.py` `save()`, which records one undo entry.
- A test never depends on an env var the package reads: it sets or unsets `TODO_DB` itself.

## Field matrix

| field | copy | import | bulk done | restore |
|---|---|---|---|---|
| due_time | keep | keep | keep | keep |
| done_priority | reset | ? | keep | keep |
| parent | drop | ? | keep | ? |

## Test isolation

- `TODO_DB` (env var, ticket 01): reset by the `isolated_db` fixture in `tests/conftest.py`.
- `~/.todo/history.jsonl` (side file, ticket 02): written under the temp `HOME` the `isolated_db` fixture in `tests/conftest.py` sets.

## Checks

```
python3 -m pytest -q
```

Setup: none

## Gaps

- Field matrix cells marked `?` are approval questions.
