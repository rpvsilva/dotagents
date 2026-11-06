"""`gh` and `git push` with a bounded retry on transient GitHub failures.

Retried: HTTP 5xx, a connection reset, GraphQL "something went wrong". Up to `TRIES` tries, waiting 2, 4, 8 and 16 s
between them, each wait multiplied by `BUILD_EPIC_RETRY_SLEEP` (default 1; tests set 0). Everything else, a 4xx, a
merge conflict or a `--match-head-commit` mismatch included, returns at once.
"""
import os
import re
import time

from . import core

TRIES = 5
WAITS = (2, 4, 8, 16)

PERMANENT = re.compile(
    r"HTTP 4\d\d|returned error: 4\d\d|merge conflict|not mergeable|Head branch was modified|"
    r"match-head-commit|expected head sha|head commit .*(?:does not match|mismatch)",
    re.IGNORECASE)
TRANSIENT = re.compile(
    r"HTTP 5\d\d|returned error: 5\d\d|\b50[0-4] (?:Internal|Bad Gateway|Service Unavailable|Gateway)|"
    r"connection reset|something went wrong",
    re.IGNORECASE)


def transient(res):
    """True when a failed result is worth another try."""
    text = res.out + res.err
    return res.rc != 0 and not PERMANENT.search(text) and bool(TRANSIENT.search(text))


def retry(argv, log, **kw):
    """Runs `argv` through `core.run`, again after a wait while it fails transiently. Returns the last `Result`."""
    scale = float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    for attempt in range(1, TRIES + 1):
        res = core.run(argv, log, **kw)
        if attempt == TRIES or not transient(res):
            return res
        wait = WAITS[attempt - 1] * scale
        log.write(f"transient failure, try {attempt}/{TRIES}; retrying in {wait:g}s\n")
        log.flush()
        time.sleep(wait)
    return res


def gh(args, log, **kw):
    """`gh <args>` with the retry."""
    return retry(["gh", *args], log, **kw)


def push(args, log, **kw):
    """`git push <args>` with the retry. Run it through `gitops.Git.push` to hold the repo's lock."""
    return retry(["git", "push", *args], log, **kw)
