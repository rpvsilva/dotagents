Review the whole ticket in PR #$pr, once and in depth. The worktree $worktree is checked out at its head, $head.

Read these, and take everything else from the code:
$reads

Review this diff, from the merge base of $base and the head:

    $diff

A change on $base after the merge base is base drift, not a finding. Look across the ticket's tasks for what each one alone would miss: features that break each other, a contract changed on one side only. Run tests only after you have read the whole diff and each changed file in context.

Report as the brief says.
