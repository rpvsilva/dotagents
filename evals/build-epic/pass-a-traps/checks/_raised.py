"""Shared by this case's checks, not a check itself (not executable).

  python3 -I _raised.py <trap>

Exits 0 when one item of the `Open questions` section of `$EVAL_SCRATCH/spec.md` raises <trap>: the item's text
(its first line and every line up to the next item or heading), lowercased, matches every pattern of the trap. An item is a numbered (`1.`, `1)`) or bulleted (`-`, `*`) line.

The epic (input/epic.md) hides three ambiguities a careful planner raises rather than settles. An item raises a
trap when it matches every one of the trap's patterns; a question only near the trap (a side detail of it) does not.

- cap-vs-add: the epic says a count never goes above its cap, and that `tally add` always adds n. What does `add`
  do when n would pass the cap (refuse, clamp)? Patterns: `cap`; `add`; going past it (`exceed`, `above`, `over`,
  `beyond`, `past`, `more than`, `clamp`, `clip`, `refus`, `reject`, `truncat`). A question about a cap set below
  the current count, with no `add` going past it, is a side detail.
- recent-order: `list --recent` orders "by when they last changed", without saying which way. Patterns: `recent` or
  `last chang`; a direction (`newest`, `oldest`, `latest`, `earliest`, `ascending`, `descending`, `direction`,
  `most recent first`, `least recent`). Ties and counts with no change time are side details.
- export-format: "a form a spreadsheet or another tool can read" leaves the file format open. Patterns: `export`;
  a format word (`format`, `csv`, `json`, `tsv`, `column`, `header`, `delimit`, `quot`, `extension`).
"""
import os
import re
import sys
from pathlib import Path

TRAPS = {
    "cap-vs-add": (r"\bcap", r"\badd", r"exceed|above|\bover\b|beyond|\bpast\b|more than|clamp|clip|refus|reject"
                                       r"|truncat"),
    "recent-order": (r"recent|last chang", r"newest|oldest|latest|earliest|ascending|descending|direction"
                                           r"|most recent first|least recent"),
    "export-format": (r"export", r"format|csv|json|tsv|column|header|delimit|quot|extension"),
}
HEADING = re.compile(r"^#+\s")
OPEN = re.compile(r"^#+\s+open questions\s*$", re.IGNORECASE)
ITEM = re.compile(r"^\s*(\d+[.)]|[-*])\s+")

patterns = [re.compile(p) for p in TRAPS[sys.argv[1]]]
spec = Path(os.environ["EVAL_SCRATCH"]) / "spec.md"
if not spec.is_file():
    sys.exit(1)
items, inside = [], False
for line in spec.read_text(errors="replace").splitlines():
    if HEADING.match(line):
        inside = bool(OPEN.match(line))
        continue
    if not inside:
        continue
    if ITEM.match(line) or not items:
        items.append(line)
    else:
        items[-1] += "\n" + line
sys.exit(0 if any(all(p.search(i.lower()) for p in patterns) for i in items) else 1)
