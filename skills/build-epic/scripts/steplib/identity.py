"""Proves who is about to merge, so a merge under any other identity never happens.

The identity is the `merge_identity` Setting (default `app`), never `decisions.md`, which the agent writes:
`decisions.md`'s `merge_identity:` only records it, and a recorded value other than the Setting fails `wrong-identity`.

- `human`: `gh api user --jq .login`, run with the tokens unset, names the human; it must answer, must not be a
  `[bot]`, and must equal `decisions.md`'s `human:` line when there is one (compared case-insensitively: the line may
  only narrow, never name the login). The merge then runs with the tokens unset too (`human_env`).
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
    """`app` or `human`: `setting()` (the Setting), else `app`. `run` is unused: `prove` checks its record."""
    got = setting() or APP
    if got not in (APP, HUMAN):
        raise core.Fail(f"bad-value: merge_identity is one of {APP}|{HUMAN}")
    return got


def same_login(a, b):
    """True when two GitHub logins name the same account (logins are case-insensitive)."""
    return bool(a) and bool(b) and a.casefold() == b.casefold()


def human_env():
    """This process's environment without a token, so `gh` runs as its own login."""
    return {k: v for k, v in os.environ.items() if k not in TOKENS}


def human_login(repo, log):
    """The human's login: `gh api user` with the tokens unset, or None when it does not answer."""
    res = gh.gh(["api", "user", "--jq", ".login"], log, identity=False, env=human_env(), cwd=repo)
    return (res.out.strip() or None) if res.rc == 0 else None


def prove(run, who, repo, log):
    """Raises `core.Fail("wrong-identity")` unless the identity `who` is the one merging. Returns the environment the
    merge must run with: None for the run's identity (`app`), `human_env()` for `human`."""
    recorded = decisions.get(run, "merge_identity")
    if recorded and recorded.strip().lower() != who:
        log.write(f"merge_identity: the Setting is {who}, decisions.md records {recorded}\n")
        raise core.Fail("wrong-identity")
    if who == HUMAN:
        want = (decisions.get(run, "human") or "").strip() or None  # a file saved on Windows ends lines with \r
        login = human_login(repo, log)
        if not login or login.endswith("[bot]") or (want and not same_login(login, want)):
            log.write(f"merge_identity human: gh's login is {login or 'unknown'}, decisions.md's human: is "
                      f"{want or 'unset'}\n")
            raise core.Fail("wrong-identity")
        return human_env()
    res = gh.gh(["api", "installation/repositories"], log, cwd=repo)
    if res.rc != 0:
        log.write("merge_identity app: the token is not an App installation token\n")
        raise core.Fail("wrong-identity")
    return None
