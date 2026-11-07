#!/usr/bin/env python3
"""Sets up the run's one-time facts: its test environment, its test command, whether CI runs, who merges, and the
tools its suite skips without.

Usage: python3 -I setup.py <run>
Prints `OK run set up ci=<on|off> env=<run>/env skips=<n>`.

- **Environment**: `map.md`'s `Setup` runs once, each command under `bash -c` in the repo with `BUILD_EPIC_ENV` set
  to `<run>/env/`, where it puts what it makes (e.g. `python3 -m venv "$BUILD_EPIC_ENV"`). The line is `Setup:` or
  `Setup (<anything>):`; `Setup: none` runs nothing; otherwise the commands are the code spans on the line, or the
  code block after it. Any other line starting `Setup` fails `unclear-setup`, never meaning none. Once it succeeds,
  `<run>/env/.setup-done` is written and a rerun does not run `Setup` again; a failed `Setup` fails `setup-failed`
  and runs again next time.
- **Test command**: `map.md`'s `Checks` joined with `&&`, inside the run's environment (`steplib/checks.py`'s
  `in_env`: `export BUILD_EPIC_ENV=<env>` once `Setup` made it, then `. <env>/bin/activate` when it made a
  virtualenv there), as the merge runs them. Recorded as `decisions.md`'s `test_command:`; every agent is given that
  exact line.
- **CI**: `ci: on` when the repo's Actions are enabled and a `.github/workflows/*.yml` on the base (`decisions.md`'s
  `base:`, else `run.md`'s `Default:`) triggers on `pull_request` (or `pull_request_target`) into it, its `branches`
  and `branches-ignore` filters applied; else `ci: off`. When the Actions setting cannot be read, the workflows decide.
- **Identity**: `merge_identity:` from the `merge_identity` Setting (default `app`), and `human:` from `gh api user`
  run with the tokens unset (left unset when that does not answer), each recorded only when `decisions.md` has none:
  a rerun after the Setting or the login changed leaves the record, so the merge still refuses (`wrong-identity`).
- **Skipped tools**: every Checks command, once, inside the run's environment, in a throwaway worktree of the base
  (removed after), never the human's checkout. A command `map.md` also names with `--all` in a code span (e.g.
  `` `tests/run.sh --all` ``) runs as that, so a runner that picks only changed suites runs them all. Their skip
  reasons (`steplib/checks.py`'s `skip_reasons`) are written to `<run>/skips.md`, one row per tool (`(not named)` for
  a reason naming none); `skips=<n>` counts the rows. A red run still lists its skips. A rerun keeps an existing
  `skips.md`; delete it to list them again.
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import checks, core, decisions, gh, gitops, identity, runfolder, tracker  # noqa: E402

ENV_VAR = checks.ENV_VAR
DONE = checks.SETUP_DONE
PR_EVENTS = ("pull_request", "pull_request_target")
NOT_NAMED = "(not named)"
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


# -- map.md's Setup ----------------------------------------------------------------------------------------------

def setup_commands(map_md):
    """The commands of `map.md`'s `Setup:` (or `Setup (<anything>):`) line: none for `Setup: none` or no line
    starting `Setup`; the code spans on the line; else the lines of the code block after it. Fails `unclear-setup` on
    any other line starting `Setup`."""
    text = map_md.read_text(encoding="utf-8")
    line = checks.SETUP_LINE.search(text)
    if not line:
        return []
    m = checks.SETUP_HEADING.match(line.group(0))
    if not m:
        raise core.Fail(f"unclear-setup: {line.group(0)[:80]!r} is neither `Setup:` nor `Setup (...):`")
    rest = m.group(1).strip()
    if re.match(r"none\b", rest, re.IGNORECASE):
        return []
    spans = re.findall(r"`([^`\n]+)`", rest)
    if spans:
        return spans
    block = FENCE.search(text, line.end())
    if rest or not block:
        raise core.Fail("unclear-setup: map.md's Setup is neither none, code spans, nor a code block")
    return [line.strip() for line in block.group(1).splitlines() if line.strip() and not line.strip().startswith("#")]


def make_env(ctx, repo, commands):
    """Runs `commands` once into `<run>/env/`; returns the env folder. With no command it stays empty and unmade."""
    env_dir = ctx.run_dir / "env"
    env_dir.mkdir(exist_ok=True)
    done = env_dir / DONE
    if not commands:
        return env_dir
    if done.exists():
        ctx.note(f"{env_dir}: set up before, Setup not run again")
        return env_dir
    environ = {**os.environ, ENV_VAR: env_dir.as_posix()}  # as the test command exports it (`checks.env_prefix`)
    for cmd in commands:
        if ctx.run(["bash", "-c", cmd], cwd=repo, env=environ).rc != 0:
            raise core.Fail(f"setup-failed: {cmd}")
    done.write_text("".join(c + "\n" for c in commands), encoding="utf-8")
    return env_dir


def test_command(run, suites):
    """The one line every agent runs its tests with: the suites inside the run's environment."""
    return checks.in_env(run, " && ".join(suites))


# -- CI ------------------------------------------------------------------------------------------------------------

def _uncomment(line):
    """`line` without its `#` comment (a `#` at the start or after a space, outside quotes)."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(s):
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def _items(value):
    """A scalar or a flow list (`[a, 'b']`) as a list of strings."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
    return [_unquote(value)] if value else []


def _key(text):
    """(key, inline value) for a `key: value` line, or None."""
    m = re.match(r"""^(["']?)([^"':]+)\1[ \t]*:(?:[ \t]+(.*))?$""", text)
    return (m.group(2).strip(), (m.group(3) or "").strip()) if m else None


def _lines(text):
    """The workflow's non-blank lines as (indent, text), comments removed."""
    out = []
    for raw in text.splitlines():
        line = _uncomment(raw)
        if line.strip():
            out.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return out


def _children(lines, i):
    """The lines nested under `lines[i]`."""
    indent, out = lines[i][0], []
    for line in lines[i + 1:]:
        if line[0] <= indent:
            break
        out.append(line)
    return out


def _mapping(lines):
    """The top level of a block of lines as {key: (inline value, child lines)}; `- item` lines as keys too."""
    if not lines:
        return {}
    top, got = min(ind for ind, _ in lines), {}
    for i, (ind, text) in enumerate(lines):
        if ind != top:
            continue
        if text.startswith("- "):
            got[_unquote(text[2:])] = ("", [])
            continue
        kv = _key(text)
        if kv:
            got[kv[0]] = (kv[1], _children(lines, i))
    return got


def _list(inline, children):
    return _items(inline) if inline else [_unquote(t[2:]) for _, t in children if t.startswith("- ")]


def _glob(pattern, branch):
    """GitHub's branch filter: `*` matches within a path segment, `**` across them, `?` one character."""
    rx = ""
    for part in re.split(r"(\*\*|\*|\?)", pattern):
        rx += {"**": ".*", "*": "[^/]*", "?": "."}.get(part, re.escape(part))
    return re.fullmatch(rx, branch) is not None if pattern else False


def _branches_match(patterns, branch):
    """A `branches:` filter: the last pattern matching `branch` decides, a `!` pattern excluding it."""
    hit = False
    for p in patterns:
        if p.startswith("!"):
            hit = False if _glob(p[1:], branch) else hit
        elif _glob(p, branch):
            hit = True
    return hit


def triggers_on_pr(text, base):
    """True when the workflow `text` runs on a pull request into `base`."""
    lines = _lines(text)
    top = [(i, kv) for i, (ind, t) in enumerate(lines) if ind == 0 for kv in [_key(t)] if kv]
    on = next(((i, v) for i, (k, v) in top if k in ("on", "true")), None)
    if on is None:
        return False
    i, inline = on
    if inline:
        if inline.startswith("{"):  # a flow mapping: taken as no branch filter
            return any(e in inline for e in PR_EVENTS)
        return any(e in _items(inline) for e in PR_EVENTS)
    events = _mapping(_children(lines, i))
    for event in PR_EVENTS:
        if event not in events:
            continue
        value, children = events[event]
        if value.startswith("{"):
            return True
        filters = _mapping(children)
        if "branches" in filters and not _branches_match(_list(*filters["branches"]), base):
            continue
        if "branches-ignore" in filters and any(_glob(p, base) for p in _list(*filters["branches-ignore"])):
            continue
        return True
    return False


def actions_enabled(repo, log):
    """True or False from the repo's Actions setting, or None when it cannot be read."""
    res = gh.gh(["api", "repos/{owner}/{repo}/actions/permissions", "--jq", ".enabled"], log, cwd=repo)
    if res.rc != 0:
        log.write("Actions setting unreadable: the workflows decide\n")
        return None
    return res.out.strip() == "true"


def ci_runs(ctx, repo, base):
    """True when CI runs on PRs into `base`."""
    if actions_enabled(repo, ctx.log) is False:
        ctx.note("Actions are disabled: ci off")
        return False
    git = gitops.Git(repo, ctx.log)
    git.fetch("origin", base)
    ref = f"refs/remotes/origin/{base}"
    if git._git("rev-parse", "--verify", "--quiet", ref, check=False).rc != 0:
        raise core.Fail(f"no-base: origin has no {base}")
    listed = git._git("ls-tree", "--name-only", f"{ref}:.github/workflows", check=False)
    for name in listed.out.splitlines() if listed.rc == 0 else ():
        if not name.endswith((".yml", ".yaml")):
            continue
        shown = git._git("show", f"{ref}:.github/workflows/{name}", check=False)
        if shown.rc == 0 and triggers_on_pr(shown.out, base):
            ctx.note(f"{name} runs on pull requests into {base}: ci on")
            return True
    ctx.note(f"no workflow runs on pull requests into {base}: ci off")
    return False


# -- skipped tools -------------------------------------------------------------------------------------------------

def _cell(text):
    return text.replace("\\", "\\\\").replace("|", "\\|")


def full_suites(map_md, suites):
    """`suites`, each as its `--all` form when `map.md` names that in a code span."""
    text = map_md.read_text(encoding="utf-8")
    return [f"{s} --all" if f"`{s} --all`" in text else s for s in suites]


def run_in_base(ctx, repo, base, suites):
    """Runs `suites` in the run's environment in a throwaway worktree of `origin/<base>`; returns their output and
    the last exit code. The worktree is removed on any exit."""
    tmp = Path(tempfile.mkdtemp(prefix="build-epic-skips-"))
    wt = tmp / "base"
    git = gitops.Git(repo, ctx.log)
    git.worktree_add(wt, f"origin/{base}", detach=True)
    try:
        outs, rc = [], 0
        for suite in suites:
            res = ctx.run(["bash", "-c", checks.in_env(ctx.run_dir, suite)], cwd=wt)
            outs += [res.out, res.err]
            rc = res.rc or rc
        return "\n".join(outs), rc
    finally:
        git.worktree_remove(wt)
        shutil.rmtree(tmp, ignore_errors=True)


def list_skips(ctx, repo, base, map_md, suites):
    """Runs every suite once in a worktree of the base and writes `<run>/skips.md`; returns its row count. An
    existing `skips.md` is kept."""
    path = ctx.run_dir / "skips.md"
    if not path.exists():
        full = full_suites(map_md, suites)
        out, rc = run_in_base(ctx, repo, base, full)
        by_tool = {}
        for skip in checks.skip_reasons(out):
            reasons = by_tool.setdefault(skip.tool or NOT_NAMED, [])
            if skip.reason not in reasons:
                reasons.append(skip.reason)
        rows = "".join(f"| {_cell(t)} | {_cell('; '.join(r))} |\n" for t, r in by_tool.items())
        path.write_text(f"# Skipped tools\n\nran: {' && '.join(full)} (in origin/{base})\nrc: {rc}\n\n"
                        f"| tool | reasons |\n| --- | --- |\n{rows}", encoding="utf-8")
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.startswith("| ") and not line.startswith(("| tool |", "| --- |")))


# -- the step ------------------------------------------------------------------------------------------------------

def step(ctx):
    if ctx.args:
        raise core.Fail("usage: setup.py <run>")
    info = runfolder.run_info(ctx.run_dir)
    repo = core.resolve(info["Repo"])
    map_md = ctx.run_dir / "map.md"
    if not map_md.is_file():
        raise core.Fail(f"no-map: {map_md}")
    suites = checks.commands(map_md)
    commands = setup_commands(map_md)
    env_dir = make_env(ctx, repo, commands)
    command = test_command(ctx.run_dir, suites)

    base = decisions.get(ctx.run_dir, "base") or info["Default"]
    ci = "on" if ci_runs(ctx, repo, base) else "off"
    who = identity.mode(ctx.run_dir, lambda: tracker.setting("build-epic", "merge_identity", str(repo), ctx.log))
    human = identity.human_login(repo, ctx.log)
    facts = {"ci": ci, "test_command": command}
    kept = {"merge_identity": who, "human": human}  # recorded once: a rerun never widens who may merge
    for key, value in {**facts, **kept}.items():
        if value:
            decisions.check_value(key, value)
    with decisions.lock(ctx.run_dir):
        d = decisions.load(ctx.run_dir) or decisions.Decisions()
        d.headers.update(facts)
        for key, value in kept.items():
            if value and not (d.headers.get(key) or "").strip():
                d.headers[key] = value
            elif value and d.headers[key].strip() != value:
                ctx.note(f"{key}: decisions.md keeps {d.headers[key].strip()}, now {value}")
        decisions.save(ctx.run_dir, d)

    skips = list_skips(ctx, repo, base, map_md, suites)
    return "set up", f"ci={ci} env={env_dir} skips={skips}"


if __name__ == "__main__":
    core.main("setup", step, takes_id=False)
