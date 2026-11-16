# todo

A small command-line todo list. Items live in a JSON file, `$TODO_FILE` (default `~/.todo.json`, in your home folder).

Upgrading from a version that used `todo.json` in the current folder: move that file to `~/.todo.json`, or set `TODO_FILE=todo.json` to keep the old behaviour. The default changed because a `todo.json` shipped in a cloned repo or shared folder was read and rewritten as your own list.

## Usage

Run it with `python -m todo <command>`.

```
todo add TITLE [--due YYYY-MM-DD] [--tag TAG[,TAG...]]... [--priority high|medium|low] [--every RULE] # RULE: [N] day|week|month|year, e.g. month, 2 weeks
todo list [--overdue] [--tag TAG]... [--priority P] [--open|--done] [--search TEXT] [--sort priority|due]
todo search TEXT                    # same as todo list --search TEXT
todo done ID
todo skip ID                        # move a recurring item to its next occurrence without marking it done
todo stop ID                        # end a series: the item stays open as a one-off
todo tag ID [+TAG[,TAG...] | -TAG[,TAG...]]...   # +TAG (or a bare TAG) adds, -TAG removes
todo priority ID high|medium|low|none
todo remind
todo export --format csv|md [--out FILE] [--tag TAG]... [--priority P] [--open|--done] [--search TEXT] [--sort priority|due]
todo sync
```

Tags are trimmed and lowercased, hold no spaces, and an item holds at most 10. `todo tag ID +a,b` adds both tags and `-a,b` removes both; adding and removing the same tag in one call is an error. `todo priority ID none` clears the priority. `todo list` marks high items with `!!` and medium items with `!`, then shows the tags:

```
[ ] 1: buy milk (due 2026-11-01) !! #groceries #home
```

`--every RULE` makes an item recurring. RULE is `day`, `week`, `month` or `year` (singular or plural) with an optional positive count first: `--every month`, `--every 2 weeks` (quotes optional), `--every day`. A recurring item added without `--due` is due today. A rule whose next occurrence would fall after 9999-12-31 (such as `--every 9999 years`) is rejected. Put `--every` after TITLE. `todo list` shows the rule right after the due date, before the priority marker and tags:

```
[ ] 4: pay rent (due 2026-11-01) ↻ every month #home
```

`todo done ID` on a recurring item marks it done and creates the next occurrence: a new item with a new id, the same title, tags, priority and rule, due on the next date in the series (stepped forward to today or later; a series started on the 31st lands on the last day of shorter months). It prints a second line naming it: `next 5: pay rent (due 2026-12-01)`. Running `todo done` again on a done item changes nothing.

`todo skip ID` moves a recurring item to its next occurrence without marking it done: same id, due date moved by the same rules as `todo done`. It is an error (exit 2) on a one-off item or an item that is already done.

`todo stop ID` removes the rule from a recurring item: the current occurrence stays as it is (open or done, same due date) and completing it later creates no next occurrence. On a one-off item it is an error.

`todo remind` shows the same `↻ every` suffix, without the priority marker or tags.

`todo export` takes the same filters as `todo list` and exports exactly the items `todo list` would show. CSV has the columns `id,title,done,due,tags,priority,every` (tags space-separated, priority as a word or empty, every as the rule like `month` or `2 weeks`, empty for one-off items). Markdown lines end like `todo list` lines, the rule suffix included (`- [ ] pay rent (due 2026-11-01) ↻ every month !! #home`), and keep open items first, then `## Done`, so `--sort` orders within each group.

## Sync

`todo sync` merges your todo file with the remote todo file named by `$TODO_REMOTE`, matching items by id. Saving a file keeps its permissions and group, so a remote shared through group permissions stays readable and writable by that group. If the group cannot be kept (you are not in it), the save stops with an error instead of handing the file to another group.

When both sides changed an item, the one with the latest `updated_at` wins as a whole, tags and priority included.

Recurring items: each series has a hidden series id, and each occurrence a number that `todo done` (on the next item) and `todo skip` (on the same item) raise by one. After matching by id, items of the same series with the same occurrence number count as one occurrence, and only the one with the latest `updated_at` is kept. So if two machines complete the same occurrence offline, both end up with exactly one next occurrence after syncing. If one machine skipped an occurrence and the other completed it, the later change wins: a later skip keeps the skipped item (the completion is dropped), a later completion keeps the done item and its next occurrence. If one side skipped and then completed while the other only completed, the counts differ and both next occurrences survive; run `todo stop` or `todo done` on the extra one.

Stopping wins: if one machine runs `todo stop` on an open occurrence and another machine, before syncing, completed that occurrence (and so created the next one), after both sync the next occurrence has no rule. It stays as a normal one-off item, nothing is deleted, and completing it creates nothing more. This holds whichever change was more recent. Stopping an occurrence that is already done only removes its own rule; the series goes on in its next occurrence.

## Known limits

- Concurrent tag edits on two sides lose one side's change: if you tag an item on one side and tag it differently on the other before syncing, only the later edit survives.
- Older versions of `todo` crash on a todo file that holds recurring items (`--every`), stopped ones included: they stop with a Python traceback. Files with only one-off items stay readable. Upgrade every copy that syncs the same remote.
- Older versions of `todo` cannot read a todo file that has tags or priority: they stop with a Python traceback, not a clean error message. Upgrade every copy that syncs the same remote.
- Items created on two sides before a sync can collide on id: both get the next free id, so one overwrites the other.
- Titles show non-printable characters (control codes, newlines, tabs) as `?`, and a tag holding one is rejected. CSV export puts `'` in front of a title or tags cell, or any `;`-separated part of one, starting with `=`, `+`, `-` or `@` so spreadsheets show it as text, even ones that split cells on `;`.
- A stored `updated_at` more than 30 days in the future loads as 30 days from now, so a bad clock cannot win every later sync.
- `todo sync` refuses a `$TODO_REMOTE` that is a symlink or a hard link to another file; point it at a file of its own. It also refuses a remote whose path goes through a folder symlink owned by another user (symlinks owned by you or by root are followed). The folders are checked just before each read and write, so someone who can swap a folder for a symlink in that split second can still redirect one sync. Saving keeps a todo file's read and write permission bits, but drops execute, setuid, setgid and sticky bits. A local todo file owned by someone else (planted in a shared folder) is saved as yours with mode 0600 instead of keeping its owner's bits.
- Two syncs to the same remote at the same moment can still lose one side's changes: each save swaps in a whole file, but nothing locks the remote between load and save.
- `export --out FILE` replaces a symlink at `FILE` with a regular file instead of writing through it, and the todo file must be a regular file (a FIFO or device stops with an error instead of hanging) of at most 16 MiB.
- Item ids go from 1 to 2**53 (9007199254740992); a file holding a larger id stops with an error, and `todo add` stops once that id is taken.
- `export --out FILE` always writes UTF-8. On a terminal or pipe whose encoding lacks a character in a title (for example an emoji under cp1252), output shows that character as `?` instead of stopping.
