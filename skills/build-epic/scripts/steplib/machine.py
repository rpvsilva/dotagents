"""The parallelism default that follows the machine: cores and memory, when no Setting Level sets `max_parallel`."""
import os

from . import core

ENV_VAR = "BUILD_EPIC_MACHINE"


def probe():
    """(cpus, total GB) of this machine, or the `BUILD_EPIC_MACHINE=<cpus>,<gb>` override."""
    given = os.environ.get(ENV_VAR, "").strip()
    if given:
        try:
            cpus, gb = (int(x) for x in given.split(","))
        except ValueError:
            raise core.Fail(f"bad-machine: {ENV_VAR} is {given!r}, not <cpus>,<gb>")
        return cpus, gb
    cpus = os.cpu_count() or 1
    try:
        gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 1024 ** 3
    except (ValueError, OSError, AttributeError):
        gb = 0
    return cpus, gb


def parallel(cpus, gb):
    """How many subagents the machine carries at once: leave 2 cores, 4 GB each, at most 6, at least 1."""
    return max(1, min(6, cpus - 2, gb // 4))


def max_parallel_level(repo, log):
    """The Level `max_parallel` is read from (`default`, `machine`, `repo`, ...), or None when unknown."""
    tool = os.path.join(core.home(), ".agents", "bin", "settings")
    if not os.path.exists(tool):
        return None
    res = core.run([tool, "--dir", str(repo), "list", "build-epic"], log)
    if res.rc != 0:
        return None
    for line in res.out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0] == "build-epic.max_parallel":
            return parts[2].strip()
    return None


def note(repo, log):
    """` parallel=<n> (<cpus> cores, <gb> GB)` when no Level sets `max_parallel`, else an empty string."""
    if max_parallel_level(repo, log) != "default":
        return ""
    cpus, gb = probe()
    return f" parallel={parallel(cpus, gb)} ({cpus} cores, {gb} GB)"
