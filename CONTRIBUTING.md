# Contributing

Thanks for helping. This page covers how to set up, which checks to run and how a pull request is reviewed and merged. If you work with an AI coding agent, point it at [`AGENTS.md`](AGENTS.md): those are the rules for this repo.

To report a vulnerability, do not open an issue or a PR. Follow [`SECURITY.md`](SECURITY.md).

## Set up

1. Fork the repo and clone your fork.
2. Optional: install the parts you want to try with `./install.sh <group>`. Group names are in [`groups.json`](groups.json). Do not install the `personal` group: it holds the maintainer's own setup.
3. Make your change on a branch.

Each skill, hook, subagent and other part has its own steps in the **Contributing** section of [`AGENTS.md`](AGENTS.md).

## Run the checks

Run this before you push:

```sh
tests/run.sh
```

It runs only the checks that cover the files you changed, and names the ones it skips. To run every check:

```sh
tests/run.sh --all
```

On macOS, `tests/run.sh` then runs each suite a second time under `/bin/bash` 3.2, the bash that ships with the system. Write scripts that run under it.

## What CI runs

CI runs on every pull request into `main`, as three jobs of [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- `lint`: shellcheck and actionlint.
- `test`: the suites in `tests/`.
- `secrets`: a scan for leaked secrets.

A draft PR skips them until you mark it ready for review. Mark it ready only when its head is final, since each push to a ready PR runs CI again.

The first time you open a PR from a fork, its workflows wait until the maintainer approves them. This is a GitHub setting for new contributors, not a sign of a problem.

## Open a pull request

- Open the PR from your fork, as yourself.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit messages, scoped by area when useful: `feat(skills): add renovate-prs`. Other types are `fix:`, `docs:` and `chore:`.
- Fill in the PR template. `Testing`, `Risk` and `How to revert` are required: under `Testing`, write what you ran and its result. `TODO` or `N/A` counts as empty.

## Review and merge

- CI must be green.
- A code owner must approve any PR that touches a high-tier path. [`.review-gate.yml`](.review-gate.yml) lists them, and [`CODEOWNERS`](CODEOWNERS) mirrors the list. Paths that run code, guard the gate or reach every tool's instructions are high-tier.
- The maintainer merges the PR. Do not merge it yourself, and do not push to `main`.

Review comments and requested changes go on the PR. Push fixes to the same branch.
