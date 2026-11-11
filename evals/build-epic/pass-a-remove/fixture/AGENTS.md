# tally

Rules for working on this repo.

- Python 3 standard library only: no dependencies.
- Every command lives in `tally/cli.py`; the store is read and written only through `tally/store.py`.
- A **count** is a name and a whole number of at least 0. The store is a JSON object of counts.
- Output is plain text, one count per line as `<name> <n>`. Errors go to stderr as `tally: <message>` and exit 1.
- Tests live in `tests/` as `*_test.py` and set `TALLY_FILE` to a temp file; never touch the real store.
- Run the tests: `python3 -m unittest discover -s tests -p '*_test.py'`.
