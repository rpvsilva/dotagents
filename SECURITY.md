# Security policy

## Report a vulnerability

Report it privately through GitHub private vulnerability reporting: open the repo's **Security** tab, choose **Report a vulnerability** and describe the problem.

Do not open a public issue or pull request for a vulnerability. This repo publishes no email address for reports.

Include what you ran, what you expected and what happened, so the maintainer can reproduce it.

## Supported versions

Fixes go to the `main` branch. There are no release branches: update to the latest `main`.

## Scope

In scope:

- **Hooks** (`scripts/hooks/`). A guard that fails open (lets through what it should block) is a vulnerability.
- **Remote runs** (`scripts/remote-agent*.sh`, `remote/`): how tokens are handled and what the sandbox can reach.
- **The runner image** (`remote/runner/`, `ghcr.io/rpvsilva/agent-runner`).

Out of scope:

- The `personal` Group, which is one person's own setup.
- Third-party tools and plugins that dotagents links or installs. Report those to their own projects.

## What to expect

The maintainer reads each report, tells you whether it is accepted, and fixes accepted ones on `main`. This is a personal project: no response time is promised.
