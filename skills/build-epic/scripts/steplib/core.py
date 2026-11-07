"""The step contract: one stdout line, every subprocess's output in `<run>/logs/<id>-<step>.log`, exit 1 on FAIL.

A step script calls `main(step, fn)`. `fn(ctx)` returns a state, or `(state, details)`, and the step prints
`OK <id> <state> [details]`; it raises `Fail(reason)` to print `FAIL <id> <reason> (log: <path>)`. Any other
exception becomes `FAIL <id> error (log: <path>)`, its traceback in the log.
"""
import contextlib
import datetime
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback
from collections import namedtuple
from pathlib import Path

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

Result = namedtuple("Result", "rc out err")

# The run folder of the step running in this process, set by `main`; `gh.identity_env` reads its `run.md`.
RUN_DIR = None


class Fail(Exception):
    """A clean failure: `reason` goes on the FAIL line."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


WINDOWS = os.name == "nt"


def home():
    """The user's home folder: `HOME` when set, as in every POSIX shell and Git Bash. On Windows, Python's own
    `expanduser` ignores `HOME` for the profile folder, which is not where `install.sh` linked `~/.agents`."""
    return os.environ.get("HOME") or os.path.expanduser("~")


def resolve(path):
    """A path with every link and `..` resolved, whatever the current folder, so all callers name one file."""
    path = str(path)
    if path == "~" or path.startswith(("~/", "~" + os.sep)):
        path = home() + path[1:]
    else:
        path = os.path.expanduser(path)
    return Path(os.path.realpath(path))


def split(command):
    """A command line split into words as a POSIX shell would. On Windows a backslash is taken as a path separator,
    not an escape, so a path such as `C:\\x\\tool.sh` survives."""
    return shlex.split(command.replace("\\", "/") if WINDOWS else command)


def _which(name, path):
    """The file `name` names on `path` (an `os.pathsep` list), in `path`'s order: a Windows program (`PATHEXT`) or a
    `#!` script; a path with a folder may name any file. None when there is none."""
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";") if e]
    named = os.path.splitext(name)[1].lower() in exts
    dirs = [""] if os.path.dirname(name) else [d for d in path.split(os.pathsep) if d]
    for d in dirs:
        base = os.path.join(d, name)
        for cand in [base] if named else [base + e for e in exts]:
            if os.path.isfile(cand):
                return cand
        if not named and os.path.isfile(base) and (not d or _shebang(base) is not None):
            return base
    return None


def _shebang(script):
    """The words of `script`'s `#!` line, or None without one."""
    try:
        with open(script, "rb") as f:
            line = f.readline(512)
    except OSError:
        return None
    if not line.startswith(b"#!"):
        return None
    return line[2:].decode("utf-8", "replace").split()


def command(argv, env=None, cwd=None):
    """`argv` in a form the OS can start. Unchanged off Windows. Windows cannot start a `#!` script, and looks for a
    bare name in its own folders before `PATH` (so `bash` is WSL's): there the program is looked up on `env`'s `PATH`
    in order, and a script runs through the interpreter its `#!` line names (`python*` as this Python), else `bash`."""
    argv = [str(a) for a in argv]
    if not WINDOWS:
        return argv
    path = (env if env is not None else os.environ).get("PATH", "")
    name = argv[0]
    if os.path.dirname(name) and not os.path.isabs(name) and cwd is not None:
        name = os.path.normpath(os.path.join(str(cwd), name))
    prog = _which(name, path)
    if prog is None:
        return argv
    if os.path.splitext(prog)[1].lower() in (".exe", ".com", ".bat", ".cmd"):
        return [prog, *argv[1:]]
    words = _shebang(prog) or ["bash"]
    if os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if w != "-S"] or ["bash"]
    interp, args = words[0], words[1:]
    if not os.path.isfile(interp):
        base = os.path.basename(interp.replace("\\", "/"))
        interp = sys.executable if base.startswith("python") else (_which(base, path) or shutil.which(base) or base)
    return [interp, *args, prog, *argv[1:]]


@contextlib.contextmanager
def file_lock(path):
    """An exclusive lock held on `path` (created if missing) for the `with` block; blocks until it is free."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows
        fcntl = None
    with open(path, "a+", encoding="utf-8") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def run(argv, log, cwd=None, env=None, input=None):
    """Runs `argv` (a list, never a shell string) and returns `Result(rc, out, err)`.

    Both streams are captured apart from the exit code and appended to `log` (an open text file), so the rc is never
    lost to a redirect or a pipe. stdin is closed unless `input` is given.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise TypeError("run() takes a non-empty argument list, not a shell string")
    argv = [str(a) for a in argv]
    log.write(f"$ {' '.join(argv)}" + (f"  (in {cwd})" if cwd else "") + "\n")
    log.flush()
    try:
        p = subprocess.run(command(argv, env, cwd), cwd=None if cwd is None else str(cwd), env=env, input=input,
                           stdin=None if input is not None else subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        res = Result(p.returncode, p.stdout, p.stderr)
    except OSError as e:
        res = Result(127, "", f"{argv[0]}: {e}\n")
    for text in (res.out, res.err):
        if text:
            log.write(text if text.endswith("\n") else text + "\n")
    log.write(f"[rc {res.rc}]\n")
    log.flush()
    return res


class Context:
    """What a step's `fn` gets: the resolved run folder, the id, the extra arguments and the open log."""

    def __init__(self, step, run_dir, ident, args, log, log_path):
        self.step, self.run_dir, self.id, self.args, self.log, self.log_path = step, run_dir, ident, args, log, log_path

    def run(self, argv, **kw):
        return run(argv, self.log, **kw)

    def note(self, text):
        self.log.write(text.rstrip("\n") + "\n")
        self.log.flush()


def main(step, fn, takes_id=True, usage="", argv=None):
    """Runs one step and exits: 0 on OK, 1 on FAIL, 2 on a usage error. Prints exactly one stdout line.

    Arguments are `<run> <id> [args...]`; with `takes_id=False` they are `<run> [args...]` and the id is `run`.
    """
    argv = sys.argv[1:] if argv is None else argv
    out = sys.stdout
    if hasattr(out, "reconfigure"):  # Windows would end the line `\r\n` and encode it in the ANSI code page
        out.reconfigure(encoding="utf-8", newline="\n")
    need = 2 if takes_id else 1
    if len(argv) < need:
        ident = argv[1] if len(argv) > 1 and takes_id else "run"
        out.write(f"FAIL {ident} usage: {step}.py <run>{' <id>' if takes_id else ''} {usage}".rstrip() + "\n")
        sys.exit(2)
    ident = argv[1] if takes_id else "run"
    if not ID.match(ident):
        # One id per call: a list that was never split (zsh) arrives as one argument and is refused, not acted on.
        out.write(f"FAIL {ident.split()[0] if ident.split() else '?'} bad-id: one id per call, got {ident!r}\n")
        sys.exit(2)
    global RUN_DIR
    run_dir = RUN_DIR = resolve(argv[0])
    args = argv[need:]
    log_path = run_dir / "logs" / f"{ident}-{step}.log"
    if takes_id and not run_dir.is_dir():
        out.write(f"FAIL {ident} no-run-folder: {run_dir}\n")
        sys.exit(1)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8")
    except OSError as e:
        out.write(f"FAIL {ident} no-run-folder: {run_dir}: {e.strerror}\n")
        sys.exit(1)
    with log:
        log.write(f"== {step} {ident} {' '.join(args)} at {datetime.datetime.now().isoformat(timespec='seconds')}\n")
        log.flush()
        ctx = Context(step, run_dir, ident, args, log, log_path)
        try:
            with contextlib.redirect_stdout(log):
                got = fn(ctx)
            state, details = got if isinstance(got, tuple) else (got, "")
            line, code = f"OK {ident} {state}" + (f" {details}" if details else ""), 0
        except Fail as e:
            log.write(f"FAIL: {e.reason}\n")
            line, code = f"FAIL {ident} {e.reason} (log: {log_path})", 1
        except BaseException as e:  # noqa: B036 - every escape, KeyboardInterrupt and SystemExit too, is one FAIL line
            log.write(traceback.format_exc())
            reason = "interrupted" if isinstance(e, KeyboardInterrupt) else "error"
            line, code = f"FAIL {ident} {reason} (log: {log_path})", 1
        log.write(line + "\n")
    out.write(line.replace("\n", " ") + "\n")
    out.flush()
    sys.exit(code)
