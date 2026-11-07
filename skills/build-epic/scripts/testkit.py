"""Test helpers for step scripts. Stdlib only.

`Kit` gives one sandbox: a temp `HOME` with a git identity, a bare `origin` with a clone on `main`, a stub `gh` first
on `PATH`, and helpers to run a step from another folder, through a link, or under `zsh -c`. The origin, its clone and
the run folder `init_run.py` writes are built once per process and copied into each sandbox, with their paths rewritten.

The stub `gh` appends each call's argv as one JSON line to `kit.gh_log` and answers from files in `kit.answers`:
for a call whose first two arguments are `pr merge`, the key is `pr-merge`; the n-th such call prints
`<key>.<n>.out` to stdout and `<key>.<n>.err` to stderr and exits with `<key>.<n>.rc`, falling back to `<key>.out`,
`<key>.err` and `<key>.rc`, and to empty output and rc 0. `kit.answer(...)` writes those files. Each call's
`GH_TOKEN` (empty when unset) goes on one line of `kit.gh_tokens()`.
"""
import atexit
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from steplib import core

SCRIPTS = Path(__file__).resolve().parent

STUB_GH = r'''#!{python} -IS
import os, sys
d = os.environ["GH_STUB_DIR"]

def quote(s):  # json.dumps(s), without importing json: that import is half this stub's start-up time
    out = []
    for c in s:
        o = ord(c)
        if o > 0xFFFF:
            o -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
        else:
            out.append(c if " " <= c <= "~" and c not in '"\\' else "\\u%04x" % o)
    return '"' + "".join(out) + '"'

with open(os.path.join(d, "gh.log"), "a", encoding="utf-8") as f:
    f.write("[" + ", ".join(map(quote, sys.argv[1:])) + "]\n")
with open(os.path.join(d, "gh-tokens.log"), "a", encoding="utf-8") as f:
    f.write(os.environ.get("GH_TOKEN", "") + "\n")
key = "".join(c if c.isascii() and (c.isalnum() or c in "_.-") else "_" for c in "-".join(sys.argv[1:3])) or "none"
count = os.path.join(d, "answers", key + ".count")
n = int(open(count, encoding="utf-8").read()) + 1 if os.path.exists(count) else 1
open(count, "w", encoding="utf-8").write(str(n))

def part(ext, default=""):
    for name in (f"{{key}}.{{n}}.{{ext}}", f"{{key}}.{{ext}}"):
        p = os.path.join(d, "answers", name)
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    return default

sys.stdout.write(part("out"))
sys.stderr.write(part("err"))
sys.exit(int(part("rc", "0").strip() or 0))
'''

def env_tool_setup(tool, output):
    """A `map.md` `Setup:` block that installs `tool` into the run's environment, as a virtualenv would: an executable
    `$BUILD_EPIC_ENV/bin/<tool>` printing `output`, and a `bin/activate` putting that folder first on `PATH`. Only a
    suite run inside that environment finds `tool`."""
    make = (f'mkdir -p "$BUILD_EPIC_ENV/bin" && printf \'#!/bin/sh\\necho "%s"\\n\' {shlex.quote(output)} '
            f'> "$BUILD_EPIC_ENV/bin/{tool}" && chmod +x "$BUILD_EPIC_ENV/bin/{tool}"')
    activate = 'echo \'PATH="$BUILD_EPIC_ENV/bin:$PATH"\' > "$BUILD_EPIC_ENV/bin/activate"'
    return f"Setup:\n\n```\n{make}\n{activate}\n```\n"


zsh = shutil.which("zsh")
skip_without_zsh = unittest.skipUnless(zsh, "zsh is not installed")


def git(*args, cwd=None, env=None):
    """Runs git and returns its stdout; raises on a non-zero exit."""
    p = subprocess.run(["git", *args], cwd=None if cwd is None else str(cwd), env=env, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


_template = None


def _template_kit():
    """The Kit every sandbox copies its origin, clone and initialised run folder from; built on first use."""
    global _template
    if _template is None:
        kit = Kit()
        atexit.register(kit.close)
        kit._build_origin()
        for sample in (*kit.origin.glob("hooks/*.sample"), *kit.clone.glob(".git/hooks/*.sample")):
            sample.unlink()  # git's inert examples: copying them is most of the copy's cost
        p = kit.step(SCRIPTS / "init_run.py", kit.run_dir, kit.clone)
        if p.returncode != 0:
            raise AssertionError(f"init_run.py failed: {p.stdout}{p.stderr}")
        _template = kit
    return _template


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
        (self.bin / "gh").write_text(STUB_GH.format(python=sys.executable), encoding="utf-8")
        (self.bin / "gh").chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "BUILD_EPIC_"))}
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        GH_STUB_DIR=str(self.stub), GIT_CONFIG_NOSYSTEM="1", BUILD_EPIC_RETRY_SLEEP="0")
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.origin = self.clone = None

    def close(self):
        def writable(fn, path, *_):  # git's objects are read-only, and Windows will not delete a read-only file
            os.chmod(path, 0o700)
            fn(path)
        try:
            shutil.rmtree(self.tmp, **{"onexc" if sys.version_info >= (3, 12) else "onerror": writable})
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def git(self, *args, cwd=None):
        return git(*args, cwd=cwd, env=self.env)

    def make_origin(self):
        """A bare `origin` with one commit on `main` and a clone of it; returns the clone's path."""
        template = _template_kit()
        self.origin = self.tmp / "origin.git"
        self.clone = self.tmp / "clone"
        shutil.copytree(template.origin, self.origin, symlinks=True)
        shutil.copytree(template.clone, self.clone, symlinks=True)
        self._rehome(template, self.clone / ".git" / "config")
        return self.clone

    def init_run(self):
        """What `init_run.py <run> <clone>` writes into the run folder (`run.md` and its log), after `make_origin`."""
        template = _template_kit()
        for src in template.run_dir.rglob("*"):
            dst = self.run_dir / src.relative_to(template.run_dir)
            if src.is_dir():
                dst.mkdir(exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                self._rehome(template, dst)

    def branch_from_main(self, branch):
        """What `git push origin main:<branch>` and a fetch leave in the clone, without their two round trips: `branch`
        on `origin` at `main`'s tip, and `origin/<branch>` tracking it."""
        tip = self.git("rev-parse", "main", cwd=self.clone)
        self.git("--git-dir", str(self.origin), "update-ref", f"refs/heads/{branch}", tip)
        self.git("update-ref", f"refs/remotes/origin/{branch}", tip, cwd=self.clone)

    def _rehome(self, template, path):
        """Points `path`'s text at this sandbox instead of the template's. git's config writes a backslash as `\\\\`,
        so on Windows a path there reads doubled; both forms are rewritten, or the copy would push to the template."""
        text, old, new = path.read_text(encoding="utf-8"), str(template.tmp), str(self.tmp)
        text = text.replace(old.replace("\\", "\\\\"), new.replace("\\", "\\\\")).replace(old, new)
        path.write_text(text, encoding="utf-8")

    def _build_origin(self):
        self.origin = self.tmp / "origin.git"
        self.git("init", "--bare", "-b", "main", str(self.origin))
        self.clone = self.tmp / "clone"
        self.git("clone", "-q", str(self.origin), str(self.clone))
        (self.clone / "README.md").write_text("hello\n", encoding="utf-8")
        self.git("add", "README.md", cwd=self.clone)
        self.git("commit", "-qm", "first", cwd=self.clone)
        self.git("push", "-q", "origin", "main", cwd=self.clone)
        self.git("remote", "set-head", "origin", "main", cwd=self.clone)
        return self.clone

    def answer(self, key, out="", err="", rc=0, n=None):
        """Sets the stub `gh`'s answer for calls keyed `key` (e.g. `pr-merge`): the n-th call, or every call."""
        stem = f"{key}.{n}" if n else key
        (self.answers / f"{stem}.out").write_text(out, encoding="utf-8")
        (self.answers / f"{stem}.err").write_text(err, encoding="utf-8")
        (self.answers / f"{stem}.rc").write_text(str(rc), encoding="utf-8")

    def gh_calls(self):
        """Every argv the stub `gh` got, in order."""
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text(encoding="utf-8").splitlines()]

    def gh_tokens(self):
        """The `GH_TOKEN` each call of the stub `gh` ran with ("" when unset), in order."""
        path = self.stub / "gh-tokens.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def link(self, target, name):
        """A symlink `<tmp>/<name>` to `target`; returns its path."""
        at = self.tmp / name
        at.symlink_to(target)
        return at

    def step(self, script, *args, cwd="/", env=None):
        """Runs a step script as `python3 -I <script> <args>` from `cwd` (default `/`); returns the CompletedProcess."""
        return subprocess.run([sys.executable, "-I", str(script), *map(str, args)], cwd=str(cwd),
                              env=env or self.env, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def tool(self, *argv):
        """Runs a program or `#!` script (as Windows can start it) with the sandbox's env; returns the
        CompletedProcess."""
        return subprocess.run(core.command(argv, self.env), env=self.env, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def shell(self, shell, command, cwd="/"):
        """Runs `command` under `<shell> -c` (`bash` or `zsh`) from `cwd`; returns the CompletedProcess."""
        return subprocess.run(core.command([shell, "-c", command], self.env), cwd=str(cwd), env=self.env, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
