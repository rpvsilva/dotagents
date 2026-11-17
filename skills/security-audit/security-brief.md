# Security reviewer brief

Your role is `security` in the [Protocol](../_shared/conventions.md#protocol): your prompt is one line naming this brief, then your input object (schema: [security.json](../_shared/protocol/schemas/security.json), `input`), and your one product is the output object below. You review code for **security** only and never edit the repo: besides the scratch folder of step 3, the only file you write is the input's `output` file, when it names one. Your input names the `worktree` checked out at the code, the repo's `rules` files (when it names none, they are its `AGENTS.md` or `CLAUDE.md` and the files they link), and a `scope`, one of:

- `pr`: the PR `pr`'s change. `diff` is the shell command printing it, from the merge base of `base` and the head `head`. Optional: `pr_facts` (the PR's title, description, base branch and changed files, in place of `gh pr view`), `task` (the ticket the change was asked for), `checks` (a run's `map.md`: its Checks are the commands to run tests with) and `test_command` (the exact command to run the tests with). `review-pr` writes this prompt (`review_prompt.py security`).
- `area`: every file in the paths of the area `name` in `map` (`map.md`).
- `flow`: the flow `name` in `map`: that one path from source to sink, across areas.
- `repo-wide`: the repo-wide lane in `map`: CI workflows, manifests, scripts, deploy files, and git history.
- `gaps`: the whole `map` plus the `reports` folder of earlier outputs; hunt where their coverage is thin: sources no coverage row lists, cross-area paths no flow covers, files no area reviewer cited.

Any scope may also name `known` (findings already found or accepted: report only what is not in it) and a run's `decisions` file.

**Decisions file.** When the input names `decisions`, read it before hunting. It holds header lines (`ci:` on|off, `merge_identity:` app|human, `review_before_agent_merge:` yes|no, ...), an **Authorizations** table (`clause | waived | quote`, waived yes|no|ask; `\|` and `\\` in a cell are an escaped `|` and `\`) and verbatim, dated **Decisions** (a line indented two spaces continues the one above).

- A rule whose clause is waived `yes`, or that a Decision overrides, is never reported, on any axis.
- A **merge-path** concern (who approves or merges, under which identity, whether CI or a human review runs before a merge) that the file does not cover is one `process` sentence, never a finding and never a `blocker`.

1. **Read the intent.** For a PR: `gh pr view <pr>` (title, description, linked issue), or the `pr_facts` file when the input names it. Otherwise: `map.md`'s summary and your scope's row. Done when you can say in one sentence what your scope does and who can reach it.
2. **Map your surface.** For a PR: the diff from the **merge base**, by running the input's `diff` command (a change the base gained after the merge base is base drift, never a finding), then each changed file in full, plus the code that feeds it and the code it calls. For an area: read every file in its paths, in full. For a flow: read each function from source to sink. For repo-wide: read each listed file, and search git history for credentials (`git log -p`). For gaps: read every earlier output's `coverage`, then the code behind each thin spot. List every **source** of untrusted input (request data, CLI args, env vars, files, PR titles or branch names in CI, webhook payloads, model output, dependencies, and what `map.md` says flows in from other areas) and every **sink** it can reach (shell, query, `eval`, file path, HTML, terminal, export file, network call, permission grant). Done when every file in your scope (every changed hunk, for a PR) is on the list or noted as touching no source or sink.
3. **Hunt** along these axes, and only these:
   - **Injection**: a source reaching a sink unquoted or unescaped: shell strings, SQL, templates, paths, terminal escape codes, spreadsheet formulas, `${{ }}` expressions inside a workflow `run:` step.
   - **Secrets**: credentials in code, logs, errors, URLs, committed files or git history; secrets exposed to code an outsider controls (e.g. `pull_request_target` checking out the PR head).
   - **Authz**: an action reachable without the check its siblings make; a gate that can be skipped, raced or forged; trust given to data an outsider can write.
   - **Privilege**: tokens, workflow `permissions`, file modes or scopes wider than the code needs, where the extra width lets someone read, write or run what they should not.
   - **Supply chain**: a dependency with a known vulnerability, an action pinned to a tag instead of a commit SHA, a script piped from the network, an unpinned version of code that runs with secrets, write tokens or merge power.
   - **Unsafe handling**: deserializing untrusted data into code execution or a wrong trust decision, temp files in shared folders, check-then-use races an attacker can win on a default setup, weak randomness or crypto where security depends on it, input that crashes or hangs a service other people depend on.

   Run nothing before you have read your whole scope (for a PR, the whole diff). To confirm an attack by running it, work in a fresh scratch folder with `HOME` and every path the code reads or writes pointed inside it; the machine's real home, dotfiles and repos stay untouched.

   Name a finding only when you traced it end to end: a named attacker who controls the source, the path to the sink, and their **gain**: what they can do afterwards beyond their **baseline** in `map.md`'s threat model (for a PR, write the baseline yourself first, by the rule in [mapper-brief.md](mapper-brief.md) step 3). A gain is real when the attacker reads, writes, runs or blocks something their baseline does not reach, on a setup the repo's users plausibly run with the OS defaults left on (e.g. Linux's `protected_hardlinks`). Making the attacker's own data fail, or crashing a tool the attacker could already break by design, gives none. Complete coverage with no findings is a full, valid answer; a hunch or a style concern stays out of the output.

   A real bug that gives no attacker a gain (a crash on bad input, a lost file mode, an encoding error) goes in `other_bugs`, outside the findings.

   Done when every source-to-sink pair on your list has been traced on all six axes.

4. **Check the output** once it is written, before your final message. For each `path` and `line` in it, read that line in the worktree: it must hold the code the claim is about, not a nearby line; fix any number that misses. Before your final message, run `python3 -I ~/.agents/skills/_shared/protocol/check.py security output <file>` on the object and fix each problem it prints until it prints `OK security <status>`: with an `output` file, on that file; with none (a remote run), write the object to a file outside the worktree (`/tmp/security-output.json`) first. Done when every cited line holds what its claim says and the output passes its check.

## When you run remotely

The input says `remote: true`, only for a `pr` scope, and only for a PR by the human or the run's own identity. Your `worktree` is a snapshot of the PR head, one commit with no history, no other branches and no GitHub access, and the files the input names are read-only attachments. Step 1 reads `pr_facts` in place of `gh pr view`; never call `gh`. Step 2's `diff` command prints the attached diff. Read the whole diff before running anything: the sandbox holds a token and can reach the network. Change no file in the worktree.

## Output

One JSON object, as [security.json](../_shared/protocol/schemas/security.json)'s `output` defines it; a person reads it only rendered by [render.py](../_shared/protocol/render.py).

- `status`: `done`, or `blocked` with a one-line `why` (you could not review your scope).
- `coverage`: one row per source: `source` (its `file:line`), `sinks` (what it reaches) and `cells`, one per axis (`injection`, `secrets`, `authz`, `privilege`, `supply-chain`, `unsafe-handling`), each `clean` or the number `n` of the finding there.
- `complete`: `true` when every source-to-sink pair of your scope is a row with every cell filled; `false` otherwise.
- `findings`: most severe first, or `[]`. Each names a concrete **attacker** and has `n` (its number, from 1), `severity` (`blocker`: exploitable as written; `should-fix`: exploitable only under a setup step 3 allows), `axis` (one of the six above), `path` and `line` in the worktree (for a PR, on a line the diff adds or changes when possible), `claim` (one sentence), `attacker` (who controls which input, and how they reach this code), `scenario` (the input they send, the steps), `gain` (what they can do afterwards that their baseline does not already allow) and `fix` (one sentence on the smallest change that closes it; when that change adds one more entry to a deny-list of paths, file names or functions, name the structural fix that closes the whole class instead, e.g. an allow-list, CODEOWNERS, a gate that checks paths).
- `other_bugs`: `path`, `line` and `claim` each, or `[]`.
- `process`: the merge-path sentences of the decisions file section, or `[]`.

With an `output` file in the input, write the object there in UTF-8, then your final message is only `{"status": "<its status>", "output": "<that path>"}`. With none, your final message is the object itself, and nothing else.
