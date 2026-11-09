"""The parallelism default that follows the machine: cores and memory, when no Setting Level sets `max_parallel`; and
each test command's memory cap, the machine's share per slot, when no Level sets `cap_mb`."""
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


# Kept out of every slot's share: the OS and the orchestrator, then each agent's own Claude.
RESERVE_MB = 2048
AGENT_MB = 1024
MIN_CAP_MB = 512


def cap_mb(gb, slots):
    """Each test command's memory share of a machine of `gb` GB running `slots` agents at once: the memory less
    RESERVE_MB, over the slots, less AGENT_MB; at least MIN_CAP_MB."""
    return max(MIN_CAP_MB, (gb * 1024 - RESERVE_MB) // max(1, slots) - AGENT_MB)


def setting(repo, key, log):
    """build-epic Setting `key`'s value as `~/.agents/bin/settings get` prints it, or None (unset, or no tool)."""
    tool = os.path.join(core.home(), ".agents", "bin", "settings")
    if not os.path.exists(tool):
        return None
    res = core.run([tool, "--dir", str(repo), "get", "build-epic", key], log)
    if res.rc != 0:
        raise core.Fail(f"settings: build-epic.{key}")
    return res.out.strip() or None


def caps(repo, log):
    """(mb, secs): the caps of each test command run on this machine. `cap_mb` when a Level sets it, else the
    machine's share (`cap_mb()`) over `max_parallel` as a Level sets it, else over the machine's default
    (`parallel()`); `cap_secs` (300 by default)."""
    secs = int(setting(repo, "cap_secs", log) or 300)
    given = setting(repo, "cap_mb", log)
    if given:
        return int(given), secs
    cpus, gb = probe()
    level = max_parallel_level(repo, log)
    slots = parallel(cpus, gb) if level in (None, "default") else int(setting(repo, "max_parallel", log))
    return cap_mb(gb, slots), secs


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
