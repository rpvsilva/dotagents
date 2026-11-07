Review the hub task PR #$pr and its whole ticket, in one run. The worktree $worktree is checked out at the PR head, $head, which holds every task of the ticket.

Read these, and take everything else from the code:
$reads

A change on a base after its merge base is base drift, not a finding. Run tests only after you have read the whole diff, both sections, and each changed file in context.

## A. The task PR

What it was asked to do: $task

Review this diff, from the merge base of the ticket branch and the head:

    $task_diff

## B. The whole ticket

What it was asked to do: $ticket

Review this diff, from the merge base of $base and the head:

    $diff

Look across the ticket's tasks for what each one alone would miss: features that break each other, a contract changed on one side only.

Report the findings of both sections in one report as the brief says, each finding marked A or B.
