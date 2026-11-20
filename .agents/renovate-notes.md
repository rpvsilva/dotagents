# Renovate notes

Lessons only.

- `scripts/shellcheck-version` and `scripts/shellcheck-image` move together: the image tag must be `v<version>` (`tests/repo.test.sh` fails otherwise). Take both in one PR.
- The `agent-pr-gate` pin is not Renovate's: bump it by hand in both `.github/workflows/agent-pr-gate.yml` and `GATE_REF` in `scripts/merge-pr.sh`, to the same commit.
