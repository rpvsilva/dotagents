# Decisions
base: main
ci: on
merge_identity: app
review_before_agent_merge: yes

## Authorizations

| clause | waived | quote |
| --- | --- | --- |
| Leave merges to the human | yes | Rui: "merge them as the App \| no waiting" |
| Approve high-tier PRs | no | Rui: "those are mine" |

## Decisions

- 2026-11-01: install shellcheck, skip docker
- 2026-11-01: go ahead, 03 can wait
- 2026-11-01: rebase 02 onto the new base
- 2026-11-02: drop task 01-03,
  the lexer covers it
