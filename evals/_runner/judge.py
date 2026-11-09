"""The rubric judge: a finished run scored against its case's `rubric.md` by a model, 1-5 and a one-line reason per
criterion (spec behaviours 12, 13).

`judge(run_dir, case, checkout)` builds the judge prompt from the rubric, the case prompt, the run's final answer (its
adapter's `answer()` on `run_dir/transcript.jsonl`), `git diff` of `run_dir/work/` against the fixture commit
(untracked files included) and the files under `run_dir/scratch/`, each cut to a size cap (spec open question 25).
It runs the prompt through an adapter's `command()` on the case's `judge_model`, under the checkout's
`scripts/capped`, with `CAPS`, in a throwaway home and an empty work folder, both removed after.

The rubric's criteria are its top-level list items: `- <name>: <what it asks>`, the name with any `**` or backticks
dropped; an item with no `:` is its own name. The judge must answer one JSON object, `{<criterion>: {"score": <1-5>,
"reason": "<one line>"}}`, every criterion and no other. The result is `{scores, judge_model, rubric_hash}`; an
answer that is missing, not JSON, misses a criterion or holds a score out of range gives `{error, judge_model,
rubric_hash}`, never a score. A case with no rubric gives None.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import adapters
import home

REPO = Path(__file__).resolve().parents[2]
# The judge reads and answers: one turn would do, a second spares a cap hit on a stray tool call.
CAPS = {"turns": 2, "secs": 300, "cost_usd": 0.5}
# Bytes of each section the prompt holds. With the rubric and the case prompt, the whole stays under the 128 KiB
# one argument may hold on Linux (the prompt is an argument).
ANSWER_CAP = 16_000
DIFF_CAP = 48_000
SCRATCH_CAP = 32_000
ITEM = re.compile(r"^[-*+] +(.+)$|^\d+[.)] +(.+)$")


def rubric_hash(case):
    """sha256 of the case's rubric file; None when it has none."""
    if not case.get("rubric"):
        return None
    return hashlib.sha256((Path(case["dir"]) / case["rubric"]).read_bytes()).hexdigest()


def criteria(rubric):
    """The criterion names of `rubric`'s top-level list items, in order, each once."""
    names = []
    for line in rubric.splitlines():
        m = ITEM.match(line)
        if not m:
            continue
        item = m.group(1) or m.group(2)
        name = item.split(":", 1)[0] if ":" in item else item
        name = name.replace("**", "").replace("`", "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _cut(data, cap):
    """`data` (bytes) as text, cut to `cap` bytes with a note saying how much was left out."""
    text = data[:cap].decode("utf-8", "replace")
    if len(data) > cap:
        text += f"\n[... {len(data) - cap} more bytes cut]"
    return text


def diff(work):
    """`git diff` of `work` against its root commit (the fixture), committed, uncommitted and untracked changes
    alike, through a throwaway index so the kept repo is left as it is."""
    env = {"PATH": os.environ.get("PATH") or os.defpath, "HOME": str(work), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull}
    with tempfile.TemporaryDirectory(prefix="evals-judge-index-") as tmp:
        env["GIT_INDEX_FILE"] = str(Path(tmp) / "index")
        try:
            root = home._git(work, "rev-list", "--max-parents=0", "HEAD", env=env).splitlines()[-1]
            home._git(work, "add", "-A", env=env)
            out = subprocess.run(["git", "-C", str(work), "diff", "--cached", "--no-color", "--no-ext-diff", root],
                                 env=env, capture_output=True, stdin=subprocess.DEVNULL)
        except (RuntimeError, IndexError):
            return "(no git repo in work/)"
    if out.returncode != 0:
        return "(git diff failed)"
    return _cut(out.stdout, DIFF_CAP) if out.stdout else "(no changes)"


def scratch(folder):
    """The files under `folder`, each as a `### <path>` heading and its text, till `SCRATCH_CAP` bytes are used;
    the files past it are named only."""
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()) if folder.is_dir() else []
    if not files:
        return "(empty)"
    parts, left = [], SCRATCH_CAP
    for path in files:
        data = path.read_bytes()
        rel = path.relative_to(folder).as_posix()
        if left <= 0:
            parts.append(f"### {rel}\n[{len(data)} bytes left out: over the size cap]")
            continue
        parts.append(f"### {rel}\n{_cut(data, left)}")
        left -= len(data)
    return "\n\n".join(parts)


def prompt(rubric, names, case_prompt, answer, work_diff, scratch_files):
    shape = ", ".join(f'"{n}": {{"score": <1-5>, "reason": "<one line>"}}' for n in names)
    return f"""You are grading one run of an AI coding agent against a rubric. Score each criterion from 1 (fails it) \
to 5 (fully meets it), with a one-line reason. Judge only from what is shown below.

Answer with one JSON object and nothing else, one key per criterion, exactly these keys:
{{{shape}}}

## Rubric

{rubric.strip()}

## The task the agent was given

{case_prompt.strip()}

## The agent's final answer

{answer}

## What the agent changed in the repo (git diff against the starting commit)

{work_diff}

## Files the agent left in its scratch folder

{scratch_files}
"""


def _strip_fence(text):
    m = re.fullmatch(r"```(?:json)?\s*\n(.*)\n\s*```", text.strip(), re.S)
    return m.group(1) if m else text.strip()


def scores(text, names):
    """`{criterion: {score, reason}}` from the judge's answer; raises ValueError saying what is wrong with it."""
    try:
        data = json.loads(_strip_fence(text))
    except ValueError:
        raise ValueError("the judge's answer is not JSON") from None
    if not isinstance(data, dict):
        raise ValueError("the judge's answer is not one JSON object")
    missing = [n for n in names if n not in data]
    if missing:
        raise ValueError(f"the judge's answer misses {', '.join(missing)}")
    extra = [k for k in data if k not in names]
    if extra:
        raise ValueError(f"the judge's answer names no criterion {', '.join(extra)}")
    out = {}
    for name in names:
        entry = data[name]
        score = entry.get("score") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise ValueError(f"{name}: score {score!r} is not an integer from 1 to 5")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{name}: no reason")
        out[name] = {"score": score, "reason": " ".join(reason.split())}
    return out


def _capped(checkout):
    own = Path(checkout) / "scripts" / "capped"
    return own if own.is_file() else REPO / "scripts" / "capped"


def _ask(adapter, text, model, checkout):
    """The judge's final answer, or None; from a run in a throwaway home, removed after."""
    root = Path(tempfile.mkdtemp(prefix="evals-judge-")).resolve()
    try:
        judge_home, work = root / "home", root / "work"
        judge_home.mkdir()
        work.mkdir()
        adapter.prepare_home(judge_home)
        argv = adapter.command(text, model, CAPS, work)
        with open(root / adapters.TRANSCRIPT, "wb") as out:
            subprocess.run([sys.executable, "-I", str(_capped(checkout)), "--secs", str(CAPS["secs"]), "--", *argv],
                           cwd=str(work), env=home.tool_env(judge_home, adapter.AUTH_VARS), stdin=subprocess.DEVNULL,
                           stdout=out, stderr=subprocess.DEVNULL)
        return adapter.answer(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def judge(run_dir, case, checkout, tool="claude"):
    """The rubric result of the run kept in `run_dir` of `case` (as `cases.load()` gives it), judged through the
    `tool` adapter with dotagents' `checkout`; None when the case has no rubric."""
    if not case.get("rubric"):
        return None
    run_dir = Path(run_dir)
    model = case["judge_model"]
    base = {"judge_model": model, "rubric_hash": rubric_hash(case)}
    rubric = (Path(case["dir"]) / case["rubric"]).read_text()
    names = criteria(rubric)
    if not names:
        return {"error": "the rubric holds no criteria (top-level list items)", **base}
    try:
        run_tool = json.loads((run_dir / "result.json").read_text()).get("tool")
        answer = adapters.load(run_tool).answer(run_dir)
    except (OSError, ValueError):
        answer = None
    text = prompt(rubric, names, (Path(case["dir"]) / case["prompt"]).read_text(),
                  _cut(answer.encode(), ANSWER_CAP) if answer else "(none)", diff(run_dir / "work"),
                  scratch(run_dir / "scratch"))
    try:
        said = _ask(adapters.load(tool), text, model, checkout)
    except ValueError as err:
        return {"error": f"the judge could not start: {err}", **base}
    if not said:
        return {"error": "the judge gave no answer", **base}
    try:
        return {"scores": scores(said, names), **base}
    except ValueError as err:
        return {"error": str(err), **base}
