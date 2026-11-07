## Summary

Ticket PR bodies generated from the plan: delivers #191, #192 in 3 tasks.

## Testing

Each task's local run of map.md's Checks before its merge:

- 07-01 Bodies from the run folder:
  - `tests/run.sh`: passed in 30.2s (12 passed, 0 failed, 1 skipped)
  - `python3 -m unittest discover -s skills/build-epic/scripts -p 'test_*.py'`: passed in 3s
- 07-02 Shipping takes no prose:
  - `tests/run.sh`: passed in 28s
- 07-03 Docs for the new body: no saved result.

Not exercised here:
- `tests/run.sh` (07-01): zsh not installed

## Risk

Touches the files its tasks write:
- `skills/build-epic/scripts/steplib/prbody.py`
- `skills/build-epic/scripts/test_ticket_body.py`
- `skills/build-epic/scripts/ship_ticket.py`
- `skills/build-epic/SKILL.md`

## How to revert

Revert the commit this PR lands on `main`: `git revert <commit>`.

## Tasks

- Bodies from the run folder: https://github.com/o/r/pull/41
- Shipping takes no prose: https://github.com/o/r/pull/42
- Docs for the new body: no PR

## Acceptance criteria

### 07-01 Bodies from the run folder

- For a fixture run folder the body equals the expected file.
- Each template heading is filled.

### 07-02 Shipping takes no prose

- `ship_ticket.py` accepts no body argument.

### 07-03 Docs for the new body

- SKILL.md step 7 names no body file.

## Not exercised here

- zsh not installed

## Merge

Merges as the App, with its installation token, into `main`, after a review and green CI.

Authorizations:
- Leave merges to the human (waived: yes): Rui: "merge them as the App"

Closes #191
Closes #192
