# Decisions
ci: off
merge_identity: app
review_before_agent_merge: no
test_command: export PATH="$PATH:$HOME/.agents/bin" && export CAPPED_MB="${CAPPED_MB:-2560}" CAPPED_SECS="${CAPPED_SECS:-600}" && capped -- python3 -I -m pytest -q -c pyproject.toml
caps: 2560 600
parallel: 4
approved: yes

## Authorizations

| clause | waived | quote |
| --- | --- | --- |
| Changes to the tests folder | yes | Rui: yes, agents merge tasks that add tests |

## Decisions

