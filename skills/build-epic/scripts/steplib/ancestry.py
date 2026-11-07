"""Records the integration ancestry a ticket's squash-merged tasks lost, so its PR merges into the base cleanly.

A hub task merges the base into its branch, but its squash merge into the ticket branch keeps the tree and drops that
parent: the ticket PR would then conflict with the base it already holds. `record` adds the commit
`git merge -s ours origin/<base>` would make on the ticket branch (the ticket's own tree, the base tip as a second
parent), made with `git commit-tree` so no checkout is touched, and pushes it. It does so only when both guards hold:

- the reviewed head contains the base tip, else `reviewed-head-lacks-base`: the base moved after the review, and the
  ticket needs a real catch-up and a new review;
- the ticket head's tree equals the reviewed tree, else `tree-mismatch`: something the reviewer never read would ship.

Either refusal pushes nothing. A reviewed head git does not know fails `reviewed-unknown`; a ticket branch that moved
while pushing fails `head-moved`.
"""
from . import core


def record(git, branch, base, reviewed):
    """Returns the ticket branch's head on origin after recording `origin/<base>` as its ancestor (the head itself when
    it already contains it). `git` is a `gitops.Git` whose remote refs are fetched; `reviewed` is a commit."""
    try:
        reviewed = git.tip(reviewed)
    except core.Fail:
        raise core.Fail("reviewed-unknown")
    base_tip, head = git.tip(f"origin/{base}"), git.tip(f"origin/{branch}")
    if not git.is_ancestor(base_tip, reviewed):
        git.log.write(f"reviewed head {reviewed} lacks origin/{base} at {base_tip}\n")
        raise core.Fail("reviewed-head-lacks-base")
    tree = git.tree(head)
    if tree != git.tree(reviewed):
        git.log.write(f"origin/{branch} at {head} has tree {tree}; the reviewed head {reviewed} has {git.tree(reviewed)}\n")
        raise core.Fail("tree-mismatch")
    if git.is_ancestor(base_tip, head):
        return head
    message = f"Merge origin/{base} into {branch}\n\nRecords the integration ancestry; the tree is the reviewed one.\n"
    merged = git._git("commit-tree", tree, "-p", head, "-p", base_tip, "-m", message).out.strip()
    if git.push("origin", f"{merged}:refs/heads/{branch}").rc != 0:
        raise core.Fail("head-moved")
    git.fetch()
    return merged
