"""Test helpers for step scripts. Stdlib only.

`Kit` gives one sandbox: a temp `HOME` with a git identity, a bare `origin` with a clone on `main`, a stub `gh` first
on `PATH`, and helpers to run a step from another folder, through a link, or under `zsh -c`.

The stub `gh` appends each call's argv as one JSON line to `kit.gh_log` and answers from files in `kit.answers`:
for a call whose first two arguments are `pr merge`, the key is `pr-merge`; the n-th such call prints
`<key>.<n>.out` to stdout and `<key>.<n>.err` to stderr and exits with `<key>.<n>.rc`, falling back to `<key>.out`,
`<key>.err` and `<key>.rc`, and to empty output and rc 0. `kit.answer(...)` writes those files.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

STUB_GH = r'''#!{python}
import json, os, re, sys
d = os.environ["GH_STUB_DIR"]
with open(os.path.join(d, "gh.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
key = re.sub(r"[^A-Za-z0-9_.-]", "_", "-".join(sys.argv[1:3])) or "none"
count = os.path.join(d, "answers", key + ".count")
n = int(open(count).read()) + 1 if os.path.exists(count) else 1
open(count, "w").write(str(n))

def part(ext, default=""):
    for name in (f"{{key}}.{{n}}.{{ext}}", f"{{key}}.{{ext}}"):
        p = os.path.join(d, "answers", name)
        if os.path.exists(p):
            return open(p).read()
    return default

sys.stdout.write(part("out"))
sys.stderr.write(part("err"))
sys.exit(int(part("rc", "0").strip() or 0))
'''

zsh = shutil.which("zsh")
skip_without_zsh = unittest.skipUnless(zsh, "zsh is not installed")


def git(*args, cwd=None, env=None):
    """Runs git and returns its stdout; raises on a non-zero exit."""
    p = subprocess.run(["git", *args], cwd=None if cwd is None else str(cwd), env=env, text=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


class Kit:
    def __init__(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="steplib-")))
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        self.stub = self.tmp / "gh-stub"
        self.answers = self.stub / "answers"
        self.gh_log = self.stub / "gh.log"
        for d in (self.home, self.bin, self.answers):
            d.mkdir(parents=True)
        (self.bin / "gh").write_text(STUB_GH.format(python=sys.executable))
        (self.bin / "gh").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "BUILD_EPIC_"))}
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        GH_STUB_DIR=str(self.stub), GIT_CONFIG_NOSYSTEM="1", BUILD_EPIC_RETRY_SLEEP="0")
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.origin = self.clone = None

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def git(self, *args, cwd=None):
        return git(*args, cwd=cwd, env=self.env)

    def make_origin(self):
        """A bare `origin` with one commit on `main` and a clone of it; returns the clone's path."""
        self.origin = self.tmp / "origin.git"
        self.git("init", "--bare", "-b", "main", str(self.origin))
        self.clone = self.tmp / "clone"
        self.git("clone", "-q", str(self.origin), str(self.clone))
        (self.clone / "README.md").write_text("hello\n")
        self.git("add", "README.md", cwd=self.clone)
        self.git("commit", "-qm", "first", cwd=self.clone)
        self.git("push", "-q", "origin", "main", cwd=self.clone)
        self.git("remote", "set-head", "origin", "main", cwd=self.clone)
        return self.clone

    def answer(self, key, out="", err="", rc=0, n=None):
        """Sets the stub `gh`'s answer for calls keyed `key` (e.g. `pr-merge`): the n-th call, or every call."""
        stem = f"{key}.{n}" if n else key
        (self.answers / f"{stem}.out").write_text(out)
        (self.answers / f"{stem}.err").write_text(err)
        (self.answers / f"{stem}.rc").write_text(str(rc))

    def gh_calls(self):
        """Every argv the stub `gh` got, in order."""
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def link(self, target, name):
        """A symlink `<tmp>/<name>` to `target`; returns its path."""
        at = self.tmp / name
        at.symlink_to(target)
        return at

    def step(self, script, *args, cwd="/", env=None):
        """Runs a step script as `python3 -I <script> <args>` from `cwd` (default `/`); returns the CompletedProcess."""
        return subprocess.run([sys.executable, "-I", str(script), *map(str, args)], cwd=str(cwd),
                              env=env or self.env, text=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def shell(self, shell, command, cwd="/"):
        """Runs `command` under `<shell> -c` (`bash` or `zsh`) from `cwd`; returns the CompletedProcess."""
        return subprocess.run([shell, "-c", command], cwd=str(cwd), env=self.env, text=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
