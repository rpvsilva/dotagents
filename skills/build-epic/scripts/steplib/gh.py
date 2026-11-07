"""`gh` and `git push` with a bounded retry on transient GitHub failures.

Retried: HTTP 5xx, a connection reset, GraphQL "something went wrong". Up to `TRIES` tries, waiting 2, 4, 8 and 16 s
between them, each wait multiplied by `BUILD_EPIC_RETRY_SLEEP` (default 1; tests set 0). Everything else, a 4xx, a
merge conflict or a `--match-head-commit` mismatch included, returns at once.

Every `gh` call runs as the run's identity: `run.md`'s `Identity:` line, written by `init_run.py` from the
`identity_command` Setting. When it names a command (e.g. `scripts/gh-app-token.sh`), each call gets a fresh token from
it in `GH_TOKEN`, so no wait outlives the token; `login` (or no line) leaves the current `gh` login.
"""
import os
import re
import subprocess
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


LOGIN = "login"


def identity(run_dir=None):
    """(command, repo) for the run's identity, or None for the current login. `run_dir` defaults to the step's run."""
    run_dir = run_dir or core.RUN_DIR
    run_md = run_dir / "run.md" if run_dir else None
    if not run_md or not run_md.is_file():
        return None
    from . import runfolder  # runfolder imports core only; imported here to keep this module light
    command = runfolder.get(run_md, "Identity")
    command = (command or "").strip().strip("`")
    if not command or command == LOGIN:
        return None
    return command, runfolder.get(run_md, "Repo")


def identity_env(log, env=None):
    """`env` (default: this process's) with `GH_TOKEN` set to a fresh token for the run's identity; unchanged when the
    run names none. The token never reaches the log."""
    who = identity()
    if not who:
        return env
    command, repo = who
    log.write(f"$ {command}  (token for the run's identity, in {repo})\n")
    try:
        p = subprocess.run(core.command(core.split(command), env, repo or None), cwd=repo or None, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
        rc, token, err = p.returncode, p.stdout.strip(), p.stderr
    except OSError as e:
        rc, token, err = 127, "", f"{e}\n"
    if err:
        log.write(err if err.endswith("\n") else err + "\n")
    log.write(f"[rc {rc}]\n")
    log.flush()
    if rc != 0 or not token or len(token.split()) != 1:
        raise core.Fail("identity-token")
    return {**(os.environ if env is None else env), "GH_TOKEN": token}


def retry(argv, log, identity=True, **kw):
    """Runs `argv` through `core.run`, again after a wait while it fails transiently. Returns the last `Result`.

    A `gh` call runs as the run's identity, with a fresh token each try, unless `identity` is False.
    """
    scale = float(os.environ.get("BUILD_EPIC_RETRY_SLEEP", "1"))
    base_env = kw.pop("env", None)
    for attempt in range(1, TRIES + 1):
        env = identity_env(log, base_env) if identity and argv[0] == "gh" else base_env
        res = core.run(argv, log, env=env, **kw)
        if attempt == TRIES or not transient(res):
            return res
        wait = WAITS[attempt - 1] * scale
        log.write(f"transient failure, try {attempt}/{TRIES}; retrying in {wait:g}s\n")
        log.flush()
        time.sleep(wait)
    return res


def gh(args, log, **kw):
    """`gh <args>` with the retry, as the run's identity."""
    return retry(["gh", *args], log, **kw)


def push(args, log, **kw):
    """`git push <args>` with the retry. Run it through `gitops.Git.push` to hold the repo's lock."""
    return retry(["git", "push", *args], log, **kw)
