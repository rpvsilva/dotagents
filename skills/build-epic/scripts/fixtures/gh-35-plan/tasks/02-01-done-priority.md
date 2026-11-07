# 02-01: done_priority and parent fields
Status: ready
Ticket: 02
Blocked by: none
Writes: todo/fields.py, tests/test_fields.py
Env: ~/.todo/history.jsonl

## What to build
`done_priority` and `parent` on items; `bulk done` writes one history entry per item.

## Acceptance criteria
- [ ] `bulk done` sets `done_priority` on every item it marks.
