#!/usr/bin/env python3
"""Checks that the README's usage section names every registered command and flag, and nothing else.

Usage: python3 -I docs_check.py <run> [<worktree>]

The `docs_commands` Setting is a command printing every command and flag the repo registers, one per line. It runs
from `<worktree>` (default: the run's repo, `run.md`'s `Repo:`), and its list is compared with the section of
`<worktree>/README.md` under the heading the `docs_readme_section` Setting names (default `Usage`), which runs to the
next heading of the same or a higher level. A name is documented when it is a word anywhere in the section; the
section names a command or flag by putting it in an inline code span (`status`, `run --force <task>`): every word of a
span counts, `<...>` placeholders, brackets and words without a letter left out. So a program name or an example
belongs in prose or a fenced block, which name nothing.

Prints `OK run docs-check commands=<n>`, or `OK run docs-check skipped` when `docs_commands` is unset.
`ship_ticket.py open` runs the same check on the ticket head, failing with the ticket's id.

FAIL reasons:
- `docs-drift [missing=<a,b>] [unknown=<c>]`: registered names the section lacks (`missing`), and names its code spans
  hold that the command does not print (`unknown`), each in list order.
- `docs-command`: the command failed or printed nothing.
- `no-readme-section: <heading>`: no `README.md`, or no such heading in it.
- `no-worktree: <path>`: `<worktree>` is not a folder.
- `usage: ...`: more than one argument after `<run>`.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steplib import core, merge, runfolder  # noqa: E402

USAGE = "usage: docs_check.py <run> [<worktree>]"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
SPAN = re.compile(r"`([^`\n]+)`")
PLACEHOLDER = re.compile(r"<[^>]*>")
WORD = re.compile(r"-{0,2}[A-Za-z0-9_][A-Za-z0-9_.:-]*")


def registered(worktree, command, log):
    """The names `command` prints, one per line, in order and without repeats. Fails `docs-command`."""
    res = core.run(core.split(command), log, cwd=worktree)
    names = list(dict.fromkeys(line.strip() for line in res.out.splitlines() if line.strip()))
    if res.rc != 0 or not names:
        raise core.Fail("docs-command")
    return names


def section(readme, heading):
    """The lines of `readme` under `heading`, to the next heading of its level or higher, fenced blocks kept whole.
    Fails `no-readme-section`."""
    lines = readme.read_text(encoding="utf-8").splitlines() if readme.is_file() else []
    level, body, fenced = None, [], False
    for line in lines:
        m = None if fenced else HEADING.match(line)
        if FENCE.match(line):
            fenced = not fenced
        if m and level is not None and len(m.group(1)) <= level:
            break
        if level is not None:
            body.append(line)
        elif m and m.group(2) == heading:
            level = len(m.group(1))
    if level is None:
        raise core.Fail(f"no-readme-section: {heading}")
    return body


def words(text):
    """The name-shaped words of `text`: placeholders dropped, trailing dots cut, words without a letter left out."""
    found = (w.rstrip(".:") for w in WORD.findall(PLACEHOLDER.sub(" ", text)))
    return [w for w in found if re.search(r"[A-Za-z]", w)]


def drift(names, body):
    """(missing, unknown): `names` absent from `body`'s words, and its code spans' words not in `names`."""
    every = set(words("\n".join(body)))
    prose, fenced = [], False
    for line in body:
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            prose.append(line)
    spans = [w for span in SPAN.findall("\n".join(prose)) for w in words(span)]
    missing = [n for n in names if n not in every]
    unknown = list(dict.fromkeys(w for w in spans if w not in names))
    return missing, unknown


def check(worktree, log, repo=None):
    """Runs the docs check on `worktree`, reading the Settings for `repo` (default: `worktree`). Returns the number of
    registered names, or None when `docs_commands` is unset; fails `docs-drift ...`."""
    repo = repo or worktree
    command = merge.setting(repo, "docs_commands", log)
    if not command:
        return None
    heading = merge.setting(repo, "docs_readme_section", log) or "Usage"
    names = registered(worktree, command, log)
    missing, unknown = drift(names, section(Path(worktree) / "README.md", heading))
    if missing or unknown:
        parts = [f"missing={','.join(missing)}"] * bool(missing) + [f"unknown={','.join(unknown)}"] * bool(unknown)
        log.write(f"README.md section {heading!r} against {command!r}: {' '.join(parts)}\n")
        raise core.Fail(f"docs-drift {' '.join(parts)}")
    return len(names)


def step(ctx):
    if len(ctx.args) > 1:
        raise core.Fail(USAGE)
    worktree = core.resolve(ctx.args[0]) if ctx.args else core.resolve(runfolder.run_info(ctx.run_dir)["Repo"])
    if not worktree.is_dir():
        raise core.Fail(f"no-worktree: {worktree}")
    n = check(worktree, ctx.log)
    return "docs-check", "skipped" if n is None else f"commands={n}"


if __name__ == "__main__":
    core.main("docs_check", step, takes_id=False, usage="[<worktree>]")
