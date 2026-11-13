"""Records the integration ancestry a ticket's squash-merged tasks lost, so its PR merges into the base cleanly.

A hub task merges the base into its branch, but its squash merge into the ticket branch keeps the tree and drops that
parent: the ticket PR would then conflict with the base it already holds. `record` adds the commit
`git merge -s ours origin/<base>` would make on the ticket branch (the ticket's own tree, the base tip as a second
parent), made with `git commit-tree` so no checkout is touched, and pushes it. It does so only when both guards hold:

- the ticket head's tree equals the reviewed tree, else `tree-mismatch`: something the reviewer never read would ship;
- the base's content is already in that tree, else `reviewed-head-lacks-base`: the base moved after the review, and the
  ticket needs a real catch-up and a new review. It is proven by content, not ancestry, since squash merges drop the
  base tip from the ticket's history: `git merge-tree --write-tree origin/<base> <head>` must give the head's own tree.

Merged from the ticket's fork point, that 3-way merge replays base changes the ticket already holds, and conflicts (or
gives another tree) once a later task moved or rewrote those lines. So the merge starts from the base commit the
ticket really merged when there is one (`fork_point`): the newest base commit a merged task PR's head contains, whose
tree landed on the ticket branch (`task_heads` fetches those heads from GitHub's `refs/pull/<n>/head`). Base commits
after it are still merged in and checked, so a ticket the base moved past is still refused. With no such head, or a
fetch failing, the merge starts from the fork point as before.

Either refusal pushes nothing (`check` is the guards alone, for a caller that wants them before any push). A reviewed head git does not know fails `reviewed-unknown`; a ticket branch that moved
while pushing fails `head-moved`.
"""
from . import ci, core, runfolder

PULLS = "refs/build-epic/pull"  # where `task_heads` fetches the task PRs' heads to


def task_heads(git, run, ticket):
    """The heads of ticket `ticket`'s merged task PRs (`tasks/TT-*.md` set `done` with a `PR:` line), fetched from
    origin's `refs/pull/<n>/head` (GitHub keeps it once the branch is deleted) to `refs/build-epic/pull/<n>`. Empty when
    there is none or the fetch fails: the guard then merges from the fork point."""
    numbers = []
    for path in sorted((run / "tasks").glob(f"{ticket}-*.md")):
        number = ci.pr_number(runfolder.get(path, "PR"))
        if number and runfolder.get(path, "Status") == "done" and number not in numbers:
            numbers.append(number)
    if not numbers:
        return []
    refs = [f"+refs/pull/{n}/head:{PULLS}/{n}" for n in numbers]
    if git._git("fetch", "--no-write-fetch-head", "origin", *refs, check=False).rc != 0:
        git.log.write("fetching the task PRs' heads failed: the base check merges from the fork point\n")
        return []
    return [git.tip(f"{PULLS}/{n}") for n in numbers]


def fork_point(git, base_tip, head, heads):
    """The base commit `head` really merged, when it is newer than every merge base of `base_tip` and `head`: the
    newest commit of `base_tip`'s history that one of `heads` contains (their common ancestor when several are
    unrelated), counting only a head whose tree is on `head`'s first-parent history since the fork point (its squash
    merge). None otherwise."""
    if not heads:
        return None
    forks = git._git("merge-base", "--all", base_tip, head, check=False)
    if forks.rc != 0:
        return None
    forks = forks.out.split()
    landed = set(git._git("log", "--first-parent", "--format=%T", head, *(f"^{f}" for f in forks)).out.split())
    found = []
    for h in heads:
        if git.tree(h) not in landed:
            continue
        res = git._git("merge-base", base_tip, h, check=False)
        if res.rc == 0 and res.out.strip() not in found:
            found.append(res.out.strip())
    if not found:
        return None
    tips = git._git("merge-base", "--independent", *found).out.split()
    if len(tips) > 1:
        res = git._git("merge-base", "--octopus", *tips, check=False)
        tips = res.out.split() if res.rc == 0 else []
    if len(tips) != 1 or tips[0] in forks or not all(git.is_ancestor(f, tips[0]) for f in forks):
        return None
    return tips[0]


def check(git, base_tip, head, reviewed, base, heads=()):
    """Fails `reviewed-unknown`, `tree-mismatch` or `reviewed-head-lacks-base` unless `head` is the reviewed tree and
    already holds the content of `base_tip`; pushes nothing. `reviewed` is a commit; `base` names the branch in the log.
    `heads` are the ticket's merged task PRs' heads (`task_heads`): the merge starts from the base commit they prove
    the ticket merged (`fork_point`), else from the fork point."""
    try:
        reviewed = git.tip(reviewed)
    except core.Fail:
        raise core.Fail("reviewed-unknown")
    tree = git.tree(head)
    if tree != git.tree(reviewed):
        git.log.write(f"{head} has tree {tree}; the reviewed head {reviewed} has {git.tree(reviewed)}\n")
        raise core.Fail("tree-mismatch")
    since = fork_point(git, base_tip, head, heads)
    if since:
        git.log.write(f"{head} merged origin/{base} at {since} through a task PR: the base check merges from there\n")
    merged = git._git("merge-tree", "--write-tree", *(["--merge-base", since] if since else []), base_tip, head,
                      check=False)
    if merged.rc not in (0, 1):  # 1 is a conflict; anything else is git failing (before 2.38: no --write-tree)
        raise core.Fail("git-merge-tree")
    if merged.rc != 0 or merged.out.split("\n", 1)[0].strip() != tree:
        git.log.write(f"merging origin/{base} at {base_tip} into {head} changes its tree: the base holds content the "
                      f"reviewed head {reviewed} lacks\n")
        raise core.Fail("reviewed-head-lacks-base")
    return tree


def record(git, branch, base, reviewed, heads=()):
    """Returns the ticket branch's head on origin after recording `origin/<base>` as its ancestor (the head itself when
    it already contains it). `git` is a `gitops.Git` whose remote refs are fetched; `reviewed` is a commit. `heads` goes to `check`."""
    base_tip = git.tip(f"refs/remotes/origin/{base}")
    head = git.tip(f"refs/remotes/origin/{branch}")
    tree = check(git, base_tip, head, reviewed, base, heads)
    if git.is_ancestor(base_tip, head):
        return head
    message = f"Merge origin/{base} into {branch}\n\nRecords the integration ancestry; the tree is the reviewed one.\n"
    merged = git._git("commit-tree", tree, "-p", head, "-p", base_tip, "-m", message).out.strip()
    if git.push("origin", f"{merged}:refs/heads/{branch}").rc != 0:
        raise core.Fail("head-moved")
    git.fetch()
    return merged
