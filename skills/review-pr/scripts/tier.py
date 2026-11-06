#!/usr/bin/env python3
"""Prints a change's risk tier, `low` or `high`, then the signals that decided it, one per line.

Usage: tier.py <diff file>
       tier.py --git <worktree> <merge-base>   (tiers `git diff <merge-base>..HEAD` in the worktree)
       tier.py --trim <diff file>              (prints the diff cut to its changed files and added lines, a fixture)

Only added lines count, and only in source files: test and doc files never raise the tier. A change is high when an
added non-comment source line reaches an I/O sink, a write or a file read (`sink: save( in <file>`), when it touches
a storage-format file (`storage: <file>`), or when it adds more source lines than the `tier_max_lines` Setting
(`size: ...`), read through `~/.agents/bin/settings` (600 when the tool is missing). Otherwise it is low. Read-only: it never writes a file.
"""
import os
import re
import subprocess
import sys

DEFAULT_MAX_LINES = 600

# Each sink as (the name a signal shows, its pattern on an added line).
SINKS = [
    ("open(", r"\bopen\("),
    ("write_text", r"\bwrite_(text|bytes)\b"),
    # A file read through a helper rather than open(): input from outside, such as a file to import.
    ("file read", r"\bread_(text|bytes|json)\("),
    ("os.replace", r"\bos\.replace\b"),
    ("os.rename", r"\bos\.rename\b"),
    ("shutil", r"\bshutil\b"),
    ("subprocess", r"\bsubprocess\b"),
    ("socket", r"\bsocket\b"),
    ("urllib", r"\burllib\b"),
    ("os.environ", r"\bos\.environ\b"),
    ("json.dump", r"\bjson\.dump\("),
    ("unlink", r"\bunlink\b"),
    ("mkdir", r"\bmkdir"),
    (".write(", r"\.write\("),
    ("save(", r"save\("),
]
SINKS = [(name, re.compile(pattern)) for name, pattern in SINKS]

COMMENT = re.compile(r"\s*(#|//|/\*|\*|--|<!--)")
DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
DOC_DIRS = {"docs", "doc"}
DOC_NAMES = re.compile(r"^(readme|changelog|license|contributing|authors|notice)\b", re.I)
TEST_DIRS = {"test", "tests", "__tests__", "spec", "testdata"}
TEST_NAMES = re.compile(r"^(test_.*|.*_test\.\w+|.*\.(test|spec)\.\w+|conftest\.py)$")
STORAGE_EXT = {".sql", ".proto", ".avsc", ".fbs", ".prisma"}
STORAGE_DIRS = {"migrations", "migration"}


def kind(path):
    """`test`, `doc`, `storage` or `source`."""
    parts = path.split("/")
    name = parts[-1]
    ext = os.path.splitext(name)[1].lower()
    if TEST_DIRS & set(parts[:-1]) or TEST_NAMES.match(name):
        return "test"
    if ext in DOC_EXT or DOC_DIRS & set(parts[:-1]) or DOC_NAMES.match(name):
        return "doc"
    if ext in STORAGE_EXT or STORAGE_DIRS & set(parts[:-1]) or "schema" in name.lower():
        return "storage"
    return "source"


def parse(text):
    """The diff's files in order, each as (path, [added lines]). A deleted file keeps its old path."""
    files = []
    header = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            files.append([m.group(2) if m else line.split()[-1], []])
            header = True
        elif header and line.startswith("+++ "):
            target = line[4:].split("\t")[0]
            if target != "/dev/null":
                files[-1][0] = target[2:] if target.startswith("b/") else target
            header = False
        elif not header and line.startswith("+") and files:
            files[-1][1].append(line[1:])
    return [(path, added) for path, added in files]


def trim(text):
    """The diff cut to what tiering reads: each file's header and its added lines."""
    out = []
    for path, added in parse(text):
        out += [f"diff --git a/{path} b/{path}", f"+++ b/{path}"]
        out += ["+" + line for line in added]
    return "\n".join(out) + "\n"


def max_lines(folder):
    """The `tier_max_lines` Setting, or the default when `~/.agents/bin/settings` is missing."""
    tool = os.path.expanduser("~/.agents/bin/settings")
    if not os.path.exists(tool):
        return DEFAULT_MAX_LINES
    res = subprocess.run([tool, "--dir", folder, "get", "review-pr", "tier_max_lines"], capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        sys.exit(f"tier.py: settings failed: {res.stderr.strip()}")
    value = res.stdout.strip()
    return int(value) if value else DEFAULT_MAX_LINES


def tier(files, limit):
    """(`low` or `high`, the signals that decided it)."""
    signals = []
    size = 0
    for path, added in files:
        k = kind(path)
        if k == "storage":
            signals.append(f"storage: {path}")
        if k != "source":
            continue
        size += len(added)
        hit = []
        for line in added:
            if COMMENT.match(line):
                continue
            hit += [name for name, pattern in SINKS if name not in hit and pattern.search(line)]
        signals += [f"sink: {name} in {path}" for name in hit]
    if size > limit:
        signals.append(f"size: {size} added source lines > {limit}")
    if signals:
        return "high", signals
    return "low", [f"no sink, no storage file, {size} added source lines <= {limit}"]


def main(argv):
    if len(argv) == 2 and argv[0] == "--trim":
        with open(argv[1], errors="replace") as f:
            sys.stdout.write(trim(f.read()))
        return 0
    if len(argv) == 3 and argv[0] == "--git":
        worktree, base = argv[1], argv[2]
        res = subprocess.run(["git", "-C", worktree, "diff", "--no-color", "--no-ext-diff", f"{base}..HEAD"],
                             capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            sys.exit(f"tier.py: git diff failed: {res.stderr.strip()}")
        text, folder = res.stdout, worktree
    elif len(argv) == 1 and not argv[0].startswith("--"):
        with open(argv[0], errors="replace") as f:
            text = f.read()
        folder = os.getcwd()
    else:
        sys.exit(__doc__.split("\n\n")[1])
    verdict, signals = tier(parse(text), max_lines(folder))
    print(verdict)
    for signal in signals:
        print(signal)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
