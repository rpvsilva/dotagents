"""The Cursor adapter: `cursor-agent -p` headless, and the parse of its stream-json transcript.

Flags and event shapes are from Cursor's docs and `cursor-agent --help`, not from a run (`notes/other-tools.md`
of the epic); the first paid run may change them. Cursor reports no token usage, so `tokens` is always `None` and
the run's cost `unknown`, never 0. It has no turn or cost cap either: only the runner's time cap holds it. Turns are
the `assistant` events (one per complete message), duration the `result` event's; a stream with no `result` event
(the tool failed or was killed) is a tool error. The key reaches it only as `CURSOR_API_KEY` in env, never
`--api-key`, so it stays out of argv. Whether headless `-p` loads the skills in `~/.agents/skills` is unverified.
"""
import json
from pathlib import Path

from . import TRANSCRIPT

INSTALL_WORD = "cursor"
AUTH_VARS = ("CURSOR_API_KEY",)
USES_CASE_MODEL = False
RENEW = "a new CURSOR_API_KEY"

# A started `tool_call` naming one of these starts an agent: a typed `<name>ToolCall` key, or a generic `function`.
SPAWN_KEYS = ("taskToolCall", "agentToolCall")
SPAWN_NAMES = ("Task", "Agent")


def prepare_home(home):
    # install.sh skips Cursor without ~/.cursor.
    (Path(home) / ".cursor").mkdir(parents=True, exist_ok=True)


def command(prompt, model, caps, work):
    # capped closes stdin, so the prompt is an argument, where a leading `-` would be read as an option.
    if prompt.lstrip().startswith("-"):
        raise ValueError("a prompt starting with '-' would be read as an option by cursor-agent")
    return ["cursor-agent", "-p", prompt, "--output-format", "stream-json", "--model", model, "--force", "--trust",
            "--workspace", str(work)]


def events(path):
    """The transcript's JSON objects; a line that is not one (cut by a kill) is skipped."""
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


def spawns(event):
    if event.get("type") != "tool_call" or event.get("subtype") != "started":
        return False
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    function = call.get("function")
    return any(k in call for k in SPAWN_KEYS) or (isinstance(function, dict) and function.get("name") in SPAWN_NAMES)


def text(event):
    """An `assistant` event's text: its message's `text` parts joined; None when it has none."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    parts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"
             and isinstance(p.get("text"), str)] if isinstance(content, list) else []
    return "".join(parts) if parts else None


def answer(run_dir):
    """The run's final answer, for the judge: the text of the last `assistant` message holding any; None without one.
    Not the `result` event's `result`, which a failed or cut run lacks."""
    return next((t for t in (text(e) for e in reversed(events(Path(run_dir) / TRANSCRIPT))
                             if e.get("type") == "assistant") if t is not None), None)


def parse(run_dir, home, work):
    stream = events(Path(run_dir) / TRANSCRIPT)
    result = next((e for e in reversed(stream) if e.get("type") == "result"), None)
    duration = result.get("duration_ms") if result else None
    return {
        "tokens": None,
        "turns": sum(1 for e in stream if e.get("type") == "assistant") if stream else None,
        "duration_s": duration / 1000 if isinstance(duration, (int, float)) else None,
        "capped": None,
        "tool_version": None,
        "spawned_agents": sum(1 for e in stream if spawns(e)),
        "error": None if result and not result.get("is_error") else "tool",
    }
