"""The Codex adapter: `codex exec --json` headless, and the parse of its JSONL events and session log.

Auth is `CODEX_API_KEY` alone, passed to the codex process only (never a saved login: the home is throwaway). Codex
has no turn or cost cap flag, so caps other than time go unused; turns are counted after the fact.

Tokens are `turn.completed.usage` summed over the run: `input_tokens` less `cached_input_tokens` as `input`,
`cached_input_tokens` as `cacheRead`, `output_tokens` plus `reasoning_output_tokens` as `output`. The events name no
model or version, so both come from the session log Codex writes for the thread
(`~/.codex/sessions/**/rollout-*-<thread id>.jsonl`: `turn_context.payload.model`, `session_meta.payload.cli_version`);
without it, the tokens are kept under `unknown`, which no price matches, so cost is unknown. A subagent is a
`collab_tool_call` item spawning an agent, counted once per item id. The event shape is from Codex's docs, the flag
names, session log and subagent items unverified until the first real run.
"""
import json
from pathlib import Path

from . import TRANSCRIPT

INSTALL_WORD = "codex"
AUTH_VARS = ("CODEX_API_KEY",)
USES_CASE_MODEL = False

KINDS = ("input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h")
TURN_END = ("turn.completed", "turn.failed", "error")


def prepare_home(home):
    # install.sh codex skips without ~/.codex (install.sh:170-174).
    (Path(home) / ".codex").mkdir(parents=True, exist_ok=True)


def command(prompt, model, caps, work):
    # capped closes stdin, so the prompt is an argument, where a leading `-` would be read as an option.
    if prompt.lstrip().startswith("-"):
        raise ValueError("a prompt starting with '-' would be read as an option by codex")
    return ["codex", "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "-m", model, prompt]


def events(path):
    """The file's JSON objects; a line that is not one (cut by a kill) is skipped."""
    found = []
    try:
        with open(path, "rb") as f:
            for line in f:
                try:
                    event = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(event, dict):
                    found.append(event)
    except OSError:
        pass
    return found


def session_log(home, thread_id):
    """This thread's session log under the home's `~/.codex/sessions`, or None."""
    if not isinstance(thread_id, str) or not thread_id or any(c in thread_id for c in "/\\*?["):
        return None
    return next((Path(home) / ".codex" / "sessions").glob(f"**/rollout-*-{thread_id}.jsonl"), None)


def payload_field(log, kind, field):
    """`field` of the last `kind` entry's payload in the session log that has it."""
    return next((e["payload"][field] for e in reversed(log) if e.get("type") == kind
                 and isinstance(e.get("payload"), dict) and isinstance(e["payload"].get(field), str)), None)


def tokens(stream, model):
    """tokensByModel summed over every `turn.completed`'s usage; None when no turn reported any."""
    usages = [e["usage"] for e in stream if e.get("type") == "turn.completed" and isinstance(e.get("usage"), dict)]
    if not usages:
        return None

    def count(usage, key):
        n = usage.get(key)
        return n if isinstance(n, int) and not isinstance(n, bool) else 0

    total = dict.fromkeys(KINDS, 0)
    for u in usages:
        cached = count(u, "cached_input_tokens")
        total["input"] += max(count(u, "input_tokens") - cached, 0)
        total["cacheRead"] += cached
        total["output"] += count(u, "output_tokens") + count(u, "reasoning_output_tokens")
    return {model or "unknown": total}


def spawned(stream):
    ids = {(e.get("item") or {}).get("id") for e in stream if str(e.get("type", "")).startswith("item.")
           and isinstance(e.get("item"), dict) and e["item"].get("type") == "collab_tool_call"
           and e["item"].get("tool", "spawn_agent") == "spawn_agent"}
    return len(ids)


def parse(run_dir, home, work):
    stream = events(Path(run_dir) / TRANSCRIPT)
    thread = next((e.get("thread_id") for e in stream if e.get("type") == "thread.started"), None)
    path = session_log(home, thread)
    log = events(path) if path else []
    turns = sum(1 for e in stream if e.get("type") == "turn.completed")
    last = next((e.get("type") for e in reversed(stream) if e.get("type") in TURN_END), None)
    return {
        "tokens": tokens(stream, payload_field(log, "turn_context", "model")),
        "turns": turns or None,
        "duration_s": None,
        "capped": None,
        "tool_version": payload_field(log, "session_meta", "cli_version"),
        "spawned_agents": spawned(stream),
        "error": None if last == "turn.completed" else "tool",
    }
