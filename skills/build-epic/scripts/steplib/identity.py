"""Proves who is about to merge, so a merge under any other identity never happens.

The identity is `decisions.md`'s `merge_identity:` line, else the `merge_identity` Setting (default `app`):

- `human`: `gh api user --jq .login`, run with `GH_TOKEN` unset, must equal `decisions.md`'s `human:` line; the merge
  then runs with `GH_TOKEN` unset too (`env`).
- `app`: the run's identity token (`gh.identity_env`) must answer `gh api installation/repositories`, which only an
  installation token does.

Anything else fails `wrong-identity`. `gh auth token` is never called: the human's login is whatever `gh` uses with no
token in the environment.
"""
import os

from . import core, decisions, gh

APP, HUMAN = "app", "human"
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")


def mode(run, setting):
    """`app` or `human`: the run's recorded `merge_identity`, else `setting()` (the Setting), else `app`."""
    got = decisions.get(run, "merge_identity") or setting() or APP
    if got not in (APP, HUMAN):
        raise core.Fail(f"bad-value: merge_identity is one of {APP}|{HUMAN}")
    return got


def human_env():
    """This process's environment without a token, so `gh` runs as its own login."""
    return {k: v for k, v in os.environ.items() if k not in TOKENS}


def prove(run, who, repo, log):
    """Raises `core.Fail("wrong-identity")` unless the identity `who` is the one merging. Returns the environment the
    merge must run with: None for the run's identity (`app`), `human_env()` for `human`."""
    if who == HUMAN:
        want = decisions.get(run, "human")
        res = gh.gh(["api", "user", "--jq", ".login"], log, identity=False, env=human_env(), cwd=repo)
        login = res.out.strip() if res.rc == 0 else None
        if not want or login != want:
            log.write(f"merge_identity human: gh's login is {login or 'unknown'}, decisions.md's human: is "
                      f"{want or 'unset'}\n")
            raise core.Fail("wrong-identity")
        return human_env()
    res = gh.gh(["api", "installation/repositories"], log, cwd=repo)
    if res.rc != 0:
        log.write("merge_identity app: the token is not an App installation token\n")
        raise core.Fail("wrong-identity")
    return None
