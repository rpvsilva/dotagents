"""Everything one eval run needs before the tool starts, tool-agnostic, and what it keeps after.

`checkout(repo, ref)` extracts dotagents at a ref; `prepare(case, checkout_dir, tool_word)` builds a run folder:
`home/` (a throwaway `$HOME` with the ref's own `install.sh` run for the case's parts), `work/` (the case fixture as a
fresh git repo, the tool's cwd), `scratch/`, `input/`, and `bin/gh`, a stub first on the tool's `PATH` that records
its arguments in `gh-calls` and fails. `Run.env()` is the tool's environment, `Run.prompt()` fills the placeholders and
`Run.keep()` copies the state into the result folder with every auth value replaced by `[redacted]`.
A remote run (`remote=True`) fills `{work}`, `{input}` and `{scratch}` as the sandbox sees them (`~/work`,
`~/attached/input`, `~/work/.eval-scratch`); `Run.collect_scratch()` then moves the returned `work/.eval-scratch/`
into `scratch/`, so `work/` reads as after a local run.
Everything lives in temp folders outside the dotagents checkout; `close()` removes them.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

TOOL_WORDS = ("claude", "codex", "cursor")
# The caller's variables a tool sees, besides the auth vars it names: the rest is set per run.
PASS_THROUGH = ("LANG", "TMPDIR")
REDACTED = b"[redacted]"
PLACEHOLDERS = ("work", "scratch", "input", "home")
KEPT = ("work", "scratch", "home", "gh-calls")
SCRATCH_DIR = ".eval-scratch"
REMOTE_PATHS = {"work": "~/work", "input": "~/attached/input", "scratch": "~/work/" + SCRATCH_DIR}


class InstallError(RuntimeError):
    """The ref's `install.sh` failed: the runner records the run as `error: install`, not as a case failure."""
    error = "install"


class Checkout:
    """dotagents at one commit, extracted with `git archive | tar -x`. Installed links point into it, so it lives
    until every run on it ends: close it (or leave the `with` block) after the last one."""

    def __init__(self, repo, ref):
        self.ref = ref
        self.sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", what=f"no commit '{ref}'")
        self.path = Path(tempfile.mkdtemp(prefix="evals-checkout-")).resolve()
        try:
            archive = subprocess.Popen(["git", "-C", str(repo), "archive", self.sha], stdout=subprocess.PIPE,
                                       stdin=subprocess.DEVNULL)
            tar = subprocess.run(["tar", "-x", "-C", str(self.path)], stdin=archive.stdout, capture_output=True)
            archive.stdout.close()
            if archive.wait() != 0 or tar.returncode != 0:
                raise RuntimeError(f"cannot extract {ref} ({self.sha}): {tar.stderr.decode(errors='replace')}")
        except BaseException:
            self.close()
            raise

    def close(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def checkout(repo, ref):
    """dotagents at `ref` of `repo`, resolved to its sha (`.sha`) and extracted into a temp folder (`.path`)."""
    return Checkout(repo, ref)


def _git(cwd, *args, what=None, env=None):
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                         env=env)
    if out.returncode != 0:
        raise RuntimeError(what or f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def check_parts(checkout_dir, home, tool_word, parts):
    """Raise ValueError unless `install` may run: `tool_word` one tool, at least one part (with none, install.sh
    would install every part) and no `global` (personal instructions), named or in the ref's `parts.sh closure` of
    them. Raises InstallError when that closure exits non-zero."""
    if tool_word not in TOOL_WORDS:
        raise ValueError(f"not a tool word: {tool_word!r} (one of {', '.join(TOOL_WORDS)})")
    parts = list(parts)
    if not parts:
        raise ValueError("install needs at least one part: with none, install.sh installs every part")
    env = _base_env(home)
    # A group (personal) or a part can pull global in through its required needs: refuse on the ref's own closure.
    closure = subprocess.run(["bash", str(Path(checkout_dir) / "scripts" / "parts.sh"), "closure", *parts], env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if closure.returncode != 0:
        raise InstallError(f"parts.sh closure {' '.join(parts)} exited {closure.returncode}:\n"
                           f"{(closure.stdout + closure.stderr).strip()[-2000:]}")
    if "global" in closure.stdout.split():
        raise ValueError(f"install must not install global, which {' '.join(parts)} pulls in")


def install(checkout_dir, home, tool_word, parts):
    """Run the checkout's own `install.sh <tool_word> <parts>` into `home`, once `check_parts` allows it.
    Raises InstallError on a non-zero exit of either."""
    parts = list(parts)
    check_parts(checkout_dir, home, tool_word, parts)
    env = _base_env(home)
    env.update(DOTAGENTS_SKIP_PLUGINS="1", DOTAGENTS_PLAIN="1")
    out = subprocess.run(["bash", str(Path(checkout_dir) / "install.sh"), tool_word, *parts], env=env,
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if out.returncode != 0:
        raise InstallError(f"install.sh {tool_word} {' '.join(parts)} exited {out.returncode}:\n"
                           f"{(out.stdout + out.stderr).strip()[-2000:]}")


def _base_env(home, path=None):
    home = Path(home)
    env = {v: os.environ[v] for v in PASS_THROUGH if v in os.environ}
    env.update(PATH=path or os.environ.get("PATH") or os.defpath, HOME=str(home),
               XDG_CONFIG_HOME=str(home / ".config"), CLAUDE_CONFIG_DIR=str(home / ".claude"))
    return env


def tool_env(home, auth_vars, bin_dir=None):
    """The tool's whole environment: `PATH` (with `bin_dir` first), `HOME`, `XDG_CONFIG_HOME` and `CLAUDE_CONFIG_DIR`
    inside it, `LANG` and `TMPDIR`, and the named auth vars from the caller's env. Nothing else of the caller's."""
    path = os.environ.get("PATH") or os.defpath
    if bin_dir is not None:
        path = str(bin_dir) + os.pathsep + path
    env = _base_env(home, path)
    env.update({v: os.environ[v] for v in auth_vars if os.environ.get(v)})
    return env


def write_gh_stub(bin_dir, calls):
    """A `gh` in `bin_dir` that appends its arguments, one call per line, to `calls` and exits 1."""
    bin_dir, calls = Path(bin_dir), Path(calls)
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls.touch()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >>{shlex.quote(str(calls))}\nexit 1\n')
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return gh


def make_work(fixture, work, settings=None):
    """Copy `fixture` to `work`, write `settings` (when any) as `.agents/settings.json`, then `git init` it with one
    commit holding everything."""
    work = Path(work)
    shutil.copytree(fixture, work, symlinks=True)
    if settings:
        (work / ".agents").mkdir(exist_ok=True)
        (work / ".agents" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    # The caller's git config (signing, hooks, templates) stays out: no global or system config is read.
    env = {"PATH": os.environ.get("PATH") or os.defpath, "HOME": str(work), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull}
    _git(work, "init", "-q", env=env)
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main", env=env)
    _git(work, "add", "-A", env=env)
    _git(work, "-c", "user.name=eval", "-c", "user.email=eval@localhost", "-c", "commit.gpgsign=false", "commit",
         "-q", "--allow-empty", "--no-verify", "-m", "fixture", env=env)
    return work


def fill_prompt(text, paths):
    """`text` with `{work}`, `{scratch}`, `{input}` and `{home}` replaced by `paths[name]`; other braces stay."""
    for name in PLACEHOLDERS:
        text = text.replace("{" + name + "}", str(paths[name]))
    return text


def _redact(data, values):
    for value in values:
        data = data.replace(value, REDACTED)
    return data


def keep(src, dest, auth_vars, extra=()):
    """Copy `work/`, `scratch/`, `home/` and `gh-calls` of run folder `src` into `dest`: real files only (symlinks,
    such as installed links into the checkout, dropped), every value of an auth var in env replaced by
    `[redacted]` in every file, and so is each value of `extra` (bytes)."""
    src, dest = Path(src), Path(dest)
    values = sorted({os.environ[v].encode() for v in auth_vars if os.environ.get(v)} | set(extra), key=len,
                    reverse=True)
    for name in KEPT:
        top = src / name
        if top.is_symlink() or not top.exists():
            continue
        if top.is_file():
            _copy(top, dest / name, values)
            continue
        for root, _, files in os.walk(top):  # never descends a linked folder
            rel = Path(root).relative_to(src)
            (dest / rel).mkdir(parents=True, exist_ok=True)
            for f in files:
                path = Path(root) / f
                if not path.is_symlink() and path.is_file():
                    _copy(path, dest / rel / f, values)


def _copy(path, to, values):
    to.parent.mkdir(parents=True, exist_ok=True)
    to.write_bytes(_redact(path.read_bytes(), values))
    shutil.copymode(path, to)


class Run:
    """One run's folder: `home/`, `work/`, `scratch/`, `input/`, `bin/` and `gh-calls`, in a temp folder."""

    def __init__(self, root, remote=False):
        self.root = Path(root)
        self.remote = remote
        self.home, self.work, self.scratch = self.root / "home", self.root / "work", self.root / "scratch"
        self.input, self.bin, self.gh_calls = self.root / "input", self.root / "bin", self.root / "gh-calls"

    @property
    def paths(self):
        return {"work": self.work, "scratch": self.scratch, "input": self.input, "home": self.home}

    @property
    def prompt_paths(self):
        """The paths the prompt names: the run's own, or for a remote run the sandbox's (`{home}` stays the run's)."""
        return {**self.paths, **REMOTE_PATHS} if self.remote else self.paths

    def collect_scratch(self):
        """Move the scratch a remote run wrote in `work/.eval-scratch/` (untracked: the patch applies unstaged) to
        `scratch/`, leaving `work/` with no trace of it. Nothing to move when the run wrote none."""
        src = self.work / SCRATCH_DIR
        if src.is_symlink() or not src.is_dir():
            if src.is_symlink() or src.exists():
                src.unlink()
            return
        shutil.rmtree(self.scratch, ignore_errors=True)
        shutil.move(str(src), str(self.scratch))

    def env(self, auth_vars):
        return tool_env(self.home, auth_vars, self.bin)

    def prompt(self, text):
        return fill_prompt(text, self.prompt_paths)

    def keep(self, dest, auth_vars, extra=()):
        keep(self.root, dest, auth_vars, extra)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def prepare(case, checkout_dir, tool_word, parts=None, prepare_home=None, remote=False):
    """A Run for `case` (as `cases.load()` returns it): its parts (default `case["install"]`) installed for
    `tool_word` from `checkout_dir`, the fixture as a git repo, `input/` a copy of the case's (else empty),
    `scratch/` empty and the `gh` stub (`remote`: a run whose prompt names the sandbox's paths). `prepare_home(home)`, when given (an adapter's), runs just before the
    install. Raises InstallError, leaving nothing behind."""
    run = Run(Path(tempfile.mkdtemp(prefix="evals-run-")).resolve(), remote=remote)
    try:
        run.home.mkdir()
        run.scratch.mkdir()
        if case.get("input"):
            shutil.copytree(case["input"], run.input, symlinks=True)
        else:
            run.input.mkdir()
        write_gh_stub(run.bin, run.gh_calls)
        make_work(case["fixture"], run.work, case.get("settings"))
        if prepare_home is not None:
            prepare_home(run.home)
        install(checkout_dir, run.home, tool_word, case["install"] if parts is None else parts)
    except BaseException:
        run.close()
        raise
    return run
