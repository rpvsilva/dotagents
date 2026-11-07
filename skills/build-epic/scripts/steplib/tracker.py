"""GitHub tracker updates for a run, each made only while the run's identity is still the active login.

Before every update, `gh api user` names the active login (an App installation token, which cannot call it, is
`app-installation` when `gh api installation/repositories` answers). The first update of a run records that login as
the run's identity in `<run>/tracker.json`. When a later one finds another login, the update is held in that file and
`core.Fail("identity-changed")` is raised; the next update made under the run's identity applies the held ones first,
in order. The same file records the human's login (learned once with `GH_TOKEN` unset) and the labels this run has
created, so `gh label create --force` runs once per label per run.
"""
import json
import os
import re

from . import core, gh

MARKER = "<!-- build-epic-plan -->"
IN_PROGRESS = "in progress"
APP = "app-installation"


def issues(value):
    """Issue numbers from a ticket's `Issue` line (`#8, #9`, `8`); none for `none` or a missing line."""
    if not value or value.strip().lower().startswith("none"):
        return []
    return [int(n) for n in re.findall(r"(?<![\w/])#?(\d+)\b", value)]


def setting(section, key, repo, log):
    """A Setting's effective value through `~/.agents/bin/settings`, or None when unset or the tool is missing."""
    tool = os.path.join(core.home(), ".agents", "bin", "settings")
    if not os.path.exists(tool):
        log.write(f"no {tool}: {section}.{key} taken as unset\n")
        return None
    res = core.run([tool, "--dir", repo, "get", section, key], log)
    if res.rc != 0:
        raise core.Fail(f"settings: {section}.{key}")
    return res.out.strip() or None


class Tracker:
    """The tracker updates of one run, in the run's repo (so `gh` resolves `{owner}/{repo}` from it)."""

    def __init__(self, run_dir, repo, log):
        self.run_dir, self.repo, self.log = core.resolve(run_dir), core.resolve(repo), log
        self.state_path = self.run_dir / "tracker.json"

    # -- state ---------------------------------------------------------------------------------------------------

    def _load(self):
        if not self.state_path.exists():
            return {"identity": None, "human": None, "labels": [], "held": []}
        return json.loads(self.state_path.read_text())

    def _save(self, state):
        tmp = self.state_path.with_name(".tracker.json.tmp")
        tmp.write_text(json.dumps(state, indent=1) + "\n")
        os.replace(str(tmp), str(self.state_path))

    def _lock(self):
        return core.file_lock(self.run_dir / ".tracker.lock")

    def held(self):
        """The updates waiting for the identity to come back, as `[op, args]` pairs."""
        return self._load()["held"]

    # -- gh ------------------------------------------------------------------------------------------------------

    def _gh(self, args, what, env=None, identity=True):
        res = gh.gh(args, self.log, cwd=self.repo, env=env, identity=identity)
        if res.rc != 0:
            raise core.Fail(f"gh-{what}")
        return res.out

    def login(self):
        """The active login: `gh api user`, or `app-installation` for an App installation token."""
        res = gh.gh(["api", "user", "--jq", ".login"], self.log, cwd=self.repo)
        if res.rc == 0 and res.out.strip():
            return res.out.strip()
        if gh.gh(["api", "installation/repositories", "--jq", ".total_count"], self.log, cwd=self.repo).rc == 0:
            return APP
        raise core.Fail("identity-unknown")

    def human(self):
        """The human's own login (`gh api user` with `GH_TOKEN` unset), learned once per run."""
        with self._lock():
            state = self._load()
            if not state["human"]:
                env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
                login = self._gh(["api", "user", "--jq", ".login"], "human", env=env, identity=False).strip()
                if not login:
                    raise core.Fail("gh-human")
                state["human"] = login
                self._save(state)
            return state["human"]

    # -- updates -------------------------------------------------------------------------------------------------

    def _update(self, op, *args):
        with self._lock():
            state = self._load()
            now = self.login()
            if not state["identity"]:
                state["identity"] = now
                self._save(state)
            if now != state["identity"]:
                state["held"].append([op, list(args)])
                self._save(state)
                self.log.write(f"identity is {now}, not {state['identity']}: held {op} {args}\n")
                raise core.Fail("identity-changed")
            while state["held"]:
                held_op, held_args = state["held"][0]
                self.log.write(f"applying held {held_op} {held_args}\n")
                getattr(self, "_do_" + held_op)(state, *held_args)
                state["held"].pop(0)
                self._save(state)
            getattr(self, "_do_" + op)(state, *args)
            self._save(state)

    def flush(self):
        """Applies the held updates, once the identity is back."""
        self._update("nothing")

    def add_label(self, issue, label):
        """Adds `label` to the issue, creating it (`--force`) the first time this run uses it."""
        self._update("add_label", int(issue), label)

    def remove_label(self, issue, label):
        self._update("remove_label", int(issue), label)

    def assign(self, issue, login):
        self._update("assign", int(issue), login)

    def upsert_plan(self, issue, body):
        """Puts `body` in the issue's one plan comment (starting with the marker): edits it when it exists."""
        self._update("upsert_plan", int(issue), body)

    def comment(self, issue, body):
        """Adds one comment, `body`, to the issue."""
        self._update("comment", int(issue), body)

    def close(self, issue, comment):
        """Closes the issue as completed, with `comment`."""
        self._update("close", int(issue), comment)

    def _do_nothing(self, state):
        pass

    def _do_add_label(self, state, issue, label):
        if label not in state["labels"]:
            self._gh(["label", "create", label, "--force"], "label")
            state["labels"].append(label)
            self._save(state)
        self._gh(["issue", "edit", str(issue), "--add-label", label], "label")

    def _do_remove_label(self, state, issue, label):
        self._gh(["issue", "edit", str(issue), "--remove-label", label], "label")

    def _do_assign(self, state, issue, login):
        self._gh(["issue", "edit", str(issue), "--add-assignee", login], "assign")

    def _do_upsert_plan(self, state, issue, body):
        if not body.startswith(MARKER):
            body = f"{MARKER}\n{body}"
        found = self._gh(["api", f"repos/{{owner}}/{{repo}}/issues/{issue}/comments", "--paginate", "--jq",
                          f'.[] | select(.body | startswith("{MARKER}")) | .id'], "comment").split()
        if found:
            self._gh(["api", f"repos/{{owner}}/{{repo}}/issues/comments/{found[0]}", "-X", "PATCH",
                      "-f", f"body={body}"], "comment")
        else:
            self._gh(["api", f"repos/{{owner}}/{{repo}}/issues/{issue}/comments", "-f", f"body={body}"], "comment")

    def _do_comment(self, state, issue, body):
        self._gh(["issue", "comment", str(issue), "--body", body], "comment")

    def _do_close(self, state, issue, comment):
        self._gh(["issue", "close", str(issue), "--reason", "completed", "--comment", comment], "close")
