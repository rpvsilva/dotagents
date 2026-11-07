Re-review PR #$pr after its fixes. The worktree $worktree is checked out at its new head, $head; $base is the head last reviewed.

Read these, and take everything else from the code:
$reads
- $findings: the earlier findings, numbered

Review only the new diff, from the merge base of $base and the head:

    $diff

A change outside this diff is not a finding unless the diff breaks it. Run tests only after you have read the whole diff and each changed file in context.

Check each numbered finding against the new head and give it one line: its number, then `fixed`, `not fixed` or `worse`, with the file:line that shows it. Then report, as the brief says, only new findings the diff brings in.
