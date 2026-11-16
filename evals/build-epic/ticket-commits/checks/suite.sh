#!/usr/bin/env bash
# Passes when the repo's own suite, with every task's new tests in it, is green on the ticket branch's tip.
set -euo pipefail
tip="$(git -C "$EVAL_WORK" rev-parse --verify refs/heads/feat/gh-403-subtasks)"
checkout="$(mktemp -d)"
trap 'git -C "$EVAL_WORK" worktree remove --force "$checkout" >/dev/null 2>&1 || true; rm -rf "$checkout"' EXIT
git -C "$EVAL_WORK" worktree add -q --detach "$checkout" "$tip"
cd "$checkout"
PYTHONDONTWRITEBYTECODE=1 python3 -I -m pytest -q -c pyproject.toml -p no:cacheprovider </dev/null
