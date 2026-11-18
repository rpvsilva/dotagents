# Setup: `init`, `rules` and `setup`

## `init run`

Create the run folder and run `init_run.py <run> <repo> [--epic <n>]`, `--epic` naming the epic's issue when the input is one. It prints `OK run initialised repo=<path> default=<branch> remote=<url> identity=<command|login>`: the identity is decided here, once per run.

## `rules run`

1. [Learn the rules](../../_shared/conventions.md#learn-the-rules) with notes file `.agents/build-epic-notes.md` (test commands, areas to avoid) and the `tracker`, `max_parallel`, `in_progress`, `cap_mb`, `cap_secs`, `merge_command`, `identity_command`, `base` and `merge_identity` Settings, and the [ship-pr](../../ship-pr/SKILL.md) rules from its step 1: the commit convention, the merge rule and the identity PRs are opened and merged as.
2. **Merge rule.** When it is a command (e.g. `scripts/merge-pr.sh`), it belongs in the `merge_command` Setting, so the scripts merge ticket PRs by it: when that Setting is unset, ask the human to set it ([configure](../../configure/SKILL.md)); a repo with a `.review-gate.yml` refuses ticket merges into the default branch without it. When the rules name an identity (e.g. `GH_TOKEN="$(scripts/gh-app-token.sh)"`), its token command belongs in the `identity_command` Setting; unset, the scripts act as the current `gh` login.
3. **Base.** `--into <branch>`, else the `base` Setting, else the default branch. When it is another branch than the default, record it with `decide.py <run> set base <branch>` before anything else runs. Never change it by hand later: a new base mid-run goes through `catch_up.py` ([build.md](build.md#base-change)).
4. **Who merges.** The `merge_identity` Setting (`app`, the default, when the rules name an identity for merges; `human` when merges run as the human's own login). When the rules call for `human` and the Setting says otherwise, ask the human to set it; never set it yourself. Record it with `decide.py <run> set merge_identity <value>`, and for `human` also `decide.py <run> set human <login>`. Read from the merge rule whether an agent may merge only a PR reviewed at its head, and record `decide.py <run> set review_before_agent_merge yes|no`.

`decisions.md` only records: each merge takes its identity from the Setting, and a recorded `merge_identity:` that differs refuses it (`wrong-identity`) rather than widening it.

## `setup run`

Run `setup.py <run>`. It prints `OK run set up ci=<on|off> env=<path> skips=<n> caps=<mb>MB,<secs>s remote=<on|off> [parallel=<n> (<cpus> cores, <gb> GB)]`: it runs `map.md`'s `Setup` once into `<run>/env/`, records `test_command:` (`map.md`'s Checks in that environment), `ci:`, `caps:` and `remote:` (on when `remote-agent ready` exits 0), and lists in `skips.md` the tools the suite skipped without.

- **N**: `--parallel N`, else `max_parallel` when a Level sets it, else the printed `parallel=`. Record it with `decide.py <run> set parallel <N>` and tell the human N and its reason once (the flag, the Level, or the machine: `N=4: 8 cores, 16 GB`).
- **Skips**: when `skips.md` lists tools, ask the human once, in one message, whether to install each, and record their answer with `decide.py <run> quote`. Install the ones they accept as they say, delete `skips.md` and run `setup.py <run>` again. A declined tool stays skipped; its reason reaches the ticket PR and the report.
- `FAIL run unclear-setup`: `map.md`'s `Setup` line is in no form it reads; start a revise planner ([plan.md](plan.md#plan-revise)) with that line as the reason. `FAIL run setup-failed <command>`: tell the human with the log; a rerun tries again.

Done when `decisions.md` holds `test_command:` and `parallel:`, and every tool in `skips.md` was asked about.
