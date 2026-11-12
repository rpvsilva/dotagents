# security-audit notes

## Accepted risks

Findings a human chose not to fix; audits treat them as known and skip them.

- `todo/sync/merge.py:48-64`: whoever can write the remote file can stop a local series, or drop a local occurrence by adding a duplicate `series_id`/`occurrence` with a later stamp. Accepted: sync trusts the remote by design ("a series stopped on either side stays stopped"), and that writer can already overwrite any item by id.
