"""The Claude Code adapter: `claude -p` headless, and the parse of its stream-json transcript and session log.

The command is the invocation of `scripts/remote-agent-sandbox.sh`, plus the case's caps. Never `--bare`: it reads
no OAuth token. `--max-turns` is hidden in `claude --help` (2.1.319), so `test_claude.py` pins it.

Tokens come from the session log (`$CLAUDE_CONFIG_DIR/projects/*/<session id>.jsonl`, the init event's
`session_id`) through `adapters/claude/telemetry/extract.py`, run by path from this checkout, so subagents count
too; without a session log, from the stream's `result` event. A log the extractor cannot measure (exit 2: nothing in
it; a turn with no model) gives `tokens: None`, never a crash. The cap subtypes of the `result` event
(`error_max_turns`, `error_max_budget_usd`) are unverified until the first real run.
"""
import json
import subprocess
import sys
from pathlib import Path

from . import TRANSCRIPT

INSTALL_WORD = "claude"
AUTH_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
USES_CASE_MODEL = True
RENEW = "claude setup-token, then export CLAUDE_CODE_OAUTH_TOKEN, or set a new ANTHROPIC_API_KEY"

EXTRACT = Path(__file__).resolve().parents[3] / "adapters" / "claude" / "telemetry" / "extract.py"
CAPS = {"error_max_turns": "turns", "error_max_budget_usd": "cost"}
SPAWNS = ("Task", "Agent")
KINDS = ("input", "output", "cacheRead", "cacheWrite5m", "cacheWrite1h")


def prepare_home(home):
    (Path(home) / ".claude").mkdir(parents=True, exist_ok=True)


def command(prompt, model, caps, work):
    # capped closes stdin, so the prompt is an argument, where a leading `-` would be read as an option.
    if prompt.lstrip().startswith("-"):
        raise ValueError("a prompt starting with '-' would be read as an option by claude")
    return ["claude", "-p", prompt, "--model", model, "--max-turns", str(caps["turns"]),
            "--max-budget-usd", str(caps["cost_usd"]), "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions"]


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


def session_log(home, session_id):
    if not isinstance(session_id, str) or not session_id or "/" in session_id:
        return None
    return next((Path(home) / ".claude" / "projects").glob(f"*/{session_id}.jsonl"), None)


def session_tokens(log, work):
    """tokensByModel summed over the extractor's spans of the whole session; None when it cannot measure it."""
    proc = subprocess.run([sys.executable, "-I", str(EXTRACT), "--session", str(log), "--whole", "--cwd", str(work)],
                          capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        return None
    tokens = {}
    for span in json.loads(proc.stdout)["spans"]:
        for model, counts in span["tokensByModel"].items():
            total = tokens.setdefault(model, dict.fromkeys(KINDS, 0))
            for kind in KINDS:
                total[kind] += counts[kind]
    return tokens


def result_tokens(result):
    """tokensByModel from the result event's `modelUsage`; it does not split cache writes by lifetime, so they count
    as 5-minute writes (Claude Code's default)."""
    usage = result.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None
    return {model: {"input": u.get("inputTokens", 0), "output": u.get("outputTokens", 0),
                    "cacheRead": u.get("cacheReadInputTokens", 0),
                    "cacheWrite5m": u.get("cacheCreationInputTokens", 0), "cacheWrite1h": 0}
            for model, u in usage.items() if isinstance(u, dict)} or None


def answer(run_dir):
    """The last `result` event's text: the final answer `claude -p` printed."""
    result = next((e for e in reversed(events(Path(run_dir) / TRANSCRIPT)) if e.get("type") == "result"), None)
    text = (result or {}).get("result")
    return text if isinstance(text, str) else None


def auth_failed(stream):
    """True when an `api_retry` event reports a 401 or `authentication_failed`; event fields only, never text."""
    return any(e.get("type") == "system" and e.get("subtype") == "api_retry"
               and (e.get("error_status") == 401 or e.get("error") == "authentication_failed") for e in stream)


def zero_cost(result):
    """True when the result event reports a cost of 0 and every usage count 0; None without a result event."""
    if result is None:
        return None
    usage = result.get("usage")
    cost = result.get("total_cost_usd")
    if not isinstance(usage, dict) or not usage or isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return False
    return cost == 0 and all(isinstance(n, int) and not isinstance(n, bool) and n == 0
                             for n in usage.values() if not isinstance(n, (dict, list, str, type(None))))


def parse(run_dir, home, work):
    stream = events(Path(run_dir) / TRANSCRIPT)
    init = next((e for e in stream if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(stream) if e.get("type") == "result"), None)
    spawned = sum(1 for e in stream if e.get("type") == "assistant"
                  for block in (e.get("message") or {}).get("content") or []
                  if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in SPAWNS)
    log = session_log(home, init.get("session_id") or (result or {}).get("session_id"))
    if log:
        tokens = session_tokens(log, work)
    else:
        tokens = result_tokens(result) if result else None
    capped = CAPS.get(result.get("subtype")) if result else None
    duration = result.get("duration_ms") if result else None
    return {
        "tokens": tokens,
        "turns": result.get("num_turns") if result else None,
        "duration_s": duration / 1000 if isinstance(duration, (int, float)) else None,
        "capped": capped,
        "tool_version": init.get("claude_code_version"),
        "spawned_agents": spawned,
        "error": "auth" if auth_failed(stream) else
                 None if result and (capped or not result.get("is_error")) else "tool",
        "zero_cost": zero_cost(result),
    }
