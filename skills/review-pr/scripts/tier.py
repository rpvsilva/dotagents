#!/usr/bin/env python3
"""Prints a change's risk tier, `low` or `high`, then the signals that decided it, one per line.

Usage: tier.py <diff file>
       tier.py --git <worktree> <merge-base>   (tiers `git diff <merge-base>..HEAD` in the worktree)
       tier.py --trim <diff file>              (prints the diff cut to its changed files and added lines, a fixture)

Only added lines count. Test files (`test_*`, `*.test.*`, ... and code files under `tests/`; test-harness scripts
and config there, such as `tests/run.sh` or `tests/suites.txt`, are not tests), fixtures and docs never raise the
tier. The default is safe, so a change is high when it:
- touches a file that runs or steers code by its kind: a test harness file (`harness: <file>`), a shell script (`shell: <file>`), a CI, workflow or build file
  (`ci: <file>`), an agent-instruction file such as `AGENTS.md`, `SKILL.md` or a brief (`agent-instructions: <file>`),
  the review gate, `CODEOWNERS` or a path either marks high (`gate: <file>`), or the repo's `.agents/settings.json`
  (`settings: <file>`);
- touches a storage-format file (`storage: <file>`);
- adds a non-comment source line reaching a sink: an I/O write or read, a process, the network, code from data
  (`sink: eval in <file>`);
- or adds more source lines than the `tier_max_lines` Setting (`size: ...`), read through `~/.agents/bin/settings`
  (600 when the tool is missing).
Otherwise it is low. The gate files and the Setting's repo Level come from the merge base in `--git` mode, so a PR
cannot lower its own tier; from the current folder otherwise. Read-only: it never writes to the repo.
"""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile

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
    ("os.system", r"\bos\.system\b"),
    ("os.popen", r"\bos\.popen\b"),
    ("os.exec", r"\bos\.(exec|spawn)\w*\("),
    ("socket", r"\bsocket\b"),
    ("urllib", r"\burllib\b"),
    ("requests", r"\b(requests|httpx|aiohttp)\.\w+\("),
    ("http", r"\bhttp\.(client|request|get)\b"),
    ("os.environ", r"\bos\.environ\b"),
    ("process.env", r"\bprocess\.env\b"),
    ("json.dump", r"\bjson\.dump\("),
    ("pickle.loads", r"\b(c?pickle|marshal|dill)\.loads?\("),
    ("yaml.load", r"\byaml\.(unsafe_)?load\("),
    ("unlink", r"\bunlink\b"),
    ("mkdir", r"\bmkdir"),
    (".write(", r"\.write\("),
    ("save(", r"save\("),
    ("eval", r"(^|[^\w.])eval\b"),
    ("exec(", r"(^|[^\w.])exec\("),
    ("child_process", r"\bchild_process\b"),
    ("fetch(", r"\bfetch\("),
    ("fs write", r"\bfs\.(write|append|rm|unlink|rename|copy|mkdir)\w*\("),
    ("new Function", r"\bnew\s+Function\("),
    ("curl", r"\b(curl|wget)\b"),
    ("| sh", r"\|\s*(sudo\s+)?(ba|z|da)?sh\b"),
    ("sh -c", r"\b(ba|z)?sh\s+-c\b"),
    ("rm -r", r"\brm\s+-\w*[rf]"),
    ("source", r"(^|[;&|]\s*)(source|\.)\s+\S"),
]
SINKS = [(name, re.compile(pattern)) for name, pattern in SINKS]

# The comment markers per file extension; any other extension takes DEFAULT_COMMENT.
HASH = r"#"
C_LIKE = r"//|/\*|\*(\s|/|$)"
COMMENTS = {
    **dict.fromkeys(("py", "sh", "bash", "zsh", "rb", "pl", "yml", "yaml", "toml", "cfg", "ini", "r", "ps1"), HASH),
    **dict.fromkeys(("js", "jsx", "ts", "tsx", "mjs", "cjs", "java", "kt", "go", "rs", "c", "h", "cc", "cpp", "hpp",
                     "cs", "swift", "scala", "dart"), C_LIKE),
    "php": f"{HASH}|{C_LIKE}",
    **dict.fromkeys(("sql", "lua", "hs", "elm"), r"--\s"),
    **dict.fromkeys(("html", "htm", "xml", "vue", "svelte"), r"<!--"),
}
DEFAULT_COMMENT = f"{HASH}|{C_LIKE}"

DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
DOC_DIRS = {"docs", "doc"}
DOC_NAMES = re.compile(r"^(readme|changelog|license|contributing|authors|notice)\b", re.I)
# Under these folders every file is test data.
FIXTURE_DIRS = {"fixtures", "__fixtures__", "testdata", "__snapshots__"}
# Under these folders a code file is a test (a helper included); a shell script, config or data file is harness.
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}
TEST_CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt", ".rs", ".c",
                 ".cc", ".cpp", ".cs", ".php", ".swift", ".scala", ".ex", ".exs"}
TEST_NAMES = re.compile(r"^(test_.*|.*_test\.\w+|.*\.(test|spec)\.\w+|conftest\.py)$")
STORAGE_EXT = {".sql", ".proto", ".avsc", ".fbs", ".prisma"}
STORAGE_DIRS = {"migrations", "migration"}
SHELL_EXT = {".sh", ".bash", ".zsh", ".ksh", ".fish", ".ps1", ".psm1", ".bat", ".cmd"}
SHEBANG = re.compile(r"^#!.*\b(ba|z|k|da|fi)?sh\b")
CI_DIRS = (".github/workflows/", ".github/actions/", ".circleci/", ".buildkite/", ".gitlab/", ".husky/")
CI_NAMES = re.compile(r"^(\.gitlab-ci\.ya?ml|\.travis\.ya?ml|azure-pipelines\.ya?ml|bitbucket-pipelines\.ya?ml"
                      r"|jenkinsfile|action\.ya?ml|\.pre-commit-config\.ya?ml|makefile|gnumakefile|.*\.mk"
                      r"|dockerfile.*|.*\.dockerfile|docker-compose.*\.ya?ml|compose\.ya?ml"
                      r"|package\.json|\.npmrc|setup\.py|setup\.cfg|pyproject\.toml|requirements.*\.(txt|in)"
                      r"|pipfile|tox\.ini|noxfile\.py|cargo\.toml|go\.mod|gemfile|rakefile|build\.gradle(\.kts)?"
                      r"|settings\.gradle(\.kts)?|pom\.xml|build\.xml|cmakelists\.txt|justfile|taskfile\.ya?ml"
                      r"|\.envrc|\.gitattributes|\.gitmodules)$", re.I)
AGENT_NAMES = re.compile(r"^(agents|claude|gemini|skill|copilot-instructions)\.md$|^\.(cursor|windsurf|cline)rules$"
                         r"|.*-brief\.md$|.*\.mdc$|.*\.prompt\.md$|.*\.instructions\.md$", re.I)
AGENT_DIRS = {"skills", "agents", "prompts", "commands", "rules", ".claude", ".cursor", ".codex", ".agents",
              ".windsurf", ".gemini"}
GATE_FILES = {".review-gate.yml", ".review-gate.yaml", "CODEOWNERS"}
CODEOWNERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
SETTINGS_FILE = ".agents/settings.json"


def is_test(parts, name, ext):
    if FIXTURE_DIRS & set(parts):
        return True
    if TEST_NAMES.match(name):
        return True
    return bool(TEST_DIRS & set(parts)) and ext in TEST_CODE_EXT


def kind(path, first=""):
    """`test`, `harness`, `doc`, `storage`, `shell`, `ci`, `agent-instructions` or `source`. `first` is the file's first added
    line, for a shebang."""
    parts = path.split("/")
    dirs, name = parts[:-1], parts[-1]
    ext = os.path.splitext(name)[1].lower()
    if is_test(dirs, name, ext):
        return "test"
    if TEST_DIRS & set(dirs) and not (ext in (".md", ".rst") or DOC_NAMES.match(name)):
        return "harness"
    if ext in SHELL_EXT or SHEBANG.match(first):
        return "shell"
    if path.startswith(CI_DIRS) or CI_NAMES.match(name):
        return "ci"
    if ext in (".md", ".mdc") or AGENT_NAMES.match(name):
        if AGENT_NAMES.match(name) or AGENT_DIRS & set(dirs) or path.startswith(".github/prompts/"):
            return "agent-instructions"
    if ext in DOC_EXT or DOC_DIRS & set(dirs) or DOC_NAMES.match(name):
        return "doc"
    if ext in STORAGE_EXT or STORAGE_DIRS & set(dirs) or "schema" in name.lower():
        return "storage"
    return "source"


def comment(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return re.compile(r"\s*(" + COMMENTS.get(ext, DEFAULT_COMMENT) + ")")


def gate_patterns(read):
    """The path patterns the review gate's high tier and CODEOWNERS mark, from `read(<file>)` (None when missing)."""
    patterns = []
    text = read(".review-gate.yml") or read(".review-gate.yaml") or ""
    high = False
    for line in text.splitlines():
        m = re.match(r"^(\s*)(\w+):\s*(.*)$", line)
        if m:
            high = m.group(2) == "high"
            if high and m.group(3).startswith("["):
                patterns += [p.strip(" '\"") for p in m.group(3).strip("[]").split(",") if p.strip()]
            continue
        m = re.match(r"^\s*-\s*['\"]?([^'\"#\s]+)", line)
        if m and high:
            patterns.append(m.group(1))
    for name in CODEOWNERS:
        for line in (read(name) or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                patterns.append(line.split()[0])
    return patterns


def matches(pattern, path):
    """Whether a CODEOWNERS-style `pattern` (anchored with a leading `/`, a folder with a trailing `/`) covers `path`."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    folder = pattern.endswith("/")
    pattern = pattern.strip("/")
    if not pattern:
        return False
    parts = path.split("/")
    starts = [0] if anchored else range(len(parts))
    for i in starts:
        n = len(pattern.split("/"))
        head = "/".join(parts[i:i + n])
        if fnmatch.fnmatchcase(head, pattern) and (not folder or i + n < len(parts)):
            return True
    return False


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


def max_lines(folder, base_settings=None):
    """The `tier_max_lines` Setting, or the default when `~/.agents/bin/settings` is missing. With `base_settings`
    (the merge base's `.agents/settings.json` text, or "" when it has none), the repo Level is that file, not
    `folder`'s."""
    # `HOME` first, as in Git Bash: on Windows `expanduser` reads the profile folder, not where install.sh linked.
    tool = os.path.join(os.environ.get("HOME") or os.path.expanduser("~"), ".agents", "bin", "settings")
    if not os.path.exists(tool):
        return DEFAULT_MAX_LINES
    with tempfile.TemporaryDirectory() as scratch:
        if base_settings is not None:
            # A throwaway repo holding only the base's file, so the tool reads that as the repo Level.
            folder = scratch
            subprocess.run(["git", "init", "-q", scratch], capture_output=True, stdin=subprocess.DEVNULL, check=True)
            if base_settings:
                os.mkdir(os.path.join(scratch, ".agents"))
                with open(os.path.join(scratch, SETTINGS_FILE), "w", encoding="utf-8") as f:
                    f.write(base_settings)
        argv = [tool, "--dir", folder, "get", "review-pr", "tier_max_lines"]
        if os.name == "nt":  # Windows starts no `#!` script, and a bare `bash` there is WSL's: take Git Bash's
            argv = [shutil.which("bash") or "bash", *argv]
        res = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        sys.exit(f"tier.py: settings failed: {res.stderr.strip()}")
    value = res.stdout.strip()
    return int(value) if value else DEFAULT_MAX_LINES


def tier(files, limit, gate=()):
    """(`low` or `high`, the signals that decided it). `gate` holds the path patterns the gate files mark high."""
    signals = []
    size = 0
    for path, added in files:
        k = kind(path, added[0] if added else "")
        if path.split("/")[-1] in GATE_FILES or any(matches(p, path) for p in gate):
            signals.append(f"gate: {path}")
        if path == SETTINGS_FILE:
            signals.append(f"settings: {path}")
        if k in ("storage", "harness", "shell", "ci", "agent-instructions"):
            signals.append(f"{k}: {path}")
        if k in ("test", "doc", "agent-instructions"):
            continue
        size += len(added)
        hit = []
        skip = comment(path)
        for line in added:
            if skip.match(line):
                continue
            hit += [name for name, pattern in SINKS if name not in hit and pattern.search(line)]
        signals += [f"sink: {name} in {path}" for name in hit]
    if size > limit:
        signals.append(f"size: {size} added source lines > {limit}")
    if signals:
        return "high", signals
    return "low", [f"no sink, no storage file, {size} added source lines <= {limit}"]


def git_show(worktree, rev, path):
    """`path`'s text at `rev`, or None when it has none."""
    res = subprocess.run(["git", "-C", worktree, "show", f"{rev}:{path}"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    return res.stdout if res.returncode == 0 else None


def read_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):  # UTF-8, not the Windows locale codec, which cannot print every diff
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) == 2 and argv[0] == "--trim":
        with open(argv[1], encoding="utf-8", errors="replace") as f:
            sys.stdout.write(trim(f.read()))
        return 0
    if len(argv) == 3 and argv[0] == "--git":
        worktree, base = argv[1], argv[2]
        res = subprocess.run(["git", "-C", worktree, "diff", "--no-color", "--no-ext-diff", f"{base}..HEAD"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            sys.exit(f"tier.py: git diff failed: {res.stderr.strip()}")
        text = res.stdout
        gate = gate_patterns(lambda name: git_show(worktree, base, name))
        limit = max_lines(worktree, git_show(worktree, base, SETTINGS_FILE) or "")
    elif len(argv) == 1 and not argv[0].startswith("--"):
        with open(argv[0], encoding="utf-8", errors="replace") as f:
            text = f.read()
        gate = gate_patterns(read_file)
        limit = max_lines(os.getcwd())
    else:
        sys.exit(__doc__.split("\n\n")[1])
    verdict, signals = tier(parse(text), limit, gate)
    print(verdict)
    for signal in signals:
        print(signal)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
