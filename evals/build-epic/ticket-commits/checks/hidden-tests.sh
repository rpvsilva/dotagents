#!/usr/bin/env bash
# Passes when the hidden acceptance tests of #403 (subtasks; hidden/accept_subtasks.py, never shown to the run) pass on
# the ticket branch's tip, where the run merged its tasks. Needs a python3 on PATH that imports pytest.
set -euo pipefail
hidden="$(cd "$(dirname "$0")" && pwd)/hidden"
tip="$(git -C "$EVAL_WORK" rev-parse --verify refs/heads/feat/gh-403-subtasks)"
checkout="$(mktemp -d)"
trap 'git -C "$EVAL_WORK" worktree remove --force "$checkout" >/dev/null 2>&1 || true; rm -rf "$checkout"' EXIT
git -C "$EVAL_WORK" worktree add -q --detach "$checkout" "$tip"
ACCEPT_CHECKOUT="$checkout" PYTHONDONTWRITEBYTECODE=1 python3 -I -m pytest -q -c "$hidden/pytest.ini" "$hidden" </dev/null
