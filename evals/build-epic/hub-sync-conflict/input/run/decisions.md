# Decisions
ci: off
merge_identity: app
review_before_agent_merge: no
test_command: python3 -I -m pytest -q -c pyproject.toml

## Authorizations

| clause | waived | quote |
| --- | --- | --- |
| Changes to the tests folder | yes | Rui: yes, agents merge tasks that add tests |

## Decisions

