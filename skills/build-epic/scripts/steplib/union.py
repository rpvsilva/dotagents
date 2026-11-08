"""Resolves a merge whose conflicts are all in hub files by keeping both sides, when the run's tests stay green.

Hub files are the shared append points `map.md`'s `## Lanes` section names on its `Hub files:` line, each path in a
code span (`` `SKILL.md` ``; one ending in `/` names a folder). With a merge stopped on conflicts in a worktree,
`resolve` acts only when every conflicted file is a hub file both sides changed (no delete or rename on either), a
regular file on every side (a symlink or submodule is refused, never written through) and `decisions.md` records a
`test_command:`. It writes each file as `git merge-file --union` makes it, as bytes (CRLF and non-UTF-8 kept), every
hunk's two sides kept (ours first), and runs `test_command:` under `bash -c` in the worktree: green, it puts back any
tracked file the command rewrote, stages the files and commits the merge (its message as git prepared it) and returns
them; red, or a commit that fails (a hook), it puts the conflict back as git left it, unmerged entries, markers and
all, and returns None, so the caller goes on as it did without it. No `Hub files:` line, a conflict
outside hub files or no `test_command:` returns None and touches nothing.
"""
import os
import re
import subprocess
import tempfile

from . import core, decisions

LANES = re.compile(r"^## Lanes[ \t]*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
HUB_LINE = re.compile(r"^Hub files:(.*)$", re.MULTILINE)
REGULAR = ("100644", "100755")


def hub_files(run):
    """The paths on `map.md`'s `## Lanes` `Hub files:` line, in order; empty when the run has no such line."""
    path = core.resolve(run) / "map.md"
    if not path.is_file():
        return []
    lanes = LANES.search(path.read_text(encoding="utf-8"))
    line = HUB_LINE.search(lanes.group(1)) if lanes else None
    return [p.strip() for p in re.findall(r"`([^`\n]+)`", line.group(1)) if p.strip()] if line else []


def is_hub(path, hubs):
    return any(path == h or (h.endswith("/") and path.startswith(h)) for h in hubs)


def _git(wt, log, *args):
    return core.run(["git", "-C", wt, *args], log)


def conflicted(wt, log):
    """The worktree's unmerged paths, each once, sorted."""
    return sorted({p for p in _git(wt, log, "diff", "--name-only", "--diff-filter=U").out.splitlines() if p})


def stages(wt, log, path):
    """{stage: (mode, sha)} of `path`'s unmerged index entries."""
    found = {}
    for line in _git(wt, log, "ls-files", "-u", "-z", "--", path).out.split("\0"):
        if line:
            meta, name = line.split("\t", 1)
            mode, sha, stage = meta.split()
            if name == path:
                found[stage] = (mode, sha)
    return found


def _blob(wt, log, sha):
    """The blob's bytes as git stores them: no newline translation, no decoding."""
    argv = ["git", "-C", str(wt), "cat-file", "blob", sha]
    log.write(f"$ {' '.join(argv)}\n")
    p = subprocess.run(core.command(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log.write(p.stderr.decode("utf-8", "replace") + f"[rc {p.returncode}]\n")
    if p.returncode != 0:
        raise core.Fail("git-cat-file")
    return p.stdout


def _union(wt, log, path, found, scratch):
    """Both sides of every hunk of `path`, as bytes; None when a side is missing (a delete), is not a regular file
    (a symlink or a submodule, whose union would be written through the link) or git cannot merge it."""
    if "2" not in found or "3" not in found:
        log.write(f"union: {path} has no two sides to keep\n")
        return None
    if any(mode not in REGULAR for mode, _ in found.values()) or os.path.islink(core.resolve(wt) / path):
        log.write(f"union: {path} is not a regular file on every side\n")
        return None
    files = []
    for stage in ("2", "1", "3"):  # ours, base (empty for an add on both sides), theirs: merge-file's order
        target = scratch / stage
        target.write_bytes(_blob(wt, log, found[stage][1]) if stage in found else b"")
        files.append(target)
    if core.run(["git", "merge-file", "--union", *files], log).rc != 0:
        return None
    return files[0].read_bytes()


def resolve(run, wt, log):
    """Settles the merge stopped in `wt` by union when every conflict is in a hub file and `test_command:` passes on
    it, committing the merge; returns the files, or None with the conflict as git left it."""
    hubs, files = hub_files(run), conflicted(wt, log)
    command = decisions.get(run, "test_command")
    if not hubs or not files or not command or not all(is_hub(f, hubs) for f in files):
        return None
    log.write(f"union: {' '.join(files)}\n")
    entries = {f: stages(wt, log, f) for f in files}
    merged = {}
    with tempfile.TemporaryDirectory(prefix="union-") as tmp:
        for i, f in enumerate(files):
            scratch = core.resolve(tmp) / str(i)
            os.mkdir(scratch)
            merged[f] = _union(wt, log, f, entries[f], scratch)
            if merged[f] is None:
                return _restore(wt, log, files, f"union: {f} not kept both ways")
    root = core.resolve(wt)
    for f, data in merged.items():
        (root / f).write_bytes(data)
    if core.run(["bash", "-c", command], log, cwd=wt).rc != 0:
        return _restore(wt, log, files, "union: test_command red")
    # What the command regenerated is not part of the merge: tracked files go back to the merge's index, and the
    # hub files to their union, so the commit leaves a clean tree.
    touched = [p for p in _git(wt, log, "diff", "--name-only").out.splitlines() if p and p not in merged]
    if touched:
        log.write(f"union: test_command changed {' '.join(touched)}; restored\n")
        _git(wt, log, "checkout", "--", *touched)
    for f, data in merged.items():
        (root / f).write_bytes(data)
    _git(wt, log, "add", "--", *files)
    if _git(wt, log, "commit", "--no-edit").rc != 0:
        _unstage(wt, log, entries)
        return _restore(wt, log, files, "union: commit failed")
    return files


def _unstage(wt, log, entries):
    """Puts back each file's unmerged index entries, as the merge left them, in place of what `git add` staged."""
    lines = []
    for path, found in entries.items():
        lines.append(f"0 {'0' * 40}\t{path}")
        lines += [f"{mode} {sha} {stage}\t{path}" for stage, (mode, sha) in sorted(found.items())]
    core.run(["git", "-C", wt, "update-index", "--index-info"], log, input="\n".join(lines) + "\n")


def _restore(wt, log, files, why):
    log.write(f"{why}; conflict put back\n")
    _git(wt, log, "checkout", "-m", "--", *files)
    return None
