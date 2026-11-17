"""quality.py: scores a ref of a throwaway repo with a stub pytest, and reads a hand-made run folder."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quality  # noqa: E402

BOT = "some-app[bot] <1+some-app[bot]@users.noreply.github.com>"
HUMAN = "Some One <one@example.com>"

# A stand-in for `python -I -m pytest ...`: writes ACCEPT_RESULTS as acceptance/conftest.py would, one row per
# accept_*.py file, passed when the checkout holds a file named after its feature, and notes the checkout it saw.
STUB = """#!/bin/sh
set -e
out="$ACCEPT_RESULTS"
printf '%s\\n' "$ACCEPT_CHECKOUT" > "$(dirname "$out")/seen"
for a; do last="$a"; done
[ -z "$EXPECT_ACCEPTANCE" ] || [ "$last" = "$EXPECT_ACCEPTANCE" ] || exit 3
rows=""
for f in subtasks notes snooze dependencies next; do
  o=failed; [ -f "$ACCEPT_CHECKOUT/$f" ] && o=passed
  rows="$rows{\\"file\\": \\"accept_$f.py\\", \\"name\\": \\"test_$f\\", \\"outcome\\": \\"$o\\", \\"tier\\": \\"core\\"},"
done
rows="$rows{\\"file\\": \\"accept_next.py\\", \\"name\\": \\"test_strict\\", \\"outcome\\": \\"error\\", \\"tier\\": \\"strict\\"}"
printf '[%s]\\n' "$rows" > "$out"
"""


def git(repo, *args, author=None):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    cmd += [*args] + ([f"--author={author}"] if author else [])
    return subprocess.run(cmd, check=True, capture_output=True, text=True, env=env).stdout.strip()


class Quality(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "sandbox"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.commit("README.md", "base", BOT)
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.commit("subtasks", "feat: Subtasks (#10)", BOT)
        self.commit("notes", "feat: Notes (#11)", BOT)
        self.commit("fix.txt", "fix: by hand", HUMAN)
        self.tip = git(self.repo, "rev-parse", "HEAD")
        self.stub = self.tmp / "stub-python"
        self.stub.write_text(STUB, encoding="utf-8")
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IXUSR)
        self.run_dir = self.make_run()

    def commit(self, name, subject, author):
        (self.repo / name).write_text(subject + "\n", encoding="utf-8")
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", subject, author=author)

    def make_run(self):
        run = self.tmp / "run"
        for d in ("tickets", "reviews", "logs"):
            (run / d).mkdir(parents=True)
        (run / "tickets" / "01-a.md").write_text("# TT: A\nStatus: merged\nPR: https://x/pull/10\n", encoding="utf-8")
        (run / "tickets" / "02-b.md").write_text("# TT: B\nStatus: merged\nPR: https://x/pull/11\n", encoding="utf-8")
        (run / "logs" / "PR10-post_review.log").write_text(
            "== post_review PR10 /elsewhere/reviews/20-aaaaaaa.md --base origin/b at 1\n$ gh ...\n"
            "== post_review PR10 /elsewhere/reviews/20-aaaaaaa-security.md --base origin/b at 2\n"
            "== post_review PR10 /elsewhere/reviews/21-bbbbbbb.md --base origin/b at 3\n", encoding="utf-8")
        (run / "logs" / "PR11-post_review.log").write_text(
            "== post_review PR11 /elsewhere/reviews/30-ccccccc.md --note x at 1\n", encoding="utf-8")
        reviews = {
            "20-aaaaaaa.md": "## Findings\n1. **should-fix** ...\n\nstatus: done\nfollow-up: 1 blocker, 2 should-fix, 0 nit; 0 process\n",
            "20-aaaaaaa-security.md": "none\nfollow-up: no findings (0 blocker, 1 should-fix); coverage table complete\n",
            "21-bbbbbbb.md": "1. fixed\nfollow-up: 0 blocker, 9 should-fix, 9 nit\n",
            "30-ccccccc.md": "no findings.\n",
            "20-aaaaaaa-findings.md": "# Findings\n\n1. blocker | a.py:1 | x\n2. should-fix, b.py:2: y\n",
            "40-ddddddd-findings.md": "1. **B · should-fix · tests** · `t.py:3`\n2. nit, README.md:4: z\n",
        }
        for name, text in reviews.items():
            (run / "reviews" / name).write_text(text, encoding="utf-8")
        (run / "logs" / "01-01-merge_task.log").write_text(
            "FAIL 01-01 red tests (log: x)\nOK 01-01 done\nFAIL 01-01 red tests (log: x)\n", encoding="utf-8")
        (run / "logs" / "01-02-merge_task.log").write_text("FAIL 01-02 conflict a.py\n", encoding="utf-8")
        (run / "logs" / "batch-ship_batch.log").write_text("FAIL batch red 02\nOK batch landed 01\n", encoding="utf-8")
        return run

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = quality.main([str(a) for a in argv])
        return rc, json.loads(out.getvalue())

    def test_scores_the_ref_in_a_throwaway_worktree(self):
        status_before = git(self.repo, "status", "--porcelain")
        rc, out = self.main(self.repo, self.base, "--python", self.stub)
        self.assertEqual(rc, 0, out)
        acc = out["acceptance"]
        self.assertEqual((acc["passed"], acc["failed"], acc["total"]), (0, 6, 6))
        rc, out = self.main(self.repo, "main", "--python", self.stub)
        acc = out["acceptance"]
        self.assertEqual(out["sha"], self.tip)
        self.assertEqual((acc["passed"], acc["total"], acc["pass_rate"]), (2, 6, 0.333))
        self.assertEqual(acc["feature"]["403-subtasks"], {"passed": 1, "failed": 0, "total": 1, "pass_rate": 1.0})
        self.assertEqual(acc["feature"]["407-next"]["total"], 2)
        self.assertEqual(acc["tier"]["strict"], {"passed": 0, "failed": 1, "total": 1, "pass_rate": 0.0})
        self.assertIn("accept_next.py::test_strict", acc["failed_tests"])
        self.assertNotIn("run", out)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.tip)
        self.assertEqual(git(self.repo, "status", "--porcelain"), status_before)
        self.assertEqual(len(git(self.repo, "worktree", "list").splitlines()), 1)

    def test_another_acceptance_folder_with_its_own_features(self):
        folder = self.tmp / "acceptance-16"
        folder.mkdir()
        (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (folder / "features.json").write_text(
            json.dumps({"accept_subtasks.py": "17-a", "accept_next.py": "18-b"}), encoding="utf-8")
        os.environ["EXPECT_ACCEPTANCE"] = str(folder.resolve())
        self.addCleanup(os.environ.pop, "EXPECT_ACCEPTANCE", None)
        rc, out = self.main(self.repo, "main", "--python", self.stub, "--acceptance", folder)
        self.assertEqual(rc, 0, out)
        acc = out["acceptance"]
        self.assertEqual(list(acc["feature"]), ["17-a", "18-b"])
        self.assertEqual((acc["feature"]["17-a"]["pass_rate"], acc["feature"]["18-b"]["pass_rate"]), (1.0, 0.0))
        self.assertEqual(acc["feature_weighted"], 0.5)
        self.assertEqual(acc["total"], 6)  # every row counts in the totals, mapped to a feature or not
        rc, out = self.main(self.repo, "main", "--python", self.stub)  # the default folder is not the one expected
        self.assertEqual(rc, 1)
        self.assertIn("exit 3", out["error"])

    def test_a_bad_acceptance_folder_is_refused(self):
        rc, out = self.main(self.repo, "main", "--python", self.stub, "--acceptance", self.tmp / "nothing")
        self.assertEqual(rc, 1)
        self.assertIn("not an acceptance folder", out["error"])
        folder = self.tmp / "bad"
        folder.mkdir()
        (folder / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (folder / "features.json").write_text("[1]", encoding="utf-8")
        rc, out = self.main(self.repo, "main", "--python", self.stub, "--acceptance", folder)
        self.assertEqual(rc, 1)
        self.assertIn("features.json", out["error"])

    def test_default_features_weigh_each_feature_the_same(self):
        rc, out = self.main(self.repo, "main", "--python", self.stub)
        # 403 1/1, 404 1/1, 405 0/1, 406 0/1, 407 0/2
        self.assertEqual(out["acceptance"]["feature_weighted"], 0.4)

    def test_a_bare_clone_works_too(self):
        bare = self.tmp / "bare"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.repo), str(bare)], check=True)
        rc, out = self.main(bare, "main", "--python", self.stub)
        self.assertEqual((rc, out["acceptance"]["passed"]), (0, 2))

    def test_json_reviews_count_their_findings(self):
        (self.run_dir / "tickets" / "03-c.md").write_text("# TT: C\nStatus: merged\nPR: https://x/pull/12\n",
                                                          encoding="utf-8")
        (self.run_dir / "logs" / "PR12-post_review.log").write_text(
            "== post_review PR12 /elsewhere/reviews/50-eeeeeee.json --base origin/b at 1\n"
            "== post_review PR12 /elsewhere/reviews/50-eeeeeee-security.json --base origin/b at 2\n"
            "== post_review PR12 /elsewhere/reviews/51-fffffff.json --base origin/b at 3\n", encoding="utf-8")
        finding = {"axis": "bugs", "path": "a.py", "line": 1, "claim": "c", "scenario": "s"}
        (self.run_dir / "reviews" / "50-eeeeeee.json").write_text(json.dumps({"status": "done", "findings": [
            dict(finding, severity="blocker"), dict(finding, severity="nit"), dict(finding, severity="nit")]}))
        (self.run_dir / "reviews" / "50-eeeeeee-security.json").write_text(
            json.dumps({"status": "done", "findings": [dict(finding, severity="should-fix")]}))
        (self.run_dir / "reviews" / "51-fffffff.json").write_text("not json")
        rc, out = self.main(self.repo, "main", "--python", self.stub, "--run", self.run_dir)
        reviews = out["run"]["ticket_reviews"]
        self.assertEqual(reviews["reports"][-2:], ["50-eeeeeee.json", "50-eeeeeee-security.json"])
        # The Markdown ones of make_run (1 blocker, 3 should-fix) plus these (1 blocker, 1 should-fix, 2 nit).
        self.assertEqual((reviews["blocker"], reviews["should-fix"], reviews["nit"]), (2, 4, 2))
        self.assertEqual(reviews["re_reviews"][-1], "51-fffffff.json")

    def test_run_folder_metrics(self):
        rc, out = self.main(self.repo, "main", "--python", self.stub, "--run", self.run_dir)
        self.assertEqual(rc, 0, out)
        run = out["run"]
        self.assertEqual(run["ticket_prs"], [10, 11])
        reviews = run["ticket_reviews"]
        self.assertEqual(reviews["reports"], ["20-aaaaaaa.md", "20-aaaaaaa-security.md", "30-ccccccc.md"])
        self.assertEqual((reviews["blocker"], reviews["should-fix"], reviews["nit"]), (1, 3, 0))
        self.assertEqual(reviews["unparsed"], ["30-ccccccc.md"])
        self.assertEqual(reviews["re_reviews"], ["21-bbbbbbb.md"])
        self.assertEqual(run["fix_findings"], {"files": 2, "blocker": 1, "should-fix": 2, "nit": 1})
        self.assertEqual((run["red_merges"], run["batch_red"]), (2, 1))
        hand = run["hand_fixes"]
        self.assertEqual((hand["count"], hand["after_last_merge"]), (1, 1))
        self.assertEqual(hand["commits"][0]["subject"], "fix: by hand")
        self.assertEqual(hand["last_merge"], git(self.repo, "rev-parse", "--short=7", "HEAD~1"))

    def test_no_ticket_merge_on_the_ref_leaves_hand_fixes_unknown(self):
        hand = quality.hand_fixes(self.repo, self.base, [10, 11])
        self.assertEqual((hand["last_merge"], hand["count"]), (None, None))
        self.assertEqual(quality.hand_fixes(self.repo, self.tip, [])["count"], None)

    def test_python_defaults_to_the_run_environment(self):
        self.assertEqual(quality.pick_python("/x/python", self.run_dir), "/x/python")
        self.assertEqual(quality.pick_python(None, self.run_dir), sys.executable)
        env = self.run_dir / "env" / "bin"
        env.mkdir(parents=True)
        (env / "python").write_text("", encoding="utf-8")
        self.assertEqual(quality.pick_python(None, self.run_dir), str(env / "python"))

    def test_errors_print_one_json_line(self):
        rc, out = self.main(self.repo, "no-such-ref", "--python", self.stub)
        self.assertEqual(rc, 1)
        self.assertIn("no-such-ref", out["error"])
        rc, out = self.main(self.repo, "main", "--run", self.tmp / "nothing")
        self.assertEqual(rc, 1)
        self.assertIn("not a run folder", out["error"])
        broken = self.tmp / "broken-python"
        broken.write_text("#!/bin/sh\necho boom >&2\nexit 4\n", encoding="utf-8")
        broken.chmod(0o755)
        rc, out = self.main(self.repo, "main", "--python", broken)
        self.assertEqual(rc, 1)
        self.assertIn("wrote no results (exit 4): boom", out["error"])
        self.assertEqual(len(git(self.repo, "worktree", "list").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
