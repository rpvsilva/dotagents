"""Saved runs: the `result.json` every eval run writes, the store holding them and the reuse lookup.

Store root: `--results`, else `EVALS_RESULTS`, else `~/.agents/evals/results`; never inside the repo. One run's folder:
`<root>/<tool>/<ref sha>/<model>/<case>/<case hash>/run-<n>/`, so a saved run is reused only when the tool, the
ref's commit, the model and every file of the case are unchanged (spec open question 20). A run is finished once its
`result.json` exists: `write()` puts it in place last, in one rename.

Unknown values are `null`, never 0: a field the caller leaves out is saved as `null`.
"""
import hashlib
import json
import os
import re
from pathlib import Path

SCHEMA = 1
RESULT = "result.json"
FIELDS = ("schema", "tool", "tool_version", "model", "ref", "ref_sha", "case", "case_hash", "run", "checks",
          "checks_hash", "passed", "tokens", "cost_usd", "cost_basis", "pricing_as_of", "turns", "duration_s", "capped",
          "subagents", "error", "rubric", "transcript")


def write(result_dir, result):
    """Save `result` as `<result_dir>/result.json`, every field present, `null` where unknown."""
    unknown = sorted(set(result) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown result field(s): {', '.join(unknown)}")
    full = {field: result.get(field) for field in FIELDS}
    if full["schema"] is None:
        full["schema"] = SCHEMA
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    tmp = result_dir / (RESULT + ".tmp")
    tmp.write_text(json.dumps(full, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, result_dir / RESULT)


def read(result_dir):
    """The saved result, with any field missing from the file as `null`."""
    saved = json.loads((Path(result_dir) / RESULT).read_text())
    return {field: saved.get(field) for field in FIELDS}


def case_hash(case_dir):
    """sha256 over the case folder's files: each relative path and the sha256 of its contents, in path order."""
    case_dir = Path(case_dir)
    files = sorted(p.relative_to(case_dir).as_posix() for p in case_dir.rglob("*") if p.is_file())
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256((case_dir / rel).read_bytes()).digest())
    return h.hexdigest()


def store_root(results=None):
    """The `--results` value, else `EVALS_RESULTS`, else `~/.agents/evals/results`."""
    root = results or os.environ.get("EVALS_RESULTS") or Path.home() / ".agents" / "evals" / "results"
    return Path(root).expanduser()


def _parts(value):
    parts = str(value).split("/")
    if not value or value.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"not a store key: {value!r}")
    return parts


def key_dir(root, tool, sha, model, case, case_hash):
    """The folder holding every run of one key; `case` may hold a `/` (`<skill>/<case>`)."""
    for value in (tool, sha, model, case_hash):
        if len(_parts(value)) != 1:
            raise ValueError(f"not a store key: {value!r}")
    return Path(root).joinpath(tool, sha, model, *_parts(case), case_hash)


def run_dir(root, tool, sha, model, case, case_hash, n):
    """The folder of run `n` (from 1) of one key."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"not a run number: {n!r}")
    return key_dir(root, tool, sha, model, case, case_hash) / f"run-{n}"


def saved_runs(root, tool, sha, model, case, case_hash):
    """The folders of the key's finished runs (those holding `result.json`), in run order."""
    d = key_dir(root, tool, sha, model, case, case_hash)
    if not d.is_dir():
        return []
    runs = [(int(m.group(1)), p) for p in d.iterdir() if (m := re.fullmatch(r"run-([1-9][0-9]*)", p.name))]
    return [p for _, p in sorted(runs) if (p / RESULT).is_file()]
