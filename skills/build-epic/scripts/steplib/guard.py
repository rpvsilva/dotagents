"""The leave-for-human merge guard: a merge's diff checked against the repo's rules before anything is pushed.

A repo declares its rules in a fenced `agent-merge-guard` block of its `AGENTS.md` (else `CLAUDE.md`), one named rule
per line, `<name> path <glob>` or `<name> added <regex> [in <glob>]` (the format: `../../merge-guard.md`). They are read
from the merge's base on `origin`, never from the PR head, so a PR cannot delete its own rules. A hit passes only when
the run's `decisions.md` Authorizations waive its rule `yes` (the rule name is the clause); a file several rules match
needs each one waived.
"""
import os
import posixpath
import re
import subprocess
from collections import namedtuple

from . import core, decisions

FENCE = "```agent-merge-guard"
FILES = ("AGENTS.md", "CLAUDE.md")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
KINDS = ("path", "added")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

Hit = namedtuple("Hit", "file line rule")


class Rule(namedtuple("Rule", "name kind pattern regex glob")):
    """One rule: `pattern` as written after its kind; `regex` (added rules) and `glob` (a compiled path glob, or None
    for an added rule with no `in`)."""

    def matches_path(self, path):
        return self.glob is not None and self.glob.fullmatch(path) is not None

    def matches_line(self, path, text):
        return (self.glob is None or self.glob.fullmatch(path) is not None) and self.regex.search(text) is not None


def compile_glob(glob):
    """`glob` as a regex over a whole `/`-separated path: `*` and `?` stay inside one folder, `**` crosses folders,
    and `**/` also matches no folder at all."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def _rule(line):
    """The rule `line` declares, or None when it is not one."""
    parts = line.split(None, 2)
    if len(parts) < 3 or not NAME.fullmatch(parts[0]) or parts[1] not in KINDS:
        return None
    name, kind, rest = parts
    if kind == "path":
        return None if len(rest.split()) != 1 else Rule(name, kind, rest, None, compile_glob(rest))
    words = rest.split()
    if words[-1] == "in":
        return None
    regex, glob = rest, None
    if len(words) >= 3 and words[-2] == "in":
        regex, _, glob = rest.rsplit(None, 2)
    try:
        compiled = re.compile(regex)
    except re.error:
        return None
    return Rule(name, kind, rest, compiled, None if glob is None else compile_glob(glob))


def parse(text):
    """The rules of every `agent-merge-guard` block in `text`, in order; [] when it holds none. Blank lines and lines
    starting with `#` are skipped. An unreadable line, a name used twice or an unclosed block raises
    `core.Fail("bad-rule: ...")`."""
    rules, inside = [], False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not inside:
            inside = stripped == FENCE
            continue
        if stripped.startswith("```"):
            inside = False
        elif stripped and not stripped.startswith("#"):
            rule = _rule(stripped)
            if rule is None or any(r.name == rule.name for r in rules):
                raise core.Fail(f"bad-rule: line {number}: {stripped}")
            rules.append(rule)
    if inside:
        raise core.Fail("bad-rule: unclosed agent-merge-guard block")
    return rules


def _git(repo, log, *args, check=True):
    """git's stdout in `repo`, decoded (bytes that are not UTF-8 replaced). Read-only, so it takes no repo lock."""
    argv = ["git", "-C", str(repo), "-c", "core.quotePath=false", *args]
    log.write(f"$ {' '.join(argv)}\n")
    p = subprocess.run(core.command(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = p.stderr.decode("utf-8", "replace")
    log.write((err if not err or err.endswith("\n") else err + "\n") + f"[rc {p.returncode}]\n")
    log.flush()
    if check and p.returncode != 0:
        raise core.Fail(f"git-{args[0]}")
    return p.returncode, p.stdout.decode("utf-8", "replace")


def _blob(repo, ref, path, log):
    """The text of `path` at `ref`, following one symlink inside the tree; None when there is no such file."""
    for _ in range(2):
        rc, out = _git(repo, log, "ls-tree", "-z", ref, "--", path, check=False)
        entry = out.split("\0")[0]
        if rc != 0 or "\t" not in entry:
            return None
        mode, kind, sha = entry.split("\t")[0].split()
        if kind != "blob":
            return None
        text = _git(repo, log, "cat-file", "blob", sha)[1]
        if mode != "120000":
            return text
        path = posixpath.normpath(posixpath.join(posixpath.dirname(path), text.strip()))
        if path.startswith("../"):
            return None
    return None


def load(repo, ref, log):
    """The rules at `ref`: the block in `AGENTS.md`, or in `CLAUDE.md` when `AGENTS.md` has none; [] without one.
    A `ref` that is not a commit raises `core.Fail("guard-no-base: <ref>")`."""
    if _git(repo, log, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)[0] != 0:
        raise core.Fail(f"guard-no-base: {ref}")
    for name in FILES:
        text = _blob(repo, ref, name, log)
        if text is not None and any(line.strip() == FENCE for line in text.splitlines()):
            log.write(f"guard rules from {ref}:{name}\n")
            return parse(text)
    log.write(f"guard: no {FENCE} block at {ref}\n")
    return []


ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}


def _unquote(path):
    """A path as git prints it: C-quoted in double quotes when it holds a quote, a backslash or a control byte, with
    `\\ooo` octal escapes for raw bytes."""
    if not path.startswith('"'):
        return path
    out, i, body = bytearray(), 0, path[1:-1]
    while i < len(body):
        c = body[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
        elif body[i + 1:i + 4].isdigit():
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            out.append(ESCAPES.get(body[i + 1], ord(body[i + 1])))
            i += 2
    return out.decode("utf-8", "replace")


def scan(repo, range_, rules, log):
    """Every hit of `rules` in `git diff <range_>`, in diff order: `Hit(file, line, rule)`. A path rule hits each
    changed path (a deleted file by its old path; renames are a delete and an add) at the new side's first changed
    line (0 when there is none); an added rule hits each `+` line at its number from the hunk header."""
    flags = ["diff", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv"]
    names = _git(repo, log, *flags, "--name-status", "-z", range_, "--")[1].split("\0")
    paths = [p for status, p in zip(names[::2], names[1::2]) if status]
    order = {p: i for i, p in enumerate(paths)}
    first, added = {}, []
    patch = _git(repo, log, *flags, "-U0", "--src-prefix=a/", "--dst-prefix=b/", range_, "--")[1]
    new, line = None, 0
    for text in patch.split("\n"):
        if text.startswith("diff --git "):
            new = None
        elif text.startswith("+++ ") and new is None:
            new = "" if text == "+++ /dev/null" else _unquote(text[4:].rstrip("\t"))[2:]  # a tab ends a spaced name
        elif text.startswith("@@") and new:
            line = int(HUNK.match(text).group(1))
            first.setdefault(new, line)
        elif text.startswith("+") and new:
            added.append((new, line, text[1:]))
            line += 1
    hits = []
    for path in paths:
        hits += [Hit(path, first.get(path, 0), r.name) for r in rules if r.kind == "path" and r.matches_path(path)]
    for path, number, text in added:
        hits += [Hit(path, number, r.name) for r in rules if r.kind == "added" and r.matches_line(path, text)]
    names = [r.name for r in rules]
    return sorted(hits, key=lambda h: (order.get(h.file, len(order)), h.line, names.index(h.rule)))


def check(repo, range_, run, log):
    """Refuses the merge of `range_` (`origin/<base>...<head>`) in `repo` with `core.Fail("guarded <file:line rule>;
    ...")`, naming every hit whose rule `<run>/decisions.md` does not waive `yes`; returns quietly otherwise. The
    rules come from `origin/<base>`. A range in another form raises `guard-bad-range`."""
    base, sep, _ = range_.partition("...")
    if not sep or not base:
        raise core.Fail(f"guard-bad-range: {range_}")
    rules = load(repo, base, log)
    if not rules:
        return
    d = decisions.load(run)
    refused = []
    for hit in scan(repo, range_, rules, log):
        waived = d is not None and d.waived(hit.rule) == "yes"
        log.write(f"guard {'waived' if waived else 'refused'} {hit.file}:{hit.line} {hit.rule}\n")
        if not waived:
            refused.append(f"{hit.file}:{hit.line} {hit.rule}")
    log.flush()
    if refused:
        raise core.Fail("guarded " + "; ".join(refused))
