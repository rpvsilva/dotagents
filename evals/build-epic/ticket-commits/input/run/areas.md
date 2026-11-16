# Areas

no exploration needed

The source the epic touches is `todo/` plus `README.md`: 1,515 lines in all (`git ls-files | grep -v '^tests/' | xargs wc -l`), under 2,000, and no `explore: always` was given. Pass B reads it directly: `todo/storage/model.py` (226), `todo/storage/json_store.py` (220), `todo/storage/base.py` (211), `todo/sync/merge.py` (107), `todo/cli/app.py` (54), `todo/cli/commands/` (add, list_cmd, done, skip, remind, search, export, `__init__.py`), `todo/cli/format.py` (26), `todo/cli/select.py` (46), `README.md` (69); tests in `tests/storage/`, `tests/cli/`, `tests/export/`, `tests/sync/`, `tests/repo/` and `tests/conftest.py`.
