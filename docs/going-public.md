# 🌍 Going public

[← Back to the docs](README.md)

This page records the history scan and the decision to keep the git history, and lists the maintainer's steps to switch the repo to public, in order. Do the steps only after the [APM study](apm.md) decision is merged.

## History scan

| Item | Record |
|---|---|
| Scanner | [gitleaks](https://github.com/gitleaks/gitleaks), the `secrets` job of [`ci.yml`](../.github/workflows/ci.yml), on every PR, with `fetch-depth: 0` (full history); the maintainer's local run uses the same tool |
| Version and run | gitleaks v8.21.2, built from source (the `zricethezav/gitleaks` Docker image is the alternative), run on 2026-11-23 |
| Local command | `gitleaks git --log-opts="--branches --tags" --redact -v .` (or the `zricethezav/gitleaks` Docker image when gitleaks is not installed) |
| Refs scanned | `main` and every branch and tag pushed to GitHub. Local-only refs (`refs/build-epic/*`, `refs/review/*`, stashes) are never pushed and are out of scope |
| Commits | 180 commits on origin's branches and tags, no leaks after the `.gitleaksignore` entry below. A `--all` scan of local refs too (389 commits) is also clean |
| Token patterns | A search of history with the token rules of [`secret-patterns.txt`](../scripts/hooks/secret-patterns.txt) (`gh_`, `github_pat_`, `AKIA`, `xox`, `sk-ant-`, `AIza`, private keys) found one hit: the fake `ghp_` placeholder in the agent-flow mod's masking test (`adapters/claude/mods/agent-flow/hooks/log.unit.ts:115`). No `.pem`, `.key`, `.p12` or `.netrc` file was ever added |
| Findings | One: the same fake `ghp_` placeholder at `adapters/claude/mods/agent-flow/hooks/log.unit.ts:115`, allowed in [`.gitleaksignore`](../.gitleaksignore) with a `#` reason above it. The rescan finds no leaks. Each accepted finding is an entry there; all are fake credentials built for masking tests |

Run the local command again just before the switch and compare its findings with `.gitleaksignore`. A finding with no entry is unresolved: see the decision rule below.

## Decision: keep the history

**Rule.** Keep the history unless the scan finds an unresolved credential, or a raw telemetry blob, in a published ref. Then squash into a fresh repo.

**Result.** Keep. No credential is unresolved. The one raw telemetry blob in history sits in a stash commit, which was never pushed, so no published ref holds it. The telemetry fixtures in the tree use synthetic values.

**Reason.** Squashing costs more than it protects:
- It drops the commit history, the `#NNN` references in commit messages and every PR and issue link.
- It must go to a fresh repo, because GitHub keeps `refs/pull/*` and fetchable dangling objects after a force-push.
- It would invalidate the commit-keyed entries in `.gitleaksignore`.

**What stays public.** Keeping the history publishes these, and the maintainer accepts that:
- The commit author and committer emails.
- The old GitHub organisation name, in older image paths and docs.
- The old cluster and warm-pool names, in older remote docs.
- The maintainer's GitHub login, already public in `CODEOWNERS`, `.review-gate.yml` and the image path.

If the maintainer stops accepting any of these, squash into a fresh repo before step 4 and redo `.gitleaksignore`.

## Switch to public

Do the steps in this order. Run each command from a checkout of the repo with `gh` logged in as the maintainer.

1. **Install the Renovate app on the repo.** Open <https://github.com/apps/renovate>, choose **Configure** and select the repo. Check that it opens its onboarding or weekly PR (see [dependencies](dependencies.md)).
2. **Make the runner image public and check an anonymous pull.** In the GitHub profile's **Packages**, open `agent-runner`, then **Package settings**, **Change visibility**, **Public**. Then:
   ```sh
   docker logout ghcr.io && docker pull ghcr.io/rpvsilva/agent-runner:main
   ```
   The pull must succeed with no login.
3. **Set the description and topics.**
   ```sh
   gh repo edit rpvsilva/dotagents \
     --description "One portable AI setup: skills, subagents, hooks and MCP servers for Claude Code, Codex and Cursor" \
     --add-topic ai-agents --add-topic claude-code --add-topic codex --add-topic cursor \
     --add-topic mcp --add-topic agent-skills
   ```
4. **Switch the visibility.** This cannot be undone in effect: forks and caches keep the content.
   ```sh
   gh repo edit rpvsilva/dotagents --visibility public --accept-visibility-change-consequences
   ```
5. **Turn on private vulnerability reporting.** [`SECURITY.md`](../SECURITY.md) tells reporters to use it.
   ```sh
   gh api -X PUT repos/rpvsilva/dotagents/private-vulnerability-reporting
   ```
6. **Protect `main`.** Run it only after step 4: rulesets need a public repo on a free plan. Run it again to update the ruleset.
   ```sh
   scripts/protect-main.sh rpvsilva/dotagents
   ```
7. **Check the README renders on GitHub.** Open the repo page and check the CI badge, every relative link (docs, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`) and the emoji tables.
